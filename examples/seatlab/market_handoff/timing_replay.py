from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from . import simulator, timing_messages as messages, timing_panel, timing_world
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
    report = messages.read_sales(
        match(turns["sales", "sales"], messages.sales_prompt(binding["sales"]))
    )
    if report != row["report"]:
        raise ValueError("the public report differs from what sales returned")
    for role in ROLES:
        early = role == row["early"]
        price_prompt = messages.price_prompt(
            binding["finance"], report if early else None
        )
        reply = match(turns[role, "finance-price"], price_prompt)
        pick = messages.read_price(reply)
        if pick != row["picks"][role]:
            raise ValueError(
                "the reported finance decision differs from its owner's reply"
            )
        ack = turns[role, "finance-receipt"]
        token = json.loads(ack["reply"])["ack"]
        prompt = messages.receipt_prompt(
            price_prompt, reply, None if early else report, token
        )
        if not messages.read_receipt(match(ack, prompt), token):
            raise ValueError("finance did not acknowledge this delivery")
        actual = timing_world.settle(stock, checkpoint, pick)
        if actual != row["settlements"][role]:
            raise ValueError(
                f"stock settlement differs for world {row['world']}, repeat {row['repeat']}, seat {role}"
            )
    return len(turns)


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
    print(
        json.dumps(
            {
                "verified_checkpoints": len({r["world"] for r in complete}),
                "verified_pairs": len(complete),
                "rebuilt_turns": turns,
                "stock_settlements": 2 * len(complete),
                "incomplete_pairs_not_replayed": len(eligible) - len(complete),
                "new_model_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
