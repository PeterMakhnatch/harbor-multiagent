import asyncio
import errno
import gzip
import hashlib
import json
import os
import socket
import tarfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.job.lock import VerifierLock, TaskLock, TrialLock
from harbor.models.job.result import JobResult, JobStats
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import (
    AgentInfo,
    ExceptionInfo,
    ModelInfo,
    SeatResult,
    TimingInfo,
    TrialResult,
)
from harbor.models.verifier.result import VerifierResult
from harbor.upload.db_client import OwnerOrgError, UploadDB
from harbor.upload.uploader import (
    TrialUploadResult,
    Uploader,
    _create_job_archive_file,
    _create_trial_archive,
    _create_trial_archive_file,
    _extract_primary_reward,
    _read_trial_lock_for_upload,
    _timing_field,
)


def _make_trial_result(
    *,
    trial_name: str = "trial-0",
    task_name: str = "task-0",
    rewards: dict[str, float | int] | None = None,
    include_model: bool = True,
    model_provider: str | None = "anthropic",
    agent_name: str = "claude-code",
    agent_version: str = "1.0",
    trial_id: UUID | None = None,
    task_config: TaskConfig | None = None,
    task_checksum: str = "deadbeef",
) -> TrialResult:
    task_config = task_config or TaskConfig(path=Path("/tmp/task"))
    trial_config = TrialConfig(
        task=task_config,
        trial_name=trial_name,
        job_id=uuid4(),
    )
    # `model_provider=None` exercises the `-m gpt-5.4` path (no provider
    # prefix) where the DB default takes over.
    model_info = (
        ModelInfo(name="claude-opus-4-1", provider=model_provider)
        if include_model
        else None
    )
    return TrialResult(
        id=trial_id or uuid4(),
        task_name=task_name,
        trial_name=trial_name,
        trial_uri=f"file:///trials/{trial_name}",
        task_id=task_config.get_task_id(),
        task_checksum=task_checksum,
        config=trial_config,
        agent_info=AgentInfo(
            name=agent_name, version=agent_version, model_info=model_info
        ),
        agent_result=AgentContext(
            n_input_tokens=100,
            n_cache_tokens=10,
            n_output_tokens=50,
            cost_usd=0.012,
        ),
        verifier_result=(VerifierResult(rewards=rewards) if rewards else None),
        started_at=datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 17, 10, 5, 0, tzinfo=timezone.utc),
        environment_setup=TimingInfo(
            started_at=datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 17, 10, 0, 30, tzinfo=timezone.utc),
        ),
        agent_execution=TimingInfo(
            started_at=datetime(2026, 4, 17, 10, 1, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 17, 10, 4, 0, tzinfo=timezone.utc),
        ),
    )


def _write_trial_dir(
    parent: Path,
    trial_result: TrialResult,
    *,
    include_trajectory: bool = True,
    include_artifacts: bool = True,
    include_lock: bool = True,
    task_digest: str = "a" * 64,
) -> Path:
    trial_dir = parent / trial_result.trial_name
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(trial_result.model_dump_json())
    (trial_dir / "config.json").write_text(trial_result.config.model_dump_json())
    if include_lock:
        _write_trial_lock(
            trial_dir, task_name=trial_result.task_name, digest=task_digest
        )
    agent_dir = trial_dir / "agent"
    agent_dir.mkdir()
    if include_trajectory:
        (agent_dir / "trajectory.json").write_text(json.dumps({"steps": []}))
    verifier_dir = trial_dir / "verifier"
    verifier_dir.mkdir()
    (verifier_dir / "reward.txt").write_text("1.0")
    if include_artifacts:
        artifacts_dir = trial_dir / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "output.log").write_text("artifact-data")
    (trial_dir / "trial.log").write_text("trial-log-content")
    return trial_dir


def _make_trial_lock(*, task_name: str, digest: str) -> TrialLock:
    return TrialLock(
        task=TaskLock(name=task_name, type="local", digest=f"sha256:{digest}"),
        agent=AgentConfig(name="claude-code"),
        environment=EnvironmentConfig(),
        verifier=VerifierLock(),
    )


def _write_trial_lock(trial_dir: Path, *, task_name: str, digest: str) -> TrialLock:
    lock = _make_trial_lock(task_name=task_name, digest=digest)
    (trial_dir / "lock.json").write_text(lock.model_dump_json())
    return lock


def _write_multi_step_outputs(trial_dir: Path) -> None:
    for step_name, reward in (("scaffold", "1.0"), ("implement", "0.5")):
        step_dir = trial_dir / "steps" / step_name
        step_agent_dir = step_dir / "agent"
        step_agent_dir.mkdir(parents=True)
        (step_agent_dir / "trajectory.json").write_text(
            json.dumps({"steps": [{"source": "agent", "message": step_name}]})
        )

        step_verifier_dir = step_dir / "verifier"
        step_verifier_dir.mkdir()
        (step_verifier_dir / "reward.txt").write_text(reward)

        step_artifacts_dir = step_dir / "artifacts"
        step_artifacts_dir.mkdir()
        (step_artifacts_dir / "output.log").write_text(f"{step_name}-artifact")

        step_workdir_dir = step_dir / "workdir"
        step_workdir_dir.mkdir()
        (step_workdir_dir / "setup.sh").write_text("#!/usr/bin/env bash\n")
        (step_dir / "scratch.txt").write_text("do-not-upload")


def _write_job_dir(
    tmp_path: Path,
    trial_results: list[TrialResult],
    *,
    include_job_log: bool = True,
) -> tuple[Path, JobResult, JobConfig]:
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    job_id = uuid4()
    job_result = JobResult(
        id=job_id,
        started_at=datetime(2026, 4, 17, 9, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 17, 10, 10, 0, tzinfo=timezone.utc),
        n_total_trials=len(trial_results),
        stats=JobStats.from_trial_results(trial_results),
        trial_results=trial_results,
    )
    job_config = JobConfig(job_name="my-job")
    (job_dir / "result.json").write_text(job_result.model_dump_json())
    (job_dir / "config.json").write_text(job_config.model_dump_json())
    (job_dir / "lock.json").write_text("{}")
    if include_job_log:
        (job_dir / "job.log").write_text("job-log-content")
    for trial_result in trial_results:
        _write_trial_dir(job_dir, trial_result)
    return job_dir, job_result, job_config


def _job_archive_names(job_dir: Path, tmp_path: Path) -> set[str]:
    archive_path = tmp_path / "archives" / "job.tar.gz"
    _create_job_archive_file(job_dir, archive_path)
    with tarfile.open(archive_path, mode="r:gz") as tar:
        return set(tar.getnames())


def _seed_archive_cache(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.with_suffix(".gz.tus-url").write_text("https://uploads.example/upload-id")


class TestCreateTrialArchive:
    def test_includes_full_trial_dir(self, tmp_path: Path) -> None:
        trial_result = _make_trial_result()
        trial_dir = _write_trial_dir(tmp_path, trial_result)

        archive_bytes = _create_trial_archive(trial_dir)

        with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as tar:
            names = set(tar.getnames())
        # Everything in the trial_dir is now in the archive — including the
        # config.json + result.json that were previously excluded, and any
        # stray files the caller happens to put there.
        assert "config.json" in names
        assert "lock.json" in names
        assert "result.json" in names
        assert "agent/trajectory.json" in names
        assert "verifier/reward.txt" in names
        assert "artifacts/output.log" in names
        assert "trial.log" in names

    def test_handles_missing_optional_dirs(self, tmp_path: Path) -> None:
        trial_result = _make_trial_result()
        trial_dir = _write_trial_dir(
            tmp_path, trial_result, include_artifacts=False, include_trajectory=False
        )

        archive_bytes = _create_trial_archive(trial_dir)

        with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as tar:
            names = set(tar.getnames())
        assert "config.json" in names
        assert "lock.json" in names
        assert "result.json" in names
        assert "verifier/reward.txt" in names
        assert "trial.log" in names
        assert not any(n.startswith("artifacts") for n in names)
        assert "agent/trajectory.json" not in names

    def test_requires_lock_json(self, tmp_path: Path) -> None:
        trial_result = _make_trial_result()
        trial_dir = _write_trial_dir(tmp_path, trial_result, include_lock=False)

        with pytest.raises(FileNotFoundError, match="Trial lock file is required"):
            _create_trial_archive(trial_dir)

    def test_allowlist_excludes_stray_files(self, tmp_path: Path) -> None:
        """Files outside _TRIAL_ARCHIVE_INCLUDES must not leak into the archive."""
        trial_result = _make_trial_result()
        trial_dir = _write_trial_dir(tmp_path, trial_result)
        # Drop some stuff a user/tool might reasonably leave lying around.
        (trial_dir / ".DS_Store").write_text("mac metadata")
        (trial_dir / ".env").write_text("SECRET=nope")
        (trial_dir / "scratch.ipynb").write_text("{}")
        stray_dir = trial_dir / "cache"
        stray_dir.mkdir()
        (stray_dir / "huge.bin").write_bytes(b"x" * 1024)

        archive_bytes = _create_trial_archive(trial_dir)

        with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as tar:
            names = set(tar.getnames())
        assert ".DS_Store" not in names
        assert ".env" not in names
        assert "scratch.ipynb" not in names
        assert not any(n.startswith("cache") for n in names)
        # Allowlisted entries still present.
        assert "config.json" in names
        assert "lock.json" in names
        assert "agent/trajectory.json" in names

    def test_includes_multi_step_outputs(self, tmp_path: Path) -> None:
        trial_result = _make_trial_result()
        trial_dir = _write_trial_dir(tmp_path, trial_result)
        _write_multi_step_outputs(trial_dir)

        archive_bytes = _create_trial_archive(trial_dir)

        with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as tar:
            names = set(tar.getnames())
        assert "steps/scaffold/agent/trajectory.json" in names
        assert "steps/scaffold/verifier/reward.txt" in names
        assert "steps/scaffold/artifacts/output.log" in names
        assert "steps/implement/agent/trajectory.json" in names
        assert "steps/implement/verifier/reward.txt" in names
        assert "steps/implement/artifacts/output.log" in names
        assert "steps/scaffold/workdir/setup.sh" not in names
        assert "steps/scaffold/scratch.txt" not in names


class TestCreateJobArchive:
    def test_includes_full_job_dir(self, tmp_path: Path) -> None:
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 0.0}),
        ]
        job_dir, job_result, _ = _write_job_dir(tmp_path, trial_results)

        names = _job_archive_names(job_dir, tmp_path)
        # Entries live under the job_dir's own name so `tar -xzf job.tar.gz`
        # in an output_dir lands at `output_dir/{job_name}/...`.
        root = job_dir.name
        assert f"{root}/config.json" in names
        assert f"{root}/lock.json" in names
        assert f"{root}/result.json" in names
        assert f"{root}/job.log" in names
        for trial_name in ("t1", "t2"):
            assert f"{root}/{trial_name}/config.json" in names
            assert f"{root}/{trial_name}/result.json" in names
            assert f"{root}/{trial_name}/agent/trajectory.json" in names
            assert f"{root}/{trial_name}/verifier/reward.txt" in names
            assert f"{root}/{trial_name}/artifacts/output.log" in names
            assert f"{root}/{trial_name}/trial.log" in names

    def test_skips_missing_job_log(self, tmp_path: Path) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results, include_job_log=False)

        names = _job_archive_names(job_dir, tmp_path)
        root = job_dir.name
        assert f"{root}/job.log" not in names
        assert f"{root}/config.json" in names
        assert f"{root}/result.json" in names

    def test_allowlist_excludes_stray_files_and_non_trial_subdirs(
        self, tmp_path: Path
    ) -> None:
        """Top-level stray files + directories without a result.json must be ignored.

        Only the allowlisted top-level names and genuine trial subdirs (detected
        by having a ``result.json`` inside) make it into the archive.
        """
        trial_results = [_make_trial_result(trial_name="t1", rewards={"reward": 1.0})]
        job_dir, job_result, _ = _write_job_dir(tmp_path, trial_results)

        # Top-level stray files.
        (job_dir / ".DS_Store").write_text("mac metadata")
        (job_dir / ".env").write_text("SECRET=nope")
        (job_dir / "notes.md").write_text("scratch notes")

        # A directory at the job root that isn't a trial (no result.json).
        not_a_trial = job_dir / "tmp_scratch"
        not_a_trial.mkdir()
        (not_a_trial / "huge.bin").write_bytes(b"x" * 1024)

        # Stray file INSIDE a real trial subdir — filtered by the trial
        # allowlist, not by the job one.
        (job_dir / "t1" / "leaked.txt").write_text("don't upload me")

        names = _job_archive_names(job_dir, tmp_path)
        root = job_dir.name
        # Top-level strays excluded.
        assert f"{root}/.DS_Store" not in names
        assert f"{root}/.env" not in names
        assert f"{root}/notes.md" not in names
        # Non-trial subdir entirely excluded.
        assert not any(n.startswith(f"{root}/tmp_scratch") for n in names)
        # Stray file inside a real trial excluded (per the trial allowlist).
        assert f"{root}/t1/leaked.txt" not in names
        # Allowlisted entries still present.
        assert f"{root}/config.json" in names
        assert f"{root}/t1/result.json" in names
        assert f"{root}/t1/agent/trajectory.json" in names

    def test_includes_multi_step_outputs(self, tmp_path: Path) -> None:
        trial_result = _make_trial_result(trial_name="t1", rewards={"reward": 1.0})
        job_dir, _, _ = _write_job_dir(tmp_path, [trial_result])
        _write_multi_step_outputs(job_dir / "t1")

        names = _job_archive_names(job_dir, tmp_path)
        root = job_dir.name
        assert f"{root}/t1/steps/scaffold/agent/trajectory.json" in names
        assert f"{root}/t1/steps/scaffold/verifier/reward.txt" in names
        assert f"{root}/t1/steps/scaffold/artifacts/output.log" in names
        assert f"{root}/t1/steps/implement/agent/trajectory.json" in names
        assert f"{root}/t1/steps/implement/verifier/reward.txt" in names
        assert f"{root}/t1/steps/implement/artifacts/output.log" in names
        assert f"{root}/t1/steps/scaffold/workdir/setup.sh" not in names
        assert f"{root}/t1/steps/scaffold/scratch.txt" not in names

    def test_includes_only_canonical_regular_retry_sidecars(
        self, tmp_path: Path
    ) -> None:
        job_dir, _, _ = _write_job_dir(tmp_path, [])
        root = job_dir.name
        legacy_names = {
            f"{root}/{name}"
            for name in ("config.json", "lock.json", "result.json", "job.log")
        }
        assert _job_archive_names(job_dir, tmp_path) == legacy_names

        retry_attempts = {
            f"retry-attempt-{UUID(int=2)}.json": b'{ "cost_usd": null }\r\n',
            f"retry-attempt-{UUID(int=1)}.json": b'{"unknown":123}\n',
        }
        for name, data in retry_attempts.items():
            (job_dir / name).write_bytes(data)

        for name in (
            "retry-attempt-AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA.json",
            f"retry-attempt-{UUID(int=3).hex}.json",
            "retry-attempt-not-a-uuid.json",
            f".retry-attempt-{UUID(int=4)}.json",
            f"retry-attempt-{UUID(int=5)}.json.tmp",
        ):
            (job_dir / name).write_bytes(b"not an archive member")

        outside = tmp_path / "outside.json"
        outside.write_bytes(b"external file")
        (job_dir / f"retry-attempt-{UUID(int=6)}.json").symlink_to(outside)
        (job_dir / f"retry-attempt-{UUID(int=7)}.json").mkdir()
        os.mkfifo(job_dir / f"retry-attempt-{UUID(int=8)}.json")

        archive_path = tmp_path / "job.tar.gz"
        _create_job_archive_file(job_dir, archive_path)
        expected = {f"{root}/{name}": data for name, data in retry_attempts.items()}
        with tarfile.open(archive_path, mode="r:gz") as tar:
            assert set(tar.getnames()) == legacy_names | set(expected)
            actual_order = [name for name in tar.getnames() if name in expected]
            assert actual_order == sorted(expected)
            for name, data in expected.items():
                assert tar.getmember(name).isreg()
                member = tar.extractfile(name)
                assert member is not None
                assert member.read() == data

    @pytest.mark.parametrize("replacement", ["symlink", "fifo"])
    def test_excludes_retry_sidecar_swapped_before_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
    ) -> None:
        job_dir, _, _ = _write_job_dir(tmp_path, [])
        sidecar = job_dir / f"retry-attempt-{UUID(int=1)}.json"
        sidecar.write_bytes(b"original retry")
        outside = tmp_path / "outside.json"
        outside.write_bytes(b"external file")
        open_file = os.open

        def swap_before_open(path, flags, *args, **kwargs):
            if path in (sidecar.name, sidecar):
                assert flags & os.O_NOFOLLOW
                assert flags & os.O_NONBLOCK
                sidecar.unlink()
                if replacement == "symlink":
                    sidecar.symlink_to(outside)
                else:
                    os.mkfifo(sidecar)
            return open_file(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", swap_before_open)

        assert f"{job_dir.name}/{sidecar.name}" not in _job_archive_names(
            job_dir, tmp_path
        )
        assert sidecar.is_symlink() or sidecar.is_fifo()

    def test_propagates_regular_retry_sidecar_io_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job_dir, _, _ = _write_job_dir(tmp_path, [])
        sidecar = job_dir / f"retry-attempt-{UUID(int=1)}.json"
        sidecar.write_bytes(b"readable retry")
        open_file = os.open

        def fail_retry_open(path, flags, *args, **kwargs):
            if path in (sidecar.name, sidecar):
                raise OSError(errno.EIO, "retry sidecar I/O failed")
            return open_file(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", fail_retry_open)

        with pytest.raises(OSError, match="retry sidecar I/O failed"):
            _create_job_archive_file(job_dir, tmp_path / "job.tar.gz")


class TestExtractPrimaryReward:
    def test_prefers_reward_key(self) -> None:
        tr = _make_trial_result(rewards={"accuracy": 0.5, "reward": 1.0})
        assert _extract_primary_reward(tr) == 1.0

    def test_uses_single_non_reward_value(self) -> None:
        tr = _make_trial_result(rewards={"accuracy": 0.7})
        assert _extract_primary_reward(tr) == 0.7

    def test_returns_none_for_multi_key_rewards_without_primary_reward(self) -> None:
        tr = _make_trial_result(rewards={"accuracy": 0.7, "style": 1.0})
        assert _extract_primary_reward(tr) is None

    def test_returns_none_without_rewards(self) -> None:
        tr = _make_trial_result(rewards=None)
        assert _extract_primary_reward(tr) is None

    def test_returns_none_for_empty_rewards(self) -> None:
        trial_result = _make_trial_result()
        trial_result.verifier_result = VerifierResult(rewards={})
        assert _extract_primary_reward(trial_result) is None


class TestTimingField:
    def test_returns_none_for_none(self) -> None:
        assert _timing_field(None, "started_at") is None

    def test_extracts_datetime(self) -> None:
        ts = datetime(2026, 4, 17, 10, 0, 0, tzinfo=timezone.utc)
        timing = TimingInfo(started_at=ts)
        assert _timing_field(timing, "started_at") == ts

    def test_returns_none_for_missing_field(self) -> None:
        timing = TimingInfo(started_at=None)
        assert _timing_field(timing, "started_at") is None


class TestTrialLockForUpload:
    def test_reads_trial_lock_from_disk(self, tmp_path: Path) -> None:
        content_hash = "c" * 64
        trial_result = _make_trial_result(task_checksum="legacy-checksum")
        trial_dir = _write_trial_dir(tmp_path, trial_result, task_digest=content_hash)

        trial_lock = _read_trial_lock_for_upload(trial_dir)

        assert trial_lock.task.digest == f"sha256:{content_hash}"

    def test_missing_trial_lock_raises(self, tmp_path: Path) -> None:
        trial_dir = tmp_path / "trial"
        trial_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="Trial lock file is required"):
            _read_trial_lock_for_upload(trial_dir)


@pytest.fixture
def mock_uploader() -> Uploader:
    with (
        patch("harbor.upload.uploader.UploadDB") as mock_db_cls,
        patch("harbor.upload.uploader.UploadStorage") as mock_storage_cls,
    ):
        db = AsyncMock()
        db.get_user_id.return_value = "user-123"
        # `None` means "job doesn't exist" — the default for the common
        # happy-path case. Individual tests can override to simulate a
        # re-upload of an existing job.
        db.get_job_visibility.return_value = None
        # Default: no trials on the server yet. Resume tests override.
        db.list_trials_for_job.return_value = []
        # Default: the server row is incomplete (archive_path NULL), so the
        # upload_job sweep finalizes. Tests that want to exercise the
        # "already finalized, skip" path override to return a dict with a
        # non-null archive_path.
        db.get_job.return_value = {"archive_path": None}
        # Default: no trial is finalized yet, so every trial uploads. The
        # skip-existing test overrides this with a finalized row.
        db.get_trial.return_value = None
        # Org-first ownership: default to a personal org for new jobs, and no
        # discoverable existing owner (the re-upload guard degrades to
        # "can't tell", so it never blocks the visibility-only re-upload tests).
        db.resolve_owner_org.return_value = {
            "id": "00000000-0000-0000-0000-0000000000aa",
            "name": "octocat",
            "display_name": "Octocat",
            "kind": "personal",
        }
        db.get_job_owner_org.return_value = None
        db.upsert_agent.side_effect = lambda name, version: f"agent-{name}-{version}"
        db.upsert_model.side_effect = lambda name, provider: f"model-{name}-{provider}"
        db.insert_job.return_value = None
        db.finalize_job.return_value = None
        db.update_job_visibility.return_value = None
        db.add_job_shares.return_value = {"orgs": [], "users": []}
        db.insert_trial.return_value = None
        db.insert_trial_model.return_value = None
        db.finalize_trial_artifacts.return_value = None
        mock_db_cls.return_value = db

        storage = AsyncMock()
        mock_storage_cls.return_value = storage

        uploader = Uploader()
    return uploader


@pytest.fixture
def archive_uploads(mock_uploader: Uploader) -> dict[str, tuple[bytes, str | None]]:
    uploads: dict[str, tuple[bytes, str | None]] = {}

    async def upload_archive(path: Path, key: str) -> None:
        if key.endswith(".tar.gz"):
            assert path.parent.name == ".harbor-upload"
            assert path.name == Path(key).name
            upload_url = path.with_suffix(".gz.tus-url")
            url = upload_url.read_text() if upload_url.exists() else None
            uploads[key] = (path.read_bytes(), url)

    mock_uploader.storage.upload_resumable_file.side_effect = upload_archive
    return uploads


class TestUploadJob:
    @pytest.mark.parametrize(
        ("exception_type", "reward", "expected"),
        [("ApiRateLimitError", 99, None), ("AgentTimeoutError", 0, 0)],
    )
    async def test_upload_scores_follow_reward_validity(
        self, tmp_path, mock_uploader, exception_type, reward, expected
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": reward})
        trial_result.exception_info = ExceptionInfo(
            exception_type=exception_type,
            exception_message="failed",
            exception_traceback="",
            occurred_at=datetime.now(timezone.utc),
        )
        trial_dir = _write_trial_dir(tmp_path, trial_result)

        result = await mock_uploader.upload_single_trial(
            trial_result=trial_result,
            trial_dir=trial_dir,
            trial_lock=_read_trial_lock_for_upload(trial_dir),
            job_id=uuid4(),
            agent_cache={},
            model_cache={},
        )

        assert result.error is None
        assert result.reward == expected
        assert mock_uploader.db.insert_trial.call_args.kwargs["rewards"] == (
            {"reward": expected} if expected is not None else None
        )
        raw_result = json.loads((trial_dir / "result.json").read_text())
        assert raw_result["verifier_result"]["rewards"] == {"reward": reward}

    @pytest.mark.asyncio
    async def test_concurrent_streaming_uploads_share_dimension_upserts(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Streaming hooks share initially empty caches across concurrent
        trial uploads. Only the first coroutine should upsert a given
        agent/model pair; followers should reuse the populated cache."""
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 0.0}),
        ]
        job_dir, job_result, _ = _write_job_dir(tmp_path, trial_results)

        async def _upsert_agent(name: str, version: str) -> str:
            await asyncio.sleep(0.01)
            return f"agent-{name}-{version}"

        async def _upsert_model(name: str, provider: str | None) -> str:
            await asyncio.sleep(0.01)
            return f"model-{name}-{provider}"

        mock_uploader.db.upsert_agent.side_effect = _upsert_agent
        mock_uploader.db.upsert_model.side_effect = _upsert_model

        agent_cache: dict[tuple[str, str], str] = {}
        model_cache: dict[tuple[str, str | None], str] = {}
        await asyncio.gather(
            *[
                mock_uploader.upload_single_trial(
                    trial_result=tr,
                    trial_dir=job_dir / tr.trial_name,
                    trial_lock=_read_trial_lock_for_upload(job_dir / tr.trial_name),
                    job_id=job_result.id,
                    agent_cache=agent_cache,
                    model_cache=model_cache,
                )
                for tr in trial_results
            ]
        )

        mock_uploader.db.upsert_agent.assert_awaited_once()
        mock_uploader.db.upsert_model.assert_awaited_once()
        assert mock_uploader.db.insert_trial.await_count == 2
        assert mock_uploader.db.insert_trial_model.await_count == 2

    @pytest.mark.asyncio
    async def test_trial_row_precedes_storage_and_paths_finalize_last(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, job_result, _ = _write_job_dir(tmp_path, [trial_result])
        events: list[str] = []

        async def _insert_trial(**kwargs) -> None:
            assert "archive_path" not in kwargs
            assert "trajectory_path" not in kwargs
            events.append("trial")

        async def _insert_trial_model(**_kwargs) -> None:
            events.append("trial_model")

        async def _upload(_local_path: Path, remote_path: str) -> None:
            events.append(Path(remote_path).name)

        async def _finalize(_trial_id: UUID, **_kwargs) -> None:
            events.append("finalize")

        mock_uploader.db.insert_trial.side_effect = _insert_trial
        mock_uploader.db.insert_trial_model.side_effect = _insert_trial_model
        mock_uploader.storage.upload_resumable_file.side_effect = _upload
        mock_uploader.db.finalize_trial_artifacts.side_effect = _finalize

        result = await mock_uploader.upload_single_trial(
            trial_result=trial_result,
            trial_dir=job_dir / trial_result.trial_name,
            trial_lock=_read_trial_lock_for_upload(job_dir / trial_result.trial_name),
            job_id=job_result.id,
            agent_cache={},
            model_cache={},
        )

        assert result.error is None
        assert events == [
            "trial",
            "trial_model",
            "trial.tar.gz",
            "trajectory.json",
            "finalize",
        ]

    @pytest.mark.asyncio
    async def test_archive_failure_leaves_resumable_trial_and_local_archive(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, job_result, _ = _write_job_dir(tmp_path, [trial_result])
        trial_dir = job_dir / trial_result.trial_name
        mock_uploader.storage.upload_resumable_file.side_effect = RuntimeError("boom")

        result = await mock_uploader.upload_single_trial(
            trial_result=trial_result,
            trial_dir=trial_dir,
            trial_lock=_read_trial_lock_for_upload(trial_dir),
            job_id=job_result.id,
            agent_cache={},
            model_cache={},
        )

        assert result.error == "RuntimeError: boom"
        mock_uploader.db.insert_trial.assert_awaited_once()
        mock_uploader.db.insert_trial_model.assert_awaited_once()
        mock_uploader.db.finalize_trial_artifacts.assert_not_awaited()
        assert (trial_dir / ".harbor-upload" / "trial.tar.gz").exists()

    @pytest.mark.asyncio
    async def test_finalization_failure_resumes_null_archive_path(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, job_result, _ = _write_job_dir(tmp_path, [trial_result])
        trial_dir = job_dir / trial_result.trial_name
        mock_uploader.db.get_trial.side_effect = [
            None,
            {"id": str(trial_result.id), "archive_path": None},
        ]
        mock_uploader.db.finalize_trial_artifacts.side_effect = [
            RuntimeError("update failed"),
            None,
        ]

        first = await mock_uploader.upload_single_trial(
            trial_result=trial_result,
            trial_dir=trial_dir,
            trial_lock=_read_trial_lock_for_upload(trial_dir),
            job_id=job_result.id,
            agent_cache={},
            model_cache={},
        )
        assert first.error == "RuntimeError: update failed"
        assert (trial_dir / ".harbor-upload" / "trial.tar.gz").exists()

        second = await mock_uploader.upload_single_trial(
            trial_result=trial_result,
            trial_dir=trial_dir,
            trial_lock=_read_trial_lock_for_upload(trial_dir),
            job_id=job_result.id,
            agent_cache={},
            model_cache={},
        )

        assert second.error is None
        assert mock_uploader.db.insert_trial.await_count == 2
        assert mock_uploader.db.insert_trial_model.await_count == 2
        assert mock_uploader.db.finalize_trial_artifacts.await_count == 2
        assert not (trial_dir / ".harbor-upload" / "trial.tar.gz").exists()

    @pytest.mark.asyncio
    async def test_trial_model_failure_prevents_storage_upload(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, job_result, _ = _write_job_dir(tmp_path, [trial_result])
        trial_dir = job_dir / trial_result.trial_name
        mock_uploader.db.insert_trial_model.side_effect = RuntimeError("model failed")

        result = await mock_uploader.upload_single_trial(
            trial_result=trial_result,
            trial_dir=trial_dir,
            trial_lock=_read_trial_lock_for_upload(trial_dir),
            job_id=job_result.id,
            agent_cache={},
            model_cache={},
        )

        assert result.error == "RuntimeError: model failed"
        mock_uploader.db.insert_trial.assert_awaited_once()
        mock_uploader.storage.upload_resumable_file.assert_not_awaited()
        mock_uploader.db.finalize_trial_artifacts.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uploads_job_and_trials(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 0.0}),
        ]
        job_dir, job_result, job_config = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        assert result.job_name == "my-job"
        assert result.job_id == str(job_result.id)
        assert result.n_trials_uploaded == 2
        assert result.n_trials_skipped == 0
        assert result.n_trials_failed == 0

        # Streaming shape: insert_job runs at start with archive_path=None
        # (no job archive yet); finalize_job writes the archive_path + timing
        # after the per-trial uploads finish.
        mock_uploader.db.insert_job.assert_awaited_once()
        insert_job_kwargs = mock_uploader.db.insert_job.await_args.kwargs
        assert insert_job_kwargs["archive_path"] is None
        assert insert_job_kwargs["finished_at"] is None
        assert insert_job_kwargs["log_path"] is None
        # n_planned_trials is sourced from `JobResult.n_total_trials` on the
        # batch path so the viewer can show progress on a fresh upload too.
        assert insert_job_kwargs["n_planned_trials"] == job_result.n_total_trials
        uploaded_config = insert_job_kwargs["config"]
        assert uploaded_config["schema_version"] == 1
        assert uploaded_config["n_attempts"] == job_config.n_attempts
        # Retry exclusions are sets; JSON list ordering is not significant.
        assert (
            JobConfig.model_validate(uploaded_config).model_dump()
            == job_config.model_dump()
        )
        mock_uploader.db.finalize_job.assert_awaited_once()
        finalize_kwargs = mock_uploader.db.finalize_job.await_args.kwargs
        assert finalize_kwargs["archive_path"].startswith(f"jobs/{job_result.id}/job-")
        assert mock_uploader.db.insert_trial.await_count == 2
        assert mock_uploader.db.insert_trial_model.await_count == 2
        # upload_resumable_file: 2 trial archives + 2 trajectories + the job
        # archive. job.log uses upload_large_file so large logs avoid the
        # standard storage path.
        assert mock_uploader.storage.upload_file.await_count == 0
        assert mock_uploader.storage.upload_resumable_file.await_count == 5
        assert mock_uploader.storage.upload_bytes.await_count == 0
        assert mock_uploader.storage.upload_large_file.await_count == 1
        large_file_calls = mock_uploader.storage.upload_large_file.await_args_list
        job_log_args = large_file_calls[0].args
        assert job_log_args[1] == f"jobs/{job_result.id}/job.log"
        assert large_file_calls[0].kwargs["content_type"] == "text/plain"
        resumable_uploads = mock_uploader.storage.upload_resumable_file.await_args_list
        resumable_paths = {call.args[1] for call in resumable_uploads}
        job_archive_call = next(
            call
            for call in resumable_uploads
            if call.args[1] == finalize_kwargs["archive_path"]
        )
        assert (
            job_archive_call.args[0].name == Path(finalize_kwargs["archive_path"]).name
        )
        assert job_archive_call.args[0].parent.name == ".harbor-upload"
        assert not job_archive_call.args[0].exists()
        per_trial_paths = {p for p in resumable_paths if p.endswith("/trial.tar.gz")}
        assert len(per_trial_paths) == 2
        assert all(p.startswith("trials/") for p in per_trial_paths)
        trajectory_paths = {p for p in resumable_paths if p.endswith(".json")}
        assert all(p.startswith("trials/") for p in trajectory_paths)
        assert all(call.kwargs == {} for call in resumable_uploads)
        archive_calls = [
            call for call in resumable_uploads if call.args[1].endswith("/trial.tar.gz")
        ]
        assert all(call.args[0].name == "trial.tar.gz" for call in archive_calls)
        assert all(
            call.args[0].parent.name == ".harbor-upload" for call in archive_calls
        )
        assert all(not call.args[0].exists() for call in archive_calls)

    @pytest.mark.asyncio
    async def test_inserts_trial_row_before_uploading_archive(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Regression: the trial row must be inserted BEFORE the archive is
        uploaded, then the archive_path committed by finalize AFTER.

        The `results` bucket RLS policy authorizes a write to
        `trials/{id}/...` only when a trial → job row for that id already
        exists, so an upload that ran before the insert (the original bug)
        is rejected. Ordering: insert_trial → upload → finalize.
        """
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        trial_archive_path = f"trials/{trial_results[0].id}/trial.tar.gz"
        calls: list[str] = []

        async def _insert_trial(**_kwargs) -> None:
            calls.append("insert_trial")

        async def _upload_resumable_file(_file_path, remote_path) -> None:
            if remote_path == trial_archive_path:
                calls.append("upload_archive")

        async def _finalize_trial_artifacts(_trial_id, **_kwargs) -> None:
            calls.append("finalize_trial_artifacts")

        mock_uploader.db.insert_trial.side_effect = _insert_trial
        mock_uploader.storage.upload_resumable_file.side_effect = _upload_resumable_file
        mock_uploader.db.finalize_trial_artifacts.side_effect = (
            _finalize_trial_artifacts
        )

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        assert calls == [
            "insert_trial",
            "upload_archive",
            "finalize_trial_artifacts",
        ]
        # The insert leaves the artifact paths unset; finalize commits the
        # deterministic path once the upload succeeds.
        insert_kwargs = mock_uploader.db.insert_trial.await_args.kwargs
        assert "archive_path" not in insert_kwargs
        finalize_kwargs = mock_uploader.db.finalize_trial_artifacts.await_args.kwargs
        assert finalize_kwargs["archive_path"] == trial_archive_path

    @pytest.mark.asyncio
    async def test_uploads_task_content_hash_from_trial_lock(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        content_hash = "b" * 64
        trial_result = _make_trial_result(
            rewards={"reward": 1.0},
            task_checksum="legacy-checksum",
        )
        job_dir, _, _ = _write_job_dir(tmp_path, [trial_result])
        trial_lock = _write_trial_lock(
            job_dir / trial_result.trial_name,
            task_name=trial_result.task_name,
            digest=content_hash,
        )

        await mock_uploader.upload_job(job_dir)

        insert_kwargs = mock_uploader.db.insert_trial.await_args.kwargs
        assert insert_kwargs["task_content_hash"] == content_hash
        assert insert_kwargs["lock"] == trial_lock.model_dump(
            mode="json", exclude_none=True
        )
        assert insert_kwargs["config"] == trial_result.config.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_only_uploads_canonical_trajectory_separately(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, _, _ = _write_job_dir(tmp_path, [trial_result])
        trial_dir = job_dir / trial_result.trial_name
        (trial_dir / "agent" / "mini-swe-agent.trajectory.json").write_text("{}")
        (trial_dir / "artifacts" / "result.json").write_text("{}")

        await mock_uploader.upload_job(job_dir)

        json_calls = [
            call
            for call in mock_uploader.storage.upload_resumable_file.await_args_list
            if call.args[1].endswith(".json")
        ]
        assert len(json_calls) == 1
        assert json_calls[0].args[1] == f"trials/{trial_result.id}/trajectory.json"
        assert json_calls[0].kwargs == {}

    @pytest.mark.asyncio
    async def test_caches_agent_and_model_upserts(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [
            _make_trial_result(trial_name=f"t{i}", rewards={"reward": 1.0})
            for i in range(3)
        ]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        await mock_uploader.upload_job(job_dir)

        # Same agent/model across all three trials -> each upsert called once.
        mock_uploader.db.upsert_agent.assert_awaited_once_with("claude-code", "1.0")
        mock_uploader.db.upsert_model.assert_awaited_once_with(
            "claude-opus-4-1", "anthropic"
        )

    @pytest.mark.asyncio
    async def test_resumes_partial_job_uploading_missing_trials_and_finalizing(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """`upload_job` on an existing row with archive_path=NULL acts as a
        resume: skip insert_job, upload any trials not already on the server,
        and finalize. This is the "streaming run crashed mid-way → user runs
        harbor upload" path."""
        mock_uploader.db.get_job_visibility.return_value = "private"
        # Server has the row but no trials yet, and hasn't been finalized.
        mock_uploader.db.list_trials_for_job.return_value = []
        mock_uploader.db.get_job.return_value = {"archive_path": None}
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        # Insert is skipped — the row is already there.
        mock_uploader.db.insert_job.assert_not_awaited()
        # The trial is uploaded (not on server yet).
        assert result.n_trials_uploaded == 1
        # And finalize runs to write the job archive + finished_at.
        mock_uploader.db.finalize_job.assert_awaited_once()
        assert result.job_already_existed is True

    @pytest.mark.asyncio
    async def test_resumes_existing_trial_with_null_archive_path(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        mock_uploader.db.get_job_visibility.return_value = "private"
        mock_uploader.db.list_trials_for_job.return_value = [
            {"id": str(trial_result.id), "archive_path": None}
        ]
        mock_uploader.db.get_trial.return_value = {
            "id": str(trial_result.id),
            "archive_path": None,
        }
        job_dir, _, _ = _write_job_dir(tmp_path, [trial_result])

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        assert result.n_trials_skipped == 0
        mock_uploader.db.insert_trial.assert_awaited_once()
        mock_uploader.db.finalize_trial_artifacts.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reupload_with_new_attempt_refreshes_archive(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trials = [_make_trial_result(trial_name=f"attempt-{i}") for i in range(2)]
        directory, job_result, _ = _write_job_dir(tmp_path, trials)
        existing_path = f"jobs/{job_result.id}/job.tar.gz"
        mock_uploader.db.get_job_visibility.return_value = "private"
        mock_uploader.db.get_job.return_value = {"archive_path": existing_path}
        mock_uploader.db.list_trials_for_job.return_value = [
            {"id": str(trials[0].id), "archive_path": "existing-trial.tar.gz"}
        ]
        result = await mock_uploader.upload_job(directory)
        assert result.n_trials_skipped == 1
        assert result.n_trials_uploaded == 1
        path = mock_uploader.db.finalize_job.await_args.kwargs["archive_path"]
        assert path.startswith(f"jobs/{job_result.id}/job-")
        assert path != existing_path
        assert mock_uploader.storage.upload_resumable_file.await_args.args[1] == path

        # A failed metadata write must be recoverable even once every trial
        # has been uploaded: the old job archive still needs to be replaced.
        mock_uploader.db.list_trials_for_job.return_value = [
            {"id": str(tr.id), "archive_path": "trial.tar.gz"} for tr in trials
        ]
        mock_uploader.db.finalize_job.reset_mock()
        await mock_uploader.upload_job(directory)
        mock_uploader.db.finalize_job.assert_awaited_once()
        assert mock_uploader.db.finalize_job.await_args.kwargs["archive_path"] == path

    @pytest.mark.asyncio
    async def test_skips_already_finalized_job(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Re-uploading a completed job is a near-no-op: trials are already
        on the server, archive_path is set, so nothing to do beyond the
        start_job visibility check."""
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        mock_uploader.db.get_job_visibility.return_value = "public"
        mock_uploader.db.list_trials_for_job.return_value = [
            {"id": str(trial_result.id), "archive_path": "trials/existing/trial.tar.gz"}
        ]
        job_dir, job_result, _ = _write_job_dir(tmp_path, [trial_result])

        await mock_uploader.upload_job(job_dir)
        revision = hashlib.sha256(str(trial_result.id).encode()).hexdigest()
        archive_path = f"jobs/{job_result.id}/job-{revision}.tar.gz"
        assert mock_uploader.db.finalize_job.await_args.kwargs["archive_path"] == (
            archive_path
        )
        mock_uploader.db.get_job.return_value = {"archive_path": archive_path}
        mock_uploader.db.finalize_job.reset_mock()

        result = await mock_uploader.upload_job(job_dir)

        mock_uploader.db.insert_job.assert_not_awaited()
        mock_uploader.db.finalize_job.assert_not_awaited()
        mock_uploader.db.insert_trial.assert_not_awaited()
        mock_uploader.storage.upload_bytes.assert_not_awaited()
        assert result.n_trials_skipped == 1
        assert result.n_trials_uploaded == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["batch", "direct", "explicit"])
    async def test_retry_sidecar_refreshes_finalized_and_cached_archive(
        self, tmp_path, mock_uploader, archive_uploads, mode
    ) -> None:
        trial = _make_trial_result(rewards={"reward": 1.0})
        job_dir, job_result, job_config = _write_job_dir(tmp_path, [trial])
        mock_uploader.db.get_job_visibility.return_value = "private"
        mock_uploader.db.list_trials_for_job.return_value = [
            {"id": str(trial.id), "archive_path": "retained-trial.tar.gz"}
        ]
        revision = "caller-revision" if mode == "explicit" else None
        legacy_revision = (
            hashlib.sha256(str(trial.id).encode()).hexdigest()
            if mode == "batch"
            else revision
        )
        legacy_name = (
            f"job-{legacy_revision}.tar.gz" if legacy_revision else "job.tar.gz"
        )
        cache_dir = job_dir / ".harbor-upload"
        legacy_archive = cache_dir / legacy_name
        _create_job_archive_file(job_dir, legacy_archive)
        with tarfile.open(legacy_archive, mode="r:gz") as tar:
            legacy_members = set(tar.getnames())
        result_paths = (Path("result.json"), Path(trial.trial_name) / "result.json")
        metadata = {
            f"{job_dir.name}/{path}": (job_dir / path).read_bytes()
            for path in result_paths
        }

        async def upload() -> None:
            if mode == "batch":
                result = await mock_uploader.upload_job(job_dir)
                assert (result.n_trials_uploaded, result.n_trials_skipped) == (0, 1)
                assert result.n_trials_failed == 0
            else:
                await mock_uploader.finalize_job(
                    job_dir,
                    job_result=job_result,
                    job_config=job_config,
                    revision=revision,
                )

        key = f"jobs/{job_result.id}/{legacy_name}"
        data = legacy_archive.read_bytes()
        seen_keys = {key}
        first = job_dir / f"retry-attempt-{UUID(int=1)}.json"
        second = job_dir / f"retry-attempt-{UUID(int=2)}.json"
        for sidecar, outcome in (
            (first, b'{"cost_usd":null}\r\n'),
            (first, b'{ "cost_usd":null}\n'),
            (second, b'{ "cost_usd":null}\n'),
        ):
            stale_path = cache_dir / Path(key).name
            _seed_archive_cache(stale_path, data)
            mock_uploader.db.get_job.return_value = {"archive_path": key}
            if sidecar == second:
                first.unlink()
            sidecar.write_bytes(outcome)

            await upload()

            key = mock_uploader.db.finalize_job.await_args.kwargs["archive_path"]
            assert key not in seen_keys
            seen_keys.add(key)
            assert stale_path.read_bytes() == data
            data, upload_url = archive_uploads[key]
            assert upload_url is None
            member_name = f"{job_dir.name}/{sidecar.name}"
            with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tar:
                assert set(tar.getnames()) == legacy_members | {member_name}
                for name, contents in {**metadata, member_name: outcome}.items():
                    member = tar.extractfile(name)
                    assert member is not None
                    assert member.read() == contents

        assert mock_uploader.db.finalize_job.await_count == 3
        mock_uploader.db.insert_trial.assert_not_awaited()
        mock_uploader.db.insert_trial_model.assert_not_awaited()
        mock_uploader.db.finalize_trial_artifacts.assert_not_awaited()
        if mode == "batch":
            mock_uploader.db.get_job.return_value = {"archive_path": key}
            await upload()
            assert mock_uploader.db.finalize_job.await_count == 3

        cached_path = cache_dir / Path(key).name
        _seed_archive_cache(cached_path, data)
        mock_uploader.db.get_job.return_value = {"archive_path": None}
        await upload()
        assert mock_uploader.db.finalize_job.await_count == 4
        assert mock_uploader.db.finalize_job.await_args.kwargs["archive_path"] == key
        assert archive_uploads[key] == (data, "https://uploads.example/upload-id")
        assert not cached_path.exists()
        assert not cached_path.with_suffix(".gz.tus-url").exists()

    @pytest.mark.asyncio
    async def test_job_revision_and_archive_share_snapshot_across_log_upload(
        self, tmp_path, mock_uploader, archive_uploads
    ) -> None:
        job_dir, _, _ = _write_job_dir(tmp_path, [])
        first = job_dir / f"retry-attempt-{UUID(int=1)}.json"
        second = job_dir / f"retry-attempt-{UUID(int=2)}.json"
        first.write_bytes(b'{"old": null}\n')

        async def replace_sidecar(*args, **kwargs):
            first.unlink()
            second.write_bytes(b'{"new": null}\n')

        mock_uploader.storage.upload_large_file.side_effect = replace_sidecar
        await mock_uploader.upload_job(job_dir)
        mock_uploader.storage.upload_large_file.side_effect = None
        first_key = mock_uploader.db.finalize_job.await_args.kwargs["archive_path"]
        mock_uploader.db.get_job.return_value = {"archive_path": first_key}
        await mock_uploader.upload_job(job_dir)
        second_key = mock_uploader.db.finalize_job.await_args.kwargs["archive_path"]
        assert first_key != second_key

        for key, included, excluded, outcome in (
            (first_key, first, second, b'{"old": null}\n'),
            (second_key, second, first, b'{"new": null}\n'),
        ):
            data, _ = archive_uploads[key]
            with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tar:
                assert f"{job_dir.name}/{excluded.name}" not in tar.getnames()
                member = tar.extractfile(f"{job_dir.name}/{included.name}")
                assert member is not None
                assert member.read() == outcome

    @pytest.mark.asyncio
    async def test_skips_existing_trial(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        mock_uploader.db.get_trial.return_value = {
            "id": "existing",
            "archive_path": "trials/existing/trial.tar.gz",
        }
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 1.0}),
        ]
        job_dir, job_result, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_skipped == 2
        assert result.n_trials_uploaded == 0
        mock_uploader.db.insert_trial.assert_not_awaited()
        mock_uploader.db.finalize_trial_artifacts.assert_not_awaited()
        # Job-level log/archive still upload; per-trial archives are skipped.
        mock_uploader.storage.upload_bytes.assert_not_awaited()
        mock_uploader.storage.upload_large_file.assert_awaited_once()
        assert mock_uploader.storage.upload_large_file.await_args.args[1] == (
            f"jobs/{job_result.id}/job.log"
        )
        mock_uploader.storage.upload_resumable_file.assert_awaited_once()
        assert (
            mock_uploader.storage.upload_resumable_file.await_args.args[1]
            == (mock_uploader.db.finalize_job.await_args.kwargs["archive_path"])
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("revision", [None, "caller-revision"])
    async def test_finalize_resumes_existing_job_archive_upload(
        self, tmp_path: Path, mock_uploader: Uploader, revision: str | None
    ) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, job_result, job_config = _write_job_dir(tmp_path, trial_results)
        archive_name = f"job-{revision}.tar.gz" if revision else "job.tar.gz"
        archive_path = job_dir / ".harbor-upload" / archive_name
        _create_job_archive_file(job_dir, archive_path)
        _seed_archive_cache(archive_path, archive_path.read_bytes())

        with patch("harbor.upload.uploader._create_job_archive_file") as create_archive:
            await mock_uploader.finalize_job(
                job_dir,
                job_result=job_result,
                job_config=job_config,
                revision=revision,
            )

        create_archive.assert_not_called()
        mock_uploader.storage.upload_large_file.assert_awaited_once()
        mock_uploader.storage.upload_resumable_file.assert_awaited_once_with(
            archive_path,
            f"jobs/{job_result.id}/{archive_name}",
        )
        assert not archive_path.exists()
        assert not archive_path.with_suffix(archive_path.suffix + ".tus-url").exists()

    @pytest.mark.asyncio
    async def test_records_per_trial_errors_without_failing_job(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """A single trial upload failing must not take down the whole job —
        the other trials upload, the job remains incomplete, and the
        failed trial is reported via ``error`` on its TrialUploadResult.
        Uses a path-keyed callable so concurrency order doesn't matter."""
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 1.0}),
        ]
        doomed_path = f"trials/{trial_results[1].id}/trial.tar.gz"

        async def _selective_fail(_file_path, path):
            if path == doomed_path:
                raise RuntimeError("boom")
            return None

        mock_uploader.storage.upload_resumable_file.side_effect = _selective_fail
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        assert result.n_trials_failed == 1
        failed = next(r for r in result.trial_results if r.error is not None)
        assert "RuntimeError: boom" in failed.error
        # A later upload can retry failures before finalizing the archive.
        mock_uploader.db.finalize_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_trial_lock_is_per_trial_error(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 1.0}),
        ]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)
        (job_dir / "t2" / "lock.json").unlink()

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        assert result.n_trials_failed == 1
        failed = next(r for r in result.trial_results if r.error is not None)
        assert failed.trial_name == "t2"
        assert "FileNotFoundError: Trial lock file is required" in failed.error
        assert mock_uploader.db.insert_trial.await_count == 1
        mock_uploader.db.finalize_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_trial_without_model_info(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [
            _make_trial_result(rewards={"reward": 1.0}, include_model=False)
        ]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        mock_uploader.db.upsert_model.assert_not_awaited()
        mock_uploader.db.insert_trial_model.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provider_less_model_propagates_none(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """A trial with ``ModelInfo(name="gpt-5.4", provider=None)`` must
        invoke ``upsert_model`` with provider=None so the DB default
        ('unknown') fires instead of triggering a NOT NULL violation.

        This is the `-m gpt-5.4` (no `<provider>/` prefix) path — the CLI
        records the bare model name, the uploader forwards the None, and
        the db_client layer handles the key-omit in the insert.
        """
        trial_results = [
            _make_trial_result(rewards={"reward": 1.0}, model_provider=None)
        ]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        mock_uploader.db.upsert_model.assert_awaited_once_with("claude-opus-4-1", None)
        # The trial_model row still gets inserted — only the provider
        # dedup is deferred to the DB default.
        mock_uploader.db.insert_trial_model.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_job_log_upload_when_missing(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results, include_job_log=False)

        await mock_uploader.upload_job(job_dir)

        # The trial archive, trajectory, and job archive upload via TUS.
        assert mock_uploader.storage.upload_resumable_file.await_count == 3
        call_kwargs = mock_uploader.db.insert_job.await_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs["log_path"] is None

    @pytest.mark.asyncio
    async def test_requires_auth(self, tmp_path: Path, mock_uploader: Uploader) -> None:
        mock_uploader.db.get_user_id.side_effect = RuntimeError("Not authenticated.")
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        with pytest.raises(RuntimeError, match="Not authenticated"):
            await mock_uploader.upload_job(job_dir)

    @pytest.mark.asyncio
    async def test_invokes_progress_callbacks(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [
            _make_trial_result(trial_name="t1", rewards={"reward": 1.0}),
            _make_trial_result(trial_name="t2", rewards={"reward": 1.0}),
        ]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        started: list[str] = []
        completed: list[str] = []

        def on_start(tr: TrialResult) -> None:
            started.append(tr.trial_name)

        def on_complete(tr: TrialResult, _upload_result) -> None:
            completed.append(tr.trial_name)

        await mock_uploader.upload_job(
            job_dir, on_trial_start=on_start, on_trial_complete=on_complete
        )

        assert sorted(started) == ["t1", "t2"]
        assert sorted(completed) == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_skips_trajectory_upload_when_missing(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, job_result, _ = _write_job_dir(tmp_path, [trial_result])
        # Remove trajectory.json to trigger the skip branch.
        (job_dir / trial_result.trial_name / "agent" / "trajectory.json").unlink()

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        # The trial archive still uploads via TUS; there is no JSON upload.
        resumable_paths = {
            call.args[1]
            for call in mock_uploader.storage.upload_resumable_file.await_args_list
        }
        assert resumable_paths == {
            f"trials/{trial_result.id}/trial.tar.gz",
            mock_uploader.db.finalize_job.await_args.kwargs["archive_path"],
        }
        insert_kwargs = mock_uploader.db.insert_trial.await_args.kwargs
        assert insert_kwargs["config"]["schema_version"] == 1
        assert insert_kwargs["config"]["timeout_multiplier"] == 1.0
        assert "trajectory_path" not in insert_kwargs
        assert "archive_path" not in insert_kwargs
        finalize_kwargs = mock_uploader.db.finalize_trial_artifacts.await_args.kwargs
        assert finalize_kwargs["trajectory_path"] is None
        assert finalize_kwargs["archive_path"] == (
            f"trials/{trial_result.id}/trial.tar.gz"
        )

    @pytest.mark.asyncio
    async def test_continues_when_direct_trajectory_upload_fails(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_result = _make_trial_result(rewards={"reward": 1.0})
        job_dir, _, _ = _write_job_dir(tmp_path, [trial_result])
        trajectory_path = (
            job_dir / trial_result.trial_name / "agent" / "trajectory.json"
        )
        sidecar_path = trajectory_path.with_suffix(trajectory_path.suffix + ".tus-url")
        job_archive_names: set[str] = set()

        async def _fail_trajectory(file_path, remote_path):
            if remote_path.endswith(".json"):
                file_path.with_suffix(file_path.suffix + ".tus-url").write_text(
                    "https://uploads.example/upload-id"
                )
                raise RuntimeError("trajectory upload blocked")
            if remote_path.startswith("jobs/"):
                with tarfile.open(file_path, mode="r:gz") as tar:
                    job_archive_names.update(tar.getnames())

        mock_uploader.storage.upload_resumable_file.side_effect = _fail_trajectory

        result = await mock_uploader.upload_job(job_dir)

        assert result.n_trials_uploaded == 1
        assert result.n_trials_failed == 0
        # Direct trajectory upload failed → finalize publishes a null
        # trajectory_path (the archive is still committed).
        assert not sidecar_path.exists()
        assert job_archive_names
        assert not any(name.endswith(".tus-url") for name in job_archive_names)
        insert_kwargs = mock_uploader.db.insert_trial.await_args.kwargs
        assert "trajectory_path" not in insert_kwargs
        assert "archive_path" not in insert_kwargs
        finalize_kwargs = mock_uploader.db.finalize_trial_artifacts.await_args.kwargs
        assert finalize_kwargs["trajectory_path"] is None
        assert finalize_kwargs["archive_path"] == (
            f"trials/{trial_result.id}/trial.tar.gz"
        )
        mock_uploader.db.insert_trial_model.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_visibility_is_private(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        assert result.visibility == "private"
        assert mock_uploader.db.insert_job.await_args.kwargs["visibility"] == "private"

    @pytest.mark.asyncio
    async def test_public_visibility_propagates(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir, visibility="public")

        assert result.visibility == "public"
        assert mock_uploader.db.insert_job.await_args.kwargs["visibility"] == "public"

    @pytest.mark.asyncio
    async def test_share_targets_are_added_during_upload(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, job_result, _ = _write_job_dir(tmp_path, trial_results)
        mock_uploader.db.add_job_shares.return_value = {
            "orgs": [{"name": "research"}],
            "users": [{"github_username": "alex"}],
        }

        result = await mock_uploader.upload_job(
            job_dir,
            share_orgs=["research"],
            share_users=["alex"],
            confirm_non_member_orgs=True,
        )

        mock_uploader.db.add_job_shares.assert_awaited_once_with(
            job_id=job_result.id,
            org_names=["research"],
            usernames=["alex"],
            confirm_non_member_orgs=True,
        )
        assert result.shared_orgs == ["research"]
        assert result.shared_users == ["alex"]

    @pytest.mark.asyncio
    async def test_existing_job_no_flag_preserves_visibility(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Re-upload with no explicit visibility keeps the server-side value."""
        mock_uploader.db.get_job_visibility.return_value = "public"
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)  # no visibility=

        mock_uploader.db.insert_job.assert_not_awaited()
        mock_uploader.db.update_job_visibility.assert_not_awaited()
        assert result.job_already_existed is True
        # Reports the server's current value, not the caller's default.
        assert result.visibility == "public"

    @pytest.mark.asyncio
    async def test_existing_job_explicit_public_flips_to_public(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Re-upload with explicit --public flips a private job public."""
        mock_uploader.db.get_job_visibility.return_value = "private"
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir, visibility="public")

        mock_uploader.db.insert_job.assert_not_awaited()
        mock_uploader.db.update_job_visibility.assert_awaited_once()
        _, kwargs = mock_uploader.db.update_job_visibility.call_args
        args = mock_uploader.db.update_job_visibility.call_args.args
        # Visibility argument is passed positionally here:
        assert args[1] == "public" or kwargs.get("visibility") == "public"
        assert result.job_already_existed is True
        assert result.visibility == "public"

    @pytest.mark.asyncio
    async def test_existing_job_explicit_matches_current_skips_update(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Don't issue a write when the explicit flag already matches the server."""
        mock_uploader.db.get_job_visibility.return_value = "public"
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir, visibility="public")

        mock_uploader.db.insert_job.assert_not_awaited()
        mock_uploader.db.update_job_visibility.assert_not_awaited()
        assert result.visibility == "public"

    @pytest.mark.asyncio
    async def test_existing_job_explicit_private_flips_to_private(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Re-upload with explicit --private pulls a public job back private."""
        mock_uploader.db.get_job_visibility.return_value = "public"
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir, visibility="private")

        mock_uploader.db.update_job_visibility.assert_awaited_once()
        assert result.visibility == "private"

    @pytest.mark.asyncio
    async def test_new_job_inserts_resolved_org_id(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """A new upload owns the job with the resolved org (personal by
        default): the resolved id is passed to insert_job and surfaced on the
        result for display."""
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        mock_uploader.db.resolve_owner_org.assert_awaited_once_with(None)
        insert_kwargs = mock_uploader.db.insert_job.await_args.kwargs
        assert insert_kwargs["org_id"] == UUID("00000000-0000-0000-0000-0000000000aa")
        assert result.owner_org is not None
        assert result.owner_org["name"] == "octocat"

    @pytest.mark.asyncio
    async def test_explicit_org_is_resolved(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        await mock_uploader.upload_job(job_dir, org="acme")

        mock_uploader.db.resolve_owner_org.assert_awaited_once_with("acme")

    @pytest.mark.asyncio
    async def test_unclaimed_username_fails_before_upload(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """No personal org → the resolve raises OwnerOrgError up-front and no
        trials are inserted (this is the preflight the run/resume paths rely
        on)."""
        mock_uploader.db.resolve_owner_org.side_effect = OwnerOrgError(
            "Claim your Harbor username first: https://hub.example/profile"
        )
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        with pytest.raises(OwnerOrgError, match="Claim your Harbor username first"):
            await mock_uploader.upload_job(job_dir)

        mock_uploader.db.insert_job.assert_not_awaited()
        mock_uploader.db.insert_trial.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reupload_with_conflicting_org_errors(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """Re-uploading an existing job under a different explicit org is a
        hard error — ownership changes go through transfer/copy."""
        mock_uploader.db.get_job_visibility.return_value = "private"
        mock_uploader.db.get_job_owner_org.return_value = {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "acme",
        }
        # resolve_owner_org (fixture default) returns the personal org, a
        # different id than the existing owner → conflict.
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        with pytest.raises(OwnerOrgError, match="already owned by 'acme'"):
            await mock_uploader.upload_job(job_dir, org="octocat")

    @pytest.mark.asyncio
    async def test_reupload_without_org_keeps_existing_owner(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """A defaulted (no --org) re-upload keeps the existing owner and never
        resolves a personal org — mirrors the visibility tri-state."""
        mock_uploader.db.get_job_visibility.return_value = "public"
        existing = {"id": "22222222-2222-2222-2222-222222222222", "name": "acme"}
        mock_uploader.db.get_job_owner_org.return_value = existing
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir)

        mock_uploader.db.resolve_owner_org.assert_not_awaited()
        assert result.owner_org == existing

    @pytest.mark.asyncio
    async def test_reupload_with_org_does_not_report_unpersisted_owner(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        """If the existing owner can't be read, --org must not be shown as
        the owner: re-upload never writes org_id."""
        mock_uploader.db.get_job_visibility.return_value = "private"
        mock_uploader.db.get_job_owner_org.return_value = None
        mock_uploader.db.resolve_owner_org.return_value = {
            "id": "33333333-3333-3333-3333-333333333333",
            "name": "acme",
        }
        trial_results = [_make_trial_result(rewards={"reward": 1.0})]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        result = await mock_uploader.upload_job(job_dir, org="acme")

        mock_uploader.db.insert_job.assert_not_awaited()
        assert result.owner_org is None

    @pytest.mark.asyncio
    async def test_respects_max_concurrency(
        self, tmp_path: Path, mock_uploader: Uploader
    ) -> None:
        import asyncio

        in_flight = 0
        peak = 0

        async def slow_upload(*_args, **_kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

        mock_uploader.storage.upload_resumable_file.side_effect = slow_upload

        trial_results = [
            _make_trial_result(trial_name=f"t{i}", rewards={"reward": 1.0})
            for i in range(6)
        ]
        job_dir, _, _ = _write_job_dir(tmp_path, trial_results)

        await mock_uploader.upload_job(job_dir, max_concurrency=2)

        assert peak <= 2


@pytest.mark.parametrize("archive_kind", ["trial", "job"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_archives_keep_both_conversation_roles_and_uploaded_inputs(
    tmp_path, archive_kind, newline
):
    trial_result = _make_trial_result()
    job_dir, _, _ = _write_job_dir(tmp_path, [trial_result])
    trial_dir = job_dir / trial_result.trial_name
    outputs = {
        "user-agent/trajectory.json": '{"steps": [{"message": "ask target"}]}',
        "user-agent/sessions/session.jsonl": '{"message": "user turn"}' + newline,
        "agent/bridge-trajectory.json": '{"messages": ["first", "second"]}',
        "user-agent-inputs/persona.md": "A patient user",
        "user-agent-inputs/prompt.j2": "{{ instruction }} {{ bridge_instructions }}",
    }
    for relative, contents in outputs.items():
        path = trial_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents.encode("utf-8"))
    (trial_dir / "unrelated-secret.txt").write_text("do not archive")
    if archive_kind == "trial":
        archive = _create_trial_archive(trial_dir)
        prefix = ""
    else:
        destination = tmp_path / "job.tar.gz"
        _create_job_archive_file(job_dir, destination)
        archive = destination.read_bytes()
        prefix = f"{job_dir.name}/{trial_dir.name}/"
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        for relative, contents in outputs.items():
            member = tar.extractfile(prefix + relative)
            assert member is not None
            assert member.read() == contents.encode("utf-8")
        assert prefix + "unrelated-secret.txt" not in tar.getnames()


@pytest.fixture
def native_archive_job(tmp_path: Path) -> tuple[Path, JobResult, TrialResult]:
    trial = _make_trial_result(rewards={"reward": 1.0})
    trial.config.participants = [
        Participant(seat="solver", name="nop"),
        Participant(seat="support", name="nop"),
    ]
    trial.seat_results = [
        SeatResult(seat="solver", under_test=True),
        SeatResult(seat="support", under_test=False),
    ]
    job_dir, job_result, _ = _write_job_dir(tmp_path, [trial])
    return job_dir, job_result, trial


def _write_test_files(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _pack_archive(
    tmp_path: Path, job_dir: Path, trial_dir: Path, kind: str
) -> tuple[bytes, str]:
    if kind == "trial-memory":
        return _create_trial_archive(trial_dir), ""
    output = tmp_path / "test-archive.tar.gz"
    if kind == "trial-file":
        _create_trial_archive_file(trial_dir, output)
        return output.read_bytes(), ""
    assert kind == "job"
    _create_job_archive_file(job_dir, output)
    return output.read_bytes(), f"{job_dir.name}/{trial_dir.name}/"


def _read_regular_member(tar: tarfile.TarFile, name: str) -> bytes:
    assert tar.getmember(name).isreg()
    member = tar.extractfile(name)
    assert member is not None
    with member:
        return member.read()


@pytest.mark.parametrize("kind", ["trial-memory", "trial-file", "job"])
@pytest.mark.parametrize("have_seat_results", [True, False])
def test_archives_include_declared_seat_logs_with_original_paths_and_bytes(
    tmp_path: Path, native_archive_job, kind: str, have_seat_results: bool
) -> None:
    job_dir, _, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    if not have_seat_results:
        trial.seat_results = None
    result_bytes = trial.model_dump_json(indent=2).encode() + b"\r\n"
    (trial_dir / "result.json").write_bytes(result_bytes)
    _write_multi_step_outputs(trial_dir)
    seat_logs = {
        "seats/support/agent/deepseek.jsonl": b'{"usage":null,"future":{"x":1}}\r\n',
        "seats/solver/agent/nested/empty.jsonl": b"",
        "seats/solver/agent/nested-raw.jsonl": b'{ "usage": {"tokens": 0} }\n',
    }
    _write_test_files(trial_dir, seat_logs)
    _write_test_files(
        trial_dir,
        {
            "seats/solver/environment/workspace": b"not an agent log",
            "seats/solver/world/private": b"not an agent log",
            "seats/support/verifier/reward.txt": b"not the root verifier result",
            "seats/undeclared/agent/log.jsonl": b"not a participant",
        },
    )
    retry_name = f"retry-attempt-{UUID(int=1)}.json"
    retry_bytes = b'{ "cost_usd": null, "unknown": 0 }\r\n'
    (job_dir / retry_name).write_bytes(retry_bytes)

    archive, prefix = _pack_archive(tmp_path, job_dir, trial_dir, kind)

    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        assert [
            name for name in tar.getnames() if name.startswith(prefix + "seats/")
        ] == [prefix + name for name in sorted(seat_logs)]
        for name, data in seat_logs.items():
            assert _read_regular_member(tar, prefix + name) == data
        assert _read_regular_member(tar, prefix + "result.json") == result_bytes
        for name in (
            "agent/trajectory.json",
            "verifier/reward.txt",
            "artifacts/output.log",
            "steps/implement/agent/trajectory.json",
        ):
            assert (
                _read_regular_member(tar, prefix + name)
                == (trial_dir / name).read_bytes()
            )
        if kind == "job":
            assert _read_regular_member(tar, f"{job_dir.name}/{retry_name}") == (
                retry_bytes
            )


@pytest.mark.parametrize("kind", ["trial-memory", "trial-file", "job"])
def test_legacy_archives_ignore_unconfigured_seat_tree(
    tmp_path: Path, kind: str
) -> None:
    trial = _make_trial_result()
    trial.seat_results = [SeatResult(seat="solver", under_test=True)]
    job_dir, _, _ = _write_job_dir(tmp_path, [trial])
    trial_dir = job_dir / trial.trial_name
    legacy, _ = _pack_archive(tmp_path, job_dir, trial_dir, kind)
    _write_test_files(trial_dir, {"seats/solver/agent/trace.jsonl": b"undeclared\n"})

    actual, _ = _pack_archive(tmp_path, job_dir, trial_dir, kind)

    assert gzip.decompress(actual) == gzip.decompress(legacy)


@pytest.mark.parametrize("kind", ["trial-file", "job"])
@pytest.mark.parametrize(
    "relative",
    [
        ".",
        "seats",
        "seats/solver",
        "seats/solver/agent",
        "seats/solver/agent/nested",
        "seats/solver/agent/nested/trace.jsonl",
    ],
)
def test_archives_do_not_follow_symlinks_in_seat_log_paths(
    tmp_path: Path, native_archive_job, kind: str, relative: str
) -> None:
    job_dir, _, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    _write_test_files(
        trial_dir, {"seats/solver/agent/nested/trace.jsonl": b"external bytes"}
    )
    path = trial_dir / relative
    outside = tmp_path / "outside-seat-tree"
    is_directory = path.is_dir()
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=is_directory)

    archive, prefix = _pack_archive(tmp_path, job_dir, trial_dir, kind)

    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        assert all(
            name != prefix + "seats" and not name.startswith(prefix + "seats/")
            for name in tar.getnames()
        )
        assert (
            _read_regular_member(tar, prefix + "result.json")
            == (trial_dir / "result.json").read_bytes()
        )


def test_job_archive_omits_seat_payload_below_symlinked_job_root(
    tmp_path: Path, native_archive_job
) -> None:
    job_dir, _, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    _write_test_files(trial_dir, {"seats/solver/agent/trace.jsonl": b"external bytes"})
    alias = tmp_path / "linked-job"
    alias.symlink_to(job_dir, target_is_directory=True)

    archive, prefix = _pack_archive(tmp_path, alias, alias / trial.trial_name, "job")

    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        assert all(
            name != prefix + "seats" and not name.startswith(prefix + "seats/")
            for name in tar.getnames()
        )
        assert (
            _read_regular_member(tar, prefix + "result.json")
            == (trial_dir / "result.json").read_bytes()
        )


@pytest.mark.parametrize("kind", ["trial-file", "job"])
@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
def test_archives_omit_seat_leaf_swapped_before_open(
    tmp_path: Path,
    native_archive_job,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    replacement: str,
) -> None:
    job_dir, _, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    relative = "seats/solver/agent/swap.jsonl"
    _write_test_files(
        trial_dir,
        {relative: b"old bytes", "seats/support/agent/keep.jsonl": b"keep"},
    )
    leaf = trial_dir / relative
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"do not follow")
    open_file = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path in (leaf.name, leaf):
            assert flags & os.O_NONBLOCK
            if not swapped:
                leaf.unlink()
                if replacement == "symlink":
                    leaf.symlink_to(outside)
                else:
                    os.mkfifo(leaf)
                swapped = True
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    archive, prefix = _pack_archive(tmp_path, job_dir, trial_dir, kind)

    assert swapped
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        assert prefix + relative not in tar.getnames()
        assert _read_regular_member(tar, prefix + "seats/support/agent/keep.jsonl") == (
            b"keep"
        )


@pytest.mark.parametrize("kind", ["trial-file", "job"])
def test_archives_propagate_seat_log_io_errors(
    tmp_path: Path, native_archive_job, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    job_dir, _, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    relative = "seats/support/agent/unreadable.jsonl"
    _write_test_files(trial_dir, {relative: b"cannot read this"})
    leaf = trial_dir / relative
    open_file = os.open

    def fail_seat_open(path, flags, *args, **kwargs):
        if path in (leaf.name, leaf):
            raise OSError(errno.EIO, "seat log read failed")
        return open_file(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_seat_open)

    with pytest.raises(OSError, match="seat log read failed"):
        _pack_archive(tmp_path, job_dir, trial_dir, kind)


@pytest.mark.asyncio
async def test_unsafe_only_seat_data_keeps_legacy_keys(
    native_archive_job, mock_uploader, archive_uploads, monkeypatch
) -> None:
    job_dir, job_result, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    agent_dir = trial_dir / "seats" / "solver" / "agent"
    agent_dir.mkdir(parents=True)
    fifo = agent_dir / "trace-fifo"
    os.mkfifo(fifo)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        with monkeypatch.context() as cwd:
            cwd.chdir(agent_dir)
            listener.bind("trace.sock")
    open_file = os.open
    open_path = Path.open

    def guard_fifo_open(path, flags, *args, **kwargs):
        if path in (fifo.name, fifo):
            assert flags & os.O_NONBLOCK
        return open_file(path, flags, *args, **kwargs)

    def guard_fifo_path_open(path, *args, **kwargs):
        assert path != fifo
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guard_fifo_open)
    monkeypatch.setattr(Path, "open", guard_fifo_path_open)

    result = await mock_uploader.upload_job(job_dir)

    assert (result.n_trials_uploaded, result.n_trials_failed) == (1, 0)
    revision = hashlib.sha256(str(trial.id).encode()).hexdigest()
    trial_key = f"trials/{trial.id}/trial.tar.gz"
    job_key = f"jobs/{job_result.id}/job-{revision}.tar.gz"
    assert set(archive_uploads) == {trial_key, job_key}
    assert (
        mock_uploader.db.finalize_trial_artifacts.await_args.kwargs["archive_path"]
        == trial_key
    )
    assert mock_uploader.db.finalize_job.await_args.kwargs["archive_path"] == job_key
    for data, _ in archive_uploads.values():
        with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tar:
            assert not any("seats" in Path(name).parts for name in tar.getnames())


async def _stream_trial(
    uploader: Uploader, job_dir: Path, job_result: JobResult, trial: TrialResult
) -> TrialUploadResult:
    trial_dir = job_dir / trial.trial_name
    return await uploader.upload_single_trial(
        trial_result=trial,
        trial_dir=trial_dir,
        trial_lock=_read_trial_lock_for_upload(trial_dir),
        job_id=job_result.id,
        agent_cache={},
        model_cache={},
    )


@pytest.fixture
def finalized_trial_row(native_archive_job, mock_uploader, monkeypatch):
    _, _, trial = native_archive_job
    row = {
        "id": str(trial.id),
        "archive_path": f"trials/{trial.id}/trial.tar.gz",
        "trajectory_path": f"trials/{trial.id}/trajectory.json",
    }

    def archive_state() -> dict[str, str]:
        return {"id": row["id"], "archive_path": row["archive_path"]}

    mock_uploader.db.get_trial.side_effect = lambda _: archive_state()
    mock_uploader.db.list_trials_for_job.side_effect = lambda _: [archive_state()]
    mock_uploader.db.get_job_visibility.return_value = "private"
    client = MagicMock()
    table = client.table.return_value
    update = table.update.return_value

    async def execute() -> MagicMock:
        assert client.table.call_args.args == ("trial",)
        assert update.eq.call_args.args == ("id", row["id"])
        row.update(table.update.call_args.args[0])
        return MagicMock(data=[{"id": row["id"]}])

    update.eq.return_value.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(
        "harbor.upload.db_client.create_authenticated_client",
        AsyncMock(return_value=client),
    )
    mock_uploader.db.finalize_trial_archive_migration.side_effect = (
        UploadDB().finalize_trial_archive_migration
    )
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["batch", "stream"])
async def test_finalized_trial_upgrades_after_success_without_touching_trajectory(
    native_archive_job, mock_uploader, finalized_trial_row, mode: str
) -> None:
    job_dir, job_result, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    old = dict(finalized_trial_row)
    old_cache = trial_dir / ".harbor-upload" / "trial.tar.gz"
    old_bytes = _create_trial_archive(trial_dir)
    _seed_archive_cache(old_cache, old_bytes)
    (trial_dir / "agent" / "trajectory.json").unlink()
    relative = "seats/support/agent/trace.jsonl"
    log_bytes = b'{"usage":null}\r\n'
    _write_test_files(trial_dir, {relative: log_bytes})
    key = f"trials/{trial.id}/trial-seats-v1.tar.gz"
    local = old_cache.with_name("trial-seats-v1.tar.gz")
    sidecar = local.with_suffix(".gz.tus-url")
    revision = hashlib.sha256(str(trial.id).encode()).hexdigest()
    mock_uploader.db.get_job.return_value = {
        "archive_path": f"jobs/{job_result.id}/job-{revision}-seats-v1.tar.gz"
    }
    uploads: list[bytes] = []

    async def upload_archive(path: Path, archive_key: str) -> None:
        assert finalized_trial_row == old
        assert (path, archive_key) == (local, key)
        uploads.append(path.read_bytes())
        if len(uploads) == 1:
            assert not sidecar.exists()
            sidecar.write_text("https://uploads.example/new-trial")
            raise RuntimeError("seat archive upload failed")
        assert sidecar.read_text() == "https://uploads.example/new-trial"

    async def upload() -> TrialUploadResult:
        if mode == "batch":
            result = await mock_uploader.upload_job(job_dir)
            (trial_upload,) = result.trial_results
            return trial_upload
        return await _stream_trial(mock_uploader, job_dir, job_result, trial)

    mock_uploader.storage.upload_resumable_file.side_effect = upload_archive
    failure = await upload()

    assert failure.error == "RuntimeError: seat archive upload failed"
    assert finalized_trial_row == old
    assert local.exists() and sidecar.exists()

    success = await upload()

    assert success.error is None and not success.skipped
    assert finalized_trial_row == {**old, "archive_path": key}
    already_current = await upload()
    assert already_current.skipped and already_current.error is None
    assert len(uploads) == 2
    assert uploads[0] == uploads[1]
    with tarfile.open(fileobj=BytesIO(uploads[1]), mode="r:gz") as tar:
        assert _read_regular_member(tar, relative) == log_bytes
    assert old_cache.read_bytes() == old_bytes
    assert old_cache.with_suffix(".gz.tus-url").read_text() == (
        "https://uploads.example/upload-id"
    )
    assert not local.exists() and not sidecar.exists()
    mock_uploader.db.insert_trial.assert_not_awaited()
    mock_uploader.db.finalize_trial_artifacts.assert_not_awaited()
    mock_uploader.db.finalize_trial_archive_migration.assert_awaited_once()
    mock_uploader.db.finalize_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_sweep_upgrades_retry_archive_without_reusing_legacy_cache(
    native_archive_job, mock_uploader, finalized_trial_row, archive_uploads
) -> None:
    job_dir, _, trial = native_archive_job
    trial_dir = job_dir / trial.trial_name
    finalized_trial_row["archive_path"] = f"trials/{trial.id}/trial-seats-v1.tar.gz"
    retry_name = f"retry-attempt-{UUID(int=1)}.json"
    retry_bytes = b'{ "cost_usd": null, "unknown": 0 }\r\n'
    (job_dir / retry_name).write_bytes(retry_bytes)
    await mock_uploader.upload_job(job_dir)
    legacy_key = mock_uploader.db.finalize_job.await_args.kwargs["archive_path"]
    key = legacy_key.removesuffix(".tar.gz") + "-seats-v1.tar.gz"
    legacy_bytes, _ = archive_uploads.pop(legacy_key)
    legacy = job_dir / ".harbor-upload" / Path(legacy_key).name
    _seed_archive_cache(legacy, legacy_bytes)
    mock_uploader.db.get_job.return_value = {"archive_path": legacy_key}
    mock_uploader.db.finalize_job.reset_mock()
    relative = "seats/support/agent/empty.jsonl"
    _write_test_files(trial_dir, {relative: b""})

    failed_upload = AsyncMock(side_effect=RuntimeError("job archive upload failed"))
    with (
        patch.object(mock_uploader.storage, "upload_resumable_file", failed_upload),
        pytest.raises(RuntimeError, match="job archive upload failed"),
    ):
        await mock_uploader.upload_job(job_dir)

    failed_upload.assert_awaited_once_with(legacy.with_name(Path(key).name), key)
    mock_uploader.db.finalize_job.assert_not_awaited()

    async def replace_seat_log(*args, **kwargs) -> None:
        (trial_dir / relative).unlink()
        _write_test_files(trial_dir, {"seats/support/agent/replacement": b"new bytes"})

    mock_uploader.storage.upload_large_file.side_effect = replace_seat_log
    result = await mock_uploader.upload_job(job_dir)

    assert (result.n_trials_skipped, result.n_trials_failed) == (1, 0)
    mock_uploader.db.finalize_job.assert_awaited_once()
    assert mock_uploader.db.finalize_job.await_args.kwargs["archive_path"] == key
    assert set(archive_uploads) == {key}
    archive, url = archive_uploads[key]
    assert url is None
    prefix = f"{job_dir.name}/{trial_dir.name}/"
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as tar:
        assert _read_regular_member(tar, prefix + relative) == b""
        assert prefix + "seats/support/agent/replacement" not in tar.getnames()
        assert _read_regular_member(tar, f"{job_dir.name}/{retry_name}") == retry_bytes
    assert legacy.read_bytes() == legacy_bytes
    assert legacy.with_suffix(".gz.tus-url").read_text() == (
        "https://uploads.example/upload-id"
    )

    mock_uploader.db.get_job.return_value = {"archive_path": key}
    mock_uploader.db.finalize_job.reset_mock()
    mock_uploader.storage.upload_resumable_file.reset_mock()
    mock_uploader.storage.upload_large_file.side_effect = None
    await mock_uploader.upload_job(job_dir)
    mock_uploader.storage.upload_resumable_file.assert_not_awaited()
    mock_uploader.db.finalize_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalized_seat_archives_do_not_downgrade_without_local_payload(
    native_archive_job, mock_uploader, finalized_trial_row
) -> None:
    job_dir, job_result, trial = native_archive_job
    finalized_trial_row["archive_path"] = f"trials/{trial.id}/trial-seats-v1.tar.gz"
    old = dict(finalized_trial_row)
    old_revision = "a" * 64
    mock_uploader.db.get_job.return_value = {
        "archive_path": f"jobs/{job_result.id}/job-{old_revision}-seats-v1.tar.gz"
    }

    single = await _stream_trial(mock_uploader, job_dir, job_result, trial)
    job = await mock_uploader.upload_job(job_dir)
    await mock_uploader.finalize_job(
        job_dir,
        job_result=job_result,
        job_config=JobConfig.model_validate_json((job_dir / "config.json").read_text()),
    )

    assert single.skipped and single.error is None
    assert (job.n_trials_uploaded, job.n_trials_skipped, job.n_trials_failed) == (
        0,
        1,
        0,
    )
    assert finalized_trial_row == old
    mock_uploader.storage.upload_resumable_file.assert_not_awaited()
    mock_uploader.db.finalize_trial_artifacts.assert_not_awaited()
    mock_uploader.db.finalize_trial_archive_migration.assert_not_awaited()
    mock_uploader.db.finalize_job.assert_not_awaited()
