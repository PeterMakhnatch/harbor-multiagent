from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .input_witness import read_starts
from .seat_stream import Malformed

ACTIONS = (
    "account_attempt_without_start_receipt",
    "seat_journal_without_start_receipt",
    "start_receipt_account_slot_mismatch",
    "start_receipt_final_missing",
    "start_receipt_final_slot_mismatch",
    "start_receipt_seat_slot_mismatch",
    "start_receipt_slots_over_enrollment",
    "start_receipt_without_account_attempt",
    "start_receipt_without_seat_journal",
    "start_receipts_over_expected",
    "start_receipts_under_expected",
)


def _join(
    started: Mapping[str, str],
    other: Mapping[str, str],
    unstarted: str,
    moved: str,
    unmatched: str | None = None,
) -> dict[str, int]:
    absent = conflicting = 0
    for trial, slot in other.items():
        start = started.get(trial)
        absent += start is None
        conflicting += start is not None and start != slot
    counts = {unstarted: absent, moved: conflicting}
    if unmatched is not None:
        counts[unmatched] = len(started) - (len(other) - absent)
    return counts


def findings(
    job: str,
    registered: int,
    retries: int | None,
    finals: Mapping[str, str],
    starts: Path,
    *,
    seat_slots: Mapping[str, str] | None,
    account_slots: Mapping[str, str] | None,
) -> dict[str, int]:
    try:
        started = {trial: w.slot for trial, w in read_starts(starts, job).items()}
    except ValueError as error:
        raise Malformed(str(error)) from None
    enrolled = set(finals.values())
    assert len(enrolled) == len(finals) <= registered
    found = _join(
        started,
        finals,
        "start_receipt_final_missing",
        "start_receipt_final_slot_mismatch",
    )
    found["start_receipt_slots_over_enrollment"] = len(
        set(started.values()) - enrolled
    ) - (registered - len(enrolled))
    if retries is not None:
        found["start_receipts_under_expected"] = registered + retries - len(started)
        found["start_receipts_over_expected"] = len(started) - registered - retries
    if seat_slots is not None:
        found |= _join(
            started,
            seat_slots,
            "seat_journal_without_start_receipt",
            "start_receipt_seat_slot_mismatch",
            "start_receipt_without_seat_journal",
        )
    if account_slots is not None:
        found |= _join(
            started,
            account_slots,
            "account_attempt_without_start_receipt",
            "start_receipt_account_slot_mismatch",
            "start_receipt_without_account_attempt",
        )
    assert found.keys() <= set(ACTIONS)
    return {code: count for code, count in sorted(found.items()) if count > 0}
