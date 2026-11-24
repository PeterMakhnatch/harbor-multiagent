import os
from collections import Counter
from pathlib import Path
from typing import Any, cast

from .core import canonical_uuid
from .native_records import read_record, record_digest

MAX_SLOTS = 4096
MAX_ENTRIES = 2 * MAX_SLOTS + 64

JOB_RESULT_SCHEMA = 1
JOB_LOCK_SCHEMA = 3
TRIAL_LOCK_SCHEMA = 2
TRIAL_RECORD_SCHEMA = 1

SYMLINK = "symbolic link; not followed"
SPECIAL = "not a regular directory"
UNREADABLE = "native membership record is unreadable"
UNRECORDED = "no native membership record; not attributed"
RECORD_SCHEMA = "native membership record is not schema version 1"
MALFORMED = "native membership fields are missing or malformed; not attributed"
INCOHERENT = "native config and result disagree on membership"
FOREIGN = "native membership names another job"
RENAMED = "native trial name does not match the directory"
LOCK_MISSING = "native trial lock is missing"
LOCK_UNREADABLE = "native trial lock is unreadable"
LOCK_SCHEMA = "native trial lock is not schema version 2"
LOCK_UNPLANNED = "native trial lock matches no planned trial"


def _schema(record: dict[str, Any], version: int) -> bool:
    found = record.get("schema_version")
    return type(found) is int and found == version


def _root(path: Path) -> tuple[str, dict[str, int]]:
    result = read_record(path / "result.json")
    if result is None:
        raise FileNotFoundError("job result.json is missing")
    if not _schema(result, JOB_RESULT_SCHEMA):
        raise ValueError("job result.json is not schema version 1")
    job_id, total = result.get("id"), result.get("n_total_trials")
    if not canonical_uuid(job_id):
        raise ValueError("job result.json has no canonical job id")
    if type(total) is not int or not 0 <= total <= MAX_SLOTS:
        raise ValueError(f"job n_total_trials is not an integer in 0..{MAX_SLOTS}")
    lock = read_record(path / "lock.json")
    if lock is None:
        raise FileNotFoundError("job lock.json is missing")
    if not _schema(lock, JOB_LOCK_SCHEMA):
        raise ValueError("job lock.json is not schema version 3")
    trials = lock.get("trials")
    if not isinstance(trials, list) or len(trials) != total:
        raise ValueError("job lock trials do not match n_total_trials")
    if not all(
        isinstance(trial, dict) and _schema(trial, TRIAL_LOCK_SCHEMA)
        for trial in trials
    ):
        raise ValueError("job lock has a trial lock that is not schema version 2")
    planned = Counter(record_digest(trial) for trial in trials)
    assert sum(planned.values()) == total
    return cast(str, job_id), dict(planned)


def _entries(path: Path) -> list[os.DirEntry[str]]:
    entries: list[os.DirEntry[str]] = []
    with os.scandir(path) as listing:
        for entry in listing:
            if len(entries) == MAX_ENTRIES:
                raise ValueError(f"job directory has more than {MAX_ENTRIES} entries")
            entries.append(entry)
    entries.sort(key=lambda entry: entry.name)
    return entries


def _claims(
    config: dict[str, Any] | None, result: dict[str, Any] | None
) -> tuple[list[object], list[object]] | None:
    assert config is not None or result is not None
    job_ids: list[object] = []
    names: list[object] = []
    if config is not None:
        job_ids.append(config.get("job_id"))
        names.append(config.get("trial_name"))
    if result is not None:
        nested = result.get("config")
        if not isinstance(nested, dict):
            return None
        job_ids.append(nested.get("job_id"))
        names.append(nested.get("trial_name"))
        names.append(result.get("trial_name"))
    if not all(canonical_uuid(job_id) for job_id in job_ids):
        return None
    if not all(isinstance(name, str) and name for name in names):
        return None
    return job_ids, names


def _membership(child: Path, job_id: str) -> str | None:
    try:
        config = read_record(child / "config.json")
        result = read_record(child / "result.json")
    except (OSError, ValueError):
        return UNREADABLE
    if config is None and result is None:
        return UNRECORDED
    records = [record for record in (config, result) if record is not None]
    if not all(_schema(record, TRIAL_RECORD_SCHEMA) for record in records):
        return RECORD_SCHEMA
    claims = _claims(config, result)
    if claims is None:
        return MALFORMED
    job_ids, names = claims
    if len(set(job_ids)) != 1 or len(set(names)) != 1:
        return INCOHERENT
    if job_ids[0] != job_id:
        return FOREIGN
    if names[0] != child.name:
        return RENAMED
    return None


def _planned(child: Path, planned: dict[str, int]) -> tuple[str | None, str | None]:
    try:
        lock = read_record(child / "lock.json")
        if lock is None:
            return None, LOCK_MISSING
        if not _schema(lock, TRIAL_LOCK_SCHEMA):
            return None, LOCK_SCHEMA
        digest = record_digest(lock)
    except (OSError, ValueError):
        return None, LOCK_UNREADABLE
    if digest not in planned:
        return None, LOCK_UNPLANNED
    return digest, None


def members(
    path: Path,
) -> tuple[str, dict[str, int], list[tuple[Path, str]], list[dict[str, str]]]:
    job_id, planned = _root(path)
    eligible: list[tuple[Path, str]] = []
    excluded: list[dict[str, str]] = []
    for entry in _entries(path):
        if entry.is_symlink():
            excluded.append({"directory": entry.name, "reason": SYMLINK})
            continue
        if entry.is_file(follow_symlinks=False):
            continue
        if not entry.is_dir(follow_symlinks=False):
            excluded.append({"directory": entry.name, "reason": SPECIAL})
            continue
        child = path / entry.name
        reason = _membership(child, job_id)
        digest = None
        if reason is None:
            digest, reason = _planned(child, planned)
        if reason is not None:
            excluded.append({"directory": entry.name, "reason": reason})
            continue
        assert digest is not None
        eligible.append((child, digest))
    assert len(eligible) + len(excluded) <= MAX_ENTRIES
    return job_id, planned, eligible, excluded
