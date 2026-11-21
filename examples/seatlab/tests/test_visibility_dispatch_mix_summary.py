import json
import math

from examples.seatlab.team_report import _registered_fingerprint
from examples.seatlab.visibility.dispatch_mix_plan import (
    CLAUDE,
    DIRECT,
    EPISODES,
    SEEDS,
    grid,
)
from examples.seatlab.visibility.dispatch_mix_summary import summarize

_CELLS = {
    (cell["seed"], cell["repetition"], cell["condition"], cell["pairing"]): cell
    for cell in grid()
}


def _row(i, repeat, condition, pairing, score=0.5, **extra):
    seed = SEEDS[i]
    return {
        **_CELLS[seed, repeat, condition, pairing],
        "status": "valid",
        "native_score": score,
        "fingerprint": _registered_fingerprint(seed, EPISODES),
        "inventory_exact": 4,
        "actions": [
            {
                "id": shipment,
                "choice": "A",
                "changed": bool(shipment % 2),
                "optimal": shipment % 2 == 0,
                "feasible": True,
            }
            for shipment in range(EPISODES)
        ],
        **extra,
    }


def test_reordered_pairs_use_equal_seed_weight_and_known_partial_scores():
    rows = [
        _row(i, repeat, "reordered", pairing, score)
        for i, repeat, cd, dc in [
            (0, 0, 0, 1),
            (0, 1, 0, 1),
            (1, 0, 1, 0),
            (2, 0, 0.25, 0.5),
        ]
        for pairing, score in [("cd", cd), ("dc", dc)]
    ]
    rows.append(_row(3, 0, "reordered", "dc", 0.75))
    result = summarize(rows)["primary"]
    assert result["registered_pairs"] == 8
    assert result["completed_pairs"] == 4
    assert result["signs"] == {"positive": 3, "negative": 1, "ties": 0}
    assert result["seed_signs"] == {"positive": 2, "negative": 1, "ties": 0}
    assert [block["observed_mean"] for block in result["seed_blocks"]] == [
        1,
        -1,
        0.25,
        None,
    ]
    assert math.isclose(result["observed_seed_mean"], 1 / 12)
    assert result["equal_seed_weight_mean"] is None
    assert result["bounds"] == [-0.25, 0.625]
    assert result["seed_blocks"][3]["repetitions"][0] == {
        "repetition": 0,
        "scores": {"reordered": {"cd": None, "dc": 0.75}},
        "difference": None,
        "bounds": [-0.25, 0.75],
    }

    rows.append(_row(3, 0, "reordered", "cd", 0.75))
    covered = summarize(rows)["primary"]
    assert covered["equal_seed_weight_mean"] == 0.0625
    assert covered["signs"]["ties"] == covered["seed_signs"]["ties"] == 1


def test_role_sign_and_interaction_bounds_use_four_native_scores():
    report = summarize(
        [
            _row(0, 0, "fresh", "cd", 0.625),
            _row(0, 0, "fresh", "dc", 0.125),
            _row(0, 0, "reordered", "cd", 0.25),
            _row(0, 0, "reordered", "dc", 0.875),
            _row(0, 1, "reordered", "dc", 0.75),
        ]
    )
    assert report["primary"]["observed_seed_mean"] == 0.625
    assert report["fresh"]["observed_seed_mean"] == -0.5
    assert report["primary"]["signs"]["positive"] == 1
    assert report["fresh"]["signs"]["negative"] == 1
    effect = report["interaction"]
    assert effect["registered_blocks"] == 8
    assert effect["completed_blocks"] == 1
    assert effect["observed_seed_mean"] == 1.125
    assert effect["bounds"] == [-1.515625, 1.859375]
    assert effect["seed_blocks"][0]["repetitions"][1]["bounds"] == [-1.25, 1.75]
    assert report["arms"]["reordered"]["dc"]["requested_models"] == {
        "inventory": DIRECT,
        "dispatch": CLAUDE,
    }


def test_malformed_duplicate_foreign_and_unresolved_rows_are_not_evidence():
    secret = "/private/mock-home/api-key=secret"
    duplicate = _row(0, 0, "reordered", "dc")
    bad_pairing = {**_row(0, 0, "reordered", "cd"), "pairing": "dc"}
    wrong_world = _row(
        1, 0, "fresh", "cd", fingerprint=_registered_fingerprint(SEEDS[2], EPISODES)
    )
    bad_rep = {**_row(1, 0, "fresh", "dc"), "repetition": False}
    swapped_models = _row(
        2, 0, "fresh", "cd", requested_models={"inventory": DIRECT, "dispatch": CLAUDE}
    )
    missing = _row(2, 0, "fresh", "dc", status="missing")
    incomplete = _row(2, 0, "reordered", "cd", status="incomplete", error=secret)
    bad_choice = _row(2, 0, "reordered", "dc")
    bad_choice["actions"][0]["choice"] = secret
    bad_exact = _row(3, 0, "fresh", "cd", inventory_exact=0.5)
    already_invalid = _row(3, 0, "fresh", "dc", status="invalid_evidence")
    nonfinite = _row(3, 0, "reordered", "cd", float("nan"))
    duplicate_id = _row(3, 0, "reordered", "dc")
    duplicate_id["actions"][1] = duplicate_id["actions"][0].copy()
    good = _row(3, 1, "fresh", "cd", extra={"exception_info": secret})
    rows = [
        duplicate,
        dict(duplicate),
        bad_pairing,
        wrong_world,
        bad_rep,
        swapped_models,
        missing,
        incomplete,
        bad_choice,
        bad_exact,
        already_invalid,
        nonfinite,
        duplicate_id,
        good,
        {"cell_id": secret},
        {"cell_id": []},
        [secret],
    ]
    result = summarize(rows)
    assert result == summarize(reversed(rows))
    assert result["counts"]["valid_trials"] == 1
    assert result["counts"]["status"] == {
        "valid": 1,
        "invalid_evidence": 10,
        "incomplete": 1,
        "missing": 20,
    }
    assert result["counts"]["unregistered_rows"] == 3
    assert result["primary"]["completed_pairs"] == 0
    assert result["primary"]["bounds"] == [-1, 1]
    assert secret not in json.dumps(result, allow_nan=False)


def test_repeat_disagreement_matches_shipment_ids_not_rewards_or_list_order():
    left = _row(0, 0, "reordered", "dc", 0.75, inventory_exact=6)
    right = _row(0, 1, "reordered", "dc", 0.75, inventory_exact=3)
    right["actions"][3]["choice"] = "D"
    right["actions"].reverse()
    report = summarize(
        [
            right,
            _row(1, 1, "reordered", "cd"),
            _row(2, 0, "reordered", "cd"),
            _row(3, 0, "fresh", "dc"),
            left,
        ]
    )
    counts = report["counts"]
    assert counts["registered_trials"] == 32
    assert counts["valid_trials"] == 5
    assert counts["registered_worlds"] == counts["observed_worlds"] == 4
    assert (
        counts["registered_distinct_shipments"]
        == counts["observed_distinct_shipments"]
        == 24
    )
    assert counts["registered_shipment_observations"] == 192
    assert counts["valid_shipment_observations"] == 30
    repeats = report["repeats"]
    assert repeats["registered_comparisons"] == 16
    assert repeats["completed_comparisons"] == 1
    assert {
        (group["seed"], group["condition"], group["pairing"]): group[
            "disagreeing_shipment_ids"
        ]
        for group in repeats["groups"]
        if group["disagreeing_shipment_ids"] is not None
    } == {(SEEDS[0], "reordered", "dc"): [3]}
    assert repeats["choice_disagreement"] == {
        "count": 1,
        "observed_denominator": 6,
        "registered_denominator": 96,
        "observed_rate": 1 / 6,
    }
    arm = report["arms"]["reordered"]["dc"]
    assert arm["inventory_exact"] == {
        "count": 9,
        "observed_denominator": 12,
        "registered_denominator": 48,
        "observed_rate": 0.75,
    }
    assert arm["optimal"] == {
        "changed": {
            "count": 0,
            "observed_denominator": 6,
            "registered_denominator": 24,
            "observed_rate": 0,
        },
        "stable": {
            "count": 6,
            "observed_denominator": 6,
            "registered_denominator": 24,
            "observed_rate": 1,
        },
    }
