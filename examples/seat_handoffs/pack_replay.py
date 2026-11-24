import argparse
import json
import sys
from collections import Counter, defaultdict
from itertools import islice
from pathlib import Path
from typing import Any

from .pack_compare import same_task_cases
from .pack_io import (
    DRIFTS,
    EVIDENCE,
    MAX_SHARD_FILES,
    STATES,
    UNSCORED,
    Job,
    Pack,
    json_record,
    read_pack,
)
from .schedule_bounds import WINDOWS, Window
from .schedule_changes import MAX_INPUTS
from .schedule_changes import STATUSES as STOCK_STATUSES
from .schedule_exit_bounds import (
    COHORTS,
    FRESH_REASON,
    OBSERVATION_MODELS,
    PRIMARY_COHORT,
    TARGETS,
    UNANCHORED_REASON,
    ExitCase,
    case_row,
    check_cut,
    exit_case,
    reduce_cohorts,
)
from .schedule_exit_clock import STOCK_PAIR, ExitCut
from .schedule_jobs import SCHEMA as STOCK_JOBS_SCHEMA
from .schedule_native import CONDITIONS

SCHEMA = "harbor.schedule-exit-pack.replay/v1"
GRID_SCHEMA = "harbor.schedule-exit-pack.replay-grid/v1"
AUDIT_SCHEMA = "harbor.schedule-exit-pack.stock-audit/v1"
PRESENT = "present"
MEASURED = "usable"
EXCLUDED_TIMEOUT = "seat_timeout"
QUARANTINED = "conflicting_reference"
SHARD_STATES = tuple(state for state in STATES if state != PRESENT)
STATUSES = (MEASURED, EXCLUDED_TIMEOUT, QUARANTINED, *UNSCORED, *SHARD_STATES)
UNKNOWN_GRADE = (*UNSCORED, *SHARD_STATES)
POPULATIONS = ("completed", "completed_and_graded_timeouts")
HEADLINE = ("completed", "all")
REFERENCE_SOURCE = "all_present_eligible_rows_of_population"
ORDERED_WINDOWS: tuple[Window, ...] = tuple(sorted(WINDOWS))
CHOICES = len(ORDERED_WINDOWS)
AUDIT_FIELDS = (
    "teams",
    "tasks",
    "graded_timeouts",
    "native_final_successes",
    "recorded_writes",
    "triggers",
    "alignments",
    *TARGETS,
    "later_writes",
    "verdict",
)
AUDIT_SLOTS = (
    ("slots", "registered_slots"),
    ("missing_slot", "missing_slots"),
    ("ambiguous_slot", "ambiguous_slots"),
)
MAX_STOCK_BYTES = 64 * 1024 * 1024
MAX_STOCK_REPORTS = 2 * len(DRIFTS)
Entry = tuple[int, int, str, dict[str, Any] | None]
assert CHOICES == 35
assert set(STOCK_STATUSES) <= set(STATUSES)
assert len(set(STATUSES)) == len(STATUSES)
assert len(set(DRIFTS)) == len(DRIFTS) and DRIFTS[0] is None


def _check_pack(pack: object) -> Pack:
    if not isinstance(pack, Pack):
        raise ValueError("replay needs a Pack from read_pack")
    total = 0
    for position, job in enumerate(pack.jobs, start=1):
        assert isinstance(job, Job) and job.index == position
        assert type(job.slots) is int and job.slots >= 0
        if job.state == PRESENT:
            assert len(job.rows) == job.slots
        else:
            assert job.state in SHARD_STATES and not job.rows
        total += job.slots
        if total > MAX_INPUTS:
            raise ValueError(f"pack registers more than {MAX_INPUTS} locked slots")
    return pack


def _check_setting(drift_ms: object, include_timeouts: object) -> int:
    if type(include_timeouts) is not bool:
        raise ValueError("include_timeouts must be a bool")
    if drift_ms is not None and type(drift_ms) is not int:
        raise ValueError("clock allowance must be null or integral milliseconds")
    if drift_ms not in DRIFTS:
        raise ValueError("clock allowance is not on the registered grid")
    return DRIFTS.index(drift_ms)


def _selected(pack: Pack, jobs: frozenset[int] | None) -> frozenset[int]:
    indices = frozenset(job.index for job in pack.jobs)
    if jobs is None:
        return indices
    if (
        not isinstance(jobs, frozenset)
        or not jobs
        or not all(type(index) is int for index in jobs)
        or not jobs <= indices
    ):
        raise ValueError("job filter must name registered job indices")
    return jobs


def _entries(pack: Pack, include_timeouts: bool) -> list[Entry]:
    entries: list[Entry] = []
    for job in pack.jobs:
        if job.state != PRESENT:
            entries.extend(
                (job.index, slot, job.state, None) for slot in range(1, job.slots + 1)
            )
            continue
        for slot, row in enumerate(job.rows, start=1):
            status = row.get("status")
            if status != EVIDENCE:
                if status not in UNSCORED:
                    raise ValueError("a shard row has an unknown status")
                entries.append((job.index, slot, status, None))
            elif row["timed_out"] and not include_timeouts:
                entries.append((job.index, slot, EXCLUDED_TIMEOUT, row))
            else:
                entries.append((job.index, slot, MEASURED, row))
    assert len(entries) == sum(job.slots for job in pack.jobs)
    return entries


def _final(row: dict[str, Any]) -> Window | None:
    writes = row["writes"]
    if not writes or writes[-1] is None:
        return None
    return ORDERED_WINDOWS[writes[-1]]


def _references(entries: list[Entry]) -> dict[str, frozenset[Window]]:
    pools: dict[str, set[Window]] = defaultdict(lambda: set(WINDOWS))
    for _, _, status, row in entries:
        if status != MEASURED:
            continue
        assert row is not None
        task = row["task"]
        pool = pools[task]
        if not pool:
            continue
        final = _final(row)
        if row["reward"] == 1:
            if final is None:
                pool.clear()
            else:
                pool.intersection_update((final,))
        elif final is not None:
            pool.discard(final)
    return {task: frozenset(pool) for task, pool in pools.items()}


def _alignment(trigger: str, reason: str) -> str:
    if trigger != "observed":
        return "inapplicable"
    if reason == UNANCHORED_REASON:
        return "unanchored"
    if reason == FRESH_REASON:
        return "fresh_interval"
    return "clock_unknown"


def _cut(row: dict[str, Any], grid: int, drift_ms: int | None) -> ExitCut:
    found: tuple[str, int, int] | None = None
    for start, reason, lo, hi in row["cuts"]:
        if start > grid:
            break
        found = (reason, lo, hi)
    if found is None:
        raise ValueError("an evidence row has no cut at the first grid setting")
    reason, lo, hi = found
    trigger = row["trigger"]
    cut = ExitCut(
        trigger, reason, _alignment(trigger, reason), row["first"], row["clean"], lo, hi
    )
    return check_cut(
        cut, seats=STOCK_PAIR, length=len(row["writes"]), drift_ms=drift_ms
    )


def _case(label: str, row: dict[str, Any], cut: ExitCut) -> ExitCase:
    if row["condition"] not in CONDITIONS:
        raise ValueError("an evidence row has an unknown condition")
    choices = tuple(
        None if token is None else ORDERED_WINDOWS[token] for token in row["writes"]
    )
    return exit_case(
        label,
        condition=row["condition"],
        timed_out=row["timed_out"],
        reward=row["reward"],
        choices=choices,
        cut=cut,
    )


def _measure(
    entries: list[Entry],
    references: dict[str, frozenset[Window]],
    selected: frozenset[int],
    setting: tuple[int, int | None],
) -> tuple[list[Entry], dict[str, list[ExitCase]], list[dict[str, Any]]]:
    grid, drift_ms = setting
    final: list[Entry] = []
    tasks: dict[str, list[ExitCase]] = {}
    cases: list[dict[str, Any]] = []
    for job, slot, status, row in entries:
        if status == MEASURED and row is not None and not references[row["task"]]:
            status = QUARANTINED
        final.append((job, slot, status, row))
        if status != MEASURED or job not in selected:
            continue
        assert row is not None
        task = row["task"]
        case = _case(f"{job}:{slot}", row, _cut(row, grid, drift_ms))
        tasks.setdefault(task, []).append(case)
        cases.append(
            {"job": job, "slot": slot, "task": task, **case_row(case, references[task])}
        )
    assert len(final) == len(entries)
    assert len(cases) == sum(len(group) for group in tasks.values())
    return final, tasks, cases


def _locked(entries: list[Entry], jobs: frozenset[int]) -> dict[str, Any]:
    counts = Counter(status for job, _, status, _ in entries if job in jobs)
    slots = sum(counts.values())
    unknown = sum(counts[status] for status in UNKNOWN_GRADE)
    shards = sum(counts[status] for status in SHARD_STATES)
    assert set(counts) <= set(STATUSES)
    assert (
        counts[MEASURED] + counts[EXCLUDED_TIMEOUT] + counts[QUARANTINED] + unknown
        == slots
    )
    return {
        "slots": slots,
        "measured": counts[MEASURED],
        "registered_excluded_timeouts": counts[EXCLUDED_TIMEOUT],
        "quarantined": counts[QUARANTINED],
        "unknown_grade": unknown,
        "unscored_shard_slots": shards,
        "statuses": {status: counts[status] for status in STATUSES},
    }


def _reference_summary(
    pack: Pack, entries: list[Entry], references: dict[str, frozenset[Window]]
) -> dict[str, Any]:
    sizes = Counter(min(len(reference), 2) for reference in references.values())
    return {
        "source": REFERENCE_SOURCE,
        "complete": all(job.state == PRESENT for job in pack.jobs),
        "rows": sum(status in (MEASURED, QUARANTINED) for _, _, status, _ in entries),
        "tasks": len(references),
        "keyed_tasks": sizes[1],
        "open_tasks": sizes[2],
        "quarantined_tasks": sizes[0],
        "choices_per_task": CHOICES,
    }


def _effect(cohort: dict[str, Any]) -> dict[str, Any]:
    net = cohort["net"]
    if net[0] > 0:
        direction = "positive"
    elif net[1] < 0:
        direction = "negative"
    else:
        direction = "not_identified"
    return {
        "teams": cohort["teams"],
        "forced_loss": cohort["lost"][0] > 0,
        "forced_gain": cohort["gained"][0] > 0,
        "net": net,
        "net_direction": direction,
        "verdict": cohort["verdict"],
    }


def _jobs(
    pack: Pack, entries: list[Entry], selected: frozenset[int]
) -> list[dict[str, Any]]:
    measured = Counter(job for job, _, status, _ in entries if status == MEASURED)
    return [
        {
            "job": job.index,
            "state": job.state,
            "slots": job.slots,
            "selected": job.index in selected,
            "measured": measured[job.index],
        }
        for job in pack.jobs
    ]


def replay(
    pack: Pack,
    *,
    drift_ms: int | None,
    include_timeouts: bool,
    jobs: frozenset[int] | None = None,
) -> dict[str, Any]:
    _check_pack(pack)
    grid = _check_setting(drift_ms, include_timeouts)
    selected = _selected(pack, jobs)
    entries = _entries(pack, include_timeouts)
    references = _references(entries)
    final, tasks, cases = _measure(entries, references, selected, (grid, drift_ms))
    cohorts = reduce_cohorts(tasks, {task: references[task] for task in tasks})
    assert cohorts["all"]["teams"] == len(cases)
    everything = frozenset(job.index for job in pack.jobs)
    locked = {
        "overall": _locked(final, everything),
        "selected": _locked(final, selected),
    }
    assert locked["selected"]["measured"] == len(cases)
    return {
        "schema": SCHEMA,
        "pack": pack.pack,
        "clock_error_ms": drift_ms,
        "observation_model": OBSERVATION_MODELS[drift_ms is not None],
        "include_verified_timeouts": include_timeouts,
        "population": POPULATIONS[include_timeouts],
        "primary_cohort": PRIMARY_COHORT,
        "job_filter": jobs is not None,
        "jobs": _jobs(pack, final, selected),
        "reference": _reference_summary(pack, final, references),
        "locked": locked,
        "cohort_names": list(COHORTS),
        "targets": list(TARGETS),
        "cohorts": cohorts,
        "effects": {name: _effect(cohorts[name]) for name in HEADLINE},
        "cases": sorted(cases, key=lambda case: (case["job"], case["slot"])),
    }


def _readout(settings: list[dict[str, Any]]) -> dict[str, Any]:
    readout: dict[str, Any] = {}
    for population in POPULATIONS:
        rows = [row for row in settings if row["population"] == population]
        assert len(rows) == len(DRIFTS)
        readout[population] = {
            name: {
                "forced_loss_clock_ms": [
                    row["clock_error_ms"]
                    for row in rows
                    if row["effects"][name]["forced_loss"]
                ],
                "signed_net": [
                    {
                        "clock_error_ms": row["clock_error_ms"],
                        "net_direction": row["effects"][name]["net_direction"],
                    }
                    for row in rows
                    if row["effects"][name]["net_direction"] != "not_identified"
                ],
            }
            for name in HEADLINE
        }
    return readout


def replay_grid(pack: Pack, *, jobs: frozenset[int] | None = None) -> dict[str, Any]:
    settings = [
        replay(pack, drift_ms=drift, include_timeouts=include, jobs=jobs)
        for include in (False, True)
        for drift in DRIFTS
    ]
    return {
        "schema": GRID_SCHEMA,
        "pack": pack.pack,
        "drift_ms": list(DRIFTS),
        "populations": list(POPULATIONS),
        "settings": settings,
        "readout": _readout(settings),
    }


def _stock_sections(
    stock: object,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not json_record(stock) or stock.get("schema") != STOCK_JOBS_SCHEMA:
        raise ValueError("stock report is not a schedule-jobs JSON report")
    behavior = stock.get("behavior")
    if not json_record(behavior):
        raise ValueError("stock report has no behavior section")
    seat_exit, summary = behavior.get("seat_exit"), behavior.get("summary")
    if not json_record(seat_exit) or not json_record(summary):
        raise ValueError("stock report was not run with --seat-exit")
    if not isinstance(seat_exit.get("cohorts"), dict):
        raise ValueError("stock report has no seat-exit cohorts")
    if not isinstance(summary.get("statuses"), dict):
        raise ValueError("stock report has no status counts")
    return stock, seat_exit, summary


def stock_audit(pack: Pack, stock: object) -> dict[str, Any]:
    top, seat_exit, summary = _stock_sections(stock)
    drift, include = seat_exit.get("clock_error_ms"), summary.get("includes_timed_out")
    if type(include) is not bool or "clock_error_ms" not in seat_exit:
        raise ValueError(
            "stock file has no explicit clock setting or timeout population"
        )
    replayed = replay(pack, drift_ms=drift, include_timeouts=include)
    checks: list[tuple[str, Any, Any]] = [
        (
            "observation_model",
            replayed["observation_model"],
            seat_exit.get("observation_model"),
        ),
        (
            "not_two_seats",
            replayed["locked"]["overall"]["statuses"]["not_two_seats"],
            seat_exit.get("not_two_seats"),
        ),
        ("task_grouped_case_multiset", same_task_cases(top, replayed["cases"]), True),
    ]
    overall = replayed["locked"]["overall"]
    for ours, theirs in AUDIT_SLOTS:
        mine = overall["slots"] if ours == "slots" else overall["statuses"][ours]
        checks.append((f"locked.{ours}", mine, top.get(theirs)))
    for status in STOCK_STATUSES:
        checks.append(
            (
                f"statuses.{status}",
                overall["statuses"][status],
                summary["statuses"].get(status),
            )
        )
    stock_cohorts = seat_exit["cohorts"]
    for name in COHORTS:
        theirs = stock_cohorts.get(name)
        theirs = theirs if isinstance(theirs, dict) else {}
        for field in AUDIT_FIELDS:
            checks.append(
                (
                    f"cohorts.{name}.{field}",
                    replayed["cohorts"][name][field],
                    theirs.get(field),
                )
            )
    mismatches = [{"field": field} for field, ours, theirs in checks if ours != theirs]
    return {
        "clock_error_ms": drift,
        "include_verified_timeouts": include,
        "reference_complete": replayed["reference"]["complete"],
        "checked": len(checks),
        "matched": not mismatches,
        "mismatches": mismatches,
    }


def _load_stock(path: Path) -> object:
    with path.open("rb") as handle:
        data = handle.read(MAX_STOCK_BYTES + 1)
    if len(data) > MAX_STOCK_BYTES:
        raise ValueError("stock report is larger than the audit limit")
    return json.loads(data)


def _shards(manifest: Path, given: list[Path]) -> list[Path]:
    if given:
        return given
    found = sorted(islice(manifest.parent.glob("job-*.json"), MAX_SHARD_FILES + 1))
    if len(found) > MAX_SHARD_FILES:
        raise ValueError("pack directory holds too many shard files")
    return found


def _span(bounds: list[int]) -> str:
    return f"{bounds[0]}..{bounds[1]}"


def _model(drift_ms: int | None) -> str:
    if drift_ms is None:
        return "unanchored (no host/world clock alignment)"
    return (
        f"conditional on a fresh stock world and elapsed clock error <= {drift_ms} ms"
    )


def _cohort_line(name: str, cohort: dict[str, Any], effect: dict[str, Any]) -> str:
    return (
        f"{name}: teams {cohort['teams']}  native successes "
        f"{cohort['native_final_successes']}  lost {_span(cohort['lost'])}  "
        f"gained {_span(cohort['gained'])}  net {_span(cohort['net'])}  "
        f"policy {_span(cohort['policy_successes'])}  verdict {cohort['verdict']}  "
        f"direction {effect['net_direction']}"
    )


def _text(report: dict[str, Any]) -> list[str]:
    overall, chosen = report["locked"]["overall"], report["locked"]["selected"]
    reference = report["reference"]
    lines = [
        f"Pack {report['pack']}  {_model(report['clock_error_ms'])}",
        f"Population: {report['population']}",
        f"Locked slots: {chosen['slots']} selected of {overall['slots']}  "
        f"measured {chosen['measured']}  excluded timeouts "
        f"{chosen['registered_excluded_timeouts']}  quarantined {chosen['quarantined']}  "
        f"unknown grade {chosen['unknown_grade']} "
        f"(lost shards {chosen['unscored_shard_slots']})",
        f"Task keys from all present eligible rows: {reference['tasks']} tasks  "
        f"keyed {reference['keyed_tasks']}  open {reference['open_tasks']}  "
        f"quarantined {reference['quarantined_tasks']}",
    ]
    if not reference["complete"]:
        lines.append("Partial pack: lost shards can widen keys and hide conflicts.")
    for name in HEADLINE:
        lines.append(
            _cohort_line(name, report["cohorts"][name], report["effects"][name])
        )
    lines.append(
        "not_identified: the net interval contains 0, so no directional effect. "
        "Unknown-grade slots get no effect. Bounds are not confidence intervals."
    )
    return lines


def _grid_text(report: dict[str, Any]) -> list[str]:
    lines = [f"Pack {report['pack']}"]
    for row in report["settings"]:
        clock = "null" if row["clock_error_ms"] is None else str(row["clock_error_ms"])
        parts = [f"{row['population']:<30} {clock:>6} {row['observation_model']:<24}"]
        for name in HEADLINE:
            cohort, effect = row["cohorts"][name], row["effects"][name]
            parts.append(
                f"{name} lost {_span(cohort['lost'])} net {_span(cohort['net'])} "
                f"{effect['net_direction']}"
            )
        lines.append("  ".join(parts))
    for population, readout in report["readout"].items():
        for name, found in readout.items():
            lines.append(
                f"{population} {name}: forced loss at {found['forced_loss_clock_ms']}  "
                f"signed net at {[row['clock_error_ms'] for row in found['signed_net']]}"
            )
    return lines


def _audit_text(report: dict[str, Any]) -> list[str]:
    lines = [f"Pack {report['pack']}  stock audits {len(report['audits'])}"]
    for audit in report["audits"]:
        clock = "null" if audit["clock_error_ms"] is None else audit["clock_error_ms"]
        state = (
            "matched" if audit["matched"] else f"{len(audit['mismatches'])} mismatches"
        )
        lines.append(
            f"clock {clock}  timeouts {audit['include_verified_timeouts']}: "
            f"{state} of {audit['checked']} checks"
        )
        lines.extend(f"  {item['field']}" for item in audit["mismatches"][:20])
    return lines


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -BS -m examples.seat_handoffs.pack_replay",
        description="Replay standing-grade bounds from a blinded schedule-exit pack.",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("shards", nargs="*", type=Path)
    parser.add_argument("--clock-ms", type=int, default=None)
    parser.add_argument("--include-timeouts", action="store_true")
    parser.add_argument("--job", type=int, action="append", default=None)
    parser.add_argument(
        "--grid", action="store_true", help="all clock settings and both populations"
    )
    parser.add_argument(
        "--stock",
        type=Path,
        action="append",
        default=None,
        help="schedule-jobs --seat-exit --json report to audit against; repeatable",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _run(
    options: argparse.Namespace, pack: Pack
) -> tuple[dict[str, Any], list[str], int]:
    jobs = None if options.job is None else frozenset(options.job)
    if options.stock is not None:
        audits = [stock_audit(pack, _load_stock(path)) for path in options.stock]
        report = {"schema": AUDIT_SCHEMA, "pack": pack.pack, "audits": audits}
        matched = all(audit["matched"] for audit in audits)
        return report, _audit_text(report), 0 if matched else 1
    if options.grid:
        report = replay_grid(pack, jobs=jobs)
        return report, _grid_text(report), 0
    report = replay(
        pack,
        drift_ms=options.clock_ms,
        include_timeouts=options.include_timeouts,
        jobs=jobs,
    )
    return report, _text(report), 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    options = parser.parse_args(argv)
    if options.grid and (options.clock_ms is not None or options.include_timeouts):
        parser.error("--grid covers every clock setting and both populations")
    if options.stock is not None:
        if (
            options.grid
            or options.job
            or options.clock_ms is not None
            or options.include_timeouts
        ):
            parser.error(
                "--stock takes each setting from its report and needs the whole pack"
            )
        if len(options.stock) > MAX_STOCK_REPORTS:
            parser.error(f"give at most {MAX_STOCK_REPORTS} stock reports")
    try:
        pack = read_pack(options.manifest, _shards(options.manifest, options.shards))
        report, lines, code = _run(options, pack)
    except OSError as exc:
        print(
            f"Cannot replay pack: {exc.strerror or 'file is unreadable'}",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        print(f"Cannot replay pack: {exc}", file=sys.stderr)
        return 2
    if options.json:
        print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    else:
        print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
