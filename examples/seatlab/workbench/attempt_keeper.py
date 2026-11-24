from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, override
from uuid import UUID

from harbor.cli.seat_events import SEAT_EVENTS_FILE_NAME, SeatEventsJobPlugin

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.trial.hooks import TrialHookEvent

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Skipped(Exception):
    pass


def _kind(exc: Exception) -> str:
    if isinstance(exc, OSError) and exc.errno is not None:
        return errno.errorcode.get(exc.errno, type(exc).__name__)
    return str(exc) if isinstance(exc, Skipped) else type(exc).__name__


def _open_private(
    path: str, pin: tuple[int, int] | None
) -> tuple[int, tuple[int, int]]:
    fd = os.open(path, DIR_FLAGS)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise Skipped("archive dir not private")
        if pin is not None and (info.st_dev, info.st_ino) != pin:
            raise Skipped("archive dir replaced")
    except BaseException:
        os.close(fd)
        raise
    return fd, (info.st_dev, info.st_ino)


class AttemptKeeper(SeatEventsJobPlugin):
    def __init__(self, archive_dir: str) -> None:
        super().__init__()
        if not isinstance(archive_dir, str) or not os.path.isabs(archive_dir):
            raise ValueError("AttemptKeeper archive_dir must be an absolute path.")
        if (
            os.link not in os.supports_dir_fd
            or os.link not in os.supports_follow_symlinks
        ):
            raise ValueError("AttemptKeeper needs linkat on this platform.")
        try:
            fd, self._pin = _open_private(archive_dir, None)
        except (OSError, Skipped) as exc:
            raise ValueError(
                f"AttemptKeeper archive_dir rejected ({_kind(exc)})."
            ) from None
        os.close(fd)
        self._archive = archive_dir

    @override
    async def on_job_start(self, job: Job) -> None:
        job_dir = Path(job.job_dir).resolve()
        archive = Path(self._archive).resolve()
        if archive.is_relative_to(job_dir) or job_dir.is_relative_to(archive):
            raise ValueError(
                "AttemptKeeper archive_dir must not nest with the job directory."
            )
        if os.stat(job_dir).st_dev != self._pin[0]:
            raise ValueError(
                "AttemptKeeper archive_dir must be on the job directory's device."
            )
        await super().on_job_start(job)
        job.on_trial_started(self._retain)

    async def _retain(self, event: TrialHookEvent) -> None:
        try:
            self._link(event)
        except (OSError, Skipped, ValueError) as exc:
            self._warnings.print(
                f"Warning: attempt journal not retained ({_kind(exc)}).",
                markup=False,
                highlight=False,
            )

    def _link(self, event: TrialHookEvent) -> None:
        name = event.trial_name
        if (
            not isinstance(name, str)
            or name in ("", ".", "..")
            or "/" in name
            or "\0" in name
        ):
            raise Skipped("unsafe trial name")
        if not isinstance(event.trial_id, UUID):
            raise Skipped("invalid trial id")
        job_id = event.config.job_id
        if not isinstance(job_id, UUID):
            raise Skipped("invalid job id")
        slot_key = hashlib.sha256(name.encode("utf-8")).hexdigest()
        kept_name = f"{job_id}.{slot_key}.{event.trial_id}.jsonl"
        archive_fd, _ = _open_private(self._archive, self._pin)
        try:
            trial_fd = os.open(Path(event.config.trials_dir) / name, DIR_FLAGS)
            try:
                before = os.stat(
                    SEAT_EVENTS_FILE_NAME, dir_fd=trial_fd, follow_symlinks=False
                )
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_nlink != 1
                ):
                    raise Skipped("journal not private")
                os.link(
                    SEAT_EVENTS_FILE_NAME,
                    kept_name,
                    src_dir_fd=trial_fd,
                    dst_dir_fd=archive_fd,
                    follow_symlinks=False,
                )
                after = os.stat(
                    SEAT_EVENTS_FILE_NAME, dir_fd=trial_fd, follow_symlinks=False
                )
                kept = os.stat(kept_name, dir_fd=archive_fd, follow_symlinks=False)
                identity = (before.st_dev, before.st_ino)
                if (
                    (after.st_dev, after.st_ino) != identity
                    or (kept.st_dev, kept.st_ino) != identity
                    or kept.st_nlink != 2
                ):
                    raise Skipped("journal changed during link")
            finally:
                os.close(trial_fd)
        finally:
            os.close(archive_fd)
