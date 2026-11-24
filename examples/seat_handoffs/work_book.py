import json
import os
import random
from collections import defaultdict
from collections.abc import Collection
from pathlib import Path
from typing import Any, TypeGuard

from .core import MAX_LOG_BYTES
from .job_members import MAX_SLOTS
from .native_records import read_record
from .schedule_changes import STATUSES
from .schedule_exit_bounds import TRIGGERS
from .schedule_jobs import MAX_JOBS
from .schedule_native import CONDITIONS, MAX_WRITES
from .work_jobs import (
    BOARDS_MS,
    MEASURED,
    PRODUCER_COMMIT,
    RECEIPT_CODE,
    SCAN_STATUSES,
    UNOBSERVED,
    scan,
)
from .work_meter import GUARDS, measure
from .work_trace import MAX_STEPS, MAX_TOKENS, REASONS, Receipt

SCHEMA = "harbor.post-return-work.book/v1"
BINS = (
    "before_5000",
    "before_1000_not_5000",
    "before_0_not_1000",
    "crossing_0",
    "after_0_not_1000",
    "after_1000_not_5000",
    "after_5000",
)
KINDS = ("prompt", "completion")
SIDES = ("before", "crossing", "after")
SEATS = ("p1", "p2")
METERS = frozenset(SCAN_STATUSES) | frozenset(REASONS)
MAX_BYTES = MAX_LOG_BYTES
TOP_KEYS = frozenset(
    {"schema", "producer", "include_timeouts", "guards_ms", "board_ms", "jobs"}
)
JOB_KEYS = frozenset(
    {"job", "slots", "missing_slots", "ambiguous_slots", "statuses", "rows"}
)
ROW_KEYS = frozenset(
    {"condition", "timed_out", "final", "trigger", "first", "boards", "meter", "work"}
)
BOARD_KEYS = frozenset({"grade", "later_writes"})
WORK_KEYS = frozenset({"bins", "native"})
ROSTER_KEYS = (
    "slot_basis",
    "include_timeouts",
    "registered",
    "matched_slots",
    "missing_slots",
    "ambiguous_slots",
    "behavior_slots",
    "statuses",
    "valid_native_grades",
    "graded_timeouts",
    "teams",
    "not_two_seats",
    "jobs",
)
FIXED_KEYS = ("condition", "timed_out", "native_grade", "trigger", "first_seat")
assert GUARDS == (0, 1000, 5000) and BOARDS_MS == (None, 1000)
assert all(RECEIPT_CODE.fullmatch(label) is not None for label in METERS)


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(f"invalid post-return work book: {what}")


def _int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _count(value: object, low: int, high: int, what: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"invalid post-return work book: {what}")
    return value


def _choice(value: object, options: Collection[str], what: str) -> str:
    if type(value) is not str or value not in options:
        raise ValueError(f"invalid post-return work book: {what}")
    return value


def _span(value: object, low: int, high: int) -> bool:
    if type(value) is not list or len(value) != 2:
        return False
    first = value[0]
    return type(first) is int and low <= first <= high and _int(value[1], first, high)


def _keyed(value: object, keys: frozenset[str]) -> TypeGuard[dict[str, Any]]:
    return type(value) is dict and value.keys() == keys


def _record(value: object, keys: frozenset[str], what: str) -> dict[str, Any]:
    if not _keyed(value, keys):
        raise ValueError(f"invalid post-return work book: {what} keys")
    return value


def _check_work(value: object) -> None:
    work = _record(value, WORK_KEYS, "work")
    native, bins = work["native"], work["bins"]
    _need(type(native) is list and len(native) == 2, "work native")
    _need(all(_int(count, 0, MAX_TOKENS) for count in native), "work native")
    _need(type(bins) is list and len(bins) == len(BINS), "work bins")
    for cell in bins:
        _need(type(cell) is list and len(cell) == 3, "work bin")
        _need(_int(cell[0], 0, MAX_STEPS), "work bin steps")
        _need(all(_int(count, 0, MAX_TOKENS) for count in cell[1:]), "bin tokens")
        _need(cell[0] > 0 or cell[1:] == [0, 0], "tokens without steps")
    _need(sum(cell[0] for cell in bins) <= MAX_STEPS, "work steps")
    for index in (1, 2):
        _need(sum(cell[index] for cell in bins) <= native[index - 1], "work total")


def _check_row(value: object, include_timeouts: bool) -> None:
    row = _record(value, ROW_KEYS, "row")
    _choice(row["condition"], CONDITIONS, "cond")
    _need(type(row["timed_out"]) is bool, "timed_out")
    _need(include_timeouts or not row["timed_out"], "timeout outside population")
    _need(_int(row["final"], 0, 1), "final grade")
    trigger = _choice(row["trigger"], TRIGGERS, "trigger")
    observed = trigger == "observed"
    absent = trigger == "absent"
    first = row["first"]
    _need(type(first) is str and first in SEATS if observed else first is None, "seat")
    boards = row["boards"]
    _need(type(boards) is list and len(boards) == len(BOARDS_MS), "boards")
    for item in boards:
        board = _record(item, BOARD_KEYS, "board")
        _need(_span(board["grade"], 0, 1), "board grade")
        _need(_span(board["later_writes"], 0, MAX_WRITES), "board later writes")
        _need(not absent or board["later_writes"] == [0, 0], "absent later writes")
        _need(not absent or board["grade"] == [row["final"]] * 2, "absent final grade")
    wide, fresh = boards[0]["grade"], boards[1]["grade"]
    _need(wide[0] <= fresh[0] and fresh[1] <= wide[1], "fresh grade outside unanchored")
    wide_writes, fresh_writes = (item["later_writes"] for item in boards)
    _need(
        wide_writes[0] <= fresh_writes[0] and fresh_writes[1] <= wide_writes[1],
        "fresh writes outside unanchored",
    )
    meter = _choice(row["meter"], METERS, "meter label")
    unobserved = UNOBSERVED.get(trigger)
    if unobserved is not None:
        _need(meter == unobserved, "meter")
    else:
        _need(meter not in UNOBSERVED.values(), "meter")
    if meter == MEASURED:
        _check_work(row["work"])
    else:
        _need(row["work"] is None, "work without measurement")


def _check_job(value: object, index: int, include_timeouts: bool) -> int:
    job = _record(value, JOB_KEYS, "job")
    statuses, rows = job["statuses"], job["rows"]
    _need(_int(job["job"], index, index), "job index")
    slots = _count(job["slots"], 0, MAX_SLOTS, "job slots")
    missing = _count(job["missing_slots"], 0, slots, "missing slots")
    ambiguous = _count(job["ambiguous_slots"], 0, slots, "ambiguous slots")
    _need(type(statuses) is dict and statuses.keys() <= set(STATUSES), "statuses")
    _need(all(_int(count, 1, slots) for count in statuses.values()), "status count")
    present = sum(statuses.values())
    _need(present + missing + ambiguous == slots, "slots")
    _need(type(rows) is list and len(rows) == statuses.get("usable", 0), "rows")
    for row in rows:
        _check_row(row, include_timeouts)
    return slots


def check(value: object) -> None:
    release = _record(value, TOP_KEYS, "release")
    _need(release["schema"] == SCHEMA, "schema")
    _need(release["producer"] == PRODUCER_COMMIT, "producer")
    include_timeouts = release["include_timeouts"]
    _need(type(include_timeouts) is bool, "include_timeouts")
    guards = release["guards_ms"]
    _need(type(guards) is list and all(type(g) is int for g in guards), "guards")
    _need(guards == list(GUARDS), "guards")
    boards = release["board_ms"]
    _need(type(boards) is list and boards == list(BOARDS_MS), "board clocks")
    _need(type(boards[1]) is int, "board clocks")
    jobs = release["jobs"]
    _need(type(jobs) is list and 1 <= len(jobs) <= MAX_JOBS, "jobs")
    total = sum(
        _check_job(job, index, include_timeouts)
        for index, job in enumerate(jobs, start=1)
    )
    _need(total <= MAX_SLOTS, "total slots")


def _cell(meter: dict[str, Any], side: str) -> tuple[int, int, int]:
    steps = meter["steps"][side]
    prompt = meter["prompt"][side]
    completion = meter["completion"][side]
    assert type(steps) is int and type(prompt) is int and type(completion) is int
    assert min(steps, prompt, completion) >= 0
    return steps, prompt, completion


def _less(outer: tuple[int, ...], inner: tuple[int, ...]) -> list[int]:
    cell = [a - b for a, b in zip(outer, inner, strict=True)]
    assert all(count >= 0 for count in cell)
    return cell


def _work(receipt: Receipt) -> dict[str, Any]:
    meters = [measure(receipt, guard) for guard in GUARDS]
    native = [meters[0][kind]["native"] for kind in KINDS]
    unplaced = [meters[0][kind]["unplaced"] for kind in KINDS]
    steps = sum(meters[0]["steps"][side] for side in SIDES)
    for meter in meters:
        assert sum(meter["steps"][side] for side in SIDES) == steps
        for kind, total, rest in zip(KINDS, native, unplaced, strict=True):
            tokens = meter[kind]
            assert tokens["native"] == total and tokens["unplaced"] == rest
            assert sum(tokens[side] for side in SIDES) + rest == total
            assert list(tokens["bounds"]) == [tokens["after"], total - tokens["before"]]
    before = [_cell(meter, "before") for meter in meters]
    after = [_cell(meter, "after") for meter in meters]
    bins = [
        list(before[2]),
        _less(before[1], before[2]),
        _less(before[0], before[1]),
        list(_cell(meters[0], "crossing")),
        _less(after[0], after[1]),
        _less(after[1], after[2]),
        list(after[2]),
    ]
    for index, rest in ((1, unplaced[0]), (2, unplaced[1])):
        assert sum(cell[index] for cell in bins) + rest == native[index - 1]
    return {"bins": bins, "native": native}


def _row(wide: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    meter = wide["meter_status"]
    return {
        "condition": wide["condition"],
        "timed_out": wide["timed_out"],
        "final": wide["native_grade"],
        "trigger": wide["trigger"],
        "first": wide["first_seat"],
        "boards": [
            {"grade": list(case["grade"]), "later_writes": list(case["later_writes"])}
            for case in (wide, fresh)
        ],
        "meter": meter,
        "work": _work(wide["receipt"]) if meter == MEASURED else None,
    }


def _joined(
    wide: dict[str, Any], fresh: dict[str, Any]
) -> dict[int, list[dict[str, Any]]]:
    for report in (wide, fresh):
        if report["not_two_seats"] != 0:
            raise ValueError("capture refuses cohorts that are not two-seat teams")
        if report["teams"] != len(report["cases"]):
            raise ValueError("scan teams disagree with its cases")
    if any(wide[key] != fresh[key] for key in ROSTER_KEYS):
        raise ValueError("board scans disagree on roster or statuses")
    paired = {(case["job"], case["slot"]): case for case in fresh["cases"]}
    if len(paired) != len(fresh["cases"]) or len(paired) != len(wide["cases"]):
        raise ValueError("board scans disagree on cases")
    rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for case in wide["cases"]:
        other = paired.pop((case["job"], case["slot"]), None)
        if other is None or any(case[key] != other[key] for key in FIXED_KEYS):
            raise ValueError("board scans disagree on a case")
        if case["meter_status"] != other["meter_status"]:
            raise ValueError("board scans disagree on a meter status")
        if case["receipt"] is not None and case["receipt"] is other["receipt"]:
            raise ValueError("board scans share one receipt read")
        if case["receipt"] != other["receipt"]:
            raise ValueError("board scans read different source receipts")
        rows[case["job"]].append(_row(case, other))
    assert not paired
    return rows


def capture(jobs: list[Path], *, include_timeouts: bool) -> dict[str, Any]:
    if type(include_timeouts) is not bool:
        raise ValueError("include_timeouts must be a bool")
    wide, fresh = (
        scan(list(jobs), include_timeouts=include_timeouts, board_ms=board)
        for board in BOARDS_MS
    )
    rows = _joined(wide, fresh)
    shuffle = random.SystemRandom()
    published = []
    for job in wide["jobs"]:
        own, counts = rows.pop(job["job"], []), job["statuses"]
        if len(own) != counts.get("usable", 0):
            raise ValueError("usable stock trials disagree with two-seat rows")
        shuffle.shuffle(own)
        published.append(
            {
                "job": job["job"],
                "slots": job["slots"],
                "missing_slots": job["missing_slots"],
                "ambiguous_slots": job["ambiguous_slots"],
                "statuses": {s: counts[s] for s in STATUSES if s in counts},
                "rows": own,
            }
        )
    assert not rows
    release = {
        "schema": SCHEMA,
        "producer": PRODUCER_COMMIT,
        "include_timeouts": include_timeouts,
        "guards_ms": list(GUARDS),
        "board_ms": list(BOARDS_MS),
        "jobs": published,
    }
    check(release)
    return release


def load(path: Path) -> dict[str, Any]:
    try:
        release = read_record(path, MAX_BYTES)
    except (OSError, ValueError) as error:
        raise ValueError("invalid post-return work book: unreadable file") from error
    if release is None:
        raise ValueError("post-return work book is missing")
    check(release)
    return release


def save(path: Path, release: dict[str, Any]) -> None:
    check(release)
    data = json.dumps(
        release, allow_nan=False, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    if len(data) + 1 > MAX_BYTES:
        raise ValueError("post-return work book exceeds its size bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with open(fd, "wb") as handle:
            handle.write(data + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
