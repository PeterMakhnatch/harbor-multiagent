from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, override
from uuid import UUID as UUIDType

from rich.console import Console

from examples.seatlab.workbench.account_keeper import _reason, result_document
from examples.seatlab.workbench.attempt_keeper import Skipped, _open_private
from examples.seatlab.workbench.native_account import project_result
from examples.seatlab.workbench.private_receipt import _linkable, put_once
from examples.seatlab.workbench.seat_stream import UUID, Record, _require
from harbor.models.job.plugin import BaseJobPlugin
from harbor.trial.hooks import TrialEvent

from .attempt_receipt import project

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.models.job.result import JobResult
    from harbor.trial.hooks import TrialHookEvent

SUFFIX = ".handoffs.json"
MAX_ENTRIES = 65_536
RECEIPT = rf"{UUID.pattern}\.[0-9a-f]{{64}}\.{UUID.pattern}\.handoffs\.json"
OWNED = re.compile(rf"{RECEIPT}|\.{RECEIPT}\.[0-9a-f]{{32}}\.tmp")


def _encode(receipt: Record) -> bytes:
    text = json.dumps(
        receipt,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return (text + "\n").encode("ascii")


def _apart(directory: str, trial_directory: Path) -> bool:
    kept = Path(directory).resolve()
    job_dir = trial_directory.parent.resolve()
    return not (kept.is_relative_to(job_dir) or job_dir.is_relative_to(kept))


def capture(
    directory: str,
    trial_directory: Path,
    *,
    job_id: str,
    attempt: Record,
    pin: tuple[int, int] | None = None,
) -> str:
    _require(
        type(directory) is str and os.path.isabs(directory),
        "receipt directory is not an absolute path",
    )
    _require(
        isinstance(trial_directory, Path) and trial_directory.is_absolute(),
        "trial directory is not an absolute path",
    )
    _require(
        _apart(directory, trial_directory),
        "receipt directory nests with the job directory",
    )
    receipt = project(trial_directory, job=job_id, attempt=attempt)
    _require(
        (receipt["job"], receipt["slot"], receipt["trial"])
        == (job_id, attempt["slot"], attempt["trial"]),
        "receipt is bound to another attempt",
    )
    name = f"{receipt['job']}.{receipt['slot']}.{receipt['trial']}{SUFFIX}"
    put_once(directory, name, _encode(receipt), pin=pin)
    return name


def _dedicated(fd: int) -> bool:
    with os.scandir(fd) as entries:
        for count, entry in enumerate(entries):
            if count >= MAX_ENTRIES or OWNED.fullmatch(entry.name) is None:
                return False
    return True


class HandoffAttemptKeeper(BaseJobPlugin):
    def __init__(self, archive_dir: str) -> None:
        super().__init__()
        self._warnings = Console(stderr=True)
        if not isinstance(archive_dir, str) or not os.path.isabs(archive_dir):
            raise ValueError(
                "HandoffAttemptKeeper archive_dir must be an absolute path."
            )
        if not _linkable():
            raise ValueError("HandoffAttemptKeeper needs linkat on this platform.")
        self._pin = self._check(archive_dir, None)
        self._dir = archive_dir
        self._job: str | None = None
        self._trials: Path | None = None

    @staticmethod
    def _check(archive_dir: str, pin: tuple[int, int] | None) -> tuple[int, int]:
        try:
            fd, kept = _open_private(archive_dir, pin)
        except (OSError, Skipped):
            raise ValueError(
                "HandoffAttemptKeeper archive_dir must be a private directory of this user."
            ) from None
        try:
            dedicated = _dedicated(fd)
        except OSError:
            dedicated = False
        finally:
            os.close(fd)
        if not dedicated:
            raise ValueError(
                "HandoffAttemptKeeper archive_dir must hold only handoff receipts."
            )
        return kept

    @override
    async def on_job_start(self, job: Job) -> None:
        job_dir = Path(job.job_dir).resolve()
        kept = Path(self._dir).resolve()
        if kept.is_relative_to(job_dir) or job_dir.is_relative_to(kept):
            raise ValueError(
                "HandoffAttemptKeeper archive_dir must not nest with the job directory."
            )
        self._check(self._dir, self._pin)
        if not isinstance(job.id, UUIDType):
            raise TypeError("HandoffAttemptKeeper needs a job with a UUID id.")
        if not job.config.participants:
            raise ValueError("HandoffAttemptKeeper needs a multi-seat job.")
        self._job = str(job.id)
        self._trials = job_dir
        job.on_trial_ended(self._capture)

    @override
    async def on_job_end(self, job_result: JobResult) -> None:
        del job_result

    async def _capture(self, event: TrialHookEvent) -> None:
        try:
            self._keep(event)
        except (OSError, ValueError, Skipped, ArithmeticError) as exc:
            self._warnings.print(
                f"Warning: attempt handoff receipt not kept ({_reason(exc)}).",
                markup=False,
                highlight=False,
            )

    def _keep(self, event: TrialHookEvent) -> None:
        job_id, trials = self._job, self._trials
        if job_id is None or trials is None:
            raise Skipped("job not started")
        if event.event is not TrialEvent.END:
            raise Skipped("not a trial end event")
        config, result = event.config, event.result
        if not isinstance(config.job_id, UUIDType) or str(config.job_id) != job_id:
            raise Skipped("trial config belongs to another job")
        if (
            not isinstance(result.config.job_id, UUIDType)
            or str(result.config.job_id) != job_id
        ):
            raise Skipped("trial result belongs to another job")
        if not isinstance(event.trial_id, UUIDType) or not isinstance(
            result.id, UUIDType
        ):
            raise Skipped("invalid trial id")
        if result.trial_name != config.trial_name:
            raise Skipped("trial result names another directory")
        if result.finished_at is None:
            raise Skipped("attempt not finished")
        if Path(config.trials_dir).resolve() != trials:
            raise Skipped("trial lives outside the job directory")
        attempt = project_result(
            result_document(result), job_id=job_id, slot_name=config.trial_name
        )
        capture(
            self._dir,
            trials / config.trial_name,
            job_id=job_id,
            attempt=attempt,
            pin=self._pin,
        )
