import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.capsule import view_hash
from examples.seatlab.export_capsules import reconstructed_views
from examples.seatlab.visibility import pair_report

_ROOT = Path(__file__).resolve().parents[3]
_TASK = _ROOT / "examples/tasks/dispatch-handoff"
_WORLD = run_path(str(_TASK / "environment/seatworld.py"))["World"]
_SCORE = run_path(str(_TASK / "tests/score.py"))["score"]
_ROLES = ("inventory", "dispatch")
_REVISION = "DeepSeek-V4.1-Flash-1017"


def _trial(
    seed: int = 3101,
    repetition: int = 0,
    condition: str = "fresh",
    *,
    optimal_count: int = 2,
    alternate_losers: tuple[int, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    world = _WORLD(
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "condition": condition,
            "episodes": 4,
        }
    )
    inventory = world.view("inventory")["cases"]
    world.submit("inventory", {"rows": inventory})
    choices = []
    for case in world.evidence()["cases"]:
        decision_id = case["id"]
        optimal = max(case["after"], key=case["utilities"].__getitem__)
        losers = [bay for bay in "ABCD" if bay != optimal]
        bay = optimal
        if decision_id >= optimal_count:
            bay = losers[1 if decision_id in alternate_losers else 0]
        choices.append({"id": decision_id, "bay": bay})
    world.submit("dispatch", {"rows": choices})
    dossier = world.evidence()
    score = _SCORE(dossier)
    cell_id = f"s{seed}-r{repetition}-{condition}"
    trial_id = str(
        UUID(int=seed * 8 + repetition * 2 + (condition == "silent"), version=4)
    )
    marker = {
        "schema": "seatlab.visibility-native-cell/v1",
        "cell_id": cell_id,
        "seed": seed,
        "repetition": repetition,
        "condition": condition,
        "pairing": "MM",
        "requested_model": "deepseek-flash",
        "episodes": 4,
        "reserved_model_turns": 2,
        "trial_name": f"seatlab-team-{sha256(cell_id.encode()).hexdigest()[:24]}",
        "task_name": None,
        "trial_id": None,
        "native_returncode": None,
        "native_score": None,
        "outcome_valid": False,
        "error": "preparation_failed",
        "fingerprint": None,
        "actions": None,
        "seats": dict.fromkeys(_ROLES),
    }
    views = reconstructed_views(dossier)
    receipt = {
        **marker,
        "task_name": "harbor/dispatch-handoff",
        "trial_id": trial_id,
        "native_returncode": 0,
        "native_score": score["optimal"] / score["episodes"],
        "outcome_valid": True,
        "error": None,
        "fingerprint": view_hash(
            [
                {key: row[key] for key in ("id", "utilities", "before", "after")}
                for row in dossier["cases"]
            ]
        ),
        "actions": [
            {**grade, "choice": choice["bay"]}
            for grade, choice in zip(score["rows"], choices, strict=True)
        ],
        "seats": {
            role: {
                "agent_info": {
                    "name": "seatlab-direct-flash",
                    "version": "1.0.0",
                    "model_info": {"provider": "deepseek", "name": "deepseek-flash"},
                },
                "invocation": {
                    "schema": "seatlab.visibility-native-seat/v1",
                    "role": role,
                    "stage": "complete",
                    "model_turns": 1,
                    "run_id": cell_id,
                    "requested_model": "deepseek-flash",
                    "served_model": _REVISION
                    if role == "dispatch"
                    else "deepseek-flash",
                    "view_sha256": view_hash(views[role]),
                    "prompt_sha256": view_hash(views[role]["cases"]),
                    "decision_sha256": view_hash(
                        choices if role == "dispatch" else inventory
                    ),
                },
            }
            for role in _ROLES
        },
    }
    return marker, receipt


def _seal(path: Path, row: str | dict[str, Any]) -> None:
    path.write_text(row if isinstance(row, str) else json.dumps(row), encoding="utf-8")
    path.chmod(0o400)


def _write(study: Path, marker: dict[str, Any], receipt: dict[str, Any] | None) -> Path:
    directory = study / "cells" / marker["cell_id"]
    directory.mkdir(parents=True)
    _seal(directory / "attempt.json", marker)
    if receipt is not None:
        _seal(directory / "receipt.json", receipt)
    return directory


def _cell(report: dict[str, Any], cell_id: str) -> dict[str, Any]:
    return next(cell for cell in report["cells"] if cell["cell_id"] == cell_id)


def test_complete_grid_preserves_missing_and_failed_first_attempts(
    tmp_path: Path,
) -> None:
    marker, receipt = _trial()
    _write(tmp_path, marker, receipt)
    marker, receipt = _trial(condition="silent")
    receipt.update(
        outcome_valid=False,
        error="native_evidence_invalid",
        actions=None,
        fingerprint=None,
    )
    receipt["seats"]["dispatch"]["invocation"].update(
        stage="invalid_model_decisions", served_model=None
    )
    _write(tmp_path, marker, receipt)
    _write(tmp_path, _trial(repetition=1)[0], None)
    (tmp_path / "cells/s3101-r1-silent").mkdir()

    report = pair_report.collect(tmp_path)
    assert report["counts"] == {
        "scheduled": 24,
        "attempted": 4,
        "valid": 1,
        "incomplete": 3,
        "unattempted": 20,
        "scheduled_pairs": 12,
        "complete_pairs": 0,
    }
    cells = report["cells"]
    assert [cell["cell_id"] for cell in cells[:6]] == [
        "s3101-r0-fresh",
        "s3101-r0-silent",
        "s3101-r1-silent",
        "s3101-r1-fresh",
        "s3101-r2-fresh",
        "s3101-r2-silent",
    ]
    assert len({cell["cell_id"] for cell in cells}) == 24
    assert cells[2]["reason"] == "attempt_missing"
    assert cells[3]["reason"] == "receipt_missing"
    assert cells[4]["status"] == "unattempted"
    assert cells[1]["reason"] == "native_evidence_invalid"
    assert cells[1]["native_score"] is None
    assert cells[1]["seats"] == {
        "inventory": {
            "requested_model": "deepseek-flash",
            "returned_model": "deepseek-flash",
        },
        "dispatch": {"requested_model": "deepseek-flash", "returned_model": None},
    }
    pairs = report["pairs"]
    assert {(pair["seed"], pair["repetition"]) for pair in pairs} == {
        (seed, rep) for seed in (3101, 3103, 3109, 3119) for rep in range(3)
    }
    assert pairs[0]["fresh_score"] == 0.5
    assert pairs[0]["silent_score"] is None
    assert pairs[0]["dispatch_choices"] is None
    assert all(pair["fresh_minus_silent"] is None for pair in pairs)
    assert report["effect"]["equal_seed_weight_mean"] is None
    assert report["across_arms_dispatch_choices"]["changed_decisions"] is None


def test_seed_equal_weight_and_same_id_choice_variation(tmp_path: Path) -> None:
    for seed, repetitions, fresh_score, silent_score in (
        (3101, (0, 1), 2, 0),
        (3103, (0,), 0, 1),
        (3109, (0,), 0, 0),
        (3119, (1,), 1, 0),
    ):
        for rep in repetitions:
            _write(
                tmp_path,
                *_trial(
                    seed,
                    rep,
                    optimal_count=fresh_score,
                    alternate_losers=(2,) if seed == 3101 and rep == 1 else (),
                ),
            )
            _write(tmp_path, *_trial(seed, rep, "silent", optimal_count=silent_score))
    report = pair_report.collect(tmp_path)
    assert report["independent_task_seeds"] == 4
    assert report["counts"]["complete_pairs"] == 5
    blocks = report["effect"]["seed_blocks"]
    assert [block["complete_pairs"] for block in blocks] == [2, 1, 1, 1]
    assert [block["fresh_minus_silent"] for block in blocks] == [0.5, -0.25, 0, 0.25]
    assert report["effect"]["equal_seed_weight_mean"] == 0.125  # Not pair-weighted 0.2.
    assert report["pairs"][0]["dispatch_choices"] == {
        "matched_decisions": 4,
        "changed_decisions": 2,
    }
    assert report["pairs"][1]["dispatch_choices"]["changed_decisions"] == 3
    assert report["across_arms_dispatch_choices"] == {
        "scheduled_comparisons": 12,
        "complete_comparisons": 5,
        "matched_decisions": 20,
        "changed_decisions": 7,
    }
    fresh, silent = report["within_arm_repeats"][:2]
    assert (
        fresh["dispatch_choices"]["changed_decisions"] == 1
    )  # Same score, different choice.
    assert silent["dispatch_choices"]["changed_decisions"] == 0
    assert fresh["comparisons"][1]["dispatch_choices"] is None
    assert fresh["world_fingerprint"]["status"] == "identical"
    assert fresh["evidence_hashes"]["inventory"]["view_sha256"]["status"] == "identical"
    assert fresh["evidence_hashes"]["dispatch"]["decision_sha256"] == {
        "status": "variable",
        "observations": 2,
        "distinct": 2,
    }
    assert (
        silent["evidence_hashes"]["dispatch"]["decision_sha256"]["status"]
        == "identical"
    )
    assert (
        report["within_arm_repeats"][2]["world_fingerprint"]["status"] == "unsupported"
    )
    assert report["within_arm_dispatch_choices"]["fresh"]["matched_decisions"] == 4


def test_missing_seed_block_suppresses_only_the_overall_mean(tmp_path: Path) -> None:
    for seed in (3101, 3103, 3119):
        _write(tmp_path, *_trial(seed, optimal_count=4))
        _write(tmp_path, *_trial(seed, condition="silent", optimal_count=0))
    effect = pair_report.collect(tmp_path)["effect"]
    assert effect["seed_blocks"] == [
        {"seed": 3101, "complete_pairs": 1, "fresh_minus_silent": 1.0},
        {"seed": 3103, "complete_pairs": 1, "fresh_minus_silent": 1.0},
        {"seed": 3109, "complete_pairs": 0, "fresh_minus_silent": None},
        {"seed": 3119, "complete_pairs": 1, "fresh_minus_silent": 1.0},
    ]
    assert effect["equal_seed_weight_mean"] is None


def test_choice_comparison_uses_intersection_not_position() -> None:
    left = pair_report._Evidence(0.0, {0: "A", 2: "A", 4: "B"}, {}, "")
    right = pair_report._Evidence(0.0, {1: "A", 4: "B", 2: "B"}, {}, "")
    assert pair_report._compare(left, right) == {
        "matched_decisions": 2,
        "changed_decisions": 1,
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("seed", 3103, "receipt_identity_mismatch"),
        ("repetition", False, "receipt_identity_mismatch"),
        ("condition", "silent", "receipt_identity_mismatch"),
        ("pairing", "SS", "receipt_identity_mismatch"),
        ("requested_model", "some-other-alias", "receipt_identity_mismatch"),
        ("task_name", "private-task", "native_completion_invalid"),
        ("trial_name", "seatlab-team-" + "0" * 24, "receipt_identity_mismatch"),
        ("trial_id", str(UUID(int=0)), "native_completion_invalid"),
        ("native_returncode", False, "native_completion_invalid"),
        ("native_score", True, "native_score_invalid"),
        ("native_score", -0.25, "native_score_invalid"),
        ("native_score", 1.25, "native_score_invalid"),
        ("native_score", 0.75, "native_score_mismatch"),
        ("fingerprint", "0" * 64, "world_fingerprint_mismatch"),
    ],
)
def test_cell_identity_native_score_and_world_fingerprint_fail_closed(
    tmp_path: Path, field: str, value: Any, reason: str
) -> None:
    marker, receipt = _trial()
    receipt[field] = value
    _write(tmp_path, marker, receipt)
    cell = _cell(pair_report.collect(tmp_path), marker["cell_id"])
    assert cell["status"] == "incomplete"
    assert cell["reason"] == reason
    assert cell["native_score"] is None


@pytest.mark.parametrize(
    "change",
    [
        "reorder",
        "duplicate_id",
        "bool_id",
        "missing_key",
        "extra_key",
        "choice",
        "flag",
    ],
)
def test_rejects_action_mutations_even_when_claimed_score_is_unchanged(
    tmp_path: Path, change: str
) -> None:
    marker, receipt = _trial()
    actions = receipt["actions"]
    if change == "reorder":
        actions.reverse()
    elif change == "duplicate_id":
        actions[0]["id"] = 1
    elif change == "bool_id":
        actions[0]["id"] = False
    elif change == "missing_key":
        del actions[0]["feasible"]
    elif change == "extra_key":
        actions[0]["extra"] = None
    elif change == "choice":
        actions[0]["choice"] = "private response text"
    elif change == "flag":
        actions[0]["optimal"] = 1
    _write(tmp_path, marker, receipt)
    cell = _cell(pair_report.collect(tmp_path), marker["cell_id"])
    assert cell["reason"] == "action_rows_invalid"


@pytest.mark.parametrize(
    ("role", "key", "value", "reason"),
    [
        (
            "inventory",
            "served_model",
            "private provider text",
            "seat_attestation_invalid",
        ),
        ("dispatch", "served_model", None, "seat_attestation_invalid"),
        ("dispatch", "run_id", "s3101-r0-silent", "seat_attestation_invalid"),
        ("dispatch", "model_turns", True, "seat_attestation_invalid"),
        ("inventory", "stage", "submission_unacknowledged", "seat_attestation_invalid"),
        ("inventory", "decision_sha256", "A" * 64, "evidence_hash_invalid"),
        ("dispatch", "prompt_sha256", "not a digest", "evidence_hash_invalid"),
        ("dispatch", "decision_sha256", "0" * 64, "dispatch_decision_hash_mismatch"),
    ],
)
def test_no_fallback_or_unverified_digest_is_used(
    tmp_path: Path, role: str, key: str, value: Any, reason: str
) -> None:
    marker, receipt = _trial()
    receipt["seats"][role]["invocation"][key] = value
    _write(tmp_path, marker, receipt)
    report = pair_report.collect(tmp_path)
    assert _cell(report, marker["cell_id"])["reason"] == reason
    assert "private provider text" not in json.dumps(report)
    group = report["within_arm_repeats"][0]
    assert group["evidence_hashes"][role]["decision_sha256"]["observations"] == 0


def test_duplicate_native_uuid_invalidates_all_uses(tmp_path: Path) -> None:
    marker, receipt = _trial()
    trial_id = receipt["trial_id"]
    _write(tmp_path, marker, receipt)
    marker, receipt = _trial(condition="silent")
    receipt.update(
        trial_id=trial_id, outcome_valid=False, error="native_evidence_invalid"
    )
    _write(tmp_path, marker, receipt)
    report = pair_report.collect(tmp_path)
    assert report["counts"]["valid"] == 0
    assert report["counts"]["incomplete"] == 2
    assert [cell["reason"] for cell in report["cells"][:2]] == [
        "duplicate_native_trial_id",
        "duplicate_native_trial_id",
    ]
    assert trial_id not in json.dumps(report)


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        ('"native_score": NaN', "receipt_invalid"),
        ('"native_score": 1e999', "native_score_invalid"),
        ('"native_score": 0.5, "native_score": 0.5', "receipt_invalid"),
    ],
)
def test_strict_json_and_finite_reward_validation(
    tmp_path: Path, replacement: str, reason: str
) -> None:
    marker, receipt = _trial()
    directory = _write(tmp_path, marker, None)
    _seal(
        directory / "receipt.json",
        json.dumps(receipt).replace('"native_score": 0.5', replacement),
    )
    assert _cell(pair_report.collect(tmp_path), marker["cell_id"])["reason"] == reason


def test_invalid_marker_cannot_be_replaced_by_a_valid_receipt(tmp_path: Path) -> None:
    marker, receipt = _trial()
    marker["requested_model"] = "gpt-6.2-sol"
    _write(tmp_path, marker, receipt)
    cell = _cell(pair_report.collect(tmp_path), marker["cell_id"])
    assert (cell["status"], cell["reason"]) == (
        "incomplete",
        "attempt_identity_mismatch",
    )
    assert cell["seats"]["dispatch"]["returned_model"] is None


def test_linked_receipt_does_not_supply_a_missing_completion(tmp_path: Path) -> None:
    marker, receipt = _trial()
    directory = _write(tmp_path, marker, None)
    unregistered = tmp_path / "unregistered-receipt.json"
    _seal(unregistered, receipt)
    (directory / "receipt.json").symlink_to(unregistered)
    cell = _cell(pair_report.collect(tmp_path), marker["cell_id"])
    assert (cell["status"], cell["reason"]) == ("incomplete", "receipt_invalid")


def test_unexpected_cell_directory_never_expands_the_grid(tmp_path: Path) -> None:
    (tmp_path / "cells/private-extra-trial").mkdir(parents=True)
    with pytest.raises(ValueError, match="unexpected cell set"):
        pair_report.collect(tmp_path)


def test_cli_is_offline_and_redacts_sources(tmp_path: Path) -> None:
    marker, receipt = _trial()
    private = "unapproved-prompt-or-provider-response"
    receipt["seats"]["dispatch"]["invocation"]["raw_private_data"] = private
    _write(tmp_path, marker, receipt)
    command = [
        sys.executable,
        "-E",
        "-S",
        "-m",
        "examples.seatlab.visibility.pair_report",
        str(tmp_path),
    ]
    result = subprocess.run(
        command, cwd=_ROOT, check=True, capture_output=True, text=True
    )
    expected = pair_report.collect(tmp_path)
    assert result.stdout == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    assert result.stderr == ""
    assert expected["cells"][0]["seats"]["dispatch"] == {
        "requested_model": "deepseek-flash",
        "returned_model": _REVISION,
    }
    for value in (
        str(tmp_path),
        private,
        receipt["trial_id"],
        receipt["trial_name"],
        receipt["fingerprint"],
    ):
        assert value not in result.stdout
