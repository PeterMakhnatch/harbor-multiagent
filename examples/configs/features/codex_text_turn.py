"""Run a single text-only turn with the local Codex subscription."""

import asyncio
import json
import os
import re
import signal
import subprocess
from contextlib import suppress
from tempfile import TemporaryDirectory
from typing import Any

from harbor.models.agent.context import AgentContext

AUTH = "chatgpt-subscription"
PROVIDER = "openai-codex"
QUOTA_ERROR = "Codex ChatGPT subscription quota exhausted"

_LOGIN_TIMEOUT_SEC = 10
_CODEX_TIMEOUT_SEC = 120
_MAX_CODEX_OUTPUT_BYTES = 1_048_576  # Per stream.
_CODEX_TOOL_CONFIG = (
    "--disable",
    "shell_tool",
    "--disable",
    "unified_exec",
    "--disable",
    "browser_use",
    "--disable",
    "remote_plugin",
    "-c",
    'forced_login_method="chatgpt"',
    "-c",
    'model_provider="openai"',
    "-c",
    'model_reasoning_effort="low"',
    "-c",
    'web_search="disabled"',
)


def _codex_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("HOME", "PATH", "USER", "LANG")
        if key in os.environ
    }


def check_subscription() -> None:
    """Fail before a trial unless the local login is ChatGPT OAuth."""
    try:
        with TemporaryDirectory(prefix="codex-login-", dir="/tmp") as scratch:
            status = subprocess.run(
                ["codex", "login", "status"],
                cwd=scratch,
                env=_codex_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_LOGIN_TIMEOUT_SEC,
                check=False,
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Codex login status timed out") from None
    except OSError:
        raise RuntimeError("Could not start the local Codex CLI") from None

    lines = status.stdout.splitlines() + status.stderr.splitlines()
    if status.returncode != 0 or not any(
        line.strip() == b"Logged in using ChatGPT" for line in lines
    ):
        raise RuntimeError("A Codex ChatGPT subscription login is required")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON keys")
    return result


def json_object(raw: str | bytes, message: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise RuntimeError(message) from None
    if not isinstance(value, dict):
        raise RuntimeError(message)
    return value


def _kill_codex(pid: int) -> None:
    # Some CLI installations have a launcher process around the native binary.
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


async def _read_codex_output(stream: asyncio.StreamReader, pid: int) -> bytes | None:
    try:
        await stream.readexactly(_MAX_CODEX_OUTPUT_BYTES + 1)
    except asyncio.IncompleteReadError as error:
        return error.partial
    _kill_codex(pid)
    return None


async def run_codex(model: str, prompt: str, scratch: str) -> tuple[int, bytes, bytes]:
    try:
        async with asyncio.timeout(_CODEX_TIMEOUT_SEC):
            process = await asyncio.create_subprocess_exec(
                "codex",
                "exec",
                "-C",
                scratch,
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "-s",
                "read-only",
                "--json",
                "-m",
                model,
                *_CODEX_TOOL_CONFIG,
                prompt,
                cwd=scratch,
                env=_codex_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                if process.stdout is None or process.stderr is None:
                    raise RuntimeError("Codex output pipes are unavailable")
                async with asyncio.TaskGroup() as group:
                    stdout = group.create_task(
                        _read_codex_output(process.stdout, process.pid)
                    )
                    stderr = group.create_task(
                        _read_codex_output(process.stderr, process.pid)
                    )
                out, err = stdout.result(), stderr.result()
                if out is None or err is None:
                    raise RuntimeError("Codex exceeded the output limit")
                return await process.wait(), out, err
            except BaseException:
                _kill_codex(process.pid)
                # Drain the closed pipes and reap the child.
                await process.communicate()
                raise
    except TimeoutError:
        raise RuntimeError("Codex turn timed out") from None
    except OSError:
        raise RuntimeError("Could not run the local Codex CLI") from None


def _quota_error(output: bytes) -> bool:
    output = output.lower()
    return any(
        phrase in output
        for phrase in (
            b"usage_limit_reached",
            b"insufficient_quota",
            b"quota_exceeded",
            b"hit your usage limit",
            b"usage limit reached",
            b"usage limit has been reached",
            b"usage limit exhausted",
            b"quota exhausted",
            b"quota exceeded",
            b"exceeded your current quota",
        )
    )


def _usage_count(usage: Any, field: str) -> int | None:
    value = usage.get(field) if isinstance(usage, dict) else None
    return value if type(value) is int and value >= 0 else None


def completion(
    return_code: int, stdout: bytes, stderr: bytes, context: AgentContext
) -> str:
    if return_code != 0:
        if _quota_error(stderr) or _quota_error(stdout):
            raise RuntimeError(QUOTA_ERROR)
        raise RuntimeError("Codex turn failed")

    turns = []
    messages = []
    unexpected_event = False
    turn_started = False
    tool_use = False
    tool_types: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = json_object(line, "Invalid Codex JSON event")
        kind = event.get("type")
        if turns:
            unexpected_event = True
        if kind == "turn.completed":
            turns.append(event.get("usage"))
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                unexpected_event = True
                continue
            item_kind = item.get("type")
            if item_kind not in ("agent_message", "reasoning"):
                tool_use = True
                if (
                    isinstance(item_kind, str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", item_kind)
                    and item_kind not in tool_types
                ):
                    tool_types.append(item_kind)
            elif kind == "item.completed" and item_kind == "agent_message":
                messages.append(item.get("text"))
        elif kind == "turn.started":
            unexpected_event |= turn_started
            turn_started = True
        elif kind != "thread.started":
            if kind in ("error", "turn.failed") and (
                _quota_error(line) or _quota_error(stderr)
            ):
                raise RuntimeError(QUOTA_ERROR)
            unexpected_event = True

    if len(turns) == 1:
        (usage,) = turns
        context.n_input_tokens = _usage_count(usage, "input_tokens")
        context.n_cache_tokens = _usage_count(usage, "cached_input_tokens")
        context.n_output_tokens = _usage_count(usage, "output_tokens")
    if tool_use:
        detail = ", ".join(tool_types) if tool_types else "unrecognized tool type"
        raise RuntimeError(f"Codex attempted a tool in a text-only turn: {detail}")
    if unexpected_event or len(turns) != 1 or len(messages) != 1:
        raise RuntimeError("Codex did not complete a single text-only turn")
    (message,) = messages
    if not isinstance(message, str):
        raise RuntimeError("Codex did not return a text message")
    return message
