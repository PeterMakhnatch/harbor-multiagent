"""Run Kit Check seats with a host Codex subscription or scripted reviewers."""

import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Literal, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext

_ROLE_URLS = {
    "left": "http://world:8080/review/left",
    "right": "http://world:8080/review/right",
    "builder": "http://world:8080/builder",
}
_KITS = ("K1", "K2", "K3", "K4")
_CASES = 24
_WORLD_TIMEOUT_SEC = 8
_BUILDER_WAIT_SEC = 60
_LOGIN_TIMEOUT_SEC = 10
_CODEX_TIMEOUT_SEC = 120
_MAX_CODEX_OUTPUT_BYTES = 1_048_576  # Per stream.
_PROVIDER = "openai-codex"
_AUTH = "chatgpt-subscription"
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

# Use a direct request to the fixed endpoint, without environment proxies or
# redirects. Only GET results need a body; POST reports only the status.
_WORLD_REQUEST = """\
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

method, url = sys.argv[1:3]
data = sys.argv[3].encode("ascii") if method == "POST" else None
headers = {"Content-Type": "application/json"} if data is not None else {}
opener = build_opener(ProxyHandler({}), NoRedirects())
try:
    with opener.open(Request(url, data=data, headers=headers, method=method), timeout=4) as response:
        body = response.read(4097) if method == "GET" else b""
        if len(body) > 4096:
            raise ValueError("Kit Check response is too large")
        print(response.status)
        print(body.decode("utf-8"))
except HTTPError as error:
    error.close()
    print(error.code)
"""


def _codex_env() -> dict[str, str]:
    return {
        key: os.environ[key]
        for key in ("HOME", "PATH", "USER", "LANG")
        if key in os.environ
    }


def check_subscription() -> None:
    """Fail before a trial unless the local login is ChatGPT OAuth."""
    try:
        with TemporaryDirectory(prefix="kit-check-codex-", dir="/tmp") as scratch:
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
        raise RuntimeError("Kit Check requires a Codex ChatGPT subscription login")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON keys")
    return result


def _json_object(raw: str | bytes, message: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        raise RuntimeError(message) from None
    if not isinstance(value, dict):
        raise RuntimeError(message)
    return value


async def _seat_exec(
    environment: BaseEnvironment, args: list[str], error: str
) -> ExecResult:
    try:
        async with asyncio.timeout(_WORLD_TIMEOUT_SEC):
            result = await environment.exec(
                shlex.join(args), timeout_sec=_WORLD_TIMEOUT_SEC
            )
    except Exception:
        raise RuntimeError(error) from None
    if result.return_code != 0:
        raise RuntimeError(error)
    return result


async def _world_request(
    environment: BaseEnvironment,
    method: Literal["GET", "POST"],
    url: str,
    action: dict[str, Any] | None = None,
) -> tuple[int, str]:
    args = ["python3", "-I", "-c", _WORLD_REQUEST, method, url]
    if action is not None:
        args.append(json.dumps(action, separators=(",", ":")))
    error = f"Kit Check world {method} failed"
    result = await _seat_exec(environment, args, error)
    status, _, body = (result.stdout or "").partition("\n")
    if not re.fullmatch(r"[1-5][0-9]{2}", status):
        raise RuntimeError(error)
    return int(status), body


def _is_kit(value: object) -> bool:
    return isinstance(value, str) and value in _KITS


def _is_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_is_kit(kit) for kit in value)
        and value[0] != value[1]
    )


def _is_batch(value: object) -> bool:
    return isinstance(value, list) and len(value) == _CASES


def _is_review(value: dict[str, Any]) -> bool:
    return (
        value.keys() == {"allowed"}
        and _is_batch(value["allowed"])
        and all(_is_pair(pair) for pair in value["allowed"])
    )


def _is_builder_observation(value: dict[str, Any]) -> bool:
    if value.keys() != {"choices", "inboxes"}:
        return False
    choices, inboxes = value["choices"], value["inboxes"]
    return (
        isinstance(choices, list)
        and len(choices) == len(_KITS)
        and all(_is_kit(kit) for kit in choices)
        and set(choices) == set(_KITS)
        and _is_batch(inboxes)
        and all(
            isinstance(inbox, dict)
            and inbox.keys() == {"left", "right"}
            and all(pair is None or _is_pair(pair) for pair in inbox.values())
            for inbox in inboxes
        )
    )


async def _observe(environment: BaseEnvironment, role: str) -> dict[str, Any]:
    url = _ROLE_URLS[role]
    if role == "builder":
        try:
            async with asyncio.timeout(_BUILDER_WAIT_SEC):
                while True:
                    status, body = await _world_request(environment, "GET", url)
                    if status != HTTPStatus.CONFLICT:
                        break
                    await asyncio.sleep(1)
        except TimeoutError:
            raise RuntimeError(
                "Kit Check builder timed out waiting for both reviews"
            ) from None
    else:
        status, body = await _world_request(environment, "GET", url)
    if status != HTTPStatus.OK:
        raise RuntimeError(f"Kit Check world GET returned HTTP {status}")
    error = "Invalid Kit Check observation"
    observation = _json_object(body, error)
    validate = _is_builder_observation if role == "builder" else _is_review
    if not validate(observation):
        raise RuntimeError(error)
    return observation


async def _setup_seat(environment: BaseEnvironment) -> None:
    await _seat_exec(
        environment,
        ["python3", "-I", "-c", "import urllib.request"],
        "Kit Check requires Python 3 with urllib.request in the seat",
    )


async def _submit(
    environment: BaseEnvironment, role: str, action: dict[str, Any]
) -> None:
    status, _ = await _world_request(environment, "POST", _ROLE_URLS[role], action)
    if status != HTTPStatus.CREATED:
        raise RuntimeError(f"Kit Check world POST returned HTTP {status}")


def _prompt(instruction: str, role: str, observation: dict[str, Any]) -> str:
    if role == "builder":
        instruction = (
            "You are the builder. For each case, pick the sole kit shared by "
            "both reports if there is one. Otherwise choose a kit from the "
            "observation. There is no prescribed fallback."
        )
        schema = 'Return {"kits":[...]} with 24 kit IDs from choices, in case order.'
    else:
        schema = (
            'Return {"allowed":[...]} with 24 pairs of distinct kit IDs, in case order.'
        )
    observation_json = json.dumps(observation, separators=(",", ":"))
    return (
        f"Seat instruction:\n{instruction}\n\n"
        f"Observation (already fetched):\n{observation_json}\n\n"
        f"{schema} Return only the JSON object. The adapter sends it for you. "
        "Do not use tools."
    )


def _action(text: str, role: str) -> dict[str, Any]:
    error = "Codex returned an invalid Kit Check action"
    text = text.strip()
    if text.startswith("```"):
        fence = re.fullmatch(
            r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL | re.IGNORECASE
        )
        if fence is None:
            raise RuntimeError(error)
        text = fence[1]
    value = _json_object(text, error)
    if role == "builder":
        valid = (
            value.keys() == {"kits"}
            and _is_batch(value["kits"])
            and all(_is_kit(kit) for kit in value["kits"])
        )
    else:
        valid = _is_review(value)
    if not valid:
        raise RuntimeError(error)
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


async def _run_codex(model: str, prompt: str, scratch: str) -> tuple[int, bytes, bytes]:
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
                    raise RuntimeError("Codex exceeded the Kit Check output limit")
                return await process.wait(), out, err
            except BaseException:
                _kill_codex(process.pid)
                # Drain the closed pipes and reap the child.
                await process.communicate()
                raise
    except TimeoutError:
        raise RuntimeError("Codex Kit Check turn timed out") from None
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


def _completion(
    return_code: int, stdout: bytes, stderr: bytes, context: AgentContext
) -> str:
    quota = "Codex ChatGPT subscription quota exhausted"
    if return_code != 0:
        if _quota_error(stderr) or _quota_error(stdout):
            raise RuntimeError(quota)
        raise RuntimeError("Codex Kit Check turn failed")

    turns = []
    messages = []
    unexpected_event = False
    turn_started = False
    tool_use = False
    tool_types: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = _json_object(line, "Invalid Codex JSON event")
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
                raise RuntimeError(quota)
            unexpected_event = True

    if len(turns) == 1:
        (usage,) = turns
        context.n_input_tokens = _usage_count(usage, "input_tokens")
        context.n_cache_tokens = _usage_count(usage, "cached_input_tokens")
        context.n_output_tokens = _usage_count(usage, "output_tokens")
    if tool_use:
        detail = ", ".join(tool_types) if tool_types else "unrecognized tool type"
        raise RuntimeError(
            f"Codex attempted a tool in a text-only Kit Check turn: {detail}"
        )
    if unexpected_event or len(turns) != 1 or len(messages) != 1:
        raise RuntimeError("Codex did not complete a single text-only Kit Check turn")
    (message,) = messages
    if not isinstance(message, str):
        raise RuntimeError("Codex did not return a Kit Check text message")
    return message


class CodexKitAgent(BaseAgent):
    """Adapt one Kit Check seat to one host-side Codex exec turn."""

    def __init__(
        self, logs_dir: Path, model_name: str | None = None, *, role: str, **kwargs: Any
    ) -> None:
        if role not in ("left", "right", "builder"):
            raise ValueError("Kit Check role must be left, right, or builder")
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        model = self._parsed_model_name
        if (
            self._parsed_model_provider != _PROVIDER
            or not model
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", model)
        ):
            raise ValueError("Kit Check requires an openai-codex/<native-id> model")
        self._native_model = model
        self._role = role

    @staticmethod
    @override
    def _parse_model_name_for_connection(
        model_name: str, *, skip_model_parsing: bool = False
    ) -> tuple[str | None, str]:
        # Register the native prefix with BaseAgent's parsing hook. It is not
        # a LiteLLM provider and must not select a LiteLLM credential route.
        if not skip_model_parsing:
            provider, separator, model = model_name.partition("/")
            if separator and provider == _PROVIDER:
                return provider, model
        return BaseAgent._parse_model_name_for_connection(
            model_name, skip_model_parsing=skip_model_parsing
        )

    @staticmethod
    @override
    def name() -> str:
        return "kit-check-codex"

    @override
    def version(self) -> str | None:
        return "1"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        await _setup_seat(environment)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        observation = await _observe(environment, self._role)
        prompt = _prompt(instruction, self._role, observation)
        with TemporaryDirectory(prefix="kit-check-codex-", dir="/tmp") as scratch:
            started = monotonic()
            try:
                return_code, stdout, stderr = await _run_codex(
                    self._native_model, prompt, scratch
                )
            finally:
                context.metadata = {
                    **(context.metadata or {}),
                    "codex_turn_seconds": monotonic() - started,
                    "codex_auth": _AUTH,
                }
        text = _completion(return_code, stdout, stderr, context)
        action = _action(text, self._role)
        await _submit(environment, self._role, action)


class ScriptedKitReviewer(BaseAgent):
    """Submit the validated allowed lists from one reviewer seat."""

    def __init__(
        self, logs_dir: Path, model_name: str | None = None, *, role: str, **kwargs: Any
    ) -> None:
        if role not in ("left", "right"):
            raise ValueError("Scripted Kit Check reviewer role must be left or right")
        if model_name is not None:
            raise ValueError("Scripted Kit Check reviewers do not use a model")
        super().__init__(logs_dir=logs_dir, **kwargs)
        self._role = role

    @staticmethod
    @override
    def name() -> str:
        return "kit-check-scripted-reviewer"

    @override
    def version(self) -> str | None:
        return "1"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        await _setup_seat(environment)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        observation = await _observe(environment, self._role)
        await _submit(environment, self._role, observation)
