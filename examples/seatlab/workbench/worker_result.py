from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import NoReturn, override

from .isolate import PIN
from .paired_jobs import COPIES, SIDES, _expected, _text, _uses
from .seat_stream import Record

COMMANDS = ("export", "check")
OPTIONS = {
    "export": ("--job-locks", "--side", "--starts"),
    "check": (
        "--job-locks",
        "--pair",
        "--copies",
        "--schedule",
        "--require-starts",
        "--json",
    ),
}
MAX_ARGUMENTS = {"export": 9, "check": 16}
MAX_ARGUMENT_BYTES = 4096
EXITS = {"covered": 0, "inconsistent": 1, "incomplete": 3}
TRUST = (
    "Sources: two unsigned worker claims. The coordinator did not inspect either job."
)
ARGUMENTS_ERROR = (
    "Invalid worker-result arguments. "
    "Use worker-result export --help or worker-result check --help."
)
PINS_ERROR = (
    "Use two distinct full lowercase sha256: job locks, left first. No worker result."
)
SHAPE_ERROR = (
    "Use two distinct full lowercase sha256: job locks, two distinct full lowercase sha256: "
    "trial-lock keys and 1 to {limit} copies. No paired report."
)
SCHEDULE_ERROR = (
    "Use --schedule with plain decimal concurrency 1 to {concurrency} "
    "and max retries 0 to {retries}. No paired report."
)
NATIVE_ERROR = (
    "Exporting a worker result needs the installed Harbor fork. Do not use -S."
)
CHECKER_ERROR = "Cannot load the worker-result checker. No paired report."
SIDE_ERROR = "Cannot verify the {side} job, its held job lock or its START archive. No worker result."
ENCODE_ERROR = "Cannot encode the {side} job as a worker result. No worker result."
READ_ERROR = (
    "Cannot read the {side} worker result as one JSON object of at most {limit} bytes. "
    "No paired report."
)
SCOPE_ERROR = (
    "The {side} worker result was made for other held job locks or for the other side. "
    "No paired report."
)
STARTS_ERROR = (
    "Both worker results must carry host START witnesses or neither may, "
    "and --require-starts needs both. No paired report."
)
MALFORMED_ERROR = (
    "The {side} worker result is not a well-formed worker result. No paired report."
)
CHECK_ERROR = "Cannot compare these two worker results. No paired report."


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, ARGUMENTS_ERROR + "\n")


def _job_locks(parser: Parser) -> None:
    parser.add_argument(
        "--job-locks",
        nargs=2,
        required=True,
        metavar=("LEFT_PIN", "RIGHT_PIN"),
        help="Both held job-lock digests, left first. Both workers and the coordinator use the same pair.",
    )


def _export_parser() -> Parser:
    parser = Parser(
        prog="workbench worker-result export",
        allow_abbrev=False,
        description=(
            "Read one retained native job that matches its held job lock and print one worker result. "
            "Job, trial, slot and view names become tags scoped to the held job-lock pair. "
            "Nothing is written and no job starts."
        ),
    )
    parser.add_argument(
        "job", type=Path, metavar="JOB", help="Retained native job directory."
    )
    _job_locks(parser)
    parser.add_argument(
        "--side",
        required=True,
        choices=SIDES,
        help="Which held job lock this job must match.",
    )
    parser.add_argument(
        "--starts",
        type=Path,
        metavar="ARCHIVE",
        help="Write-once host START archive for this job.",
    )
    return parser


def _check_parser() -> Parser:
    parser = Parser(
        prog="workbench worker-result check",
        allow_abbrev=False,
        description=(
            "Compare two worker results with the held job locks, trial-lock keys, copies and schedule. "
            "This reads only the two result files, never a job, so the report is only as true as the "
            "workers. Nothing is written."
        ),
    )
    parser.add_argument(
        "left_file", type=Path, metavar="LEFT_FILE", help="Left worker result."
    )
    parser.add_argument(
        "right_file", type=Path, metavar="RIGHT_FILE", help="Right worker result."
    )
    _job_locks(parser)
    parser.add_argument(
        "--pair",
        nargs=2,
        required=True,
        metavar=("LEFT_KEY", "RIGHT_KEY"),
        help="The one full trial-lock key each job must register.",
    )
    parser.add_argument(
        "--copies",
        required=True,
        metavar="N",
        help="Planned copies of its key in each job.",
    )
    parser.add_argument(
        "--schedule",
        nargs=2,
        required=True,
        metavar=("CONCURRENCY", "MAX_RETRIES"),
        help="The concurrency and max retries each job lock must claim.",
    )
    parser.add_argument(
        "--require-starts",
        action="store_true",
        help="Refuse unless both worker results carry host START witnesses.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _bounded(command: str, args: list[str]) -> bool:
    if len(args) > MAX_ARGUMENTS[command]:
        return False
    try:
        sizes = [len(os.fsencode(arg)) for arg in args]
    except (TypeError, UnicodeError):
        return False
    if any(size == 0 or size > MAX_ARGUMENT_BYTES for size in sizes):
        return False
    return all(_uses(args, option) <= 1 for option in OPTIONS[command])


def _parse(parser: Parser, args: list[str]) -> argparse.Namespace | int:
    try:
        return parser.parse_args(args)
    except SystemExit as stop:
        return stop.code if type(stop.code) is int else 2


def _export(opts: argparse.Namespace) -> int:
    try:
        from . import return_encode
        from .input_evidence import read_job
        from .input_witness import read_starts
    except ImportError:
        print(NATIVE_ERROR, file=sys.stderr)
        return 2
    pins = (opts.job_locks[0], opts.job_locks[1])
    try:
        return_encode.scope(pins)
    except (ValueError, TypeError):
        print(PINS_ERROR, file=sys.stderr)
        return 2
    try:
        evidence = read_job(
            opts.job,
            pins[SIDES.index(opts.side)],
            bind_starts=opts.starts is not None,
        )
        starts = None if opts.starts is None else read_starts(opts.starts, evidence.job)
    except ImportError:
        print(NATIVE_ERROR, file=sys.stderr)
        return 2
    except (ValueError, TypeError, OSError, RecursionError):
        print(SIDE_ERROR.format(side=opts.side), file=sys.stderr)
        return 2
    try:
        data = return_encode.dump(return_encode.project(evidence, starts, pins))
    except (ValueError, TypeError, RecursionError):
        print(ENCODE_ERROR.format(side=opts.side), file=sys.stderr)
        return 2
    if len(data) >= return_encode.MAX_BYTES:
        print(ENCODE_ERROR.format(side=opts.side), file=sys.stderr)
        return 2
    print(data.decode("ascii"))
    return 0


def _refusal(decoder: ModuleType, reason: str) -> str:
    if reason in (decoder.STARTS_UNEVEN, decoder.STARTS_REQUIRED):
        return STARTS_ERROR
    for side in SIDES:
        if reason in (
            decoder.WRONG_SCOPE.format(side),
            decoder.WRONG_LOCK.format(side, side),
        ):
            return SCOPE_ERROR.format(side=side)
        if reason.startswith(f"{side} worker result "):
            return MALFORMED_ERROR.format(side=side)
    return CHECK_ERROR


def _held(
    opts: argparse.Namespace, copy_limit: int
) -> tuple[tuple[str, str], tuple[str, str], int] | None:
    pins = (opts.job_locks[0], opts.job_locks[1])
    keys = (opts.pair[0], opts.pair[1])
    if pins[0] == pins[1] or keys[0] == keys[1]:
        return None
    if not all(PIN.fullmatch(value) for value in (*pins, *keys)):
        return None
    if COPIES.fullmatch(opts.copies) is None:
        return None
    copies = int(opts.copies)
    return (pins, keys, copies) if 1 <= copies <= copy_limit else None


def _text_report(result: Record) -> str:
    return "\n".join((TRUST, _text(result["comparison"]), result["caveat"]))


def _check(opts: argparse.Namespace) -> int:
    try:
        from ...seat_handoffs.native_records import read_record
        from . import return_decode, return_encode
        from .input_evidence import MAX_TRIALS
        from .worker_handoff import MAX_CONCURRENCY, MAX_RETRIES
    except ImportError:
        print(CHECKER_ERROR, file=sys.stderr)
        return 2
    held = _held(opts, MAX_TRIALS)
    if held is None:
        print(SHAPE_ERROR.format(limit=MAX_TRIALS), file=sys.stderr)
        return 2
    pins, keys, copies = held
    schedule = _expected(opts.schedule, MAX_CONCURRENCY, MAX_RETRIES)
    if schedule is None:
        print(
            SCHEDULE_ERROR.format(concurrency=MAX_CONCURRENCY, retries=MAX_RETRIES),
            file=sys.stderr,
        )
        return 2
    raws = []
    for side, path in zip(SIDES, (opts.left_file, opts.right_file), strict=True):
        try:
            raw = read_record(path, return_encode.MAX_BYTES)
        except (ValueError, OSError, RecursionError):
            raw = None
        if raw is None:
            print(
                READ_ERROR.format(side=side, limit=return_encode.MAX_BYTES),
                file=sys.stderr,
            )
            return 2
        raws.append(raw)
    try:
        result = return_decode.check(
            raws[0],
            raws[1],
            pins=pins,
            keys=keys,
            copies=copies,
            schedule=schedule,
            require_starts=opts.require_starts,
        )
    except ImportError:
        print(CHECKER_ERROR, file=sys.stderr)
        return 2
    except (ValueError, TypeError, RecursionError) as error:
        print(_refusal(return_decode, str(error)), file=sys.stderr)
        return 2
    status = result["comparison"]["status"]
    assert status in EXITS
    print(
        json.dumps(result, sort_keys=True, separators=(",", ":"))
        if opts.json
        else _text_report(result)
    )
    return EXITS[status]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args in (["-h"], ["--help"]):
        print("worker-result export: read one native job and emit its scoped receipt.")
        print(
            "worker-result check: compare two receipts against the held job locks and plan."
        )
        print(
            "Use export --help or check --help for arguments. Neither command launches a team."
        )
        return 0
    if not args or args[0] not in COMMANDS or not _bounded(args[0], args[1:]):
        print(ARGUMENTS_ERROR, file=sys.stderr)
        return 2
    command = args[0]
    parser = _export_parser() if command == "export" else _check_parser()
    opts = _parse(parser, args[1:])
    if isinstance(opts, int):
        return opts
    if command == "export":
        return _export(opts)
    return _check(opts)


if __name__ == "__main__":
    raise SystemExit(main())
