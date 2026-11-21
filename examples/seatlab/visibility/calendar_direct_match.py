"""Match one first-party response to its independent, settled usage record."""

from typing import Any

from . import calendar_client, calendar_turns, deepseek

_RESULT = (
    "response_sha256",
    "served_model",
    "input_tokens",
    "cache_tokens",
    "output_tokens",
)
_KEYS = {"schema", "attempt", "status", "run", "role", "prompt_sha256", *_RESULT}


def match(
    receipt: dict[str, Any],
    row: dict[str, Any] | None,
    *,
    run: str,
    role: str,
    prompt_digest: str,
    response_digest: str | None,
) -> str:
    """Return a fixed category, never provider data or the action text."""
    if (
        receipt.keys() != _KEYS
        or receipt["schema"] != calendar_client._METER_SCHEMA
        or type(receipt["attempt"]) is not int
        or receipt["attempt"] != 1
        or (receipt["run"], receipt["role"]) != (run, role)
    ):
        return "meter_invalid"
    if receipt["prompt_sha256"] != prompt_digest:
        return "prompt_mismatch"
    if receipt["status"] == "failed":
        return (
            "meter_failed"
            if all(receipt[key] is None for key in _RESULT)
            else "meter_invalid"
        )
    if receipt["status"] != "success":
        return "meter_invalid"
    if response_digest is None:
        return "response_unverified"
    if receipt["response_sha256"] != response_digest:
        return "response_mismatch"
    if row is not None and (row.get("run"), row.get("role")) != (run, role):
        return "ledger_mismatch"
    try:
        deepseek._served_model(receipt["served_model"])
        calendar_turns._join_usage(receipt, row, require_model=True)
    except deepseek.SeatUnavailable:
        return "meter_invalid"
    except calendar_turns.TurnError as exc:
        return "meter_invalid" if exc.category == "receipt_invalid" else exc.category
    return "matched"
