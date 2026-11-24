import asyncio
import contextvars
import json
import logging
import math
import os
import re
import secrets
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.result import SeatResult
from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.seats import SeatsTrial

HOST_SCHEMA = "harbor.seat-cut.host/v1"
HOST_DOCUMENT = "seat-cut-host.json"
MARK_COMMAND = "python3 /app/mark.py"
MARK_USER = "root"
MARK_CWD = "/"
PROTOCOL_MAX_SAMPLES = 32
MIN_SAMPLES = 2
MIN_INTERVAL_SEC = 0.05
MAX_INTERVAL_SEC = 60.0
DEFAULT_TIMEOUT_SEC = 10.0
MIN_TIMEOUT_SEC = 1.0
MAX_TIMEOUT_SEC = 60.0
EXEC_TERMINATE_GRACE_SEC = 20.0
SETTLE_GRACE_SEC = 5.0
LIFECYCLE_POLL_SEC = 0.25
MAX_WATCH_SEC = 24 * 60 * 60
MAX_DOCUMENT_BYTES = 64 * 1024
TEMP_NAME_ATTEMPTS = 4
MARK_ID = re.compile(r"[0-9a-f]{32}")
HOST_COMMAND = re.compile(r"python3 /app/[a-z_]{1,32}\.py")
SEAT_STATES = frozenset({"not_started", "running", "clean", "error"})
TERMINAL_STATES = frozenset({"clean", "error"})

Select = Callable[[dict[str, str]], Awaitable[str | None]]

logger = logging.getLogger(__name__)


def seat_state(result: SeatResult) -> str:
    execution = result.agent_execution
    if execution is None or execution.started_at is None:
        return "not_started"
    if execution.finished_at is None:
        return "running"
    if result.exception_info is not None:
        return "error"
    return "clean"


def role_states(
    seat_results: Sequence[SeatResult], target: str, peer: str
) -> dict[str, str]:
    assert target != peer
    by_seat = {result.seat: result for result in seat_results}
    assert len(by_seat) == len(seat_results)
    states = {"target": seat_state(by_seat[target]), "peer": seat_state(by_seat[peer])}
    assert set(states.values()) <= SEAT_STATES
    return states


async def _classic(before: dict[str, str]) -> str:
    return MARK_COMMAND


def _spawn(coroutine: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    return asyncio.get_running_loop().create_task(
        coroutine, context=contextvars.Context()
    )


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage(directory: Path, name: str, data: bytes) -> tuple[Path, tuple[int, int]]:
    assert 0 < len(data) <= MAX_DOCUMENT_BYTES
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(TEMP_NAME_ATTEMPTS):
        staged = directory / f".{name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(staged, flags, 0o644)
        except FileExistsError:
            continue
        try:
            view = memoryview(data)
            for _ in range(len(data)):
                if not view:
                    break
                view = view[os.write(fd, view) :]
            if view:
                raise OSError(f"short write staging {name}")
            os.fsync(fd)
            stat = os.fstat(fd)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        finally:
            os.close(fd)
        return staged, (stat.st_dev, stat.st_ino)
    raise FileExistsError(f"no free staging name for {name}")


class _Journal:
    def __init__(self, path: Path, document: dict[str, Any]) -> None:
        self.path = path
        self.document = document
        self._identity: tuple[int, int] | None = None

    def _encode(self) -> bytes:
        data = (json.dumps(self.document, indent=2) + "\n").encode()
        if len(data) > MAX_DOCUMENT_BYTES:
            raise ValueError("host document exceeds its size bound")
        return data

    def create(self) -> None:
        assert self._identity is None
        staged, identity = _stage(self.path.parent, self.path.name, self._encode())
        try:
            os.link(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)
        self._identity = identity
        _fsync_directory(self.path.parent)

    def replace(self) -> None:
        assert self._identity is not None
        current = os.lstat(self.path)
        if (current.st_dev, current.st_ino) != self._identity:
            raise RuntimeError("host document changed underneath the observer")
        staged, identity = _stage(self.path.parent, self.path.name, self._encode())
        try:
            os.replace(staged, self.path)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        self._identity = identity
        _fsync_directory(self.path.parent)


class _Observer:
    def __init__(
        self,
        trial: SeatsTrial,
        journal: _Journal,
        *,
        target: str,
        peer: str,
        interval_seconds: float,
        timeout_seconds: float,
        max_samples: int,
        select: Select = _classic,
    ) -> None:
        self._trial = trial
        self._journal = journal
        self._samples: list[dict[str, Any]] = journal.document["samples"]
        self._trial_id = journal.document["trial_id"]
        self._ticket = journal.document["ticket"]
        self._target = target
        self._peer = peer
        self._interval = interval_seconds
        self._exec_timeout = math.ceil(timeout_seconds)
        self._exec_guard = timeout_seconds + EXEC_TERMINATE_GRACE_SEC
        self._settle_bound = self._exec_guard + SETTLE_GRACE_SEC
        self._max_samples = max_samples
        self._select = select
        self._stop = asyncio.Event()
        self._sampler: asyncio.Task[None] | None = None
        self._started = False
        self._marking = True
        self._inflight = False
        self._witnessed = False
        self._broken = False
        self._closed = False

    def _states(self) -> dict[str, str]:
        return role_states(
            self._trial.result.seat_results or [], self._target, self._peer
        )

    def _write(self) -> bool:
        if self._closed or self._broken:
            return False
        try:
            self._journal.replace()
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("seat-cut host journal disabled: %s", type(exc).__name__)
            self._broken = True
            self._marking = False
            return False
        return True

    def _close(self) -> None:
        if self._closed:
            return
        self._stop.set()
        self._marking = False
        if self._samples and self._samples[-1]["ack"] == "pending":
            self._samples[-1]["ack"] = "failed"
        self._journal.document["closed"] = True
        self._write()
        self._closed = True

    def _may_mark(self, limit: int) -> bool:
        return (
            self._marking
            and not self._inflight
            and not self._closed
            and not self._broken
            and len(self._samples) < limit
        )

    async def _mark(self, mark_id: str, command: str) -> bool:
        assert MARK_ID.fullmatch(mark_id)
        assert HOST_COMMAND.fullmatch(command)
        try:
            result = await asyncio.wait_for(
                self._trial.agent_environment.exec(
                    f"{command} {mark_id}",
                    cwd=MARK_CWD,
                    timeout_sec=self._exec_timeout,
                    user=MARK_USER,
                ),
                timeout=self._exec_guard,
            )
        except Exception as exc:
            logger.warning("seat-cut mark failed: %s", type(exc).__name__)
            return False
        return result.return_code == 0 and result.stdout == f"OK {mark_id}\n"

    async def _attempt(self, limit: int) -> None:
        if not self._may_mark(limit):
            return
        mark_id = secrets.token_hex(16)
        before = self._states()
        sample: dict[str, Any] = {
            "id": mark_id,
            "before": before,
            "after": None,
            "ack": "pending",
        }
        self._samples.append(sample)
        if not self._write():
            return
        self._inflight = True
        try:
            command = await self._select(dict(before))
            acknowledged = command is not None and await self._mark(mark_id, command)
        except BaseException:
            self._marking = False
            raise
        finally:
            self._inflight = False
        sample["after"] = self._states()
        sample["ack"] = "ok" if acknowledged else "failed"
        if not acknowledged:
            self._marking = False
        elif before["target"] in TERMINAL_STATES:
            self._witnessed = True
        self._write()

    async def _sample_loop(self) -> None:
        loop = asyncio.get_running_loop()
        poll = min(self._interval, LIFECYCLE_POLL_SEC)
        next_mark = loop.time() + self._interval
        deadline = loop.time() + MAX_WATCH_SEC
        for _ in range(math.ceil(MAX_WATCH_SEC / poll)):
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=poll)
                return
            except TimeoutError:
                pass
            if self._witnessed or not self._may_mark(self._max_samples):
                return
            if self._states()["target"] in TERMINAL_STATES:
                await self._attempt(self._max_samples)
                return
            if loop.time() >= deadline:
                return
            if loop.time() >= next_mark and self._may_mark(self._max_samples - 1):
                await self._attempt(self._max_samples - 1)
                next_mark = loop.time() + self._interval

    async def _settle(self, task: asyncio.Task[None], timeout: float) -> None:
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
        except asyncio.CancelledError:
            task.cancel()
            try:
                await asyncio.wait({task}, timeout=SETTLE_GRACE_SEC)
            finally:
                self._marking = False
            raise
        if not done:
            self._marking = False
            task.cancel()
            await asyncio.wait({task}, timeout=SETTLE_GRACE_SEC)
        if task.done() and not task.cancelled() and task.exception() is not None:
            logger.warning(
                "seat-cut observer task failed: %s", type(task.exception()).__name__
            )
            self._marking = False

    async def on_agent_start(self, event: TrialHookEvent) -> None:
        if self._started or self._closed:
            return
        self._started = True
        if str(event.trial_id) != self._trial_id or event.trial_name != self._ticket:
            logger.warning("seat-cut observer saw a different trial; marking disabled")
            self._marking = False
            return
        await self._settle(
            _spawn(self._attempt(self._max_samples - 1)),
            self._settle_bound,
        )
        if self._may_mark(self._max_samples) and not self._witnessed:
            self._sampler = _spawn(self._sample_loop())

    async def on_agent_end(self, event: TrialHookEvent) -> None:
        if self._closed:
            return
        self._stop.set()
        try:
            if self._sampler is not None:
                await self._settle(self._sampler, self._settle_bound)
            current = asyncio.current_task()
            cancelling = current is not None and current.cancelling() > 0
            sampler_done = self._sampler is None or self._sampler.done()
            if (
                self._started
                and sampler_done
                and not cancelling
                and not self._witnessed
            ):
                await self._settle(
                    _spawn(self._attempt(self._max_samples)),
                    self._settle_bound,
                )
        finally:
            self._close()

    async def on_finish(self, event: TrialHookEvent) -> None:
        if self._closed:
            return
        self._stop.set()
        self._marking = False
        try:
            if self._sampler is not None and not self._sampler.done():
                self._sampler.cancel()
                await self._settle(self._sampler, SETTLE_GRACE_SEC)
        finally:
            self._close()


def _seconds(name: str, value: float, low: float, high: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ValueError(f"{name} must be within [{low}, {high}] seconds")
    return float(value)


def attach(
    trial: SeatsTrial,
    *,
    target: str,
    peer: str,
    interval_seconds: float = 0.25,
    max_samples: int = 16,
    timeout_seconds: float = DEFAULT_TIMEOUT_SEC,
    select: Select = _classic,
) -> None:
    if not isinstance(trial, SeatsTrial):
        raise TypeError("seat-cut observation requires a native SeatsTrial")
    for role in (target, peer):
        if not isinstance(role, str) or not role:
            raise ValueError("target and peer must be non-empty seat names")
    if target == peer:
        raise ValueError("target and peer must be distinct seats")
    interval = _seconds(
        "interval_seconds", interval_seconds, MIN_INTERVAL_SEC, MAX_INTERVAL_SEC
    )
    timeout = _seconds(
        "timeout_seconds", timeout_seconds, MIN_TIMEOUT_SEC, MAX_TIMEOUT_SEC
    )
    if (
        isinstance(max_samples, bool)
        or not isinstance(max_samples, int)
        or not MIN_SAMPLES <= max_samples <= PROTOCOL_MAX_SAMPLES
    ):
        raise ValueError(
            f"max_samples must be within [{MIN_SAMPLES}, {PROTOCOL_MAX_SAMPLES}]"
        )
    if trial.agent_environment.type() != EnvironmentType.DOCKER:
        raise ValueError(
            "live seat-cut observation requires the local docker environment"
        )
    seats = {seat.name: seat for seat in trial.seats}
    if len(trial.seats) != 2 or set(seats) != {target, peer}:
        raise ValueError(
            "live seat-cut observation requires exactly the two named seats"
        )
    if not all(seats[role].under_test for role in (target, peer)):
        raise ValueError("both observed seats must be under test")
    if trial.config.install_only:
        raise ValueError("install-only trials never play")
    try:
        trial.result
    except RuntimeError:
        pass
    else:
        raise ValueError("attach before the trial runs")
    document: dict[str, Any] = {
        "schema": HOST_SCHEMA,
        "trial_id": str(trial.id),
        "ticket": trial.config.trial_name,
        "target": target,
        "peer": peer,
        "closed": False,
        "samples": [],
    }
    journal = _Journal(trial.paths.trial_dir / HOST_DOCUMENT, document)
    journal.create()
    observer = _Observer(
        trial,
        journal,
        target=target,
        peer=peer,
        interval_seconds=interval,
        timeout_seconds=timeout,
        max_samples=max_samples,
        select=select,
    )
    trial.add_hook(TrialEvent.AGENT_START, observer.on_agent_start)
    trial.add_hook(TrialEvent.AGENT_END, observer.on_agent_end)
    trial.add_hook(TrialEvent.CANCEL, observer.on_finish)
    trial.add_hook(TrialEvent.END, observer.on_finish)
