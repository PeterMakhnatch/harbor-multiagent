import errno
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from harbor.models.job.config import RetryConfig
from harbor.models.trial.config import Participant, TaskConfig, TrialConfig
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import AgentInfo, ExceptionInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.trial.queue import TrialQueue
from harbor.trial.trial import Trial


@pytest.fixture
def trial_config(tmp_path: Path) -> TrialConfig:
    return TrialConfig(
        task=TaskConfig(path=tmp_path / "task"),
        trial_name="seat-trial",
        trials_dir=tmp_path,
        job_id=uuid4(),
        participants=[
            Participant(seat="primary", name="nop"),
            Participant(seat="partner", name="nop"),
        ],
    )


def _result(config: TrialConfig, *, failed: bool = False) -> TrialResult:
    finished_at = datetime(2025, 1, 1, 12 if failed else 13, tzinfo=UTC)
    error = (
        ExceptionInfo(
            exception_type="AgentSetupTimeoutError",
            exception_message="private exception message",
            exception_traceback="private traceback text",
            occurred_at=finished_at,
        )
        if failed
        else None
    )
    return TrialResult(
        task_name="task",
        trial_name=config.trial_name,
        trial_uri=(config.trials_dir / config.trial_name).as_uri(),
        task_id=config.task.get_task_id(),
        task_checksum="test-checksum",
        config=config,
        agent_info=AgentInfo(name="nop", version="1.0"),
        finished_at=finished_at,
        exception_info=None if config.participants else error,
        seat_results=[
            SeatResult(seat="primary", under_test=True),
            SeatResult(seat="partner", under_test=False, exception_info=error),
        ]
        if config.participants
        else None,
        verifier_result=None if failed else VerifierResult(rewards={"reward": 1.0}),
    )


@pytest.fixture
def stub_attempts(monkeypatch: pytest.MonkeyPatch):
    def install(*results: TrialResult) -> None:
        pending = iter(results)

        async def create(config: TrialConfig) -> SimpleNamespace:
            result = next(pending)
            paths = TrialPaths(trial_dir=config.trials_dir / config.trial_name)
            # The retry must remove the old directory before creating this one.
            paths.trial_dir.mkdir()

            async def run() -> TrialResult:
                paths.result_path.write_text(result.model_dump_json(), encoding="utf-8")
                return result

            return SimpleNamespace(config=config, paths=paths, run=run)

        monkeypatch.setattr(Trial, "create", create)

    return install


async def test_seat_retry_records_only_the_replaced_attempt(
    trial_config, stub_attempts
):
    failed = _result(trial_config, failed=True)
    succeeded = _result(trial_config)
    stub_attempts(failed, succeeded)
    queue = TrialQueue(1, RetryConfig(max_retries=1, min_wait_sec=0))

    result = await queue.submit(trial_config)

    assert result is succeeded
    assert result.reward_valid
    assert result.id != failed.id
    paths = TrialPaths(trial_dir=trial_config.trials_dir / trial_config.trial_name)
    saved_result = json.loads(paths.result_path.read_text(encoding="utf-8"))
    assert saved_result["id"] == str(result.id)
    sidecar = trial_config.trials_dir / "retry-attempts.jsonl"
    rows = sidecar.read_text(encoding="utf-8").splitlines()
    assert [json.loads(row) for row in rows] == [
        {
            "schema": "harbor-seat-retry-attempt/v1",
            "job_id": str(failed.config.job_id),
            "trial_id": str(failed.id),
            "trial_name": failed.trial_name,
            "attempt": 1,
            "finished_at": "2025-01-01T12:00:00+00:00",
            "trial_exception_type": None,
            "seat_exceptions": [
                {"seat": "partner", "exception_type": "AgentSetupTimeoutError"},
            ],
        }
    ]


@pytest.mark.parametrize(
    "retry_config",
    [
        pytest.param(RetryConfig(max_retries=0), id="exhausted"),
        pytest.param(
            RetryConfig(max_retries=1, exclude_exceptions={"AgentSetupTimeoutError"}),
            id="excluded",
        ),
    ],
)
async def test_terminal_seat_failure_has_no_retry_record(
    trial_config, stub_attempts, retry_config
):
    failed = _result(trial_config, failed=True)
    stub_attempts(failed)

    result = await TrialQueue(1, retry_config).submit(trial_config)

    assert result is failed
    assert not (trial_config.trials_dir / "retry-attempts.jsonl").exists()
    assert (trial_config.trials_dir / trial_config.trial_name).is_dir()


async def test_seat_retry_continues_when_sidecar_write_fails(
    trial_config, stub_attempts, caplog: pytest.LogCaptureFixture
):
    sidecar = trial_config.trials_dir / "retry-attempts.jsonl"
    sidecar.mkdir()
    succeeded = _result(trial_config)
    stub_attempts(_result(trial_config, failed=True), succeeded)
    queue = TrialQueue(1, RetryConfig(max_retries=1, min_wait_sec=0))

    result = await queue.submit(trial_config)

    assert result is succeeded
    assert sidecar.is_dir()
    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    (warning,) = warnings
    message = warning.getMessage()
    assert str(trial_config.trials_dir) not in message
    assert os.strerror(errno.EISDIR) not in message
    assert warning.exc_info is None


async def test_classic_retry_has_no_seat_retry_record(trial_config, stub_attempts):
    classic_config = trial_config.model_copy(update={"participants": []})
    succeeded = _result(classic_config)
    stub_attempts(_result(classic_config, failed=True), succeeded)
    queue = TrialQueue(1, RetryConfig(max_retries=1, min_wait_sec=0))

    result = await queue.submit(classic_config)

    assert result is succeeded
    assert not (classic_config.trials_dir / "retry-attempts.jsonl").exists()
