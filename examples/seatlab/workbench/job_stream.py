import argparse
import json
import os
import stat
import sys
from typing import NoReturn, override

from .seat_stream import (
    MAX_RESULT_BYTES,
    UUID,
    Malformed,
    Record,
    _read,
    _require,
    _strict_json,
    compare_roster,
    decode_snapshot,
    read_journal_bytes,
    result_roster,
)

SCHEMA = "seatlab.workbench.job-stream/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.job_stream"
RESULT = "result.json"
JOURNAL = "seat-events.jsonl"
MAX_TRIALS = 4096
MAX_ENTRIES = 8192
LIFECYCLES = ("complete", "incomplete", "conflict", "not_recorded")
CAVEAT = (
    "Snapshot of one native job directory. Counts cover retained final trial directories only, not every attempt. "
    "complete means a closed journal whose every seat end is known and agrees with the native result; it is not a "
    "grade. A journal without a result is unbound and never fills a missing result. This is not a live watch."
)


def _object(path: str, label: str) -> Record:
    document = _strict_json(
        _read(path, MAX_RESULT_BYTES, label), f"{label} is not strict UTF-8 JSON"
    )
    _require(type(document) is dict, f"{label} is not a JSON object")
    return document


def _root(job: str) -> tuple[str, int, int | None]:
    try:
        mode = os.lstat(job).st_mode
    except (OSError, ValueError) as error:
        raise Malformed("job directory is missing or unreadable") from error
    _require(stat.S_ISDIR(mode), "job is not a real non-symlink directory")
    document = _object(os.path.join(job, RESULT), "job result")
    version, job_id = document.get("schema_version"), document.get("id")
    _require(
        type(version) is int and version == 1, "job result schema_version is not 1"
    )
    if type(job_id) is not str or UUID.fullmatch(job_id) is None:
        raise Malformed("job result id is not a canonical lowercase UUID")
    total = document.get("n_total_trials")
    if type(total) is not int or not 0 <= total <= MAX_TRIALS:
        raise Malformed("job result n_total_trials is not an integer from 0 to 4096")
    stats = document.get("stats")
    retries = stats.get("n_retries") if type(stats) is dict else None
    valid = type(retries) is int and 0 <= retries <= MAX_ENTRIES
    return job_id, total, retries if valid else None


def _children(job: str) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(job) as entries:
            for index, entry in enumerate(entries):
                _require(index < MAX_ENTRIES, "job directory exceeds 8192 entries")
                _require(not entry.is_symlink(), "job directory holds a symlink")
                if entry.is_dir(follow_symlinks=False):
                    names.append(entry.name)
                    continue
                _require(
                    entry.is_file(follow_symlinks=False),
                    "job directory holds a special file",
                )
    except OSError as error:
        raise Malformed("job directory is unreadable") from error
    return sorted(names)


def _present(path: str, label: str) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as error:
        raise Malformed(f"{label} is unreadable") from error
    _require(stat.S_ISREG(mode), f"{label} is not a regular non-symlink file")
    return True


def _lifecycle(report: Record, trial: str, roster: Record) -> str:
    if report["trial"] is None:
        return "incomplete"
    counts = compare_roster(report, trial, roster)["counts"]
    if counts["conflict"]:
        return "conflict"
    agreed = counts["agree"] == len(roster)
    clean = report["counts"]["play_end_without_enter"] == 0
    closed = report["state"] == "closed"
    return "complete" if closed and agreed and clean else "incomplete"


def _trial(directory: str, name: str, job_id: str) -> tuple[str, str]:
    document = _object(os.path.join(directory, RESULT), "trial result")
    config = document.get("config")
    _require(
        type(config) is dict and config.get("job_id") == job_id,
        "trial result belongs to another job",
    )
    _require(
        document.get("trial_name") == name,
        "trial result names another directory",
    )
    trial, roster = result_roster(document)
    journal = os.path.join(directory, JOURNAL)
    if not _present(journal, "journal"):
        return trial, "not_recorded"
    return trial, _lifecycle(
        decode_snapshot(read_journal_bytes(journal)), trial, roster
    )


def build(job: str) -> Record:
    job_id, registered, retries = _root(job)
    lifecycles: dict[str, str] = {}
    unbound = 0
    for name in _children(job):
        directory = os.path.join(job, name)
        if _present(os.path.join(directory, RESULT), "trial result"):
            trial, lifecycle = _trial(directory, name, job_id)
            _require(trial not in lifecycles, "two directories hold the same trial")
            lifecycles[trial] = lifecycle
            _require(
                len(lifecycles) <= registered,
                "job holds more trial results than n_total_trials",
            )
        elif _present(os.path.join(directory, JOURNAL), "journal"):
            decode_snapshot(read_journal_bytes(os.path.join(directory, JOURNAL)))
            unbound += 1
    counts = {
        state: sum(value == state for value in lifecycles.values())
        for state in LIFECYCLES
    }
    report: Record = {
        "schema": SCHEMA,
        "job": job_id,
        "registered": registered,
        "bound_results": len(lifecycles),
        "missing_results": registered - len(lifecycles),
        "complete": counts["complete"],
        "unbound_journals": unbound,
        "lifecycles": counts,
        "trials": [
            {"trial": trial, "lifecycle": lifecycles[trial]}
            for trial in sorted(lifecycles)
        ],
        "caveat": CAVEAT,
    }
    if retries is not None:
        report["retries"] = retries
    return report


def passed(report: Record) -> bool:
    return (
        report["registered"] > 0
        and report["missing_results"] == 0
        and report["complete"] == report["registered"]
        and report["unbound_journals"] == 0
    )


def text(report: Record) -> str:
    lines = [
        (
            f"job {report['job']}: registered {report['registered']}; bound results {report['bound_results']}; "
            f"missing results {report['missing_results']}; unbound journals {report['unbound_journals']}"
        ),
        "lifecycles: "
        + ", ".join(
            f"{state} {count}" for state, count in report["lifecycles"].items()
        ),
    ]
    if "retries" in report:
        lines.append(
            f"root n_retries: {report['retries']} (attempts not retained here)"
        )
    lines.extend(f"  {row['trial']}: {row['lifecycle']}" for row in report["trials"])
    return "\n".join([*lines, report["caveat"]])


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        sys.stderr.write("job_stream: usage error; expected JOB_DIR [--json]\n")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    cli = _Cli(
        prog=PROG,
        description="Count native multi-seat trials of one finished job by seat journal lifecycle.",
    )
    cli.add_argument("job")
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    try:
        report = build(args.job)
    except Malformed as error:
        print(f"job_stream: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
        if args.json
        else text(report)
    )
    return 0 if passed(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
