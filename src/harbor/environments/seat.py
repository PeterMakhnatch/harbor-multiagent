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
from harbor.environments.ssh import StreamHandle
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

_KILL_EXEC_PROCESSES = r"""
ppid() { while read -r key value; do
  if [ "$key" = PPid: ]; then echo "$value"; return 0; fi
done < "/proc/$1/status"; return 1; }
own=" "
pid=$$
while [ "$pid" -gt 0 ]; do own="$own$pid "; pid=$(ppid "$pid" 2>/dev/null) || break; done
while :; do
  targets=""
  for dir in /proc/[0-9]*; do
    target=${dir#/proc/}
    pid=$target
    while [ "$pid" -gt 1 ]; do
      case "$own" in *" $pid "*) break ;; esac
      parent=$(ppid "$pid" 2>/dev/null) || break
      if [ "$parent" = 0 ]; then targets="$targets $target"; break; fi
      pid=$parent
    done
  done
  [ -z "$targets" ] && exit 0
  kill -9 $targets 2>/dev/null
done
"""

# Reads one process's state, parent and start time (clock ticks after boot)
# from /proc/<pid>/stat: the fields after the last ")", which a process name
# cannot fake. Then records the ancestry of this shell, which an exec started.
_PROCESS_TABLE = r"""
info() {
  stat=
  while IFS= read -r line || [ -n "$line" ]; do stat="$stat $line"; done < "/proc/$1/stat" || return 1
  case $stat in *") "*) ;; *) return 1 ;; esac
  set -- ${stat##*) }
  state=$1 parent=$2 start=${20}
}
own=" "
pid=$$
while [ "$pid" -gt 0 ]; do own="$own$pid "; info "$pid" 2>/dev/null || break; pid=$parent; done
"""

# Prints pid:start for every live process but this exec's own: the service's
# processes before an agent is installed.
_SERVICE_PROCESSES = (
    _PROCESS_TABLE
    + r"""
for dir in /proc/[0-9]*; do
  p=${dir#/proc/}
  case "$own" in *" $p "*) continue ;; esac
  info "$p" 2>/dev/null || continue
  case $state in Z | X) continue ;; esac
  echo "$p:$start"
done
"""
)

# Kills, as root, every live process but PID 1, this exec's own, and the
# service's (pid:start in $spare) with their descendants: what an exec
# started, and what an agent left under PID 1 with setsid, nohup or a double
# fork. It repeats until none is left, at most 50 times, then lists the
# processes that are still left: none when the seat has stopped.
_KILL_LEFTOVERS = (
    _PROCESS_TABLE
    + r"""
target() {
  [ "$1" -gt 1 ] || return 1
  info "$1" 2>/dev/null || return 1
  case $state in Z | X) return 1 ;; esac
  pid=$1
  while :; do
    case "$own" in *" $pid "*) return 1 ;; esac
    case "$spare" in *" $pid:$start "*) return 1 ;; esac
    [ "$parent" -le 1 ] && return 0
    pid=$parent
    info "$pid" 2>/dev/null || return 0
  done
}
killed=" "
round=0
while [ "$round" -lt 50 ]; do
  round=$((round + 1))
  targets=
  for dir in /proc/[0-9]*; do
    p=${dir#/proc/}
    target "$p" || continue
    targets="$targets $p"
    case "$killed" in *" $p "*) ;; *) killed="$killed$p " ;; esac
  done
  [ -z "$targets" ] && break
  kill -9 $targets 2>/dev/null
  sleep 0.1 2>/dev/null || :
done
left=
for dir in /proc/[0-9]*; do
  p=${dir#/proc/}
  target "$p" && left="$left $p"
done
set -- $killed
echo "killed=$# left=${left# }"
"""
)
_PROCESS = re.compile(r"\d+:\d+")
_KILLED = re.compile(r"killed=(\d+) left=([\d ]*)")
_KILL_LEFTOVERS_TIMEOUT_SEC = 60


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
        self.service_processes: frozenset[str] | None = None
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

    @property
    @override
    def stream_enabled(self) -> bool:
        return (
            self.project.stream_enabled
            and "service" in self.project.stream_handle.reference
        )

    @property
    @override
    def stream_handle(self) -> StreamHandle:
        handle = self.project.stream_handle
        if "service" not in handle.reference:
            return super().stream_handle
        return handle.model_copy(
            update={
                "reference": {**handle.reference, "service": self.service},
                "workspace": None,
            }
        )

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

    async def kill_exec_processes(self) -> None:
        """Kill every process in the seat that an exec started (its parent is
        outside the container), with its descendants, so a stopped agent stops
        acting. The service's own processes, under PID 1, keep running."""
        with self.project.scoped_output_callback(self._output_callback()):
            result = await self.project.service_exec(
                _KILL_EXEC_PROCESSES, service=self.service, user="root"
            )
        if result.return_code != 0:
            raise RuntimeError(
                f"Seat {self.service!r}: could not kill its agent's processes: "
                f"{result.stdout}{result.stderr or ''}"
            )

    async def record_service_processes(self) -> None:
        """Record, by PID and start time, the processes the service runs
        before an agent is installed: PID 1, the service's command and what
        they started. kill_leftover_processes spares them and their
        descendants; a PID that a later process reuses is not spared."""
        with self.project.scoped_output_callback(self._output_callback()):
            result = await self.project.service_exec(
                _SERVICE_PROCESSES, service=self.service, user="root"
            )
        processes = (result.stdout or "").split()
        if (
            result.return_code != 0
            or not processes
            or not all(_PROCESS.fullmatch(process) for process in processes)
        ):
            raise RuntimeError(
                f"Seat {self.service!r}: could not list its processes: "
                f"{result.stdout}{result.stderr or ''}"
            )
        self.service_processes = frozenset(processes)

    async def kill_leftover_processes(self) -> tuple[int, list[int]]:
        """Kill every process in the seat but the service's own, as
        record_service_processes found them, and their descendants: what an
        exec started, and what an agent left behind under PID 1, for example
        with ``setsid nohup``. Then list the processes that are still alive
        and would be killed. Return how many processes were killed and the
        PIDs still alive, which is empty when the seat has stopped.
        Zombies, which cannot act, are not counted."""
        if self.service_processes is None:
            raise RuntimeError(
                f"Seat {self.service!r}: its own processes were not recorded."
            )
        spare = shlex.quote(f" {' '.join(sorted(self.service_processes))} ")
        with self.project.scoped_output_callback(self._output_callback()):
            result = await self.project.service_exec(
                f"spare={spare}\n{_KILL_LEFTOVERS}",
                service=self.service,
                user="root",
                timeout_sec=_KILL_LEFTOVERS_TIMEOUT_SEC,
            )
        counts = _KILLED.fullmatch((result.stdout or "").strip())
        if result.return_code != 0 or counts is None:
            raise RuntimeError(
                f"Seat {self.service!r}: could not kill its leftover processes "
                f"(exit {result.return_code}): {result.stdout}{result.stderr or ''}"
            )
        return int(counts[1]), [int(pid) for pid in counts[2].split()]

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
