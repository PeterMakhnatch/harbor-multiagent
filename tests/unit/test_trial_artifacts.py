import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from harbor.environments.base import ExecResult
from harbor.models.task.config import TaskOS
from harbor.models.trial.artifact_manifest import ArtifactManifestEntry
from harbor.models.trial.config import ArtifactConfig
from harbor.models.trial.paths import EnvironmentPaths
from harbor.trial.artifact_handler import ArtifactHandler
from tests.unit.artifact_uploads import record_uploads

ENV_ARTIFACTS_DIR = EnvironmentPaths().artifacts_dir
WINDOWS_ARTIFACTS_DIR = EnvironmentPaths.for_windows().artifacts_dir
# Canonical host location of the main service's convention publish dir.
CONVENTION_HOST_PARTS = ("logs", "artifacts")


def _handler(
    artifacts: list[str | ArtifactConfig],
) -> ArtifactHandler:
    return ArtifactHandler(
        artifacts=artifacts,
        logger=logging.getLogger(__name__),
    )


def _mock_env(*, mounted: bool, is_dir: bool = False) -> AsyncMock:
    environment = AsyncMock()
    environment.os = TaskOS.LINUX
    environment.auto_mounting = mounted
    environment.is_path_mounted = MagicMock(return_value=mounted)
    environment.service_is_dir = AsyncMock(return_value=is_dir)
    environment.service_download_file = AsyncMock()
    environment.service_download_dir = AsyncMock()
    environment.service_download_dir_with_exclusions = AsyncMock()
    environment.upload_file = AsyncMock()
    environment.upload_dir = AsyncMock()
    environment.empty_dirs = AsyncMock()
    environment.ensure_dirs = AsyncMock()
    return environment


def _convention_host_dir(artifacts_dir: Path) -> Path:
    return artifacts_dir.joinpath(*CONVENTION_HOST_PARTS)


# ---------------------------------------------------------------------------
# Download: host placement
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_downloads_configured_file_to_destination(tmp_path: Path) -> None:
    """Destination-set entries land at their relative path under artifacts/."""
    environment = _mock_env(mounted=True)
    handler = _handler(
        [
            ArtifactConfig(
                source="/tmp/answer.json",
                destination="answers/final.json",
            )
        ],
    )

    artifacts_dir = tmp_path / "artifacts"

    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_file.assert_awaited_once_with(
        source_path="/tmp/answer.json",
        target_path=artifacts_dir / "answers" / "final.json",
        service=None,
    )
    assert manifest.entries[1].source == "/tmp/answer.json"
    assert manifest.entries[1].destination == "artifacts/answers/final.json"
    assert manifest.entries[1].type == "file"
    assert manifest.entries[1].status == "ok"
    assert manifest.entries[1].service is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_downloads_source_derived_file_to_flat_base(
    tmp_path: Path,
) -> None:
    """Entries without a destination mirror their source path under the flat artifacts/ base."""
    environment = _mock_env(mounted=True)
    handler = _handler(["/app/build/output.csv"])

    artifacts_dir = tmp_path / "artifacts"

    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_file.assert_awaited_once_with(
        source_path="/app/build/output.csv",
        target_path=artifacts_dir / "app" / "build" / "output.csv",
        service=None,
    )
    assert manifest.entries[1].destination == "artifacts/app/build/output.csv"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_downloads_sidecar_artifact_from_service(tmp_path: Path) -> None:
    """Sidecar entries are pulled from the named service's filesystem."""
    environment = _mock_env(mounted=True)
    handler = _handler(
        [ArtifactConfig(source="/var/log/requests.log", service="api")],
    )

    artifacts_dir = tmp_path / "artifacts"

    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_file.assert_awaited_once_with(
        source_path="/var/log/requests.log",
        target_path=artifacts_dir / "var" / "log" / "requests.log",
        service="api",
    )
    sidecar_entries = [entry for entry in manifest.entries if entry.service == "api"]
    assert len(sidecar_entries) == 1
    assert sidecar_entries[0].status == "ok"
    assert sidecar_entries[0].destination == "artifacts/var/log/requests.log"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_downloads_configured_directory_with_exclude(tmp_path: Path) -> None:
    environment = _mock_env(mounted=True, is_dir=True)
    handler = _handler(
        [
            ArtifactConfig(
                source="/app/my dir",
                exclude=["*.pyc", "helper files", "$(touch hacked)"],
            )
        ],
    )

    artifacts_dir = tmp_path / "artifacts"

    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_dir.assert_not_awaited()
    environment.service_download_dir_with_exclusions.assert_awaited_once_with(
        source_dir="/app/my dir",
        target_dir=artifacts_dir / "app" / "my dir",
        exclude=["*.pyc", "helper files", "$(touch hacked)"],
        service=None,
    )
    entry = next(entry for entry in manifest.entries if entry.source == "/app/my dir")
    assert entry.exclude == ["*.pyc", "helper files", "$(touch hacked)"]
    disk_manifest = json.loads((artifacts_dir / "manifest.json").read_text())
    disk_entry = next(
        entry for entry in disk_manifest if entry["source"] == "/app/my dir"
    )
    assert disk_entry["exclude"] == ["*.pyc", "helper files", "$(touch hacked)"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_implicit_artifacts_dir_downloads_to_convention_host_dir(
    tmp_path: Path,
) -> None:
    """The auto-injected convention entry lands at artifacts/logs/artifacts/."""
    environment = _mock_env(mounted=False, is_dir=True)
    handler = _handler([])

    artifacts_dir = tmp_path / "artifacts"

    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_dir.assert_awaited_once_with(
        source_dir="/logs/artifacts",
        target_dir=_convention_host_dir(artifacts_dir),
        service=None,
    )
    assert manifest.entries[0].source == "/logs/artifacts"
    assert manifest.entries[0].destination == "artifacts/logs/artifacts"
    disk_manifest = json.loads((artifacts_dir / "manifest.json").read_text())
    assert disk_manifest[0]["source"] == "/logs/artifacts"
    assert disk_manifest[0]["destination"] == "artifacts/logs/artifacts"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mounted_convention_dir_skips_download(tmp_path: Path) -> None:
    """On mounted envs the convention dir is already on the host via bind mount."""
    environment = _mock_env(mounted=True, is_dir=True)
    handler = _handler([])

    artifacts_dir = tmp_path / "artifacts"
    convention_dir = _convention_host_dir(artifacts_dir)
    convention_dir.mkdir(parents=True)
    (convention_dir / "result.txt").write_text("ok")

    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_dir.assert_not_awaited()
    assert manifest.entries[0].status == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_artifacts_dir_with_exclude_downloads_with_exclusions(
    tmp_path: Path,
) -> None:
    environment = _mock_env(mounted=False, is_dir=True)
    handler = _handler(
        [ArtifactConfig(source="/logs/artifacts", exclude=["*.pt"])],
    )

    artifacts_dir = tmp_path / "artifacts"

    await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_download_dir.assert_not_awaited()
    environment.service_download_dir_with_exclusions.assert_awaited_once_with(
        source_dir="/logs/artifacts",
        target_dir=_convention_host_dir(artifacts_dir),
        exclude=["*.pt"],
        service=None,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_download_failure_records_failed_manifest_entry(
    tmp_path: Path,
) -> None:
    environment = _mock_env(mounted=True)
    environment.service_download_file = AsyncMock(
        side_effect=RuntimeError("service not found")
    )
    handler = _handler(
        [ArtifactConfig(source="/data/dump.sql", service="db")],
    )

    manifest = await handler.download_artifacts(
        environment,
        tmp_path / "artifacts",
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    failed = [entry for entry in manifest.entries if entry.status == "failed"]
    assert len(failed) == 1
    assert failed[0].source == "/data/dump.sql"
    assert failed[0].service == "db"
    assert failed[0].phase is None
    assert failed[0].source_environment == "agent"


def test_legacy_artifact_manifest_entry_has_unknown_phase() -> None:
    entry = ArtifactManifestEntry.model_validate(
        {
            "source": "/data/dump.sql",
            "destination": "artifacts/data/dump.sql",
            "type": "file",
            "status": "ok",
        }
    )

    assert entry.status == "ok"
    assert entry.phase is None
    assert entry.source_environment is None


async def test_retry_downloads_duplicate_file_once_and_excludes_live_paths(
    tmp_path: Path,
) -> None:
    output = "/tmp/report.txt"
    seat_output = "/tmp/seat.txt"
    seat_log = "/logs/seats/p1/agent/oracle.txt"
    partial_dir = "/tmp/partial"
    handler = _handler(
        [
            output,
            output,
            ArtifactConfig(source=seat_output, service="p1"),
            seat_log,
            partial_dir,
        ]
    )
    environment = _mock_env(mounted=True)
    environment.service_is_dir.side_effect = lambda path, **_: path == partial_dir
    environment.service_download_file.side_effect = FileNotFoundError

    async def partial_download(*, target_dir: Path, **_) -> None:
        (target_dir / "stale.txt").write_text("partial old snapshot")
        raise OSError("interrupted")

    async def download(source: str, target: Path) -> None:
        assert source == output
        target.write_text("report")

    environment.service_download_dir.side_effect = partial_download
    environment.exec.return_value = ExecResult(return_code=0)
    environment.download_file.side_effect = download
    artifacts = tmp_path / "artifacts"
    warnings: list[str] = []
    await handler.download_artifacts(
        environment,
        artifacts,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        pre_verifier=True,
    )
    await handler.retry_failed_artifacts(
        environment,
        artifacts,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        warn=warnings.append,
    )

    entries = json.loads((artifacts / "manifest.json").read_text())
    assert entries[0]["phase"] is None  # The mounted directory is not a snapshot.
    assert entries[1] == entries[2]
    assert entries[1]["phase"] == "post_verifier"
    assert entries[1]["status"] == "ok"
    assert entries[1]["source_environment"] == "agent"
    assert [entry["status"] for entry in entries[3:]] == ["failed"] * 3
    assert len(warnings) == 3
    assert all(
        any(path in message for message in warnings)
        for path in (seat_output, seat_log, partial_dir)
    )
    environment.download_file.assert_awaited_once()
    assert (artifacts / "tmp/report.txt").read_text() == "report"
    assert (artifacts / "tmp/partial/stale.txt").read_text() == "partial old snapshot"


@pytest.mark.parametrize(
    "source",
    ["//tests/./new.txt", "///logs//seats/p1/new.txt", "//logs/agent/new.txt"],
)
async def test_retry_normalizes_reserved_paths(tmp_path: Path, source: str) -> None:
    environment = _mock_env(mounted=True)
    environment.service_download_file.side_effect = FileNotFoundError
    handler = _handler([ArtifactConfig(source=source, destination="disputed.txt")])
    artifacts = tmp_path / "artifacts"
    await handler.download_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR
    )

    warning = MagicMock()
    await handler.retry_failed_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR, warn=warning
    )

    environment.exec.assert_not_awaited()
    environment.download_file.assert_not_awaited()
    warning.assert_called_once()


async def test_retry_uses_checked_path_for_transfer(tmp_path: Path) -> None:
    environment = _mock_env(mounted=True)
    environment.service_download_file.side_effect = FileNotFoundError
    environment.exec.return_value = ExecResult(return_code=0)
    handler = _handler(
        [ArtifactConfig(source="//tmp/./report.txt", destination="report.txt")]
    )
    artifacts = tmp_path / "artifacts"
    await handler.download_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR
    )

    async def download(source: str, target: Path) -> None:
        assert source == "/tmp/report.txt"
        target.write_text("report")

    environment.download_file.side_effect = download
    await handler.retry_failed_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR, warn=MagicMock()
    )

    environment.exec.assert_awaited_once()
    assert (artifacts / "report.txt").read_text() == "report"


async def test_retry_stops_before_a_dereferencing_provider_copies(
    tmp_path: Path,
) -> None:
    environment = _mock_env(mounted=True)
    environment.service_download_file.side_effect = FileNotFoundError
    environment.exec.return_value = ExecResult(return_code=1)
    handler = _handler(["/tmp/report-link.txt"])
    artifacts = tmp_path / "artifacts"
    await handler.download_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR
    )

    async def dereference(_source: str, target: Path) -> None:
        target.write_text("reserved bytes")

    environment.download_file.side_effect = dereference
    warning = MagicMock()
    await handler.retry_failed_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR, warn=warning
    )

    environment.exec.assert_awaited_once()
    environment.download_file.assert_not_awaited()
    warning.assert_called_once()
    assert not (artifacts / "tmp/report-link.txt").exists()


async def test_cancelled_retry_does_not_publish_partial_file(tmp_path: Path) -> None:
    environment = _mock_env(mounted=True)
    environment.service_download_file.side_effect = FileNotFoundError
    environment.exec.return_value = ExecResult(return_code=0)
    handler = _handler(["/tmp/report.txt"])
    artifacts = tmp_path / "artifacts"
    await handler.download_artifacts(
        environment, artifacts, source_artifacts_dir=ENV_ARTIFACTS_DIR
    )
    previous = (artifacts / "manifest.json").read_bytes()
    started = asyncio.Event()

    async def download(_, target: Path) -> None:
        target.write_text("partial")
        started.set()
        await asyncio.Event().wait()

    environment.download_file.side_effect = download
    task = asyncio.create_task(
        handler.retry_failed_artifacts(
            environment,
            artifacts,
            source_artifacts_dir=ENV_ARTIFACTS_DIR,
            warn=MagicMock(),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (artifacts / "tmp/report.txt").exists()
    assert (artifacts / "manifest.json").read_bytes() == previous
    assert not list(tmp_path.glob(".artifact-retry-*"))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("probe_raises", [False, True])
async def test_linux_artifact_probe_retains_root_and_fallback(
    tmp_path: Path, probe_raises: bool
) -> None:
    environment = _mock_env(mounted=False, is_dir=True)
    if probe_raises:
        environment.service_is_dir.side_effect = RuntimeError("probe failed")

    manifest = await _handler([]).download_artifacts(
        environment,
        tmp_path / "artifacts",
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.service_is_dir.assert_awaited_once_with(
        "/logs/artifacts", service=None, user="root"
    )
    environment.service_download_dir.assert_awaited_once()
    environment.service_download_file.assert_not_awaited()
    assert manifest.entries[0].type == "directory"
    assert manifest.entries[0].status == "ok"


# ---------------------------------------------------------------------------
# Download: per-service collection passes
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_services_filter_limits_collection_pass(tmp_path: Path) -> None:
    environment = _mock_env(mounted=False, is_dir=False)
    handler = _handler(
        [
            "/app/main-output.txt",
            ArtifactConfig(source="/data/dump.sql", service="db"),
        ],
    )

    artifacts_dir = tmp_path / "artifacts"

    main_manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        services={"main"},
    )

    main_sources = {entry.source for entry in main_manifest.entries}
    assert main_sources == {"/logs/artifacts", "/app/main-output.txt"}

    sidecar_manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        services={"db"},
    )

    # The on-disk manifest accumulates entries across passes.
    assert {entry.source for entry in sidecar_manifest.entries} == {
        "/logs/artifacts",
        "/app/main-output.txt",
        "/data/dump.sql",
    }
    disk_manifest = json.loads((artifacts_dir / "manifest.json").read_text())
    assert len(disk_manifest) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claims_persist_across_main_and_sidecar_phases(
    tmp_path: Path,
) -> None:
    """Within one collection pass, a sidecar entry nesting with an already-
    collected host path is skipped rather than overwriting it."""
    environment = _mock_env(mounted=False)
    handler = _handler(
        [
            "/app/logs/result.json",
            ArtifactConfig(source="/app/logs", service="api"),
        ],
    )

    artifacts_dir = tmp_path / "artifacts"

    handler.begin_collection()
    await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        services={"main"},
    )
    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        services={"api"},
    )

    entry = next(e for e in manifest.entries if e.source == "/app/logs")
    assert entry.status == "skipped"
    assert entry.service == "api"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_begin_collection_resets_claims_between_passes(
    tmp_path: Path,
) -> None:
    """Claims from a prior pass must not skip later entries: multi-step trials
    vacate the shared host artifacts dir between steps, so step N's claims no
    longer protect real content when step N+1 collects."""
    environment = _mock_env(mounted=False)
    handler = _handler([])

    artifacts_dir = tmp_path / "artifacts"

    handler.begin_collection()
    await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        artifacts=["/app/logs"],
    )

    handler.begin_collection()
    manifest = await handler.download_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        artifacts=["/app/logs/result.json"],
    )

    entry = next(e for e in manifest.entries if e.source == "/app/logs/result.json")
    assert entry.status == "ok"


@pytest.mark.unit
def test_sidecar_services_helper() -> None:
    handler = _handler(
        [
            "/app/output.txt",
            ArtifactConfig(source="/data/dump.sql", service="db"),
            ArtifactConfig(source="/var/log/api.log", service="api"),
        ],
    )

    assert handler.sidecar_services() == {"db", "api"}
    assert handler.sidecar_services([ArtifactConfig(source="/x", service="cache")]) == {
        "db",
        "api",
        "cache",
    }


# ---------------------------------------------------------------------------
# Upload into the verifier environment
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uploads_convention_dir_to_verifier_convention_dir(
    tmp_path: Path,
) -> None:
    environment = _mock_env(mounted=False)
    uploads = record_uploads(environment)
    handler = _handler([])
    artifacts_dir = tmp_path / "artifacts"
    convention_dir = _convention_host_dir(artifacts_dir)
    convention_dir.mkdir(parents=True)
    (convention_dir / "result.txt").write_text("ok")

    await handler.upload_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.empty_dirs.assert_awaited_once_with(["/logs/artifacts"], chmod=True)
    assert uploads == {"/logs/artifacts": {"result.txt": "ok"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uploads_configured_file_back_to_source_path(tmp_path: Path) -> None:
    """Main entries re-materialize at their original source path (no translation)."""
    environment = _mock_env(mounted=False)
    handler = _handler(
        [
            ArtifactConfig(
                source="/tmp/answer.json",
                destination="answers/final.json",
            )
        ],
    )
    uploads = record_uploads(environment)
    artifacts_dir = tmp_path / "artifacts"
    host_file = artifacts_dir / "answers" / "final.json"
    host_file.parent.mkdir(parents=True)
    host_file.write_text("ok")

    await handler.upload_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    # Parent dirs are created so verifier images need not pre-create them.
    environment.ensure_dirs.assert_awaited_once_with(["/tmp"], chmod=True)
    assert uploads == {"/tmp/answer.json": "ok"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uploads_sidecar_artifact_to_original_source_path(
    tmp_path: Path,
) -> None:
    """Sidecar evidence re-materializes at its original path in the verifier."""
    environment = _mock_env(mounted=False)
    handler = _handler(
        [ArtifactConfig(source="/data/dump.sql", service="postgres")],
    )
    uploads = record_uploads(environment)
    artifacts_dir = tmp_path / "artifacts"
    host_file = artifacts_dir / "data" / "dump.sql"
    host_file.parent.mkdir(parents=True)
    host_file.write_text("SELECT 1;")

    await handler.upload_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.ensure_dirs.assert_awaited_once_with(["/data"], chmod=True)
    assert uploads == {"/data/dump.sql": "SELECT 1;"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uploads_configured_directory_from_destination_to_source(
    tmp_path: Path,
) -> None:
    environment = _mock_env(mounted=False)
    handler = _handler(
        [ArtifactConfig(source="/tmp/output", destination="out")],
    )
    uploads = record_uploads(environment)
    artifacts_dir = tmp_path / "artifacts"
    target = artifacts_dir / "out"
    target.mkdir(parents=True)
    (target / "result.txt").write_text("ok")

    await handler.upload_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.empty_dirs.assert_any_await(["/tmp/output"], chmod=True)
    assert uploads["/tmp/output"] == {"result.txt": "ok"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_upload_skips_missing_host_paths(tmp_path: Path) -> None:
    environment = _mock_env(mounted=False)
    handler = _handler(
        [ArtifactConfig(source="/tmp/missing.txt", destination="missing.txt")],
    )

    await handler.upload_artifacts(
        environment,
        tmp_path / "artifacts",
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    environment.upload_file.assert_not_awaited()
    environment.upload_dir.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uploads_convention_dir_to_windows_target_convention(
    tmp_path: Path,
) -> None:
    environment = _mock_env(mounted=False)
    uploads = record_uploads(environment)
    handler = _handler([])
    artifacts_dir = tmp_path / "artifacts"
    convention_dir = _convention_host_dir(artifacts_dir)
    convention_dir.mkdir(parents=True)
    (convention_dir / "result.txt").write_text("ok")

    await handler.upload_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=WINDOWS_ARTIFACTS_DIR,
    )

    windows_artifacts_dir = WINDOWS_ARTIFACTS_DIR.as_posix()
    environment.empty_dirs.assert_awaited_once_with([windows_artifacts_dir], chmod=True)
    assert uploads == {windows_artifacts_dir: {"result.txt": "ok"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manifest_is_never_uploaded_to_verifier(tmp_path: Path) -> None:
    """manifest.json lives outside every entry's host path, so it never leaks."""
    environment = _mock_env(mounted=False)
    uploads = record_uploads(environment)
    handler = _handler([])
    artifacts_dir = tmp_path / "artifacts"
    convention_dir = _convention_host_dir(artifacts_dir)
    convention_dir.mkdir(parents=True)
    (convention_dir / "result.txt").write_text("ok")
    (artifacts_dir / "manifest.json").write_text("[]")

    await handler.upload_artifacts(
        environment,
        artifacts_dir,
        source_artifacts_dir=ENV_ARTIFACTS_DIR,
        target_artifacts_dir=ENV_ARTIFACTS_DIR,
    )

    assert uploads == {"/logs/artifacts": {"result.txt": "ok"}}


# ---------------------------------------------------------------------------
# Directory moves (multi-step archiving)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_move_dir_contents_moves_contents_and_leaves_source_empty(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "file.txt").write_text("ok")
    (src / "nested").mkdir()
    (src / "nested" / "value.txt").write_text("nested")

    ArtifactHandler.move_dir_contents(src, dst)

    assert not any(src.iterdir())
    assert (dst / "file.txt").read_text() == "ok"
    assert (dst / "nested" / "value.txt").read_text() == "nested"


@pytest.mark.unit
def test_move_dir_contents_preserving_keeps_mount_chain_dirs(
    tmp_path: Path,
) -> None:
    """Preserved dirs keep their inode (bind-mount safety); contents still move."""
    src = tmp_path / "artifacts"
    dst = tmp_path / "archived"
    mount_dir = src / "logs" / "artifacts"
    mount_dir.mkdir(parents=True)
    (mount_dir / "published.txt").write_text("ok")
    sidecar_dir = src / "data"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "dump.sql").write_text("SELECT 1;")
    (src / "manifest.json").write_text("[]")

    mount_inode_before = mount_dir.stat().st_ino

    ArtifactHandler.move_dir_contents_preserving(src, dst, preserve_dirs=[mount_dir])

    # The mount chain dirs still exist at their original paths (same inode).
    assert mount_dir.exists()
    assert mount_dir.stat().st_ino == mount_inode_before
    assert not any(mount_dir.iterdir())
    # Their contents (and everything else) moved to the destination.
    assert (dst / "logs" / "artifacts" / "published.txt").read_text() == "ok"
    assert (dst / "data" / "dump.sql").read_text() == "SELECT 1;"
    assert (dst / "manifest.json").read_text() == "[]"
    # Non-preserved sidecar dirs are moved wholesale.
    assert not sidecar_dir.exists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first, second, warns, skipped",
    [
        ({"source": "/app"}, {"source": "/app/result"}, False, True),
        ({"source": "/app/result"}, {"source": "/app"}, True, True),
        ({"source": "/app"}, {"source": "/app", "service": "main"}, False, False),
        ({"source": "/app"}, {"source": "/app", "service": "api"}, True, True),
        (
            {"source": "/app", "service": "db"},
            {"source": "/app", "service": "api"},
            True,
            True,
        ),
        ({"source": "/app"}, {"source": "/app/result", "service": "api"}, True, True),
        (
            {"source": "/app", "service": "db"},
            {"source": "/app/result", "service": "api"},
            True,
            True,
        ),
        (
            {"source": "/app/result", "service": "db"},
            {"source": "/app", "service": "api"},
            True,
            True,
        ),
        (
            {"source": "/app", "exclude": ["*.patch"]},
            {"source": "/app/model.patch"},
            True,
            True,
        ),
        (
            {"source": "/app", "destination": "out"},
            {"source": "/app/result", "destination": "out/result"},
            False,
            True,
        ),
        (
            {"source": "/app", "destination": "out"},
            {"source": "/app/result", "destination": "out"},
            True,
            True,
        ),
        (
            {"source": "/app", "destination": "out"},
            {"source": "/app", "destination": "out/nested"},
            True,
            True,
        ),
        ({"source": "/app"}, {"source": "/other", "destination": "app"}, True, True),
        (
            {"source": "/app", "service": "db"},
            {"source": "/app", "service": "api", "destination": "api"},
            False,
            False,
        ),
        (
            {"source": "/logs/artifacts"},
            {"source": "/logs/artifacts/model.patch"},
            False,
            True,
        ),
    ],
)
async def test_artifact_collision_policy(
    tmp_path, caplog, first, second, warns, skipped
):
    handler = _handler([ArtifactConfig(**first), ArtifactConfig(**second)])
    with caplog.at_level(logging.WARNING):
        manifest = await handler.download_artifacts(
            _mock_env(mounted=False, is_dir=True),
            tmp_path / "artifacts",
            source_artifacts_dir=ENV_ARTIFACTS_DIR,
        )
    assert ("Artifact collision" in caplog.text) == warns
    assert (manifest.entries[-1].status == "skipped") == skipped
