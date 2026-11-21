"""Exchange actual archived notes between two independently replaceable readers."""

import argparse
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..coverage import log
from ..coverage.__main__ import _ledger
from ..coverage.jobs import Request
from ..handoff import external_audit, packet
from ..handoff.__main__ import _load, _meter, _print
from ..visibility.clarify_run import _directory, _private
from ..visibility.omp_seat import _validate_model
from ..visibility.route_run import Answer
from . import analysis, source

_READERS = ("R0", "R1")


def _reader(slot: int) -> str:
    return _READERS[slot % 2]


def _work(index: int, request: Request) -> dict[str, Any]:
    return {
        "index": index,
        "run": request.key,
        "slot": request.slot,
        "reader": _reader(request.slot),
        "writer": f"W{(request.slot // 2) % 2}",
    }


def _native(args: argparse.Namespace):
    from ..handoff.native_input import build

    corpus = build(args.trial, args.ids)
    packet.parse(corpus.packet, args.sha256)
    return corpus


def _subscription(rows: list[dict[str, Any]], path: Path, model: str) -> dict[str, Any]:
    _validate_model(model)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("receipt directory must be absolute")
    role = "route_decide"
    expected = Counter(
        (role, row["prompt_sha256"], row["response_sha256"])
        for row in rows
        if row["status"] == "received"
    )
    errors = sum(row["status"] == "callback_error" for row in rows)
    with _directory(path) as fd:
        _private(fd, mode=0o700)
        totals = external_audit._receipts(fd, role, model, expected, errors)
    return {
        "requested_model": model,
        "verified_served_model": model if totals["matched"] else None,
        **totals,
    }


def _run(args: argparse.Namespace) -> None:
    if args.mode == "stock":
        _print(analysis.stock(_native(args), external_audit._report(args.report)))
        return
    subject = _load(args)
    blocks = packet.grid(subject)
    if args.mode == "verify":
        report = external_audit._report(args.report)
        if report["schema"] != "seatlab.exchange-report/v1":
            raise ValueError("unexpected report schema")
        summary = analysis.summarize(blocks, report["source"], report["rows"])
        if summary != report["summary"]:
            raise ValueError("published choices and summary differ")
        _print(
            {
                "packet_sha256": subject.sha256,
                "source_sha256": report["source"]["source_sha256"],
                "summary": summary,
            }
        )
        return
    if len(args.writer) != 2:
        raise ValueError("provide exactly two writer studies")
    registry, requests = source.build(
        blocks, (args.writer[0], args.writer[1]), tuple(args.indices)
    )
    seal = registry["source_sha256"]
    if args.mode == "seal":
        _print(registry)
        return
    if seal != args.source_sha256:
        raise ValueError("frozen source changed")
    lanes = {
        reader: tuple(r for r in requests if _reader(r.slot) == reader)
        for reader in _READERS
    }
    if args.mode == "plan":
        _print(
            {
                "source_sha256": seal,
                "readers": {
                    name: [_work(i, r) for i, r in enumerate(lane)]
                    for name, lane in lanes.items()
                },
            }
        )
        return
    rows = log.collect(args.study, seal, requests)
    directory = getattr(args, "spend_dir", None)
    if args.mode == "direct":
        directory = Path(os.environ.get("SEATLAB_SPEND_DIR", ""))
        if not directory.is_absolute():
            raise ValueError("use the original absolute spend directory")
    ledger = None if directory is None else _ledger(directory, seal, requests)
    occupied = {r["run"] for r in rows if r["state"] != "unclaimed"}
    if ledger is not None:
        occupied.update(r["run"] for r in ledger["calls"])
    if args.mode == "pending":
        _print(
            {
                "source_sha256": seal,
                "readers": {
                    name: [i for i, r in enumerate(lane) if r.key not in occupied]
                    for name, lane in lanes.items()
                },
            }
        )
        return
    if args.mode == "report":
        out: dict[str, Any] = {
            "schema": "seatlab.exchange-report/v1",
            "source": registry,
            "rows": rows,
            "summary": analysis.summarize(blocks, registry, rows),
        }
        if (ledger is None) != (args.direct_reader is None):
            raise ValueError("a direct meter needs both reader and spend directory")
        if ledger is not None:
            direct = [r for r in rows if _reader(r["slot"]) == args.direct_reader]
            runs = {r["run"] for r in direct}
            if any(b["run"] not in runs for b in ledger["calls"]):
                raise ValueError("a direct bill belongs to another reader")
            actual = [r for r in direct if r["status"] != "missing"]
            out.setdefault("meter", {})["direct"] = {
                "reader": args.direct_reader,
                **_meter([{"turns": actual}], ledger),
            }
        external = (args.subscription_reader, args.receipts, args.model)
        if any(v is not None for v in external):
            if None in external or args.direct_reader == args.subscription_reader:
                raise ValueError(
                    "bind a separate subscription reader, model and receipt directory"
                )
            subset = [r for r in rows if _reader(r["slot"]) == args.subscription_reader]
            out.setdefault("meter", {})["subscription"] = {
                "reader": args.subscription_reader,
                **_subscription(subset, args.receipts, args.model),
            }
        _print(out)
        return
    lane = lanes[args.reader]
    start, stop = args.start, args.stop if args.stop is not None else args.start + 1
    if not 0 <= start < stop <= len(lane) or any(
        r.key in occupied for r in lane[start:stop]
    ):
        raise ValueError("invalid or occupied reader work range")
    if args.mode == "direct":
        from ..visibility.route_cli import _admit, _direct

        corpus = _native(args)
        if any(
            s.get(k) != "bound"
            for s in corpus.receipt["sources"]
            for k in ("inventory_basis", "dispatch_basis")
        ):
            raise ValueError("the paid transport requires native role acknowledgments")
        _admit()
        respond = _direct
    else:
        from ..visibility.seat_command import load_commands

        seat = load_commands(args.config)[_READERS.index(args.reader)]

        def respond(prompt: str, _run_id: str, _role: str) -> Answer:
            return Answer(seat(prompt))

    for i in range(start, stop):
        request = lane[i]
        row = log.play(args.study, seal, request, respond)
        _print(
            _work(i, request)
            | {"status": row["status"], "delivered": row["choices"] is not None}
        )
        if row["status"] != "received":
            raise ValueError("reader stopped; first attempt retained")


def main(argv: Sequence[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in (
        "seal",
        "plan",
        "pending",
        "commands",
        "direct",
        "report",
        "verify",
        "stock",
    ):
        sub = modes.add_parser(mode, allow_abbrev=False)
        sub.add_argument(
            "--sha256", required=True, help="Expected original packet digest."
        )
        if mode in ("direct", "stock"):
            sub.add_argument(
                "--trial",
                action="append",
                type=Path,
                required=True,
                help="Native source trial, in original order.",
            )
            sub.add_argument("--ids", nargs="+", type=int, required=True)
        if mode != "stock":
            sub.add_argument("--packet", type=Path, required=True)
        if mode in ("verify", "stock"):
            sub.add_argument("report", type=Path, help="Public exchange report.")
            continue
        sub.add_argument(
            "--writer",
            action="append",
            type=Path,
            required=True,
            help="Private handoff journal, W0 then W1.",
        )
        sub.add_argument(
            "--indices",
            nargs="+",
            type=int,
            required=True,
            help="Increasing indices in the original handoff register.",
        )
        if mode == "seal":
            continue
        sub.add_argument(
            "--source-sha256",
            required=True,
            help="Digest from seal; never change a running study.",
        )
        if mode == "plan":
            continue
        sub.add_argument(
            "--study",
            type=Path,
            required=True,
            help="Separate private first-attempt directory.",
        )
        if mode != "direct":
            sub.add_argument(
                "--spend-dir",
                type=Path,
                help="Original ledger when a direct reader participates.",
            )
        if mode == "report":
            sub.add_argument("--direct-reader", choices=_READERS)
            sub.add_argument("--subscription-reader", choices=_READERS)
            sub.add_argument("--receipts", type=Path)
            sub.add_argument("--model")
        if mode in ("direct", "commands"):
            sub.add_argument("--reader", required=True, choices=_READERS)
            sub.add_argument(
                "--start", type=int, default=0, help="First work index for this reader."
            )
            sub.add_argument(
                "--stop",
                type=int,
                help="First excluded work index. Default: start + 1.",
            )
        if mode == "commands":
            sub.add_argument(
                "--config",
                type=Path,
                required=True,
                help="Trusted programs: reviewer=R0, decider=R1.",
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
            "Cannot verify source, reader, work range, or receipts. Keep occupied attempts closed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
