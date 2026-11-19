"""Unit tests for the receipt-board job census join."""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "seatlab"
if str(_EXAMPLES) not in sys.path:
    sys.path.insert(0, str(_EXAMPLES))

census = importlib.import_module("receipt_job_census")
team_census = importlib.import_module("team_census")

JOB = "2bd2aeca-4cee-46aa-ba0e-37fce0bcc309"
FULL = "10e6cc6c-3107-4ad4-86cf-b91d3951b498"
FIRST = "22e6cc6c-3107-4ad4-86cf-b91d3951b499"


def _receipts(arm: str) -> dict[str, dict[str, Any]]:
    case = census._DEFAULT_CASE
    views = {
        role: list(case[role]) if arm == "full" else [case[role][0]]
        for role in census.ROLES
    }
    return {
        role: {
            "action": views[role][-1],
            "allowed_options": views[role],
            "acknowledged_sha256": "a" * 64,
        }
        for role in census.ROLES
    }


def _trial(
    path: str,
    arm: str,
    trial_id: str,
    *,
    reward: float | None = 1.0,
    tokens: int | None = None,
) -> dict[str, Any]:
    seats = {}
    for role in census.ROLES:
        seats[role] = {
            "present": True,
            "has_exception": False,
            "n_input_tokens": tokens,
            "n_cache_tokens": tokens,
            "n_output_tokens": tokens,
        }
    return {
        "_path": path,
        "arm": arm,
        "arm_error": None,
        "choices_match_expected": True,
        "native_job_id": JOB,
        "native_trial_id": trial_id,
        "job_id_matches_parent": True,
        "native_team_reward": reward,
        "seat_count": 2,
        "seats": seats,
        "world_receipts": _receipts(arm),
        "issues": [],
    }


def _row(
    path: str | None, trial_id: str | None, job_id: str | None, reward: float | None
) -> Any:
    return team_census.CensusRowSnapshot(
        source_trial_dir=path,
        trial_id=trial_id,
        job_id=job_id,
        reward=reward,
        projection_error=None,
        raw_trial_id_present=trial_id is not None,
    )


@pytest.mark.unit
def test_job_trial_join_counts_shared_reward_once_and_keeps_null_tokens() -> None:
    report = census.compare_job_census(
        {
            "native_job_id": JOB,
            "n_total_trials": 2,
            "discovered_trials": 2,
            "issues": [],
        },
        [
            _trial("/job/full", "full", FULL, tokens=4),
            _trial("/job/first", "first", FIRST, tokens=None),
        ],
        [
            _row("/job/full", FULL, JOB, 1.0),
            _row("/job/first", FIRST, JOB, 1.0),
            _row("/other/orphan", "33e6cc6c-3107-4ad4-86cf-b91d3951b400", JOB, 1.0),
        ],
    )
    assert report["counts"]["seat_records_total"] == 4
    assert report["counts"]["raw_team_reward_sum"] == 2.0
    assert report["counts"]["census_matched_by_job_and_trial_uuid"] == 2
    assert report["counts"]["census_orphans"] == 1
    assert report["role_usage"]["p1"]["sum_input_tokens"] == 4
    assert report["role_usage"]["p1"]["known_input_tokens_trials"] == 1
    assert report["role_usage"]["p1"]["null_input_tokens_trials"] == 1
    encoded = json.dumps(report)
    assert "/job/" not in encoded
    assert FULL in encoded


@pytest.mark.unit
def test_missing_receipts_and_overridden_reward_are_not_success(tmp_path: Path) -> None:
    trial = tmp_path / "named-full"
    trial.mkdir()
    arm, _, choices_ok, error = census._bind_arm(
        trial, *census._arm_spec(census._DEFAULT_CASE)
    )
    assert arm is None and choices_ok is None and error == "missing_world_receipts"
    broken = _trial("/job/full", "full", FULL, reward=None)
    broken["issues"] = ["error_policy_overridden"]
    report = census.compare_job_census(
        {
            "native_job_id": JOB,
            "n_total_trials": 1,
            "discovered_trials": 1,
            "issues": [],
        },
        [broken],
        [_row("/job/full", FULL, JOB, 1.0)],
    )
    assert report["trials"][0]["native_team_reward"] is None
    assert report["trials"][0]["census"]["reward_matches_native"] is False
    assert report["counts"]["raw_team_rewards_known"] == 0


@pytest.mark.unit
def test_prepared_plan_rejects_duplicate_arm(tmp_path: Path) -> None:
    slot = {
        "arm": "full",
        "expected_views": {role: {"options": ["a", "b"]} for role in census.ROLES},
        "expected_actions": {role: "b" for role in census.ROLES},
    }
    plan = tmp_path / "job-plan.json"
    plan.write_text(json.dumps({"slots": [slot, slot]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid or duplicate arm"):
        census._load_prepared(plan)


@pytest.mark.unit
def test_inspect_job_scans_job_dir_read_only_and_keeps_scratch_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = tmp_path / "jobs" / "receipt-board-job"
    trial = job / "receipt-board-full"
    trial.mkdir(parents=True)
    (job / "result.json").write_text(
        json.dumps(
            {
                "id": JOB,
                "finished_at": "2026-11-19T12:00:00Z",
                "n_total_trials": 1,
                "stats": {},
            }
        ),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps(
            {"id": FULL, "task_name": "seatlab/receipt-board", "trial_name": "full"}
        ),
        encoding="utf-8",
    )
    repo = tmp_path / "eval-lab"
    (repo / "src" / "evallab").mkdir(parents=True)
    seen: dict[str, Any] = {}

    def fake_load(path: Path) -> Any:
        return SimpleNamespace(
            id=FULL,
            primary_reward=1.0,
            result={
                "id": FULL,
                "finished_at": "2026-11-19T12:00:00Z",
                "reward_valid": True,
                "config": {"job_id": JOB},
                "verifier_result": {"rewards": {"reward": 1.0}},
                "seat_results": [
                    {"seat": "p1", "agent_result": {"n_input_tokens": None}},
                    {"seat": "p2", "agent_result": {}},
                ],
            },
        )

    def fake_rows(
        repo_root: Path, scratch: Path, roots: list[Path]
    ) -> tuple[list[Any], int]:
        seen.update(roots=list(roots), empty=not any(scratch.iterdir()))
        return [_row(str(trial.resolve()), FULL, JOB, 1.0)], 1

    evallab = types.ModuleType("evallab")
    evallab.__file__ = str(repo / "src" / "evallab" / "__init__.py")
    results = types.ModuleType("evallab.results")
    results.load_trial = fake_load
    monkeypatch.setitem(sys.modules, "evallab", evallab)
    monkeypatch.setitem(sys.modules, "evallab.results", results)
    monkeypatch.setattr(census.team_census, "_load_census_rows", fake_rows)
    report = census.inspect_job(job, repo)
    assert seen["roots"] == [job.resolve()]
    assert seen["empty"] is True
    assert report["derived_root_empty_after"] is True
    assert report["job"]["native_job_id"] == JOB
    assert report["counts"]["seat_records_total"] == 2
    assert report["trials"][0]["native_team_reward"] == 1.0
    assert report["trials"][0]["seats"]["p1"]["n_input_tokens"] is None
    assert str(job) not in json.dumps(report)
