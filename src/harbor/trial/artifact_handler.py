import asyncio
import json
import logging
import posixpath
import shlex
import shutil
import tempfile
from collections.abc import Callable, Collection, Sequence
from pathlib import Path, PurePath, PurePosixPath

from harbor.constants import MAIN_SERVICE_NAME
from harbor.environments.base import BaseEnvironment, EnvironmentPath
from harbor.environments.tar_transfer import copy_via_tar
from harbor.models.task.artifacts import (
    effective_artifact_service,
    is_convention_entry,
    artifact_is_covered_by,
    source_host_path,
    with_convention_entry,
)
from harbor.models.task.config import TaskOS
from harbor.models.trial.artifact_manifest import (
    ArtifactManifest,
    ArtifactManifestEntry,
)
from harbor.models.trial.config import ArtifactConfig
from harbor.models.trial.paths import EnvironmentPaths
from harbor.utils.path_safety import directory_move_plan, validate_output_path

_MANIFEST_FILENAME = "manifest.json"


def artifact_host_path(artifacts_dir: Path, artifact: ArtifactConfig) -> Path:
    """Canonical host location of an entry under the artifacts dir.

    Entries with an explicit destination land at that (relative) path;
    all other entries — regardless of service — mirror their absolute
    source path directly under the shared ``artifacts/`` base dir
    (e.g. ``/var/log/x`` → ``artifacts/var/log/x``).
    """
    if artifact.destination:
        return artifacts_dir / _relative_host_destination(artifact.destination)

    return source_host_path(artifacts_dir, artifact.source)


def _relative_host_destination(destination: str) -> Path:
    destination_path = PurePosixPath(destination)
    parts = [part for part in destination_path.parts if part not in ("", "/", "..")]
    return Path(*parts) if parts else Path(".")


class ArtifactHandler:
    """Collects artifacts from agent environments and re-materializes them in
    separate verifier environments.

    Host layout (under the trial's ``artifacts/`` dir) — a single flat base dir
    shared by every compose service:

    - ``<abs source path>`` — source-derived entries from ANY service, mirrored
      directly under ``artifacts/`` (no per-service subtree). The agent's
      conventional publish dir lands at ``artifacts/logs/artifacts/``.
    - ``<destination>`` — entries with an explicit (relative) destination.
    - ``manifest.json`` — record of every collection attempt.

    Because services share the base dir, two services that export the same
    source path collide on the host; collection keeps the first and logs a
    warning for the rest (it never overwrites).

    Verifier-side placement never depends on ``destination``: every entry
    re-materializes at its original ``source`` path ("no translation"), and
    the convention dir maps to the verifier's convention dir.
    """

    def __init__(
        self,
        *,
        artifacts: Sequence[str | ArtifactConfig],
        logger: logging.Logger,
    ):
        self.artifacts = list(artifacts)
        self.logger = logger
        # (host target path, artifact) claims, in collection order. Scoped to one
        # collection pass (reset via begin_collection) and persists across that
        # pass's main + sidecar phases, so a later entry whose host path equals
        # or nests with an already-claimed one is warned and skipped instead of
        # overwriting (or polluting) the first claimant's content.
        self._claimed_targets: list[tuple[Path, ArtifactConfig]] = []

    def begin_collection(self) -> None:
        """Reset collision claims for a new collection.

        Claims must span the main + sidecar phases of one pass, not the whole
        trial: multi-step trials vacate the shared host artifacts dir between
        steps (outputs are archived under ``steps/<name>/``), so a prior step's
        claims would otherwise skip later entries that no longer collide.
        """
        self._claimed_targets.clear()

    def _find_conflicting_claim(
        self, target: Path, artifact: ArtifactConfig
    ) -> ArtifactConfig | None:
        """Return the artifact that already claimed a host path overlapping
        *target* (equal or nested either way), or None. Re-collection of the
        same source, service, target, and exclusions is not a conflict
        (multi-step re-runs overwrite their own files)."""
        for claimed_target, claimed_artifact in self._claimed_targets:
            if claimed_artifact.source == artifact.source and artifact_is_covered_by(
                claimed_artifact, artifact
            ):
                continue
            if (
                claimed_target == target
                or claimed_target in target.parents
                or target in claimed_target.parents
            ):
                return claimed_artifact
        return None

    @staticmethod
    def move_dir_contents(src: Path, dst: Path) -> None:
        """Move all contents from src to dst, leaving src empty."""
        ArtifactHandler.move_dir_contents_preserving(src, dst, preserve_dirs=[])

    @staticmethod
    def move_dir_contents_preserving(
        src: Path,
        dst: Path,
        *,
        preserve_dirs: Sequence[Path],
    ) -> None:
        """Move entries while retaining live mount directories and their ancestors."""
        moves = directory_move_plan(src, dst, preserve_dirs=preserve_dirs)
        for item, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_junction():
                target.rmdir()
            elif target.exists() or target.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), target)

    async def download_artifacts(
        self,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
        services: Collection[str] | None = None,
        pre_verifier: bool = False,
    ) -> ArtifactManifest:
        """Best-effort artifact download with a manifest of attempted sources.

        When *services* is given, only entries targeting those services are
        collected; the manifest on disk accumulates entries across calls so
        a collection split into per-service passes still produces one
        complete manifest.
        """
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        manifest = self._read_manifest(artifacts_dir)
        convention_source = self._environment_path_str(source_artifacts_dir)

        for artifact in self._normalized_artifacts(artifacts, convention_source):
            if (
                services is not None
                and effective_artifact_service(artifact) not in services
            ):
                continue
            entry = await self._download_artifact(
                source_env=source_env,
                artifacts_dir=artifacts_dir,
                artifact=artifact,
                convention_source=convention_source,
                pre_verifier=pre_verifier,
            )
            manifest.entries.append(entry)

        self._write_manifest(artifacts_dir, manifest)
        return manifest

    async def retry_failed_artifacts(
        self,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        warn: Callable[[str], None],
    ) -> None:
        """Save missed files from the shared verifier's main service only."""
        manifest = self._read_manifest(artifacts_dir, strict=True)
        groups: dict[str, list[tuple[int, ArtifactManifestEntry]]] = {}
        collected: set[str] = set()
        for index, entry in enumerate(manifest.entries):
            if entry.status == "failed":
                groups.setdefault(entry.destination, []).append((index, entry))
            elif entry.status in ("ok", "empty"):
                collected.add(entry.destination)

        env_paths = EnvironmentPaths.for_os(source_env.os)
        reserved = (
            self._environment_path_str(source_artifacts_dir),
            env_paths.agent_dir.as_posix(),
            env_paths.user_agent_dir.as_posix(),
            (env_paths.logs_dir / "seats").as_posix(),
            env_paths.tests_dir.as_posix(),
        )
        for destination, failures in groups.items():
            if destination in collected:
                continue
            first = failures[0][1]
            service = first.service or MAIN_SERVICE_NAME
            label = f"Artifact {first.source!r} from service {service!r}"
            if service != MAIN_SERVICE_NAME:
                warn(f"{label} failed before verification; only main can be retried.")
                continue
            if source_env.os != TaskOS.LINUX:
                warn(f"{label} was not retried; remote path validation requires Linux.")
                continue
            # POSIX normpath preserves exactly two leading slashes, but Linux
            # resolves them as one. Use the same spelling for checks and copy.
            source = posixpath.normpath(first.source)
            if source.startswith("//"):
                source = "/" + source.lstrip("/")
            source_path = PurePosixPath(source.casefold())
            if (
                first.type != "file"
                or not source.startswith("/")
                or any(source_path.is_relative_to(path.casefold()) for path in reserved)
            ):
                warn(f"{label} failed before verification; this path is not retried.")
                continue
            if any(
                entry.source != first.source
                or (entry.service or MAIN_SERVICE_NAME) != service
                or entry.type != "file"
                for _, entry in failures[1:]
            ):
                warn(f"{label} has conflicting declarations; it is not retried.")
                continue

            try:
                target = artifacts_dir.parent / destination
                validate_output_path(target, artifacts_dir)
                if target.exists() or target == artifacts_dir:
                    warn(f"{label} has an existing host path; it is not overwritten.")
                    continue
                original = shlex.quote(first.source)
                canonical = shlex.quote(source)
                # Check the full path, including parent symlinks, before asking
                # a provider to copy it. Some providers dereference symlinks.
                probe = await source_env.exec(
                    f"test -f {original} && "
                    f'test "$(readlink -f -- {original})" = {canonical}',
                    timeout_sec=10,
                    user="root",
                )
                if probe.return_code != 0:
                    warn(f"{label} is not a verified non-symlink regular file.")
                    continue
                with tempfile.TemporaryDirectory(
                    prefix=".artifact-retry-",
                    dir=artifacts_dir.parent,
                    ignore_cleanup_errors=True,
                ) as staging:
                    snapshot = Path(staging) / "snapshot"
                    await source_env.download_file(source, snapshot)
                    if snapshot.is_symlink() or not snapshot.is_file():
                        warn(f"{label} did not download as a regular file.")
                        continue
                    for index, entry in failures:
                        manifest.entries[index] = entry.model_copy(
                            update={
                                "status": "ok",
                                "phase": "post_verifier",
                                "source_environment": "agent",
                            }
                        )
                    manifest_file = Path(staging) / _MANIFEST_FILENAME
                    manifest_file.write_text(
                        json.dumps(manifest.to_json_data(), indent=2)
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    snapshot.replace(target)
                    try:
                        manifest_file.replace(artifacts_dir / _MANIFEST_FILENAME)
                    except OSError:
                        target.unlink()
                        raise
            except Exception as exc:
                for index, entry in failures:
                    manifest.entries[index] = entry
                warn(f"{label} still failed after verification ({type(exc).__name__}).")
                self.logger.debug(
                    "Post-verifier artifact transfer failed", exc_info=True
                )

    async def upload_artifacts(
        self,
        target_env: BaseEnvironment,
        artifacts_dir: Path,
        *,
        source_artifacts_dir: EnvironmentPath,
        target_artifacts_dir: EnvironmentPath,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
    ) -> None:
        """Re-materialize collected artifacts inside a verifier environment.

        Every entry is uploaded to its original ``source`` path; the
        convention entry is uploaded to the target environment's convention
        dir (which differs from the source's only across OSes). Parent
        directories are created so verifier images do not need to pre-create
        them.

        Each artifact is uploaded from a sanitized copy: it is packed into a tar
        archive and extracted with tarfile's ``data`` filter, which clears
        setuid/setgid bits and skips special files and symlinks that leave the
        artifact.
        """
        source_convention = self._environment_path_str(source_artifacts_dir)
        target_convention = self._environment_path_str(target_artifacts_dir)

        staging = Path(
            tempfile.mkdtemp(prefix=".verifier-artifacts-", dir=artifacts_dir.parent)
        )
        try:
            artifacts = self._normalized_artifacts(artifacts, source_convention)
            for index, artifact in enumerate(artifacts):
                host_path = self._host_path(artifacts_dir, artifact, source_convention)
                if not host_path.exists():
                    continue

                # Copying is blocking I/O; keep it off the shared event loop.
                refused = await asyncio.to_thread(
                    copy_via_tar, host_path, staging / str(index)
                )
                if refused:
                    self.logger.warning(
                        "Skipped %d unsafe entries in artifact %s, e.g. %s",
                        len(refused),
                        host_path,
                        refused[0],
                    )
                copy = staging / str(index) / host_path.name
                if not copy.exists():
                    continue

                target_source = self._upload_target_source(
                    artifact,
                    source_convention=source_convention,
                    target_convention=target_convention,
                )
                if copy.is_dir():
                    await target_env.empty_dirs([target_source], chmod=True)
                    await target_env.upload_dir(
                        source_dir=copy,
                        target_dir=target_source,
                    )
                    continue

                parent = PurePosixPath(target_source).parent.as_posix()
                if parent and parent != target_source:
                    await target_env.ensure_dirs([parent], chmod=True)
                await target_env.upload_file(
                    source_path=copy,
                    target_path=target_source,
                )
        finally:
            # Deleting a large staged tree is also blocking I/O.
            await asyncio.to_thread(shutil.rmtree, staging, ignore_errors=True)

    def sidecar_services(
        self,
        artifacts: Sequence[str | ArtifactConfig] | None = None,
    ) -> set[str]:
        """Names of non-main services referenced by the effective artifact set."""
        from harbor.models.task.artifacts import sidecar_services

        return sidecar_services([*self.artifacts, *(artifacts or [])])

    def _normalized_artifacts(
        self,
        artifacts: Sequence[str | ArtifactConfig] | None,
        convention_source: str,
    ) -> list[ArtifactConfig]:
        return with_convention_entry(
            [*self.artifacts, *(artifacts or [])],
            convention_source=convention_source,
        )

    async def _download_artifact(
        self,
        *,
        source_env: BaseEnvironment,
        artifacts_dir: Path,
        artifact: ArtifactConfig,
        convention_source: str,
        pre_verifier: bool,
    ) -> ArtifactManifestEntry:
        source = artifact.source
        service = effective_artifact_service(artifact)
        target = self._host_path(artifacts_dir, artifact, convention_source)
        manifest_destination = self._manifest_destination(artifacts_dir, target)

        # All services share one flat host base dir, so two services that export
        # the same path map to the same host target. Keep the first claimant and
        # skip later ones rather than overwriting (persists across the main and
        # sidecar collection passes via self._claimed_targets).
        prior = self._find_conflicting_claim(target, artifact)
        if prior is not None:
            artifact_type = "file" if PurePosixPath(source).suffix else "directory"
            if not artifact_is_covered_by(prior, artifact):
                self.logger.warning(
                    "Artifact collision: source %r (service %r) maps to host path "
                    "%s, which overlaps content already claimed by source %r (service %r); "
                    "keeping the first and skipping this one.",
                    source,
                    service,
                    target,
                    prior.source,
                    effective_artifact_service(prior),
                )
            return ArtifactManifestEntry(
                source=source,
                destination=manifest_destination,
                type=artifact_type,
                status="skipped",
                service=artifact.service,
                exclude=list(artifact.exclude) if artifact_type == "directory" else [],
            )
        self._claimed_targets.append((target, artifact))

        if (
            is_convention_entry(artifact, convention_source)
            and not artifact.destination
            and source_env.is_path_mounted(source, target)
            and not artifact.exclude
        ):
            return self._record_mounted_artifacts_dir(
                source=source,
                target=target,
                manifest_destination=manifest_destination,
                service=artifact.service,
                exclude=artifact.exclude,
            )

        is_windows = source_env.os == TaskOS.WINDOWS
        # Retain a best-effort manifest type if probing fails, but never use
        # that guess to select a Windows transfer.
        is_dir = not Path(source).suffix
        try:
            try:
                is_dir = await source_env.service_is_dir(
                    source,
                    service=artifact.service,
                    user=None if is_windows else "root",
                )
            except Exception:
                if is_windows:
                    raise

            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                if artifact.exclude:
                    await source_env.service_download_dir_with_exclusions(
                        source_dir=source,
                        target_dir=target,
                        exclude=artifact.exclude,
                        service=artifact.service,
                    )
                else:
                    await source_env.service_download_dir(
                        source_dir=source,
                        target_dir=target,
                        service=artifact.service,
                    )
                artifact_type = "directory"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                await source_env.service_download_file(
                    source_path=source,
                    target_path=target,
                    service=artifact.service,
                )
                artifact_type = "file"

            return ArtifactManifestEntry(
                source=source,
                destination=manifest_destination,
                type=artifact_type,
                status="ok",
                service=artifact.service,
                exclude=list(artifact.exclude) if is_dir else [],
                phase="pre_verifier" if pre_verifier else None,
                source_environment="agent",
            )
        except Exception:
            self.logger.debug(
                f"Failed to download artifact '{source}' from service "
                f"'{service}' (best-effort)",
                exc_info=True,
            )
            return ArtifactManifestEntry(
                source=source,
                destination=manifest_destination,
                type="directory" if is_dir else "file",
                status="failed",
                service=artifact.service,
                exclude=list(artifact.exclude) if is_dir else [],
                source_environment="agent",
            )

    def _record_mounted_artifacts_dir(
        self,
        *,
        source: str,
        target: Path,
        manifest_destination: str,
        service: str | None,
        exclude: Sequence[str],
    ) -> ArtifactManifestEntry:
        has_contents = target.exists() and any(target.iterdir())
        return ArtifactManifestEntry(
            source=source,
            destination=manifest_destination,
            type="directory",
            status="ok" if has_contents else "empty",
            service=service,
            exclude=list(exclude),
            source_environment="agent",
        )

    def _host_path(
        self,
        artifacts_dir: Path,
        artifact: ArtifactConfig,
        convention_source: str,
    ) -> Path:
        return artifact_host_path(artifacts_dir, artifact)

    def _upload_target_source(
        self,
        artifact: ArtifactConfig,
        *,
        source_convention: str,
        target_convention: str,
    ) -> str:
        if is_convention_entry(artifact, source_convention):
            return target_convention
        return artifact.source

    @staticmethod
    def _environment_path_str(path: EnvironmentPath) -> str:
        if isinstance(path, PurePath):
            return path.as_posix()
        return path

    def _manifest_destination(self, artifacts_dir: Path, target: Path) -> str:
        if target == artifacts_dir:
            return "artifacts"
        return f"artifacts/{target.relative_to(artifacts_dir).as_posix()}"

    def _read_manifest(
        self, artifacts_dir: Path, *, strict: bool = False
    ) -> ArtifactManifest:
        manifest_path = artifacts_dir / _MANIFEST_FILENAME

        entries: list[ArtifactManifestEntry] = []
        if strict or manifest_path.exists():
            try:
                entries = [
                    ArtifactManifestEntry.model_validate(entry)
                    for entry in json.loads(manifest_path.read_text())
                ]
            except Exception:
                if strict:
                    raise
                self.logger.debug(
                    "Failed to read existing artifacts manifest", exc_info=True
                )

        return ArtifactManifest(entries=entries)

    def _write_manifest(self, artifacts_dir: Path, manifest: ArtifactManifest) -> None:
        if not manifest.entries:
            return

        try:
            (artifacts_dir / _MANIFEST_FILENAME).write_text(
                json.dumps(manifest.to_json_data(), indent=2)
            )
        except Exception:
            self.logger.debug("Failed to write artifacts manifest", exc_info=True)
