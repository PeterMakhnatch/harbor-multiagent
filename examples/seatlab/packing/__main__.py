"""Compare checkpoint encoders without starting the original writer again."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..handoff.__main__ import _load, _meter, _print
from ..handoff.packet import grid
from ..visibility.clarify_run import _object
from ..visibility.route_run import Answer
from . import analysis, log, source

_STAGES = ("rewrite", "prefix-decide", "rewrite-decide", "both-decide")


def _ledger(root: Path, frozen: source.Frozen) -> dict[str, Any]:
    from ..visibility import spend

    return spend.report(root, run_prefix=f"np-{frozen.sha256[:12]}-")


def _bill_claims(frozen: source.Frozen, bills: dict[str, Any]) -> set[str]:
    keys = {
        (
            f"{key}-{stage}",
            "route_review" if stage == "rewrite" else "route_decide",
        ): key
        for item in frozen.entries
        for key in (log.key(item, frozen.sha256),)
        for stage in _STAGES
    }
    return {keys[row["run"], row["role"]] for row in bills["calls"]}


def _seats(args: argparse.Namespace) -> Callable[[str, str, str], Answer]:
    if args.mode == "commands":
        from ..visibility.seat_command import load_commands

        packer, successor = load_commands(args.config)
        return lambda prompt, _run, role: Answer(
            (packer if role == "route_review" else successor)(prompt)
        )
    from ..visibility.route_cli import _admit, _direct

    _admit()
    return _direct


def _run(args: argparse.Namespace) -> None:
    if args.mode == "stock":
        with args.report.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("oversize report")
        _print(analysis.stock(_object(raw)))
        return
    subject = _load(args)
    blocks = grid(subject)
    frozen = source.load(args.original, blocks)
    if args.mode == "seal":
        _print(
            {
                "schema": "seatlab.packing-source/v1",
                "packet_sha256": frozen.packet_sha256,
                "source_sha256": frozen.sha256,
                "slots": len(frozen.entries),
                "sources": dict(Counter(item.status for item in frozen.entries)),
            }
        )
        return
    if frozen.sha256 != args.source_sha256:
        raise ValueError("original source changed")
    rows = log.collect(args.study, frozen)
    directory = getattr(args, "spend_dir", None)
    if args.mode == "direct":
        directory = Path(os.environ.get("SEATLAB_SPEND_DIR", ""))
        if not directory.is_absolute():
            raise ValueError("direct mode requires the original ledger")
    bills = None if directory is None else _ledger(directory, frozen)
    if args.mode == "report":
        result = {
            "schema": "seatlab.packing-report/v1",
            "packet_sha256": frozen.packet_sha256,
            "source_sha256": frozen.sha256,
            "outcomes": rows,
            **analysis.summarize(blocks, rows),
        }
        if bills is not None:
            result["meter"] = _meter(rows, bills)
        _print(result)
        return
    occupied = {row["block"] for row in rows if row["state"] != "unclaimed"}
    if bills is not None:
        occupied |= _bill_claims(frozen, bills)
    if args.mode == "pending":
        _print(
            {
                "source_sha256": frozen.sha256,
                "occupied": len(occupied),
                "unclaimed": [
                    i for i, row in enumerate(rows) if row["block"] not in occupied
                ],
            }
        )
        return
    stop = args.start + 1 if args.stop is None else args.stop
    if not 0 <= args.start < stop <= len(rows) or any(
        rows[i]["block"] in occupied for i in range(args.start, stop)
    ):
        raise ValueError("invalid or occupied range")
    respond = _seats(args)
    for i in range(args.start, stop):
        outcome = log.play(args.study, frozen, frozen.entries[i], respond)
        statuses = {name: arm["status"] for name, arm in outcome["arms"].items()}
        _print(
            {
                "index": i,
                "block": rows[i]["block"],
                "source": outcome["source_status"],
                "arms": statuses,
            }
        )
        if any(
            status in {"missing_rewrite", "missing_decide"}
            for status in statuses.values()
        ):
            raise ValueError("transport stopped")


def main(argv: Sequence[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in ("seal", "pending", "commands", "direct", "report", "stock"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        if mode == "stock":
            sub.add_argument(
                "report", type=Path, help="Public default-domain packing report."
            )
            continue
        sub.add_argument(
            "--original",
            required=True,
            type=Path,
            help="Original private handoff study, after the writers stopped.",
        )
        if mode == "direct":
            sub.set_defaults(packet=None, sha256=None)
        else:
            sub.add_argument(
                "--packet",
                type=Path,
                help="Original domain packet; default is Dispatch Handoff.",
            )
            sub.add_argument(
                "--sha256",
                help="Expected digest of the exact domain packet. Required with --packet.",
            )
        if mode == "seal":
            continue
        sub.add_argument(
            "--source-sha256",
            required=True,
            help="Source digest returned by seal, before any packing call.",
        )
        sub.add_argument(
            "--study",
            required=True,
            type=Path,
            help="Separate private output directory outside the checkout.",
        )
        if mode in ("pending", "report"):
            sub.add_argument(
                "--spend-dir",
                type=Path,
                help="Original direct-provider spend ledger, if used.",
            )
        if mode in ("commands", "direct"):
            sub.add_argument(
                "--start", type=int, default=0, help="First block index to attempt."
            )
            sub.add_argument(
                "--stop", type=int, help="First excluded index. Default: start + 1."
            )
        if mode == "commands":
            sub.add_argument(
                "--config",
                type=Path,
                required=True,
                help="Trusted reviewer (packer) and decider (successor) executables.",
            )
    args = cli.parse_args(argv)
    try:
        _run(args)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, sqlite3.Error):
        print(
            "Cannot verify the original source, packing evidence, or first-attempt range. Do not retry an attempted block.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
