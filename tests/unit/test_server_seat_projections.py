from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.trial.config import AgentConfig, Participant, TaskConfig, TrialConfig
from harbor.models.trial.result import (
    AgentInfo,
    ModelInfo,
    SeatResult,
    TimingInfo,
    TrialResult,
)
from harbor.server.database import RolloutRecord
from harbor.server.projections import (
    current_phase,
    input_projection,
    result_projection,
)
from harbor.server.service import Service


def _seat_config(tmp_path: Path) -> TrialConfig:
    return TrialConfig(
        task=TaskConfig(path=tmp_path / "task"),
        participants=[
            Participant(
                seat="helper",
                name="claude-code",
                model_name="anthropic/claude-x",
                kwargs={"reasoning_effort": "low"},
            ),
            Participant(
                seat="solver",
                name="codex",
                model_name="openai/gpt-5",
                kwargs={"reasoning_effort": "high"},
            ),
        ],
    )


def _trial_lock(config: TrialConfig) -> TrialLock:
    return TrialLock(
        task=TaskLock(
            name="seat-task",
            type="local",
            digest="sha256:" + "a" * 64,
            source="examples",
        ),
        agent=config.agent,
        participants=config.participants,
        environment=config.environment,
        verifier=VerifierLock(),
    )


def _seat_result(config: TrialConfig, **kwargs) -> TrialResult:
    return TrialResult(
        task_name="seat-task",
        trial_name=config.trial_name,
        trial_uri="file:///rollout",
        task_id=config.task.get_task_id(),
        task_checksum="a" * 64,
        config=config,
        agent_info=AgentInfo(name="oracle", version=""),
        **kwargs,
    )


def test_input_projection_reports_first_participant(tmp_path: Path) -> None:
    config = _seat_config(tmp_path)
    projected = input_projection(config, _trial_lock(config))

    assert projected["agent_name"] == "claude-code"
    assert projected["model_name"] == "anthropic/claude-x"
    assert projected["reasoning_effort"] == "low"
    assert projected["agent_name"] != "oracle"


def test_result_projection_uses_first_under_test_seat_and_sums_totals(
    tmp_path: Path,
) -> None:
    config = _seat_config(tmp_path)
    seats = [
        SeatResult(
            seat="helper",
            under_test=False,
            agent_info=AgentInfo(
                name="claude-code",
                version="1.0",
                model_info=ModelInfo(name="claude-x", provider="anthropic"),
            ),
            agent_result=AgentContext(
                n_input_tokens=10,
                n_cache_tokens=2,
                n_output_tokens=5,
                cost_usd=0.5,
            ),
        ),
        SeatResult(
            seat="solver",
            under_test=True,
            agent_info=AgentInfo(
                name="codex",
                version="2.0",
                model_info=ModelInfo(name="gpt-5", provider="openai"),
            ),
            agent_result=AgentContext(
                n_input_tokens=20,
                n_cache_tokens=3,
                n_output_tokens=7,
                cost_usd=1.0,
            ),
        ),
    ]
    projected = result_projection(_seat_result(config, seat_results=seats))

    assert projected["agent_name"] == "codex"
    assert projected["agent_version"] == "2.0"
    assert projected["model_name"] == "openai/gpt-5"
    assert projected["model_provider"] == "openai"
    assert projected["n_input_tokens"] == 30
    assert projected["n_cached_input_tokens"] == 5
    assert projected["n_output_tokens"] == 12
    assert projected["cost_usd"] == 1.5

    no_under_test = [seat.model_copy(update={"under_test": False}) for seat in seats]
    fallback = result_projection(_seat_result(config, seat_results=no_under_test))
    assert fallback["agent_name"] == "claude-code"
    assert fallback["model_name"] == "anthropic/claude-x"


def test_current_phase_sees_running_seat_timing(tmp_path: Path) -> None:
    config = _seat_config(tmp_path)
    now = datetime.now(UTC)
    running_exec = _seat_result(
        config,
        seat_results=[
            SeatResult(
                seat="solver",
                under_test=True,
                agent_setup=TimingInfo(started_at=now, finished_at=now),
                agent_execution=TimingInfo(started_at=now),
            )
        ],
    )
    assert current_phase(running_exec) == "agent_execution"

    running_setup = _seat_result(
        config,
        seat_results=[
            SeatResult(
                seat="solver",
                under_test=True,
                agent_setup=TimingInfo(started_at=now),
            )
        ],
    )
    assert current_phase(running_setup) == "agent_setup"


def test_non_seat_rollout_projection_unchanged(tmp_path: Path) -> None:
    config = TrialConfig(
        task=TaskConfig(path=tmp_path / "task"),
        agent=AgentConfig(
            name="nop",
            model_name="openai/gpt-5",
            kwargs={"reasoning_effort": "high"},
        ),
    )
    inp = input_projection(config, _trial_lock(config))
    assert inp["agent_name"] == "nop"
    assert inp["model_name"] == "openai/gpt-5"
    assert inp["reasoning_effort"] == "high"

    result = TrialResult(
        task_name="seat-task",
        trial_name=config.trial_name,
        trial_uri="file:///rollout",
        task_id=config.task.get_task_id(),
        task_checksum="a" * 64,
        config=config,
        agent_info=AgentInfo(
            name="nop",
            version="1.2.3",
            model_info=ModelInfo(name="gpt-5", provider="openai"),
        ),
    )
    out = result_projection(result)
    assert out["agent_name"] == "nop"
    assert out["agent_version"] == "1.2.3"
    assert out["model_name"] == "openai/gpt-5"
    assert out["model_provider"] == "openai"


def test_pre_trial_failure_reports_participant(tmp_path: Path) -> None:
    config = _seat_config(tmp_path)
    now = datetime.now(UTC)
    rollout = RolloutRecord(
        id=uuid4(),
        job_id=uuid4(),
        rollout_request_id=uuid4(),
        name=config.trial_name,
        config=config,
        lock=_trial_lock(config),
        uri="file:///rollout",
        created_at=now,
        updated_at=now,
        started_at=now,
    )
    failed = Service._failure(
        SimpleNamespace(work_dir=tmp_path),  # ty: ignore[invalid-argument-type]
        rollout,
        RuntimeError("boom"),
    )

    assert failed.agent_info.name == "claude-code"
    assert failed.agent_info.model_info is not None
    assert failed.agent_info.model_info.name == "anthropic/claude-x"
    assert failed.exception_info is not None
    assert failed.exception_info.exception_type == "RuntimeError"
