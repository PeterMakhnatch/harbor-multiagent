"""A seat: one Compose service of the trial's running project, as an environment.

The trial's environment owns the Compose project. A seat environment
gives an unchanged Harbor agent a handle on one other service of that project,
so the agent installs and runs there. Exec, upload and download go through the
project's per-service operations; starting and stopping belong to the project.
"""

import logging
import shlex
import tarfile
import tempfile
from pathlib import Path
from typing import override
from uuid import uuid4

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths


class SeatEnvironment(BaseEnvironment):
    """The seat's image, resources, env, user and workdir are its service's, in
    Compose, so its task environment config carries only the task's OS. Any
    environment with per-service exec, upload and download can host it: the
    docker environment and the cloud environments that run Compose."""

    def __init__(
        self,
        project: BaseEnvironment,
        service: str,
        *,
        session_id: str,
        trial_paths: TrialPaths,
        logger: logging.Logger | None = None,
    ):
        self.project = project
        self.service = service
        super().__init__(
            environment_dir=project.environment_dir,
            environment_name=project.environment_name,
            session_id=session_id,
            trial_paths=trial_paths,
            task_env_config=EnvironmentConfig(os=project.task_env_config.os),
            logger=logger,
        )

    @staticmethod
    @override
    def type() -> str:
        return "seat"

    @override
    def _validate_definition(self):
        pass

    @override
    async def start(self, force_build: bool) -> None:
        raise RuntimeError(f"Seat {self.service!r} starts with the trial's project.")

    @override
    async def stop(self, delete: bool):
        raise RuntimeError(f"Seat {self.service!r} stops with the trial's project.")

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """Run the command with bash in the seat; agents write bash, and
        service_exec runs ``sh -c``. Without a cwd, the service's Compose
        ``working_dir:`` applies. Without a user, its Compose ``user:`` does, in
        full (uid and gid): default_user, which SeatsTrial sets to that uid for
        agents' ownership fixes, is never sent as ``-u``."""
        with self.project.scoped_output_callback(self._output_callback()):
            return await self.project.service_exec(
                f"exec bash -c {shlex.quote(command)}",
                service=self.service,
                cwd=cwd,
                env=self._merge_env(env),
                timeout_sec=timeout_sec,
                user=user,
            )

    @override
    async def upload_file(self, source_path: Path | str, target_path: str):
        await self.project.service_upload_file(
            source_path, target_path, service=self.service
        )

    @override
    async def upload_dir(self, source_dir: Path | str, target_dir: str):
        await self.project.service_upload_dir(
            source_dir, target_dir, service=self.service
        )

    async def upload_oracle_solution(self, source_dir: Path) -> None:
        """Stage filtered solution inputs and extract them as the Linux seat user."""
        target = shlex.quote(str(EnvironmentPaths.for_os(self.os).solution_dir))
        source_dir = source_dir.resolve()

        async def stage(command: str, *, user: str | None = None) -> ExecResult:
            result = await self.project.service_exec(
                command, service=self.service, user=user
            )
            if result.return_code != 0:
                detail = result.stderr or result.stdout or result.return_code
                raise RuntimeError(
                    f"Seat {self.service!r}: oracle solution staging failed: {detail}"
                )
            return result

        ids = await stage(
            "if [ -w / ]; then echo 'container root must not be seat-writable' >&2; "
            "exit 1; fi; id -u && id -g"
        )
        tokens = (ids.stdout or "").split()
        if len(tokens) != 2 or not all(token.isdecimal() for token in tokens):
            raise RuntimeError(f"Seat {self.service!r}: could not resolve its UID:GID")
        uid, gid = map(int, tokens)
        if uid == 0:
            raise RuntimeError(f"Seat {self.service!r}: expected a nonroot user")

        def validate_entry(info: tarfile.TarInfo) -> tarfile.TarInfo:
            if info.issym() and (
                Path(info.linkname).is_absolute()
                or not (source_dir / info.name).resolve().is_relative_to(source_dir)
            ):
                raise ValueError(
                    f"Oracle solution link escapes its tree: {info.name!r}"
                )
            if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
                raise ValueError(f"Unsupported oracle solution entry: {info.name!r}")
            return info

        staging = f"/.harbor-oracle-{uuid4().hex}"
        archive_path = f"{staging}/solution.tar"
        with tempfile.TemporaryDirectory() as temporary:
            archive_file = Path(temporary) / "solution.tar"
            with tarfile.open(archive_file, "w", format=tarfile.GNU_FORMAT) as tar:
                for entry in source_dir.iterdir():
                    tar.add(entry, arcname=entry.name, filter=validate_entry)
            await stage(
                f"if [ -L {target} ] || {{ [ -e {target} ] && [ ! -d {target} ]; }}; "
                "then echo 'oracle solution destination must be a real directory' >&2; "
                "exit 1; fi; "
                f"if [ ! -d {target} ]; then mkdir -m 700 -- {target} && "
                f"chown -h -- {uid}:{gid} {target} || exit; fi; "
                f"mkdir -m 700 -- {staging}",
                user="root",
            )
            try:
                await self.upload_file(archive_file, archive_path)
                # Keep the handoff directory root-owned and non-writable to the seat.
                # Its group can traverse it, but only the seat UID can read the archive.
                await stage(
                    f"chmod 400 -- {archive_path} && "
                    f"chown -h -- {uid}:{gid} {archive_path} && "
                    f"chown -h -- 0:{gid} {staging} && chmod 710 -- {staging}",
                    user="root",
                )
                await stage(
                    f'if [ -L {target} ] || ! cd -P {target} || [ "$PWD" != {target} ] '
                    "|| [ ! -r . ] || [ ! -w . ] || [ ! -x . ]; then "
                    "echo 'oracle solution destination must be a writable real directory' >&2; "
                    "exit 1; fi; "
                    "set +f; "
                    "for entry in ./* ./.[!.]* ./..?*; do "
                    'if [ -e "$entry" ] || [ -L "$entry" ]; then '
                    "echo 'oracle solution destination must be empty' >&2; exit 1; fi; done; "
                    f'if [ "$(id -u)" != {uid} ] || [ "$(id -g)" != {gid} ]; then '
                    "echo 'oracle solution extraction requires the seat UID:GID' >&2; "
                    "exit 1; fi; "
                    f"exec tar --no-same-owner -xpf {archive_path}"
                )
            finally:
                try:
                    await stage(
                        f"rm -f -- {archive_path}; rmdir -- {staging}", user="root"
                    )
                except Exception as error:
                    self.logger.warning("Could not remove oracle staging: %s", error)

    @override
    async def download_file(self, source_path: str, target_path: Path | str):
        await self.project.service_download_file(
            source_path, target_path, service=self.service
        )

    @override
    async def download_dir(self, source_dir: str, target_dir: Path | str):
        await self.project.service_download_dir(
            source_dir, target_dir, service=self.service
        )
