import os
import subprocess
from unittest.mock import AsyncMock

import pytest

from harbor.environments.base import ExecResult
from harbor.environments.desktop_exec import posix_exec_files, staged_exec_files


@pytest.mark.skipif(os.name == "nt", reason="Executes a POSIX shell locally")
def test_posix_environment_is_exported_without_restarting_bash(tmp_path):
    startup = tmp_path / "startup.sh"
    startup.write_text("echo unexpected-startup\n")
    files = posix_exec_files("printf 'task\\n'", {"BASH_ENV": str(startup)}, "")
    result = subprocess.run(
        ["bash", "-s"], input=files["run.sh"], capture_output=True, check=True
    )
    assert result.stdout == b"task\n"


@pytest.mark.parametrize(
    "command,output", [("printf 'task\\n'", b"task\n"), ("# empty command", b"")]
)
@pytest.mark.skipif(os.name == "nt", reason="Executes a POSIX shell locally")
def test_password_only_staging_does_not_print_inherited_environment(
    monkeypatch, command, output
):
    monkeypatch.setenv("AMBIENT_SECRET", "synthetic-ambient-value")
    files = posix_exec_files(command, {}, "synthetic-password")
    result = subprocess.run(
        ["bash", "-s"], input=files["run.sh"], capture_output=True, check=True
    )
    assert result.stdout == output
    assert "askpass" in files


async def test_cleanup_failure_is_reported():
    run = AsyncMock(
        side_effect=[
            ExecResult(return_code=0),
            ExecResult(return_code=1, stderr="refused"),
        ]
    )
    with pytest.raises(RuntimeError, match="Private exec cleanup failed: refused"):
        async with staged_exec_files(run, AsyncMock(), {"run.sh": b"true"}):
            pass


async def test_cleanup_error_does_not_replace_command_error():
    run = AsyncMock(
        side_effect=[ExecResult(return_code=0), RuntimeError("cleanup refused")]
    )
    with pytest.raises(ValueError, match="command failed") as error:
        async with staged_exec_files(run, AsyncMock(), {"run.sh": b"true"}):
            raise ValueError("command failed")
    assert error.value.__notes__ == ["Private exec cleanup failed: cleanup refused"]
