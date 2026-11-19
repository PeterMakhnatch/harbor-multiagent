"""Regrade a recorded multi-seat trial without rerunning its agents."""

import shutil
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from harbor.job import Job
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    AgentConfig,
    Participant,
    SourceTrialConfig,
    TaskConfig as TrialTaskConfig,
    TrialConfig,
)
from harbor.models.trial.error_policy import ErrorPolicyOverride
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import (
    AgentInfo,
    ExceptionInfo,
    ModelInfo,
    SeatResult,
    TrialResult,
)
from harbor.models.verifier.result import VerifierResult
from harbor.trial.regrade import SeatsRegradeTrial
from harbor.trial.trial import Trial
from tests.unit.test_regrade import (
    CONVENTION_MANIFEST_ENTRY,
    _separate_verifier_task,
    _shared_verifier_task,
    _stock_mock_env,
)


pytestmark = pytest.mark.unit


def _seat_task(tmp: Path, *, separate: bool = True) -> Path:
    task_dir = (
        _separate_verifier_task(tmp, "seat-task")
        if separate
        else _shared_verifier_task(tmp, "seat-task")
    )
    (task_dir / "instructions").mkdir()
    for seat in ("maya", "raj"):
        (task_dir / "instructions" / f"{seat}.md").write_text(f"You are {seat}.\n")
    (task_dir / "task.toml").write_text(
        (task_dir / "task.toml").read_text()
        + "[agents.maya]\n[agents.raj]\nunder_test = false\n"
    )
    return task_dir


def _participants() -> list[Participant]:
    return [
        Participant(seat="maya", name="oracle", model_name="anthropic/opus"),
        Participant(seat="raj", name="nop"),
    ]


def _write_seat_source(
    parent: Path,
    task_dir: Path,
    *,
    trial_name: str = "source-trial",
    manifest_entries: list[dict] | None = None,
    include_seats: bool = True,
    include_seat_results: bool = True,
) -> Path:
    trial_dir = parent / trial_name
    paths = TrialPaths(trial_dir=trial_dir)
    paths.mkdir()
    if include_seats:
        for seat, text in (("maya", "maya log\n"), ("raj", "raj log\n")):
            seat_dir = paths.seats_dir / seat / "agent"
            seat_dir.mkdir(parents=True)
            (seat_dir / "agent.log").write_text(text)
    convention_dir = paths.artifacts_dir / "logs" / "artifacts"
    convention_dir.mkdir(parents=True, exist_ok=True)
    (convention_dir / "output.txt").write_text("artifact content\n")
    paths.artifacts_manifest_path.write_text(
        json.dumps(manifest_entries or [CONVENTION_MANIFEST_ENTRY], indent=2)
    )

    config = TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trial_name=trial_name,
        trials_dir=parent,
        participants=_participants(),
    )
    paths.config_path.write_text(config.model_dump_json(indent=2, exclude={"agent"}))
    result = TrialResult(
        task_name=task_dir.name,
        trial_name=trial_name,
        trial_uri=trial_dir.resolve().as_uri(),
        task_id=LocalTaskId(path=task_dir),
        task_checksum="test-checksum",
        config=config,
        agent_info=AgentInfo(
            name="oracle",
            version="1.0",
            model_info=ModelInfo(name="opus", provider="anthropic"),
        ),
        agent_result=AgentContext(n_input_tokens=3, cost_usd=0.3),
        verifier_result=VerifierResult(rewards={"reward": 0.0}),
        seat_results=(
            [
                SeatResult(
                    seat="maya",
                    under_test=True,
                    agent_info=AgentInfo(name="oracle", version="1.0"),
                    agent_result=AgentContext(n_input_tokens=2, cost_usd=0.2),
                    exception_info=ExceptionInfo(
                        exception_type="AgentTimeoutError",
                        exception_message="maya timed out",
                        exception_traceback="source traceback",
                        occurred_at=datetime.now(timezone.utc),
                    ),
                ),
                SeatResult(
                    seat="raj",
                    under_test=False,
                    agent_info=AgentInfo(name="nop", version="1.0"),
                    agent_result=AgentContext(n_input_tokens=1, cost_usd=0.1),
                ),
            ]
            if include_seat_results
            else None
        ),
    )
    paths.result_path.write_text(result.model_dump_json(indent=2))
    return trial_dir


async def test_trial_create_dispatches_seat_regrade_and_forwards_id(tmp_path: Path):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(tmp_path, task_dir)
    trial_id = uuid4()
    config = TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trials_dir=tmp_path / "trials",
        participants=_participants(),
        source_trial=SourceTrialConfig(action="regrade", type="local", path=source_dir),
    )

    with patch(
        "harbor.trial.trial.EnvironmentFactory.create_environment_from_config",
        return_value=_stock_mock_env(),
    ):
        trial = await Trial.create(config, trial_id=trial_id)

    assert type(trial) is SeatsRegradeTrial
    assert trial.id == trial_id


async def test_seat_regrade_replays_records_and_leaves_source_unchanged(
    tmp_path: Path,
):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(tmp_path, task_dir)
    source_before = {
        path.relative_to(source_dir): path.read_bytes()
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    config = TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trial_name="regraded",
        trials_dir=tmp_path / "trials",
        participants=_participants(),
        override_error_policy={
            "AgentTimeoutError": ErrorPolicyOverride(has_valid_reward=False)
        },
        source_trial=SourceTrialConfig(action="regrade", type="local", path=source_dir),
    )
    agent_env = _stock_mock_env()
    verifier_env = _stock_mock_env()

    with patch(
        "harbor.trial.trial.EnvironmentFactory.create_environment_from_config",
        return_value=agent_env,
    ):
        trial = await Trial.create(config)

    async def verify(**kwargs):
        await verifier_env.upload_dir(kwargs["seats_dir"], "/logs/seats")
        return VerifierResult(rewards={"reward": 1.0})

    trial._run_separate_verifier = AsyncMock(side_effect=verify)
    result = await trial.run()

    agent_env.start.assert_not_awaited()
    trial._run_separate_verifier.assert_awaited_once()
    assert trial._run_separate_verifier.await_args.kwargs["seats_dir"] == (
        trial.paths.seats_dir
    )
    verifier_env.upload_dir.assert_awaited_once_with(
        trial.paths.seats_dir, "/logs/seats"
    )
    assert (trial.paths.seats_dir / "maya/agent/agent.log").read_text() == "maya log\n"
    assert (trial.paths.artifacts_dir / "logs/artifacts/output.txt").read_text() == (
        "artifact content\n"
    )
    assert "agent" not in json.loads(trial.paths.config_path.read_text())
    assert result.agent_info.name == "oracle"
    assert result.agent_result is not None
    assert result.agent_result.cost_usd == 0.3
    assert result.verifier_result == VerifierResult(rewards={"reward": 1.0})
    assert result.seat_results is not None
    assert [seat.seat for seat in result.seat_results] == ["maya", "raj"]
    assert result.seat_results[0].agent_result is not None
    assert result.seat_results[0].agent_result.n_input_tokens == 2
    assert result.seat_results[0].exception_info is not None
    assert result.seat_results[0].exception_info.policy_override == (
        ErrorPolicyOverride(has_valid_reward=False)
    )
    assert result.seat_results[1].agent_result is not None
    assert result.seat_results[1].agent_result.cost_usd == 0.1
    assert source_before == {
        path.relative_to(source_dir): path.read_bytes()
        for path in source_dir.rglob("*")
        if path.is_file()
    }


async def test_seat_regrade_rejects_symlink_escape(tmp_path: Path):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(tmp_path, task_dir)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n")
    (TrialPaths(source_dir).seats_dir / "maya/agent/escape").symlink_to(secret)
    config = TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trials_dir=tmp_path / "trials",
        participants=_participants(),
        source_trial=SourceTrialConfig(action="regrade", type="local", path=source_dir),
    )

    with patch(
        "harbor.trial.trial.EnvironmentFactory.create_environment_from_config",
        return_value=_stock_mock_env(),
    ):
        trial = await Trial.create(config)

    result = await trial.run()

    assert result.exception_info is not None
    assert result.exception_info.exception_type == "RegradeError"
    assert "Recorded output links" in result.exception_info.exception_message


@pytest.mark.parametrize("failure", ["coverage", "shared"])
async def test_seat_regrade_rejects_incomplete_or_shared_task(
    tmp_path: Path, failure: str
):
    task_dir = _seat_task(tmp_path, separate=failure != "shared")
    source_dir = _write_seat_source(
        tmp_path,
        task_dir,
        manifest_entries=[] if failure == "coverage" else None,
    )
    if failure == "coverage":
        shutil.rmtree(TrialPaths(source_dir).artifacts_dir)
    config = TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trials_dir=tmp_path / "trials",
        participants=_participants(),
        source_trial=SourceTrialConfig(action="regrade", type="local", path=source_dir),
    )

    with patch(
        "harbor.trial.trial.EnvironmentFactory.create_environment_from_config",
        return_value=_stock_mock_env(),
    ):
        trial = await Trial.create(config)

    result = await trial.run()

    assert result.exception_info is not None
    assert result.exception_info.exception_type == "RegradeError"
    expected = "shared-mode" if failure == "shared" else "artifacts/manifest.json"
    assert expected in result.exception_info.exception_message


async def test_job_regrade_carries_source_participants(tmp_path: Path):
    task_dir = _seat_task(tmp_path)
    source_job = tmp_path / "source-job"
    source_job.mkdir()
    _write_seat_source(source_job, task_dir)

    job = await Job.create(
        JobConfig(
            source_jobs=[
                SourceJobConfig(action="regrade", type="local", path=source_job)
            ],
            tasks=[TrialTaskConfig(path=task_dir)],
            jobs_dir=tmp_path / "jobs",
        )
    )

    assert len(job) == 1
    assert job._trial_configs[0].participants == _participants()
    assert job._trial_configs[0].agent == AgentConfig()
