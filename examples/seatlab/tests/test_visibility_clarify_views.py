import json
from typing import Any

import pytest

from examples.seatlab.visibility.clarify_schedule import PACKETS, SEEDS, Cell, packet_path
from examples.seatlab.visibility.clarify_views import (
    build_final,
    build_initial,
    grade_actions,
    validate_actions,
    validate_selector,
)
from examples.seatlab.visibility.dispatch_packet import verify_packet


def _packet(seed: int) -> dict[str, Any]:
    source = packet_path(PACKETS, seed).read_text(encoding="utf-8")
    return verify_packet(json.loads(source))


def _body(prompt: str) -> dict[str, Any]:
    return json.loads(prompt.split("\n", 1)[1])


@pytest.fixture
def packet() -> dict[str, Any]:
    return _packet(3101)


@pytest.mark.parametrize("seed", SEEDS)
def test_initial_is_the_same_role_view_for_both_arms(seed: int) -> None:
    packet = _packet(seed)
    active = Cell(0, seed, 0, "active")
    queue = Cell(0, seed, 0, "queue")
    initial = build_initial(packet, active)
    assert initial == build_initial(packet, queue)
    assert str(seed) not in initial
    sender = packet["arms"]["fresh"]["inventory"]["rows"]
    assert _body(initial) == {
        "role": "dispatch",
        "cases": [
            {
                "id": case["id"],
                "utilities": case["utilities"],
                "messages": posted["reports"][:1]
                if case["id"] in active.hidden
                else posted["reports"],
                "pooled_available": None,
            }
            for case, posted in zip(packet["world"], sender, strict=True)
        ],
    }


def test_overlapping_delivery_does_not_reveal_policy_or_unserved_request(
    packet: dict[str, Any],
) -> None:
    active = Cell(0, 3101, 0, "active")
    queue = Cell(0, 3101, 0, "queue")
    delivered = queue.queued_case
    unread = next(case for case in queue.hidden if case != delivered)
    initial = build_initial(packet, active)
    final = build_final(packet, active, delivered)
    assert final == build_final(packet, queue, delivered)
    assert final == build_final(packet, queue, unread)
    assert _body(final)["delivered_case"] == delivered

    # Changes to an unread bulletin and unobserved native fields cannot reach either turn.
    packet["arms"]["fresh"]["inventory"]["rows"][unread]["reports"][1]["available"] = [
        "A",
        "C",
    ]
    packet["world"][unread]["after"] = ["A", "C"]
    packet["arms"]["fresh"]["dispatch"]["rows"][unread]["bay"] = "A"
    packet["arms"]["fresh"]["request"] = "historical private path"
    packet["arms"]["fresh"]["score"] = {"private": "historical score"}
    assert build_initial(packet, queue) == initial
    assert build_final(packet, queue, unread) == final
    assert build_final(packet, active, unread) != final


def test_refresh_of_visible_case_records_one_duplicate_delivery(
    packet: dict[str, Any],
) -> None:
    active = Cell(0, 3101, 0, "active")
    queue = Cell(0, 3101, 0, "queue")
    visible = validate_selector('{"case":2}')
    assert visible not in active.hidden
    for cell, delivered in ((active, visible), (queue, queue.queued_case)):
        initial = _body(build_initial(packet, cell))
        final = _body(build_final(packet, cell, visible))
        assert final.keys() == initial.keys() | {"delivered_case"}
        assert final["delivered_case"] == delivered
        bulletin = packet["arms"]["fresh"]["inventory"]["rows"][delivered]["reports"][1]
        initial["cases"][delivered]["messages"].append(bulletin)
        assert final["cases"] == initial["cases"]
    active_messages = _body(build_final(packet, active, visible))["cases"][visible][
        "messages"
    ]
    assert [bulletin["revision"] for bulletin in active_messages] == [1, 2, 2]
    assert active_messages[1] == active_messages[2]


def test_joint_sender_and_dispatch_changes_score_against_original_truth(
    packet: dict[str, Any],
) -> None:
    active = Cell(0, 3101, 0, "active")
    queue = Cell(0, 3101, 0, "queue")
    fresh = packet["arms"]["fresh"]
    changed = 1
    reports = fresh["inventory"]["rows"][changed]["reports"]
    reports[0]["available"] = ["B", "C"]
    reports[1]["available"] = ["A", "C"]
    fresh["dispatch"]["rows"][changed]["bay"] = "A"
    initial = _body(build_initial(packet, active))
    final = _body(build_final(packet, active, changed))
    assert initial["cases"][changed]["messages"] == reports[:1]
    assert final["cases"][changed]["messages"] == reports

    for bay, optimal, feasible in (
        ("B", True, True),
        ("D", False, True),
        ("C", False, False),
    ):
        rows = [{"id": i, "bay": choice} for i, choice in enumerate(("A", bay, "C", "C"))]
        actions = validate_actions(packet, active, json.dumps({"rows": rows[::-1]}))
        assert actions == {"rows": rows}
        expected = {
            "episodes": 4,
            "optimal": 3 + optimal,
            "feasible": 3 + feasible,
            "by_case": [
                {"id": 0, "optimal": True, "feasible": True},
                {"id": 1, "optimal": optimal, "feasible": feasible},
                {"id": 2, "optimal": True, "feasible": True},
                {"id": 3, "optimal": True, "feasible": True},
            ],
        }
        assert grade_actions(packet, active, actions) == expected
        assert grade_actions(packet, queue, actions) == expected


@pytest.mark.parametrize("case", range(4))
def test_selector_accepts_each_case(case: int) -> None:
    assert validate_selector(json.dumps({"case": case})) == case


@pytest.mark.parametrize(
    "text",
    (
        "3",
        "{}",
        '{"case":true}',
        '{"case":2.0}',
        '{"case":-1}',
        '{"case":4}',
        '{"case":NaN}',
        '{"case":0,"case":0}',
        '{"case":0,"bay":"A"}',
        '```json\n{"case":0}\n```',
    ),
)
def test_selector_rejects_invalid_responses(text: str) -> None:
    with pytest.raises(ValueError):
        validate_selector(text)


@pytest.mark.parametrize(
    "replacement",
    (
        None,
        '"not a row"',
        '{"id":0,"bay":"B"}',
        '{"id":true,"bay":"B"}',
        '{"id":1.0,"bay":"B"}',
        '{"id":4,"bay":"B"}',
        '{"id":1,"bay":"E"}',
        '{"id":1,"bay":1}',
        '{"id":1,"bay":NaN}',
        '{"id":1,"bay":"B","id":1}',
        '{"id":1,"bay":"B","extra":0}',
    ),
)
def test_dispatch_requires_the_exact_roster_and_bays(
    packet: dict[str, Any], replacement: str | None
) -> None:
    rows = '{"id":0,"bay":"A"},{"id":2,"bay":"C"},{"id":3,"bay":"C"}'
    if replacement is not None:
        rows = f"{rows},{replacement}"
    with pytest.raises(ValueError):
        validate_actions(packet, Cell(0, 3101, 0, "active"), f'{{"rows":[{rows}]}}')


def test_dispatch_rejects_extra_or_duplicate_top_level_keys(packet: dict[str, Any]) -> None:
    rows = json.dumps(packet["arms"]["fresh"]["dispatch"]["rows"])
    cell = Cell(0, 3101, 0, "active")
    for field in ('"extra":0', f'"rows":{rows}'):
        with pytest.raises(ValueError):
            validate_actions(packet, cell, f'{{"rows":{rows},{field}}}')
