from __future__ import annotations

from itertools import product
from typing import Any

import pytest

from examples.seatlab.visibility.calendar_list_summary import summarize


@pytest.fixture
def rows() -> dict[str, dict[str, Any]]:
    result = {}
    for world, direction, repetition, arm, draw, condition in product(
        (0, 4, 8), (0, 1), (0, 1), ("blind", "peer"), (0, 1), ("rr", "er", "re", "ee")
    ):
        coordinate = f"w{world:02d}-d{direction}-r{repetition}-{arm}"
        run = f"cl-{coordinate}-k{draw}-{condition}"
        result[run] = {
            "run": run,
            "case": f"rx-{coordinate}",
            "world": world,
            "direction": direction,
            "repetition": repetition,
            "source_arm": arm,
            "draw": draw,
            "condition": condition,
            "status": "complete",
            "slot": 0,
            "reward": 0,
        }
    return result


def test_reports_full_registration_and_equal_world_contrasts(rows):
    for row in rows.values():
        if row["condition"] != "rr":
            row.update(slot=1, reward=1)

    report = summarize(reversed(list(rows.values())))
    assert report["counts"] == {
        "registered": 192,
        "valid": 192,
        "model_invalid": 0,
        "missing": 0,
        "scored": 192,
        "wins": 144,
        "valid_losses": 48,
    }
    assert report["by_condition"]["rr"]["valid_losses"] == 48
    assert report["by_world"]["4"]["counts"]["registered"] == 64
    assert report["by_world"]["4"]["by_condition"]["ee"]["wins"] == 16
    assert report["registered_pairs"] == 48
    assert report["historical_contexts"] == 6
    assert report["primary"] == {
        "pairs": 48,
        "worlds": 3,
        "historical_contexts": 6,
        "world_counts": {"0": 16, "4": 16, "8": 16},
        "world_means": {"0": 1, "4": 1, "8": 1},
        "equal_world_mean": 1,
    }
    assert report["accepted_pairs_only"] == report["primary"]
    assert report["all_registered_pair_bounds"] == [1, 1]
    assert report["secondary"]["er_minus_rr"]["equal_world_mean"] == 1
    assert report["secondary"]["re_minus_rr"]["equal_world_mean"] == 1
    assert report["secondary"]["interaction"]["equal_world_mean"] == -1
    assert report["draw_disagreement"]["by_condition"]["rr"] == {
        "registered_pairs": 24,
        "comparable_pairs": 24,
        "different_slots": 0,
    }


def test_missing_and_invalid_separate_bounds_and_accepted_sample(rows):
    for row in rows.values():
        if row["world"] == 0 and row["condition"] == "ee":
            row.update(status="missing", slot=None, reward=None)
        if row["world"] == 4:
            if row["condition"] == "rr":
                row.update(slot=1, reward=1)
            if row["condition"] == "ee":
                row.update(status="model_invalid", slot=None, reward=0)
    rows["cl-w00-d0-r0-blind-k0-ee"].update(status="complete", slot=1, reward=1)
    rows["cl-w04-d0-r0-blind-k0-ee"]["reward"] = None

    report = summarize(rows.values())
    assert report["by_condition"]["ee"] == {
        "registered": 48,
        "valid": 17,
        "model_invalid": 16,
        "missing": 15,
        "scored": 33,
        "wins": 1,
        "valid_losses": 16,
    }
    assert report["primary"]["world_counts"] == {"0": 1, "4": 16, "8": 16}
    assert report["primary"]["world_means"] == {"0": 1, "4": -1, "8": 0}
    assert report["primary"]["historical_contexts"] == 5
    assert report["primary"]["equal_world_mean"] == 0
    assert report["accepted_pairs_only"]["world_counts"] == {"0": 1, "4": 0, "8": 16}
    assert report["accepted_pairs_only"]["equal_world_mean"] is None
    assert report["all_registered_pair_bounds"] == [-5 / 16, 0]
    assert report["all_registered_pair_bounds_by_world"]["4"] == [-1, -1]


def test_primary_withheld_when_world_has_no_pair(rows):
    for row in rows.values():
        if row["world"] == 8 and row["condition"] in ("rr", "ee"):
            row.update(status="missing", slot=None, reward=None)

    report = summarize(rows.values())
    assert report["primary"]["equal_world_mean"] is None
    assert report["primary"]["world_means"] == {"0": 0, "4": 0, "8": None}
    assert report["primary"]["pairs"] == 32
    assert report["all_registered_pair_bounds"] == [-1 / 3, 1 / 3]


def test_match_within_draw_and_compare_two_draws_of_each_prompt(rows):
    base = "cl-w00-d0-r0-blind"
    rows[f"{base}-k0-rr"].update(slot=1, reward=1)
    rows[f"{base}-k0-ee"].update(slot=1, reward=1)
    rows[f"{base}-k0-er"]["slot"] = 2
    rows[f"{base}-k0-re"].update(status="model_invalid", slot=None, reward=0)
    rows[f"{base}-k1-rr"].update(status="missing", slot=None, reward=None)
    rows["cl-w00-d0-r0-peer-k0-er"].update(status="missing", slot=None, reward=None)

    report = summarize(rows.values())
    assert report["primary"]["pairs"] == 47
    assert report["primary"]["equal_world_mean"] == 0
    assert report["all_registered_pair_bounds"] == pytest.approx([-1 / 48, 0])
    for name, delta in [("er_minus_rr", -1), ("re_minus_rr", -1), ("interaction", 2)]:
        metric = report["secondary"][name]
        assert metric["quartets"] == 46
        assert metric["world_means"]["0"] == pytest.approx(delta / 14)
    disagreement = report["draw_disagreement"]
    assert disagreement["by_case"]["rx-w00-d0-r0-blind"] == {
        "rr": None,
        "er": True,
        "re": None,
        "ee": True,
    }
    assert disagreement["by_case"]["rx-w00-d0-r0-peer"]["ee"] is False
    assert disagreement["by_condition"]["er"] == {
        "registered_pairs": 24,
        "comparable_pairs": 23,
        "different_slots": 1,
    }


def test_accepted_only_excludes_invalid_zero_without_losing_world_support(rows):
    rows["cl-w04-d0-r0-blind-k0-rr"].update(slot=1, reward=1)
    rows["cl-w04-d0-r0-blind-k0-ee"].update(status="model_invalid", slot=None)

    report = summarize(rows.values())
    assert report["primary"]["equal_world_mean"] == pytest.approx(-1 / 48)
    assert report["accepted_pairs_only"]["pairs"] == 47
    assert report["accepted_pairs_only"]["equal_world_mean"] == 0


def test_rejects_missing_registration_even_for_unobserved_rows(rows):
    original = rows.pop("cl-w00-d0-r0-blind-k0-rr")
    original.update(status="missing", slot=None, reward=None)
    with pytest.raises(ValueError, match="missing registered replay runs"):
        summarize(rows.values())
    with pytest.raises(ValueError, match="duplicate replay run"):
        summarize([*rows.values(), rows["cl-w00-d0-r0-blind-k1-rr"]])
    original["run"] = "cl-w00-d0-r0-blind-k2-rr"
    with pytest.raises(ValueError, match="unregistered replay run"):
        summarize([*rows.values(), original])


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"case": "rx-w00-d0-r0-peer"}, "coordinates do not match"),
        ({"direction": False}, "coordinates do not match"),
        ({"draw": 1}, "coordinates do not match"),
        ({"reward": False}, "reward must be binary"),
        ({"slot": False}, "inconsistent replay outcome"),
        ({"status": "missing", "slot": None}, "inconsistent replay outcome"),
        (
            {"status": "model_invalid", "slot": None, "reward": 1},
            "inconsistent replay outcome",
        ),
    ],
)
def test_rejects_inconsistent_rows(rows, changes, error):
    rows["cl-w00-d0-r0-blind-k0-rr"].update(changes)
    with pytest.raises(ValueError, match=error):
        summarize(rows.values())
