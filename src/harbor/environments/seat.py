"""A seat: one Compose service of the trial's running project, as an environment.

The trial's environment owns the Compose project. A seat environment
gives an unchanged Harbor agent a handle on one other service of that project,
so the agent installs and runs there. Exec, upload and download go through the
project's per-service operations; starting and stopping belong to the project.
"""

import logging
import re
import shlex
from pathlib import Path
from typing import override

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

# Sends SIGSTOP to every process the caller may signal but PID 1 and itself
# (kill -1), then reads each process's state from /proc until none is left
# running or a second has passed, and prints how many are stopped and how many
# are not. kill's exit status says nothing: it is 0 when no process could be
# signalled. The state is the field after the last ")" of /proc/<pid>/stat, so
# a process cannot name itself stopped.
_FREEZE = """\
kill -s STOP -- -1 2>/dev/null
for _ in 1 2 3 4 5 6 7 8 9 10; do
  stopped=0 running=0
  for dir in /proc/[0-9]*; do
    case ${dir#/proc/} in 1 | $$) continue ;; esac
    stat=
    { IFS= read -r -d '' stat < "$dir/stat"; } 2>/dev/null
    state=${stat##*) }
    case ${state%% *} in
      T | t) stopped=$((stopped + 1)) ;;
      '' | Z | X) ;;
      *) running=$((running + 1)) ;;
    esac
  done
  [ "$running" -eq 0 ] && break
  sleep 0.1
done
echo "stopped=$stopped running=$running"
"""
_FREEZE_TIMEOUT_SEC = 30


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

    async def freeze(self) -> tuple[int, int]:
        """Stop every process in the seat with SIGSTOP, as root, and return how
        many are now stopped and how many are still running. The container
        stays up and nothing is killed, so exec, upload and download still
        work: they start new processes, which are not stopped. PID 1 cannot be
        stopped from inside its container and is not counted: with Compose
        ``init: true`` it is the init process, without it the service's own
        command."""
        result = await self.exec(_FREEZE, user="root", timeout_sec=_FREEZE_TIMEOUT_SEC)
        counts = re.search(r"stopped=(\d+) running=(\d+)", result.stdout or "")
        if result.return_code != 0 or counts is None:
            raise RuntimeError(
                f"Seat {self.service!r}: could not stop its processes "
                f"(exit {result.return_code}): {result.stdout}"
            )
        return int(counts[1]), int(counts[2])

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
