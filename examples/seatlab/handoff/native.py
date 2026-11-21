"""Replay accepted reports from a completed native Dispatch Handoff trial."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from ..visibility.clarify_run import _object
from . import native_input, packet
from .__main__ import _print, _run_subject


def _run(args: argparse.Namespace) -> None:
    corpus = native_input.build(args.trial, args.ids)
    subject = packet.parse(corpus.packet, getattr(args, "sha256", None))
    if args.mode == "packet":
        sys.stdout.buffer.write(corpus.packet)
    elif args.mode == "receipt":
        _print(corpus.receipt)
    elif args.mode == "stock":
        from . import native_stock

        with args.report.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("oversize report")
        _print(native_stock.stock(corpus, _object(raw)))
    elif args.mode == "direct":
        if any(
            row.get("inventory_basis") != "bound"
            or row.get("dispatch_basis") != "bound"
            for row in corpus.receipt["sources"]
        ):
            raise ValueError("direct mode needs recorded seat acknowledgments")
        _run_subject(args, subject)


def main(argv: Sequence[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in ("packet", "receipt", "stock", "direct"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        sub.add_argument(
            "--trial",
            type=Path,
            action="append",
            required=True,
            help="Completed native trial directory. Repeat in a fixed order for multiple trials.",
        )
        sub.add_argument(
            "--ids",
            type=int,
            nargs="+",
            required=True,
            help="2 to 8 distinct shipment IDs, in replay order.",
        )
        if mode in ("direct", "stock"):
            sub.add_argument(
                "--sha256",
                required=True,
                help="Pin packet_sha256 from the original receipt.",
            )
        if mode == "stock":
            sub.add_argument(
                "--report",
                type=Path,
                required=True,
                help="Read-only public handoff report on this packet.",
            )
        elif mode == "direct":
            sub.add_argument(
                "--study",
                type=Path,
                required=True,
                help="New private study directory, outside the checkout.",
            )
            sub.add_argument(
                "--start",
                type=int,
                default=0,
                help="First block index. Never repeat an attempt.",
            )
            sub.add_argument(
                "--stop", type=int, help="First excluded block. Default: start + 1."
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
            "Cannot verify the recorded Harbor trials, their packet, or the requested first attempt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
