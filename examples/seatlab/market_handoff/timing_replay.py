from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from . import simulator, timing_evidence, timing_panel, timing_world
from .timing_study import ROLES


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def match(turn: dict[str, Any], prompt: str) -> str:
    reply = turn["reply"]
    if sha(prompt) != turn["prompt_sha256"] or sha(reply) != turn["answer_sha256"]:
        raise ValueError("the public prompt or answer does not match its receipt")
    return reply


def reproduce(stock: Any, row: dict[str, Any]) -> int:
    checkpoint, binding = timing_world.make_company(stock, row["world"])
    if binding["checkpoint_sha256"] != row["checkpoint_sha256"]:
        raise ValueError("the stock checkpoint differs from the decision checkpoint")
    turns = {(t["seat"], t["purpose"]): t for t in row["turns"]}
    report, sales = timing_evidence.sales_plan(binding, turns["sales", "sales"])
    match(sales["step"]["turn"], sales["step"]["prompt"])
    if report != row["report"]:
        raise ValueError("the public report differs from what sales returned")
    digests = {"sales": sales["digests"]}
    for role in ROLES:
        plan = timing_evidence.finance_plan(
            binding,
            row["early"],
            report,
            role,
            [turns[role, "finance-price"], turns[role, "finance-receipt"]],
        )
        for step in plan["steps"]:
            match(step["turn"], step["prompt"])
        digests[role] = plan["digests"]
        pick = plan["pick"]
        if pick != row["picks"][role] or plan["token"] != row["ack_tokens"][role]:
            raise ValueError(
                "the public decision or acknowledgment differs from its bound reply"
            )
        actual = timing_world.settle(stock, checkpoint, pick)
        if actual != row["settlements"][role]:
            raise ValueError(
                f"stock settlement differs for world {row['world']}, repeat {row['repeat']}, seat {role}"
            )
    timing_evidence.check_events(row["events"], digests)
    return len(turns)


def partial_settlements(stock: Any, row: dict[str, Any]) -> int:
    settlements = row.get("settlements") or {}
    if not settlements:
        return 0
    checkpoint, binding = timing_world.make_company(stock, row["world"])
    if row["checkpoint_sha256"] != binding["checkpoint_sha256"]:
        raise ValueError("unexpected partial company checkpoint")
    for role, settled in settlements.items():
        if settled != timing_world.settle(stock, checkpoint, row["picks"][role]):
            raise ValueError(f"partial branch does not reproduce for seat {role}")
    return len(settlements)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the live companies and check public decisions without calling a model."
    )
    parser.add_argument("rows", type=Path)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument(
        "--world",
        type=int,
        choices=range(8),
        help="verify one company's complete repetitions",
    )
    args = parser.parse_args()
    rows = json.loads(simulator.read_own(args.rows))
    timing_panel.summarize(rows)
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    if stock.numpy_version != "2.1.3":
        raise ValueError("the registered runtime uses NumPy 2.1.3")
    eligible = [r for r in rows if args.world is None or r["world"] == args.world]
    complete = [r for r in eligible if r["status"] == "complete"]
    turns = sum(reproduce(stock, row) for row in complete)
    partial = [r for r in eligible if r["status"] == "incomplete"]
    observed = sum(partial_settlements(stock, row) for row in partial)
    print(
        json.dumps(
            {
                "verified_checkpoints": len({r["world"] for r in complete}),
                "verified_pairs": len(complete),
                "rebuilt_turns": turns,
                "stock_settlements": 2 * len(complete),
                "incomplete_pairs": len(partial),
                "additional_partial_stock_settlements": observed,
                "new_model_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
