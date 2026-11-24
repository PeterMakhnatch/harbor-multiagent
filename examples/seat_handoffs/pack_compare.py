import json
from collections import Counter, defaultdict
from typing import Any

from .schedule_changes import MAX_INPUTS
from .schedule_jobs import MAX_JOBS

CASE_FIELDS = (
    "condition",
    "native_grade",
    "timed_out",
    "writes",
    "clean_seats",
    "trigger",
    "first_seat",
    "reason",
    "alignment",
    "prefix",
    "later_writes",
    "grade",
)


def _locations(stock: dict[str, Any]) -> dict[str, tuple[int, str]]:
    jobs, rows = stock["jobs"], stock["behavior"]["trials"]
    if len(jobs) > MAX_JOBS or len(rows) > MAX_INPUTS:
        raise ValueError("stock job membership exceeds the native limit")
    locations: dict[str, tuple[int, str]] = {}
    seen: set[int] = set()
    for index, job in enumerate(jobs, 1):
        if job["slot"] != index or len(job["behavior_slots"]) > MAX_INPUTS:
            raise ValueError("invalid stock job order or membership")
        for slot in job["behavior_slots"]:
            if type(slot) is not int or not 1 <= slot <= len(rows) or slot in seen:
                raise ValueError("ambiguous stock job membership")
            seen.add(slot)
            row = rows[slot - 1]
            if row["slot"] != slot:
                raise ValueError("stock reports use inconsistent trial positions")
            if row["status"] != "usable":
                continue
            native, task = row["native_trial"], row["task_digest"]
            if (
                not isinstance(native, str)
                or not isinstance(task, str)
                or native in locations
            ):
                raise ValueError("ambiguous stock case identity")
            locations[native] = (index, task)
    return locations


def _signature(job: int, case: dict[str, Any]) -> str:
    return json.dumps(
        [job, *(case[k] for k in CASE_FIELDS)],
        separators=(",", ":"),
        allow_nan=False,
    )


def _groups(cases: dict[str, list[str]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(sorted(signatures)) for signatures in cases.values())


def same_task_cases(stock: dict[str, Any], cold: list[dict[str, Any]]) -> bool:
    raw = stock["behavior"]["seat_exit"]["trials"]
    if not isinstance(raw, list) or len(raw) > MAX_INPUTS or len(raw) != len(cold):
        return False
    try:
        locations = _locations(stock)
        original: dict[str, list[str]] = defaultdict(list)
        released: dict[str, list[str]] = defaultdict(list)
        for row in raw:
            job, task = locations.pop(row["native_id"])
            original[task].append(_signature(job, row))
        for row in cold:
            released[row["task"]].append(_signature(row["job"], row))
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "stock file does not describe comparable case membership"
        ) from None
    return _groups(original) == _groups(released)
