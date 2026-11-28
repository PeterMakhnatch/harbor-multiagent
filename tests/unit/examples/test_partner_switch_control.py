from __future__ import annotations

import json
from collections import Counter
from itertools import combinations, permutations, product
from typing import Any

import pytest

from examples.seatlab.partner_switch import control, engine


def _packet(cycle: tuple[str, ...], seat: str, card: list[str], **fields: Any) -> bytes:
    return json.dumps(
        {
            "schema": "seatlab.partner-switch/v1",
            "stage": "report",
            "role": seat,
            "instruction": "Choose a slot.",
            "incumbent": next(slot for slot in engine.OPTIONS if slot not in cycle),
            "options": list(engine.OPTIONS),
            "active": cycle,
            "own": card,
            **fields,
        },
        separators=(",", ":"),
    ).encode()


def test_controls_for_every_public_cycle_and_card_order() -> None:
    for active in combinations(engine.OPTIONS, 3):
        for missing_east, missing_west in permutations(active, 2):
            east = [slot for slot in active if slot != missing_east]
            west = [slot for slot in reversed(active) if slot != missing_west]
            (target,) = set(east).intersection(west)
            for east_direction, west_direction in product((-1, 1), repeat=2):
                east_report = control.choose(
                    east_direction, "east", _packet(active, "east", east)
                )
                west_report = control.choose(
                    west_direction, "west", _packet(active, "west", west)
                )
                assert east_report in east
                assert west_report in west
                assert (
                    east_report
                    == active[(active.index(missing_east) + east_direction) % 3]
                )
                assert (
                    west_report
                    == active[(active.index(missing_west) + west_direction) % 3]
                )
                east_vote = _packet(
                    active,
                    "east",
                    east,
                    stage="vote",
                    sent=east_report,
                    peer=west_report,
                )
                west_vote = _packet(
                    active,
                    "west",
                    west,
                    stage="vote",
                    sent=west_report,
                    peer=east_report,
                )
                if east_direction == west_direction or east_report == west_report:
                    assert control.choose(east_direction, "east", east_vote) == target
                    assert control.choose(west_direction, "west", west_vote) == target
                else:
                    assert (
                        control.choose(east_direction, "east", east_vote)
                        == missing_west
                    )
                    assert (
                        control.choose(west_direction, "west", west_vote)
                        == missing_east
                    )


def test_controls_native_actual_delivery_against_frozen_home_votes() -> None:
    cases = engine.cases(0)
    assert len(cases) == 6
    active = cases[0].active
    assert all(case.active == active for case in cases)
    missing_pairs = set()
    for case in cases:
        east, west = (json.loads(packet)["own"] for packet in case.reports)
        (missing_east,) = set(active).difference(east)
        (missing_west,) = set(active).difference(west)
        missing_pairs.add((missing_east, missing_west))
    assert missing_pairs == set(permutations(active, 2))

    expected_histogram = Counter({slot: 2 for slot in active})
    for east_direction, west_direction in product((-1, 1), repeat=2):
        outgoing = (Counter[str](), Counter[str]())
        ballots = (Counter[str](), Counter[str]())
        native_score = 0
        frozen_home_score = 0
        for case in cases:
            east_packet, west_packet = case.reports
            east_report = control.choose(east_direction, "east", east_packet)
            west_report = control.choose(west_direction, "west", west_packet)
            assert east_report in json.loads(east_packet)["own"]
            assert west_report in json.loads(west_packet)["own"]
            branch = engine.exchange(case, east_report, west_report)
            assert branch.reports == (east_report, west_report)
            assert json.loads(branch.votes[0])["peer"] == west_report
            assert json.loads(branch.votes[1])["peer"] == east_report
            east_vote = control.choose(east_direction, "east", branch.votes[0])
            west_vote = control.choose(west_direction, "west", branch.votes[1])
            outgoing[0][east_report] += 1
            outgoing[1][west_report] += 1
            ballots[0][east_vote] += 1
            ballots[1][west_vote] += 1
            native_score += engine.grade(branch, east_vote, west_vote)

            home_east = engine.exchange(
                case, east_report, control.choose(east_direction, "west", west_packet)
            )
            home_west = engine.exchange(
                case, control.choose(west_direction, "east", east_packet), west_report
            )
            frozen_east = control.choose(east_direction, "east", home_east.votes[0])
            frozen_west = control.choose(west_direction, "west", home_west.votes[1])
            frozen_home_score += engine.grade(branch, frozen_east, frozen_west)

        assert native_score == (6 if east_direction == west_direction else 3)
        assert frozen_home_score == 6
        assert outgoing == (expected_histogram, expected_histogram)
        assert ballots == (expected_histogram, expected_histogram)


@pytest.mark.parametrize(
    "fields",
    [
        {"stage": "unknown"},
        {"role": "west"},
        {"seed": 12},
        {"active": ["C", "B", "A"]},
        {"active": ["A", "B", "B"]},
        {"active": ["A", "B", []]},
        {"own": ["A", "A"]},
        {"own": ["A", "F"]},
        {"stage": "vote", "sent": "C", "peer": "A"},
        {"stage": "vote", "sent": "A", "peer": ["B"]},
        {"stage": "vote", "sent": "A", "peer": "F"},
        {"stage": "vote", "sent": "A"},
    ],
)
def test_control_rejects_malformed_packets(fields: dict[str, Any]) -> None:
    packet = _packet(("A", "B", "C"), "east", ["A", "B"], **fields)
    with pytest.raises(ValueError):
        control.choose(1, "east", packet)


def test_control_rejects_invalid_arguments() -> None:
    packet = _packet(("A", "B", "C"), "east", ["A", "B"])
    with pytest.raises(ValueError):
        control.choose(0, "east", packet)
    with pytest.raises(ValueError):
        control.choose(1, "north", packet)
    with pytest.raises(ValueError):
        control.choose(1, "east", b"not JSON")
