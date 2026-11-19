from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import (
    AgentInfo,
    ModelInfo,
    SeatResult,
    StepResult,
    TrialResult,
)
from harbor.upload.uploader import Uploader

SONNET = ModelInfo(name="claude-sonnet-5-5", provider="anthropic")
GPT = ModelInfo(name="gpt-6.1", provider="openai")


@pytest.fixture
def uploader() -> Iterator[Uploader]:
    with (
        patch("harbor.upload.uploader.UploadDB") as mock_db_cls,
        patch("harbor.upload.uploader.UploadStorage") as mock_storage_cls,
    ):
        db = AsyncMock()
        db.get_trial.return_value = None
        db.upsert_agent.side_effect = lambda name, version: f"agent-{name}-{version}"
        db.upsert_model.side_effect = lambda name, provider: f"model-{name}-{provider}"
        mock_db_cls.return_value = db
        mock_storage_cls.return_value = AsyncMock()
        yield Uploader()


def _context(
    n_input: int | None,
    n_cache: int | None,
    n_output: int | None,
    cost: float | None,
) -> AgentContext:
    return AgentContext(
        n_input_tokens=n_input,
        n_cache_tokens=n_cache,
        n_output_tokens=n_output,
        cost_usd=cost,
    )


def _seat(
    name: str,
    model: ModelInfo | None,
    context: AgentContext | None,
    *,
    under_test: bool = True,
) -> SeatResult:
    return SeatResult(
        seat=name,
        under_test=under_test,
        agent_info=AgentInfo(name="agent", version="1.0", model_info=model),
        agent_result=context,
    )


def _trial_result(
    *,
    seats: list[SeatResult] | None = None,
    steps: list[StepResult] | None = None,
    model: ModelInfo | None = None,
) -> TrialResult:
    task_config = TaskConfig(path=Path("/tmp/task"))
    return TrialResult(
        task_name="task-0",
        trial_name="trial-0",
        trial_uri="file:///trials/trial-0",
        task_id=task_config.get_task_id(),
        task_checksum="deadbeef",
        config=TrialConfig(task=task_config, trial_name="trial-0", job_id=uuid4()),
        agent_info=AgentInfo(name="agent", version="1.0", model_info=model),
        agent_result=None,
        started_at=datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 17, 10, 5, 0, tzinfo=timezone.utc),
        seat_results=seats,
        step_results=steps,
    )


async def _upload(
    uploader: Uploader, trial_result: TrialResult, tmp_path: Path
) -> None:
    trial_dir = tmp_path / trial_result.trial_name
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(trial_result.model_dump_json())
    trial_lock = TrialLock(
        task=TaskLock(name="task-0", type="local", digest=f"sha256:{'a' * 64}"),
        agent=AgentConfig(name="agent"),
        environment=EnvironmentConfig(),
        verifier=VerifierLock(),
    )
    (trial_dir / "lock.json").write_text(trial_lock.model_dump_json())
    result = await uploader._upload_single_trial(
        trial_result=trial_result,
        trial_dir=trial_dir,
        trial_lock=trial_lock,
        job_id=uuid4(),
        agent_cache={},
        model_cache={},
    )
    assert result.error is None


def _inserted_usage(uploader: Uploader) -> dict[str, tuple[Any, ...]]:
    return {
        call.kwargs["model_id"]: (
            call.kwargs["n_input_tokens"],
            call.kwargs["n_cache_tokens"],
            call.kwargs["n_output_tokens"],
            call.kwargs["cost_usd"],
        )
        for call in uploader.db.insert_trial_model.await_args_list  # type: ignore[attr-defined]
    }


@pytest.mark.asyncio
async def test_seat_trial_same_model_aggregates_token_and_cost_usage(
    tmp_path: Path, uploader: Uploader
) -> None:
    trial_result = _trial_result(
        seats=[
            _seat("chef0", SONNET, _context(100, 10, 50, 0.01)),
            _seat("chef1", SONNET, _context(200, None, 75, 0.025)),
        ]
    )

    await _upload(uploader, trial_result, tmp_path)

    uploader.db.insert_trial_model.assert_awaited_once()  # type: ignore[attr-defined]
    usage = _inserted_usage(uploader)
    assert list(usage) == ["model-claude-sonnet-5-5-anthropic"]
    assert usage["model-claude-sonnet-5-5-anthropic"] == (
        300,
        10,
        125,
        pytest.approx(0.035),
    )


@pytest.mark.asyncio
async def test_seat_trial_mixed_models_upserts_and_inserts_each_model(
    tmp_path: Path, uploader: Uploader
) -> None:
    trial_result = _trial_result(
        seats=[
            _seat("chef0", SONNET, _context(100, 10, 50, 0.01)),
            _seat("chef1", GPT, _context(400, 0, 80, 0.04)),
            _seat("oracle", None, _context(999, 99, 999, 9.99), under_test=False),
        ]
    )

    await uploader._upsert_dimensions([trial_result])
    upserted = {
        (call.args[0], call.args[1])
        for call in uploader.db.upsert_model.await_args_list  # type: ignore[attr-defined]
    }
    assert upserted == {
        ("claude-sonnet-5-5", "anthropic"),
        ("gpt-6.1", "openai"),
    }

    uploader.db.insert_trial_model.reset_mock()  # type: ignore[attr-defined]
    await _upload(uploader, trial_result, tmp_path)

    assert _inserted_usage(uploader) == {
        "model-claude-sonnet-5-5-anthropic": (100, 10, 50, 0.01),
        "model-gpt-6.1-openai": (400, 0, 80, 0.04),
    }


@pytest.mark.asyncio
async def test_seat_trial_preserves_none_when_no_usage_reported(
    tmp_path: Path, uploader: Uploader
) -> None:
    trial_result = _trial_result(
        seats=[
            _seat("chef0", SONNET, None),
            _seat("chef1", SONNET, None),
        ]
    )

    await _upload(uploader, trial_result, tmp_path)

    assert _inserted_usage(uploader) == {
        "model-claude-sonnet-5-5-anthropic": (None, None, None, None)
    }


@pytest.mark.asyncio
async def test_multi_step_trial_persists_aggregated_step_usage(
    tmp_path: Path, uploader: Uploader
) -> None:
    trial_result = _trial_result(
        model=SONNET,
        steps=[
            StepResult(step_name="scaffold", agent_result=_context(100, 10, 50, 0.01)),
            StepResult(step_name="implement", agent_result=_context(40, 5, 20, 0.02)),
        ],
    )

    await _upload(uploader, trial_result, tmp_path)

    usage = _inserted_usage(uploader)
    assert usage == {
        "model-claude-sonnet-5-5-anthropic": (140, 15, 70, pytest.approx(0.03))
    }
