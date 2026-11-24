"""Prompt-free accounting projections. Native usage, list prices and bills are different evidence."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from .seat_stream import (
    DISPOSITIONS,
    MAX_SEATS,
    SEAT,
    UUID,
    Malformed,
    Record,
    _require,
)

SCHEMA = "seatlab.team-account/v1"
DIGEST = re.compile(r"[a-f0-9]{64}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,191}")
USD_TEXT = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]+)?")
MAX_ATTEMPTS = 4096
MAX_SEAT_ROWS = 65_536
MAX_CALLS = 1_000_000
MAX_TOKENS = 2**53 - 1
SOURCES = {"native-job", "calendar-repeatability"}
GRADES = {"valid", "invalid", "unknown"}
PACKET_KEYS = {
    "schema",
    "source",
    "scope",
    "registered",
    "retries",
    "job_closed",
    "attempts",
}
ATTEMPT_KEYS = {"trial", "slot", "grade_state", "reward", "seats"}
SEAT_KEYS = {
    "name",
    "under_test",
    "disposition",
    "reported_model",
    "served_models",
    "harness",
    "reported_provider",
    "input_tokens",
    "cache_tokens",
    "output_tokens",
    "harness_usd",
    "priced_low_usd",
    "priced_high_usd",
    "calls",
    "held_calls",
}


def money(value: object) -> str:
    if type(value) not in (int, float, str, Decimal) or len(str(value)) > 100:
        raise Malformed("invalid USD amount")
    _require(USD_TEXT.fullmatch(str(value)) is not None, "invalid USD amount")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as error:
        raise Malformed("invalid USD amount") from error
    _require(
        amount.is_finite() and amount >= 0, "USD amount must be finite and nonnegative"
    )
    if not amount:
        return "0"
    exponent = amount.as_tuple().exponent
    _require(
        isinstance(exponent, int) and -36 <= exponent <= 15 and amount.adjusted() <= 15,
        "USD amount out of supported range",
    )
    text = format(amount, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _count(value: Any, limit: int) -> bool:
    return type(value) is int and 0 <= value <= limit


def _id(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _optional_money(value: str | None) -> str | None:
    return None if value is None else money(value)


def seat(
    name: str,
    *,
    under_test: bool | None = None,
    disposition: str | None = None,
    harness: str | None = None,
    reported_provider: str | None = None,
    reported_model: str | None = None,
    served_models: tuple[str, ...] = (),
    input_tokens: int | None = None,
    cache_tokens: int | None = None,
    output_tokens: int | None = None,
    harness_usd: str | None = None,
    priced_low_usd: str | None = None,
    priced_high_usd: str | None = None,
    calls: int | None = None,
    held_calls: int | None = None,
) -> Record:
    _require(_id(name, SEAT), "invalid seat identifier")
    _require(under_test is None or type(under_test) is bool, "invalid seat test flag")
    _require(
        disposition is None
        or (type(disposition) is str and disposition in DISPOSITIONS),
        "invalid seat disposition",
    )
    _require(harness is None or _id(harness, MODEL), "unsupported harness label")
    _require(
        reported_provider is None or _id(reported_provider, SEAT),
        "unsupported provider route label",
    )
    _require(
        reported_model is None or _id(reported_model, MODEL),
        "unsupported native model label",
    )
    _require(
        type(served_models) in (tuple, list) and len(served_models) <= 64,
        "invalid served model inventory",
    )
    _require(
        all(_id(item, MODEL) for item in served_models),
        "unsupported served model label",
    )
    _require(
        len(set(served_models)) == len(served_models), "duplicate served model label"
    )
    _require(
        all(
            v is None or _count(v, MAX_TOKENS)
            for v in (input_tokens, cache_tokens, output_tokens)
        ),
        "invalid token count",
    )
    _require(
        input_tokens is None or cache_tokens is None or cache_tokens <= input_tokens,
        "cached input exceeds total input",
    )
    _require(
        all(v is None or _count(v, MAX_CALLS) for v in (calls, held_calls)),
        "invalid call count",
    )
    _require(
        held_calls is None or (calls is not None and held_calls <= calls),
        "unsettled calls exceed recorded calls",
    )
    low, high = _optional_money(priced_low_usd), _optional_money(priced_high_usd)
    _require((low is None) == (high is None), "both listed price bounds are required")
    _require(
        low is None or (high is not None and Decimal(low) <= Decimal(high)),
        "reversed listed price bounds",
    )
    return {
        "name": name,
        "under_test": under_test,
        "disposition": disposition,
        "harness": harness,
        "reported_provider": reported_provider,
        "reported_model": reported_model,
        "served_models": sorted(served_models),
        "input_tokens": input_tokens,
        "cache_tokens": cache_tokens,
        "output_tokens": output_tokens,
        "harness_usd": _optional_money(harness_usd),
        "priced_low_usd": low,
        "priced_high_usd": high,
        "calls": calls,
        "held_calls": held_calls,
    }


def _fields(value: object, keys: set[str], reason: str) -> Record:
    if not isinstance(value, dict) or value.keys() != keys:
        raise Malformed(reason)
    return cast(Record, value)


def _seat_row(value: object) -> Record:
    return seat(**_fields(value, SEAT_KEYS, "unsupported seat packet fields"))


def attempt(
    trial: str, slot: str, grade_state: str, reward: float | None, seats: list[Record]
) -> Record:
    _require(_id(trial, UUID) and _id(slot, DIGEST), "invalid attempt or slot identity")
    _require(type(grade_state) is str and grade_state in GRADES, "invalid grade state")
    if grade_state == "valid":
        _require(
            type(reward) in (int, float)
            and reward is not None
            and -1e100 <= reward <= 1e100,
            "invalid numeric reward",
        )
    else:
        _require(reward is None, "unverified grades cannot carry a reward")
    _require(type(seats) is list and len(seats) <= MAX_SEATS, "invalid attempt roster")
    names = [s["name"] for s in seats]
    _require(len(set(names)) == len(names), "duplicate seat in attempt")
    return {
        "trial": trial,
        "slot": slot,
        "grade_state": grade_state,
        "reward": reward,
        "seats": sorted(seats, key=lambda s: s["name"]),
    }


def _attempt_row(value: object) -> Record:
    row = _fields(value, ATTEMPT_KEYS, "unsupported attempt packet fields")
    roster = row["seats"]
    _require(
        isinstance(roster, list) and len(roster) <= MAX_SEATS, "invalid attempt roster"
    )
    return attempt(
        row["trial"],
        row["slot"],
        row["grade_state"],
        row["reward"],
        [_seat_row(s) for s in roster],
    )


def packet(
    source: str,
    scope: str,
    registered: int,
    retries: int | None,
    attempts: list[Record],
    *,
    job_closed: bool | None = None,
) -> Record:
    _require(type(source) is str and source in SOURCES, "unsupported account source")
    _require(
        _id(scope, UUID if source == "native-job" else DIGEST), "invalid scope identity"
    )
    _require(_count(registered, MAX_ATTEMPTS), "invalid registered slot count")
    _require(retries is None or _count(retries, 16_384), "invalid retry count")
    _require(
        job_closed is None or type(job_closed) is bool, "invalid job closure state"
    )
    _require(
        type(attempts) is list and len(attempts) <= registered,
        "surviving attempts exceed registered slots",
    )
    _require(
        sum(len(a["seats"]) for a in attempts) <= MAX_SEAT_ROWS,
        "packet has too many seat rows",
    )
    for field in ("trial", "slot"):
        _require(
            len({a[field] for a in attempts}) == len(attempts),
            "duplicate native execution or final slot",
        )
    return {
        "schema": SCHEMA,
        "source": source,
        "scope": scope,
        "registered": registered,
        "retries": retries,
        "job_closed": job_closed,
        "attempts": sorted(attempts, key=lambda a: a["trial"]),
    }


def validate(value: object) -> Record:
    row = _fields(value, PACKET_KEYS, "unsupported team account packet")
    _require(row["schema"] == SCHEMA, "unsupported team account packet")
    attempts = row["attempts"]
    _require(
        type(attempts) is list and len(attempts) <= MAX_ATTEMPTS,
        "invalid attempt inventory",
    )
    return packet(
        row["source"],
        row["scope"],
        row["registered"],
        row["retries"],
        [_attempt_row(a) for a in attempts],
        job_closed=row["job_closed"],
    )
