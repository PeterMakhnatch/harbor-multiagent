"""Make one bounded direct DeepSeek seat turn on the host key."""

import json
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from http.client import HTTPException, HTTPSConnection
from typing import Any

import certifi

MODEL = "deepseek-flash"
REVISION = "DeepSeek-V4.1-Flash-1017"
_ACCEPTED_MODELS = frozenset({MODEL, "deepseek-v4.1-flash-1017"})
_HOST = "api.deepseek.com"
_URL = "https://api.deepseek.com"
_SYSTEM = "Answer only with JSON."
_MAX_PAYLOAD_BYTES = 8192
_MAX_RESPONSE_BYTES = 64 * 1024
_BALANCE_TIMEOUT = 10
_GENERATION_TIMEOUT = 60
_MIN_BALANCE = Decimal("1.50")
_KEY: str | None = None
_BOUND = False


def _budget() -> Any:
    from . import spend

    return spend


class SeatUnavailable(RuntimeError):
    """A fixed safe category, without provider text or account data."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True, slots=True)
class SeatTurn:
    """Text and the served model from the same completed provider response."""

    text: str
    served_model: str


def bind_host_key() -> None:
    """Move the host key into this module before any child environment starts."""
    global _BOUND, _KEY
    if _BOUND:
        raise SeatUnavailable("key_already_bound")
    key = os.environ.pop("DEEPSEEK_API_KEY", "")
    _BOUND = True
    if key:
        _KEY = key


def _key() -> str:
    key = _KEY if _BOUND else os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise SeatUnavailable("key_unavailable")
    return key


def _request(path: str, body: bytes | None, timeout: int) -> dict[str, Any]:
    if path not in {"/user/balance", "/chat/completions"}:
        raise SeatUnavailable("route_unverified")
    connection = HTTPSConnection(
        _HOST,
        timeout=timeout,
        context=ssl.create_default_context(cafile=certifi.where()),
    )
    try:
        headers = {"Authorization": f"Bearer {_key()}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request("GET" if body is None else "POST", path, body, headers)
        response = connection.getresponse()
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(raw) > _MAX_RESPONSE_BYTES:
            raise SeatUnavailable("provider_unavailable")
        value = json.loads(raw)
    except SeatUnavailable:
        raise
    except TimeoutError:
        raise SeatUnavailable("provider_timeout") from None
    except (OSError, HTTPException):
        raise SeatUnavailable("provider_unavailable") from None
    except ValueError:
        raise SeatUnavailable("provider_response_invalid") from None
    finally:
        connection.close()
    if not isinstance(value, dict):
        raise SeatUnavailable("provider_response_invalid")
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str) or not value:
        raise SeatUnavailable("balance_unverified")
    try:
        amount = Decimal(value)
    except (ValueError, InvalidOperation):
        raise SeatUnavailable("balance_unverified") from None
    if not amount.is_finite():
        raise SeatUnavailable("balance_unverified")
    return amount


def _balance() -> Decimal:
    document = _request("/user/balance", None, _BALANCE_TIMEOUT)
    balances = document.get("balance_infos")
    if document.get("is_available") is not True or not isinstance(balances, list):
        raise SeatUnavailable("balance_unverified")
    usd = [
        row
        for row in balances
        if isinstance(row, dict) and row.get("currency") == "USD"
    ]
    if len(usd) != 1:
        raise SeatUnavailable("balance_unverified")
    row = usd[0]
    total = _decimal(row.get("total_balance"))
    parts = _decimal(row.get("granted_balance")) + _decimal(row.get("topped_up_balance"))
    if total != parts or total < _MIN_BALANCE:
        raise SeatUnavailable("balance_below_minimum")
    return total


def preflight() -> dict[str, str | bool]:
    """Check the live direct balance without reserving spend or calling a model."""
    return {
        "provider": "deepseek",
        "endpoint": _URL,
        "requested_model": MODEL,
        "revision": REVISION,
        "balance_ok": _balance() >= _MIN_BALANCE,
    }


def _payload(prompt: str) -> bytes:
    if not isinstance(prompt, str) or not prompt:
        raise SeatUnavailable("payload_invalid")
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.7,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(body) > _MAX_PAYLOAD_BYTES:
        raise SeatUnavailable("payload_too_large")
    return body


def _usage(document: dict[str, Any]) -> tuple[int, int, int]:
    usage = document.get("usage")
    if not isinstance(usage, dict):
        raise SeatUnavailable("usage_unverified")
    values = [
        usage.get(name)
        for name in (
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "completion_tokens",
        )
    ]
    if any(type(value) is not int or value < 0 for value in values):
        raise SeatUnavailable("usage_unverified")
    hit, miss, output = values
    if usage.get("prompt_tokens") != hit + miss or usage.get("total_tokens") != hit + miss + output:
        raise SeatUnavailable("usage_unverified")
    return hit, miss, output


def _served_model(value: object) -> str:
    if not isinstance(value, str) or value.casefold() not in _ACCEPTED_MODELS:
        raise SeatUnavailable("served_model_unverified")
    return value


def _text(document: dict[str, Any]) -> str:
    choices = document.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise SeatUnavailable("provider_response_invalid")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise SeatUnavailable("provider_response_invalid")
    return content


def turn(prompt: str, *, run_id: str, role: str) -> SeatTurn:
    """Reserve, call once, and settle only verified usage from that response."""
    body = _payload(prompt)
    balance = _balance()
    budget = _budget()
    try:
        budget.reserve(run_id, role, balance_usd=balance)
    except budget.SpendRefused:
        raise SeatUnavailable("budget_guard") from None
    started = datetime.now(UTC)
    document = _request("/chat/completions", body, _GENERATION_TIMEOUT)
    finished = datetime.now(UTC)
    hit, miss, output = _usage(document)
    served = _served_model(document.get("model"))
    budget.settle(
        run_id,
        role,
        cache_hit=hit,
        cache_miss=miss,
        output=output,
        served_model=served,
        started_at=started,
        finished_at=finished,
    )
    return SeatTurn(_text(document), served)


def ask(prompt: str, *, run_id: str, role: str) -> str:
    """Return only the text from one settled direct turn."""
    return turn(prompt, run_id=run_id, role=role).text
