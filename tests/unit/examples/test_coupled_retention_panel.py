from collections import Counter
from dataclasses import replace
from fractions import Fraction
from itertools import permutations

import pytest

from examples.seatlab.coupled_retention.panel import qualify, schedules
from examples.seatlab.coupled_retention.policy import render
from examples.seatlab.coupled_retention.records import NativeCase

ACTIONS = tuple("ABCDEF")


def _scores(winner: str) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(int(e == w == winner) for w in ACTIONS) for e in ACTIONS)


def _toy(sender: str = "truthful", *, swap: bool = False) -> list[NativeCase]:
    cases = []
    for seed, (shared, east_only, west_only) in enumerate(permutations("BCD")):
        east = (shared, east_only)
        west = (shared, west_only)
        if swap:
            east, west = west, east
        east_pair = (
            render("east", "A", ACTIONS, east, None),
            render("east", "A", ACTIONS, east, west if sender == "truthful" else ()),
        )
        west_pair = (
            render("west", "A", ACTIONS, west, None),
            render("west", "A", ACTIONS, west, east if sender == "truthful" else ()),
        )
        cases.append(NativeCase(seed, east_pair, west_pair, ACTIONS, _scores(shared)))
    return cases


def test_schedules_preserve_exact_weighted_packets_within_each_world():
    cases = _toy()
    arms = schedules(cases)
    expected_masks = {
        "together": [(0, 0), (0, 0), (1, 1), (1, 1)],
        "apart": [(0, 1), (0, 1), (1, 0), (1, 0)],
        "independent": [(0, 0), (0, 1), (1, 0), (1, 1)],
    }
    for arm, rows in arms.items():
        assert len(rows) == 24
        for index, case in enumerate(cases):
            block = rows[4 * index : 4 * index + 4]
            assert all(row.weight == 1 and row.scores == case.scores for row in block)
            assert [(row.east, row.west) for row in block] == [
                (case.east[e], case.west[w]) for e, w in expected_masks[arm]
            ]
            assert Counter(row.east for row in block) == Counter(case.east * 2)
            assert Counter(row.west for row in block) == Counter(case.west * 2)
        assert Counter(row.east for row in rows) == Counter(
            packet for case in cases for packet in case.east * 2
        )
        assert Counter(row.west for row in rows) == Counter(
            packet for case in cases for packet in case.west * 2
        )


def test_truthful_rotations_change_team_score_without_changing_accuracy():
    report = qualify(_toy())
    assert report["worlds"] == 6
    assert report["rows_per_arm"] == 24
    assert report["equal_role_marginals"] is True
    assert report["equal_role_accuracy"] is True
    assert report["team_gap"] == "1/6"
    teams = {"together": "2/3", "apart": "1/2", "independent": "7/12"}
    decentralized = {"together": "3/4", "apart": "1/2", "independent": "5/8"}
    for arm, result in report["arms"].items():
        assert result["reference"] == {
            "team": teams[arm],
            "east": "3/4",
            "west": "3/4",
            "split": str(1 - Fraction(teams[arm])),
            "agree_wrong": "0",
        }
        assert result["ceilings"]["decentralized"] == decentralized[arm]
        assert result["ceilings"]["pooled"] == result["ceilings"]["oracle"] == "1"
    independent = report["arms"]["independent"]["reference"]
    assert Fraction(independent["team"]) != Fraction(independent["east"]) * Fraction(
        independent["west"]
    )
    assert (
        Fraction(independent["team"])
        == (Fraction(teams["together"]) + Fraction(teams["apart"])) / 2
    )


def test_explicit_pass_has_no_retention_effect():
    report = qualify(_toy("pass"))
    assert report["equal_role_marginals"] is True
    assert report["equal_role_accuracy"] is True
    assert report["team_gap"] == "0"
    for result in report["arms"].values():
        assert result["reference"] == {
            "team": "1/3",
            "east": "1/2",
            "west": "1/2",
            "split": "2/3",
            "agree_wrong": "0",
        }
        assert result["ceilings"]["decentralized"] == "1/2"
        assert result["ceilings"]["pooled"] == result["ceilings"]["oracle"] == "1"


@pytest.mark.parametrize("sender", ["truthful", "pass"])
def test_swapping_role_observations_preserves_all_reported_values(sender: str):
    original = qualify(_toy(sender))
    swapped = qualify(_toy(sender, swap=True))
    assert original["team_gap"] == swapped["team_gap"]
    assert swapped["equal_role_marginals"] and swapped["equal_role_accuracy"]
    for arm in original["arms"]:
        first, second = original["arms"][arm], swapped["arms"][arm]
        assert first["reference"] == second["reference"]
        for ceiling in ("decentralized", "pooled", "oracle"):
            assert first["ceilings"][ceiling] == second["ceilings"][ceiling]


def test_reference_measures_native_score_matrix_not_card_intersection():
    cases = _toy()
    cases[0] = replace(cases[0], scores=_scores("F"))
    report = qualify(cases)
    assert report["arms"]["together"]["reference"]["team"] == "1/2"
    assert report["arms"]["together"]["reference"]["east"] == "7/12"


def test_reject_invalid_case_rosters_and_dual_packets():
    cases = _toy()
    first = cases[0]
    for invalid in (
        cases[:-1],
        [replace(first, seed=cases[1].seed), *cases[1:]],
        [replace(first, actions=ACTIONS[::-1]), *cases[1:]],
        [replace(first, actions=("A",) * 6), *cases[1:]],
        [replace(first, east=first.west), *cases[1:]],
        [replace(first, east=(first.east[0], first.east[0])), *cases[1:]],
        [replace(first, east=(first.east[0], first.east[1] + b"\n")), *cases[1:]],
    ):
        with pytest.raises(ValueError):
            schedules(invalid)


def test_reject_nonbinary_or_nondiagonal_or_nonunique_native_rewards():
    cases = _toy()
    first = cases[0]
    for matrix in (
        first.scores[:-1],
        tuple((0,) * 6 for _ in ACTIONS),
        tuple(tuple(int(e == w) for w in ACTIONS) for e in ACTIONS),
        tuple(tuple(int(e == "A" and w == "B") for w in ACTIONS) for e in ACTIONS),
        tuple(tuple(2 * value for value in line) for line in first.scores),
    ):
        with pytest.raises(ValueError):
            schedules([replace(first, scores=matrix), *cases[1:]])
