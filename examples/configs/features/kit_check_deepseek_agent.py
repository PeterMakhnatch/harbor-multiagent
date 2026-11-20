"""Run one text-only Kit Check seat through the DeepSeek API on the host."""

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from kit_check_codex_agent import (
    _ROLE_URLS,
    _action,
    _observe,
    _prompt,
    _seat_exec,
    _world_request,
)

_PROVIDER = "deepseek-host"
_MODEL = "deepseek-flash"
_AUTH = "deepseek-api-host"
_URL = "https://api.deepseek.com/chat/completions"
_MAX_OUTPUT_TOKENS = 512
_TIMEOUT_SEC = 60
_MAX_RESPONSE_BYTES = 1_048_576
# Prices checked 2026-11-20 at https://api-docs.deepseek.com/quick_start/pricing.
# Peak is 01:00-04:00 and 06:00-10:00 UTC, Monday through Friday.
_PRICE_PER_MILLION = {
    False: {"miss": 0.15, "hit": 0.003, "output": 0.60},
    True: {"miss": 0.30, "hit": 0.006, "output": 1.20},
}
_API_KEY: str | None = None


def set_api_key(key: str) -> None:
    """Keep the key in this process. Do not pass it through Harbor config."""
    global _API_KEY
    if not key or any(char.isspace() for char in key):
        raise ValueError("DeepSeek API key is empty")
    _API_KEY = key


def _count(usage: dict[str, Any], field: str) -> int | None:
    value = usage.get(field)
    return value if type(value) is int and value >= 0 else None


def _calculated_cost(
    input_tokens: int, cache_tokens: int, output_tokens: int, when: datetime
) -> tuple[float, str]:
    peak = when.weekday() < 5 and (1 <= when.hour < 4 or 6 <= when.hour < 10)
    rate = _PRICE_PER_MILLION[peak]
    cached = min(cache_tokens, input_tokens)
    cost = (
        (input_tokens - cached) * rate["miss"]
        + cached * rate["hit"]
        + output_tokens * rate["output"]
    ) / 1_000_000
    return cost, "peak" if peak else "off-peak"


def _request(prompt: str) -> dict[str, Any]:
    if _API_KEY is None:
        raise RuntimeError("DeepSeek API key is not set")
    body = json.dumps(
        {
            "model": _MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
    ).encode()
    request = urllib.request.Request(
        _URL,
        data=body,
        headers={
            "Authorization": f"Bearer {_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 402:
            raise RuntimeError("DeepSeek balance is too low") from None
        raise RuntimeError(f"DeepSeek request failed with HTTP {error.code}") from None
    except OSError:
        raise RuntimeError("Could not reach the DeepSeek API") from None
    if status != 200 or len(payload) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("DeepSeek returned an unusable response")
    try:
        parsed = json.loads(payload)
    except ValueError:
        raise RuntimeError("DeepSeek returned invalid JSON") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("DeepSeek returned invalid JSON")
    return parsed


def _message(payload: dict[str, Any], context: AgentContext) -> str:
    choices = payload.get("choices")
    raw_usage = payload.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    input_tokens = _count(usage, "prompt_tokens")
    output_tokens = _count(usage, "completion_tokens")
    cache_field = _count(usage, "prompt_cache_hit_tokens")
    cache_tokens = 0 if cache_field is None else cache_field
    context.n_input_tokens = input_tokens
    context.n_cache_tokens = cache_field
    context.n_output_tokens = output_tokens
    if input_tokens is not None and output_tokens is not None:
        cost, rate = _calculated_cost(
            input_tokens, cache_tokens, output_tokens, datetime.now(timezone.utc)
        )
        if cache_field is None:
            rate += "-cache-unknown"
        context.metadata = {
            **(context.metadata or {}),
            "calculated_cost_usd": cost,
            "price_rate": rate,
        }
    if (
        not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
    ):
        raise RuntimeError("DeepSeek did not return one choice")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise RuntimeError("DeepSeek did not finish the Kit Check turn")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("tool_calls"):
        raise RuntimeError("DeepSeek attempted a tool in a text-only Kit Check turn")
    content = message.get("content")
    if not isinstance(content, str):
        raise RuntimeError("DeepSeek did not return a Kit Check text message")
    return content


class DeepSeekKitAgent(BaseAgent):
    """Adapt one Kit Check seat to one host-side DeepSeek API turn."""

    def __init__(
        self, logs_dir: Path, model_name: str | None = None, *, role: str, **kwargs: Any
    ) -> None:
        if role not in ("left", "right", "builder"):
            raise ValueError("Kit Check role must be left, right, or builder")
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        if (
            self._parsed_model_provider != _PROVIDER
            or self._parsed_model_name != _MODEL
        ):
            raise ValueError(
                "Kit Check DeepSeek seats use deepseek-host/deepseek-flash"
            )
        self._role = role

    @staticmethod
    @override
    def _parse_model_name_for_connection(
        model_name: str, *, skip_model_parsing: bool = False
    ) -> tuple[str | None, str]:
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
        return "kit-check-deepseek"

    @override
    def version(self) -> str | None:
        return "1"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        await _seat_exec(
            environment,
            ["python3", "-I", "-c", "import urllib.request"],
            "Kit Check requires Python 3 with urllib.request in the seat",
        )

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        observation = await _observe(environment, self._role)
        prompt = _prompt(instruction, self._role, observation)
        started = monotonic()
        try:
            payload = _request(prompt)
        finally:
            context.metadata = {
                **(context.metadata or {}),
                "model_turn_seconds": monotonic() - started,
                "model_auth": _AUTH,
            }
        action = _action(_message(payload, context), self._role)
        status, _ = await _world_request(
            environment, "POST", _ROLE_URLS[self._role], action
        )
        if status != 201:
            raise RuntimeError(f"Kit Check world POST returned HTTP {status}")
