"""Run Kit Check seats with a host Codex subscription or scripted reviewers."""

import asyncio
import json
import re
import shlex
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, Literal, override

from codex_text_turn import AUTH, PROVIDER, completion, json_object, run_codex

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
_BUILDER_WAIT_SEC = 150

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
    observation = json_object(body, error)
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
    value = json_object(text, error)
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
            self._parsed_model_provider != PROVIDER
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
            if separator and provider == PROVIDER:
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
        return "2"

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
                return_code, stdout, stderr = await run_codex(
                    self._native_model, prompt, scratch
                )
            finally:
                context.metadata = {
                    **(context.metadata or {}),
                    "codex_turn_seconds": monotonic() - started,
                    "codex_auth": AUTH,
                }
        text = completion(return_code, stdout, stderr, context)
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
