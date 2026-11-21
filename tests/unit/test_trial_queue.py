import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from harbor.models.job.config import RetryConfig
from harbor.models.job.lock import VerifierLock, TaskLock, TrialLock
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import AgentInfo, ExceptionInfo, StepResult, TrialResult
from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.queue import TrialQueue, _archive_retry_attempt


def _make_trial_result(config: TrialConfig, *, task_name: str = "task") -> TrialResult:
    return TrialResult(
        task_name=task_name,
        trial_name=config.trial_name,
        trial_uri=f"file:///test/{config.trial_name}",
        task_id=config.task.get_task_id(),
        task_checksum="abc123",
        config=config,
        agent_info=AgentInfo(name="test_agent", version="1.0"),
    )


def _mock_trial_with_result_file(
    result: TrialResult, *, result_bytes: bytes | None = None
) -> MagicMock:
    paths = TrialPaths(result.config.trials_dir / result.trial_name)

    async def run() -> TrialResult:
        paths.trial_dir.mkdir()
        data = (
            result.model_dump_json().encode() if result_bytes is None else result_bytes
        )
        paths.result_path.write_bytes(data)
        return result

    return MagicMock(
        config=result.config, id=result.id, paths=paths, run=AsyncMock(side_effect=run)
    )


def _make_trial_lock(task_name: str = "task") -> TrialLock:
    return TrialLock(
        task=TaskLock(name=task_name, type="local", digest=f"sha256:{'a' * 64}"),
        agent=AgentConfig(name="claude-code"),
        environment=EnvironmentConfig(),
        verifier=VerifierLock(),
    )


@pytest.fixture
def trial_config(tmp_path):
    """Create a basic trial config for testing."""
    return TrialConfig(
        task=TaskConfig(path=Path("/test/task")),
        trial_name="test_trial",
        job_id=uuid4(),
        trials_dir=tmp_path,
    )


@pytest.fixture
def trial_result(trial_config):
    """Create a basic trial result for testing."""
    return _make_trial_result(trial_config, task_name="test_task")


@pytest.fixture
def retryable_trial_result(trial_result):
    trial_result.exception_info = ExceptionInfo.from_exception(RuntimeError("failure"))
    return trial_result


@pytest.fixture
def retrying_queue():
    return TrialQueue(
        n_concurrent=1, retry_config=RetryConfig(max_retries=1, min_wait_sec=0)
    )


@pytest.fixture
def hooks():
    """Create empty hooks dict."""
    return {event: [] for event in TrialEvent}


@pytest.fixture
def queue(hooks):
    """Create a TrialQueue instance."""
    return TrialQueue(
        n_concurrent=2,
        retry_config=RetryConfig(),
        hooks=hooks,
    )


@pytest.mark.parametrize(
    ("errors", "max_retries", "retried"),
    [
        (["ApiUsageLimitError"], 1, True),
        (["ApiRateLimitError"], 0, False),
        (["AgentTimeoutError"], 1, False),
        (["NonZeroAgentExitCodeError"], 1, False),
        (["ApiRateLimitError", "AgentTimeoutError"], 1, False),
    ],
)
async def test_queue_retries_consider_all_steps(
    trial_config, trial_result, errors, max_retries, retried, tmp_path
):
    failed = trial_result.model_copy(deep=True, update={"id": uuid4()})
    failed.step_results = [
        StepResult(
            step_name=str(index),
            exception_info=ExceptionInfo.from_exception(
                RuntimeError("failure")
            ).model_copy(update={"exception_type": error}),
        )
        for index, error in enumerate(errors)
    ]
    failed_trial = _mock_trial_with_result_file(failed)
    replacement = _mock_trial_with_result_file(trial_result)
    create = AsyncMock(side_effect=[failed_trial, replacement])
    queue = TrialQueue(
        n_concurrent=1,
        retry_config=RetryConfig(max_retries=max_retries, min_wait_sec=0),
    )
    with patch("harbor.trial.trial.Trial.create", create):
        result = await queue.submit(trial_config)

    assert create.await_count == 1 + retried
    failed_trial.run.assert_awaited_once_with()
    assert replacement.run.await_count == retried
    assert result is (trial_result if retried else failed)
    if not retried:
        assert not list(tmp_path.glob("retry-attempt-*.json"))
        assert failed_trial.paths.result_path.read_bytes() == (
            failed.model_dump_json().encode()
        )


async def test_queue_archives_native_bytes_by_attempt_id(
    trial_config, retryable_trial_result, tmp_path
):
    first = retryable_trial_result
    second = first.model_copy(update={"id": uuid4()})
    retained = first.model_copy(update={"id": uuid4()})
    first_bytes = (
        first.model_dump_json().encode().removesuffix(b"}")
        + b',\r\n  "extra": {"unknown": null, "cost": 0.000}\r\n}\r\n'
    )
    second_bytes = second.model_dump_json(indent=4).encode() + b"\n"
    first_trial = _mock_trial_with_result_file(first, result_bytes=first_bytes)
    second_trial = _mock_trial_with_result_file(second, result_bytes=second_bytes)
    retained_trial = _mock_trial_with_result_file(retained)
    create = AsyncMock(side_effect=[first_trial, second_trial, retained_trial])
    queue = TrialQueue(
        n_concurrent=1, retry_config=RetryConfig(max_retries=2, min_wait_sec=0)
    )
    with patch("harbor.trial.trial.Trial.create", create):
        result = await queue.submit(trial_config)

    assert result is retained
    assert create.await_count == 3
    assert first_trial.paths == second_trial.paths == retained_trial.paths
    assert retained_trial.paths.result_path.read_bytes() == (
        retained.model_dump_json().encode()
    )
    assert {
        path.name: path.read_bytes() for path in tmp_path.glob("retry-attempt-*.json")
    } == {
        f"retry-attempt-{first.id}.json": first_bytes,
        f"retry-attempt-{second.id}.json": second_bytes,
    }


def test_retry_archive_becomes_visible_only_after_copy(tmp_path):
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir()
    result_path = trial_dir / "result.json"
    result_bytes = b'{\r\n  "unknown": null\r\n}\r\n'
    result_path.write_bytes(result_bytes)
    trial_id = uuid4()
    archive_path = tmp_path / f"retry-attempt-{trial_id}.json"
    copying = Event()
    finish_copy = Event()

    def pause_copy(source, destination):
        assert destination.write(source.read(5)) == 5
        destination.flush()
        copying.set()
        assert finish_copy.wait(timeout=5)
        destination.write(source.read())

    with (
        patch("harbor.trial.queue.shutil.copyfileobj", side_effect=pause_copy),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        publication = executor.submit(_archive_retry_attempt, result_path, trial_id)
        try:
            assert copying.wait(timeout=5)
            with pytest.raises(FileNotFoundError):
                archive_path.read_bytes()
            (partial,) = set(tmp_path.iterdir()) - {trial_dir}
            assert partial.name.startswith(".")
            assert partial.read_bytes() == result_bytes[:5]
        finally:
            finish_copy.set()
        publication.result(timeout=5)

    assert archive_path.read_bytes() == result_bytes
    assert set(tmp_path.iterdir()) == {trial_dir, archive_path}


@pytest.mark.parametrize("entry_type", ["file", "symlink", "directory"])
async def test_queue_preserves_trial_when_retry_archive_exists(
    trial_config, retryable_trial_result, retrying_queue, tmp_path, entry_type
):
    trial = _mock_trial_with_result_file(retryable_trial_result)
    archive = tmp_path / f"retry-attempt-{retryable_trial_result.id}.json"
    if entry_type == "directory":
        archive.mkdir()
        existing = archive / "sentinel"
    elif entry_type == "symlink":
        existing = tmp_path / "existing.json"
        archive.symlink_to(existing)
    else:
        existing = archive
    existing.write_bytes(b"previous archive")
    existing_entries = set(tmp_path.iterdir())
    create = AsyncMock(return_value=trial)

    with (
        patch("harbor.trial.trial.Trial.create", create),
        pytest.raises(FileExistsError),
    ):
        await retrying_queue.submit(trial_config)

    create.assert_awaited_once_with(trial_config)
    assert existing.read_bytes() == b"previous archive"
    if entry_type == "symlink":
        assert archive.readlink() == existing
    assert trial.paths.result_path.read_bytes() == (
        retryable_trial_result.model_dump_json().encode()
    )
    assert set(tmp_path.iterdir()) == existing_entries | {trial.paths.trial_dir}


async def test_queue_preserves_trial_when_retry_archive_write_fails(
    trial_config, retryable_trial_result, retrying_queue, tmp_path
):
    trial = _mock_trial_with_result_file(retryable_trial_result)
    create = AsyncMock(return_value=trial)
    write_error = OSError("archive write failed")

    def fail_copy(source, destination):
        assert destination.write(source.read(5)) == 5
        destination.flush()
        raise write_error

    with (
        patch("harbor.trial.trial.Trial.create", create),
        patch("harbor.trial.queue.shutil.copyfileobj", side_effect=fail_copy),
        pytest.raises(OSError, match="archive write failed") as exc_info,
    ):
        await retrying_queue.submit(trial_config)

    assert exc_info.value is write_error
    create.assert_awaited_once_with(trial_config)
    assert trial.paths.result_path.read_bytes() == (
        retryable_trial_result.model_dump_json().encode()
    )
    assert not list(tmp_path.glob("retry-attempt-*.json"))
    assert set(tmp_path.iterdir()) == {trial.paths.trial_dir}


async def test_queue_preserves_trial_when_retry_archive_link_fails(
    trial_config, retryable_trial_result, retrying_queue, tmp_path
):
    result_bytes = retryable_trial_result.model_dump_json().encode()
    trial = _mock_trial_with_result_file(retryable_trial_result)
    archive = tmp_path / f"retry-attempt-{retryable_trial_result.id}.json"
    other_temp = tmp_path / ".retry-attempt-other"
    other_temp.write_bytes(b"another writer")
    create = AsyncMock(return_value=trial)
    link_error = OSError("archive link failed")

    def fail_link(source, destination):
        assert Path(source).read_bytes() == result_bytes
        assert destination == archive
        raise link_error

    with (
        patch("harbor.trial.trial.Trial.create", create),
        patch("harbor.trial.queue.os.link", side_effect=fail_link),
        pytest.raises(OSError, match="archive link failed") as exc_info,
    ):
        await retrying_queue.submit(trial_config)

    assert exc_info.value is link_error
    create.assert_awaited_once_with(trial_config)
    assert trial.paths.result_path.read_bytes() == result_bytes
    assert other_temp.read_bytes() == b"another writer"
    assert set(tmp_path.iterdir()) == {trial.paths.trial_dir, other_temp}


async def test_queue_preserves_trial_when_retry_source_is_missing(
    trial_config, retryable_trial_result, retrying_queue, tmp_path
):
    paths = TrialPaths(tmp_path / trial_config.trial_name)
    paths.trial_dir.mkdir()
    paths.log_path.write_bytes(b"original trial log")
    trial = MagicMock(
        config=trial_config,
        id=retryable_trial_result.id,
        paths=paths,
        run=AsyncMock(return_value=retryable_trial_result),
    )
    create = AsyncMock(return_value=trial)
    with (
        patch("harbor.trial.trial.Trial.create", create),
        pytest.raises(FileNotFoundError) as exc_info,
    ):
        await retrying_queue.submit(trial_config)

    assert exc_info.value.filename == str(paths.result_path)
    create.assert_awaited_once_with(trial_config)
    assert paths.log_path.read_bytes() == b"original trial log"
    assert not list(tmp_path.glob("retry-attempt-*.json"))
    assert not paths.result_path.exists()
    assert set(tmp_path.iterdir()) == {paths.trial_dir}


class TestTrialQueue:
    """Tests for TrialQueue."""

    @pytest.mark.unit
    def test_initialization(self, hooks):
        """Test TrialQueue initialization."""
        queue = TrialQueue(
            n_concurrent=3,
            retry_config=RetryConfig(max_retries=5),
            hooks=hooks,
        )

        assert queue._n_concurrent == 3
        assert queue._retry_config.max_retries == 5
        assert queue._semaphore._value == 3

    @pytest.mark.unit
    def test_initialization_with_defaults(self):
        """Test TrialQueue initialization with public API defaults."""
        queue = TrialQueue(n_concurrent=3)

        assert queue._n_concurrent == 3
        assert queue._retry_config == RetryConfig()
        assert queue._hooks == {event: [] for event in TrialEvent}

    @pytest.mark.unit
    async def test_submit_single_trial(self, queue, trial_config, trial_result):
        """Test submitting and awaiting a single trial."""
        with patch.object(
            queue, "_execute_trial_with_retries", return_value=trial_result
        ):
            result = await queue.submit(trial_config)
            assert result == trial_result

    @pytest.mark.unit
    async def test_submit_batch(self, queue, trial_result):
        """Test submitting multiple trials via gather."""
        configs = [
            TrialConfig(
                task=TaskConfig(path=Path(f"/test/task{i}")),
                trial_name=f"test_trial_{i}",
                job_id=uuid4(),
            )
            for i in range(3)
        ]

        with patch.object(
            queue, "_execute_trial_with_retries", return_value=trial_result
        ):
            coros = queue.submit_batch(configs)
            assert len(coros) == 3

            results = await asyncio.gather(*coros)
            assert len(results) == 3
            assert all(result == trial_result for result in results)

    @pytest.mark.unit
    async def test_submit_batch_with_task_group(self, queue, trial_result):
        """Test submitting multiple trials via TaskGroup."""
        configs = [
            TrialConfig(
                task=TaskConfig(path=Path(f"/test/task{i}")),
                trial_name=f"test_trial_{i}",
                job_id=uuid4(),
            )
            for i in range(3)
        ]

        with patch.object(
            queue, "_execute_trial_with_retries", return_value=trial_result
        ):
            coros = queue.submit_batch(configs)
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(coro) for coro in coros]

            results = [t.result() for t in tasks]
            assert len(results) == 3
            assert all(result == trial_result for result in results)

    @pytest.mark.unit
    async def test_cancellation_via_task_group(self, queue, trial_config):
        """Test that TaskGroup cancels remaining trials on exception."""

        async def slow_execute(config):
            await asyncio.sleep(60)
            return MagicMock(spec=TrialResult)

        async def failing_execute(config):
            raise ValueError("forced failure")

        call_count = 0

        async def mixed_execute(config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("forced failure")
            await asyncio.sleep(60)
            return MagicMock(spec=TrialResult)

        configs = [
            TrialConfig(
                task=TaskConfig(path=Path(f"/test/task{i}")),
                trial_name=f"test_trial_{i}",
                job_id=uuid4(),
            )
            for i in range(3)
        ]

        with patch.object(
            queue, "_execute_trial_with_retries", side_effect=mixed_execute
        ):
            with pytest.raises(ExceptionGroup):
                async with asyncio.TaskGroup() as tg:
                    for coro in queue.submit_batch(configs):
                        tg.create_task(coro)

    @pytest.mark.unit
    def test_add_hook_registers_callback(self):
        """Test that add_hook registers callbacks on the public API."""

        async def test_hook(event: TrialHookEvent):
            return None

        queue = TrialQueue(n_concurrent=1)

        result = queue.add_hook(TrialEvent.END, test_hook)

        assert result is queue
        assert queue._hooks[TrialEvent.END] == [test_hook]

    @pytest.mark.unit
    def test_setup_hooks_wires_all_events(self):
        """Test that _setup_hooks wires all hook events to a trial."""
        queue = TrialQueue(n_concurrent=1)

        async def hook_a(event: TrialHookEvent):
            pass

        async def hook_b(event: TrialHookEvent):
            pass

        queue.add_hook(TrialEvent.START, hook_a)
        queue.add_hook(TrialEvent.END, hook_b)
        queue.add_hook(TrialEvent.ENVIRONMENT_START, hook_a)

        mock_trial = MagicMock()
        mock_trial.config = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="test_trial",
            job_id=uuid4(),
        )
        queue._setup_hooks(mock_trial)

        calls = mock_trial.add_hook.call_args_list
        wired = [(call.args[0], call.args[1]) for call in calls]

        assert (TrialEvent.START, hook_a) in wired
        assert (TrialEvent.END, hook_b) in wired
        assert (TrialEvent.ENVIRONMENT_START, hook_a) in wired

    @pytest.mark.unit
    async def test_exception_handling(self, queue, trial_config):
        """Test that exceptions propagate through awaiting the coroutine."""
        test_exception = ValueError("Test error")

        with patch.object(
            queue,
            "_execute_trial_with_retries",
            side_effect=test_exception,
        ):
            with pytest.raises(ValueError, match="Test error"):
                await queue.submit(trial_config)

    @pytest.mark.unit
    def test_should_retry_exception(self, queue):
        """Test retry logic for exceptions."""
        assert queue._retry_config.is_retryable("SomeError")

        queue._retry_config.exclude_exceptions = {"TimeoutError"}
        assert not queue._retry_config.is_retryable("TimeoutError")
        assert queue._retry_config.is_retryable("ValueError")

        queue._retry_config.exclude_exceptions = None
        queue._retry_config.include_exceptions = {"TimeoutError", "ValueError"}
        assert queue._retry_config.is_retryable("TimeoutError")
        assert queue._retry_config.is_retryable("ValueError")
        assert not queue._retry_config.is_retryable("RuntimeError")

    @pytest.mark.unit
    def test_api_rate_limit_error_is_retryable(self, queue):
        """Test that ApiRateLimitError (#1798) is retried by default and
        survives an include_exceptions policy that drops the generic failure."""
        assert queue._retry_config.is_retryable("ApiRateLimitError")

        queue._retry_config.include_exceptions = {"ApiRateLimitError"}
        assert queue._retry_config.is_retryable("ApiRateLimitError")
        assert not queue._retry_config.is_retryable("NonZeroAgentExitCodeError")

    @pytest.mark.unit
    def test_api_usage_limit_error_is_retryable_by_default(self, queue):
        assert queue._retry_config.is_retryable("ApiUsageLimitError")

    @pytest.mark.unit
    def test_agent_authentication_error_is_not_retryable_by_default(self, queue):
        assert not queue._retry_config.is_retryable("AgentAuthenticationError")

    @pytest.mark.unit
    def test_model_not_found_error_is_not_retryable_by_default(self, queue):
        assert not queue._retry_config.is_retryable("ModelNotFoundError")

    @pytest.mark.unit
    def test_calculate_backoff_delay_sec(self, queue):
        """Test backoff delay calculation."""
        queue._retry_config.min_wait_sec = 1.0
        queue._retry_config.wait_multiplier = 2.0
        queue._retry_config.max_wait_sec = 10.0

        assert queue._calculate_backoff_delay_sec(0) == 1.0
        assert queue._calculate_backoff_delay_sec(1) == 2.0
        assert queue._calculate_backoff_delay_sec(2) == 4.0
        assert queue._calculate_backoff_delay_sec(3) == 8.0
        assert queue._calculate_backoff_delay_sec(4) == 10.0  # capped at max
        assert queue._calculate_backoff_delay_sec(5) == 10.0  # capped at max

    @pytest.mark.unit
    async def test_concurrent_execution(self, queue):
        """Test that trials execute concurrently."""
        configs = [
            TrialConfig(
                task=TaskConfig(path=Path(f"/test/task{i}")),
                trial_name=f"test_trial_{i}",
                job_id=uuid4(),
            )
            for i in range(5)
        ]

        execution_times = []

        async def mock_execute_trial(config):
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)
            end = asyncio.get_event_loop().time()
            execution_times.append((start, end))
            return MagicMock(spec=TrialResult)

        with patch.object(
            queue, "_execute_trial_with_retries", side_effect=mock_execute_trial
        ):
            await asyncio.gather(*queue.submit_batch(configs))

        assert len(execution_times) == 5

        overlapping = False
        for i in range(1, len(execution_times)):
            if execution_times[i][0] < execution_times[i - 1][1]:
                overlapping = True
                break

        assert overlapping, "Expected some concurrent execution"

    @pytest.mark.unit
    async def test_agent_concurrency_blocks_until_agent_end(self, trial_config):
        queue = TrialQueue(n_concurrent=2)
        trial_config = trial_config.model_copy(
            update={"agent": AgentConfig(n_concurrent=1)}
        )
        first = TrialHookEvent(
            event=TrialEvent.AGENT_START,
            task_name="task",
            config=trial_config,
            result=_make_trial_result(trial_config),
            lock=_make_trial_lock(),
        )
        second_config = trial_config.model_copy(update={"trial_name": "trial-2"})
        second = TrialHookEvent(
            event=TrialEvent.AGENT_START,
            task_name="task",
            config=second_config,
            result=_make_trial_result(second_config),
            lock=_make_trial_lock(),
        )

        await queue._acquire_agent_permit(first)
        blocked = asyncio.create_task(queue._acquire_agent_permit(second))
        await asyncio.sleep(0)

        assert not blocked.done()

        await queue._release_agent_permit(first)
        await asyncio.wait_for(blocked, timeout=1)
        await queue._release_agent_permit(second)

    @pytest.mark.unit
    async def test_per_agent_concurrency_group_blocks_until_agent_end(self):
        queue = TrialQueue(n_concurrent=2)
        agent = AgentConfig(
            name="claude-code",
            model_name="anthropic/claude-opus-4-1",
            n_concurrent=1,
            concurrency_group="anthropic",
        )
        first_config = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-1",
            job_id=uuid4(),
            agent=agent,
        )
        second_config = first_config.model_copy(update={"trial_name": "trial-2"})
        first = TrialHookEvent(
            event=TrialEvent.AGENT_START,
            task_name="task",
            config=first_config,
            result=_make_trial_result(first_config),
            lock=_make_trial_lock(),
        )
        second = TrialHookEvent(
            event=TrialEvent.AGENT_START,
            task_name="task",
            config=second_config,
            result=_make_trial_result(second_config),
            lock=_make_trial_lock(),
        )

        queue._validate_agent_concurrency([first_config, second_config])
        await queue._acquire_agent_permit(first)
        blocked = asyncio.create_task(queue._acquire_agent_permit(second))
        await asyncio.sleep(0)

        assert not blocked.done()

        await queue._release_agent_permit(first)
        await asyncio.wait_for(blocked, timeout=1)
        await queue._release_agent_permit(second)

    @pytest.mark.unit
    def test_conflicting_agent_concurrency_group_limits_are_rejected(self):
        queue = TrialQueue(n_concurrent=2)
        first = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-1",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                n_concurrent=1,
                concurrency_group="shared",
            ),
        )
        second = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-2",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                n_concurrent=2,
                concurrency_group="shared",
            ),
        )

        with pytest.raises(ValueError, match="Conflicting n_concurrent"):
            queue.submit_batch([first, second])

    @pytest.mark.unit
    def test_implicit_agent_pool_key_uses_full_agent_identity(self):
        queue = TrialQueue(n_concurrent=2)
        first = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-1",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                model_name="anthropic/claude-opus-4-1",
                n_concurrent=1,
                kwargs={"permission_mode": "acceptEdits"},
            ),
        )
        second = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-2",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                model_name="anthropic/claude-opus-4-1",
                n_concurrent=2,
                kwargs={"permission_mode": "bypassPermissions"},
            ),
        )

        assert first.agent.concurrency_key != second.agent.concurrency_key
        queue._validate_agent_concurrency([first, second])

        assert len(queue._agent_pool_semaphores) == 2

    @pytest.mark.unit
    def test_implicit_agent_pool_key_uses_raw_sensitive_env_values(self):
        queue = TrialQueue(n_concurrent=2)
        first = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-1",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                n_concurrent=1,
                env={"API_KEY": "super-secret-one"},
            ),
        )
        second = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-2",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                n_concurrent=2,
                env={"API_KEY": "super-secret-two"},
            ),
        )

        assert first.agent.concurrency_key != second.agent.concurrency_key
        queue._validate_agent_concurrency([first, second])

        assert len(queue._agent_pool_semaphores) == 2

    @pytest.mark.unit
    def test_agent_concurrency_key_is_not_serialized(self):
        agent = AgentConfig(name="claude-code", n_concurrent=1)

        assert "concurrency_key" not in agent.model_dump()

    @pytest.mark.unit
    def test_implicit_agent_pool_conflict_error_does_not_expose_agent_env(self):
        queue = TrialQueue(n_concurrent=2)
        first = TrialConfig(
            task=TaskConfig(path=Path("/test/task")),
            trial_name="trial-1",
            job_id=uuid4(),
            agent=AgentConfig(
                name="claude-code",
                n_concurrent=1,
                env={"API_KEY": "super-secret-value"},
            ),
        )
        second = first.model_copy(
            update={
                "trial_name": "trial-2",
                "agent": first.agent.model_copy(update={"n_concurrent": 2}),
            }
        )

        with pytest.raises(ValueError) as exc_info:
            queue.submit_batch([first, second])

        message = str(exc_info.value)
        assert "Conflicting n_concurrent" in message
        assert "super-secret-value" not in message

    @pytest.mark.unit
    async def test_trial_end_releases_leftover_agent_permits(self, trial_config):
        queue = TrialQueue(n_concurrent=1)
        trial_config = trial_config.model_copy(
            update={"agent": AgentConfig(n_concurrent=1)}
        )
        first = TrialHookEvent(
            event=TrialEvent.AGENT_START,
            task_name="task",
            config=trial_config,
            result=_make_trial_result(trial_config),
            lock=_make_trial_lock(),
        )
        second_config = trial_config.model_copy(update={"trial_name": "trial-2"})
        second = TrialHookEvent(
            event=TrialEvent.AGENT_START,
            task_name="task",
            config=second_config,
            result=_make_trial_result(second_config),
            lock=_make_trial_lock(),
        )

        await queue._acquire_agent_permit(first)
        await queue._release_all_agent_permits(
            TrialHookEvent(
                event=TrialEvent.END,
                task_name="task",
                config=trial_config,
                result=_make_trial_result(trial_config),
                lock=_make_trial_lock(),
            )
        )

        await asyncio.wait_for(queue._acquire_agent_permit(second), timeout=1)
        await queue._release_agent_permit(second)


@pytest.mark.parametrize("retry_error", [False, True])
async def test_queue_uses_job_policy_overrides(
    trial_config, trial_result, tmp_path, retry_error
):
    from harbor.models.job.config import JobConfig
    from harbor.models.trial.error_policy import ErrorPolicyOverride
    from harbor.trial.errors import AgentTimeoutError

    job = JobConfig(
        retry=RetryConfig(max_retries=1, min_wait_sec=0),
        override_error_policy={
            "AgentTimeoutError": ErrorPolicyOverride(should_retry=retry_error)
        },
    )
    trial_config.override_error_policy = job.override_error_policy
    failed = trial_result.model_copy(deep=True, update={"id": uuid4()})
    failed.exception_info = ExceptionInfo.from_exception(
        AgentTimeoutError("timeout"),
        override_error_policy=trial_config.override_error_policy,
    )
    failed_trial = _mock_trial_with_result_file(failed)
    replacement = _mock_trial_with_result_file(trial_result)
    create = AsyncMock(side_effect=[failed_trial, replacement])
    queue = TrialQueue(n_concurrent=1, retry_config=job.retry)
    with patch("harbor.trial.trial.Trial.create", create):
        result = await queue.submit(trial_config)
    assert create.await_count == 1 + retry_error
    failed_trial.run.assert_awaited_once_with()
    assert replacement.run.await_count == retry_error
    assert result is (trial_result if retry_error else failed)
