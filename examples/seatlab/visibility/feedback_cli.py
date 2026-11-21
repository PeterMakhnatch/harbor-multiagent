"""Run two seat programs through a changing stock channel, or read a study offline."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import feedback_protocol as protocol
from . import feedback_run
from .route_run import Answer


def _emit(value: object) -> None:
    print(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False),
        flush=True,
    )


def _transport(args: argparse.Namespace) -> Callable[[str, str, str], Answer]:
    if args.mode == "direct":
        from .route_cli import _admit, _direct

        _admit()
        return _direct
    if args.mode == "commands":
        from .seat_command import load_commands

        reviewer, decider = load_commands(args.config)

        def programs(prompt: str, _run: str, role: str) -> Answer:
            seat = reviewer if role == "route_review" else decider
            return Answer(seat(prompt))

        return programs

    def fixture(_prompt: str, run: str, _role: str) -> Answer:
        # A fixed transport fixture, not a simulated adaptive team.
        if run.endswith("-select"):
            return Answer('{"ask":"X"}')
        if run.endswith("-review"):
            return Answer('{"available":["A","B"]}')
        if run.endswith("-decide"):
            return Answer('{"bay":"A"}')
        raise ValueError("unknown stage")

    return fixture


def _meter(blocks: list[dict[str, Any]], directory: Path) -> dict[str, Any]:
    from . import route_audit, spend

    costs = spend.report(directory, run_prefix="hz-")
    ledger = {(r["run"], r["role"]): r for r in costs["calls"]}
    matches: Counter[str] = Counter()
    for block in blocks:
        for turn in block["turns"]:
            key = turn["run"], turn["role"]
            usage = route_audit._Turn(turn["status"], usage=turn["usage"])
            matches[route_audit._meter(usage, ledger.pop(key, None), True)] += 1
    return {
        "by_match": dict(matches),
        "ledger_without_turn": len(ledger),
        "unsettled_calls": costs["unsettled_calls"],
        "known_usd_low": costs["known_usd_low"],
        "upper_usd_including_holds": costs["upper_usd_including_holds"],
    }


def _run(args: argparse.Namespace) -> None:
    blocks = protocol.grid(tuple(args.seed) if args.seed else protocol.SEEDS)
    if args.mode == "plan":
        _emit(
            {
                "schema": "seatlab.feedback-plan/v1",
                "max_calls_per_block": 52,
                "blocks": [
                    {
                        "index": index,
                        "block": b.key,
                        "seed": b.seed,
                        "regime": b.regime,
                        "rep": b.rep,
                        "fingerprint": protocol.fingerprint(b),
                    }
                    for index, b in enumerate(blocks)
                ],
            }
        )
        return
    if args.mode == "report":
        from .feedback_audit import collect

        result = collect(args.study, blocks)
        if args.spend_dir is not None:
            result["meter"] = _meter(result["blocks"], args.spend_dir)
        _emit(result)
        return
    stop = args.start + 1 if args.stop is None else args.stop
    if not 0 <= args.start < stop <= len(blocks):
        raise ValueError("range outside register")
    selected = blocks[args.start : stop]
    if any(os.path.lexists(args.study / block.key) for block in selected):
        raise ValueError("requested range contains a first attempt")
    if args.mode == "direct":
        from . import spend

        ledger = Path(os.environ.get("SEATLAB_SPEND_DIR", ""))
        if not ledger.is_absolute():
            raise ValueError(
                "direct mode requires the original absolute spend directory"
            )
        prefixes = tuple(b.key + "-" for b in selected)
        if any(
            row["run"].startswith(prefixes)
            for row in spend.report(ledger, run_prefix="hz-")["calls"]
        ):
            raise ValueError("requested range contains a prior reservation")
    respond = _transport(args)
    for i, block in enumerate(selected, args.start):
        rows = feedback_run.play(args.study, block, respond)
        _emit(
            {
                "index": i,
                "block": block.key,
                "completed_decisions": sum(row["status"] == "complete" for row in rows),
                "scheduled_decisions": len(rows),
            }
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "report", "demo", "commands", "direct"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        if mode == "direct":
            sub.set_defaults(seed=None)
        else:
            sub.add_argument(
                "--seed",
                type=int,
                action="append",
                help="Use your own native seed. Repeat for several seeds.",
            )
        if mode != "plan":
            sub.add_argument(
                "--study",
                required=True,
                type=Path,
                help="Existing owner-only physical directory outside the checkout.",
            )
        if mode == "report":
            sub.add_argument(
                "--spend-dir",
                type=Path,
                help="Original direct ledger. Omit for external programs.",
            )
        if mode in ("demo", "commands", "direct"):
            sub.add_argument(
                "--start", type=int, default=0, help="First block, included."
            )
            sub.add_argument(
                "--stop", type=int, help="First excluded block. Default: start + 1."
            )
        if mode == "commands":
            sub.add_argument(
                "--config",
                required=True,
                type=Path,
                help="Trusted reviewer and decider executable configuration.",
            )
    args = parser.parse_args(argv)
    try:
        _run(args)
    except (KeyError, OSError, ValueError, RuntimeError, sqlite3.Error):
        # Seat output, paths, and provider exceptions can contain private data.
        print(
            "Cannot verify the register, original study, or first attempt. Do not repeat an attempted block.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
