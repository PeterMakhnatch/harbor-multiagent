import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn, override

from .job_stream import JOURNAL
from .native_account import _root, retained
from .seat_stream import Malformed, Record
from .team_timing_math import DURATIONS, measure
from .team_timing_source import windows

SCHEMA = "seatlab.workbench.team-timing/v1"
CAVEAT = (
    "Times describe retained final seat windows, including waiting and harness work. "
    "They do not identify model activity, coordination, normal returns, bills or recoverable time. "
    "Each total sums within trials; it is not the elapsed time of the job."
)


def build(job: str, *, clock: str, under_test: bool, roles: bool) -> Record:
    root = _root(job)
    job_id, registered, retries, closed, _ = root
    coverage: dict[str, int] = {}
    totals = dict.fromkeys(DURATIONS, 0)
    by_role: dict[str, list[int]] = {}
    base = Path(job)
    for slot, document, trial, declared in retained(job, root):
        status, intervals = windows(
            document,
            trial,
            declared,
            base / slot / JOURNAL,
            clock=clock,
            under_test=under_test,
        )
        coverage[status] = coverage.get(status, 0) + 1
        if status != "measured":
            continue
        geometry = measure(intervals)
        for field in DURATIONS:
            totals[field] += geometry[field]
        if roles:
            for name, _, _ in intervals:
                if name not in by_role:
                    by_role[name] = [0, 0, 0]
                by_role[name][0] += 1
                if name == geometry["last_seat"]:
                    by_role[name][1] += 1
                    by_role[name][2] += geometry["last_alone_ns"]
    count = sum(coverage.values())
    measured = coverage.get("measured", 0)
    report: Record = {
        "schema": SCHEMA,
        "job": job_id,
        "registered": registered,
        "retained": count,
        "missing": registered - count,
        "retries": retries,
        "job_closed": closed,
        "clock": clock,
        "selection": "under_test" if under_test else "all",
        "coverage": coverage,
        "measured": measured,
        "totals": totals if measured else None,
        "caveat": CAVEAT,
    }
    if roles:
        report["roles"] = [
            {"seat": name, "windows": n, "last_unique": last, "last_alone_ns": tail}
            for name, (n, last, tail) in sorted(by_role.items())
        ]
    return report


def _code(r: Record) -> int:
    if any(reason in r["coverage"] for reason in ("invalid_clock", "journal_conflict")):
        return 1
    return 0 if r["job_closed"] is True and 0 < r["registered"] == r["measured"] else 3


def _seconds(ns: int) -> str:
    seconds, remainder = divmod(ns, 1_000_000_000)
    return (
        f"{seconds}.{remainder:09d}".rstrip("0").rstrip(".")
        if remainder
        else str(seconds)
    ) + " s"


def text(r: Record) -> str:
    lines = [
        f"Job {r['job']} | clock={r['clock']} seats={r['selection']}",
        f"Measured {r['measured']}/{r['registered']} enrolled final attempts; retained={r['retained']}, missing={r['missing']}.",
        "Coverage: "
        + (", ".join(f"{k}={v}" for k, v in sorted(r["coverage"].items())) or "none"),
    ]
    if r["totals"] is None:
        lines.append("No complete team windows. Timing totals are unknown.")
    else:
        t = r["totals"]
        lines += [
            f"Span: {_seconds(t['span_ns'])}; at least one seat: {_seconds(t['occupied_ns'])}; seat time summed: {_seconds(t['seat_ns'])}.",
            f"At least two seats: {_seconds(t['shared_ns'])}; all selected seats: {_seconds(t['all_ns'])}; last seat alone: {_seconds(t['last_alone_ns'])}.",
        ]
    for row in r.get("roles", []):
        lines.append(
            f"{row['seat']}: {row['windows']} windows, last alone {_seconds(row['last_alone_ns'])}, last unique {row['last_unique']}."
        )
    return "\n".join([*lines, CAVEAT])


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        print("timing: invalid arguments; use timing --help", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _Cli(
        prog="python3.12 -BS -m examples.seatlab.workbench timing",
        description="Measure team overlap from one saved, stationary native job. Does not start a trial.",
        allow_abbrev=False,
    )
    parser.add_argument("job", metavar="JOB")
    parser.add_argument(
        "--clock",
        choices=("result", "journal"),
        default="result",
        help="result: host wall time; journal: host monotonic seat events (no fallback)",
    )
    parser.add_argument(
        "--under-test",
        action="store_true",
        help="exclude supporting seats; at least two selected seats are required",
    )
    parser.add_argument(
        "--roles",
        action="store_true",
        help="include local seat labels and last-seat counts",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build(
            args.job, clock=args.clock, under_test=args.under_test, roles=args.roles
        )
    except Malformed as error:
        print(f"timing: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, sort_keys=True, allow_nan=False)
        if args.json
        else text(report)
    )
    return _code(report)


if __name__ == "__main__":
    raise SystemExit(main())
