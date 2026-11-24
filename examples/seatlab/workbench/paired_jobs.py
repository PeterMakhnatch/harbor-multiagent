from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

from .isolate import PIN
from .seat_stream import Record

if TYPE_CHECKING:
    from .input_evidence import Evidence
    from .input_witness import Witness

SIDES = ("left", "right")
OPTIONS = (
    "--pair",
    "--copies",
    "--job-locks",
    "--plans",
    "--starts",
    "--schedule",
    "--json",
)
MAX_ARGUMENTS = 17
MAX_ARGUMENT_BYTES = 4096
COPIES = re.compile(r"[1-9][0-9]{0,5}")
CONCURRENCY = re.compile(r"[1-9][0-9]{0,3}")
RETRIES = re.compile(r"0|[1-9][0-9]?")
EXITS = {"covered": 0, "inconsistent": 1, "incomplete": 3}
ARGUMENTS_ERROR = "Invalid cohort-jobs arguments. Use cohort-jobs --help."
SHAPE_ERROR = (
    "Use distinct full lowercase sha256: trial-lock keys, full lowercase sha256: job locks if required, "
    "and 1 to {limit} copies."
)
SCHEDULE_ERROR = (
    "Use --schedule with plain decimal concurrency 1 to {concurrency} and max retries 0 to {retries}, "
    "only with --job-locks."
)
NATIVE_ERROR = "Paired input coverage needs the installed Harbor fork. Do not use -S."
SIDE_ERROR = "Cannot verify the {side} job, its enrollment pin, its START archive or its retained files. No paired report."
COMBINE_ERROR = "Cannot compare these two jobs. No paired report."


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, ARGUMENTS_ERROR + "\n")


def _parser() -> Parser:
    parser = Parser(
        prog="workbench cohort-jobs",
        allow_abbrev=False,
        description=(
            "Before launch, project two local native job configs. Afterward, check the retained jobs "
            "against the same two trial-lock keys and, with --schedule, the launch schedule both native "
            "job locks claim. No job starts and nothing is written."
        ),
    )
    parser.add_argument(
        "left_job",
        type=Path,
        metavar="LEFT",
        help="Left config file or retained job directory.",
    )
    parser.add_argument(
        "right_job",
        type=Path,
        metavar="RIGHT",
        help="Right config file or retained job directory.",
    )
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plans",
        action="store_true",
        help="Project two local job configs before any worker registers.",
    )
    mode.add_argument(
        "--job-locks",
        nargs=2,
        metavar=("LEFT_PIN", "RIGHT_PIN"),
        help="Raw enrollment lock digests checked before final trial directories are read.",
    )
    parser.add_argument(
        "--starts",
        nargs=2,
        type=Path,
        metavar=("LEFT_STARTS", "RIGHT_STARTS"),
        help="Write-once host START archives, one per job; required for both or neither.",
    )
    parser.add_argument(
        "--schedule",
        nargs=2,
        metavar=("CONCURRENCY", "MAX_RETRIES"),
        help=(
            "Retained jobs only: the concurrency and max retries each job lock must claim. "
            "Any drift withholds both arms."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _uses(args: list[str], option: str) -> int:
    return sum(arg == option or arg.startswith(option + "=") for arg in args)


def _bounded(args: list[str]) -> bool:
    if len(args) > MAX_ARGUMENTS:
        return False
    try:
        sizes = [len(os.fsencode(arg)) for arg in args]
    except (TypeError, UnicodeError):
        return False
    if any(size == 0 or size > MAX_ARGUMENT_BYTES for size in sizes):
        return False
    return all(_uses(args, option) <= 1 for option in OPTIONS)


def _shaped(opts: argparse.Namespace, limit: int) -> int | None:
    keys, pins = tuple(opts.pair), tuple(opts.job_locks or ())
    if keys[0] == keys[1] or not all(PIN.fullmatch(value) for value in (*keys, *pins)):
        return None
    if COPIES.fullmatch(opts.copies) is None:
        return None
    copies = int(opts.copies)
    return copies if 1 <= copies <= limit else None


def _expected(
    values: list[str], concurrency: int, retries: int
) -> tuple[int, int] | None:
    if CONCURRENCY.fullmatch(values[0]) is None or RETRIES.fullmatch(values[1]) is None:
        return None
    expected = (int(values[0]), int(values[1]))
    if expected[0] > concurrency or expected[1] > retries:
        return None
    return expected


def _side(
    index: int, opts: argparse.Namespace
) -> tuple[Evidence, dict[str, Witness] | None]:
    from .input_evidence import read_job
    from .input_witness import read_starts

    jobs = (opts.left_job, opts.right_job)
    evidence = read_job(
        jobs[index], opts.job_locks[index], bind_starts=opts.starts is not None
    )
    if opts.starts is None:
        return evidence, None
    return evidence, read_starts(opts.starts[index], evidence.job)


def _arm(side: str, arm: Record) -> str:
    grades = arm["grades"]
    tally = (
        "grades withheld"
        if grades is None
        else ", ".join(f"{grade}={count}" for grade, count in grades.items())
    )
    attributed = "withheld" if arm["attributed"] is None else arm["attributed"]
    return (
        f"{side.capitalize()}: input {arm['input']} in job {arm['job']} with lock {arm['job_lock']}; "
        f"{arm['registered']} registered, {arm['retained']} retained, {attributed} attributed; {tally}."
    )


def _profile(registered: Record | None) -> str:
    if registered is None:
        return "unrecorded"
    return f"concurrency {registered['concurrency']}, max retries {registered['max_retries']}"


def _registration(registration: Record) -> list[str]:
    fields = registration["fields"]
    agreement = ", ".join(f"{name} {fields[name]}" for name in sorted(fields))
    registered = registration["registered"]
    expected = registration["expected"]
    wanted = "not requested" if expected is None else _profile(expected)
    return [
        f"Registration from native job-lock claims: {registration['pair']}; {agreement}.",
        f"Schedule {registration['schedule'].replace('_', ' ')}: expected {wanted}; "
        + "; ".join(f"{side} {_profile(registered[side])}" for side in SIDES)
        + ".",
    ]


def _text(report: Record) -> str:
    reasons = report["reasons"]
    lines = [
        f"{report['status']}: {report['copies_per_arm']} planned copies per arm in two separate jobs.",
        f"Reasons: {', '.join(reasons) if reasons else 'none'}.",
        *_registration(report["registration"]),
        *(_arm(side, report["arms"][side]) for side in SIDES),
        report["caveat"],
    ]
    return "\n".join(lines)


def _emit(report: Record, as_json: bool, text: str) -> None:
    print(
        json.dumps(report, sort_keys=True, separators=(",", ":")) if as_json else text
    )


def _plans(opts: argparse.Namespace) -> int:
    from .job_projection import MAX_COPIES, compare, project

    copies = _shaped(opts, MAX_COPIES)
    if copies is None or opts.starts is not None or opts.schedule is not None:
        print(ARGUMENTS_ERROR, file=sys.stderr)
        return 2
    plans = []
    for side, path in zip(SIDES, (opts.left_job, opts.right_job), strict=True):
        try:
            plans.append(project(path))
        except (ValueError, OSError, RecursionError):
            print(
                f"Cannot verify the {side} local job config. No projected plan.",
                file=sys.stderr,
            )
            return 2
    report = compare(plans[0], plans[1], (opts.pair[0], opts.pair[1]), copies)
    lines = [f"{report['status']}: {copies} expected copies per job."]
    for side, plan in zip(SIDES, plans, strict=True):
        lines.append(
            f"{side.capitalize()}: {plan.copies} copies of {plan.input}; "
            f"concurrency {plan.concurrency}, max retries {plan.max_retries}."
        )
    lines.extend([*report["reasons"], report["caveat"]])
    _emit(report, opts.json, "\n".join(lines))
    return 0 if report["status"] == "matches_plan" else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not _bounded(args):
        print(ARGUMENTS_ERROR, file=sys.stderr)
        return 2
    opts = _parser().parse_args(args)
    if opts.starts is not None and any(
        str(path).startswith("-") for path in opts.starts
    ):
        print(ARGUMENTS_ERROR, file=sys.stderr)
        return 2
    try:
        if opts.plans:
            return _plans(opts)
        from .input_evidence import MAX_TRIALS
        from .job_projection import MAX_CONCURRENCY, MAX_RETRIES
        from .paired_cohort import combine
    except ImportError:
        print(NATIVE_ERROR, file=sys.stderr)
        return 2
    copies = _shaped(opts, MAX_TRIALS)
    if copies is None:
        print(SHAPE_ERROR.format(limit=MAX_TRIALS), file=sys.stderr)
        return 2
    schedule = None
    if opts.schedule is not None:
        schedule = _expected(opts.schedule, MAX_CONCURRENCY, MAX_RETRIES)
        if schedule is None:
            print(
                SCHEDULE_ERROR.format(concurrency=MAX_CONCURRENCY, retries=MAX_RETRIES),
                file=sys.stderr,
            )
            return 2
    sides = []
    for index, side in enumerate(SIDES):
        try:
            sides.append(_side(index, opts))
        except ImportError:
            print(NATIVE_ERROR, file=sys.stderr)
            return 2
        except (ValueError, OSError, RecursionError):
            print(SIDE_ERROR.format(side=side), file=sys.stderr)
            return 2
    (left, left_starts), (right, right_starts) = sides
    starts = None
    if opts.starts is not None:
        assert left_starts is not None and right_starts is not None
        starts = (left_starts, right_starts)
    try:
        report = combine(
            (left, right),
            (opts.pair[0], opts.pair[1]),
            copies,
            starts=starts,
            schedule=schedule,
        )
    except ImportError:
        print(NATIVE_ERROR, file=sys.stderr)
        return 2
    except (ValueError, RecursionError):
        print(COMBINE_ERROR, file=sys.stderr)
        return 2
    status = report["status"]
    assert status in EXITS
    _emit(report, opts.json, _text(report))
    return EXITS[status]


if __name__ == "__main__":
    raise SystemExit(main())
