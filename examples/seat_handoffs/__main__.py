import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import bespoke, calendar, core, job, schedule_changes, schedule_jobs
from .schedule_exit_clock import MAX_DRIFT_MS, ExitPolicy
from .trial import read_trial

EXIT_CLOCK_DIGITS = len(str(MAX_DRIFT_MS))


def _exit_clock_ms(text: str) -> int:
    if (
        not text.isascii()
        or not text.isdigit()
        or not 1 <= len(text) <= EXIT_CLOCK_DIGITS
        or int(text) > MAX_DRIFT_MS
    ):
        raise argparse.ArgumentTypeError(
            f"expected whole milliseconds from 0 to {MAX_DRIFT_MS}"
        )
    value = int(text)
    assert 0 <= value <= MAX_DRIFT_MS
    return value


def _job_report(path: Path, boundary: str, as_json: bool) -> int:
    try:
        snapshot = job.inspect_job(path, boundary=boundary)
    except (OSError, ValueError) as exc:
        print(f"Cannot inspect job: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(snapshot, allow_nan=False, separators=(",", ":")))
    else:
        print(f"Job: {snapshot['job_id']}   Receipt boundary: {boundary}")
        print(
            f"Registered slots: {snapshot['registered_slots']}   "
            f"Matched trials: {snapshot['matched_trials']}   "
            f"Missing slots: {snapshot['missing_slots']}"
        )
        print(
            f"Valid native grades: {snapshot['valid_native_grades']}   "
            f"Usable trials: {snapshot['usable_trials']}   "
            f"Unassigned directories: {len(snapshot['unassigned'])}"
        )
        for record in snapshot["trials"]:
            print(f"{json.dumps(record['directory'])}\t{record['status']}")
    return 0


def _checkpoint_lines(section: dict[str, Any]) -> None:
    print(
        f"Checkpoint teams (two seats): {section['teams']}   "
        f"Tasks: {section['tasks']}   Not two seats: {section['not_two_seats']}"
    )
    for name, cohort in section["cohorts"].items():
        print(
            f"{name}: eligible {cohort['eligible']}   "
            f"native successes {cohort['native_final_successes']}   "
            f"graded timeouts {cohort['graded_timeouts']}"
        )
        for rule, found in cohort["rules"].items():
            early = found["early_stratum"]
            print(
                f"  {rule}: reached {found['reached']}   early {found['early']}   "
                f"final only {found['final_only']}   abstain {found['abstain']}   "
                f"later writes {found['subsequent_writes']}"
            )
            print(
                f"    lost {found['worsened'][0]} to {found['worsened'][1]}   "
                f"gained {found['improved'][0]} to {found['improved'][1]}   "
                f"net {found['net'][0]} to {found['net'][1]}   "
                f"early completed {early['completed']}   verdict {found['verdict']}"
            )


def _seat_exit_lines(section: dict[str, Any], clock_ms: int | None) -> None:
    assert clock_ms is None or 0 <= clock_ms <= MAX_DRIFT_MS
    if clock_ms is None:
        print(
            "Seat exit freeze, unanchored: native finish times are not aligned "
            "with world elapsed time; after a trigger every ordered answer prefix, "
            "including none, stays feasible."
        )
    else:
        print(
            "Seat exit freeze, conditional on operator attestation: a fresh native "
            f"trial world and elapsed clock discrepancy of at most {clock_ms} ms."
        )
    print(
        f"Seat exit teams (two seats): {section['teams']}   "
        f"Not two seats: {section['not_two_seats']}"
    )
    cohort = section["cohorts"]["all"]
    lost, gained, net = cohort["lost"], cohort["gained"], cohort["net"]
    assert len(lost) == len(gained) == len(net) == 2
    print(
        f"All two-seat teams: lost {lost[0]} to {lost[1]}   "
        f"gained {gained[0]} to {gained[1]}   "
        f"net {net[0]} to {net[1]}   verdict {cohort['verdict']}"
    )
    print("Other strata appear only in --json output.")
    print(
        "Bounds cover recorded native grades only. A clean seat record does not "
        "show that the agent stopped, and no speed or savings claim follows."
    )


def _schedule_report(
    paths: list[Path],
    *,
    as_json: bool,
    jobs: bool,
    include_timeouts: bool,
    checkpoints: bool,
    seat_exit: bool,
    clock_ms: int | None,
) -> int:
    assert seat_exit or clock_ms is None
    policy = None
    if seat_exit:
        policy = ExitPolicy() if clock_ms is None else ExitPolicy(drift_ms=clock_ms)
    try:
        if jobs:
            report = schedule_jobs.inspect_jobs(
                paths,
                include_timeouts=include_timeouts,
                checkpoints=checkpoints,
                seat_exit=policy,
            )
            behavior = report["behavior"]
        else:
            report = schedule_changes.inspect_changes(
                paths,
                include_timeouts=include_timeouts,
                checkpoints=checkpoints,
                seat_exit=policy,
            )
            behavior = report
    except ValueError as exc:
        print(f"Cannot inspect schedules: {exc}", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(report, allow_nan=False, separators=(",", ":")))
        return 0
    if jobs:
        print(
            f"Scheduled: {report['registered_slots']}   Matched: {report['matched_slots']}   "
            f"Missing: {report['missing_slots']}   Ambiguous: {report['ambiguous_slots']}"
        )
    summary = behavior["summary"]
    print(
        f"Trial directories: {behavior['inputs']}   Valid grades: {summary['valid_native_grades']}   "
        f"Graded timeouts: {summary['graded_timeouts']}   Usable: {behavior['usable_trials']}"
    )
    print(f"Cross-seat writes after another write: {summary['cross_seat_transitions']}")
    for metric, label in (
        ("cross_seat_improved", "Repaired a wrong answer"),
        ("cross_seat_worsened", "Replaced a correct answer"),
        ("final_cross_seat_worsened", "Replaced a correct answer on the final write"),
    ):
        lower, upper = summary["bounds"][metric]
        print(f"{label}: {lower} to {upper}")
    if jobs:
        for row in report["jobs"]:
            print(
                f"{row['job_id']}\t{row['usable_trials']} usable / {row['registered_slots']} scheduled"
            )
    elif not checkpoints:
        for row in behavior["trials"]:
            print(f"{row['slot']}\t{row['native_trial'] or '-'}\t{row['status']}")
    if checkpoints:
        _checkpoint_lines(behavior["checkpoints"])
    if seat_exit:
        _seat_exit_lines(behavior["seat_exit"], clock_ms)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect team handoffs independently of team grades."
    )
    parser.add_argument(
        "source",
        choices=("events", "job", "bespoke", "calendar", "schedule", "schedule-jobs"),
    )
    parser.add_argument("paths", type=Path, nargs="+", metavar="PATH")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit findings, native trial links, and counts",
    )
    parser.add_argument(
        "--boundary",
        choices=core.BOUNDARIES,
        help="for event logs or native jobs: inspect prepared replies or receiver acknowledgements",
    )
    parser.add_argument(
        "--include-timeouts",
        action="store_true",
        help="for schedules: include verified grades from teams with an agent timeout",
    )
    parser.add_argument(
        "--checkpoints",
        action="store_true",
        help="for schedules: measure two-seat cutoff checkpoints against shared keys",
    )
    parser.add_argument(
        "--seat-exit",
        action="store_true",
        help=(
            "for schedules: bound native successes lost or gained if the board froze "
            "when the first clean seat returned; no clock alignment unless "
            "--exit-clock-ms is given"
        ),
    )
    parser.add_argument(
        "--exit-clock-ms",
        type=_exit_clock_ms,
        metavar="N",
        help=(
            "with --seat-exit: attest a fresh native trial world and an elapsed clock "
            f"discrepancy of at most N ms (0 to {MAX_DRIFT_MS})"
        ),
    )
    options = parser.parse_args()
    if options.boundary and options.source not in ("events", "job"):
        parser.error(
            "--boundary applies only to events and jobs; other sources use their native evidence"
        )
    if options.checkpoints and options.source not in ("schedule", "schedule-jobs"):
        parser.error("--checkpoints applies only to schedule and schedule-jobs")
    if options.seat_exit and options.source not in ("schedule", "schedule-jobs"):
        parser.error("--seat-exit applies only to schedule and schedule-jobs")
    if options.exit_clock_ms is not None and not options.seat_exit:
        parser.error("--exit-clock-ms applies only with --seat-exit")
    boundary = options.boundary or "returned"
    if options.source in ("schedule", "schedule-jobs"):
        return _schedule_report(
            options.paths,
            as_json=options.json,
            jobs=options.source == "schedule-jobs",
            include_timeouts=options.include_timeouts,
            checkpoints=options.checkpoints,
            seat_exit=options.seat_exit,
            clock_ms=options.exit_clock_ms,
        )
    if options.include_timeouts:
        parser.error("--include-timeouts applies only to schedule and schedule-jobs")
    if options.source == "job":
        if len(options.paths) != 1:
            parser.error("job needs one native job directory")
        return _job_report(options.paths[0], boundary, options.json)
    adapters = {"bespoke": bespoke.inspect, "calendar": calendar.inspect}
    rows: list[core.Finding] = []
    links = []
    try:
        for path in options.paths:
            if options.source == "events" and path.is_dir():
                found, link = read_trial(path, boundary=boundary)
                links.append(link)
            elif options.source == "events":
                found = core.inspect(path, boundary=boundary)
            else:
                found = adapters[options.source](path)
            rows.extend(found)
        ids = [
            link["native_trial"] for link in links if link["native_trial"] is not None
        ]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate native trial identifier")
        counts = core.summarize(rows)
    except (OSError, ValueError) as exc:
        print(f"Cannot inspect handoffs: {exc}", file=sys.stderr)
        return 2
    report = {
        "schema": "harbor.handoff-inspection/v1",
        "source": options.source,
        "boundary": boundary if options.source == "events" else "model_request",
        "findings": [asdict(row) for row in rows],
        "native_trials": links,
        "summary": counts,
    }
    if options.json:
        print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    else:
        print("Receipt boundary:", report["boundary"])
        print(
            "trial\tmessage\tsender\treceiver\treceipt\ttiming\tcommit\treward\tbasis"
        )
        for row in rows:
            print(
                "\t".join(
                    "unknown" if value is None else str(value)
                    for value in asdict(row).values()
                )
            )
        if links:
            print("Native trial links:", json.dumps(links, sort_keys=True))
        print("Summary:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
