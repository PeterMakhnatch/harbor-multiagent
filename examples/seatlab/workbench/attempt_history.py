import argparse
import datetime
import json
import os
import re
import stat
import sys
from typing import Any, NoReturn, override

from . import seat_stream
from .seat_stream import UUID, Malformed, Record, _read, _require, _strict_json

SCHEMA = "seatlab.workbench.attempt-history/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.attempt_history"
MAX_TRIALS = 4096
MAX_RETRIES = 16384
MAX_FILES = MAX_TRIALS + MAX_RETRIES
MAX_ARCHIVE_BYTES = 256 * seat_stream.MIB
WITNESS_SCHEMA = "seatlab.independent-slot-retry-witness/v1"
MAX_WITNESS_BYTES = 8 * seat_stream.MIB
SLOT_KEY = re.compile(r"[0-9a-f]{64}")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
ENTRY = re.compile(
    rf"(?P<job>{UUID.pattern})\.(?P<slot>[0-9a-f]{{64}})\.(?P<attempt>{UUID.pattern})\.jsonl"
)
CLASSES = ("pending_header", "open", "closed_partial_roster", "closed_full_roster")
CAVEAT = (
    "Counts passive native seat journals kept as one hardlink per execution. Entry names are local labels from a "
    "trusted collector, not authentication; the read-only archive may be a shared plain directory. "
    "closed_full_roster counts rows only: closed with no trailing partial line, and every declared seat has run_enter "
    "and a play_end with an explicit disposition. A null or ended_unknown play_end, a trailing partial line, "
    "pending_header and open are unknown, never an outcome. No grade, reward or pass is read or implied. "
    "expected_executions exists only for finished jobs whose completed trials equal registered trials."
)


def _bounded(value: Any, high: int, reason: str) -> int:
    _require(type(value) is int and 0 <= value <= high, reason)
    return value


def _job(path: str) -> Record:
    document = _strict_json(
        _read(path, seat_stream.MAX_RESULT_BYTES, "job result"),
        "job result is not strict UTF-8 JSON",
    )
    if not isinstance(document, dict):
        raise Malformed("job result is not a JSON object")
    version = document.get("schema_version", 1)
    _require(
        type(version) is int and version == 1, "job result schema_version is not 1"
    )
    job = document.get("id")
    if not isinstance(job, str) or UUID.fullmatch(job) is None:
        raise Malformed("job result id is not a canonical lowercase UUID")
    _require(type(document.get("started_at")) is str, "job result has no started_at")
    stats = document.get("stats")
    if not isinstance(stats, dict):
        raise Malformed("job result has no stats object")
    total = _bounded(
        document.get("n_total_trials"), MAX_TRIALS, "n_total_trials is not 0 to 4096"
    )
    retries = _bounded(
        stats.get("n_retries"), MAX_RETRIES, "stats.n_retries is not 0 to 16384"
    )
    completed = _bounded(
        stats.get("n_completed_trials"),
        total,
        "stats.n_completed_trials is not 0 to n_total_trials",
    )
    finished = document.get("finished_at")
    if finished is not None:
        if not isinstance(finished, str):
            raise Malformed("job result finished_at is not null or an ISO timestamp")
        try:
            datetime.datetime.fromisoformat(finished)
        except ValueError as error:
            raise Malformed(
                "job result finished_at is not null or an ISO timestamp"
            ) from error
    final = finished is not None and completed == total
    return {
        "id": job,
        "total": total,
        "retries": retries,
        "finished_at": finished,
        "final": final,
    }


def _names(archive: str) -> tuple[int, list[str]]:
    try:
        fd = os.open(archive, DIR_FLAGS)
    except (OSError, ValueError) as error:
        raise Malformed("archive is not a readable non-symlink directory") from error
    try:
        _require(stat.S_ISDIR(os.fstat(fd).st_mode), "archive is not a directory")
        names: list[str] = []
        with os.scandir(fd) as entries:
            for entry in entries:
                _require(len(names) < MAX_FILES, f"archive exceeds {MAX_FILES} entries")
                names.append(entry.name)
    except BaseException as error:
        os.close(fd)
        if isinstance(error, OSError):
            raise Malformed("archive is unreadable") from error
        raise
    return fd, sorted(names)


def _classify(report: Record) -> str:
    if report["state"] != "closed":
        return "pending_header" if report["state"] == "no_header" else "open"
    explicit = report["state"] == "closed" and not report["trailing_partial"]
    full = explicit and all(
        seat["entered"] and seat["terminal"] in seat_stream.DISPOSITIONS
        for seat in report["seats"]
    )
    return "closed_full_roster" if full else "closed_partial_roster"


def _scan(job: str, archive: str) -> tuple[dict[str, list[str]], dict[str, int]]:
    fd, names = _names(archive)
    slots: dict[str, list[str]] = {}
    counts = dict.fromkeys(CLASSES, 0)
    seen: set[str] = set()
    size = 0
    try:
        pin = os.fstat(fd)
        for name in names:
            match = ENTRY.fullmatch(name)
            if match is None:
                raise Malformed(
                    "archive holds an entry without a canonical attempt name"
                )
            _require(match["job"] == job, "archive holds an attempt of another job")
            attempt = match["attempt"]
            _require(attempt not in seen, "archive holds a duplicate attempt")
            seen.add(attempt)
            data = seat_stream.read_journal_bytes(os.path.join(archive, name))
            size += len(data)
            _require(size <= MAX_ARCHIVE_BYTES, "archive journals exceed 256 MiB")
            report = seat_stream.decode_snapshot(data)
            _require(
                report["trial"] in (None, attempt),
                "journal header names another attempt",
            )
            counts[_classify(report)] += 1
            slots.setdefault(match["slot"], []).append(attempt)
        after = os.lstat(archive)
        _require(
            (after.st_dev, after.st_ino) == (pin.st_dev, pin.st_ino),
            "archive was replaced",
        )
    except OSError as error:
        raise Malformed("archive is unreadable") from error
    finally:
        os.close(fd)
    return slots, counts


def _witness(path: str, job: str) -> dict[str, str]:
    document = _strict_json(
        _read(path, MAX_WITNESS_BYTES, "witness"), "witness is malformed"
    )
    if not isinstance(document, dict) or document.get("schema") != WITNESS_SCHEMA:
        raise Malformed("witness is malformed")
    _require(type(document.get("job")) is str, "witness is malformed")
    _require(document.get("job") == job, "witness names another job")
    grouping = document.get("observed_grouping")
    if not isinstance(grouping, dict) or len(grouping) > MAX_TRIALS:
        raise Malformed("witness is malformed")
    owners: dict[str, str] = {}
    for slot, attempts in grouping.items():
        valid = SLOT_KEY.fullmatch(slot) is not None and isinstance(attempts, list)
        _require(
            valid and 0 < len(attempts) <= MAX_FILES - len(owners),
            "witness is malformed",
        )
        for attempt in attempts:
            canonical = type(attempt) is str and UUID.fullmatch(attempt) is not None
            _require(canonical and attempt not in owners, "witness is malformed")
            owners[attempt] = slot
        _require(attempts == sorted(attempts), "witness is malformed")
    return owners


def _agreement(slots: dict[str, list[str]], owners: dict[str, str]) -> Record:
    archive = {attempt: slot for slot, group in slots.items() for attempt in group}
    shared = archive.keys() & owners.keys()
    matched = sum(archive[attempt] == owners[attempt] for attempt in shared)
    counts = {
        "matched": matched,
        "moved": len(shared) - matched,
        "archive_only": len(archive.keys() - owners.keys()),
        "witness_only": len(owners.keys() - archive.keys()),
    }
    return {**counts, "agrees": matched == len(archive) == len(owners)}


def build(job_path: str, archive: str, witness: str | None = None) -> Record:
    job = _job(job_path)
    owners = None if witness is None else _witness(witness, job["id"])
    slots, counts = _scan(job["id"], archive)
    observed = sum(counts.values())
    _require(
        len(slots) <= job["total"], "archive holds more slots than registered trials"
    )
    expected = job["total"] + job["retries"] if job["final"] else None
    _require(
        expected is None or observed <= expected,
        "archive holds more executions than expected",
    )
    known = counts["closed_full_roster"]
    complete = expected is not None and job["total"] > 0 and observed == expected
    complete = complete and len(slots) == job["total"] and known == observed
    report = {
        "schema": SCHEMA,
        "job": job["id"],
        "finished_at": job["finished_at"],
        "finalized": job["final"],
        "registered_slots": job["total"],
        "native_retries": job["retries"],
        "expected_executions": expected,
        "observed_executions": observed,
        "closed_executions": counts["closed_partial_roster"] + known,
        "known_closed_executions": known,
        "missing_executions": None if expected is None else max(expected - observed, 0),
        "observed_slots": len(slots),
        "slots": [
            {"slot_key": key, "attempts": sorted(slots[key])} for key in sorted(slots)
        ],
        "classifications": counts,
        "complete": complete,
        "caveat": CAVEAT,
    }
    if owners is not None:
        agreement = _agreement(slots, owners)
        report["coverage_complete"] = complete
        report["witness"] = agreement
        report["complete"] = complete and agreement["agrees"]
    return report


def text(report: Record) -> str:
    skip = ("schema", "slots", "classifications", "caveat")
    lines = [
        f"{key}: {json.dumps(value)}"
        for key, value in report.items()
        if key not in skip
    ]
    lines += [f"  {name}: {count}" for name, count in report["classifications"].items()]
    lines += [
        f"slot {slot['slot_key']}: {' '.join(slot['attempts'])}"
        for slot in report["slots"]
    ]
    return "\n".join([*lines, report["caveat"]])


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        sys.stderr.write(
            "attempt_history: usage error; expected JOB_RESULT_PATH ARCHIVE_DIR [--witness PATH] [--json]\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    cli = _Cli(
        prog=PROG,
        description="Count retained native attempt journals against one job result.",
    )
    cli.add_argument("job_result")
    cli.add_argument("archive")
    cli.add_argument("--witness")
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    try:
        report = build(args.job_result, args.archive, args.witness)
    except Malformed as error:
        print(f"attempt_history: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
        if args.json
        else text(report)
    )
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
