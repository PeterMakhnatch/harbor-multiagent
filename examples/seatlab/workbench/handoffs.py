import argparse
import json
import os
import sys
from typing import NoReturn, override

from ...seat_handoffs.attempt_report import report, text
from .native_account import build
from .seat_stream import Malformed
from .team_account import _files

PROG = "python3.12 -BS -m examples.seatlab.workbench handoffs"


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid handoff arguments. Use handoffs --help.\n")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 8 or any(len(os.fsencode(arg)) > 4096 for arg in args):
        print("Invalid handoff arguments.", file=sys.stderr)
        return 2
    parser = Parser(
        prog=PROG,
        allow_abbrev=False,
        description="Compare retained message observations across attempts of the same native team slot.",
        epilog="Exit 0: requested evidence complete. Exit 1: incomplete. Exit 2: input rejected. Never starts a trial.",
    )
    parser.add_argument(
        "source",
        metavar="JOB_OR_PACKET",
        help="a stationary native job directory; use --packet for a worker's exported JSON",
    )
    parser.add_argument(
        "archive",
        metavar="RECEIPT_DIR",
        help="the handoff keeper's directory; not an attempt account directory",
    )
    parser.add_argument(
        "--packet",
        action="store_true",
        help="read a packet from 'account native JOB --packet' instead of a job directory",
    )
    parser.add_argument(
        "--require-sealed",
        action="store_true",
        help="also require a sealed world journal for every expected execution (at least one)",
    )
    parser.add_argument(
        "--json", action="store_true", help="include the per-slot comparison records"
    )
    opts = parser.parse_args(args)
    try:
        packet = _files([opts.source])[0] if opts.packet else build(opts.source)
        found = report(packet, opts.archive)
        print(
            json.dumps(found, allow_nan=False, sort_keys=True, separators=(",", ":"))
            if opts.json
            else text(found)
        )
        complete = found[
            "all_expected_sealed" if opts.require_sealed else "coverage_complete"
        ]
        return 0 if complete else 1
    except Malformed as error:
        print(f"Handoff input rejected: {error}.", file=sys.stderr)
        return 2
    except OSError:
        print("Cannot read or write local handoff data.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
