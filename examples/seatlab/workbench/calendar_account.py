from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from functools import partial
from typing import Any

from ..calendar_notices import protocol, route_result, team_views, turn_summary
from ..visibility import spend
from .account_contract import attempt, packet, seat
from .seat_stream import MIB, Malformed, Record, _read, _require, _strict_json

SOURCE = "calendar-repeatability"
PRODUCER_COMMIT = "232992b96710a212764f9e4c30068aeb8f00c755"
TEAMS_RELEASE = (
    "examples/seatlab/results/calendar-native-repeatability.json",
    "3650bd3235d8634272e3fc84560420788736f56a7d374c1a584a96f67ad555a3",
)
TURNS_RELEASE = (
    "examples/seatlab/results/calendar-native-repeatability-turns.json",
    "010874340417ad9f8b35b935c1d9334073af6ec38535babf040f98d4a0998fb4",
)
COST_RELEASE = (
    "examples/seatlab/results/calendar-native-repeatability-cost.json",
    "ea177d77037f92c6d17146fc31f90adc6d1558bfc74e586ff935decb3d62e123",
)
REGISTRATION = "https://github.com/PeterMakhnatch/harbor-multiagent/pull/75#issuecomment-7062119814"
SCOPE_DOMAIN = b"seatlab.team-account.calendar-repeatability.enrollment/v1\x00"
MAX_INPUT_BYTES = MIB
COST_SCHEMA = "seatlab.native-calendar-repeat-charges/v1"
LEDGER_SCHEMA = "seatlab.direct-flash-cost/v1"
COST_KEYS = frozenset({"schema", "registration", "ledger", "verification"})
LEDGER_KEYS = frozenset(
    {
        "schema",
        "currency",
        "calls",
        "unsettled_calls",
        "known_usd_low",
        "upper_usd_including_holds",
        "price_source",
    }
)
CALL_KEYS = frozenset(
    {
        "run",
        "role",
        "served_model",
        "cache_hit",
        "cache_miss",
        "output",
        "estimated_usd_low",
        "estimated_usd_high",
        "unsettled_hold_usd",
    }
)
VERIFICATION_KEYS = frozenset(
    {"cache_hit", "cache_miss", "output", "holds", "settled", "known_usd", "upper_usd"}
)
TOKENS = ("cache_hit", "cache_miss", "output")
SENDER_PHASES = ("initial", "revision")
PRODUCER_ERRORS = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    ArithmeticError,
)

type _Owners = dict[str, str]
type _Rows = dict[tuple[str, str], tuple[Record, route_result._Charge]]


def _producer(check: Callable[[], Any], reason: str) -> Any:
    try:
        return check()
    except Malformed:
        raise
    except PRODUCER_ERRORS:
        raise Malformed(reason) from None


def _load(path: str, release: tuple[str, str]) -> Any:
    data = _read(path, MAX_INPUT_BYTES, "calendar repeatability input")
    _require(
        hashlib.sha256(data).hexdigest() == release[1],
        "unpinned calendar repeatability input",
    )
    return _strict_json(data, "calendar repeatability input is not strict UTF-8 JSON")


def _owners(row: Record) -> _Owners:
    cell = team_views.RepeatJoinCell(
        world=row["world"], arm=row["arm"], repetition=row["repetition"]
    )
    return {
        protocol.role(cell, phase): cell.sender
        if phase in SENDER_PHASES
        else cell.receiver
        for phase in protocol.PHASES
    }


def _enrollment(receipt: Record) -> dict[str, tuple[Record, _Owners]]:
    teams: dict[str, tuple[Record, _Owners]] = {}
    for row in receipt["teams"]:
        _require(
            row["status"] == "complete",
            "pinned release contains a non-complete native team",
        )
        teams[row["run"]] = (row, _owners(row))
    return teams


def _totals(ledger: Record, verification: Any, rows: _Rows) -> None:
    tokens = {key: sum(call[key] for call, _ in rows.values()) for key in TOKENS}
    low = spend._usd(sum(charge.low for _, charge in rows.values()))
    high = spend._usd(sum(charge.high for _, charge in rows.values()))
    _require(
        type(ledger["unsettled_calls"]) is int
        and (
            ledger["unsettled_calls"],
            ledger["known_usd_low"],
            ledger["upper_usd_including_holds"],
        )
        == (0, low, high),
        "direct cost totals differ from call rows",
    )
    _require(
        isinstance(verification, dict)
        and verification.keys() == VERIFICATION_KEYS
        and all(type(verification[key]) is int for key in (*TOKENS, "holds", "settled"))
        and verification
        == {
            **tokens,
            "holds": 0,
            "settled": len(rows),
            "known_usd": low,
            "upper_usd": high,
        },
        "saved verification differs from direct cost calls",
    )


def _ledger(cost: Any, teams: dict[str, tuple[Record, _Owners]]) -> _Rows:
    _require(
        isinstance(cost, dict)
        and cost.keys() == COST_KEYS
        and cost["schema"] == COST_SCHEMA
        and cost["registration"] == REGISTRATION,
        "unsupported calendar repeatability charges",
    )
    ledger = cost["ledger"]
    _require(
        isinstance(ledger, dict)
        and ledger.keys() == LEDGER_KEYS
        and ledger["schema"] == LEDGER_SCHEMA
        and ledger["currency"] == "USD"
        and ledger["price_source"] == route_result._PRICE_SOURCE
        and isinstance(ledger["calls"], list),
        "unsupported direct cost ledger",
    )
    rows: _Rows = {}
    for call in ledger["calls"]:
        _require(
            isinstance(call, dict) and call.keys() == CALL_KEYS,
            "unsupported direct cost call fields",
        )
        run, role = call["run"], call["role"]
        owners = teams[run][1] if type(run) is str and run in teams else {}
        _require(
            type(role) is str and role in owners and (run, role) not in rows,
            "foreign or repeated direct cost call",
        )
        charge = _producer(
            partial(route_result._charge, call),
            "direct cost call rejected by producer pricing",
        )
        _require(charge is not None, "pinned release contains a held direct cost call")
        rows[run, role] = (call, charge)
    _require(
        len(rows) == len(protocol.PHASES) * len(teams),
        "direct cost ledger lacks a registered call",
    )
    _totals(ledger, cost["verification"], rows)
    return rows


def _seat(name: str, run: str, owners: _Owners, rows: _Rows) -> Record:
    found = [rows[run, role] for role, owner in owners.items() if owner == name]
    hit, miss, output = (sum(call[key] for call, _ in found) for key in TOKENS)
    return seat(
        name,
        served_models=tuple(sorted({call["served_model"] for call, _ in found})),
        input_tokens=hit + miss,
        cache_tokens=hit,
        output_tokens=output,
        priced_low_usd=spend._usd(sum(charge.low for _, charge in found)),
        priced_high_usd=spend._usd(sum(charge.high for _, charge in found)),
        calls=len(found),
        held_calls=0,
    )


def _scope(receipt: Record) -> str:
    cells = sorted(
        [row["world"], row["arm"], row["repetition"], row["run"], row["source_sha256"]]
        for row in receipt["teams"]
    )
    body = json.dumps(
        {"schema": receipt["schema"], "cells": cells},
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(SCOPE_DOMAIN + body.encode("ascii")).hexdigest()


def build(teams_path: str, turns_path: str, cost_path: str) -> Record:
    receipt = _load(teams_path, TEAMS_RELEASE)
    panel = _load(turns_path, TURNS_RELEASE)
    cost = _load(cost_path, COST_RELEASE)
    _producer(
        partial(turn_summary.summarize, receipt, panel),
        "native receipt or turn panel rejected by producer validation",
    )
    teams = _enrollment(receipt)
    rows = _ledger(cost, teams)
    attempts = [
        attempt(
            row["trial_uuid"],
            hashlib.sha256(run.encode("utf-8")).hexdigest(),
            "valid",
            row["reward"],
            [_seat(name, run, owners, rows) for name in protocol.SEATS],
        )
        for run, (row, owners) in teams.items()
    ]
    return packet(SOURCE, _scope(receipt), len(teams), None, attempts)
