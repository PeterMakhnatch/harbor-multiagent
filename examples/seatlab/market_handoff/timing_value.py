from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import simulator, timing_panel, timing_world
from .timing_messages import ACTIONS

Record = dict[str, Any]
SCHEMA = "seatlab.price-timing-keep-diagnostic/v1"
KEEP = 1
NUMPY_VERSION = "2.1.3"
SCOPE = (
    "secondary descriptive diagnostic specified after trials started; "
    "cannot change the registered verdict; hindsight best is room to improve, "
    "not an available agent or what sales knew"
)
METRIC = (
    "continuation_profit_usd: stock monthly operating profit after the first "
    "3 complete 30-day months, day 90 to terminal day; exact cents"
)


def _company(stock: Any, world: int) -> Record:
    try:
        checkpoint, binding = timing_world.make_company(stock, world)
    except timing_world.CompanyEndedBeforeDecision as ended:
        return {
            "world": world,
            "status": "ended_before_decision",
            "ended": ended.record,
        }
    settled = {
        pick: timing_world.settle(stock, checkpoint, pick)
        for pick in timing_world.PRICE_MULTIPLIERS
    }
    return {
        "world": world,
        "status": "settled",
        "checkpoint": binding["checkpoint_sha256"],
        "settled": settled,
        "cents": {
            pick: timing_panel._cents(item["continuation_profit_usd"], f"world {world}")
            for pick, item in settled.items()
        },
    }


def _company_row(company: Record) -> Record:
    world = company["world"]
    shape = timing_world.SHAPES[world]
    if company["status"] != "settled":
        return {"world": world, "shape": shape, **company, "prices": None}
    cents = company["cents"]
    best = max(cents.values())
    keep = cents[KEEP]
    return {
        "world": world,
        "shape": shape,
        "status": "settled",
        "checkpoint_sha256": company["checkpoint"],
        "prices": {
            ACTIONS[pick]: {
                key: item[key]
                for key in (
                    "pick",
                    "continuation_profit_usd",
                    "profit_usd",
                    "net_worth_usd",
                    "stock_score",
                    "bankrupt",
                    "end_day",
                )
            }
            | {"minus_keep_usd": timing_panel._usd(cents[pick] - keep)}
            for pick, item in company["settled"].items()
        },
        "keep_usd": timing_panel._usd(keep),
        "best_picks": [ACTIONS[p] for p, c in cents.items() if c == best],
        "best_usd": timing_panel._usd(best),
        "best_minus_keep_usd": timing_panel._usd(best - keep),
        "keep_is_best_or_tied": keep == best,
    }


def _verify(row: Record, company: Record, label: str) -> dict[str, int]:
    if company["status"] != "settled":
        raise ValueError(f"{label} is complete but its company ended before day 90")
    if row["checkpoint_sha256"] != company["checkpoint"]:
        raise ValueError(f"{label} checkpoint differs from the fresh company")
    early = row["early"]
    late = next(seat for seat in timing_panel.FINANCE if seat != early)
    picks = {}
    for arm, seat in zip(timing_panel.ARMS, (early, late)):
        pick, released = row["picks"][seat], row["settlements"][seat]
        fresh = company["settled"][pick]
        if released["pick"] != pick or not released.keys() <= fresh.keys():
            raise ValueError(f"{label} {seat} settlement is not a stock settlement")
        if any(released[key] != fresh[key] for key in released):
            raise ValueError(f"{label} {seat} released settlement differs from fresh")
        picks[arm] = pick
    picks["sales"] = row["report"]["pick"]
    return picks


def _pair_row(row: Record, company: Record) -> Record:
    label = f"world {row['world']} repeat {row['repeat']}"
    picks = _verify(row, company, label)
    cents = company["cents"]
    best, keep = max(cents.values()), cents[KEEP]
    return {
        "world": row["world"],
        "repeat": row["repeat"],
        "picks": {who: ACTIONS[p] for who, p in picks.items()},
        "minus_keep_cents": {who: cents[p] - keep for who, p in picks.items()},
        "regret_cents": {who: best - cents[p] for who, p in picks.items()},
    }


def _vs_keep(pairs: list[Record], who: str) -> Record:
    deltas = [p["minus_keep_cents"][who] for p in pairs]
    regrets = [p["regret_cents"][who] for p in pairs]
    n = len(pairs)
    return {
        "pairs": n,
        "beats_keep": sum(d > 0 for d in deltas),
        "loses_to_keep": sum(d < 0 for d in deltas),
        "ties_keep": sum(d == 0 for d in deltas),
        "same_pick_as_keep": sum(p["picks"][who] == ACTIONS[KEEP] for p in pairs),
        "total_minus_keep_usd": timing_panel._usd(sum(deltas)),
        "mean_minus_keep_usd": None
        if not n
        else timing_panel._usd(Fraction(sum(deltas), n)),
        "mean_regret_usd": None
        if not n
        else timing_panel._usd(Fraction(sum(regrets), n)),
        "zero_regret": sum(r == 0 for r in regrets),
    }


def _public_pair(pair: Record) -> Record:
    return {
        "world": pair["world"],
        "repeat": pair["repeat"],
        "picks": pair["picks"],
        "minus_keep_usd": {
            who: timing_panel._usd(c) for who, c in pair["minus_keep_cents"].items()
        },
        "regret_usd": {
            who: timing_panel._usd(c) for who, c in pair["regret_cents"].items()
        },
    }


def diagnose(stock: Any, rows: list[Record]) -> Record:
    registered = timing_panel.summarize(rows)
    companies = [_company(stock, w) for w in range(timing_world.ANALYSIS_WORLDS)]
    complete = sorted(
        (r for r in rows if r["status"] == "complete"),
        key=lambda r: (r["world"], r["repeat"]),
    )
    pairs = [_pair_row(row, companies[row["world"]]) for row in complete]
    settled = [c for c in companies if c["status"] == "settled"]
    gaps = [max(c["cents"].values()) - c["cents"][KEEP] for c in settled]
    return {
        "schema": SCHEMA,
        "scope": SCOPE,
        "metric": METRIC,
        "source": simulator.PIN,
        "numpy_version": stock.numpy_version,
        "new_model_calls": 0,
        "registered_complete_pairs": registered["complete"],
        "fresh_settlements": 3 * len(settled),
        "verified_released_settlements": 2 * len(pairs),
        "summary": {
            "companies_settled": len(settled),
            "companies_ended_before_decision": [
                c["world"] for c in companies if c["status"] != "settled"
            ],
            "keep_best_or_tied": sum(g == 0 for g in gaps),
            "best_minus_keep_total_usd": timing_panel._usd(sum(gaps)),
            "best_minus_keep_mean_usd": None
            if not gaps
            else timing_panel._usd(Fraction(sum(gaps), len(gaps))),
            "complete_pairs": len(pairs),
            "vs_keep": {
                who: _vs_keep(pairs, who) for who in ("sales", "early", "late")
            },
        },
        "companies": [_company_row(c) for c in companies],
        "pairs": [_public_pair(p) for p in pairs],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare released price picks with keeping prices in fresh stock runs."
    )
    parser.add_argument("rows", type=Path)
    parser.add_argument("--stock", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = json.loads(simulator.read_own(args.rows))
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    if stock.numpy_version != NUMPY_VERSION:
        raise ValueError(f"the registered runtime uses NumPy {NUMPY_VERSION}")
    print(json.dumps(diagnose(stock, rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
