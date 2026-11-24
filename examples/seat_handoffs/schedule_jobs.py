import os
import stat
from pathlib import Path
from typing import Any

from .job_members import MAX_SLOTS, members
from .schedule_changes import SCHEMA as BEHAVIOR_SCHEMA
from .schedule_changes import empty_report, inspect_changes
from .schedule_exit_clock import ExitPolicy

SCHEMA = "harbor.schedule-jobs/v1"
MAX_JOBS = 128
MAX_TOTAL = MAX_SLOTS

JOB_COUNT = f"supply between 1 and {MAX_JOBS} job roots"
JOB_TYPE = "every job root must be a pathlib.Path"
MALFORMED_ROOT = "job root is not a readable native job directory"
DUPLICATE_JOB = "job roots repeat a native job id"
TOO_MANY_SLOTS = f"job roots plan more than {MAX_TOTAL} slots in total"
TOO_MANY_CANDIDATES = f"job roots hold more than {MAX_TOTAL} candidate trials in total"

Member = tuple[str, dict[str, int], list[tuple[Path, str]], int]
COUNT_KEYS = (
    "registered_slots",
    "matched_slots",
    "missing_slots",
    "ambiguous_slots",
    "surplus_directories",
    "unassigned_directories",
    "usable_trials",
)


def _real_directory(path: Path) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except (OSError, ValueError):
        return False
    return stat.S_ISDIR(mode) and not stat.S_ISLNK(mode)


def _member(path: Path) -> Member:
    if not _real_directory(path):
        raise ValueError(MALFORMED_ROOT)
    try:
        job_id, planned, eligible, excluded = members(path)
    except (OSError, ValueError, RecursionError):
        raise ValueError(MALFORMED_ROOT) from None
    assert isinstance(job_id, str) and job_id
    assert all(type(count) is int and count >= 1 for count in planned.values())
    assert all(digest in planned for _, digest in eligible)
    return job_id, planned, eligible, len(excluded)


def _members(paths: list[Path]) -> list[Member]:
    if not isinstance(paths, list | tuple) or not 1 <= len(paths) <= MAX_JOBS:
        raise ValueError(JOB_COUNT)
    if not all(isinstance(path, Path) for path in paths):
        raise ValueError(JOB_TYPE)
    found: list[Member] = []
    seen: set[str] = set()
    slots = 0
    candidates = 0
    for path in paths:
        member = _member(path)
        job_id, planned, eligible, _ = member
        if job_id in seen:
            raise ValueError(DUPLICATE_JOB)
        seen.add(job_id)
        slots += sum(planned.values())
        candidates += len(eligible)
        if slots > MAX_TOTAL:
            raise ValueError(TOO_MANY_SLOTS)
        if candidates > MAX_TOTAL:
            raise ValueError(TOO_MANY_CANDIDATES)
        found.append(member)
    assert len(found) == len(paths) == len(seen)
    return found


def _plan(member: Member) -> tuple[dict[str, int], list[Path]]:
    _, planned, eligible, excluded = member
    by_digest: dict[str, list[Path]] = {digest: [] for digest in planned}
    for child, digest in eligible:
        by_digest[digest].append(child)
    counts = {key: 0 for key in COUNT_KEYS if key != "usable_trials"}
    behavior: list[Path] = []
    for digest, scheduled in planned.items():
        found = by_digest[digest]
        matched = min(len(found), scheduled)
        counts["registered_slots"] += scheduled
        counts["matched_slots"] += matched
        counts["missing_slots"] += scheduled - matched
        counts["surplus_directories"] += len(found) - matched
        if len(found) > scheduled:
            assert matched == scheduled
            counts["ambiguous_slots"] += scheduled
            continue
        assert matched == len(found)
        behavior.extend(found)
    counts["unassigned_directories"] = excluded
    assert len(behavior) == counts["matched_slots"] - counts["ambiguous_slots"]
    assert (
        counts["matched_slots"] + counts["missing_slots"] == counts["registered_slots"]
    )
    behavior.sort(key=lambda child: child.name)
    return counts, behavior


def _behavior(
    paths: list[Path],
    *,
    include_timeouts: bool,
    checkpoints: bool,
    seat_exit: ExitPolicy | None,
) -> dict[str, Any]:
    assert type(include_timeouts) is bool and type(checkpoints) is bool
    assert seat_exit is None or isinstance(seat_exit, ExitPolicy)
    if not paths:
        report = empty_report(
            include_timeouts=include_timeouts,
            checkpoints=checkpoints,
            seat_exit=seat_exit,
        )
        assert report["inputs"] == 0 and report["usable_trials"] == 0
        assert report["trials"] == []
    else:
        report = inspect_changes(
            paths,
            include_timeouts=include_timeouts,
            checkpoints=checkpoints,
            seat_exit=seat_exit,
        )
    assert ("checkpoints" in report) == checkpoints
    assert ("seat_exit" in report) == (seat_exit is not None)
    assert report["schema"] == BEHAVIOR_SCHEMA
    assert report["inputs"] == len(paths) == len(report["trials"])
    assert all(row["slot"] == index + 1 for index, row in enumerate(report["trials"]))
    return report


def _job_row(
    slot: int,
    job_id: str,
    counts: dict[str, int],
    behavior_slots: list[int],
    usable: int,
) -> dict[str, Any]:
    assert slot >= 1 and len(behavior_slots) == len(set(behavior_slots))
    assert 0 <= usable <= len(behavior_slots)
    assert len(behavior_slots) == counts["matched_slots"] - counts["ambiguous_slots"]
    assert (
        counts["matched_slots"] - counts["ambiguous_slots"]
        <= counts["registered_slots"]
    )
    return {
        "slot": slot,
        "job_id": job_id,
        **counts,
        "behavior_slots": behavior_slots,
        "usable_trials": usable,
    }


def _totals(rows: list[dict[str, Any]], behavior: dict[str, Any]) -> dict[str, int]:
    totals = {key: sum(row[key] for row in rows) for key in COUNT_KEYS}
    assert totals["usable_trials"] == behavior["usable_trials"]
    assert (
        totals["matched_slots"] + totals["missing_slots"] == totals["registered_slots"]
    )
    behavioral = totals["matched_slots"] - totals["ambiguous_slots"]
    assert 0 <= totals["usable_trials"] <= behavioral <= totals["registered_slots"]
    assert behavioral == behavior["inputs"]
    return totals


def inspect_jobs(
    paths: list[Path],
    *,
    include_timeouts: bool = False,
    checkpoints: bool = False,
    seat_exit: ExitPolicy | None = None,
) -> dict[str, Any]:
    if type(include_timeouts) is not bool:
        raise ValueError("include_timeouts must be a bool")
    if type(checkpoints) is not bool:
        raise ValueError("checkpoints must be a bool")
    if seat_exit is not None and not isinstance(seat_exit, ExitPolicy):
        raise ValueError("seat_exit must be an ExitPolicy or None")
    found = _members(paths)
    plans = [_plan(member) for member in found]
    joint: list[Path] = []
    spans: list[list[int]] = []
    for _, behavior_paths in plans:
        start = len(joint)
        joint.extend(behavior_paths)
        spans.append(list(range(start + 1, len(joint) + 1)))
    assert len(joint) <= MAX_TOTAL
    behavior = _behavior(
        joint,
        include_timeouts=include_timeouts,
        checkpoints=checkpoints,
        seat_exit=seat_exit,
    )
    trials = behavior["trials"]
    rows: list[dict[str, Any]] = []
    for index, (member, (counts, _), slots) in enumerate(
        zip(found, plans, spans, strict=True)
    ):
        usable = sum(1 for slot in slots if trials[slot - 1]["status"] == "usable")
        rows.append(_job_row(index + 1, member[0], counts, slots, usable))
    return {
        "schema": SCHEMA,
        "registered_jobs": len(rows),
        **_totals(rows, behavior),
        "jobs": rows,
        "behavior": behavior,
    }
