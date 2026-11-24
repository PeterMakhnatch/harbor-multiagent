from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace
from typing import Any

from .observations import SalesWindow
from .simulator import (
    NATIVE_DAYS,
    PIN,
    PREFIX_DAYS,
    RESEARCH_SPENT,
    SimulatorError,
    advance_prefix,
    continuation_policy,
    continue_with,
    fingerprint,
    start_market,
)

NAMESPACE = "seatlab/price-timing/v1"
SETTLE_SCHEMA = "seatlab.price-timing-settle/v1"
ANALYSIS_WORLDS = 8
CONTROL_WORLD = 8
SHAPES = (
    "mnl",
    "nested",
    "wom",
    "delayed_ads",
    "deal_seekers",
    "mnl",
    "nested",
    "wom",
    "mnl",
)
PRICE_MULTIPLIERS = {1: 1.0, 2: 1.15, 3: 0.85}
PREFIX_MONTHS = PREFIX_DAYS // 30
USD_DIGITS = 2
SCORE_DIGITS = 6
MAX_ABS_USD = 1e12
BINDING_KEYS = frozenset(
    {
        "world",
        "stock_commit",
        "world_seed",
        "truth_seed",
        "shape",
        "checkpoint_sha256",
        "sales",
        "finance",
    }
)
SETTLE_KEYS = frozenset(
    {
        "schema",
        "source",
        "pick",
        "price_multiplier",
        "checkpoint_sha256",
        "profit_usd",
        "continuation_profit_usd",
        "net_worth_usd",
        "stock_score",
        "bankrupt",
        "bankrupt_day",
        "end_day",
    }
)

if len(SHAPES) != CONTROL_WORLD + 1 or PREFIX_MONTHS * 30 != PREFIX_DAYS:
    raise SimulatorError("price-timing world table is inconsistent")


class CompanyEndedBeforeDecision(SimulatorError):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__(
            f"world {record['world']}: company ended on day {record['end_day']}, "
            f"before the day-{PREFIX_DAYS} decision; no live decision allowed"
        )
        self.record = record


def _require_world(world: Any) -> int:
    if type(world) is not int or not 0 <= world <= CONTROL_WORLD:
        raise SimulatorError(
            f"world must be an int in 0..{CONTROL_WORLD}, got {world!r}"
        )
    return world


def world_seed(world: int) -> str:
    return hashlib.sha256(
        f"{NAMESPACE}:{_require_world(world)}:world".encode()
    ).hexdigest()


def truth_seed(world: int) -> str:
    return hashlib.sha256(
        f"{NAMESPACE}:{_require_world(world)}:truth".encode()
    ).hexdigest()


def make_company(stock: SimpleNamespace, world: int) -> tuple[Any, dict[str, Any]]:
    shape = SHAPES[_require_world(world)]
    seeds = (world_seed(world), truth_seed(world))
    if seeds[0] == seeds[1]:
        raise SimulatorError(f"world {world}: world and truth seeds collide")
    window = SalesWindow()
    checkpoint = start_market(stock, seeds[0], seeds[1], shape)
    advance_prefix(checkpoint, window)
    views = window.finish()
    if views.keys() != {"sales", "finance"} or not all(
        isinstance(view, dict) for view in views.values()
    ):
        raise SimulatorError(f"world {world}: role views are malformed")
    if checkpoint.w["shape"] != shape or checkpoint.w["seed"] != seeds[0]:
        raise SimulatorError(f"world {world}: stock world does not match its seed")
    if checkpoint.ended or checkpoint.next_day != PREFIX_DAYS:
        ended = checkpoint.result()
        raise CompanyEndedBeforeDecision(
            {
                "world": world,
                "stock_commit": PIN,
                "shape": shape,
                "checkpoint_sha256": fingerprint(checkpoint),
                "ended_before_decision": True,
                "bankrupt": bool(ended["bankrupt"]),
                "bankrupt_day": ended["bankrupt_day"],
                "end_day": ended["days"],
            }
        )
    binding = {
        "world": world,
        "stock_commit": PIN,
        "world_seed": seeds[0],
        "truth_seed": seeds[1],
        "shape": shape,
        "checkpoint_sha256": fingerprint(checkpoint),
        "sales": views["sales"],
        "finance": views["finance"],
    }
    if binding.keys() != BINDING_KEYS:
        raise SimulatorError("binding keys drifted")
    return checkpoint, binding


def _usd(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SimulatorError(f"{label}: stock value {value!r} is not a number")
    if not math.isfinite(value) or abs(value) > MAX_ABS_USD:
        raise SimulatorError(f"{label}: stock value {value!r} is out of bounds")
    return round(float(value), USD_DIGITS)


def _profit(rows: Any, label: str) -> float:
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SimulatorError(f"{label}: stock rows are malformed")
    return _usd(math.fsum(_usd(row.get("profit"), label) for row in rows), label)


def _metrics(stock: SimpleNamespace, result: dict[str, Any]) -> dict[str, Any]:
    bankrupt, bankrupt_day, days = (
        result.get("bankrupt"),
        result.get("bankrupt_day"),
        result.get("days"),
    )
    if not isinstance(bankrupt, bool) or (bankrupt_day is None) == bankrupt:
        raise SimulatorError("stock bankruptcy fields disagree")
    if type(days) is not int or not 0 < days <= NATIVE_DAYS:
        raise SimulatorError(f"stock day count {days!r} is out of range")
    if bankrupt and (type(bankrupt_day) is not int or days != bankrupt_day + 1):
        raise SimulatorError("stock bankruptcy day disagrees with its day count")
    if not bankrupt and days != NATIVE_DAYS:
        raise SimulatorError(f"solvent company stopped on day {days}")
    net_worth = _usd(result.get("value"), "value")
    if bankrupt and net_worth != 0.0:
        raise SimulatorError("bankrupt company reports a nonzero value")
    score = float(stock.scoring.world_score(result, RESEARCH_SPENT))
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise SimulatorError(f"stock world score {score!r} is out of bounds")
    if bankrupt and score != 0.0:
        raise SimulatorError("bankrupt company reports a nonzero score")
    months = result.get("months")
    if not isinstance(months, list):
        raise SimulatorError("stock months are malformed")
    return {
        "profit_usd": _profit(result.get("years"), "years"),
        "continuation_profit_usd": _profit(months[PREFIX_MONTHS:], "months"),
        "net_worth_usd": net_worth,
        "stock_score": round(score, SCORE_DIGITS),
        "bankrupt": bankrupt,
        "bankrupt_day": bankrupt_day,
        "end_day": days,
    }


def settle(stock: SimpleNamespace, checkpoint: Any, pick: int) -> dict[str, Any]:
    if type(pick) is not int or pick not in PRICE_MULTIPLIERS:
        raise SimulatorError(
            f"pick must be one of {sorted(PRICE_MULTIPLIERS)}, got {pick!r}"
        )
    before = fingerprint(checkpoint)
    if checkpoint.ended or checkpoint.next_day != PREFIX_DAYS:
        raise SimulatorError(
            f"checkpoint ended={checkpoint.ended} on day {checkpoint.next_day}; "
            f"settle needs a live day-{PREFIX_DAYS} company"
        )
    result = continue_with(
        checkpoint, continuation_policy(stock, PRICE_MULTIPLIERS[pick])
    )
    if fingerprint(checkpoint) != before:
        raise SimulatorError("settling a branch mutated the shared day-90 checkpoint")
    record = {
        "schema": SETTLE_SCHEMA,
        "source": PIN,
        "pick": pick,
        "price_multiplier": PRICE_MULTIPLIERS[pick],
        "checkpoint_sha256": before,
        **_metrics(stock, result),
    }
    if record.keys() != SETTLE_KEYS:
        raise SimulatorError("settle keys drifted")
    return record
