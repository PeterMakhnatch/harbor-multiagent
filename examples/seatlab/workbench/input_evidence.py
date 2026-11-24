import hashlib
import json
import os
import re
import stat
import warnings
from pathlib import Path
from typing import Any, NamedTuple

from . import native_account
from .input_keys import Cell, cell, result_view
from .job_stream import RESULT, _children, _present
from .seat_stream import (
    MAX_RESULT_BYTES,
    MIB,
    Malformed,
    Record,
    _read,
    _require,
    _strict_json,
)

LOCK = "lock.json"
JOB_LOCK_VERSION = 3
TRIAL_LOCK_VERSION = 2
MAX_TRIALS = 1024
MAX_JOB_LOCK_BYTES = 32 * MIB
MAX_TRIAL_LOCK_BYTES = 4 * MIB
MIN_SEATS = 2
JOB_LOCK_FIELDS = frozenset(
    {"schema_version", "created_at", "harbor", "n_concurrent_trials", "retry", "trials"}
)
GRADES = ("zero", "one", "other", "invalid", "unknown")
PIN = re.compile(r"sha256:[0-9a-f]{64}")
CAVEAT = (
    "Reads one stationary native job directory. Every file and directory read is checked again at the end; "
    "a change during the read refuses the job, but a change after it is not seen. A saved lock describes "
    "registered inputs, not what a model saw. A native trial lock holds no execution UUID."
)

PIN_SHAPE = "expected job lock pin is not sha256:<64 hex>"
PIN_MISMATCH = "job lock does not match the expected pin"
NOT_PATH = "job path is not a filesystem path"
NOT_DIRECTORY = "job is not a real non-symlink directory"
TOTAL = "job inputs exceed 256 MiB in total"
MOVED = "job input changed while it was read"
LOCK_JSON = "job lock is not strict UTF-8 JSON"
LOCK_OBJECT = "job lock is not a JSON object"
LOCK_FIELDS = "job lock has unknown fields"
LOCK_VERSION = "job lock is not a v3 job lock"
LOCK_TRIALS = "job lock trials is not a list of 1 to 1024 trial locks"
LOCK_COUNT = "job lock trial count disagrees with n_total_trials"
LOCK_NATIVE = "job lock fails native job-lock validation"
TRIAL_OBJECT = "trial lock is not a JSON object"
TRIAL_JSON = "trial lock is not strict UTF-8 JSON"
TRIAL_SIZE = "trial lock exceeds 4 MiB"
TRIAL_VERSION = "trial lock is not a v2 trial lock"
TRIAL_LOCAL = "trial lock is not a local task"
TRIAL_REGRADE = "trial lock regrades a source trial"
TRIAL_INSTALL = "trial lock is install-only"
TRIAL_TEAM = "trial lock is not a multi-seat team"
RESULT_JSON = "trial result is not strict UTF-8 JSON"
RESULT_TEAM = "trial result is not a multi-seat result"
DUPLICATE = "two final results share one execution UUID"
SURPLUS = "job holds more trial directories than registered trials"
UNREADABLE = "job evidence is unreadable"

Stamp = tuple[int, ...]


class Final(NamedTuple):
    execution: str
    cell: Cell | None
    agrees: bool | None
    grade: str


class Evidence(NamedTuple):
    job: str
    registered: tuple[Cell, ...]
    closed: bool | None
    retries: int | None
    lock_digest: str
    finals: tuple[Final, ...]
    lock_without_result: int


class _Reader:
    def __init__(self, remaining: int) -> None:
        self.remaining = remaining
        self.watched: list[tuple[str, Stamp]] = []
        self.cells: dict[bytes, Cell] = {}

    def watch(self, path: str, label: str) -> Stamp:
        found = _stamp(path, label)
        self.watched.append((path, found))
        return found

    def snapshot(self, path: str, limit: int, label: str) -> bytes:
        before = _stamp(path, label)
        _require(before[3] <= self.remaining, TOTAL)
        data = _read(path, limit, label)
        after = _stamp(path, label)
        _require(before == after and len(data) == before[3], MOVED)
        self.remaining -= len(data)
        self.watched.append((path, after))
        return data

    def cell(self, document: Any) -> Cell:
        _trial_shape(document)
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        _require(len(canonical) <= MAX_TRIAL_LOCK_BYTES, TRIAL_SIZE)
        digest = hashlib.sha256(canonical).digest()
        found = self.cells.get(digest)
        if found is None:
            found = cell(document)
            self.cells[digest] = found
        return found

    def unchanged(self) -> None:
        for path, before in self.watched:
            _require(_stamp(path, "job input") == before, MOVED)


def _stamp(path: str, label: str) -> Stamp:
    found: os.stat_result | None = None
    try:
        found = os.lstat(path)
    except (OSError, ValueError):
        pass
    if found is None:
        raise Malformed(f"{label} is missing or unreadable")
    return (
        found.st_dev,
        found.st_ino,
        found.st_mode,
        found.st_size,
        found.st_mtime_ns,
        found.st_ctime_ns,
    )


def _trial_shape(document: Any) -> None:
    _require(type(document) is dict, TRIAL_OBJECT)
    version = document.get("schema_version")
    _require(type(version) is int and version == TRIAL_LOCK_VERSION, TRIAL_VERSION)
    task = document.get("task")
    _require(type(task) is dict and task.get("type") == "local", TRIAL_LOCAL)
    _require(document.get("source_trial") is None, TRIAL_REGRADE)
    _require(document.get("install_only", False) is False, TRIAL_INSTALL)
    seats = document.get("participants")
    _require(type(seats) is list and len(seats) >= MIN_SEATS, TRIAL_TEAM)


def _native(data: bytes) -> None:
    from harbor.models.job.lock import JobLock

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            lock = JobLock.model_validate_json(data)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, Warning):
        lock = None
    _require(lock is not None and lock.schema_version == JOB_LOCK_VERSION, LOCK_NATIVE)


def _header(data: bytes) -> list[Any]:
    header = _strict_json(data, LOCK_JSON)
    _require(type(header) is dict, LOCK_OBJECT)
    _require(JOB_LOCK_FIELDS.issuperset(header), LOCK_FIELDS)
    version = header.get("schema_version")
    _require(type(version) is int and version == JOB_LOCK_VERSION, LOCK_VERSION)
    trials = header.get("trials")
    _require(type(trials) is list and 0 < len(trials) <= MAX_TRIALS, LOCK_TRIALS)
    return trials


def _grade(row: Record) -> str:
    state, reward = row["grade_state"], row["reward"]
    if state != "valid":
        _require(state in ("invalid", "unknown"), "trial result grade is unknown")
        return state
    if reward == 0:
        return "zero"
    return "one" if reward == 1 else "other"


def _final(reader: _Reader, job_id: str, folder: str, saved: Cell | None) -> Final:
    data = reader.snapshot(
        os.path.join(folder, RESULT), MAX_RESULT_BYTES, "trial result"
    )
    row = native_account.project_result(
        native_account._strict_json(data, RESULT_JSON),
        job_id=job_id,
        slot_name=os.path.basename(folder),
    )
    _require(len(row["seats"]) >= MIN_SEATS, RESULT_TEAM)
    view = result_view(_strict_json(data, RESULT_JSON), raw=data)
    agrees = None if saved is None or view is None else view == saved.view
    return Final(row["trial"], saved, agrees, _grade(row))


def _traverse(
    reader: _Reader, job: str, job_id: str, registered: tuple[Cell, ...]
) -> tuple[list[Final], int]:
    finals: list[Final] = []
    executions: set[str] = set()
    lock_only = 0
    for name in _children(job):
        folder = os.path.join(job, name)
        reader.watch(folder, "trial directory")
        has_lock = _present(os.path.join(folder, LOCK), "trial lock")
        if not _present(os.path.join(folder, RESULT), "trial result"):
            lock_only += 1 if has_lock else 0
            _require(len(finals) + lock_only <= len(registered), SURPLUS)
            continue
        _require(len(finals) + lock_only < len(registered), SURPLUS)
        saved = None
        if has_lock:
            data = reader.snapshot(
                os.path.join(folder, LOCK), MAX_TRIAL_LOCK_BYTES, "trial lock"
            )
            saved = reader.cell(_strict_json(data, TRIAL_JSON))
        final = _final(reader, job_id, folder, saved)
        _require(final.execution not in executions, DUPLICATE)
        executions.add(final.execution)
        finals.append(final)
    return sorted(finals, key=lambda final: final.execution), lock_only


def _read_job(path: Path, expected: str | None) -> Evidence:
    _require(isinstance(path, Path), NOT_PATH)
    pinned = type(expected) is str and PIN.fullmatch(expected) is not None
    _require(expected is None or pinned, PIN_SHAPE)
    job = os.fspath(path)
    reader = _Reader(native_account.MAX_TOTAL_BYTES)
    _require(stat.S_ISDIR(reader.watch(job, "job directory")[2]), NOT_DIRECTORY)
    data = reader.snapshot(os.path.join(job, LOCK), MAX_JOB_LOCK_BYTES, "job lock")
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    _require(expected is None or digest == expected, PIN_MISMATCH)
    trials = _header(data)
    reader.watch(os.path.join(job, RESULT), "job result")
    job_id, total, retries, closed, remaining = native_account._root(job)
    reader.remaining -= native_account.MAX_TOTAL_BYTES - remaining
    _require(reader.remaining >= 0, TOTAL)
    _require(len(trials) == total, LOCK_COUNT)
    _native(data)
    registered = tuple(reader.cell(entry) for entry in trials)
    del trials, data
    finals, lock_only = _traverse(reader, job, job_id, registered)
    reader.unchanged()
    return Evidence(
        job=job_id,
        registered=registered,
        closed=closed,
        retries=retries,
        lock_digest=digest,
        finals=tuple(finals),
        lock_without_result=lock_only,
    )


def read_job(path: Path, expected: str | None) -> Evidence:
    reason = UNREADABLE
    malformed = True
    try:
        return _read_job(path, expected)
    except Malformed as error:
        reason = str(error)
    except ValueError as error:
        malformed = False
        reason = str(error) if type(error) is ValueError else UNREADABLE
    except (OSError, RecursionError):
        reason = UNREADABLE
    if malformed:
        raise Malformed(reason)
    raise ValueError(reason)
