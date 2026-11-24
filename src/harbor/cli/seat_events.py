from __future__ import annotations

import asyncio
import errno
import functools
import json
import os
import stat
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, override

from rich.console import Console

from harbor.models.job.plugin import BaseJobPlugin
from harbor.trial.seat_events import SeatEventRow, SeatEventStream
from harbor.trial.seats import SeatsTrial

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.models.job.result import JobResult
    from harbor.models.trial.result import TrialResult
    from harbor.trial.trial import Trial

SEAT_EVENTS_FILE_NAME = "seat-events.jsonl"


class SeatEventsJournalError(Exception):
    """The seat-events journal could not be created; play has not started."""


def _errno_name(exc: OSError) -> str:
    if exc.errno is None:
        return "OSError"
    return errno.errorcode.get(exc.errno, f"errno {exc.errno}")


def _open_journal(trial_dir: Path) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None or os.open not in os.supports_dir_fd:
        raise SeatEventsJournalError("unsupported platform")
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        dir_fd = os.open(trial_dir, os.O_RDONLY | directory | nofollow | cloexec)
        try:
            fd = os.open(
                SEAT_EVENTS_FILE_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | nofollow | cloexec,
                0o600,
                dir_fd=dir_fd,
            )
        finally:
            os.close(dir_fd)
    except OSError as exc:
        raise SeatEventsJournalError(_errno_name(exc)) from None
    try:
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        raise SeatEventsJournalError(_errno_name(exc)) from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != 0:
        os.close(fd)
        raise SeatEventsJournalError("not a new regular file")
    return fd


def _encode(row: SeatEventRow) -> bytes:
    line = json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return line.encode("ascii") + b"\n"


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    for _ in range(len(data)):
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]
        if not view:
            return


def _close(fd: int, done: asyncio.Future[None] | None = None) -> None:
    if done is not None and not done.cancelled():
        done.exception()
    try:
        os.close(fd)
    except OSError:
        pass


async def _drain(stream: SeatEventStream, fd: int) -> str | None:
    loop = asyncio.get_running_loop()
    pending: asyncio.Future[None] | None = None
    try:
        async for row in stream:
            pending = loop.run_in_executor(None, _write_all, fd, _encode(row))
            await asyncio.shield(pending)
            if row["kind"] == "closed":
                pending = loop.run_in_executor(None, os.fsync, fd)
                await asyncio.shield(pending)
                return None
        return "no closed row"
    except OSError as exc:
        return f"write failed: {_errno_name(exc)}"
    finally:
        if pending is None or pending.done():
            _close(fd)
        else:
            pending.add_done_callback(functools.partial(_close, fd))


@asynccontextmanager
async def observe_seat_events(
    trial: SeatsTrial, *, warnings: Console
) -> AsyncIterator[None]:
    """Around one native trial.run(), one drain task appends each seat event as
    a line of TRIAL_DIR/seat-events.jsonl. Raises SeatEventsJournalError before
    play if the journal cannot be created; a later write failure only warns."""
    drain: asyncio.Task[str | None] | None = None
    try:
        with trial.seat_events() as stream:
            fd = _open_journal(trial.paths.trial_dir)
            drain = asyncio.Task(
                _drain(stream, fd),
                loop=asyncio.get_running_loop(),
                name="harbor-seat-events",
                eager_start=True,
            )
            yield
    finally:
        if drain is not None:
            reason = await asyncio.shield(drain)
            if reason is not None:
                warnings.print(
                    f"Warning: {SEAT_EVENTS_FILE_NAME} in the trial directory is "
                    f"incomplete ({reason}); do not use it as a complete record.",
                    markup=False,
                    highlight=False,
                )


async def run_with_seat_events(trial: SeatsTrial, *, warnings: Console) -> TrialResult:
    async with observe_seat_events(trial, warnings=warnings):
        return await trial.run()


class SeatEventsJobPlugin(BaseJobPlugin):
    def __init__(self) -> None:
        super().__init__()
        self._warnings = Console(stderr=True)

    @override
    async def on_job_start(self, job: Job) -> None:
        if not job.config.participants:
            raise ValueError(
                "SeatEventsJobPlugin requires a job with configured participants."
            )
        job.observe_trial_attempts(self._observe)

    @override
    async def on_job_end(self, job_result: JobResult) -> None:
        del job_result

    def _observe(self, trial: Trial) -> AbstractAsyncContextManager[None]:
        if not isinstance(trial, SeatsTrial):
            raise TypeError(
                f"SeatEventsJobPlugin needs a SeatsTrial, got {type(trial).__name__}."
            )
        return observe_seat_events(trial, warnings=self._warnings)
