"""Tests for `harbor job resume --filter-error-type` across trial shapes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from harbor.models.trial.result import (
    ExceptionInfo,
    SeatResult,
    StepResult,
    TrialResult,
)


def _exc(exception_type: str) -> ExceptionInfo:
    return ExceptionInfo(
        exception_type=exception_type,
        exception_message="failed",
        exception_traceback="traceback",
        occurred_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
    )


def _write_trial(
    tmp_path: Path,
    *,
    root: ExceptionInfo | None,
    seat_results: list[SeatResult] | None = None,
    step_results: list[StepResult] | None = None,
) -> tuple[Path, Path]:
    from harbor.models.trial.config import TaskConfig, TrialConfig
    from harbor.models.trial.result import AgentInfo
    from harbor.models.verifier.result import VerifierResult

    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    (job_dir / "config.json").write_text(json.dumps({}))
    trial_dir = job_dir / "trial-1"
    trial_dir.mkdir()

    trial_config = TrialConfig(
        task=TaskConfig(path=Path("/tmp/task")),
        trial_name="trial-1",
    )
    result = TrialResult(
        task_name=trial_config.task.get_task_id().get_name(),
        trial_name="trial-1",
        trial_uri="file:///tmp/trial-1",
        task_id=trial_config.task.get_task_id(),
        source=trial_config.task.source,
        task_checksum="abc123",
        config=trial_config,
        agent_info=AgentInfo(name="test-agent", version="1.0"),
        verifier_result=VerifierResult(rewards={"reward": 0}),
        exception_info=root,
        seat_results=seat_results,
        step_results=step_results,
    )
    (trial_dir / "result.json").write_text(result.model_dump_json())
    return job_dir, trial_dir


def _seat_trial(tmp_path: Path) -> tuple[Path, Path]:
    return _write_trial(
        tmp_path,
        root=_exc("AgentTimeoutError"),
        seat_results=[
            SeatResult(
                seat="a", under_test=True, exception_info=_exc("AgentTimeoutError")
            ),
            SeatResult(
                seat="b", under_test=False, exception_info=_exc("ApiRateLimitError")
            ),
        ],
    )


def _patch_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    job_instance = MagicMock()
    job_instance.run = AsyncMock(return_value=MagicMock(stats=MagicMock(evals={})))
    monkeypatch.setattr("harbor.job.Job.create", AsyncMock(return_value=job_instance))
    monkeypatch.setattr(
        "harbor.environments.factory.EnvironmentFactory.run_preflight",
        lambda **_: None,
    )
    monkeypatch.setattr("harbor.cli.jobs.print_job_results_tables", lambda _: None)


def test_resume_filter_removes_trial_on_secondary_seat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harbor.cli.jobs import resume

    job_dir, trial_dir = _seat_trial(tmp_path)
    _patch_resume(monkeypatch)

    resume(job_path=job_dir, filter_error_types=["ApiRateLimitError"], upload=False)

    assert not trial_dir.exists()


def test_resume_filter_keeps_trial_when_no_seat_error_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harbor.cli.jobs import resume

    job_dir, trial_dir = _seat_trial(tmp_path)
    _patch_resume(monkeypatch)

    resume(job_path=job_dir, filter_error_types=["CancelledError"], upload=False)

    assert trial_dir.exists()
    assert (trial_dir / "result.json").exists()


def test_resume_filter_removes_trial_on_step_level_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harbor.cli.jobs import resume

    job_dir, trial_dir = _write_trial(
        tmp_path,
        root=None,
        step_results=[
            StepResult(step_name="step-0"),
            StepResult(step_name="step-1", exception_info=_exc("ApiRateLimitError")),
        ],
    )
    _patch_resume(monkeypatch)

    resume(job_path=job_dir, filter_error_types=["ApiRateLimitError"], upload=False)

    assert not trial_dir.exists()


def test_resume_filter_preserves_single_agent_root_error_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from harbor.cli.jobs import resume

    job_dir, trial_dir = _write_trial(tmp_path, root=_exc("CancelledError"))
    _patch_resume(monkeypatch)

    resume(job_path=job_dir, filter_error_types=["CancelledError"], upload=False)

    assert not trial_dir.exists()
