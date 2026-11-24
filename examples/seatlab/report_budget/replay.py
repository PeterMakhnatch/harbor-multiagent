from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from ..market_handoff import simulator, timing_messages, timing_panel
from . import domain, panel

Record = dict[str, Any]
NUMPY_VERSION = "2.1.3"
CHECKS = (
    "rows_checked",
    "unissued_skipped",
    "companies_rebuilt",
    "predecision_confirmed",
    "checkpoints_matched",
    "sales_prompt_contracts",
    "sales_prompts_matched",
    "sales_replies_parsed",
    "finance_prompts_matched",
    "finance_prompts_null_report",
    "finance_replies_parsed",
    "stock_settlements",
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _prompt_contract(view: Record, world: int) -> None:
    long = domain.sales_prompt(view, panel.LONG)
    short = domain.sales_prompt(view, panel.SHORT)
    if long != timing_messages.sales_prompt(view):
        raise ValueError(
            f"world {world} target {panel.LONG} sales prompt is not the baseline"
        )
    old, new = str(panel.LONG), str(panel.SHORT)
    sites = [
        i
        for i in range(len(long))
        if long.startswith(old, i) and long[:i] + new + long[i + len(old) :] == short
    ]
    if len(sites) != 1:
        raise ValueError(
            f"world {world} short prompt is not one {old}->{new} substitution"
        )


def _company(
    stock: Any, world: int, cache: dict[int, Record], counts: Record
) -> Record:
    if world in cache:
        return cache[world]
    try:
        checkpoint, binding = domain.make_company(stock, world)
    except domain.CompanyEndedBeforeDecision as ended:
        cache[world] = {"ended": ended.record}
        return cache[world]
    _prompt_contract(binding["sales"], world)
    counts["companies_rebuilt"] += 1
    counts["sales_prompt_contracts"] += 1
    cache[world] = {"checkpoint": checkpoint, "binding": binding}
    return cache[world]


def _sales(row: Record, binding: Record, label: str, counts: Record) -> None:
    turn = row["sales"]
    if turn is None:
        return
    if turn["prompt_sha256"] is not None:
        prompt = domain.sales_prompt(binding["sales"], row["target"])
        if _sha(prompt) != turn["prompt_sha256"]:
            raise ValueError(f"{label} sales prompt digest does not rebuild")
        counts["sales_prompts_matched"] += 1
    reply = turn["reply"]
    if reply is None:
        return
    try:
        report = domain.read_sales(reply)
    except ValueError:
        report = None
    if row["sales_status"] in ("accepted", "invalid") and report != row["report"]:
        raise ValueError(f"{label} sales reply does not reparse to the world outcome")
    counts["sales_replies_parsed"] += 1


def _finance(row: Record, binding: Record, label: str, counts: Record) -> None:
    turn = row["finance"]
    if turn is None:
        return
    if turn["prompt_sha256"] is not None:
        prompt = domain.finance_prompt(binding["finance"], row["report"])
        if _sha(prompt) != turn["prompt_sha256"]:
            raise ValueError(
                f"{label} finance prompt is not its view with the accepted report or null"
            )
        counts["finance_prompts_matched"] += 1
        counts["finance_prompts_null_report"] += row["report"] is None
    reply = turn["reply"]
    if reply is None:
        return
    try:
        price = domain.read_price(reply)
    except ValueError:
        price = None
    if row["finance_pick"] is not None and price != row["finance_pick"]:
        raise ValueError(
            f"{label} finance reply does not reparse to the committed price"
        )
    counts["finance_replies_parsed"] += 1


def _settle(
    stock: Any, company: Record, row: Record, label: str, counts: Record
) -> None:
    pick = row["finance_pick"]
    if pick is None:
        return
    fresh = domain.settle(stock, company["checkpoint"], pick)
    released = row["settlement"]
    if fresh.keys() != domain.SETTLE_KEYS or released is None or released != fresh:
        raise ValueError(
            f"{label} released settlement is not exactly the fresh stock settlement"
        )
    counts["stock_settlements"] += 1


def _ended(row: Record, company: Record, label: str, counts: Record) -> None:
    if row["status"] not in panel.UNISSUED:
        raise ValueError(
            f"{label} is {row['status']} but its company ended before day 90"
        )
    known = row["checkpoint_sha256"]
    if known is not None and known != company["ended"]["checkpoint_sha256"]:
        raise ValueError(
            f"{label} predecision checkpoint differs from the fresh company"
        )
    counts["predecision_confirmed"] += row["status"] == "predecision"


def replay(stock: Any, row: Record, cache: dict[int, Record], counts: Record) -> None:
    label = f"world {row['world']} target {row['target']}"
    counts["rows_checked"] += 1
    if row["status"] == "not_issued" and row["checkpoint_sha256"] is None:
        counts["unissued_skipped"] += 1
        return
    company = _company(stock, row["world"], cache, counts)
    if "ended" in company:
        _ended(row, company, label, counts)
        return
    if row["status"] == "predecision":
        raise ValueError(f"{label} is predecision but its company reaches day 90")
    binding = company["binding"]
    if row["checkpoint_sha256"] is not None:
        if row["checkpoint_sha256"] != binding["checkpoint_sha256"]:
            raise ValueError(f"{label} checkpoint differs from the fresh company")
        counts["checkpoints_matched"] += 1
    _sales(row, binding, label, counts)
    _finance(row, binding, label, counts)
    _settle(stock, company, row, label, counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild report-budget companies and check public rows without model calls."
    )
    parser.add_argument("rows", type=Path)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--world", type=int, choices=range(domain.WORLD_COUNT))
    args = parser.parse_args(argv)
    rows = json.loads(
        simulator.read_own(args.rows),
        object_pairs_hook=timing_panel._strict,
        parse_constant=timing_panel._constant,
    )
    panel.summarize(rows)
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    if stock.numpy_version != NUMPY_VERSION:
        raise ValueError(f"the registered runtime uses NumPy {NUMPY_VERSION}")
    counts: Record = dict.fromkeys(CHECKS, 0)
    cache: dict[int, Record] = {}
    for row in sorted(rows, key=lambda r: (r["world"], r["target"])):
        if args.world is None or row["world"] == args.world:
            replay(stock, row, cache, counts)
    print(
        json.dumps(
            counts | {"new_model_calls": 0, "docker_runs": 0, "world": args.world},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
