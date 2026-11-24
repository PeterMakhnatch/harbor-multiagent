"""Inspect completed Harbor teams and their costs. Never starts a process, model or job."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn, override

from . import account_report
from .seat_stream import MIB, UUID, Malformed, Record, _read, _require, _strict_json

RESULTS = Path(__file__).resolve().parents[1] / "results"
CALENDAR = "calendar-native-repeatability"
MAX_PACKET_BYTES = 48 * MIB
MAX_BATCH_BYTES = 128 * MIB
MAX_ARGS = 384
PROG = "python3.12 -BS -m examples.seatlab.workbench account"


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid account arguments. Use account --help.\n")


def parser() -> argparse.ArgumentParser:
    root = Parser(
        prog=PROG,
        description=__doc__,
        epilog="No-launch previews: account plan --help for a native lock; account shipping-plan --help for a registered two-world campaign.",
    )
    sub = root.add_subparsers(dest="source", required=True)
    native = sub.add_parser(
        "native", help="read the final results in a saved Harbor job"
    )
    native.add_argument("job", metavar="JOB_DIR")
    history = native.add_mutually_exclusive_group()
    history.add_argument(
        "--archive", help="compare private per-execution receipts retained before retry"
    )
    history.add_argument(
        "--fragment",
        action="append",
        metavar="DISPLACED_DIR",
        help="inspect displaced trial directories; never use their grades as final (repeatable)",
    )
    archive = sub.add_parser(
        "calendar", help="inspect the released 96-team calendar archive"
    )
    archive.add_argument("--teams", default=str(RESULTS / f"{CALENDAR}.json"))
    archive.add_argument("--turns", default=str(RESULTS / f"{CALENDAR}-turns.json"))
    archive.add_argument("--cost", default=str(RESULTS / f"{CALENDAR}-cost.json"))
    merge = sub.add_parser(
        "merge", help="combine portable worker packets for disjoint scopes"
    )
    merge.add_argument("packets", nargs="+", metavar="PACKET")
    ledger = sub.add_parser(
        "ledger",
        help="explain a saved settlement ledger without assuming it belongs to a job",
    )
    ledger.add_argument("events", metavar="EVENTS.jsonl")
    ledger.add_argument(
        "--family",
        action="append",
        default=[],
        metavar="TAG",
        help="group local call identities starting with this exact TAG/ (repeatable)",
    )
    ledger.add_argument(
        "--routes", action="store_true", help="show requested and reported model labels"
    )
    ledger.add_argument(
        "--json", action="store_true", help="print exact decimal subtotals"
    )
    for command in (native, archive, merge):
        fmt = command.add_mutually_exclusive_group()
        fmt.add_argument(
            "--json",
            action="store_true",
            help="print machine-readable evidence coverage",
        )
        if command is not merge:
            fmt.add_argument(
                "--packet",
                action="store_true",
                help="export only the allowlisted worker packet as JSON",
            )
        command.add_argument(
            "--trial", metavar="UUID", help="inspect one retained final execution"
        )
        command.add_argument(
            "--ledger",
            metavar="EVENTS.jsonl",
            help="read a saved OpenRouter generation settlement ledger; never contacts the provider",
        )
        command.add_argument(
            "--owners",
            metavar="OWNERS.jsonl",
            help="bind generation receipts using exact host-captured request hashes; requires --ledger",
        )
    return root


def _files(paths: list[str]) -> list[Record]:
    _require(
        1 <= len(paths) <= account_report.MAX_PACKETS,
        "expected 1 to 128 worker packets",
    )
    records = []
    remaining = MAX_BATCH_BYTES
    for path in paths:
        data = _read(path, min(remaining, MAX_PACKET_BYTES), "worker packet")
        remaining -= len(data)
        records.append(_strict_json(data, "worker packet is not strict UTF-8 JSON"))
    return records


def _packets(args: argparse.Namespace) -> list[Record]:
    if args.source == "merge":
        return _files(args.packets)
    if args.source == "native":
        from .native_account import build

        return [build(args.job)]
    from .calendar_account import build as calendar

    return [calendar(args.teams, args.turns, args.cost)]


def _json(value: object) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > MAX_ARGS or any(len(os.fsencode(a)) > 4096 for a in args):
        print("Invalid account arguments.", file=sys.stderr)
        return 2
    if args and args[0] == "plan":
        from .lock_cost import main as preview_lock

        return preview_lock(args[1:])
    if args and args[0] == "shipping-plan":
        from .cost_plan import main as preview_shipping

        return preview_shipping(args[1:])
    try:
        options = parser().parse_args(args)
        if options.source == "ledger":
            from . import ledger_scope

            report = ledger_scope.summarize(
                options.events, families=options.family, routes=options.routes
            )
            print(_json(report) if options.json else ledger_scope.render(report))
            return 0
        exporting = getattr(options, "packet", False)
        kept = getattr(options, "archive", None)
        fragments = getattr(options, "fragment", None)
        _require(
            not exporting
            or not (
                kept or fragments or options.ledger or options.owners or options.trial
            ),
            "export a packet before adding external evidence or selecting a trial",
        )
        _require(
            not options.owners or options.ledger is not None,
            "ownership records require a generation ledger",
        )
        _require(
            options.trial is None or UUID.fullmatch(options.trial) is not None,
            "invalid requested execution UUID",
        )
        packets, copies = account_report.unique(_packets(options))
        if exporting:
            print(_json(packets[0]))
            return 0
        report = account_report.summarize(packets, copies=copies, trial=options.trial)
        if kept:
            from .account_history import history

            report["attempt_archive"] = history(packets[0], kept)
        if fragments:
            from .account_fragments import recover

            report["displaced"] = recover(packets[0], fragments)
        if options.ledger:
            from .generation_receipts import reconcile

            report["provider_receipts"] = reconcile(
                packets, options.ledger, options.owners
            )
        if options.json:
            print(_json(report))
        else:
            print(account_report.text(report))
            if (displaced := report.get("displaced")) is not None:
                from .account_fragments import text

                print(text(displaced))
        return 0
    except Malformed as error:
        print(f"Account input rejected: {error}.", file=sys.stderr)
        return 2
    except OSError:
        print("Cannot read or write local accounting data.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
