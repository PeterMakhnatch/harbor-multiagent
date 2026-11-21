"""Run live role programs from two frozen Harbor messages, or check their public decisions offline."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from ..briefing import protocol as briefing
from . import protocol

_RESULTS = Path(__file__).resolve().parents[1] / "results"


def _print(value: object) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _grid() -> list[dict[str, Any]]:
    return [
        {"source_id": f"s{seed}-r{rep}", "seed": seed, "repetition": rep, "arm": arm}
        for seed in briefing.SEEDS
        for rep in briefing.REPETITIONS
        for arm in protocol.arms(seed, rep)
    ]


def _load(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    from .packet import load

    if (args.packet is None) != (args.sha256 is None):
        raise ValueError("provide both packet and digest")
    if args.packet is None:
        digest = (_RESULTS / "turn-fork.sha256").read_text(encoding="ascii").strip()
        path = _RESULTS / "turn-fork-packet.json"
    else:
        path, digest = args.packet, args.sha256
    return load(path, digest), digest


def _envelope(
    packet: dict[str, Any], digest: str, transport: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    from ..visibility.deepseek import _ACCEPTED_MODELS
    from .analysis import summarize

    if transport not in ("direct", "commands"):
        raise ValueError("unknown transport")
    summary = summarize(packet, digest, rows)
    for row in rows:
        for turn in row["turns"]:
            if turn["status"] != "received":
                continue
            usage = turn["usage"]
            if (transport == "commands") != (usage is None):
                raise ValueError("turn usage does not match transport")
            if (
                usage is not None
                and usage["served_model"].casefold() not in _ACCEPTED_MODELS
            ):
                raise ValueError("unregistered direct server")
    return {
        "schema": "seatlab.turn-fork-report/v1",
        "transport": transport,
        "packet_sha256": digest,
        "rows": rows,
        "summary": summary,
    }


def verified(packet: dict[str, Any], digest: str, path: Path) -> dict[str, Any]:
    from ..handoff.external_audit import _report

    public = _report(path)
    actual = _envelope(packet, digest, public["transport"], public["rows"])
    if json.dumps(public, sort_keys=True, allow_nan=False) != json.dumps(
        actual, sort_keys=True, allow_nan=False
    ):
        raise ValueError("public report differs")
    return actual


def _collect(packet: dict[str, Any], digest: str, study: Path) -> dict[str, Any]:
    from . import journal, run

    transport = run.load(study, digest)["transport"]
    sources = {source["source_id"]: source for source in packet["sources"]}
    rows = []
    for cell in _grid():
        source = sources.get(cell["source_id"])
        row = (
            dict(cell, state="no_source", outcome=None, turns=[])
            if source is None
            else journal.collect(study, digest, source, cell["arm"])
        )
        rows.append(row)
    return _envelope(packet, digest, transport, rows)


def _dispatch(args: argparse.Namespace) -> int:
    if args.mode == "export":
        from .extract import build

        _print(build(args.native_study, args.native_report))
        return 0
    packet, digest = _load(args)
    if args.mode == "plan":
        enrolled = {s["source_id"] for s in packet["sources"]}
        _print(
            {
                "schema": "seatlab.turn-fork-plan/v1",
                "packet_sha256": digest,
                "available_sources": len(enrolled),
                "registered_cells": 36,
                "max_first_calls": 2 * 3 * len(enrolled),
                "cells": [
                    dict(row, index=i, available=row["source_id"] in enrolled)
                    for i, row in enumerate(_grid())
                ],
            }
        )
    elif args.mode == "show":
        source = next(
            (s for s in packet["sources"] if s["source_id"] == args.source), None
        )
        if source is None:
            raise ValueError("source unavailable")
        print(
            protocol.push_prompt(source, args.arm)
            if args.opened is None
            else protocol.decide_prompt(source, (args.opened[0], args.opened[1]))
        )
    elif args.mode in ("seal", "run"):
        from . import run

        if args.mode == "seal":
            _print(
                run.seal(
                    args.study, digest, args.transport, args.config, args.spend_dir
                )
            )
        else:
            stop = args.start + 1 if args.stop is None else args.stop
            if not 0 <= args.start < stop <= 36:
                raise ValueError("invalid registered range")
            rows = run.run_cells(packet, digest, args.study, range(args.start, stop))
            _print(rows)
            return int(any(row["state"] == "incomplete" for row in rows))
    elif args.mode == "report":
        _print(_collect(packet, digest, args.study))
    else:
        report = verified(packet, digest, args.report)
        if args.mode == "verify":
            _print(report["summary"])
        elif args.mode == "stock":
            from .stock import compare

            _print(compare(packet, report, args.native_study))
        else:
            from . import attribution

            if args.mode == "receipts":
                _print(
                    attribution.subscriptions(
                        report,
                        args.inventory_receipts,
                        args.dispatch_receipts,
                        args.model,
                    )
                )
            else:
                from ..visibility import spend

                _print(
                    attribution.direct(
                        report,
                        spend.report(args.spend_dir, run_prefix=f"tf-{digest[:12]}-"),
                    )
                )
    return 0


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for name in (
        "plan",
        "show",
        "export",
        "seal",
        "run",
        "report",
        "verify",
        "stock",
        "receipts",
        "meter",
    ):
        parser = modes.add_parser(name, allow_abbrev=False)
        if name == "export":
            parser.add_argument("--native-study", type=Path, required=True)
            parser.add_argument("--native-report", type=Path, required=True)
            continue
        parser.add_argument(
            "--packet",
            type=Path,
            help="Use a different exported packet; requires --sha256.",
        )
        parser.add_argument("--sha256", help="Exact SHA-256 of the packet bytes.")
        if name in ("seal", "run", "report"):
            parser.add_argument("--study", type=Path, required=True)
        if name == "seal":
            parser.add_argument(
                "--transport", choices=("commands", "direct"), required=True
            )
            parser.add_argument("--config", type=Path)
            parser.add_argument("--spend-dir", type=Path)
        elif name == "run":
            parser.add_argument("--start", type=int, required=True)
            parser.add_argument(
                "--stop", type=int, help="Exclusive; default is one first attempt."
            )
        elif name == "show":
            parser.add_argument("--source", required=True)
            parser.add_argument(
                "--arm", choices=("aligned", "shifted", "repeat"), default="aligned"
            )
            parser.add_argument(
                "--opened",
                nargs=2,
                metavar=("ALIAS1", "ALIAS2"),
                help="Show the final dispatcher prompt for this selection instead.",
            )
        elif name in ("verify", "stock", "receipts", "meter"):
            parser.add_argument("report", type=Path)
            if name == "stock":
                parser.add_argument("--native-study", type=Path, required=True)
            elif name == "receipts":
                parser.add_argument("--inventory-receipts", type=Path, required=True)
                parser.add_argument("--dispatch-receipts", type=Path, required=True)
                parser.add_argument("--model", required=True)
            elif name == "meter":
                parser.add_argument("--spend-dir", type=Path, required=True)
    try:
        return _dispatch(cli.parse_args(argv))
    except (
        KeyError,
        IndexError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        RecursionError,
        sqlite3.Error,
    ):
        print(
            "Cannot verify the registered continuation, its source, or its first-attempt evidence.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
