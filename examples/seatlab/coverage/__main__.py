"""Replay one observed handoff as a batch, a checklist, or independent answer owners."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..handoff.__main__ import _load, _meter, _print
from ..handoff.packet import grid
from ..visibility.clarify_run import _object
from ..visibility.route_run import Answer
from . import analysis, jobs, log, source


def _slots(frozen: source.Frozen) -> list[dict[str, Any]]:
    return [
        {
            "block": entry.block.key,
            "case": entry.block.case.id,
            "cluster": entry.block.case.cluster,
            "rep": entry.block.rep,
            "status": entry.status,
            "prompt_sha256": entry.prompt_sha256,
        }
        for entry in frozen.entries
    ]


def _ledger(
    directory: Path, frozen_sha256: str, requests: tuple[jobs.Request, ...]
) -> dict[str, Any]:
    from ..visibility import spend

    ledger = spend.report(directory, run_prefix=f"cv-{frozen_sha256[:12]}-")
    keys = {r.key for r in requests}
    if any(
        row["run"] not in keys or row["role"] != "route_decide"
        for row in ledger["calls"]
    ):
        raise ValueError("spend ledger contains an unregistered call")
    return ledger


def _account(rows: list[dict[str, Any]], ledger: dict[str, Any]) -> dict[str, Any]:
    actual = [row for row in rows if row["status"] != "missing"]
    result = _meter([{"turns": actual}], ledger)
    by_run = {row["run"]: row["policy"] for row in rows}
    totals = {
        policy: [Decimal(0), Decimal(0)] for policy in ("batch", "checklist", "owners")
    }
    n: Counter[str] = Counter()
    for bill in ledger["calls"]:
        policy = by_run[bill["run"]]
        totals[policy][0] += Decimal(bill["estimated_usd_low"] or "0")
        totals[policy][1] += Decimal(
            bill["estimated_usd_high"] or bill["unsettled_hold_usd"]
        )
        n[policy] += 1
    result["by_policy"] = {
        key: {
            "calls": n[key],
            "known_usd_low": f"{low:.9f}",
            "upper_usd_including_holds": f"{high:.9f}",
        }
        for key, (low, high) in totals.items()
    }
    return result


def _work(index: int, request: jobs.Request) -> dict[str, object]:
    return {
        "index": index,
        "run": request.key,
        "slot": request.slot,
        "policy": request.policy,
        "target": request.target,
    }


def _transport(
    args: argparse.Namespace, requests: tuple[jobs.Request, ...]
) -> Callable[[str, str, str], Answer]:
    if args.mode == "direct":
        from ..visibility.route_cli import _admit, _direct

        _admit()
        return _direct
    from ..visibility.seat_command import load_commands

    owner, central = load_commands(args.config)
    owners = {r.key for r in requests if r.policy == "owners"}
    return lambda prompt, run, _role: Answer(
        (owner if run in owners else central)(prompt)
    )


def _run(args: argparse.Namespace) -> None:
    if args.mode == "stock":
        with args.report.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("oversize report")
        _print(analysis.stock(_object(raw)))
        return
    packet = _load(args)
    blocks = grid(packet)
    frozen = source.load(args.original, blocks)
    slots = _slots(frozen)
    if args.mode == "seal":
        _print(
            {
                "schema": "seatlab.coverage-source/v1",
                "packet_sha256": packet.sha256,
                "source_sha256": frozen.sha256,
                "states": dict(Counter(entry.status for entry in frozen.entries)),
                "slots": slots,
            }
        )
        return
    if frozen.sha256 != args.source_sha256:
        raise ValueError("original final requests changed")
    requests = jobs.make(frozen)
    if args.mode == "plan":
        _print(
            {
                "source_sha256": frozen.sha256,
                "work": [_work(i, r) for i, r in enumerate(requests)],
            }
        )
        return
    rows = log.collect(args.study, frozen.sha256, requests)
    directory = getattr(args, "spend_dir", None)
    if args.mode == "direct":
        directory = Path(os.environ.get("SEATLAB_SPEND_DIR", ""))
        if not directory.is_absolute():
            raise ValueError(
                "direct mode requires the original absolute spend directory"
            )
    bills = None if directory is None else _ledger(directory, frozen.sha256, requests)
    if args.mode == "report":
        out = {
            "schema": "seatlab.coverage-report/v1",
            "packet_sha256": packet.sha256,
            "source_sha256": frozen.sha256,
            "slots": slots,
            "rows": rows,
            **analysis.summarize(blocks, frozen.sha256, slots, rows),
        }
        if bills is not None:
            out["meter"] = _account(rows, bills)
        _print(out)
        return
    occupied = {row["run"] for row in rows if row["state"] != "unclaimed"}
    if bills is not None:
        occupied.update(r["run"] for r in bills["calls"])
    if args.mode == "pending":
        _print(
            {
                "source_sha256": frozen.sha256,
                "occupied": len(occupied),
                "unclaimed": [
                    i for i, r in enumerate(requests) if r.key not in occupied
                ],
            }
        )
        return
    start = args.start
    stop = start + 1 if args.stop is None else args.stop
    if not 0 <= start < stop <= len(requests) or any(
        r.key in occupied for r in requests[start:stop]
    ):
        raise ValueError("invalid or occupied work index")
    answer = _transport(args, requests)
    for i in range(start, stop):
        request = requests[i]
        row = log.play(args.study, frozen.sha256, request, answer)
        _print(
            _work(i, request)
            | {"status": row["status"], "valid": row["choices"] is not None}
        )
        if row["status"] != "received":
            raise ValueError("worker transport stopped; first attempt retained")


def main(argv: Sequence[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for name in ("seal", "plan", "pending", "commands", "direct", "report", "stock"):
        sub = modes.add_parser(name, allow_abbrev=False)
        if name == "stock":
            sub.add_argument("report", type=Path, help="Public default-domain report.")
            continue
        sub.add_argument(
            "--original",
            required=True,
            type=Path,
            help="Original private handoff directory, after the team stopped.",
        )
        if name == "direct":
            sub.set_defaults(packet=None, sha256=None)
        else:
            sub.add_argument(
                "--packet",
                type=Path,
                help="Original handoff packet. Default is Dispatch Handoff.",
            )
            sub.add_argument(
                "--sha256", help="Expected exact packet digest, required with --packet."
            )
        if name == "seal":
            continue
        sub.add_argument(
            "--source-sha256",
            required=True,
            help="Frozen final-request digest from seal.",
        )
        if name == "plan":
            continue
        sub.add_argument(
            "--study",
            type=Path,
            required=True,
            help="Separate private directory for first attempts.",
        )
        if name in ("pending", "report"):
            sub.add_argument(
                "--spend-dir", type=Path, help="Original direct spend ledger, if used."
            )
        if name in ("direct", "commands"):
            sub.add_argument(
                "--start", type=int, default=0, help="First physical work index."
            )
            sub.add_argument(
                "--stop",
                type=int,
                help="First excluded work index. Default: start + 1.",
            )
        if name == "commands":
            sub.add_argument(
                "--config",
                type=Path,
                required=True,
                help="Trusted reviewer (record owner) and decider (batch and checklist) programs.",
            )
    args = cli.parse_args(argv)
    try:
        _run(args)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        RuntimeError,
        sqlite3.Error,
    ):
        print(
            "Cannot verify the original request, source seal, or first-attempt work index. Do not retry occupied work.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
