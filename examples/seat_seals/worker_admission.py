import os
from pathlib import Path
from typing import Any

from ..native_study import (
    DIGEST,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    require,
)
from ..seat_handoffs.native_records import read_record
from ..shift_board.plan import TICKET
from .admission import ISSUED, STATUS_SCHEMA, UNISSUED
from .collect import MODES
from .worker_layout import MAX_PATH_BYTES, MAX_RECORD_BYTES, assigned, digest

SCHEMA = "harbor.seat-seal.worker-admission/v1"
STATUS_FIELDS = frozenset({"schema", "plan", "registration", "counts", "tickets"})
ENTRY_FIELDS = frozenset({"ticket", "worker", "state"})
ADMISSIONS = (ISSUED, UNISSUED)
STATES = (
    "issued_with_grade",
    "issued_without_grade",
    "unissued_with_grade",
    "unissued_without_grade",
)
AFFECTED = ("issued_without_grade", "unissued_with_grade")
NOTICE = (
    "admission status is independent coordinator evidence; it neither authenticates"
    " owners nor proves exactly-once completion, and it never changes grades or"
    " bounds; issued without a grade is unknown, not failed; unissued is not proof"
    " that no other launcher ran; an unissued ticket with a grade disagrees with"
    " admission and keeps its grade"
)

Record = dict[str, Any]


def _read(path: Path) -> Record:
    require(isinstance(path, Path), "admission status path is not a Path")
    size = len(os.fsencode(path))
    require(0 < size <= MAX_PATH_BYTES, "admission status path exceeds its bound")
    try:
        record = read_record(path, MAX_RECORD_BYTES)
    except OSError as error:
        raise ValueError(f"admission status {path} is not a plain file") from error
    require(record is not None, f"admission status {path} does not exist")
    return as_record(record, "admission status", STATUS_FIELDS)


def load_status(path: Path, plan: Record) -> dict[str, str]:
    status = _read(path)
    require(status["schema"] == STATUS_SCHEMA, "admission status schema differs")
    found = as_text(status["plan"], "admission status plan digest", DIGEST)
    require(found == digest(plan), "admission status plan differs from the worker plan")
    found = as_text(status["registration"], "admission status registration", DIGEST)
    require(found == plan["registration"], "admission status registration differs")
    total = 2 * len(plan["blocks"])
    entries = as_list(status["tickets"], "admission status tickets", total, total)
    states: dict[str, str] = {}
    owners: dict[str, int] = {}
    previous = ""
    for value in entries:
        entry = as_record(value, "admission status ticket", ENTRY_FIELDS)
        ticket = as_text(entry["ticket"], "admission status ticket id", TICKET)
        require(ticket > previous, "admission status tickets are not strictly ordered")
        previous = ticket
        top = plan["workers"] - 1
        owners[ticket] = as_int(entry["worker"], "admission status worker", 0, top)
        states[ticket] = as_choice(entry["state"], "admission status state", ADMISSIONS)
    planned = {
        ticket: worker
        for worker in range(plan["workers"])
        for ticket in assigned(plan, worker)
    }
    require(owners == planned, "admission status tickets or workers differ")
    fields = frozenset(ADMISSIONS)
    counts = as_record(status["counts"], "admission status counts", fields)
    for admitted in ADMISSIONS:
        claimed = as_int(counts[admitted], f"admission status {admitted}", 0, total)
        tally = list(states.values()).count(admitted)
        require(claimed == tally, "admission status counts differ from its tickets")
    assert len(states) == total
    return states


def classify(report: Record, states: dict[str, str]) -> Record:
    slots = [block[mode] for block in report["blocks"] for mode in MODES]
    assert sorted(slot["ticket"] for slot in slots) == sorted(states)
    buckets: dict[str, list[str]] = {state: [] for state in STATES}
    for slot in slots:
        found = "with" if slot["grade"] is not None else "without"
        buckets[f"{states[slot['ticket']]}_{found}_grade"].append(slot["ticket"])
    counts = {state: len(buckets[state]) for state in STATES}
    assert sum(counts.values()) == len(slots)
    admitted = list(states.values())
    return {
        "schema": SCHEMA,
        "status": STATUS_SCHEMA,
        "admitted": {state: admitted.count(state) for state in ADMISSIONS},
        "counts": counts,
        "affected": {state: sorted(buckets[state]) for state in AFFECTED},
        "notice": NOTICE,
    }


def render(admission: Record) -> list[str]:
    issued, unissued = (admission["admitted"][state] for state in ADMISSIONS)
    counts = [admission["counts"][state] for state in STATES]
    return [
        f"admission tickets issued\t{issued}/{issued + unissued}",
        f"issued tickets with an eligible grade\t{counts[0]}/{issued}",
        f"issued tickets without a returned grade (unknown)\t{counts[1]}/{issued}",
        f"unissued tickets with a reported grade (disagree)\t{counts[2]}/{unissued}",
        f"unissued tickets without a returned grade\t{counts[3]}/{unissued}",
        f"admission notice\t{NOTICE}",
    ]
