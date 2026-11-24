import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .job_stream import JOURNAL, _present
from .seat_stream import (
    UUID,
    Record,
    _require,
    compare_roster,
    decode_snapshot,
    read_journal_bytes,
    result_roster,
)
from .team_timing_math import Window

CLOCKS = ("result", "journal")
MEASURED = "measured"
NO_ROSTER = "no_roster"
FEWER_THAN_TWO = "fewer_than_two"
MISSING_INTERVALS = "missing_intervals"
INVALID_CLOCK = "invalid_clock"
NO_JOURNAL = "no_journal"
INCOMPLETE_JOURNAL = "incomplete_journal"
JOURNAL_CONFLICT = "journal_conflict"
CODES = (
    MEASURED,
    NO_ROSTER,
    FEWER_THAN_TWO,
    MISSING_INTERVALS,
    INVALID_CLOCK,
    NO_JOURNAL,
    INCOMPLETE_JOURNAL,
    JOURNAL_CONFLICT,
)
MAX_TIMESTAMP = 64
NS_PER_US = 1000
STAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?"
    r"(?:(Z)|([+-])(\d{2}):(\d{2}))",
    flags=re.ASCII,
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ONE_US = timedelta(microseconds=1)

Bounds = tuple[int | None, int | None]


class _Invalid(Exception):
    pass


def _nanos(value: object) -> int:
    if type(value) is not str or len(value) > MAX_TIMESTAMP:
        raise _Invalid
    match = STAMP.fullmatch(value)
    if match is None:
        raise _Invalid
    *fields, fraction, zulu, sign, hours, minutes = match.groups()
    digits = fraction or ""
    if digits[6:].strip("0"):
        raise _Invalid
    if zulu is None and int(minutes) >= 60:
        raise _Invalid
    shift = (
        timedelta(0)
        if zulu is not None
        else (1 if sign == "+" else -1)
        * timedelta(hours=int(hours), minutes=int(minutes))
    )
    try:
        moment = datetime(*(int(field) for field in fields), tzinfo=timezone(shift))
        micros = (moment - EPOCH) // ONE_US
    except (ValueError, OverflowError) as error:
        raise _Invalid from error
    return (micros + int(digits[:6].ljust(6, "0"))) * NS_PER_US


def _bounds(record: Record) -> Bounds:
    container = record.get("agent_execution")
    if container is None:
        return None, None
    if type(container) is not dict:
        raise _Invalid
    start, end = (
        None if container.get(name) is None else _nanos(container[name])
        for name in ("started_at", "finished_at")
    )
    if start is not None and end is not None and end < start:
        raise _Invalid
    return start, end


def _inside(point: int | None, frame: Bounds) -> bool:
    low, high = frame
    if point is None:
        return True
    return (low is None or low <= point) and (high is None or point <= high)


def _from_result(document: Record, rows: list[Record]) -> tuple[str, list[Window]]:
    found: list[Window] = []
    missing = False
    try:
        frame = _bounds(document)
        for row in rows:
            start, end = _bounds(row)
            if not (_inside(start, frame) and _inside(end, frame)):
                raise _Invalid
            if start is None or end is None:
                missing = True
                continue
            found.append((row["seat"], start, end))
    except _Invalid:
        return INVALID_CLOCK, []
    return (MISSING_INTERVALS, []) if missing else (MEASURED, found)


def _from_journal(
    journal: Path,
    trial: str,
    roster: dict[str, tuple[bool, str | None]],
    selected: list[str],
) -> tuple[str, list[Window]]:
    if not _present(str(journal), "journal"):
        return NO_JOURNAL, []
    report = decode_snapshot(read_journal_bytes(str(journal)))
    if report["trial"] is None:
        return INCOMPLETE_JOURNAL, []
    if compare_roster(report, trial, roster)["counts"]["conflict"]:
        return JOURNAL_CONFLICT, []
    if report["state"] != "closed":
        return INCOMPLETE_JOURNAL, []
    assert not report["trailing_partial"]
    seats = {seat["name"]: seat for seat in report["seats"]}
    found: list[Window] = []
    for name in selected:
        start, end = seats[name]["enter_elapsed_ns"], seats[name]["end_elapsed_ns"]
        if start is None or end is None:
            return INCOMPLETE_JOURNAL, []
        assert type(start) is int and type(end) is int and 0 <= start <= end
        found.append((name, start, end))
    return MEASURED, found


def windows(
    document: dict[str, Any],
    trial: str,
    declared: list[str] | None,
    journal: Path,
    *,
    clock: str,
    under_test: bool,
) -> tuple[str, list[Window]]:
    assert clock in CLOCKS and type(under_test) is bool
    assert type(trial) is str and UUID.fullmatch(trial) is not None
    assert isinstance(journal, Path) and journal.name == JOURNAL
    assert declared is None or (
        type(declared) is list and all(type(name) is str for name in declared)
    )
    _require(type(document) is dict, "trial result is not a JSON object")
    _require(document.get("id") == trial, "trial result id is not the retained trial")
    if document.get("seat_results") is None:
        return NO_ROSTER, []
    bound, roster = result_roster(document)
    assert bound == trial
    if declared is not None:
        _require(
            len(set(declared)) == len(declared) and set(declared) == roster.keys(),
            "configured participants do not match the native seat roster",
        )
    selected = [
        name for name, (tested, _) in roster.items() if tested or not under_test
    ]
    if len(selected) < 2:
        return FEWER_THAN_TWO, []
    if clock == "result":
        chosen = set(selected)
        rows = [row for row in document["seat_results"] if row["seat"] in chosen]
        code, found = _from_result(document, rows)
    else:
        code, found = _from_journal(journal, trial, roster, selected)
    assert code in CODES
    assert (code == MEASURED) == bool(found)
    assert not found or [name for name, _, _ in found] == selected
    return code, found
