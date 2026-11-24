from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, override
from uuid import UUID

from rich.console import Console

from harbor.models.job.plugin import BaseJobPlugin
from harbor.trial.hooks import TrialEvent

from .arm_runtime import _load
from .attempt_keeper import Skipped, _open_private
from .input_keys import cell, declared_view
from .input_witness import Witness, binding, encode, read_starts
from .native_account import _slot
from .private_receipt import _linkable, put_once

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.models.job.result import JobResult
    from harbor.trial.hooks import TrialHookEvent

LOCK = "lock.json"
RESULT_FIELDS = {
    "id",
    "trial_name",
    "config",
    "task_id",
    "task_checksum",
    "finished_at",
}
REJECTED = "InputStartKeeper archive_dir rejected."
NESTED = "InputStartKeeper archive_dir must not nest with the job directory."
REUSED = "InputStartKeeper serves exactly one job."
WRONG_JOB = "InputStartKeeper job directory differs from the enrolled path."
WARNING = "Warning: input start witness not retained."


class InputStartKeeper(BaseJobPlugin):
    def __init__(
        self, archive_dir: str, *, expected_job_dir: str | None = None
    ) -> None:
        super().__init__()
        if expected_job_dir is not None and (
            type(expected_job_dir) is not str
            or "\0" in expected_job_dir
            or not os.path.isabs(expected_job_dir)
            or os.path.realpath(expected_job_dir) != expected_job_dir
        ):
            raise ValueError(WRONG_JOB)
        self._expected_job_dir = expected_job_dir
        if (
            type(archive_dir) is not str
            or not os.path.isabs(archive_dir)
            or os.path.realpath(archive_dir) != archive_dir
            or not _linkable()
        ):
            raise ValueError(REJECTED)
        try:
            fd, self._pin = _open_private(archive_dir, None)
        except (OSError, Skipped):
            raise ValueError(REJECTED) from None
        os.close(fd)
        self._archive = archive_dir
        self._job: UUID | None = None
        self._trials: Path | None = None
        self._warnings = Console(stderr=True)

    @override
    async def on_job_start(self, job: Job) -> None:
        if self._job is not None:
            raise ValueError(REUSED)
        job_id = job.id
        if not isinstance(job_id, UUID):
            raise TypeError(REJECTED)
        self._job = job_id
        job_dir = Path(job.job_dir).resolve()
        if (
            self._expected_job_dir is not None
            and str(job_dir) != self._expected_job_dir
        ):
            raise ValueError(WRONG_JOB)
        archive = Path(self._archive)
        if archive.is_relative_to(job_dir) or job_dir.is_relative_to(archive):
            raise ValueError(NESTED)
        try:
            fd, _ = _open_private(self._archive, self._pin)
            os.close(fd)
            read_starts(archive, str(job_id))
        except (OSError, Skipped, ValueError):
            raise ValueError(REJECTED) from None
        self._trials = job_dir
        job.on_trial_started(self._capture)

    @override
    async def on_job_end(self, job_result: JobResult) -> None:
        del job_result

    async def _capture(self, event: TrialHookEvent) -> None:
        try:
            name, data = encode(self._witness(event))
            put_once(self._archive, name, data, pin=self._pin)
        except Exception:  # noqa: BLE001
            self._warnings.print(WARNING, markup=False, highlight=False)

    def _witness(self, event: TrialHookEvent) -> Witness:
        job, trials = self._job, self._trials
        if event.event is not TrialEvent.START or job is None or trials is None:
            raise Skipped("not an enrolled start")
        config, trial = event.config, str(event.result.id)
        document = event.result.model_dump(mode="json", include=RESULT_FIELDS)
        slot = _slot(config.trial_name)
        if (
            document["finished_at"] is not None
            or config.job_id != job
            or Path(config.trials_dir).resolve() != trials
            or document["id"] != trial
            or str(event.trial_id) != trial
            or document["trial_name"] != config.trial_name
            or document["config"] != config.model_dump(mode="json")
        ):
            raise Skipped("start disagrees")
        _, saved = _load(trials / config.trial_name / LOCK)
        if saved != event.lock.model_dump(mode="json", exclude_none=True):
            raise Skipped("saved lock disagrees")
        key = cell(saved)
        bound = binding(document)
        if bound is None or declared_view(document) != key.view:
            raise Skipped("declared input disagrees")
        return Witness(
            job=str(job), slot=slot, trial=trial, input=key.key, binding=bound
        )
