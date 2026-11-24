import re
from collections import Counter
from pathlib import Path
from typing import Any

from .job_members import JOB_LOCK_SCHEMA
from .native_records import read_record
from .schedule_changes import STATUSES
from .schedule_exit_clock import ExitPolicy, _seats, _trigger
from .schedule_jobs import (
    MAX_TOTAL,
    Member,
    _members,
    _plan,
    _real_directory,
    inspect_jobs,
)
from .work_trace import Receipt, ReceiptError, read_receipt

BOARDS_MS = (None, 1000)
SLOT_BASIS = "stock_behavior_slot_1_based"
PRODUCER_VERSION = "0.26.0"
PRODUCER_COMMIT = "6aeb609e31fd77e5f89ce3bcb018db8045520fb7"
SOURCE_KEYS = frozenset({"version", "git_commit_hash", "is_editable"})
PINNED = "pinned"
MEASURED = "measured"
TRIGGER_ABSENT = "trigger_absent"
TRIGGER_UNKNOWN = "trigger_unknown"
UNSUPPORTED = "unsupported_producer"
BINDING_MISSING = "binding_missing"
BINDING_INVALID = "binding_invalid"
BINDING_MISMATCH = "binding_mismatch"
RECEIPT_UNUSABLE = "receipt_unusable"
UNOBSERVED = {"absent": TRIGGER_ABSENT, "unknown": TRIGGER_UNKNOWN}
SCAN_STATUSES = frozenset(
    {
        MEASURED,
        TRIGGER_ABSENT,
        TRIGGER_UNKNOWN,
        UNSUPPORTED,
        BINDING_MISSING,
        BINDING_INVALID,
        BINDING_MISMATCH,
        RECEIPT_UNUSABLE,
    }
)
RECEIPT_CODE = re.compile(r"[a-z][a-z0-9_]{0,47}", flags=re.ASCII)
CASE_KEYS = (
    "condition",
    "timed_out",
    "native_grade",
    "trigger",
    "first_seat",
    "prefix",
    "grade",
    "later_writes",
)
PLAN_KEYS = (
    "registered_slots",
    "matched_slots",
    "missing_slots",
    "ambiguous_slots",
    "surplus_directories",
    "unassigned_directories",
)
MEMBERSHIP_CHANGED = "job membership changed during the scan"
BOARD = "board clock must be None or 1000 milliseconds"
TIMEOUTS = "include_timeouts must be a bool"


def _check_options(include_timeouts: object, board_ms: object) -> None:
    if type(include_timeouts) is not bool:
        raise ValueError(TIMEOUTS)
    if board_ms is None:
        return
    if type(board_ms) is not int or board_ms not in BOARDS_MS:
        raise ValueError(BOARD)


def _source(root: Path) -> bool | None:
    try:
        lock = read_record(root / "lock.json")
    except (OSError, ValueError, RecursionError):
        return None
    if lock is None:
        return None
    schema = lock.get("schema_version")
    if type(schema) is not int or schema != JOB_LOCK_SCHEMA:
        return None
    harbor = lock.get("harbor")
    if type(harbor) is not dict or harbor.keys() != SOURCE_KEYS:
        return None
    version, commit = harbor["version"], harbor["git_commit_hash"]
    if type(version) is not str or version != PRODUCER_VERSION:
        return None
    if type(commit) is not str or commit != PRODUCER_COMMIT:
        return None
    editable = harbor["is_editable"]
    return editable if type(editable) is bool else None


def _bind_plan(
    report: dict[str, Any], found: list[Member]
) -> tuple[list[Path], list[int]]:
    rows = report["jobs"]
    if len(rows) != len(found):
        raise ValueError(MEMBERSHIP_CHANGED)
    paths: list[Path] = []
    owners: list[int] = []
    for index, (member, row) in enumerate(zip(found, rows, strict=True), start=1):
        counts, behavior = _plan(member)
        start = len(paths)
        paths.extend(behavior)
        owners.extend([index] * len(behavior))
        span = list(range(start + 1, len(paths) + 1))
        if row["slot"] != index or row["job_id"] != member[0]:
            raise ValueError(MEMBERSHIP_CHANGED)
        if row["behavior_slots"] != span:
            raise ValueError(MEMBERSHIP_CHANGED)
        if any(row[key] != counts[key] for key in PLAN_KEYS):
            raise ValueError(MEMBERSHIP_CHANGED)
    if len(paths) != report["behavior"]["inputs"] or len(paths) > MAX_TOTAL:
        raise ValueError(MEMBERSHIP_CHANGED)
    assert len(owners) == len(paths)
    return paths, owners


def _usable_slots(trials: list[dict[str, Any]]) -> dict[str, int]:
    slots: dict[str, int] = {}
    for index, row in enumerate(trials, start=1):
        assert row["slot"] == index
        if row["status"] != "usable":
            continue
        identifier = row["native_trial"]
        assert isinstance(identifier, str) and identifier not in slots
        slots[identifier] = index
    return slots


def _bound_result(
    path: Path, stock: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any] | str:
    if not _real_directory(path):
        return BINDING_MISSING
    try:
        result = read_record(path / "result.json")
    except (OSError, ValueError, RecursionError):
        return BINDING_INVALID
    if result is None:
        return BINDING_MISSING
    if (
        result.get("id") != stock["native_trial"]
        or result.get("task_checksum") != stock["task_digest"]
        or result.get("trial_name") != path.name
        or (result.get("exception_info") is not None) != case["timed_out"]
    ):
        return BINDING_MISMATCH
    try:
        trigger, _, seat = _trigger(result, _seats(result))
    except (ValueError, TypeError):
        return BINDING_MISMATCH
    if trigger != "observed" or seat is None or seat.name != case["first_seat"]:
        return BINDING_MISMATCH
    return result


def _receipt_code(error: ReceiptError) -> str:
    code = str(error)
    if RECEIPT_CODE.fullmatch(code) is None or code in SCAN_STATUSES:
        return RECEIPT_UNUSABLE
    return code


def _meter(
    case: dict[str, Any], stock: dict[str, Any], path: Path, editable: bool | None
) -> tuple[str, Receipt | None]:
    trigger = case["trigger"]
    if trigger != "observed":
        return UNOBSERVED[trigger], None
    if editable is None:
        return UNSUPPORTED, None
    result = _bound_result(path, stock, case)
    if isinstance(result, str):
        return result, None
    try:
        receipt = read_receipt(path, result, case["first_seat"])
    except ReceiptError as error:
        return _receipt_code(error), None
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        return RECEIPT_UNUSABLE, None
    assert isinstance(receipt, Receipt)
    return MEASURED, receipt


def _cases(
    section: dict[str, Any],
    trials: list[dict[str, Any]],
    bound: tuple[list[Path], list[int]],
    sources: list[bool | None],
) -> list[dict[str, Any]]:
    paths, owners = bound
    slots = _usable_slots(trials)
    cases: list[dict[str, Any]] = []
    for row in section["trials"]:
        slot = slots[row["native_id"]]
        stock = trials[slot - 1]
        assert stock["native_trial"] == row["native_id"]
        assert stock["native_reward"] == row["native_grade"]
        job = owners[slot - 1]
        meter, receipt = _meter(row, stock, paths[slot - 1], sources[job - 1])
        assert meter in SCAN_STATUSES or RECEIPT_CODE.fullmatch(meter) is not None
        assert (meter == MEASURED) == (receipt is not None)
        case: dict[str, Any] = {"job": job, "slot": slot}
        for key in CASE_KEYS:
            value = row[key]
            case[key] = list(value) if isinstance(value, list) else value
        case["meter_status"] = meter
        case["receipt"] = receipt
        cases.append(case)
    cases.sort(key=lambda case: case["slot"])
    assert len(cases) == section["teams"]
    return cases


def _job_rows(
    report: dict[str, Any],
    sources: list[bool | None],
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trials = report["behavior"]["trials"]
    per_job = Counter(case["job"] for case in cases)
    rows: list[dict[str, Any]] = []
    for index, (row, editable) in enumerate(
        zip(report["jobs"], sources, strict=True), start=1
    ):
        counted = Counter(trials[slot - 1]["status"] for slot in row["behavior_slots"])
        assert set(counted) <= set(STATUSES)
        rows.append(
            {
                "job": index,
                "slots": row["registered_slots"],
                "missing_slots": row["missing_slots"],
                "ambiguous_slots": row["ambiguous_slots"],
                "statuses": {s: counted[s] for s in STATUSES if counted[s]},
                "source": PINNED if editable is not None else UNSUPPORTED,
                "editable": editable,
                "cases": per_job[index],
            }
        )
    assert sum(row["cases"] for row in rows) == len(cases)
    return rows


def scan(
    jobs: list[Path], *, include_timeouts: bool, board_ms: int | None
) -> dict[str, Any]:
    _check_options(include_timeouts, board_ms)
    found = _members(jobs)
    sources = [_source(path) for path in jobs]
    report = inspect_jobs(
        list(jobs),
        include_timeouts=include_timeouts,
        checkpoints=False,
        seat_exit=ExitPolicy(drift_ms=board_ms),
    )
    bound = _bind_plan(report, found)
    behavior = report["behavior"]
    section = behavior["seat_exit"]
    assert section["clock_error_ms"] == board_ms
    cases = _cases(section, behavior["trials"], bound, sources)
    summary = behavior["summary"]
    assert summary["includes_timed_out"] == include_timeouts
    return {
        "slot_basis": SLOT_BASIS,
        "include_timeouts": include_timeouts,
        "board_ms": board_ms,
        "registered": report["registered_slots"],
        "matched_slots": report["matched_slots"],
        "missing_slots": report["missing_slots"],
        "ambiguous_slots": report["ambiguous_slots"],
        "behavior_slots": behavior["inputs"],
        "statuses": dict(summary["statuses"]),
        "valid_native_grades": summary["valid_native_grades"],
        "graded_timeouts": summary["graded_timeouts"],
        "teams": section["teams"],
        "not_two_seats": section["not_two_seats"],
        "cohorts": section["cohorts"],
        "jobs": _job_rows(report, sources, cases),
        "cases": cases,
    }
