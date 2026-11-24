import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any

import pytest

from examples.seatlab import team_driver
from examples.seatlab.capsule import view_hash
from examples.seatlab.dispatch_returns import reduce_returns

_SEATLAB = Path(__file__).resolve().parents[1]
_STUDY = _SEATLAB / "results" / "native-model-primary.json"
_WORLD = _SEATLAB.parent / "tasks" / "dispatch-handoff" / "environment" / "seatworld.py"
_COUNT_KEYS = (
    "qualified_different_latest",
    "qualified_different_last",
    "qualified_different_neither",
    "qualified_same_best",
    "qualified_same_other",
    "unqualified_reports",
)
_EXPECTED = (
    (2001, "SS", (0, 4, 0, 8, 0, 0)),
    (2001, "SM", (4, 0, 0, 8, 0, 0)),
    (2001, "MS", (0, 4, 0, 8, 0, 0)),
    (2001, "MM", (4, 0, 0, 8, 0, 0)),
    (2003, "MM", (3, 0, 0, 9, 0, 0)),
    (2003, "MS", (0, 3, 0, 9, 0, 0)),
    (2003, "SM", (1, 0, 2, 7, 2, 0)),
    (2003, "SS", (0, 3, 0, 9, 0, 0)),
)


@pytest.fixture
def retained() -> tuple[dict[str, Any], str]:
    raw = _STUDY.read_bytes()
    return json.loads(raw), sha256(raw).hexdigest()


@pytest.fixture(scope="module")
def world_type() -> type:
    return run_path(str(_WORLD))["World"]


def _cell(study: dict[str, Any], cell_id: str) -> dict[str, Any]:
    return next(cell for cell in study["cells"] if cell["cell_id"] == cell_id)


def _cases(world_type: type, cell: dict[str, Any]) -> list[dict[str, Any]]:
    return world_type(
        {
            "schema": "seatlab.scenario/v1",
            "seed": cell["seed"],
            "episodes": team_driver.EPISODES,
            "condition": cell["condition"],
        }
    ).evidence()["cases"]


def _reduce_variant(study: dict[str, Any], world_type: type) -> dict[str, Any]:
    raw = json.dumps(study, allow_nan=False).encode("utf-8")
    return reduce_returns(
        json.loads(raw),
        sha256(raw).hexdigest(),
        team_driver._grid(),
        team_driver.EPISODES,
        world_type,
    )


def test_frozen_counts_and_grid_order(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, pin = retained
    grid = team_driver._grid()[::-1]
    observed = reduce_returns(study, pin, grid, team_driver.EPISODES, world_type)
    assert observed == {
        "schema": "seatlab.dispatch-return-diagnostic/v1",
        "study_sha256": pin,
        "coverage": {
            "registered": 16,
            "measured": 12,
            "invalid": 1,
            "unmeasured": 0,
            "not_attempted": 3,
        },
        "teams": [
            {
                "seed": seed,
                "pairing": pairing,
                "status": "measured",
                "counts": dict(zip(_COUNT_KEYS, counts, strict=True)),
            }
            for seed, pairing, counts in reversed(_EXPECTED)
        ],
    }


def test_wrong_inventory_report_is_unqualified_not_a_receiver_outcome(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, pin = retained
    original = deepcopy(study)
    changed = deepcopy(study)
    cell = _cell(changed, "s2001-reordered-MS")
    metrics = cell["metrics"]
    cases = _cases(world_type, cell)
    case = cases[3]
    row = metrics["rows"][3]
    assert case["before"] != case["after"]
    assert row["choice"] == max(case["before"], key=case["utilities"].__getitem__)
    assert not row["optimal"]
    delivered = [
        {
            "id": other["id"],
            "messages": [
                {"revision": 2, "available": other["after"]},
                {"revision": 1, "available": other["before"]},
            ],
        }
        for other in cases
    ]
    assert view_hash(delivered) == metrics["delivered_messages_sha256"]
    delivered[3]["messages"][0]["available"] = case["before"]
    row["revision_2_correct"] = False
    row["delivered_messages_sha256"] = view_hash(delivered[3]["messages"])
    metrics["delivered_messages_sha256"] = view_hash(delivered)
    metrics["inventory_copy_fidelity"]["revision_2_correct"] -= 1
    metrics["inventory_copy_fidelity"]["both_correct"] -= 1

    baseline = reduce_returns(
        study, pin, team_driver._grid(), team_driver.EPISODES, world_type
    )
    observed = _reduce_variant(changed, world_type)
    expected = deepcopy(baseline["teams"])
    counts = next(
        team["counts"]
        for team in expected
        if (team["seed"], team["pairing"]) == (2001, "MS")
    )
    counts["qualified_different_last"] -= 1
    counts["unqualified_reports"] += 1
    assert observed["teams"] == expected
    assert observed["coverage"] == baseline["coverage"]
    assert study == original


def test_unmeasured_reordered_attempt_has_no_counts(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, _ = retained
    _cell(study, "s2003-reordered-MM").update(
        status="unmeasured",
        error="native_grade_missing",
        metrics=None,
        fingerprint=None,
    )
    study["counts"]["measured"] -= 1
    study["counts"]["unmeasured"] += 1
    study["counts"]["by_model_actors"]["2"]["measured"] -= 1
    observed = _reduce_variant(study, world_type)
    assert observed["coverage"] == {
        "registered": 16,
        "measured": 11,
        "invalid": 1,
        "unmeasured": 1,
        "not_attempted": 3,
    }
    assert observed["teams"][4] == {
        "seed": 2003,
        "pairing": "MM",
        "status": "unmeasured",
        "counts": None,
    }


def test_grid_seed_must_match_declared_id(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, pin = retained
    grid = team_driver._grid()
    grid[0]["seed"] = 2003
    with pytest.raises(ValueError, match=r"grid|cell|seed"):
        reduce_returns(study, pin, grid, team_driver.EPISODES, world_type)


def test_other_registered_world_fingerprint_is_rejected(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, _ = retained
    cell = _cell(study, "s2001-reordered-MS")
    cell["fingerprint"] = _cell(study, "s2003-reordered-MS")["fingerprint"]
    with pytest.raises(ValueError, match="fingerprint"):
        _reduce_variant(study, world_type)


@pytest.mark.parametrize("revision", (1, 2), ids=("stale", "fresh"))
def test_single_revision_digest_cannot_qualify_a_reordered_row(
    retained: tuple[dict[str, Any], str], world_type: type, revision: int
) -> None:
    study, _ = retained
    cell = _cell(study, "s2001-reordered-MS")
    case = _cases(world_type, cell)[3]
    available = case["before"] if revision == 1 else case["after"]
    cell["metrics"]["rows"][3]["delivered_messages_sha256"] = view_hash(
        [{"revision": revision, "available": available}]
    )
    with pytest.raises(ValueError, match=r"delivered|digest"):
        _reduce_variant(study, world_type)


def test_wrong_report_flag_cannot_keep_correct_reordered_digest(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, _ = retained
    metrics = _cell(study, "s2001-reordered-MS")["metrics"]
    metrics["rows"][3]["revision_1_correct"] = False
    metrics["inventory_copy_fidelity"]["revision_1_correct"] -= 1
    metrics["inventory_copy_fidelity"]["both_correct"] -= 1
    with pytest.raises(ValueError, match=r"delivered|digest|revision_1_correct"):
        _reduce_variant(study, world_type)


def test_wrong_native_grade_is_rejected_even_when_total_agrees(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, _ = retained
    metrics = _cell(study, "s2001-reordered-MS")["metrics"]
    metrics["rows"][1]["optimal"] = True
    metrics["optimal"] += 1
    with pytest.raises(ValueError, match="optimal"):
        _reduce_variant(study, world_type)


def test_swapped_role_actors_are_rejected_even_when_model_count_agrees(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, _ = retained
    seats = _cell(study, "s2001-reordered-MS")["seats"]
    seats["inventory"], seats["dispatch"] = seats["dispatch"], seats["inventory"]
    with pytest.raises(ValueError, match=r"seat|role|actor|pairing"):
        _reduce_variant(study, world_type)


def test_duplicate_measured_attempt_is_rejected(
    retained: tuple[dict[str, Any], str], world_type: type
) -> None:
    study, _ = retained
    cell = _cell(study, "s2001-reordered-MS")
    cell["trial_id"] = _cell(study, "s2003-reordered-MS")["trial_id"]
    with pytest.raises(ValueError, match=r"trial_id|UUID|duplicate"):
        _reduce_variant(study, world_type)
