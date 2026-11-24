import argparse
import errno
import hashlib
import json
import os
import secrets
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pack_io import (
    DRIFTS,
    MANIFEST_SCHEMA,
    MAX_MANIFEST_BYTES,
    MAX_SHARD_BYTES,
    SHARD_SCHEMA,
)
from .schedule_bounds import WINDOWS, Window
from .schedule_changes import _partial_id
from .schedule_exit_clock import ExitCut, cut_at_exit
from .schedule_jobs import MAX_TOTAL, _members, _plan
from .schedule_native import (
    MAX_WRITES,
    AmbiguousRubric,
    Team,
    read_records,
    team_from_records,
)

SEATS = 2
CHOICES: tuple[Window, ...] = tuple(sorted(WINDOWS))
LABEL_BYTES = 32
MANIFEST = "manifest.json"
PARTIAL = ".manifest.json.partial"
FILE_MODE = 0o644
DIRECTORY_MODE = 0o755
READ_ERRORS = (OSError, ValueError, RecursionError, OverflowError)
assert len(CHOICES) == 35
assert len(DRIFTS) == 11 and DRIFTS[0] is None

OUTPUT_TYPE = "the output path must be a pathlib.Path"
OUTPUT_EXISTS = "the output path already exists; choose a new directory"
NESTED_OUTPUT = "the output directory must lie outside every job root"
MIXED_ROSTER = (
    "a task has both two-seat and larger teams; the stock reader infers that "
    "task's shared key from both rosters, so a two-seat pack would change it"
)
SPLIT_VALIDITY = "a team's seat exit evidence is valid at some clock settings only"
TOO_LARGE = "an export file exceeds its size bound"
WRITE_STALLED = "the operating system accepted no bytes for an export file"

Blind = tuple[str, dict[Window, int]]


@dataclass(frozen=True)
class _Read:
    status: str
    team: Team | None
    cuts: tuple[ExitCut, ...] | None


def _status(error: BaseException) -> str:
    if isinstance(error, OSError):
        return "missing_evidence"
    if isinstance(error, AmbiguousRubric):
        return "rubric_ambiguous"
    return "invalid_evidence"


def _cuts(
    result: dict[str, Any], state: dict[str, Any], seats: tuple[str, ...]
) -> tuple[ExitCut, ...] | str:
    found: list[ExitCut] = []
    failed: set[str] = set()
    for drift in DRIFTS:
        try:
            found.append(cut_at_exit(result, state, seats, drift_ms=drift))
        except READ_ERRORS as error:
            failed.add(_status(error))
    if not failed:
        assert len(found) == len(DRIFTS)
        return tuple(found)
    if found or len(failed) != 1:
        raise ValueError(SPLIT_VALIDITY)
    return failed.pop()


def _snapshot(path: Path) -> _Read:
    try:
        result, score, state = read_records(path)
        team = team_from_records(result, score, state, seat_exit=None)
    except READ_ERRORS as error:
        return _Read(_status(error), None, None)
    assert team.seat_exit is None and len(team.writes) <= MAX_WRITES
    if len(team.seats) != SEATS:
        return _Read("usable", team, None)
    cuts = _cuts(result, state, team.seats)
    if isinstance(cuts, str):
        return _Read(cuts, None, None)
    return _Read("usable", team, cuts)


def _quarantine(paths: list[Path], reads: list[_Read]) -> list[_Read]:
    identities = [
        read.team.native_id if read.team is not None else _partial_id(path)
        for path, read in zip(paths, reads, strict=True)
    ]
    seen = Counter(identity for identity in identities if identity is not None)
    quarantined = [
        _Read("duplicate_trial", None, None)
        if identity is not None and seen[identity] > 1
        else read
        for read, identity in zip(reads, identities, strict=True)
    ]
    assert len(quarantined) == len(reads)
    return quarantined


def _check_rosters(reads: list[_Read]) -> None:
    pairs: set[str] = set()
    larger: set[str] = set()
    for read in reads:
        if read.team is None:
            continue
        assert read.status == "usable"
        target = pairs if read.cuts is not None else larger
        target.add(read.team.task_digest)
    if pairs & larger:
        raise ValueError(MIXED_ROSTER)


def _blinds(reads: list[_Read], rng: secrets.SystemRandom) -> dict[str, Blind]:
    blinds: dict[str, Blind] = {}
    for read in reads:
        if read.team is None or read.cuts is None:
            continue
        digest = read.team.task_digest
        if digest in blinds:
            continue
        tokens = rng.sample(range(len(CHOICES)), len(CHOICES))
        label = secrets.token_hex(LABEL_BYTES)
        blinds[digest] = (label, dict(zip(CHOICES, tokens, strict=True)))
    labels = {label for label, _ in blinds.values()}
    assert len(labels) == len(blinds)
    return blinds


def _runs(cuts: tuple[ExitCut, ...]) -> list[list[Any]]:
    runs: list[list[Any]] = []
    last: tuple[str, int, int] | None = None
    for index, cut in enumerate(cuts):
        key = (cut.reason, cut.lo, cut.hi)
        if key != last:
            runs.append([index, *key])
            last = key
    assert runs and runs[0][0] == 0 and len(runs) <= len(cuts)
    return runs


def _evidence(team: Team, cuts: tuple[ExitCut, ...], blind: Blind) -> dict[str, Any]:
    first = cuts[0]
    fixed = (first.trigger, first.first_seat, first.clean_seats)
    assert len(cuts) == len(DRIFTS)
    assert all((cut.trigger, cut.first_seat, cut.clean_seats) == fixed for cut in cuts)
    assert all(0 <= cut.lo <= cut.hi <= len(team.writes) for cut in cuts)
    label, tokens = blind
    writes = [None if w.choice is None else tokens[w.choice] for w in team.writes]
    assert len(writes) == len(team.writes) <= MAX_WRITES
    return {
        "status": "evidence",
        "task": label,
        "condition": team.condition,
        "timed_out": team.timed_out,
        "reward": team.reward,
        "writes": writes,
        "trigger": first.trigger,
        "first": first.first_seat,
        "clean": first.clean_seats,
        "cuts": _runs(cuts),
    }


def _row(read: _Read, blinds: dict[str, Blind]) -> dict[str, Any]:
    if read.status != "usable":
        assert read.team is None and read.cuts is None
        return {"status": read.status}
    assert read.team is not None
    if read.cuts is None:
        return {"status": "not_two_seats"}
    return _evidence(read.team, read.cuts, blinds[read.team.task_digest])


def _job_rows(
    counts: dict[str, int],
    reads: list[_Read],
    blinds: dict[str, Blind],
    rng: secrets.SystemRandom,
) -> list[dict[str, Any]]:
    rows = [_row(read, blinds) for read in reads]
    rows.extend({"status": "missing_slot"} for _ in range(counts["missing_slots"]))
    rows.extend({"status": "ambiguous_slot"} for _ in range(counts["ambiguous_slots"]))
    assert len(rows) == counts["registered_slots"] <= MAX_TOTAL
    rng.shuffle(rows)
    return rows


def _encode(document: dict[str, Any], limit: int) -> bytes:
    text = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    data = text.encode("ascii") + b"\n"
    if len(data) > limit:
        raise ValueError(TOO_LARGE)
    return data


def build(paths: list[Path]) -> tuple[list[tuple[str, bytes]], bytes, dict[str, int]]:
    plans = [_plan(member) for member in _members(paths)]
    joint = [path for _, behavior in plans for path in behavior]
    assert len(joint) <= MAX_TOTAL
    reads = _quarantine(joint, [_snapshot(path) for path in joint])
    _check_rosters(reads)
    rng = secrets.SystemRandom()
    blinds = _blinds(reads, rng)
    pack = str(uuid.uuid4())
    files: list[tuple[str, bytes]] = []
    jobs: list[dict[str, Any]] = []
    start = 0
    for index, (counts, behavior) in enumerate(plans, start=1):
        part = reads[start : start + len(behavior)]
        start += len(behavior)
        rows = _job_rows(counts, part, blinds, rng)
        shard = {"schema": SHARD_SCHEMA, "pack": pack, "job": index, "rows": rows}
        data = _encode(shard, MAX_SHARD_BYTES)
        files.append((f"job-{index:03d}.json", data))
        digest = hashlib.sha256(data).hexdigest()
        jobs.append({"job": index, "slots": len(rows), "sha256": digest})
    assert start == len(reads) and len(files) == len(plans)
    document = {
        "schema": MANIFEST_SCHEMA,
        "pack": pack,
        "drift_ms": list(DRIFTS),
        "jobs": jobs,
    }
    summary = {
        "jobs": len(jobs),
        "slots": sum(job["slots"] for job in jobs),
        "evidence": sum(read.cuts is not None for read in reads),
    }
    return files, _encode(document, MAX_MANIFEST_BYTES), summary


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, FILE_MODE)
    try:
        view = memoryview(data)
        written = 0
        for _ in range(len(data)):
            if written == len(data):
                break
            count = os.write(fd, view[written:])
            if count <= 0:
                raise OSError(errno.EIO, WRITE_STALLED)
            written += count
        assert written == len(data)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _discard_file(path)
        raise
    os.close(fd)


def _discard_file(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _discard(out: Path, created: list[Path]) -> None:
    for path in reversed(created):
        _discard_file(path)
    try:
        os.rmdir(out)
    except OSError:
        pass


def publish(out: Path, files: list[tuple[str, bytes]], manifest: bytes) -> None:
    assert files and all(name != MANIFEST and name != PARTIAL for name, _ in files)
    os.mkdir(out, DIRECTORY_MODE)
    created: list[Path] = []
    partial = out / PARTIAL
    try:
        for name, data in files:
            _write_new(out / name, data)
            created.append(out / name)
        _write_new(partial, manifest)
        created.append(partial)
        _sync(out)
        os.link(partial, out / MANIFEST)
    except BaseException:
        _discard(out, created)
        raise
    os.unlink(partial)
    _sync(out)


def _outside(out: Path, paths: list[Path]) -> None:
    target = os.path.realpath(out)
    for path in paths:
        root = os.path.realpath(path)
        if os.path.commonpath([target, root]) == root:
            raise ValueError(NESTED_OUTPUT)


def export(out: Path, paths: list[Path]) -> dict[str, int]:
    if not isinstance(out, Path):
        raise ValueError(OUTPUT_TYPE)
    if os.path.lexists(out):
        raise ValueError(OUTPUT_EXISTS)
    files, manifest, summary = build(paths)
    _outside(out, paths)
    publish(out, files, manifest)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -BS -m examples.seat_handoffs.pack_owner",
        description="Export a blinded seat-exit pack from native schedule job roots.",
    )
    parser.add_argument("out", type=Path, help="new directory for the pack files")
    parser.add_argument("jobs", type=Path, nargs="+", help="native job roots in order")
    args = parser.parse_args(argv)
    try:
        summary = export(args.out, args.jobs)
    except FileExistsError:
        print(f"Cannot export pack: {OUTPUT_EXISTS}", file=sys.stderr)
        return 2
    except OSError as exc:
        reason = exc.strerror or "operating system error"
        print(f"Cannot export pack: {reason}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Cannot export pack: {exc}", file=sys.stderr)
        return 2
    print(
        f"Pack written: {summary['jobs']} job shards, {summary['slots']} locked "
        f"slots, {summary['evidence']} two-seat evidence rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
