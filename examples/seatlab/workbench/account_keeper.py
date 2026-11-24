from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, override
from uuid import UUID as UUIDType

from rich.console import Console

from harbor.models.job.plugin import BaseJobPlugin

from .account_contract import _attempt_row
from .attempt_keeper import Skipped, _kind, _open_private
from .native_account import project_result
from .private_receipt import MAX_BYTES, _linkable, put_once
from .seat_stream import UUID, Malformed, Record, _require

if TYPE_CHECKING:
    from harbor.job import Job
    from harbor.models.job.result import JobResult
    from harbor.models.trial.result import SeatResult, TrialResult
    from harbor.trial.hooks import TrialHookEvent

SCHEMA = "seatlab.native-attempt-account/v1"
SUFFIX = ".json"


def _reason(exc: Exception) -> str:
    return str(exc) if isinstance(exc, Malformed) else _kind(exc)


def encode(job_id: str, attempt: Record) -> tuple[str, bytes]:
    _require(
        type(job_id) is str and UUID.fullmatch(job_id) is not None,
        "job id is not a canonical lowercase UUID",
    )
    _require(_attempt_row(attempt) == attempt, "attempt is not a canonical account row")
    name = f"{job_id}.{attempt['slot']}.{attempt['trial']}{SUFFIX}"
    body = {"schema": SCHEMA, "job": job_id, "attempt": attempt}
    data = (
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    _require(len(data) <= MAX_BYTES, "attempt account exceeds 1 MiB")
    return name, data


def store(
    directory: str,
    job_id: str,
    attempt: Record,
    *,
    pin: tuple[int, int] | None = None,
) -> str:
    name, data = encode(job_id, attempt)
    put_once(directory, name, data, pin=pin)
    return name


def write_attempt(
    directory: str,
    document: Record,
    *,
    job_id: str,
    slot_name: str,
    pin: tuple[int, int] | None = None,
) -> str:
    attempt = project_result(document, job_id=job_id, slot_name=slot_name)
    return store(directory, job_id, attempt, pin=pin)


def _seat_document(seat: SeatResult) -> Record:
    info = seat.agent_info
    model = None if info is None else info.model_info
    context = seat.agent_result
    return {
        "seat": seat.seat,
        "under_test": seat.under_test,
        "execution_disposition": seat.execution_disposition,
        "agent_info": None
        if info is None
        else {
            "name": info.name,
            "model_info": None
            if model is None
            else {"name": model.name, "provider": model.provider},
        },
        "agent_result": None
        if context is None
        else {
            "n_input_tokens": context.n_input_tokens,
            "n_cache_tokens": context.n_cache_tokens,
            "n_output_tokens": context.n_output_tokens,
            "cost_usd": context.cost_usd,
        },
    }


def result_document(result: TrialResult) -> Record:
    config = result.config
    verifier = result.verifier_result
    rewards = None if verifier is None else verifier.rewards
    seats = result.seat_results
    return {
        "schema_version": result.schema_version,
        "id": str(result.id),
        "trial_name": result.trial_name,
        "reward_valid": result.reward_valid,
        "verifier_result": None
        if verifier is None
        else {"rewards": None if rewards is None else dict(rewards)},
        "config": {
            "schema_version": config.schema_version,
            "job_id": None if config.job_id is None else str(config.job_id),
            "trial_name": config.trial_name,
            "participants": [{"seat": p.seat} for p in config.participants],
        },
        "seat_results": None if seats is None else [_seat_document(s) for s in seats],
    }


class AccountKeeper(BaseJobPlugin):
    def __init__(self, account_dir: str) -> None:
        super().__init__()
        self._warnings = Console(stderr=True)
        if not isinstance(account_dir, str) or not os.path.isabs(account_dir):
            raise ValueError("AccountKeeper account_dir must be an absolute path.")
        if not _linkable():
            raise ValueError("AccountKeeper needs linkat on this platform.")
        try:
            fd, self._pin = _open_private(account_dir, None)
        except (OSError, Skipped) as exc:
            raise ValueError(
                f"AccountKeeper account_dir rejected ({_kind(exc)})."
            ) from None
        os.close(fd)
        self._dir = account_dir
        self._job: str | None = None

    @override
    async def on_job_start(self, job: Job) -> None:
        job_dir = Path(job.job_dir).resolve()
        kept = Path(self._dir).resolve()
        if kept.is_relative_to(job_dir) or job_dir.is_relative_to(kept):
            raise ValueError(
                "AccountKeeper account_dir must not nest with the job directory."
            )
        try:
            fd, _ = _open_private(self._dir, self._pin)
        except (OSError, Skipped) as exc:
            raise ValueError(
                f"AccountKeeper account_dir rejected ({_kind(exc)})."
            ) from None
        os.close(fd)
        if not isinstance(job.id, UUIDType):
            raise TypeError("AccountKeeper needs a job with a UUID id.")
        if not job.config.participants:
            self._warn("not a multi-seat job")
            return
        self._job = str(job.id)
        job.on_trial_ended(self._capture)

    @override
    async def on_job_end(self, job_result: JobResult) -> None:
        del job_result

    def _warn(self, reason: str) -> None:
        self._warnings.print(
            f"Warning: native attempt account not kept ({reason}).",
            markup=False,
            highlight=False,
        )

    async def _capture(self, event: TrialHookEvent) -> None:
        try:
            self._keep(event)
        except (OSError, ValueError, Skipped, ArithmeticError) as exc:
            self._warn(_reason(exc))

    def _keep(self, event: TrialHookEvent) -> None:
        job_id = self._job
        if job_id is None:
            raise Skipped("job not started")
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
        if event.trial_id != result.id:
            raise Skipped("trial id changed")
        if result.trial_name != config.trial_name:
            raise Skipped("trial result names another directory")
        if result.finished_at is None:
            raise Skipped("attempt not finished")
        if not config.participants and result.seat_results is None:
            raise Skipped("not a multi-seat attempt")
        write_attempt(
            self._dir,
            result_document(result),
            job_id=job_id,
            slot_name=config.trial_name,
            pin=self._pin,
        )
