import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from itertools import pairwise, takewhile
from pathlib import Path
from typing import Any, TypeGuard

from .core import canonical_uuid, reject_constant, unique_object
from .job_members import MAX_SLOTS
from .schedule_bounds import WINDOWS
from .schedule_changes import MAX_INPUTS
from .schedule_exit_bounds import FRESH_REASON, UNANCHORED_REASON
from .schedule_exit_clock import (
    ABSENT_REASONS,
    CLOCK_REASONS,
    EXIT_REASONS,
    MAX_DRIFT_MS,
    MAX_WRITES,
    STOCK_PAIR,
    TRIGGERS,
    UNKNOWN_REASONS,
)
from .schedule_jobs import MAX_JOBS
from .schedule_native import CONDITIONS

MANIFEST_SCHEMA = "harbor.schedule-exit-pack.manifest/v1"
SHARD_SCHEMA = "harbor.schedule-exit-pack.shard/v1"
DRIFTS: tuple[int | None, ...] = (
    None,
    0,
    100,
    250,
    500,
    1000,
    2000,
    5000,
    10000,
    30000,
    60000,
)
STATES = ("present", "missing_shard", "duplicate_shard", "changed_shard")
EVIDENCE = "evidence"
UNSCORED = (
    "missing_slot",
    "ambiguous_slot",
    "missing_evidence",
    "invalid_evidence",
    "rubric_ambiguous",
    "duplicate_trial",
    "not_two_seats",
)
CHOICES = len(WINDOWS)
CLEAN_COUNTS = (0, 1, 2)
REWARDS = (0, 1)
MAX_SHARD_FILES = 2 * MAX_JOBS
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SHARD_BYTES = 32 * 1024 * 1024
MAX_READ_BYTES = 128 * 1024 * 1024
MANIFEST_KEYS = frozenset({"schema", "pack", "drift_ms", "jobs"})
ENTRY_KEYS = frozenset({"job", "slots", "sha256"})
SHARD_KEYS = frozenset({"schema", "pack", "job", "rows"})
UNSCORED_KEYS = frozenset({"status"})
EVIDENCE_KEYS = frozenset(
    {
        "status",
        "task",
        "condition",
        "timed_out",
        "reward",
        "writes",
        "trigger",
        "first",
        "clean",
        "cuts",
    }
)
HEX64 = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
SHARD_NAME = re.compile(r"job-(\d{3})\.json", flags=re.ASCII)
WORLD_CLOCK = "world_clock_inconsistent"
FIXED_CLOCK_REASONS = CLOCK_REASONS - {WORLD_CLOCK}
NO_CLEAN_SEAT = "no_clean_seat"
SEAT_STATUS_UNKNOWN = "seat_status_unknown"
WORLD_TIME_INVALID = "world_time_invalid"
RUN_FIELDS = 4

assert DRIFTS[0] is None and all(type(drift) is int for drift in DRIFTS[1:])
assert all(
    type(a) is int and type(b) is int and 0 <= a < b <= MAX_DRIFT_MS
    for a, b in pairwise(DRIFTS[1:])
)
assert WORLD_CLOCK in CLOCK_REASONS and WORLD_TIME_INVALID in FIXED_CLOCK_REASONS
assert {UNANCHORED_REASON, FRESH_REASON} <= EXIT_REASONS
assert NO_CLEAN_SEAT in ABSENT_REASONS and SEAT_STATUS_UNKNOWN in UNKNOWN_REASONS
assert len(STOCK_PAIR) == len(CLEAN_COUNTS) - 1 and CHOICES == 35
assert MAX_JOBS <= 999 and MAX_SLOTS <= MAX_INPUTS
assert MAX_MANIFEST_BYTES <= MAX_SHARD_BYTES <= MAX_READ_BYTES
assert EVIDENCE not in UNSCORED

Point = tuple[str, int, int]
Entry = tuple[int, str]


@dataclass(frozen=True)
class Job:
    index: int
    slots: int
    state: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Pack:
    pack: str
    jobs: tuple[Job, ...]


@dataclass(frozen=True)
class _File:
    data: bytes
    digest: str
    identity: tuple[int, int]


@dataclass(frozen=True)
class _Seen:
    job: int
    certified: bool
    rows: tuple[dict[str, Any], ...]


class _Unreadable(Exception):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _pack_id(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or not canonical_uuid(value):
        return False
    return uuid.UUID(value).version == 4


def json_record(value: object) -> TypeGuard[dict[str, Any]]:
    return type(value) is dict


def _read_file(path: Path, limit: int, label: str) -> _File:
    assert 0 <= limit <= MAX_SHARD_BYTES
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with open(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            regular = stat.S_ISREG(info.st_mode)
            data = handle.read(limit + 1) if regular else b""
    except (OSError, ValueError):
        raise ValueError(f"{label} cannot be opened as a local file") from None
    _require(regular, f"{label} is not a regular local file")
    _require(len(data) <= limit, f"{label} exceeds the pack byte bounds")
    digest = hashlib.sha256(data).hexdigest()
    return _File(data, digest, (info.st_dev, info.st_ino))


def _parse(data: bytes) -> object:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, RecursionError):
        raise _Unreadable from None


def _grid(value: object) -> bool:
    if type(value) is not list or len(value) != len(DRIFTS):
        return False
    return all(
        type(found) is type(drift) and found == drift
        for found, drift in zip(value, DRIFTS, strict=True)
    )


def _entries(value: object) -> tuple[Entry, ...]:
    if type(value) is not list or not 1 <= len(value) <= MAX_JOBS:
        raise ValueError(f"manifest needs between 1 and {MAX_JOBS} jobs")
    entries: list[Entry] = []
    total = 0
    for index, entry in enumerate(value, start=1):
        if not json_record(entry) or entry.keys() != ENTRY_KEYS:
            raise ValueError(f"manifest job {index} does not have exactly job fields")
        job, slots, digest = entry["job"], entry["slots"], entry["sha256"]
        if type(job) is not int or job != index:
            raise ValueError("manifest jobs are not contiguous 1-based indices")
        if type(slots) is not int or not 1 <= slots <= MAX_SLOTS:
            raise ValueError(f"manifest job {index} has no bounded locked slot count")
        if type(digest) is not str or HEX64.fullmatch(digest) is None:
            raise ValueError(f"manifest job {index} hash is not lowercase SHA-256 hex")
        total += slots
        _require(total <= MAX_INPUTS, f"manifest registers over {MAX_INPUTS} slots")
        entries.append((slots, digest))
    digests = {digest for _, digest in entries}
    _require(len(digests) == len(entries), "manifest certifies one shard twice")
    return tuple(entries)


def _manifest(path: Path) -> tuple[str, tuple[Entry, ...], tuple[int, int]]:
    found = _read_file(path, MAX_MANIFEST_BYTES, "manifest")
    try:
        document = _parse(found.data)
    except _Unreadable:
        raise ValueError("manifest is not strict JSON") from None
    if not json_record(document) or document.keys() != MANIFEST_KEYS:
        raise ValueError("manifest does not have exactly the pack fields")
    _require(
        document["schema"] == MANIFEST_SCHEMA,
        "manifest is not a schedule-exit pack manifest",
    )
    pack = document["pack"]
    if not _pack_id(pack):
        raise ValueError("manifest pack id is not a canonical random UUID")
    _require(_grid(document["drift_ms"]), "manifest drift grid is not the frozen grid")
    entries = _entries(document["jobs"])
    assert 1 <= len(entries) <= MAX_JOBS
    return pack, entries, found.identity


def _check_fields(row: dict[str, Any], label: str) -> None:
    task, condition, writes = row["task"], row["condition"], row["writes"]
    _require(
        type(task) is str and HEX64.fullmatch(task) is not None,
        f"{label} task label is not 64 lowercase hex",
    )
    _require(
        type(condition) is str and condition in CONDITIONS,
        f"{label} condition is not talk or silent",
    )
    _require(type(row["timed_out"]) is bool, f"{label} timed_out is not a boolean")
    reward, trigger = row["reward"], row["trigger"]
    _require(type(reward) is int and reward in REWARDS, f"{label} reward is not 0 or 1")
    _require(
        type(writes) is list and len(writes) <= MAX_WRITES,
        f"{label} writes are not a bounded list",
    )
    _require(
        all(
            write is None or (type(write) is int and 0 <= write < CHOICES)
            for write in writes
        ),
        f"{label} write is not a choice token or null",
    )
    _require(
        type(trigger) is str and trigger in TRIGGERS, f"{label} trigger is unknown"
    )
    first, clean = row["first"], row["clean"]
    _require(
        first is None or (type(first) is str and first in STOCK_PAIR),
        f"{label} first seat is not p1, p2 or null",
    )
    _require(
        clean is None or (type(clean) is int and clean in CLEAN_COUNTS),
        f"{label} clean seat count is not 0, 1, 2 or null",
    )


def _check_outcome(row: dict[str, Any], label: str) -> None:
    writes, clean = row["writes"], row["clean"]
    _require(
        row["reward"] == 0 or (bool(writes) and writes[-1] is not None),
        f"{label} native success has no legal final write",
    )
    allowed = (0, 1, None) if row["timed_out"] else (2, None)
    _require(clean in allowed, f"{label} clean seat count disagrees with its timeout")


def _expand(cuts: object, count: int, label: str) -> tuple[Point, ...]:
    if type(cuts) is not list or not 1 <= len(cuts) <= len(DRIFTS):
        raise ValueError(f"{label} cuts are not a bounded run list")
    starts: list[int] = []
    values: list[Point] = []
    for run in cuts:
        if type(run) is not list or len(run) != RUN_FIELDS:
            raise ValueError(f"{label} cut run is not [start, reason, lo, hi]")
        start, reason, lo, hi = run
        if type(start) is not int or not (
            start == 0 if not starts else starts[-1] < start < len(DRIFTS)
        ):
            raise ValueError(
                f"{label} cut runs do not start at 0 with strictly increasing indices"
            )
        if type(reason) is not str or reason not in EXIT_REASONS:
            raise ValueError(f"{label} cut reason is not a stock exit reason")
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= count:
            raise ValueError(f"{label} cut prefix range lies outside the writes")
        value = (reason, lo, hi)
        _require(not values or values[-1] != value, f"{label} cut runs are redundant")
        starts.append(start)
        values.append(value)
    ends = [*starts[1:], len(DRIFTS)]
    points = tuple(
        value
        for start, end, value in zip(starts, ends, values, strict=True)
        for _ in range(end - start)
    )
    assert len(points) == len(DRIFTS)
    return points


def _check_observed(points: tuple[Point, ...], count: int, label: str) -> None:
    _require(
        points[0] == (UNANCHORED_REASON, 0, count),
        f"{label} observed trigger without clock alignment must allow every prefix",
    )
    grid = points[1:]
    head = grid[0][0]
    if head in FIXED_CLOCK_REASONS:
        _require(
            all(point == (head, 0, count) for point in grid),
            f"{label} drift-independent clock fallback changes across the grid",
        )
        _require(
            head != WORLD_TIME_INVALID or count > 0,
            f"{label} invalid world times need at least one write",
        )
        return
    fallback = sum(1 for _ in takewhile(lambda point: point[0] == WORLD_CLOCK, grid))
    _require(
        all(point == (WORLD_CLOCK, 0, count) for point in grid[:fallback]),
        f"{label} world clock fallback must allow every prefix",
    )
    _require(
        fallback == 0 or count > 0,
        f"{label} world clock fallback needs at least one write",
    )
    fresh = grid[fallback:]
    _require(
        all(reason == FRESH_REASON for reason, _, _ in fresh),
        f"{label} clock reasons are not a fallback followed by fresh bounds",
    )
    _require(
        count == 0 or all(lo < count for _, lo, _ in fresh),
        f"{label} fresh bounds certify the final write as earlier",
    )
    _require(
        all(a[1] >= b[1] and a[2] <= b[2] for a, b in pairwise(fresh)),
        f"{label} fresh bounds narrow as the clock allowance grows",
    )


def _check_trigger(row: dict[str, Any], points: tuple[Point, ...], label: str) -> None:
    trigger, first, clean = row["trigger"], row["first"], row["clean"]
    count = len(row["writes"])
    _require(
        (first is None) == (trigger != "observed"),
        f"{label} first seat disagrees with its trigger",
    )
    _require(
        (clean == 0) == (points[0][0] == NO_CLEAN_SEAT),
        f"{label} clean seat count disagrees with its exit reason",
    )
    if trigger == "observed":
        _check_observed(points, count, label)
        return
    if trigger == "absent":
        reasons, prefix = ABSENT_REASONS, (count, count)
    else:
        reasons, prefix = UNKNOWN_REASONS, (0, count)
    reason, lo, hi = points[0]
    _require(
        all(point == points[0] for point in points),
        f"{label} {trigger} trigger changes across the drift grid",
    )
    _require(
        reason in reasons and (lo, hi) == prefix,
        f"{label} {trigger} trigger has a reason or prefix of another status",
    )
    _require(
        reason != SEAT_STATUS_UNKNOWN or clean is None,
        f"{label} unknown seat status needs an unknown clean seat count",
    )


def _check_row(row: object, label: str) -> None:
    if not json_record(row) or "status" not in row:
        raise ValueError(f"{label} is not a pack row")
    status = row["status"]
    if status != EVIDENCE:
        _require(
            row.keys() == UNSCORED_KEYS and type(status) is str and status in UNSCORED,
            f"{label} is not a valid unscored row",
        )
        return
    _require(
        row.keys() == EVIDENCE_KEYS,
        f"{label} evidence row does not have exactly the evidence fields",
    )
    _check_fields(row, label)
    _check_outcome(row, label)
    points = _expand(row["cuts"], len(row["writes"]), label)
    _check_trigger(row, points, label)


def _shard_rows(
    document: dict[str, Any], job: int, slots: int
) -> tuple[dict[str, Any], ...]:
    _require(
        document.keys() == SHARD_KEYS,
        f"job {job} shard does not have exactly the shard fields",
    )
    rows = document["rows"]
    if type(rows) is not list or len(rows) != slots:
        raise ValueError(f"job {job} shard does not hold one row per locked slot")
    for slot, row in enumerate(rows, start=1):
        _check_row(row, f"job {job} slot {slot}")
    return tuple(rows)


def _header(document: object, label: str) -> tuple[dict[str, Any], str, int]:
    if not json_record(document) or document.get("schema") != SHARD_SCHEMA:
        raise ValueError(f"{label} is not a schedule-exit pack shard")
    pack, job = document.get("pack"), document.get("job")
    if not _pack_id(pack) or type(job) is not int:
        raise ValueError(f"{label} has a malformed shard header")
    return document, pack, job


def _named_job(path: Path) -> int | None:
    match = SHARD_NAME.fullmatch(path.name)
    return None if match is None else int(match.group(1))


def _attribute(
    found: _File, path: Path, label: str, pack: str, entries: tuple[Entry, ...]
) -> _Seen:
    certified = next(
        (job for job, (_, sha) in enumerate(entries, start=1) if sha == found.digest),
        None,
    )
    named = _named_job(path)
    try:
        document = _parse(found.data)
    except _Unreadable:
        _require(
            certified is None,
            f"the manifest certifies {label}, but it is not strict JSON",
        )
        _require(
            named is not None and 1 <= named <= len(entries),
            f"{label} is unreadable and its file name names no registered job",
        )
        assert named is not None
        return _Seen(named, False, ())
    shard, shard_pack, job = _header(document, label)
    _require(shard_pack == pack, f"{label} belongs to another pack")
    _require(1 <= job <= len(entries), f"{label} names a job outside the manifest")
    _require(named is None or named == job, f"{label} file name names another job")
    if certified is None:
        return _Seen(job, False, ())
    _require(certified == job, f"{label} carries the manifest hash of another job")
    return _Seen(job, True, _shard_rows(shard, job, entries[job - 1][0]))


def _intake(
    paths: list[Path], pack: str, entries: tuple[Entry, ...], manifest: tuple[int, int]
) -> list[list[_Seen]]:
    seen: list[list[_Seen]] = [[] for _ in entries]
    identities = {manifest}
    budget = MAX_READ_BYTES
    for position, path in enumerate(paths, start=1):
        label = f"shard input {position}"
        found = _read_file(path, min(MAX_SHARD_BYTES, budget), label)
        _require(
            found.identity not in identities,
            f"{label} repeats the manifest or another shard input",
        )
        identities.add(found.identity)
        budget -= len(found.data)
        assert budget >= 0
        item = _attribute(found, path, label, pack, entries)
        seen[item.job - 1].append(item)
    assert sum(len(found) for found in seen) == len(paths)
    return seen


def _job(index: int, slots: int, seen: list[_Seen]) -> Job:
    if not seen:
        return Job(index, slots, "missing_shard", ())
    if len(seen) > 1:
        return Job(index, slots, "duplicate_shard", ())
    only = seen[0]
    if not only.certified:
        return Job(index, slots, "changed_shard", ())
    assert len(only.rows) == slots
    return Job(index, slots, "present", only.rows)


def _check_tasks(jobs: tuple[Job, ...]) -> None:
    conditions: dict[str, str] = {}
    for job in jobs:
        for row in job.rows:
            if row["status"] != EVIDENCE:
                continue
            known = conditions.setdefault(row["task"], row["condition"])
            _require(
                known == row["condition"],
                f"job {job.index} reuses a task label under another condition",
            )
    assert len(conditions) <= MAX_INPUTS


def read_pack(manifest: Path, shards: list[Path]) -> Pack:
    if not isinstance(manifest, Path):
        raise ValueError("the manifest must be a pathlib.Path")
    if not isinstance(shards, list | tuple) or len(shards) > MAX_SHARD_FILES:
        raise ValueError(f"supply at most {MAX_SHARD_FILES} shard files")
    if not all(isinstance(path, Path) for path in shards):
        raise ValueError("every shard must be a pathlib.Path")
    pack, entries, identity = _manifest(manifest)
    seen = _intake(list(shards), pack, entries, identity)
    jobs = tuple(
        _job(index, slots, found)
        for index, ((slots, _), found) in enumerate(
            zip(entries, seen, strict=True), start=1
        )
    )
    _check_tasks(jobs)
    assert len(jobs) == len(entries)
    assert all(job.state in STATES for job in jobs)
    assert all((job.state == "present") == bool(job.rows) for job in jobs)
    assert sum(job.slots for job in jobs) <= MAX_INPUTS
    return Pack(pack, jobs)
