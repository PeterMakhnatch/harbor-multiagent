from __future__ import annotations

import json

from ..seatlab.workbench.account_contract import DIGEST
from ..seatlab.workbench.seat_stream import UUID, Record
from .attempt_receipt import SOURCES
from .attempt_report import MAX_RECEIPTS, SKIPPED, _receipts

STATUSES = (
    *SOURCES,
    "no_handoff_capture",
    "slot_disagrees",
    "roster_disagrees",
    "roster_unknown",
    "handoff_only",
)
SLOTTED = ("slot_disagrees", "handoff_only")
CAVEAT = (
    "Joins handoff receipts to the validated seat archive by job, slot digest, execution UUID and the exact sorted "
    "seat roster of the journal header. A receipt counts only under the seat archive's own slot and roster; the "
    "same execution under another slot is slot_disagrees, another roster is roster_disagrees, a journal without a "
    "header is roster_unknown and a receipt without a seat journal is handoff_only. no_handoff_capture means no "
    "receipt was retained; neither it nor a missing closing row shows that the seats did not communicate. complete "
    "needs a finished job with more than zero expected executions, each with a sealed receipt bound in this way, "
    "no extra receipt and no unpublished staging file. Receipt grades and rewards are not read; this is coverage, "
    "not a grade or job success."
)


def _status(slot: str | None, roster: list[str] | None, row: Record | None) -> str:
    if row is None:
        return "no_handoff_capture"
    if slot is None:
        return "handoff_only"
    if row["slot"] != slot:
        return "slot_disagrees"
    if roster is None:
        return "roster_unknown"
    return row["source"] if row["roster"] == roster else "roster_disagrees"


def join(
    *,
    job: str,
    executions: dict[str, str],
    rosters: dict[str, list[str] | None],
    expected: int | None,
    handoffs: str,
) -> Record:
    assert type(job) is str and UUID.fullmatch(job) is not None
    assert len(executions) <= MAX_RECEIPTS and rosters.keys() == executions.keys()
    assert all(UUID.fullmatch(e) and DIGEST.fullmatch(s) for e, s in executions.items())
    assert all(r is None or (r and r == sorted(set(r))) for r in rosters.values())
    assert expected is None or (type(expected) is int and expected >= 0)
    rows, skipped = _receipts(job, handoffs)
    entries: list[Record] = []
    for execution in sorted(executions.keys() | rows.keys()):
        row = rows.get(execution)
        status = _status(executions.get(execution), rosters.get(execution), row)
        assert row is not None or status == "no_handoff_capture"
        handoff_slot = row["slot"] if row is not None and status in SLOTTED else None
        entries.append(
            {"execution": execution, "status": status, "handoff_slot": handoff_slot}
        )
    counts = dict.fromkeys(STATUSES, 0)
    for entry in entries:
        counts[entry["status"]] += 1
    assert sum(counts.values()) == len(entries) <= len(executions) + len(rows)
    assert counts["handoff_only"] == len(rows.keys() - executions.keys())
    assert counts["no_handoff_capture"] == len(executions.keys() - rows.keys())
    complete = (
        expected is not None
        and expected > 0
        and len(entries) == len(executions) == expected
        and counts["sealed"] == expected
        and skipped["staging_unpublished"] == 0
    )
    assert set(skipped) == set(SKIPPED)
    return {
        "complete": complete,
        "counts": counts,
        "skipped": skipped,
        "executions": entries,
        "caveat": CAVEAT,
    }


def text(coverage: Record) -> list[str]:
    lines = [f"handoff_complete: {json.dumps(coverage['complete'])}"]
    lines += [f"  {name}: {count}" for name, count in coverage["counts"].items()]
    lines += [f"  {name}: {count}" for name, count in coverage["skipped"].items()]
    for entry in coverage["executions"]:
        where = (
            "" if entry["handoff_slot"] is None else f" in slot {entry['handoff_slot']}"
        )
        lines.append(f"handoff {entry['execution']}: {entry['status']}{where}")
    return [*lines, coverage["caveat"]]
