import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cursor import (  # noqa: E402
    Resources,
    analyze_case,
    choose_one_shot,
    coordinate_cursor,
    make_cursor,
)


def test_cursor_proves_no_overlap_and_counts_final_round():
    starts = {"m1": [1, 5], "m2": [2, 4], "m3": [4, 5], "m4": [2, 5], "m5": [3, 5]}
    decision = coordinate_cursor(
        {seat: make_cursor(ticks, horizon=8) for seat, ticks in starts.items()},
        horizon=8,
    )

    # Replies: [1,2,4,2,3], [5,4,4,5,5], then [5,null,5,5,5].
    assert decision.start is None
    assert decision.round_bounds == (0, 4, 5)
    assert decision.resources == Resources(
        round_trips=3,
        seat_exchanges=15,
        scalar_disclosures=14,
        distinct_disclosed_starts=10,
        request_payload_bytes=15,
        response_payload_bytes=18,
    )
    assert decision.resources.payload_bytes == 33

    full = choose_one_shot(starts, horizon=8)
    assert full.start is None
    assert full.resources.payload_bytes == 25


def test_cursor_matches_independent_enumeration():
    rng = random.Random(958_220)
    for _ in range(400):
        horizon = rng.randrange(1, 81)
        local = {}
        for seat in range(5):
            probability = rng.random()
            local[f"m{seat + 1}"] = {
                tick for tick in range(horizon) if rng.random() < probability
            }
        expected = next(
            (
                tick
                for tick in range(horizon)
                if all(tick in starts for starts in local.values())
            ),
            None,
        )
        decision = coordinate_cursor(
            {
                seat: make_cursor(sorted(starts), horizon=horizon)
                for seat, starts in local.items()
            },
            horizon=horizon,
        )

        assert decision.start == expected
        assert decision.round_bounds[0] == 0
        assert all(
            a < b for a, b in zip(decision.round_bounds, decision.round_bounds[1:])
        )
        assert len(decision.round_bounds) <= horizon


@pytest.mark.parametrize("starts", [[1, 1], [2, 1], [-1], [10], [True], ["1"], [1.5]])
def test_cursor_rejects_malformed_private_starts(starts):
    with pytest.raises(ValueError, match="strictly increasing integer ticks"):
        make_cursor(starts, horizon=10)


@pytest.mark.parametrize("reply", [-1, 10, True, "1", 1.5])
def test_coordinator_rejects_malformed_replies(reply):
    with pytest.raises(ValueError, match="m1: invalid reply"):
        coordinate_cursor({"m1": lambda bound: reply}, horizon=10)


def test_coordinator_rejects_reply_behind_advanced_bound():
    with pytest.raises(ValueError, match="m2: invalid reply 2 at bound 3"):
        coordinate_cursor(
            {
                "m1": make_cursor([3], horizon=10),
                "m2": lambda bound: 1 if bound == 0 else 2,
            },
            horizon=10,
        )


@pytest.mark.parametrize("contradiction", [None, 4])
def test_coordinator_rejects_discarding_a_witness(contradiction):
    with pytest.raises(ValueError, match="m1: .*contradicts known start 3"):
        coordinate_cursor(
            {
                "m1": lambda bound: 3 if bound == 0 else contradiction,
                "m2": make_cursor([1, 3], horizon=10),
            },
            horizon=10,
        )


@pytest.mark.parametrize("gold, expected_score", [([19], 1), ([18], 0)])
def test_case_comparison_calibrates_selection_scoring_and_resources(
    gold, expected_score
):
    free_ticks = {
        "m1": set(range(9)) | {19, 20},
        "m2": set(range(8, 17)) | {19, 20},
        "m3": {19, 20},
        "m4": {19, 20},
        "m5": {19, 20},
    }
    case = {
        "schema": "seatlab.calendar-case/v1",
        "case_id": "2/1",
        "dataset_commit": "synthetic",
        "duration": 2,
        "horizon": 720,
        "slots_per_day": 144,
        "members": {
            role: sorted(set(range(720)) - free) for role, free in free_ticks.items()
        },
        "gold": gold,
    }
    row = analyze_case(case)

    assert row["seat_count"] == 5
    assert row["full_cursor_same_start"] is True
    modes = row["modes"]
    for name in ("cursor", "full"):
        assert modes[name]["start"] == 19
        assert modes[name]["reference_score"] == expected_score
        assert modes[name]["physically_feasible"] is True
    assert modes["first8"]["start"] is None
    assert modes["first8"]["reference_score"] == 0
    assert modes["first8"]["physically_feasible"] is None

    assert modes["full"]["resources"]["scalar_disclosures"] == 21
    assert modes["full"]["resources"]["payload_bytes"] == 58
    assert modes["first8"]["resources"]["scalar_disclosures"] == 19
    assert modes["first8"]["resources"]["payload_bytes"] == 52
    assert modes["cursor"]["resources"] == {
        "round_trips": 2,
        "seat_exchanges": 10,
        "scalar_disclosures": 10,
        "distinct_disclosed_starts": 7,
        "request_payload_bytes": 15,
        "response_payload_bytes": 18,
        "payload_bytes": 33,
    }
