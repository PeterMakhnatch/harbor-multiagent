"""Regrade a recorded multi-seat trial without rerunning its agents."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from harbor.cli.main import app
from harbor.environments.base import ExecResult
from harbor.job import Job
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.job.lock import TrialLock
from harbor.models.task.config import MCPServerConfig, VerifierEnvironmentMode
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    AgentConfig,
    Participant,
    SourceTrialConfig,
    TrialConfig,
)
from harbor.models.trial.config import (
    TaskConfig as TrialTaskConfig,
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
from harbor.trial.regrade import RegradeError, SeatsRegradeTrial
from harbor.trial.trial import Trial
from tests.unit.test_regrade import (
    CONVENTION_MANIFEST_ENTRY,
    _make_factory_recorder,
    _separate_verifier_task,
    _shared_verifier_task,
    _stock_mock_env,
)
from tests.unit.trial.test_seats import _config, _participant, _seats_trial, _task_dir

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
    if separate:
        _configure_seat_regrade(task_dir)
    return task_dir


def _configure_seat_regrade(task_dir: Path, *, acknowledge: bool = True) -> None:
    task_toml = task_dir / "task.toml"
    text = task_toml.read_text()
    verifier_options = 'environment_mode = "separate"\n'
    if acknowledge:
        verifier_options += "regrade_from_seat_record = true\n"
    if "[verifier]\n" in text:
        text = text.replace("[verifier]\n", f"[verifier]\n{verifier_options}", 1)
    else:
        text += f"\n[verifier]\n{verifier_options}"
    task_toml.write_text(text)


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
    manifest_entries: list[dict[str, Any]] | None = None,
    include_seats: bool = True,
    include_seat_results: bool = True,
    participants: list[Participant] | None = None,
    include_trial_exception: bool = False,
    seat_exception_type: str = "AgentTimeoutError",
    seat_policy_override: ErrorPolicyOverride | None = None,
    override_error_policy: dict[str, ErrorPolicyOverride] | None = None,
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
        json.dumps(
            [CONVENTION_MANIFEST_ENTRY]
            if manifest_entries is None
            else manifest_entries,
            indent=2,
        )
    )

    config = TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trial_name=trial_name,
        trials_dir=parent,
        participants=participants if participants is not None else _participants(),
        override_error_policy=override_error_policy or {},
    )
    paths.config_path.write_text(config.model_dump_json(indent=2, exclude={"agent"}))
    seat_exception = ExceptionInfo(
        exception_type=seat_exception_type,
        exception_message="maya failed",
        exception_traceback="source traceback",
        occurred_at=datetime.now(UTC),
        policy_override=seat_policy_override,
    )
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
        exception_info=(
            seat_exception.model_copy(
                update={"exception_message": "Seat 'maya': maya failed"}
            )
            if include_trial_exception
            else None
        ),
        seat_results=(
            [
                SeatResult(
                    seat="maya",
                    under_test=True,
                    agent_info=AgentInfo(name="oracle", version="1.0"),
                    agent_result=AgentContext(n_input_tokens=2, cost_usd=0.2),
                    exception_info=seat_exception,
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


def _regrade_config(task_dir: Path, source_dir: Path) -> TrialConfig:
    return TrialConfig(
        task=TrialTaskConfig(path=task_dir),
        trials_dir=source_dir.parent / "regrades",
        participants=_participants(),
        source_trial=SourceTrialConfig(action="regrade", type="local", path=source_dir),
    )


async def _regrade_with_stub_verifier(
    config: TrialConfig,
) -> tuple[Trial, TrialResult, AsyncMock]:
    mock_verifier = AsyncMock(return_value=VerifierResult(rewards={"reward": 1.0}))
    with patch(
        "harbor.trial.trial.EnvironmentFactory.create_environment_from_config",
        return_value=_stock_mock_env(),
    ):
        trial = await Trial.create(config)
    with patch.object(trial, "_run_separate_verifier", mock_verifier):
        result = await trial.run()
    return trial, result, mock_verifier


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
    source_dir = _write_seat_source(tmp_path, task_dir, include_trial_exception=True)
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

    verifier = AsyncMock(side_effect=verify)
    with patch.object(trial, "_run_separate_verifier", verifier):
        result = await trial.run()

    agent_env.start.assert_not_awaited()
    verifier.assert_awaited_once_with(
        key="trial",
        timeout_sec=trial._verifier_timeout_sec,
        artifacts_dir=trial.paths.artifacts_dir,
        seats_dir=trial.paths.seats_dir,
        user=trial.task.config.verifier.user,
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
    assert result.exception_info is not None
    assert result.exception_info.exception_type == "AgentTimeoutError"
    assert (
        result.exception_info.policy_override
        == config.override_error_policy["AgentTimeoutError"]
    )
    assert not result.reward_valid
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


@pytest.mark.parametrize("config_exists", [True, False])
async def test_job_regrade_carries_source_participants_and_policy(
    tmp_path: Path, config_exists: bool
):
    task_dir = _seat_task(tmp_path)
    source_job = tmp_path / "source-job"
    source_job.mkdir()
    source_policy = {"AgentTimeoutError": ErrorPolicyOverride(has_valid_reward=False)}
    source_dir = _write_seat_source(
        source_job, task_dir, override_error_policy=source_policy
    )
    if not config_exists:
        TrialPaths(source_dir).config_path.unlink()

    job = await Job.create(
        JobConfig(
            source_jobs=[
                SourceJobConfig(action="regrade", type="local", path=source_job)
            ],
            tasks=[TrialTaskConfig(path=task_dir)],
            jobs_dir=tmp_path / "jobs",
            override_error_policy={
                "VerifierTimeoutError": ErrorPolicyOverride(should_retry=True)
            },
        )
    )

    assert len(job) == 1
    assert job._trial_configs[0].participants == _participants()
    assert job._trial_configs[0].agent == AgentConfig()
    assert job._trial_configs[0].override_error_policy == (
        source_policy | job.config.override_error_policy
    )


@pytest.mark.parametrize("config_exists", [True, False])
def test_cli_regrade_carries_source_participants_and_policy(
    tmp_path: Path, config_exists: bool
):
    task_dir = _seat_task(tmp_path)
    source_policy = {"AgentTimeoutError": ErrorPolicyOverride(has_valid_reward=False)}
    source_dir = _write_seat_source(
        tmp_path, task_dir, override_error_policy=source_policy
    )
    source_paths = TrialPaths(source_dir)
    if not config_exists:
        source_paths.config_path.unlink()
    fake_trial = AsyncMock()
    fake_trial.run.return_value = TrialResult.model_validate_json(
        source_paths.result_path.read_text()
    )
    with patch.object(Trial, "create", return_value=fake_trial) as create:
        result = CliRunner().invoke(
            app, ["trial", "regrade", str(source_dir), "-p", str(task_dir)]
        )

    assert result.exit_code == 0, result.exception
    assert create.await_args is not None
    config = create.await_args.args[0]
    assert config.participants == _participants()
    assert config.override_error_policy == source_policy


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("unknown participant", "regrade participants"),
        ("no participants", "regrade participants"),
        ("different identity", "does not match the recorded participant"),
        ("source participants", "source participants"),
        ("missing results", "no seat_results"),
        ("duplicate results", "source seat_results"),
        ("missing seats", "needs a recorded seats/ directory"),
        ("empty seats", "source seats/ entries"),
        ("extra seat dir", "source seats/ entries"),
        ("missing agent dir", "seats/raj/agent/"),
        ("changed under_test", "changed its under_test setting"),
        ("all seats removed", "requires a task with the same seats"),
    ],
)
async def test_seat_regrade_refuses_inconsistent_roster_or_record(
    tmp_path: Path, failure: str, message: str
):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(
        tmp_path,
        task_dir,
        include_seats=failure != "missing seats",
        include_seat_results=failure != "missing results",
    )
    config = _regrade_config(task_dir, source_dir)
    paths = TrialPaths(source_dir)
    if failure == "unknown participant":
        config.participants = [Participant(seat="eve", name="oracle")]
    elif failure == "no participants":
        config.participants = []
    elif failure == "different identity":
        config.participants[0].model_name = "anthropic/other"
    elif failure in ("source participants", "duplicate results"):
        data = json.loads(paths.result_path.read_text())
        if failure == "source participants":
            data["config"]["participants"].pop()
        else:
            data["seat_results"][1]["seat"] = "maya"
        paths.result_path.write_text(json.dumps(data))
    elif failure == "empty seats":
        shutil.rmtree(paths.seats_dir / "maya")
        shutil.rmtree(paths.seats_dir / "raj")
    elif failure == "extra seat dir":
        (paths.seats_dir / "eve").mkdir()
    elif failure == "missing agent dir":
        shutil.rmtree(paths.seats_dir / "raj/agent")
    elif failure == "changed under_test":
        task_toml = task_dir / "task.toml"
        task_toml.write_text(
            task_toml.read_text().replace("under_test = false", "under_test = true")
        )
    elif failure == "all seats removed":
        task_toml = task_dir / "task.toml"
        task_toml.write_text(task_toml.read_text().split("[agents.maya]")[0])
        config.participants = []

    _, result, verifier = await _regrade_with_stub_verifier(config)

    assert result.exception_info is not None
    assert result.exception_info.exception_type == "RegradeError"
    assert message in result.exception_info.exception_message
    assert result.verifier_result is None
    assert not result.reward_valid
    verifier.assert_not_awaited()


@pytest.mark.parametrize("logs_downloaded", [False, True, None])
async def test_seat_regrade_checks_raw_log_download_status(
    tmp_path: Path, logs_downloaded: bool | None
):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(tmp_path, task_dir, include_trial_exception=True)
    result_path = TrialPaths(source_dir).result_path
    data = json.loads(result_path.read_text())
    data["seat_results"][1]["logs_downloaded"] = logs_downloaded
    result_path.write_text(json.dumps(data))

    trial, result, verifier = await _regrade_with_stub_verifier(
        _regrade_config(task_dir, source_dir)
    )

    if logs_downloaded is False:
        assert result.exception_info is not None
        assert result.exception_info.exception_type == "RegradeError"
        assert (
            "recorded seat logs are known incomplete for 'raj'"
            in result.exception_info.exception_message
        )
        assert not trial.paths.seats_dir.exists()
        assert result.verifier_result is None
        verifier.assert_not_awaited()
    else:
        assert result.reward_valid
        verifier.assert_awaited_once()


async def test_seat_regrade_rebuilds_missing_exception_without_grading(tmp_path: Path):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(tmp_path, task_dir)
    config = _regrade_config(task_dir, source_dir)

    trial, result, verifier = await _regrade_with_stub_verifier(config)

    assert result.exception_info is not None
    assert result.exception_info.exception_type == "AgentTimeoutError"
    assert result.exception_info.exception_message == "Seat 'maya': maya failed"
    assert "Seat 'maya': maya failed" in trial.paths.exception_message_path.read_text()
    assert result.verifier_result is None
    assert not result.reward_valid
    verifier.assert_not_awaited()


@pytest.mark.parametrize(
    ("seat_type", "source_policy"),
    [
        ("AgentSetupTimeoutError", {}),
        (
            "AgentTimeoutError",
            {"AgentTimeoutError": ErrorPolicyOverride(should_grade=False)},
        ),
    ],
)
async def test_seat_regrade_honors_no_grade_policy(
    tmp_path: Path, seat_type: str, source_policy: dict[str, ErrorPolicyOverride]
):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(
        tmp_path,
        task_dir,
        include_trial_exception=True,
        seat_exception_type=seat_type,
        override_error_policy=source_policy,
    )
    _, result, verifier = await _regrade_with_stub_verifier(
        _regrade_config(task_dir, source_dir)
    )

    assert result.exception_info is not None
    assert result.exception_info.exception_type == seat_type
    assert not result.exception_info.policy.should_grade
    assert result.verifier_result is None
    assert not result.reward_valid
    verifier.assert_not_awaited()


async def test_seat_regrade_preserves_recorded_policy_override(tmp_path: Path):
    task_dir = _seat_task(tmp_path)
    policy = ErrorPolicyOverride(has_valid_reward=False)
    source_dir = _write_seat_source(
        tmp_path, task_dir, include_trial_exception=True, seat_policy_override=policy
    )
    _, result, verifier = await _regrade_with_stub_verifier(
        _regrade_config(task_dir, source_dir)
    )

    verifier.assert_awaited_once()
    assert result.verifier_result is not None
    assert result.exception_info is not None
    assert result.exception_info.policy_override == policy
    assert result.seat_results is not None
    seat_error = result.seat_results[0].exception_info
    assert seat_error is not None
    assert seat_error.policy_override == policy
    assert not result.reward_valid


@pytest.mark.parametrize("destination", ["direct", "alias", "nested"])
async def test_seat_regrade_refuses_to_write_into_source(
    tmp_path: Path, destination: str
):
    task_dir = _seat_task(tmp_path)
    source_dir = _write_seat_source(tmp_path, task_dir)
    config = _regrade_config(task_dir, source_dir)
    target_dir = source_dir
    if destination == "alias":
        target_dir = tmp_path / "alias"
        target_dir.symlink_to(source_dir, target_is_directory=True)
    elif destination == "nested":
        target_dir = source_dir / "regrade"
    config.trial_name = target_dir.name
    config.trials_dir = target_dir.parent
    before = {
        path: path.read_bytes() for path in source_dir.rglob("*") if path.is_file()
    }

    with pytest.raises(RegradeError, match="overlaps source trial directory"):
        await Trial.create(config)

    assert before == {
        path: path.read_bytes() for path in source_dir.rglob("*") if path.is_file()
    }
    assert not TrialPaths(source_dir).lock_path.exists()


async def test_seat_regrade_scrubs_every_participant_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task_dir = _seat_task(tmp_path)
    participants = _participants()
    maya, raj = participants
    maya.model_api_key_name = "CONNECTION"
    maya.env = {"CONNECTION": "${MAYA_KEY}"}
    maya.mcp_servers = [
        MCPServerConfig(
            name="test-mcp",
            transport="streamable-http",
            url="https://example.test/mcp",
            headers={"Authorization": "Bearer ${MAYA_HEADER}"},
        )
    ]
    raj.env = {"RAJ_TOKEN": "${RAJ_TOKEN}", "AUTH_FLAG": "1"}
    secrets = {
        "MAYA_KEY": "test-maya-credential",
        "MAYA_HEADER": "test-maya-header",
        "RAJ_TOKEN": "test-raj-credential",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    source_dir = _write_seat_source(
        tmp_path, task_dir, participants=participants, include_trial_exception=True
    )
    relative_log = "maya/agent/agent.log"
    text = "record 1\n" + "\n".join(secrets.values())
    source_log = TrialPaths(source_dir).seats_dir / relative_log
    source_log.write_text(text)
    config = _regrade_config(task_dir, source_dir)
    config.participants = participants

    trial, result, verifier = await _regrade_with_stub_verifier(config)

    verifier.assert_awaited_once()
    assert result.verifier_result is not None
    copied_log = (trial.paths.seats_dir / relative_log).read_text()
    assert copied_log.startswith("record 1\n")
    assert not any(value in copied_log for value in secrets.values())
    assert source_log.read_text() == text


async def test_record_shared_edit_task_and_regrade_in_fresh_environment(tmp_path: Path):
    task_dir = _task_dir(tmp_path)
    source = await _seats_trial(
        _config(
            task_dir,
            {**_participant("maya"), "env": {"RECORDED": "maya log"}},
            _participant("raj"),
            _participant("lee"),
        )
    )

    async def collect_artifact(*, source_dir, target_dir, service):
        assert source_dir == "/logs/artifacts"
        (target_dir / "snapshot.txt").write_text("recorded world state")

    with (
        patch.object(
            source.agent_environment, "service_is_dir", AsyncMock(return_value=True)
        ),
        patch.object(
            source.agent_environment,
            "service_download_dir",
            AsyncMock(side_effect=collect_artifact),
        ),
    ):
        recorded = await source.run()
    assert recorded.exception_info is None
    assert recorded.verifier_environment_mode == VerifierEnvironmentMode.SHARED
    source_lock = TrialLock.model_validate_json(source.paths.lock_path.read_text())

    config = _regrade_config(task_dir, source.paths.trial_dir)
    config.participants = recorded.config.participants
    _configure_seat_regrade(task_dir, acknowledge=False)

    refused, unacknowledged, verifier = await _regrade_with_stub_verifier(config)

    verifier.assert_not_awaited()
    assert unacknowledged.exception_info is not None
    assert unacknowledged.exception_info.exception_type == "RegradeError"
    assert (
        "regrade_from_seat_record = true"
        in unacknowledged.exception_info.exception_message
    )
    assert "task drift:" in refused.paths.log_path.read_text()

    task_toml = task_dir / "task.toml"
    task_toml.write_text(
        task_toml.read_text().replace(
            'environment_mode = "separate"\n',
            'environment_mode = "separate"\nregrade_from_seat_record = true\n',
        )
    )
    config.trial_name = "acknowledged"
    agent_env = _stock_mock_env()
    verifier_env = _stock_mock_env()
    fake_create, calls = _make_factory_recorder(agent_env, verifier_env)
    uploaded = {}

    async def upload(source_dir: Path, target_dir: str):
        if target_dir == "/logs/seats":
            uploaded[target_dir] = (source_dir / "maya/agent/log.txt").read_text()
        elif target_dir == "/logs/artifacts":
            uploaded[target_dir] = (source_dir / "snapshot.txt").read_text()

    async def exec_verifier(command: str, **_):
        if "/tests/test.sh" in command and not command.startswith("chmod "):
            assert uploaded == {
                "/logs/seats": "RECORDED=maya log\n",
                "/logs/artifacts": "recorded world state",
            }
            trial.paths.reward_text_path.write_text("0.75")
        return ExecResult(return_code=0)

    verifier_env.upload_dir.side_effect = upload
    verifier_env.exec.side_effect = exec_verifier
    with patch(
        "harbor.trial.trial.EnvironmentFactory.create_environment_from_config",
        side_effect=fake_create,
    ):
        trial = await Trial.create(config)
        result = await trial.run()

    assert result.exception_info is None
    assert result.verifier_result == VerifierResult(rewards={"reward": 0.75})
    assert result.reward_valid
    assert result.verifier_environment_mode == VerifierEnvironmentMode.SEPARATE
    agent_env.start.assert_not_awaited()
    verifier_env.start.assert_awaited_once()
    verifier_env.upload_dir.assert_any_await(trial.paths.seats_dir, "/logs/seats")
    assert calls[0]["session_id"] != calls[1]["session_id"]
    assert calls[1]["environment_dir"] == task_dir / "environment"

    assert result.source_task_checksum == recorded.task_checksum
    assert result.task_checksum != recorded.task_checksum
    lock = TrialLock.model_validate_json(trial.paths.lock_path.read_text())
    assert lock.source_trial is not None
    assert lock.source_trial.task == source_lock.task
    assert lock.task.digest != source_lock.task.digest
    log = trial.paths.log_path.read_text()
    assert source_lock.task.digest in log and lock.task.digest in log
    assert "starts fresh services" in log
