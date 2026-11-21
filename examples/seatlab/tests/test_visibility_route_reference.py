from dataclasses import replace

from examples.seatlab.visibility import route_packet, route_reference
from examples.seatlab.visibility.listener import Report


def _case(reports: tuple[Report, ...]) -> route_packet.Case:
    return route_packet.Case(
        policy=0,
        round=1,
        reports=reports,
        best=0,
        source_sha256="0" * 64,
        trial_sha256="1" * 64,
        truth=((10, 10, 10), (8, 10, 10)),
        fetch_seq=10,
        decision_seq=12,
    )


_HARMFUL: tuple[Report, ...] = (
    ((10, 10), (8, 10)),
    ((10, 10), (13, 10)),
    ((10, 10), (10, 10)),
)


def test_equal_margins_can_conflict_and_neither_rule_breaks_ties():
    case = _case(
        (
            ((10, 10), (11, 9)),
            ((20, 10), (21, 10)),
            ((10, 10), (9, 10)),
        )
    )
    report = route_reference.describe((case,))
    assert report["sources"] == [
        {
            "policy": 0,
            "source_rounds": 1,
            "eligible_rounds": 1,
            "own": {"n": 3, "correct": 1, "tied": 1},
            "complementary": {"n": 3, "correct": 0, "tied": 3},
            "fixed": 0,
            "regressed": 1,
            "conflict_rows": 1,
            "conflicting_cells": 2,
            "rounds_with_prior_decision_pending": 0,
        }
    ]


def test_an_incorrect_complement_can_reverse_a_correct_own_choice():
    report = route_reference.describe((_case(_HARMFUL),))
    assert report["sources"] == [
        {
            "policy": 0,
            "source_rounds": 1,
            "eligible_rounds": 1,
            "own": {"n": 3, "correct": 1, "tied": 1},
            "complementary": {"n": 3, "correct": 1, "tied": 0},
            "fixed": 1,
            "regressed": 1,
            "conflict_rows": 1,
            "conflicting_cells": 1,
            "rounds_with_prior_decision_pending": 0,
        }
    ]


def test_counts_use_physical_rotations_and_event_timing_uses_source_rounds():
    first = _case(_HARMFUL)
    pending = replace(first, round=2, fetch_seq=11, decision_seq=13)
    other = replace(
        first, policy=1, trial_sha256="2" * 64, source_sha256="3" * 64, round=2
    )
    ineligible = replace(
        other,
        round=3,
        reports=None,
        truth=None,
        best=None,
        fetch_seq=None,
        decision_seq=None,
        reason="incomplete_predecision_fetch",
    )
    report = route_reference.describe((pending, other, ineligible, first))
    assert report["source_rounds"] == 4
    assert report["eligible_rounds"] == 3
    assert report["distinct_source_trials"] == 2
    assert report["distinct_physical_truth_arrays"] == 1
    assert report["distinct_numeric_snapshots"] == 1
    assert report["distinct_own_views"] == 3
    assert report["sources"] == [
        {
            "policy": 0,
            "source_rounds": 2,
            "eligible_rounds": 2,
            "own": {"n": 6, "correct": 2, "tied": 2},
            "complementary": {"n": 6, "correct": 2, "tied": 0},
            "fixed": 2,
            "regressed": 2,
            "conflict_rows": 2,
            "conflicting_cells": 2,
            "rounds_with_prior_decision_pending": 1,
        },
        {
            "policy": 1,
            "source_rounds": 2,
            "eligible_rounds": 1,
            "own": {"n": 3, "correct": 1, "tied": 1},
            "complementary": {"n": 3, "correct": 1, "tied": 0},
            "fixed": 1,
            "regressed": 1,
            "conflict_rows": 1,
            "conflicting_cells": 1,
            "rounds_with_prior_decision_pending": 0,
        },
    ]
