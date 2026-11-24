from __future__ import annotations

import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..market_handoff import simulator, timing_messages, timing_panel
from . import domain, panel, replay

Record = dict[str, Any]
Pair = tuple[dict[str, int], int]
Slots = dict[panel.Slot, Record]
Memo = dict[int, Record]
Options = dict[int, list[tuple[int | None, int]]]
SCHEMA = "seatlab.report-budget-missing-price-bounds/v1"
SHORT, LONG = panel.SHORT, panel.LONG
TARGETS = domain.TARGETS
MENU = {1: "keep", 2: "raise", 3: "discount"}
PRICES = tuple(MENU)
CHECKS = tuple(c for c in replay.CHECKS if c != "stock_settlements") + (
    "committed_settlements_matched",
    "hypothetical_branches",
    "fresh_settlements",
    "branches_reused",
)
QUESTION = (
    "Could any admissible menu price for a missing finance decision reverse the "
    "positive observed paired short-minus-long continuation profit difference?"
)
CRITERION = (
    f"reversal is ruled out only if the minimum attainable {domain.WORLD_COUNT}-world "
    "mean short-minus-long continuation profit is > 0; the identified-pair mean "
    "cannot rule it out"
)
HYPOTHETICAL = (
    "stock continuations of admissible menu prices at each existing confirmed day-90 "
    "checkpoint; not model actions, not commits, not trials; an absent commit has no "
    "actual stock outcome"
)

if PRICES != tuple(sorted(timing_messages.MULTIPLIERS)):
    raise ValueError("price menu drifted from the registered multipliers")
if timing_messages.MULTIPLIERS[2] <= 1.0 or timing_messages.MULTIPLIERS[3] >= 1.0:
    raise ValueError("menu labels disagree with the registered multipliers")


def _usd(cents: Fraction | int) -> str:
    return timing_panel._usd(cents)


def _exact(cents: Fraction) -> str:
    return f"{cents.numerator}/{cents.denominator}"


def _check(stock: Any, row: Record, cache: dict[int, Record], counts: Record) -> Any:
    label = f"world {row['world']} target {row['target']}"
    counts["rows_checked"] += 1
    if row["status"] == "not_issued" and row["checkpoint_sha256"] is None:
        counts["unissued_skipped"] += 1
        return None
    company = replay._company(stock, row["world"], cache, counts)
    if "ended" in company:
        replay._ended(row, company, label, counts)
        return company
    if row["status"] == "predecision":
        raise ValueError(f"{label} is predecision but its company reaches day 90")
    binding = company["binding"]
    if row["checkpoint_sha256"] is not None:
        if row["checkpoint_sha256"] != binding["checkpoint_sha256"]:
            raise ValueError(f"{label} checkpoint differs from the fresh company")
        counts["checkpoints_matched"] += 1
    replay._sales(row, binding, label, counts)
    replay._finance(row, binding, label, counts)
    return company


def _gap(row: Record, company: Record | None) -> str | None:
    if row["status"] == "predecision" or (company is not None and "ended" in company):
        return "predecision: the company ended before the day-90 decision"
    if row["status"] == "not_issued":
        return "unissued: no finance decision was ever requested"
    if row["checkpoint_sha256"] is None or company is None:
        return "no confirmed day-90 checkpoint"
    return None


def _fresh(
    stock: Any, company: Record, pick: int, memo: dict[int, Record], counts: Record
) -> Record:
    if pick in memo:
        counts["branches_reused"] += 1
        return memo[pick]
    seal = company["binding"]["checkpoint_sha256"]
    fresh = domain.settle(stock, company["checkpoint"], pick)
    if (
        fresh.keys() != domain.SETTLE_KEYS
        or fresh["pick"] != pick
        or fresh["checkpoint_sha256"] != seal
    ):
        world = company["binding"]["world"]
        raise ValueError(f"world {world} pick {pick} settles off its checkpoint")
    counts["fresh_settlements"] += 1
    memo[pick] = fresh
    return fresh


def _committed(
    stock: Any, company: Record, row: Record, slot: Record, memo: Memo, counts: Record
) -> list[tuple[int | None, int]]:
    label = slot["label"]
    fresh = _fresh(stock, company, slot["pick"], memo, counts)
    if row["settlement"] != fresh:
        raise ValueError(
            f"{label} released settlement is not exactly the fresh stock settlement"
        )
    cents = slot["settlement"]["continuation"]
    if timing_panel._cents(fresh["continuation_profit_usd"], label) != cents:
        raise ValueError(f"{label} parsed continuation differs from the fresh stock")
    counts["committed_settlements_matched"] += 1
    return [(None, cents)]


def _missing(
    stock: Any, company: Record, label: str, memo: Memo, counts: Record
) -> list[tuple[int | None, int]]:
    out: list[tuple[int | None, int]] = []
    for pick in PRICES:
        fresh = _fresh(stock, company, pick, memo, counts)
        cents = timing_panel._cents(
            fresh["continuation_profit_usd"], f"{label} hypothetical pick {pick}"
        )
        out.append((pick, cents))
        counts["hypothetical_branches"] += 1
    if len(out) != len(PRICES):
        raise ValueError(f"{label} did not enumerate every admissible price")
    return out


def _world(
    stock: Any, world: int, raw: Slots, slots: Slots, cache: Memo, counts: Record
) -> Record:
    memo: dict[int, Record] = {}
    options: dict[int, list[tuple[int | None, int]]] = {}
    gaps: list[Record] = []
    for target in TARGETS:
        row, slot = raw[(world, target)], slots[(world, target)]
        company = _check(stock, row, cache, counts)
        if slot["pick"] is not None:
            options[target] = _committed(stock, company, row, slot, memo, counts)
            continue
        reason = _gap(row, company)
        if reason is not None:
            gaps.append({"world": world, "target": target, "reason": reason})
            continue
        options[target] = _missing(stock, company, slot["label"], memo, counts)
    seals = {slots[(world, t)]["checkpoint"] for t in TARGETS} - {None}
    seals |= {fresh["checkpoint_sha256"] for fresh in memo.values()}
    if len(seals) > 1 or (not gaps and len(seals) != 1):
        raise ValueError(f"world {world} arms and branches do not share one checkpoint")
    return {"gaps": gaps, "options": options}


def _pairs(options: dict[int, list[tuple[int | None, int]]]) -> list[Pair]:
    out: list[Pair] = []
    for short_pick, short_cents in options[SHORT]:
        for long_pick, long_cents in options[LONG]:
            prices = {
                str(target): pick
                for target, pick in ((SHORT, short_pick), (LONG, long_pick))
                if pick is not None
            }
            out.append((prices, short_cents - long_cents))
    if not 1 <= len(out) <= len(PRICES) ** len(TARGETS):
        raise ValueError("a world has no admissible completion or too many")
    return out


def _extreme(pairs: list[Pair], value: int) -> Record:
    return {
        "short_minus_long_usd": _usd(value),
        "mean_contribution_usd": _usd(Fraction(value, domain.WORLD_COUNT)),
        "prices": [prices for prices, diff in pairs if diff == value],
    }


def _affected(world: int, options: Options, slots: Slots, pairs: list[Pair]) -> Record:
    missing = [t for t in TARGETS if slots[(world, t)]["pick"] is None]
    partner = [slots[(world, t)] for t in TARGETS if t not in missing]
    low, high = min(d for _, d in pairs), max(d for _, d in pairs)
    return {
        "world": world,
        "checkpoint_sha256": slots[(world, missing[0])]["checkpoint"],
        "missing_targets": missing,
        "committed_partner": None
        if not partner
        else {
            "target": partner[0]["target"],
            "pick": partner[0]["pick"],
            "price": MENU[partner[0]["pick"]],
            "continuation_usd": _usd(partner[0]["settlement"]["continuation"]),
        },
        "hypothetical_continuation_usd": {
            str(t): {str(p): _usd(c) for p, c in options[t]} for t in missing
        },
        "admissible": [
            {
                "prices": prices,
                "short_minus_long_usd": _usd(diff),
                "mean_contribution_usd": _usd(Fraction(diff, domain.WORLD_COUNT)),
            }
            for prices, diff in pairs
        ],
        "min": _extreme(pairs, low),
        "max": _extreme(pairs, high),
        "spread_usd": _usd(high - low),
    }


def _identified(slots: Slots, summary: Record) -> Record:
    deltas = [
        slots[(w, SHORT)]["settlement"]["continuation"]
        - slots[(w, LONG)]["settlement"]["continuation"]
        for w in range(domain.WORLD_COUNT)
        if all(slots[(w, t)]["pick"] is not None for t in TARGETS)
    ]
    total = sum(deltas)
    mean = Fraction(total, len(deltas)) if deltas else None
    shown = None if mean is None else _usd(mean)
    secondary = summary["secondary"]
    if (
        len(deltas) != secondary["settled_pairs"]
        or shown != secondary["mean_short_minus_long_usd"]
    ):
        raise ValueError("identified pairs disagree with the released panel summary")
    return {
        "pairs": len(deltas),
        "sum_short_minus_long_usd": _usd(total),
        "mean_short_minus_long_usd": shown,
        "sign": "none" if mean is None else timing_panel._sign(mean),
    }


def _aggregate(pairs: dict[int, list[Pair]], affected: list[int], sign: str) -> Record:
    n = domain.WORLD_COUNT
    if len(pairs) != n:
        raise ValueError("a completed mean must keep every world in the denominator")
    sides: dict[str, tuple[int, Record]] = {}
    for side, choose in (("worst", min), ("best", max)):
        values = {w: choose(d for _, d in pairs[w]) for w in range(n)}
        total = sum(values.values())
        mean = Fraction(total, n)
        sides[side] = (
            total,
            {
                "sum_short_minus_long_usd": _usd(total),
                "mean_short_minus_long_usd": _usd(mean),
                "mean_exact_cents": _exact(mean),
                "prices": {
                    str(w): [p for p, d in pairs[w] if d == values[w]] for w in affected
                },
            },
        )
    low, worst = sides["worst"]
    high, best = sides["best"]
    if low > high:
        raise ValueError("worst completed mean exceeds best completed mean")
    ruled_out = low > 0
    if sign != "positive":
        answer = "premise fails: the identified-pair mean is not positive"
    elif ruled_out:
        answer = f"no: every admissible completion keeps the {n}-world mean > 0"
    else:
        answer = f"yes: an admissible completion brings the {n}-world mean to <= 0"
    return {
        "available": True,
        "denominator": domain.WORLD_COUNT,
        "worlds_excluded": 0,
        "affected_worlds": affected,
        "completions": _count(pairs),
        "worst": worst,
        "best": best,
        "mean_range_width_usd": _usd(Fraction(high - low, domain.WORLD_COUNT)),
        "reversal_ruled_out": ruled_out,
        "answer": answer,
    }


def _count(pairs: dict[int, list[Pair]]) -> int:
    total = 1
    for options in pairs.values():
        total *= len(options)
    return total


def _unavailable(gaps: list[Record]) -> Record:
    return {
        "available": False,
        "denominator": domain.WORLD_COUNT,
        "worlds_excluded": 0,
        "unavailable": gaps,
        "worst": None,
        "best": None,
        "reversal_ruled_out": False,
        "answer": "unavailable: a missing decision has no confirmed day-90 checkpoint; "
        "reversal cannot be ruled out",
    }


def _original(summary: Record) -> Record:
    secondary = summary["secondary"]
    recommend = summary["recommend_short_for_this_cohort"]
    return {
        "primary_verdict": summary["primary"]["verdict"],
        "primary_pairs": summary["primary"]["pairs"],
        "settled_pairs": secondary["settled_pairs"],
        "mean_short_minus_long_usd": secondary["mean_short_minus_long_usd"],
        "economic_guard": secondary["economic_guard"],
        "recommend_short_for_this_cohort": recommend,
        "adoption": "PASS" if recommend else "FAIL",
        "decision": "unchanged; these bounds are sensitivity analysis and make no "
        "new recommendation",
    }


def bounds(stock: Any, rows: list[Record]) -> Record:
    slots = panel._parse(rows)
    summary = panel.summarize_slots(slots)
    raw = {(row["world"], row["target"]): row for row in rows}
    counts: Record = dict.fromkeys(CHECKS, 0)
    cache: dict[int, Record] = {}
    gaps: list[Record] = []
    pairs: dict[int, list[Pair]] = {}
    affected: list[Record] = []
    for world in range(domain.WORLD_COUNT):
        result = _world(stock, world, raw, slots, cache, counts)
        gaps += result["gaps"]
        if result["gaps"]:
            continue
        pairs[world] = _pairs(result["options"])
        if any(slots[(world, t)]["pick"] is None for t in TARGETS):
            affected.append(_affected(world, result["options"], slots, pairs[world]))
    if counts["rows_checked"] != len(panel.SLOTS):
        raise ValueError("not every registered slot was checked")
    identified = _identified(slots, summary)
    worlds = [entry["world"] for entry in affected]
    return {
        "schema": SCHEMA,
        "question": QUESTION,
        "criterion": CRITERION,
        "menu": {str(p): MENU[p] for p in PRICES},
        "hypothetical": HYPOTHETICAL,
        "original_result": _original(summary),
        "identified": identified,
        "affected": affected,
        "hypothetical_complete": _unavailable(gaps)
        if gaps
        else _aggregate(pairs, worlds, identified["sign"]),
        "checks": counts,
        "ledger": {
            "actual_commits_added": 0,
            "model_calls": 0,
            "docker_runs": 0,
            "network_calls": 0,
            "files_written": 0,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bound the completed 24-world short-minus-long mean over admissible "
        "prices for missing finance decisions, without model calls."
    )
    parser.add_argument("rows", type=Path)
    parser.add_argument("--stock", type=Path, required=True)
    args = parser.parse_args(argv)
    data = simulator.read_own(args.rows)
    rows = json.loads(
        data,
        object_pairs_hook=timing_panel._strict,
        parse_constant=timing_panel._constant,
    )
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    if stock.numpy_version != replay.NUMPY_VERSION:
        raise ValueError(f"the registered runtime uses NumPy {replay.NUMPY_VERSION}")
    report = bounds(stock, rows)
    report["inputs"] = {
        "rows_sha256": hashlib.sha256(data).hexdigest(),
        "stock_commit": simulator.PIN,
        "numpy": stock.numpy_version,
        "namespace": domain.NAMESPACE,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["hypothetical_complete"]["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
