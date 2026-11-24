import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .work_book import capture, load, save
from .work_meter import GUARDS

SCHEMA = "harbor.post-return-work.report/v1"
FATES = ("forced_loss", "forced_gain", "identified_unchanged", "grade_unresolved")
GROUPS = (
    "all",
    "completed",
    "graded_timeout",
    "talk",
    "silent",
    "observed_return",
    "absent_return",
    "unknown_return",
    *FATES,
    "certain_later_write",
    "no_later_write",
    "possible_later_write",
)
STAGES = ("before", "crossing", "after")
FIELDS = ("steps", "prompt", "completion")


def categories(row: dict[str, Any], board: dict[str, Any]) -> tuple[str, ...]:
    trigger = row["trigger"]
    groups = (
        "all",
        "graded_timeout" if row["timed_out"] else "completed",
        row["condition"],
        f"{trigger}_return",
    )
    if trigger != "observed":
        return groups
    lo, hi = board["grade"]
    if lo != hi:
        fate = "grade_unresolved"
    elif lo == row["final"]:
        fate = "identified_unchanged"
    else:
        fate = "forced_loss" if row["final"] else "forced_gain"
    least, most = board["later_writes"]
    later = (
        "certain_later_write"
        if least > 0
        else ("no_later_write" if most == 0 else "possible_later_write")
    )
    return (*groups, fate, later)


def amount(work: dict[str, Any], guard: int) -> dict[str, Any]:
    radius = GUARDS.index(guard)
    native = work["native"]
    bins = work["bins"]
    assert len(bins) == 7 and len(native) == 2
    split = (bins[: 3 - radius], bins[3 - radius : 4 + radius], bins[4 + radius :])
    ans: dict[str, Any] = {}
    for axis, name in enumerate(FIELDS):
        counts = {
            stage: sum(bin_[axis] for bin_ in part)
            for stage, part in zip(STAGES, split, strict=True)
        }
        if axis:
            total = native[axis - 1]
            residual = total - sum(counts.values())
            assert residual >= 0
            counts.update(native=total, unplaced=residual)
        ans[name] = counts
    return ans


def _new() -> dict[str, Any]:
    return {
        "teams": 0,
        "native_successes": 0,
        "meter_statuses": Counter(),
        "positive_certain_prompt_and_completion": 0,
        "measured_teams": 0,
        "work_on_measured_teams": None,
    }


def _add(
    group: dict[str, Any], row: dict[str, Any], counts: dict[str, Any] | None
) -> None:
    group["teams"] += 1
    group["native_successes"] += row["final"]
    group["meter_statuses"][row["meter"]] += 1
    if counts is None:
        return
    group["measured_teams"] += 1
    group["positive_certain_prompt_and_completion"] += int(
        counts["prompt"]["after"] > 0 and counts["completion"]["after"] > 0
    )
    work = group["work_on_measured_teams"]
    if work is None:
        work = {name: Counter() for name in FIELDS}
        group["work_on_measured_teams"] = work
    for name in FIELDS:
        work[name].update(counts[name])


def _finish(group: dict[str, Any]) -> None:
    work = group["work_on_measured_teams"]
    assert group["measured_teams"] == group["meter_statuses"]["measured"]
    assert sum(group["meter_statuses"].values()) == group["teams"]
    if work is None:
        return
    for name in ("prompt", "completion"):
        count = work[name]
        assert sum(count[s] for s in STAGES) + count["unplaced"] == count["native"]
        work[name] = {
            **count,
            "bounds": [count["after"], count["native"] - count["before"]],
        }


def summarize(
    book: dict[str, Any],
    *,
    guard: int,
    board_ms: int | None,
    jobs: frozenset[int] | None = None,
) -> dict[str, Any]:
    if type(guard) is not int or guard not in GUARDS:
        raise ValueError("unknown host guard")
    if board_ms is not None and (type(board_ms) is not int or board_ms != 1000):
        raise ValueError("unknown board-clock assumption")
    allowed = {job["job"] for job in book["jobs"]}
    if jobs is not None and (not jobs or not jobs <= allowed):
        raise ValueError("job filter must name registered job indices")
    groups = {g: _new() for g in GROUPS}
    statuses: Counter[str] = Counter()
    slots = missing = ambiguous = 0
    for job in book["jobs"]:
        if jobs is not None and job["job"] not in jobs:
            continue
        slots += job["slots"]
        missing += job["missing_slots"]
        ambiguous += job["ambiguous_slots"]
        statuses.update(job["statuses"])
        for row in job["rows"]:
            board = row["boards"][int(board_ms is not None)]
            work = amount(row["work"], guard) if row["work"] is not None else None
            for group in categories(row, board):
                _add(groups[group], row, work)
    for value in groups.values():
        _finish(value)
    total = groups["all"]["teams"]
    assert total == groups["completed"]["teams"] + groups["graded_timeout"]["teams"]
    assert total == groups["talk"]["teams"] + groups["silent"]["teams"]
    assert groups["observed_return"]["teams"] == sum(groups[g]["teams"] for g in FATES)
    assert total == sum(
        groups[k]["teams"]
        for k in ("observed_return", "absent_return", "unknown_return")
    )
    assert (
        slots == missing + ambiguous + sum(statuses.values())
        and statuses["usable"] == total
    )
    return {
        "schema": SCHEMA,
        "include_timeouts": book["include_timeouts"],
        "host_guard_ms": guard,
        "board_clock_ms": board_ms,
        "selected_jobs": sorted(jobs or allowed),
        "all_registered_slots": sum(j["slots"] for j in book["jobs"]),
        "selected_slots": slots,
        "missing_slots": missing,
        "ambiguous_slots": ambiguous,
        "stock_statuses": dict(sorted(statuses.items())),
        "groups": groups,
    }


def _text(report: dict[str, Any]) -> list[str]:
    board = report["board_clock_ms"]
    label = (
        "unanchored"
        if board is None
        else f"fresh world, elapsed clock error <= {board} ms"
    )
    lines = [
        f"Host guard: {report['host_guard_ms']} ms. Board assumption: {label}.",
        f"Registered slots: {report['selected_slots']} selected of {report['all_registered_slots']}. Verified timeouts included: {report['include_timeouts']}.",
    ]
    for name in ("all", "forced_loss", "identified_unchanged", "no_later_write"):
        g = report["groups"][name]
        work = g["work_on_measured_teams"]
        label = (
            "no metered team"
            if work is None
            else "; ".join(
                f"{k} {work[k]['bounds'][0]}..{work[k]['bounds'][1]}"
                for k in ("prompt", "completion")
            )
        )
        lines.append(
            f"{name}: {g['measured_teams']}/{g['teams']} metered; {g['positive_certain_prompt_and_completion']} with certain prompt and completion work; {label}"
        )
    lines.append(
        "Bounds cover native-recorded tokens of metered peers only. They do not measure provider usage or savings from stopping."
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect recorded peer work after a clean native seat return."
    )
    modes = parser.add_subparsers(dest="command", required=True)
    owner = modes.add_parser(
        "capture", help="read private native jobs and create a fresh redacted release"
    )
    owner.add_argument("out", type=Path)
    owner.add_argument("jobs", type=Path, nargs="+")
    owner.add_argument("--include-timeouts", action="store_true")
    reader = modes.add_parser(
        "show", help="read a redacted release without native trajectories"
    )
    reader.add_argument("file", type=Path)
    reader.add_argument("--guard-ms", type=int, choices=GUARDS, default=0)
    reader.add_argument("--board-clock-ms", type=int, choices=(1000,))
    reader.add_argument("--job", type=int, action="append")
    reader.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            book = capture(args.jobs, include_timeouts=args.include_timeouts)
            save(args.out, book)
            print(
                f"Wrote {len(book['jobs'])} native job summaries. No trajectory text was released."
            )
            return 0
        jobs = frozenset(args.job) if args.job is not None else None
        report = summarize(
            load(args.file),
            guard=args.guard_ms,
            board_ms=args.board_clock_ms,
            jobs=jobs,
        )
    except (ValueError, OSError) as error:
        print(
            f"Cannot process recorded work: {error.strerror if isinstance(error, OSError) else error}",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(report, allow_nan=False, separators=(",", ":"))
        if args.json
        else "\n".join(_text(report))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
