"""SeatEnvironment points the docker environment at one Compose service."""

import asyncio
import os
import shlex
import tarfile
from pathlib import Path, PurePosixPath
from unittest.mock import AsyncMock

import pytest

from harbor.environments.base import (
    BaseEnvironment,
    ExecResult,
    ServiceOperationsUnsupportedError,
)
from harbor.environments.docker.docker import DockerEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import EnvironmentPaths, TrialPaths


@pytest.fixture
def project(temp_dir: Path) -> DockerEnvironment:
    env_dir = temp_dir / "environment"
    env_dir.mkdir()
    (env_dir / "docker-compose.yaml").write_text(
        'services:\n  main: {}\n  maya:\n    user: "1000:2000"\n'
    )
    trial_paths = TrialPaths(trial_dir=temp_dir / "trial")
    trial_paths.mkdir()
    env = DockerEnvironment(
        environment_dir=env_dir,
        environment_name="hello",
        session_id="hello__abc__env",
        trial_paths=trial_paths,
        task_env_config=EnvironmentConfig(workdir="/app", env={"TASK": "1"}),
    )
    env._run_docker_compose_command = AsyncMock(
        return_value=ExecResult(stdout="", stderr="", return_code=0)
    )
    return env


@pytest.fixture
def seat(project: DockerEnvironment, temp_dir: Path) -> SeatEnvironment:
    return SeatEnvironment(
        project,
        "maya",
        session_id="hello__abc__env__maya",
        trial_paths=TrialPaths(trial_dir=temp_dir / "trial" / "seats" / "maya"),
    )


def _commands(project: DockerEnvironment) -> list[list[str]]:
    mock = project._run_docker_compose_command
    assert isinstance(mock, AsyncMock)
    return [call.args[0] for call in mock.call_args_list]


async def test_exec_wraps_in_bash_and_keeps_the_service_user_and_workdir(project, seat):
    await seat.exec("set -o pipefail; echo hi | cat")

    assert _commands(project)[-1] == [
        "exec",
        "maya",
        "sh",
        "-c",
        "exec bash -c " + shlex.quote("set -o pipefail; echo hi | cat"),
    ]


async def test_exec_passes_explicit_user_cwd_and_scoped_env(project, seat):
    with seat.scoped_exec_env({"INVOCATION_ID": "tok"}):
        await seat.exec("whoami", cwd="/home/maya", env={"A": "1"}, user="root")

    command = _commands(project)[-1]
    assert command[: command.index("maya")] == [
        "exec",
        "-w",
        "/home/maya",
        "-e",
        "A=1",
        "-e",
        "INVOCATION_ID=tok",
        "-u",
        "root",
    ]


async def test_compose_identity_applies_in_full_and_main_settings_do_not(project, seat):
    project.default_user = "agent"
    seat.default_user = 1000

    await seat.exec("id")
    await seat.exec("apt-get install -y curl", user="root")

    ordinary, root = _commands(project)[-2:]
    assert ordinary[:2] == ["exec", "maya"]
    assert "-u" not in ordinary
    assert root[:4] == ["exec", "-u", "root", "maya"]
    assert "TASK=1" not in ordinary and "/app" not in ordinary


async def test_seat_reports_what_agents_read(seat, temp_dir):
    assert seat.default_user is None
    assert seat.capabilities.mounted is False
    assert seat.capabilities.stream is False
    assert seat.task_env_config.workdir is None
    assert seat.trial_paths.agent_dir == temp_dir / "trial" / "seats" / "maya" / "agent"
    assert seat.session_id == "hello__abc__env__maya"
    assert seat.os == seat.project.os


async def test_uploads_target_the_seat(project, seat, temp_dir):
    source = temp_dir / "config.json"
    source.write_text("{}")

    await seat.upload_file(source, "/home/maya/.config.json")
    await seat.upload_dir(temp_dir, "/opt/agent")

    commands = _commands(project)
    assert [c for c in commands if c[0] == "cp"] == [
        ["cp", str(source), "maya:/home/maya/.config.json"],
        ["cp", f"{temp_dir}/.", "maya:/opt/agent"],
    ]
    assert all(c[1] == "maya" for c in commands if c[0] == "exec")


async def test_upload_tar_fallback_targets_the_seat(project, seat, temp_dir):
    source = temp_dir / "config.json"
    source.write_text("{}")

    async def runner(command, check=True, **_):
        if command[0] == "cp":
            raise RuntimeError("cp unsupported")
        return ExecResult(return_code=1 if "test" in command else 0)

    project._run_docker_compose_command = AsyncMock(side_effect=runner)

    await seat.upload_file(source, "/home/maya/config.json")

    tar_steps = [c for c in _commands(project) if c[0] == "exec"]
    assert all(c[:5] == ["exec", "-T", "-u", "root", "maya"] for c in tar_steps)
    assert tar_steps[-1][5:] == ["tar", "-xf", "-", "-C", "/home/maya"]


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX host file modes")
async def test_oracle_solution_archive_preserves_modes_and_extracts_as_seat(
    project, seat, temp_dir
):
    source = temp_dir / "solution"
    source.mkdir(mode=0o700)
    script = source / "solve.sh"
    script.write_text("#!/bin/bash\n")
    script.chmod(0o600)
    nested = source / ".inputs"
    nested.mkdir(mode=0o700)
    (nested / "answer").write_text("task input")
    (nested / "answer").chmod(0o600)
    (source / "link").symlink_to(".inputs/answer")

    async def runner(command, **_):
        if command[-1].endswith("id -u && id -g"):
            return ExecResult(stdout="1000\n2000\n", return_code=0)
        if command[0] == "cp":
            with tarfile.open(command[1]) as archive:
                entries = {info.name: info for info in archive.getmembers()}
                assert entries.keys() == {
                    ".inputs",
                    ".inputs/answer",
                    "solve.sh",
                    "link",
                }
                assert entries["link"].issym()
                assert entries[".inputs"].mode == 0o700
                assert entries[".inputs/answer"].mode == 0o600
                assert entries["solve.sh"].mode == 0o600
                assert entries["link"].linkname == ".inputs/answer"
        return ExecResult(return_code=0)

    project._run_docker_compose_command = AsyncMock(side_effect=runner)

    await seat.upload_oracle_solution(source)

    identity, prepare, copy, handoff, extract, cleanup = _commands(project)
    assert identity[:4] == ["exec", "maya", "sh", "-c"]
    assert "mkdir -m 700 -- /solution && chown -h -- 1000:2000 /solution" in prepare[-1]
    staging = prepare[-1].split()[-1]
    assert copy[-1] == f"maya:{staging}/solution.tar"
    assert all(
        c[:4] == ["exec", "-u", "root", "maya"] for c in (prepare, handoff, cleanup)
    )
    assert f"chmod 400 -- {staging}/solution.tar" in handoff[-1]
    assert f"chown -h -- 1000:2000 {staging}/solution.tar" in handoff[-1]
    assert f"chown -h -- 0:2000 {staging} && chmod 710 -- {staging}" in handoff[-1]
    assert extract[:4] == ["exec", "maya", "sh", "-c"]
    assert extract[-1].endswith(f"exec tar --no-same-owner -xpf {staging}/solution.tar")
    assert cleanup[-1] == f"rm -f -- {staging}/solution.tar; rmdir -- {staging}"


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX shell")
@pytest.mark.parametrize("redirected", [True, False], ids=["symlink", "nonempty"])
async def test_oracle_solution_refuses_unsafe_destinations(
    project, seat, temp_dir, monkeypatch, redirected
):
    source = temp_dir / "source"
    source.mkdir()
    (source / "solve.sh").write_text("#!/bin/bash\n")
    target = temp_dir.resolve() / "target"
    if redirected:
        target.symlink_to(source, target_is_directory=True)
    else:
        target.mkdir()
        (target / ".keep").write_text("pre-existing")
    monkeypatch.setattr(
        EnvironmentPaths,
        "for_os",
        lambda _: EnvironmentPaths(solution_dir=PurePosixPath(target)),
    )

    async def run_guard(command: str, tail: str) -> ExecResult:
        # Run only the destination check against the fixture, never setup or tar.
        guard, separator, _ = command.partition(tail)
        assert separator
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", guard + "exit 42", stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        return ExecResult(stderr=stderr.decode(), return_code=await proc.wait())

    async def runner(
        command: str, *, service: str, user: str | None = None
    ) -> ExecResult:
        assert service == "maya"
        if command.endswith("id -u && id -g"):
            assert user is None
            return ExecResult(stdout="1000\n2000\n", return_code=0)
        if "exec tar" in command:
            assert user is None
            return await run_guard(command, "exec tar")
        if redirected and "destination must be a real directory" in command:
            assert user == "root"
            return await run_guard(command, f"if [ ! -d {shlex.quote(str(target))} ]")
        return ExecResult(return_code=0)

    project.service_exec = AsyncMock(side_effect=runner)
    project.service_upload_file = AsyncMock()
    expected = "must be a real directory" if redirected else "must be empty"

    with pytest.raises(RuntimeError, match=expected):
        await seat.upload_oracle_solution(source)

    if redirected:
        project.service_upload_file.assert_not_awaited()
        assert target.is_symlink()
    else:
        project.service_upload_file.assert_awaited_once()
        assert (target / ".keep").read_text() == "pre-existing"


async def test_oracle_solution_refuses_root_identity(project, seat, temp_dir):
    project.service_exec = AsyncMock(
        return_value=ExecResult(stdout="0\n2000\n", return_code=0)
    )
    project.service_upload_file = AsyncMock()

    with pytest.raises(RuntimeError, match="expected a nonroot user"):
        await seat.upload_oracle_solution(temp_dir)

    project.service_exec.assert_awaited_once()
    assert project.service_exec.call_args.kwargs["user"] is None
    project.service_upload_file.assert_not_awaited()


async def test_downloads_target_the_seat(project, seat, temp_dir):
    await seat.download_file("/logs/agent/a.txt", temp_dir / "a.txt")
    await seat.download_dir("/logs/agent", temp_dir / "agent")

    assert _commands(project)[-2:] == [
        ["cp", "maya:/logs/agent/a.txt", str(temp_dir / "a.txt")],
        ["cp", "maya:/logs/agent/.", str(temp_dir / "agent")],
    ]


async def test_inherited_helpers_run_in_the_seat(project, seat):
    await seat.is_dir("/logs/agent")
    await seat.ensure_dirs(["/logs/agent"])

    is_dir, ensure = _commands(project)[-2:]
    assert is_dir[1] == "maya" and "test -d /logs/agent" in is_dir[-1]
    assert ensure[1:4] == ["-u", "root", "maya"]
    assert "mkdir -p /logs/agent && chmod 777 /logs/agent" in ensure[-1]


async def test_seat_output_streams_through_its_own_callbacks(project, seat):
    seen: list[str] = []

    async def runner(command, on_output=None, **_):
        assert on_output is not None
        await on_output("hello\n", "stdout")
        return ExecResult(return_code=0)

    async def collect(text, stream):
        seen.append(text)

    project._run_docker_compose_command = AsyncMock(side_effect=runner)
    with seat.scoped_output_callback(collect):
        await seat.exec("echo hello")

    assert seen == ["hello\n"]


async def test_seat_refuses_start_and_stop(seat):
    with pytest.raises(RuntimeError, match="starts with the trial's project"):
        await seat.start(force_build=False)
    with pytest.raises(RuntimeError, match="stops with the trial's project"):
        await seat.stop(delete=True)


async def test_base_environment_refuses_service_uploads(temp_dir):
    class Plain(BaseEnvironment):
        @staticmethod
        def type() -> str:
            return "plain"

        def _validate_definition(self):
            pass

        async def start(self, force_build): ...
        async def stop(self, delete): ...
        async def upload_file(self, source_path, target_path): ...
        async def upload_dir(self, source_dir, target_dir): ...
        async def download_file(self, source_path, target_path): ...
        async def download_dir(self, source_dir, target_dir): ...
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            return ExecResult(return_code=0)

    env = Plain(
        environment_dir=temp_dir,
        environment_name="plain",
        session_id="plain",
        trial_paths=TrialPaths(trial_dir=temp_dir),
        task_env_config=EnvironmentConfig(),
    )

    with pytest.raises(ServiceOperationsUnsupportedError):
        await env.service_upload_file("a", "/a", service="maya")
    with pytest.raises(ServiceOperationsUnsupportedError):
        await env.service_upload_dir("a", "/a", service="maya")
    await env.service_upload_file("a", "/a", service="main")
