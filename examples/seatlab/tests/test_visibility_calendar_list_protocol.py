import json
from collections import Counter
from dataclasses import replace
from hashlib import sha256
from itertools import batched, pairwise
from pathlib import Path

import pytest

from examples.seatlab.visibility.calendar_list_packet import load as load_cases
from examples.seatlab.visibility.calendar_list_protocol import (
    cells,
    grade,
    main,
    prompt,
)
from examples.seatlab.visibility.calendar_packet import PACKET, PACKET_SHA256, load
from examples.seatlab.visibility.calendar_protocol import decision_prompt

_NATIVE = PACKET.with_name("flotilla-calendar-external-results.json")
_ORDERS = (
    ("rr", "er", "ee", "re"),
    ("er", "re", "rr", "ee"),
    ("re", "ee", "er", "rr"),
    ("ee", "rr", "re", "er"),
)
_P1_STARTS = (7, 8, 9, 26)
_P2_STARTS = (16, 26, 28, 29)


@pytest.mark.parametrize(
    ("direction", "receiver_index", "own_exact", "sender_exact"),
    ((0, 1, _P2_STARTS, _P1_STARTS), (1, 0, _P1_STARTS, _P2_STARTS)),
)
def test_only_the_named_list_changes_in_both_directions(
    direction: int,
    receiver_index: int,
    own_exact: tuple[int, ...],
    sender_exact: tuple[int, ...],
) -> None:
    case = next(c for c in load_cases() if c.world == 0 and c.direction == direction)
    case = replace(case, receiver=(0,), sender=(34,))
    table = load(PACKET, PACKET_SHA256)[0].tables[receiver_index]
    assert prompt(case, "rr") == decision_prompt(table, (0,), (34,))
    assert prompt(case, "er") == decision_prompt(table, own_exact, (34,))
    assert prompt(case, "re") == decision_prompt(table, (0,), sender_exact)
    assert prompt(case, "ee") == decision_prompt(table, own_exact, sender_exact)
    with pytest.raises(ValueError, match="condition"):
        prompt(case, "exact")


def test_all_original_views_match_published_receiver_prompt_digests() -> None:
    published = {
        f"{row['block']}-{arm}": info["receiver_prompt_sha256"]
        for row in json.loads(_NATIVE.read_text(encoding="utf-8"))["rows"]
        for arm, info in row["report"]["arms"].items()
    }
    registered = load_cases()
    assert len(published) == len(registered) == 24
    for case in registered:
        digest = sha256(prompt(case, "rr").encode("utf-8")).hexdigest()
        assert digest == case.prompt_sha256 == published[case.id]


def test_both_draws_contain_24_complete_balanced_quartets() -> None:
    case_ids = tuple(
        f"rx-w{world:02d}-d{direction}-r{repetition}-{arm}"
        for world in (0, 4, 8)
        for direction in range(2)
        for repetition in range(2)
        for arm in ("blind", "peer")
    )
    plan = cells(reversed(load_cases()))
    quartets = tuple(batched(plan, 4))
    assert len(plan) == len({cell.id for cell in plan}) == 192
    assert len(quartets) == 48
    for draw in range(2):
        order = range(23, -1, -1) if draw else range(24)
        groups = quartets[24 * draw : 24 * (draw + 1)]
        positions = Counter()
        transitions = Counter()
        for index, group in zip(order, groups, strict=True):
            case_id = case_ids[index]
            conditions = tuple(cell.condition for cell in group)
            assert conditions == _ORDERS[(index + draw) % 4]
            assert {cell.case.id for cell in group} == {case_id}
            assert {cell.draw for cell in group} == {draw}
            assert [cell.id for cell in group] == [
                f"cl-{case_id[3:]}-k{draw}-{condition}" for condition in conditions
            ]
            positions.update(enumerate(conditions))
            transitions.update(pairwise(conditions))
        assert len(positions) == 16 and set(positions.values()) == {6}
        assert len(transitions) == 12 and set(transitions.values()) == {6}


def test_grade_checks_selected_slot_without_using_either_list() -> None:
    case = replace(load_cases()[0], receiver=(7,), sender=(16,))
    for slot in range(35):
        score = grade(case, json.dumps({"slot": slot}))
        assert type(score) is int
        assert score == int(slot == 26)


@pytest.mark.parametrize(
    "text",
    (
        'Try {"slot":26}',
        '```json\n{"slot":26}\n```',
        '{"slot":7,"slot":26}',
        '{"slot":26,"correct":1}',
        '{"slot":true}',
        '{"slot":26.0}',
        '{"slot":35}',
        '{"starts":[26]}',
    ),
)
def test_grade_rejects_ungrammatical_choices(text: str) -> None:
    with pytest.raises(ValueError):
        grade(load_cases()[0], text)


def test_cli_prompt_and_plan_emit_only_the_public_payloads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registered = load_cases()
    case = registered[0]
    main(["prompt", case.id, "er"])
    captured = capsys.readouterr()
    assert captured.out == prompt(case, "er")
    assert captured.err == ""

    main(["plan"])
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [cell.id for cell in cells(registered)]
    assert captured.err == ""


def test_cli_score_reports_grammar_and_never_repairs_an_answer(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    case_id = load_cases()[0].id
    response = tmp_path / "response.json"
    response.write_text('{"slot":7}\n', encoding="utf-8")
    for text, expected in (
        (f"@{response}", {"grammar": True, "correct": 0}),
        ('{"slot":26}', {"grammar": True, "correct": 1}),
        ('Try {"slot":26}', {"grammar": False, "correct": None}),
    ):
        main(["score", case_id, text])
        captured = capsys.readouterr()
        assert json.loads(captured.out) == expected
        assert captured.err == ""
