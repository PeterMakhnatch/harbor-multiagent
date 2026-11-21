"""Core orchestrator for uploading job results to Supabase."""

import asyncio
import errno
import hashlib
import json
import logging
import os
import stat
import tarfile
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from harbor.db.types import PublicJobVisibility
from harbor.models.job.config import JobConfig
from harbor.models.job.lock import TrialLock
from harbor.models.job.result import JobResult
from harbor.models.trial.result import TrialResult
from harbor.upload.db_client import OwnerOrgError, UploadDB
from harbor.upload.storage import UploadStorage

logger = logging.getLogger(__name__)
_DIGEST_PREFIX = "sha256:"


class TrialUploadResult(BaseModel):
    trial_name: str
    task_name: str
    reward: float | int | None = None
    archive_size_bytes: int = 0
    upload_time_sec: float = 0.0
    skipped: bool = False
    error: str | None = None


class JobStartResult(BaseModel):
    """Result of :meth:`Uploader.start_job`.

    Carries the caches the streaming-upload hook handler needs for each
    per-trial upload (so it doesn't re-upsert agent/model per trial) and a
    sentinel indicating whether the row existed server-side already.
    """

    job_id: UUID
    visibility: PublicJobVisibility
    already_existed: bool
    # The organization that owns the job: {id, name, display_name, kind}.
    # None when the Hub predates org ownership (legacy no-org_id upload).
    owner_org: dict[str, Any] | None = None
    shared_orgs: list[str] = []
    shared_users: list[str] = []
    # agent_cache: (name, version) -> agent_id
    agent_cache: dict[tuple[str, str], str] = {}
    # model_cache: (name, provider) -> model_id. Provider is None when the
    # user ran with a bare model name (no "<provider>/" prefix) — the cache
    # key has to preserve that distinction so a user running both
    # `-m gpt-5.4` and `-m openai/gpt-5.4` in the same job doesn't collide
    # into one cache entry.
    model_cache: dict[tuple[str, str | None], str] = {}


class JobUploadResult(BaseModel):
    job_name: str
    job_id: str
    visibility: PublicJobVisibility
    owner_org: dict[str, Any] | None = None
    shared_orgs: list[str] = []
    shared_users: list[str] = []
    job_already_existed: bool = False
    n_trials_uploaded: int = 0
    n_trials_skipped: int = 0
    n_trials_failed: int = 0
    total_time_sec: float = 0.0
    trial_results: list[TrialUploadResult] = []


# Allowlist of entries included in each archive. Everything else in the
# directory is intentionally left out — random scratch files, editor
# metadata, or secrets a user happens to drop in the job_dir don't leak into
# shared artifacts. Missing optional entries are skipped; lock.json is required.
#
# Per-trial archive: self-contained trial subdir. Consumed by the viewer UI
# (artifact browser) and by `harbor trial download`.
_TRIAL_ARCHIVE_INCLUDES: tuple[str, ...] = (
    "config.json",
    "lock.json",
    "result.json",
    "analysis.md",
    "agent",
    "user-agent",
    "user-agent-inputs",
    "verifier",
    "artifacts",
    "trial.log",
    "exception.txt",
)

# Per-step archive entries under ``steps/{step_name}/``. Multi-step trials
# may leave other task/setup files under the step tree; only runtime outputs
# should be uploaded as trial artifacts.
_STEP_ARCHIVE_INCLUDES: tuple[str, ...] = (
    "agent",
    "verifier",
    "artifacts",
)

# Per-job archive: job-level allowlist + every trial subdir, each filtered
# through `_TRIAL_ARCHIVE_INCLUDES`. Consumed by `harbor job download`.
_JOB_ARCHIVE_INCLUDES: tuple[str, ...] = (
    "config.json",
    "lock.json",
    "result.json",
    "analysis.md",
    "job.log",
)
_RETRY_ATTEMPT_PREFIX = "retry-attempt-"
_RETRY_ATTEMPT_SUFFIX = ".json"
_SEAT_ARCHIVE_SUFFIX = "-seats-v1.tar.gz"
_SEAT_LOG_COPY_CHUNK_BYTES = 1024 * 1024

type _RetryAttemptSnapshot = tuple[tuple[str, bytes], ...]
type _SeatLogSnapshot = dict[str, tuple[Path, int]]
type _JobSeatLogSnapshot = dict[str, _SeatLogSnapshot]


class Uploader:
    def __init__(self) -> None:
        self.db = UploadDB()
        self.storage = UploadStorage()
        self._agent_cache_lock = asyncio.Lock()
        self._model_cache_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Streaming primitives
    # ------------------------------------------------------------------
    #
    # `upload_job` below composes these three for the standalone CLI.
    # `harbor run --upload` uses them individually:
    #   1. `start_job(...)` at run start — inserts the job row with
    #      `archive_path=NULL` and primes the agent/model caches.
    #   2. `upload_single_trial(...)` registered as a `TrialEvent.END` hook —
    #      runs once per trial as they finish, in parallel with the next
    #      trial's work.
    #   3. `finalize_job(...)` at run end — builds the job archive, uploads
    #      it, writes `archive_path` + `finished_at` onto the row.
    # A crash between 1 and 3 leaves the row in "in-progress" state
    # (archive_path NULL); `harbor upload <job_dir>` later is an idempotent
    # sweep that fills any missing trials and finalizes if needed.

    async def start_job(
        self,
        *,
        job_id: UUID,
        job_name: str,
        started_at: datetime,
        config: dict[str, Any],
        visibility: PublicJobVisibility | None = None,
        org: str | None = None,
        share_orgs: list[str] | None = None,
        share_users: list[str] | None = None,
        confirm_non_member_orgs: bool = False,
        n_planned_trials: int | None = None,
    ) -> JobStartResult:
        """Insert the job row (or detect an existing one) from an explicit
        spec — no disk reads.

        The spec-based signature lets `harbor run --upload` call this before
        the run produces any ``result.json`` files. The standalone
        ``upload_job`` path reads ``result.json`` + ``config.json`` itself
        and then calls this with the extracted fields.

        ``archive_path`` / ``finished_at`` / ``log_path`` are left ``NULL`` —
        :meth:`finalize_job` writes them at end-of-run. The returned caches
        start empty; :meth:`_upload_single_trial` lazily upserts agents +
        models as it sees them.

        Visibility rules:
        - ``None`` + new row → default ``"private"``
        - ``None`` + existing row → keep server value
        - explicit + existing row → ``update_job_visibility`` if different

        Ownership rules, mirroring the visibility
        tri-state so idempotent re-uploads stay friendly:
        - new row → own it with ``org`` (or the caller's personal org when
          ``org`` is ``None``). Raises :class:`OwnerOrgError` up-front when the
          caller has no personal org (unclaimed username) — this doubles as
          the preflight before any trials stream on the ``run``/``resume``
          paths.
        - existing row + ``org`` is ``None`` → keep the current owner.
        - existing row + explicit ``org`` that differs from the current owner →
          :class:`OwnerOrgError` (ownership changes go through transfer/copy).
        """
        await self.db.get_user_id()  # auth check

        existing_visibility = await self.db.get_job_visibility(job_id)
        already_existed = existing_visibility is not None

        owner_org: dict[str, Any] | None
        if not already_existed:
            owner_org = await self.db.resolve_owner_org(org)
            org_id = (
                UUID(str(owner_org["id"]))
                if owner_org and owner_org.get("id")
                else None
            )
            effective_visibility: PublicJobVisibility = visibility or "private"
            await self.db.insert_job(
                id=job_id,
                job_name=job_name,
                started_at=started_at,
                finished_at=None,  # filled by finalize_job
                config=config,
                log_path=None,  # filled by finalize_job
                archive_path=None,  # filled by finalize_job
                visibility=effective_visibility,
                n_planned_trials=n_planned_trials,
                org_id=org_id,
            )
        else:
            assert existing_visibility is not None
            # Ownership is never re-assigned on re-upload. Only guard against an
            # explicit, conflicting --org; a defaulted (org=None) re-upload
            # keeps the current owner untouched.
            existing_org = await self.db.get_job_owner_org(job_id)
            if org is not None:
                requested_org = await self.db.resolve_owner_org(org)
                if (
                    requested_org is not None
                    and existing_org is not None
                    and existing_org.get("id")
                    and str(existing_org["id"]) != str(requested_org["id"])
                ):
                    label = (
                        existing_org.get("name")
                        or existing_org.get("display_name")
                        or existing_org["id"]
                    )
                    raise OwnerOrgError(
                        f"Job {job_id} is already owned by '{label}'; ownership "
                        "can't be changed on re-upload (use transfer or copy)."
                    )
            owner_org = existing_org
            if visibility is not None and visibility != existing_visibility:
                await self.db.update_job_visibility(job_id, visibility)
                effective_visibility = visibility
                logger.debug(
                    "Job %s already existed; flipped visibility %s → %s.",
                    job_id,
                    existing_visibility,
                    visibility,
                )
            else:
                effective_visibility = existing_visibility

        shared_orgs: list[str] = []
        shared_users: list[str] = []
        if share_orgs or share_users:
            shares = await self.db.add_job_shares(
                job_id=job_id,
                org_names=share_orgs or [],
                usernames=share_users or [],
                confirm_non_member_orgs=confirm_non_member_orgs,
            )
            shared_orgs = [
                str(org.get("name"))
                for org in shares.get("orgs", [])
                if isinstance(org, dict) and org.get("name")
            ]
            shared_users = [
                str(user.get("github_username") or user.get("id"))
                for user in shares.get("users", [])
                if isinstance(user, dict)
                and (user.get("github_username") or user.get("id"))
            ]

        return JobStartResult(
            job_id=job_id,
            visibility=effective_visibility,
            already_existed=already_existed,
            owner_org=owner_org,
            shared_orgs=shared_orgs,
            shared_users=shared_users,
            agent_cache={},
            model_cache={},
        )

    async def upload_single_trial(
        self,
        *,
        trial_result: TrialResult,
        trial_dir: Path,
        trial_lock: TrialLock,
        job_id: UUID,
        agent_cache: dict[tuple[str, str], str],
        model_cache: dict[tuple[str, str | None], str],
    ) -> TrialUploadResult:
        """Upload one trial's archive + trajectory and insert its DB rows.

        Public API so `harbor run --upload`'s `on_trial_ended` hook can call
        it directly. Idempotent: returns ``skipped=True`` if the trial row
        already exists server-side (another hook fired, or a previous
        streaming run already persisted this trial).
        """
        return await self._upload_single_trial(
            trial_result=trial_result,
            trial_dir=trial_dir,
            trial_lock=trial_lock,
            job_id=job_id,
            agent_cache=agent_cache,
            model_cache=model_cache,
        )

    async def finalize_job(
        self,
        job_dir: Path,
        *,
        job_result: JobResult,
        job_config: JobConfig,
        revision: str | None = None,
    ) -> None:
        """Build + upload the job archive and write the completion fields.

        Called once at end-of-run (streaming) or after all per-trial uploads
        complete (batch `upload_job`). Idempotency at this level is the
        caller's responsibility — the idempotent-sweep in :meth:`upload_job`
        calls this for incomplete jobs or when new attempts were uploaded.
        A revision preserves immutable storage objects on re-upload.
        """
        remote = await self.db.get_job(job_result.id)
        await self._finalize_job_archive(
            job_dir,
            job_result=job_result,
            revision=revision,
            existing_archive_path=remote.get("archive_path") if remote else None,
            skip_existing_archive=False,
        )

    async def _finalize_job_archive(
        self,
        job_dir: Path,
        *,
        job_result: JobResult,
        revision: str | None,
        existing_archive_path: str | None,
        skip_existing_archive: bool,
    ) -> None:
        retry_attempts = _snapshot_retry_attempts(job_dir)
        with TemporaryDirectory() as staging_dir:
            seat_logs = _snapshot_job_seat_logs(job_dir, Path(staging_dir))
            archive_name = _job_archive_name(
                revision, retry_attempts, has_seat_logs=bool(seat_logs)
            )
            archive_path = f"jobs/{job_result.id}/{archive_name}"
            if (skip_existing_archive and existing_archive_path == archive_path) or (
                not seat_logs
                and existing_archive_path is not None
                and existing_archive_path.endswith(_SEAT_ARCHIVE_SUFFIX)
            ):
                return

            log_path: str | None = None
            job_log = job_dir / "job.log"
            if job_log.exists():
                log_path = f"jobs/{job_result.id}/job.log"
                await self.storage.upload_large_file(
                    job_log,
                    log_path,
                    content_type="text/plain",
                )

            local_archive_path = job_dir / ".harbor-upload" / archive_name
            upload_url_path = local_archive_path.with_suffix(
                local_archive_path.suffix + ".tus-url"
            )
            if not (local_archive_path.exists() and upload_url_path.exists()):
                upload_url_path.unlink(missing_ok=True)
                _create_job_archive_file(
                    job_dir,
                    local_archive_path,
                    retry_attempts=retry_attempts,
                    seat_logs=seat_logs,
                )

        await self.storage.upload_resumable_file(local_archive_path, archive_path)

        await self.db.finalize_job(
            job_result.id,
            archive_path=archive_path,
            log_path=log_path,
            finished_at=job_result.finished_at or datetime.now(),
        )
        local_archive_path.unlink(missing_ok=True)
        upload_url_path.unlink(missing_ok=True)
        try:
            local_archive_path.parent.rmdir()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Top-level orchestrator (idempotent sweep)
    # ------------------------------------------------------------------

    async def upload_job(
        self,
        job_dir: Path,
        *,
        visibility: PublicJobVisibility | None = None,
        org: str | None = None,
        share_orgs: list[str] | None = None,
        share_users: list[str] | None = None,
        confirm_non_member_orgs: bool = False,
        max_concurrency: int = 10,
        on_trial_start: Callable[[TrialResult], None] | None = None,
        on_trial_complete: Callable[[TrialResult, TrialUploadResult], None]
        | None = None,
    ) -> JobUploadResult:
        """Upload a ``job_dir`` to Supabase — idempotent end-to-end.

        This is the public entry point for both:

        * **First-time upload** (standalone ``harbor upload``) — inserts the
          row, uploads every trial, uploads the job archive, finalizes.
        * **Resume after crash / streaming finalize** — detects existing
          server state, uploads only trials whose archive path is still null,
          finalizes only if the row's ``archive_path`` is still ``NULL``.

        Re-running always converges to "complete upload."

        Visibility semantics (unchanged):
        * ``visibility=None`` → new jobs default to private; existing untouched.
        * ``visibility="public"``/``"private"`` → always applied, updated if
          the existing row's value differs.
        """
        t0 = time.monotonic()

        job_result, job_config, trial_results, trial_dirs = _load_job_from_disk(job_dir)

        start = await self.start_job(
            job_id=job_result.id,
            job_name=job_config.job_name,
            started_at=job_result.started_at,
            config=job_config.model_dump(mode="json"),
            visibility=visibility,
            org=org,
            share_orgs=share_orgs,
            share_users=share_users,
            confirm_non_member_orgs=confirm_non_member_orgs,
            n_planned_trials=job_result.n_total_trials,
        )

        # Eagerly upsert agents + models on the batch path. Streaming relies
        # on the per-trial lazy upsert in `_upload_single_trial`; here we
        # know the full trial set up front so this saves N round-trips on
        # the hot path.
        agent_cache, model_cache = await self._upsert_dimensions(trial_results)
        start.agent_cache.update(agent_cache)
        start.model_cache.update(model_cache)

        # Resume path: a trial row is complete only once its archive path has
        # been finalized. Rows with a NULL archive path are upload reservations
        # left by an interrupted attempt and must be resumed.
        completed_archives: dict[UUID, str] = {}
        if start.already_existed:
            remote_trials = await self.db.list_trials_for_job(start.job_id)
            completed_archives = {
                UUID(row["id"]): row["archive_path"]
                for row in remote_trials
                if row.get("archive_path") is not None
            }

        # Concurrent per-trial uploads. The `_upload_single_trial` body
        # re-checks the archive path and uses conflict-safe inserts, so the
        # list-then-upload gap is purely an optimization.
        sem = asyncio.Semaphore(max_concurrency)

        async def _upload_trial(tr: TrialResult) -> TrialUploadResult:
            async with sem:
                if on_trial_start:
                    on_trial_start(tr)
                try:
                    trial_dir = trial_dirs.get(tr.id)
                    if trial_dir is None:
                        raise FileNotFoundError(
                            f"Trial directory is required for upload: {tr.trial_name}"
                        )
                    result = await self._upload_single_trial(
                        trial_result=tr,
                        trial_dir=trial_dir,
                        trial_lock=None,
                        job_id=start.job_id,
                        agent_cache=start.agent_cache,
                        model_cache=start.model_cache,
                        listed_archive_path=completed_archives.get(tr.id),
                        job_dir=job_dir,
                    )
                except Exception as exc:
                    logger.debug(
                        "Failed to prepare trial %s for upload: %s",
                        tr.trial_name,
                        exc,
                    )
                    result = _trial_upload_error_result(tr, exc)
                if on_trial_complete:
                    on_trial_complete(tr, result)
                return result

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_upload_trial(tr)) for tr in trial_results]

        trial_upload_results = [t.result() for t in tasks]

        remote = await self.db.get_job(start.job_id)
        if remote is not None and not any(
            r.error is not None for r in trial_upload_results
        ):
            revision = hashlib.sha256(
                ",".join(sorted(str(tr.id) for tr in trial_results)).encode()
            ).hexdigest()
            await self._finalize_job_archive(
                job_dir,
                job_result=job_result,
                revision=revision,
                existing_archive_path=remote.get("archive_path"),
                skip_existing_archive=True,
            )

        elapsed = time.monotonic() - t0
        return JobUploadResult(
            job_name=job_config.job_name,
            job_id=str(start.job_id),
            visibility=start.visibility,
            owner_org=start.owner_org,
            shared_orgs=start.shared_orgs,
            shared_users=start.shared_users,
            job_already_existed=start.already_existed,
            n_trials_uploaded=sum(
                1 for r in trial_upload_results if not r.skipped and r.error is None
            ),
            n_trials_skipped=sum(1 for r in trial_upload_results if r.skipped),
            n_trials_failed=sum(1 for r in trial_upload_results if r.error is not None),
            total_time_sec=elapsed,
            trial_results=trial_upload_results,
        )

    async def _upsert_dimensions(
        self, trial_results: list[TrialResult]
    ) -> tuple[
        dict[tuple[str, str], str],
        dict[tuple[str, str | None], str],
    ]:
        """Eagerly upsert every agent + model referenced by these trials.

        Returns caches keyed by identity tuples so the per-trial upload path
        can skip the lookup on the hot path. Model cache key preserves
        ``provider=None`` as a distinct identity (see ``JobStartResult``).
        """
        agent_cache: dict[tuple[str, str], str] = {}
        model_cache: dict[tuple[str, str | None], str] = {}
        for tr in trial_results:
            agent_key = (tr.agent_info.name, tr.agent_info.version)
            if agent_key not in agent_cache:
                agent_cache[agent_key] = await self.db.upsert_agent(
                    tr.agent_info.name, tr.agent_info.version
                )
            if tr.agent_info.model_info is not None:
                model_key = (
                    tr.agent_info.model_info.name,
                    tr.agent_info.model_info.provider,
                )
                if model_key not in model_cache:
                    model_cache[model_key] = await self.db.upsert_model(
                        tr.agent_info.model_info.name,
                        tr.agent_info.model_info.provider,
                    )
        return agent_cache, model_cache

    async def _upload_single_trial(
        self,
        *,
        trial_result: TrialResult,
        trial_dir: Path,
        trial_lock: TrialLock | None,
        job_id: UUID,
        agent_cache: dict[tuple[str, str], str],
        model_cache: dict[tuple[str, str | None], str],
        listed_archive_path: str | None = None,
        job_dir: Path | None = None,
    ) -> TrialUploadResult:
        primary_reward = _extract_primary_reward(trial_result)

        try:
            t0 = time.monotonic()
            with TemporaryDirectory() as staging_dir:
                seat_logs = _snapshot_trial_seat_logs(
                    trial_dir, Path(staging_dir) / "trial", job_dir=job_dir
                )
                archive_name = _trial_archive_name(bool(seat_logs))
                archive_path = f"trials/{trial_result.id}/{archive_name}"
                existing_archive_path = listed_archive_path
                if not _trial_archive_is_current(
                    existing_archive_path, archive_path, has_seat_logs=bool(seat_logs)
                ):
                    remote = await self.db.get_trial(trial_result.id)
                    if remote is not None and remote.get("archive_path") is not None:
                        existing_archive_path = remote["archive_path"]
                if _trial_archive_is_current(
                    existing_archive_path, archive_path, has_seat_logs=bool(seat_logs)
                ):
                    return TrialUploadResult(
                        trial_name=trial_result.trial_name,
                        task_name=trial_result.task_name,
                        reward=primary_reward,
                        skipped=True,
                    )

                if existing_archive_path is None and trial_lock is None:
                    trial_lock = _read_trial_lock_for_upload(trial_dir)
                local_archive_path = trial_dir / ".harbor-upload" / archive_name
                upload_url_path = local_archive_path.with_suffix(
                    local_archive_path.suffix + ".tus-url"
                )
                if not (local_archive_path.exists() and upload_url_path.exists()):
                    upload_url_path.unlink(missing_ok=True)
                    _create_trial_archive_file(
                        trial_dir, local_archive_path, seat_logs=seat_logs
                    )
                archive_size = local_archive_path.stat().st_size

            if existing_archive_path is not None:
                await self.storage.upload_resumable_file(
                    local_archive_path, archive_path
                )
                await self.db.finalize_trial_archive_migration(
                    trial_result.id, archive_path=archive_path
                )
                return _complete_trial_upload(
                    trial_result, primary_reward, archive_size, t0, local_archive_path
                )

            if trial_lock is None:
                raise ValueError("Trial lock is required for upload")
            trajectory_path: str | None = None
            # Upsert the agent if we haven't seen this (name, version) pair
            # yet. This lazy path matters for the streaming flow — hooks see
            # trials one at a time and the cache is initially empty; the
            # batch flow pre-populates the cache via _upsert_dimensions.
            agent_key = (
                trial_result.agent_info.name,
                trial_result.agent_info.version,
            )
            async with self._agent_cache_lock:
                if agent_key not in agent_cache:
                    agent_cache[agent_key] = await self.db.upsert_agent(
                        trial_result.agent_info.name, trial_result.agent_info.version
                    )
                agent_id = agent_cache[agent_key]

            model_id: str | None = None
            if trial_result.agent_info.model_info is not None:
                model_key = (
                    trial_result.agent_info.model_info.name,
                    trial_result.agent_info.model_info.provider,
                )
                async with self._model_cache_lock:
                    if model_key not in model_cache:
                        model_cache[model_key] = await self.db.upsert_model(
                            trial_result.agent_info.model_info.name,
                            trial_result.agent_info.model_info.provider,
                        )
                    model_id = model_cache[model_key]

            # Reserve the trial before writing Storage. Conflict-safe inserts
            # let an interrupted or concurrent attempt continue without
            # overwriting the existing metadata. Artifact paths stay NULL until
            # every required upload succeeds.
            await self.db.insert_trial(
                id=trial_result.id,
                trial_name=trial_result.trial_name,
                task_name=trial_result.task_name,
                task_content_hash=trial_lock.task.digest.removeprefix(_DIGEST_PREFIX),
                lock=trial_lock.model_dump(mode="json", exclude_none=True),
                job_id=job_id,
                agent_id=agent_id,
                started_at=trial_result.started_at,
                finished_at=trial_result.finished_at,
                config=trial_result.config.model_dump(mode="json"),
                rewards=(
                    trial_result.verifier_result.rewards
                    if trial_result.reward_valid and trial_result.verifier_result
                    else None
                ),
                exception_type=(
                    trial_result.exception_info.exception_type
                    if trial_result.exception_info
                    else None
                ),
                environment_setup_started_at=_timing_field(
                    trial_result.environment_setup, "started_at"
                ),
                environment_setup_finished_at=_timing_field(
                    trial_result.environment_setup, "finished_at"
                ),
                agent_setup_started_at=_timing_field(
                    trial_result.agent_setup, "started_at"
                ),
                agent_setup_finished_at=_timing_field(
                    trial_result.agent_setup, "finished_at"
                ),
                agent_execution_started_at=_timing_field(
                    trial_result.agent_execution, "started_at"
                ),
                agent_execution_finished_at=_timing_field(
                    trial_result.agent_execution, "finished_at"
                ),
                verifier_started_at=_timing_field(trial_result.verifier, "started_at"),
                verifier_finished_at=_timing_field(
                    trial_result.verifier, "finished_at"
                ),
            )

            if model_id is not None:
                agent_result = trial_result.agent_result
                await self.db.insert_trial_model(
                    trial_id=trial_result.id,
                    model_id=model_id,
                    n_input_tokens=(
                        agent_result.n_input_tokens if agent_result else None
                    ),
                    n_cache_tokens=(
                        agent_result.n_cache_tokens if agent_result else None
                    ),
                    n_output_tokens=(
                        agent_result.n_output_tokens if agent_result else None
                    ),
                    cost_usd=agent_result.cost_usd if agent_result else None,
                )

            # Now that the row exists (authorizing the storage write), upload
            # the archive. A failure here leaves archive_path null on the row,
            # so the resume sweep re-runs this trial.
            await self.storage.upload_resumable_file(local_archive_path, archive_path)

            # Upload the canonical trajectory separately for direct access.
            traj_file = trial_dir / "agent" / "trajectory.json"
            if traj_file.exists():
                direct_trajectory_path = f"trials/{trial_result.id}/trajectory.json"
                trajectory_upload_url_path = traj_file.with_suffix(
                    traj_file.suffix + ".tus-url"
                )
                try:
                    await self.storage.upload_resumable_file(
                        traj_file,
                        direct_trajectory_path,
                    )
                except Exception as exc:
                    # The archive already contains agent/trajectory.json, so a
                    # failed direct upload only removes the convenience path.
                    logger.debug(
                        "Failed to upload direct trajectory for trial %s; "
                        "continuing with archive only: %s: %s",
                        trial_result.trial_name,
                        type(exc).__name__,
                        exc,
                    )
                else:
                    trajectory_path = direct_trajectory_path
                finally:
                    # A failed optional upload is not resumed after the required
                    # archive path is finalized.
                    trajectory_upload_url_path.unlink(missing_ok=True)

            # Commit — publish the storage paths onto the row. Writing
            # archive_path is what flips the trial to "finalized"; a crash
            # before this point is repaired on the next upload sweep.
            await self.db.finalize_trial_artifacts(
                trial_result.id,
                archive_path=archive_path,
                trajectory_path=trajectory_path,
            )

            return _complete_trial_upload(
                trial_result, primary_reward, archive_size, t0, local_archive_path
            )

        except Exception as exc:
            logger.debug("Failed to upload trial %s: %s", trial_result.trial_name, exc)
            return _trial_upload_error_result(trial_result, exc, reward=primary_reward)


def _load_job_from_disk(
    job_dir: Path,
) -> tuple[JobResult, JobConfig, list[TrialResult], dict[UUID, Path]]:
    """Load the job_dir's result.json + config.json + every trial's result.json.

    Returns the parent result/config plus a parallel list of child trial
    results and an ID→path map the uploader uses to find each trial's
    on-disk subdir. Used by both ``start_job`` and ``upload_job`` — kept as
    a single helper so both paths load the same trial set with the same
    ordering and detection rules.
    """
    job_result = JobResult.model_validate_json((job_dir / "result.json").read_text())
    job_config = JobConfig.model_validate_json((job_dir / "config.json").read_text())
    trial_dirs: dict[UUID, Path] = {}
    trial_results: list[TrialResult] = []
    for child in sorted(job_dir.iterdir()):
        if child.is_dir() and (child / "result.json").exists():
            result = TrialResult.model_validate_json(
                (child / "result.json").read_text()
            )
            trial_dirs[result.id] = child
            trial_results.append(result)
    return job_result, job_config, trial_results, trial_dirs


def _timing_field(timing: object | None, field: str) -> datetime | None:
    if timing is None:
        return None
    value = getattr(timing, field, None)
    if isinstance(value, datetime):
        return value
    return None


def _extract_primary_reward(trial_result: TrialResult) -> float | int | None:
    if (
        trial_result.reward_valid
        and trial_result.verifier_result is not None
        and trial_result.verifier_result.rewards
    ):
        rewards = trial_result.verifier_result.rewards
        if "reward" in rewards:
            return rewards["reward"]
        if len(rewards) == 1:
            return next(iter(rewards.values()))
    return None


def _trial_upload_error_result(
    trial_result: TrialResult,
    exc: Exception,
    *,
    reward: float | int | None = None,
) -> TrialUploadResult:
    if reward is None:
        reward = _extract_primary_reward(trial_result)
    return TrialUploadResult(
        trial_name=trial_result.trial_name,
        task_name=trial_result.task_name,
        reward=reward,
        error=f"{type(exc).__name__}: {exc}",
    )


def _complete_trial_upload(
    trial_result: TrialResult,
    reward: float | int | None,
    archive_size: int,
    started_at: float,
    local_archive_path: Path,
) -> TrialUploadResult:
    local_archive_path.unlink(missing_ok=True)
    upload_url_path = local_archive_path.with_suffix(
        local_archive_path.suffix + ".tus-url"
    )
    upload_url_path.unlink(missing_ok=True)
    try:
        local_archive_path.parent.rmdir()
    except OSError:
        pass
    return TrialUploadResult(
        trial_name=trial_result.trial_name,
        task_name=trial_result.task_name,
        reward=reward,
        archive_size_bytes=archive_size,
        upload_time_sec=time.monotonic() - started_at,
    )


def _read_trial_lock_for_upload(trial_dir: Path) -> TrialLock:
    lock_path = trial_dir / "lock.json"
    if not lock_path.exists():
        raise FileNotFoundError(f"Trial lock file is required for upload: {lock_path}")
    return TrialLock.model_validate_json(lock_path.read_text())


@contextmanager
def _open_seat_directory(
    name: str | Path, *, dir_fd: int | None = None
) -> Iterator[int | None]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EMLINK):
            raise
        yield None
        return
    try:
        yield fd if stat.S_ISDIR(os.fstat(fd).st_mode) else None
    finally:
        os.close(fd)


def _seat_file_mode(dir_fd: int, name: str) -> int:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return 0


def _open_seat_file_fd(dir_fd: int, name: str) -> int | None:
    if not stat.S_ISREG(_seat_file_mode(dir_fd, name)):
        return None
    try:
        return os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
            dir_fd=dir_fd,
        )
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ELOOP, errno.EMLINK):
            return None
        if exc.errno in (errno.EISDIR, errno.ENXIO, errno.ENODEV) and not stat.S_ISREG(
            _seat_file_mode(dir_fd, name)
        ):
            return None
        raise


@contextmanager
def _open_seat_file(dir_fd: int, name: str) -> Iterator[tuple[int, int] | None]:
    fd = _open_seat_file_fd(dir_fd, name)
    if fd is None:
        yield None
        return
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            yield None
            return
        yield fd, metadata.st_size
    finally:
        os.close(fd)


def _declared_seats(trial_fd: int) -> set[str]:
    with _open_seat_file(trial_fd, "config.json") as config_file:
        if config_file is None:
            return set()
        try:
            with os.fdopen(config_file[0], "rb", closefd=False) as source:
                config = json.load(source)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return set()
    participants = config.get("participants") if isinstance(config, dict) else None
    if not isinstance(participants, list):
        return set()
    return {
        seat
        for participant in participants
        if isinstance(participant, dict)
        and isinstance(seat := participant.get("seat"), str)
        and seat
        and seat not in (".", "..")
        and not any(character in seat for character in "/\\\0")
    }


def _capture_seat_file(
    dir_fd: int,
    name: str,
    relative_name: str,
    staging_dir: Path,
    logs: _SeatLogSnapshot,
) -> None:
    with _open_seat_file(dir_fd, name) as opened:
        if opened is None:
            return
        fd, size = opened
        if not logs:
            staging_dir.mkdir()
        staged = staging_dir / str(len(logs))
        with os.fdopen(fd, "rb", closefd=False) as source, staged.open("xb") as sink:
            for offset in range(0, size, _SEAT_LOG_COPY_CHUNK_BYTES):
                chunk_size = min(_SEAT_LOG_COPY_CHUNK_BYTES, size - offset)
                data = source.read(chunk_size)
                if len(data) != chunk_size:
                    raise OSError(f"Seat log ended during capture: {relative_name}")
                sink.write(data)
    logs[relative_name] = (staged, size)


def _capture_seat_agent_logs(
    agent_fd: int, prefix: str, staging_dir: Path, logs: _SeatLogSnapshot
) -> None:
    with ExitStack() as root:
        stack = [(agent_fd, prefix, root.enter_context(os.scandir(agent_fd)), root)]
        try:
            while stack:
                dir_fd, parent, entries, scope = stack[-1]
                entry = next(entries, None)
                if entry is None:
                    stack.pop()
                    scope.close()
                    continue
                relative_name = f"{parent}/{entry.name}"
                if entry.is_dir(follow_symlinks=False):
                    with ExitStack() as child:
                        child_fd = child.enter_context(
                            _open_seat_directory(entry.name, dir_fd=dir_fd)
                        )
                        if child_fd is not None:
                            child_entries = child.enter_context(os.scandir(child_fd))
                            stack.append(
                                (
                                    child_fd,
                                    relative_name,
                                    child_entries,
                                    child.pop_all(),
                                )
                            )
                elif entry.is_file(follow_symlinks=False):
                    _capture_seat_file(
                        dir_fd, entry.name, relative_name, staging_dir, logs
                    )
        finally:
            for _, _, _, scope in reversed(stack):
                scope.close()


def _snapshot_seat_logs_at(trial_fd: int, staging_dir: Path) -> _SeatLogSnapshot:
    logs: _SeatLogSnapshot = {}
    with _open_seat_directory("seats", dir_fd=trial_fd) as seats_fd:
        if seats_fd is None:
            return logs
        for seat in sorted(_declared_seats(trial_fd)):
            with _open_seat_directory(seat, dir_fd=seats_fd) as seat_fd:
                if seat_fd is None:
                    continue
                with _open_seat_directory("agent", dir_fd=seat_fd) as agent_fd:
                    if agent_fd is not None:
                        _capture_seat_agent_logs(
                            agent_fd, f"seats/{seat}/agent", staging_dir, logs
                        )
    return logs


def _snapshot_trial_seat_logs(
    trial_dir: Path, staging_dir: Path, *, job_dir: Path | None = None
) -> _SeatLogSnapshot:
    with ExitStack() as scope:
        job_fd = None
        trial_name: str | Path = trial_dir
        if job_dir is not None:
            job_fd = scope.enter_context(_open_seat_directory(job_dir))
            if job_fd is None:
                return {}
            trial_name = trial_dir.name
        trial_fd = scope.enter_context(_open_seat_directory(trial_name, dir_fd=job_fd))
        if trial_fd is None:
            return {}
        return _snapshot_seat_logs_at(trial_fd, staging_dir)


def _snapshot_job_seat_logs(job_dir: Path, staging_dir: Path) -> _JobSeatLogSnapshot:
    snapshots: _JobSeatLogSnapshot = {}
    with _open_seat_directory(job_dir) as job_fd:
        if job_fd is None:
            return snapshots
        with os.scandir(job_fd) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                with _open_seat_directory(entry.name, dir_fd=job_fd) as trial_fd:
                    if trial_fd is None or not stat.S_ISREG(
                        _seat_file_mode(trial_fd, "result.json")
                    ):
                        continue
                    logs = _snapshot_seat_logs_at(trial_fd, staging_dir / entry.name)
                    if logs:
                        snapshots[entry.name] = logs
    return snapshots


def _trial_archive_name(has_seat_logs: bool) -> str:
    return f"trial{_SEAT_ARCHIVE_SUFFIX}" if has_seat_logs else "trial.tar.gz"


def _trial_archive_is_current(
    existing_archive_path: str | None, archive_path: str, *, has_seat_logs: bool
) -> bool:
    return existing_archive_path is not None and (
        not has_seat_logs or existing_archive_path == archive_path
    )


def _create_trial_archive(trial_dir: Path) -> bytes:
    buf = BytesIO()
    with TemporaryDirectory() as staging_dir:
        seat_logs = _snapshot_trial_seat_logs(trial_dir, Path(staging_dir) / "trial")
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            _add_trial_archive_entries(tar, trial_dir, seat_logs=seat_logs)
    return buf.getvalue()


def _create_trial_archive_file(
    trial_dir: Path, archive_path: Path, *, seat_logs: _SeatLogSnapshot | None = None
) -> None:
    """Create a trial archive on disk for resumable upload."""
    with ExitStack() as scope:
        if seat_logs is None:
            staging_dir = Path(scope.enter_context(TemporaryDirectory()))
            seat_logs = _snapshot_trial_seat_logs(trial_dir, staging_dir / "trial")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, mode="w:gz") as tar:
            _add_trial_archive_entries(tar, trial_dir, seat_logs=seat_logs)


def _add_trial_archive_entries(
    tar: tarfile.TarFile,
    trial_dir: Path,
    *,
    seat_logs: _SeatLogSnapshot | None,
    arcname_prefix: str = "",
) -> None:
    """Add allowlisted trial entries to an open tar archive."""
    if not (trial_dir / "lock.json").exists():
        raise FileNotFoundError(
            f"Trial lock file is required for upload: {trial_dir / 'lock.json'}"
        )

    for name in _TRIAL_ARCHIVE_INCLUDES:
        path = trial_dir / name
        if path.exists():
            tar.add(path, arcname=f"{arcname_prefix}{name}")

    if seat_logs:
        for name, (staged, size) in sorted(seat_logs.items()):
            member = tarfile.TarInfo(f"{arcname_prefix}{name}")
            member.mode = 0o600
            member.size = size
            with staged.open("rb") as source:
                tar.addfile(member, source)

    steps_dir = trial_dir / "steps"
    if not steps_dir.exists():
        return

    for step_dir in sorted(p for p in steps_dir.iterdir() if p.is_dir()):
        for name in _STEP_ARCHIVE_INCLUDES:
            path = step_dir / name
            if path.exists():
                tar.add(
                    path,
                    arcname=f"{arcname_prefix}steps/{step_dir.name}/{name}",
                )


def _is_retry_attempt_name(name: str) -> bool:
    if not (
        name.startswith(_RETRY_ATTEMPT_PREFIX) and name.endswith(_RETRY_ATTEMPT_SUFFIX)
    ):
        return False
    trial_id = name[len(_RETRY_ATTEMPT_PREFIX) : -len(_RETRY_ATTEMPT_SUFFIX)]
    try:
        return str(UUID(trial_id)) == trial_id
    except ValueError:
        return False


def _read_retry_attempt(job_fd: int, name: str) -> bytes | None:
    try:
        mode = os.stat(name, dir_fd=job_fd, follow_symlinks=False).st_mode
        if not stat.S_ISREG(mode):
            return None
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY,
            dir_fd=job_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            return None
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as source:
            return source.read()
    finally:
        os.close(fd)


def _snapshot_retry_attempts(job_dir: Path) -> _RetryAttemptSnapshot:
    job_fd = os.open(job_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with os.scandir(job_fd) as entries:
            names = sorted(
                entry.name for entry in entries if _is_retry_attempt_name(entry.name)
            )
        return tuple(
            (name, data)
            for name in names
            if (data := _read_retry_attempt(job_fd, name)) is not None
        )
    finally:
        os.close(job_fd)


def _job_archive_name(
    revision: str | None,
    retry_attempts: _RetryAttemptSnapshot,
    *,
    has_seat_logs: bool = False,
) -> str:
    if retry_attempts:
        revision_digest = hashlib.sha256((revision or "").encode()).digest()
        digest = hashlib.sha256(b"harbor-job-retry-attempts-v1\0" + revision_digest)
        for name, data in retry_attempts:
            digest.update(name.encode("ascii"))
            digest.update(hashlib.sha256(data).digest())
        revision = digest.hexdigest()
    stem = f"job-{revision}" if revision else "job"
    return stem + (_SEAT_ARCHIVE_SUFFIX if has_seat_logs else ".tar.gz")


def _create_job_archive_file(
    job_dir: Path,
    archive_path: Path,
    *,
    retry_attempts: _RetryAttemptSnapshot | None = None,
    seat_logs: _JobSeatLogSnapshot | None = None,
) -> None:
    """Create the job archive on disk to avoid holding large jobs in memory."""
    if retry_attempts is None:
        retry_attempts = _snapshot_retry_attempts(job_dir)
    with ExitStack() as scope:
        if seat_logs is None:
            staging_dir = Path(scope.enter_context(TemporaryDirectory()))
            seat_logs = _snapshot_job_seat_logs(job_dir, staging_dir)
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, mode="w:gz") as tar:
            _add_job_archive_entries(tar, job_dir, retry_attempts, seat_logs)


def _add_job_archive_entries(
    tar: tarfile.TarFile,
    job_dir: Path,
    retry_attempts: _RetryAttemptSnapshot,
    seat_logs: _JobSeatLogSnapshot,
) -> None:
    root = job_dir.name
    for name in _JOB_ARCHIVE_INCLUDES:
        path = job_dir / name
        if path.exists():
            tar.add(path, arcname=f"{root}/{name}")
    archived_seat_trials = 0
    for child in sorted(job_dir.iterdir()):
        if not (child.is_dir() and (child / "result.json").exists()):
            continue
        trial_seat_logs = seat_logs.get(child.name)
        if trial_seat_logs is not None:
            archived_seat_trials += 1
        _add_trial_archive_entries(
            tar,
            child,
            seat_logs=trial_seat_logs,
            arcname_prefix=f"{root}/{child.name}/",
        )
    if archived_seat_trials != len(seat_logs):
        raise FileNotFoundError(
            f"A trial with captured seat logs vanished from {job_dir}"
        )
    for name, data in retry_attempts:
        member = tarfile.TarInfo(f"{root}/{name}")
        member.mode = 0o600
        member.size = len(data)
        tar.addfile(member, BytesIO(data))
