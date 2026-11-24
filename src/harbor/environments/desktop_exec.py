import json
import ntpath
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from harbor.environments.base import ExecResult
from harbor.environments.dind_compose import shell_env_exports
from harbor.models.task.config import TaskOS
from harbor.utils.scripts import quote_shell_arg


@asynccontextmanager
async def staged_exec_files(
    run: Callable[[str], Awaitable[ExecResult]],
    upload: Callable[[bytes, str], Awaitable[None]],
    files: dict[str, bytes],
    *,
    windows: bool = False,
) -> AsyncIterator[str]:
    name = f"harbor-exec-{uuid4().hex}"
    if windows:
        local_appdata = await run("echo %LOCALAPPDATA%")
        root = (local_appdata.stdout or "").strip()
        if local_appdata.return_code != 0 or not ntpath.isabs(root):
            raise RuntimeError("Could not resolve the Windows user profile directory")
        directory = ntpath.join(root, name)
        quoted = quote_shell_arg(directory, TaskOS.WINDOWS)
        mkdir, cleanup = f"mkdir {quoted}", f"rmdir /s /q {quoted}"
    else:
        directory = f"/tmp/{name}"
        mkdir, cleanup = f"mkdir -m 700 {directory}", f"rm -rf {directory}"
    created = await run(mkdir)
    if created.return_code != 0:
        raise RuntimeError(f"Could not create private exec directory: {created.stderr}")
    command_error: BaseException | None = None
    try:
        for file_name, body in files.items():
            path = (
                ntpath.join(directory, file_name)
                if windows
                else f"{directory}/{file_name}"
            )
            await upload(body, path)
        yield directory
    except BaseException as error:
        command_error = error
        raise
    finally:
        try:
            result = await run(cleanup)
            if result.return_code != 0:
                raise RuntimeError(f"Private exec cleanup failed: {result.stderr}")
        except BaseException as error:
            if command_error is None:
                raise
            command_error.add_note(f"Private exec cleanup failed: {error}")


def posix_exec_files(
    command: str, env: dict[str, str], sudo_password: str
) -> dict[str, bytes]:
    exports = f"{shell_env_exports(env)}\n" if env else ""
    files = {"run.sh": f"{exports}{{\n:\n{command}\n}} </dev/null\n".encode()}
    if sudo_password:
        password = shlex.quote(sudo_password)
        files["askpass"] = f"#!/bin/sh\nprintf '%s\\n' {password}\n".encode()
    return files


def posix_staged_exec(
    directory: str, *, user: str | int | None, login: bool, askpass: bool
) -> str:
    shell = ["/bin/bash", "-ls" if login else "-s"]
    if user is not None:
        sudo = ["sudo"]
        if askpass:
            sudo.extend(["-A", "-p", ""])
        if str(user) != "root":
            sudo.extend(["-u", str(user)])
        shell = [*sudo, "--", *shell]
    command = f"{shlex.join(shell)} < {directory}/run.sh"
    if askpass:
        helper = f"{directory}/askpass"
        command = f"chmod 700 {helper} && SUDO_ASKPASS={helper} {command}"
    return command


def windows_exec_files(command: str, env: dict[str, str]) -> dict[str, bytes]:
    serialized = json.dumps(env)
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$variables = @'\n{serialized}\n'@ | ConvertFrom-Json\n"
        "foreach ($variable in $variables.PSObject.Properties) {\n"
        "  [Environment]::SetEnvironmentVariable($variable.Name, $variable.Value)\n"
        "}\n"
        "$command = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'command.txt'))\n"
        "$start = New-Object System.Diagnostics.ProcessStartInfo\n"
        "$start.FileName = 'cmd.exe'\n"
        "$start.Arguments = '/d /s /c \"' + $command + '\"'\n"
        "$start.UseShellExecute = $false\n"
        "$process = [Diagnostics.Process]::Start($start)\n"
        "$process.WaitForExit()\n"
        "exit $process.ExitCode\n"
    )
    return {
        "env.ps1": script.encode(),
        "command.txt": command.encode(),
    }


def windows_staged_exec(directory: str) -> str:
    script = quote_shell_arg(ntpath.join(directory, "env.ps1"), TaskOS.WINDOWS)
    return f"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File {script}"
