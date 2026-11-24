"""Shared rate card, seat limits and exact USD ceiling math for team previews."""

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from decimal import (
    ROUND_CEILING,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
)
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, override

from ...native_study import as_int, as_record, as_text, require
from ...seat_handoffs.native_records import read_record_with_digest

RATES_SCHEMA = "harbor.rate-card/v1"
LIMITS_SCHEMA = "harbor.seat-limits/v1"
CURRENCY = "USD"
RATE_SCALE = 6
TOKENS_PER_RATE = 10**RATE_SCALE
RATES_FIELDS = frozenset({"schema", "currency", "tokens_per_rate", "models"})
RATE_FIELDS = frozenset({"input_usd", "output_usd"})
LIMITS_FIELDS = frozenset({"schema", "attempts_per_ticket", "seats"})
MODEL_SEAT_FIELDS = frozenset(
    {
        "agent",
        "model",
        "max_calls",
        "max_input_tokens_per_call",
        "max_output_tokens_per_call",
    }
)
SCRIPTED_SEAT_FIELDS = frozenset({"agent", "model"})
ORACLE = "oracle"
SCRIPTED = (ORACLE, "nop")
MODEL_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._:+-]|/(?!/)){0,255}")
MAX_PATH_BYTES = 4096
MAX_RATES_BYTES = 64 << 10
MAX_LIMITS_BYTES = 16 << 10
MAX_MODELS = 256
MAX_SEATS = 16
MAX_ATTEMPTS = 16
MAX_CALLS = 100_000
MAX_TOKENS_PER_CALL = 10_000_000
MAX_DIAGNOSTIC = 512
RATE_TEXT = re.compile(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?")
USD_TEXT = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?")
ZERO = Decimal(0)
CENT = Decimal("0.01")
EXACT = Context(
    prec=96,
    rounding=ROUND_CEILING,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact, Rounded],
)
UP = Context(prec=96, rounding=ROUND_CEILING, traps=[InvalidOperation, Overflow])
ABOVE = "ceiling_above_declared"
WITHIN = "ceiling_at_or_below_declared"
EXIT_WITHIN = 0
EXIT_ABOVE = 1
EXIT_INVALID = 2
PREVIEW = "hypothetical cost ceiling"
ROUNDING = "exact decimal arithmetic; each USD figure rounds up to the cent"
CONDITIONS = (
    ("Each seat attempt makes at most max_calls billed requests."),
    ("max_calls counts every retry and every harness call, such as a summary."),
    ("Each request sends at most max_input_tokens_per_call input tokens."),
    ("Each request bills at most max_output_tokens_per_call output tokens."),
    ("Output tokens include reasoning tokens."),
    ("Something outside this tool must enforce these limits."),
    ("Each ticket or final slot starts at most attempts_per_ticket attempts."),
    ("attempts_per_ticket counts every retry and restart."),
    ("Each model seat bills only its named model."),
    ("Each rate is the highest rate the account can bill for that model."),
    ("That rate covers cache writes, long-context tiers and priority tiers."),
    ("All input tokens use the full input rate. No cache discount applies."),
    ("Scripted oracle and nop seats are priced at 0 USD."),
    ("A model call by a scripted seat is outside this ceiling."),
    ("Sandbox, compute, storage, tax and currency costs are outside this ceiling."),
)
NOT_CLAIMED = (
    ("a spend cap"),
    ("a measured or billed cost"),
    ("permission to start a trial"),
)


class Seat(NamedTuple):
    role: str
    agent: str
    model: str | None
    api_format: str | None
    limits: tuple[int, int, int] | None
    rates: tuple[Decimal, Decimal] | None


def _check_path(path: object, label: str) -> Path:
    require(isinstance(path, Path), f"{label} path is not a Path")
    assert isinstance(path, Path)
    size = len(os.fsencode(path))
    require(0 < size <= MAX_PATH_BYTES, f"{label} path exceeds {MAX_PATH_BYTES} bytes")
    return path


def read_json(path: Path, limit: int, label: str) -> tuple[dict[str, Any], str]:
    path = _check_path(path, label)
    try:
        found = read_record_with_digest(path, limit)
    except OSError as error:
        raise ValueError(
            f"{label} is not a readable regular non-symlink file ({error.strerror})"
        ) from None
    except ValueError as error:
        raise ValueError(f"{label}: {error}") from None
    require(found is not None, f"{label} does not exist")
    assert found is not None
    return found[0], "sha256:" + found[1]


def _amount(value: object, label: str, pattern: re.Pattern[str]) -> Decimal:
    amount = EXACT.create_decimal(as_text(value, label, pattern))
    require(amount > ZERO, f"{label} must be positive")
    return amount


def declared_usd(value: object) -> Decimal:
    amount = EXACT.create_decimal(as_text(value, "--declared-usd", USD_TEXT))
    require(amount >= ZERO, "--declared-usd must not be negative")
    return amount


def times(count: int, value: Decimal) -> Decimal:
    require(type(count) is int and count >= 0, "count must be a non-negative integer")
    return EXACT.multiply(Decimal(count), value)


def cents(value: Decimal) -> str:
    assert value >= ZERO
    return format(value.quantize(CENT, rounding=ROUND_CEILING, context=UP), "f")


def exact(value: Decimal) -> str:
    assert value >= ZERO
    return format(value, "f")


def rate_card(record: dict[str, Any]) -> dict[str, tuple[Decimal, Decimal]]:
    card = as_record(record, "rate card", RATES_FIELDS)
    require(card["schema"] == RATES_SCHEMA, f"rate card schema must be {RATES_SCHEMA}")
    require(card["currency"] == CURRENCY, f"rate card currency must be {CURRENCY}")
    unit = card["tokens_per_rate"]
    require(
        type(unit) is int and unit == TOKENS_PER_RATE,
        f"rate card tokens_per_rate must be {TOKENS_PER_RATE}",
    )
    models = as_record(card["models"], "rate card models")
    require(len(models) <= MAX_MODELS, f"rate card lists more than {MAX_MODELS} models")
    rates: dict[str, tuple[Decimal, Decimal]] = {}
    for position, (model, entry) in enumerate(models.items()):
        where = f"rate card model {position}"
        as_text(model, f"{where} name", MODEL_NAME)
        found = as_record(entry, where, RATE_FIELDS)
        rates[model] = (
            _amount(found["input_usd"], f"{where} input_usd", RATE_TEXT),
            _amount(found["output_usd"], f"{where} output_usd", RATE_TEXT),
        )
    assert len(rates) == len(models)
    return rates


def _scripted(role: str, entry: object, member: dict[str, Any]) -> Seat:
    where = f"{role} seat limits"
    agent = member["name"]
    require(
        member.get("model_name") is None,
        f"registered {role} participant is scripted but names a model",
    )
    found = as_record(entry, where, SCRIPTED_SEAT_FIELDS)
    require(
        found["agent"] == agent and found["model"] is None,
        f"{where} do not name the registered scripted participant",
    )
    return Seat(role, agent, None, None, None, None)


def _seat(
    role: str,
    entry: object,
    member: dict[str, Any],
    rates: dict[str, tuple[Decimal, Decimal]],
) -> Seat:
    agent = member["name"]
    if agent in SCRIPTED:
        return _scripted(role, entry, member)
    where = f"{role} seat limits"
    model = member.get("model_name")
    require(isinstance(model, str), f"registered {role} participant names no model")
    found = as_record(entry, where, MODEL_SEAT_FIELDS)
    require(
        found["agent"] == agent and found["model"] == model,
        f"{where} do not name the registered agent and model",
    )
    rate = rates.get(model)
    require(rate is not None, f"rate card has no exact rate for the {role} model")
    assert rate is not None
    limits = (
        as_int(found["max_calls"], f"{where} max_calls", 1, MAX_CALLS),
        as_int(
            found["max_input_tokens_per_call"],
            f"{where} max_input_tokens_per_call",
            1,
            MAX_TOKENS_PER_CALL,
        ),
        as_int(
            found["max_output_tokens_per_call"],
            f"{where} max_output_tokens_per_call",
            1,
            MAX_TOKENS_PER_CALL,
        ),
    )
    return Seat(role, agent, model, member.get("model_api_format"), limits, rate)


def seat_limits(
    record: dict[str, Any],
    members: dict[str, dict[str, Any]],
    rates: dict[str, tuple[Decimal, Decimal]],
) -> tuple[int, list[Seat]]:
    require(0 < len(members) <= MAX_SEATS, f"roster needs 1..{MAX_SEATS} seats")
    limits = as_record(record, "seat limits", LIMITS_FIELDS)
    require(
        limits["schema"] == LIMITS_SCHEMA, f"seat limits schema must be {LIMITS_SCHEMA}"
    )
    attempts = as_int(
        limits["attempts_per_ticket"],
        "seat limits attempts_per_ticket",
        1,
        MAX_ATTEMPTS,
    )
    seats = as_record(limits["seats"], "seat limits seats")
    require(
        set(seats) == set(members),
        f"seat limits must name exactly the {len(members)} registered seats",
    )
    found = [_seat(role, seats[role], members[role], rates) for role in members]
    return attempts, found


def load_costs(
    args: argparse.Namespace, members: dict[str, dict[str, Any]]
) -> tuple[int, list[Seat], dict[str, str]]:
    card, card_digest = read_json(args.rates, MAX_RATES_BYTES, "rate card")
    limits, limits_digest = read_json(args.limits, MAX_LIMITS_BYTES, "seat limits")
    attempts, seats = seat_limits(limits, members, rate_card(card))
    digests = {"rate_card_sha256": card_digest, "limits_sha256": limits_digest}
    return attempts, seats, digests


def _request_ceiling(seat: Seat) -> Decimal:
    assert seat.limits is not None and seat.rates is not None
    _, max_input, max_output = seat.limits
    input_rate, output_rate = seat.rates
    spend = EXACT.add(times(max_input, input_rate), times(max_output, output_rate))
    return spend.scaleb(-RATE_SCALE, EXACT)


def _attempt_ceiling(seat: Seat) -> Decimal:
    if seat.limits is None:
        return ZERO
    return times(seat.limits[0], _request_ceiling(seat))


def team_ceiling(seats: list[Seat]) -> tuple[dict[str, Decimal], Decimal]:
    per_seat = {seat.role: _attempt_ceiling(seat) for seat in seats}
    assert len(per_seat) == len(seats)
    team = ZERO
    for seat in seats:
        team = EXACT.add(team, per_seat[seat.role])
    return per_seat, team


def _seat_row(seat: Seat, attempt: Decimal, runs: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "seat": seat.role,
        "agent": seat.agent,
        "model": seat.model,
        "api_format": seat.api_format,
        "scripted": seat.limits is None,
        "max_calls": None,
        "max_input_tokens_per_call": None,
        "max_output_tokens_per_call": None,
        "input_usd_per_million_tokens": None,
        "output_usd_per_million_tokens": None,
        "request_ceiling_usd_exact": None,
        "attempt_ceiling_usd_exact": exact(attempt),
        "attempt_ceiling_usd": cents(attempt),
        "campaign_ceiling_usd": cents(times(runs, attempt)),
    }
    if seat.limits is not None and seat.rates is not None:
        max_calls, max_input, max_output = seat.limits
        row["max_calls"] = max_calls
        row["max_input_tokens_per_call"] = max_input
        row["max_output_tokens_per_call"] = max_output
        row["input_usd_per_million_tokens"] = exact(seat.rates[0])
        row["output_usd_per_million_tokens"] = exact(seat.rates[1])
        row["request_ceiling_usd_exact"] = exact(_request_ceiling(seat))
    return row


def seat_rows(
    seats: list[Seat], per_seat: dict[str, Decimal], runs: int
) -> list[dict[str, Any]]:
    return [_seat_row(seat, per_seat[seat.role], runs) for seat in seats]


def verdict(total: Decimal, declared: Decimal) -> dict[str, str]:
    ceiling = cents(total)
    return {
        "ceiling_usd_exact": exact(total),
        "ceiling_usd": ceiling,
        "declared_usd": format(declared.quantize(CENT, context=EXACT), "f"),
        "decision": ABOVE if Decimal(ceiling) > declared else WITHIN,
    }


def _scrub(text: str, paths: list[Path]) -> str:
    forms = {
        form
        for path in paths
        for form in (str(path), os.path.abspath(path), os.path.realpath(path))
        if form
    }
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, "<input>")
    clean = "".join(char if " " <= char <= "~" else "?" for char in text[:4096])
    return clean[:MAX_DIAGNOSTIC]


def _reason(error: BaseException) -> str:
    if isinstance(error, OSError):
        return error.strerror or "storage failed"
    if isinstance(error, ArithmeticError):
        return "decimal arithmetic left its exact bound"
    return str(error)


def run(
    builder: Callable[[argparse.Namespace], dict[str, Any]],
    args: argparse.Namespace,
    inputs: list[Path],
    label: str,
) -> int:
    try:
        result = builder(args)
    except (OSError, ValueError, ArithmeticError) as error:
        print(f"{label}: {_scrub(_reason(error), inputs)}", file=sys.stderr)
        return EXIT_INVALID
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return EXIT_ABOVE if result["decision"] == ABOVE else EXIT_WITHIN


class QuietParser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(EXIT_INVALID, f"{self.prog}: invalid arguments; see --help\n")


def new_parser(prog: str, description: str) -> argparse.ArgumentParser:
    found = QuietParser(prog=prog, description=description, allow_abbrev=False)
    found.add_argument("--rates", type=Path, required=True, metavar="RATE_CARD")
    found.add_argument("--limits", type=Path, required=True, metavar="SEAT_LIMITS")
    found.add_argument("--declared-usd", required=True, metavar="USD")
    return found
