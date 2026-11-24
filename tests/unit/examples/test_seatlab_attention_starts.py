import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from examples.seatlab.workbench import input_witness
from examples.seatlab.workbench.attention_starts import findings
from examples.seatlab.workbench.input_witness import Witness, encode
from examples.seatlab.workbench.seat_stream import Malformed

JOB = str(uuid4())
OTHER_JOB = str(uuid4())
FINAL_A, FINAL_B, RETRY, STRAY = (str(uuid4()) for _ in range(4))


def slot(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


LEFT, RIGHT, THIRD, FOURTH = (slot(f"task__{n}") for n in ("left", "right", "c", "d"))
FINALS = {FINAL_A: LEFT, FINAL_B: RIGHT}


def store(directory: Path, trial: str, at: str, *, job: str = JOB) -> None:
    directory.mkdir(mode=0o700, exist_ok=True)
    found = Witness(job, at, trial, f"sha256:{'1' * 64}", f"sha256:{'2' * 64}")
    name, body = encode(found)
    path = directory / name
    path.write_bytes(body)
    path.chmod(0o600)


def archive(tmp_path: Path, started: dict[str, str]) -> Path:
    starts = tmp_path / "starts"
    starts.mkdir(mode=0o700)
    for trial, at in started.items():
        store(starts, trial, at)
    return starts


def run(starts: Path, **changes: Any) -> dict[str, int]:
    options: dict[str, Any] = {
        "registered": 2,
        "retries": 1,
        "finals": FINALS,
        "seat_slots": None,
        "account_slots": None,
        **changes,
    }
    registered, retries, finals = (
        options.pop(k) for k in ("registered", "retries", "finals")
    )
    return findings(JOB, registered, retries, finals, starts, **options)


def test_complete_nonfinal_execution_needs_no_final_grade(tmp_path: Path) -> None:
    every = {**FINALS, RETRY: LEFT}
    starts = archive(tmp_path, every)
    assert run(starts, seat_slots=every, account_slots=every) == {}


def test_unknown_retry_count_is_neither_zero_nor_a_second_finding(
    tmp_path: Path,
) -> None:
    starts = archive(tmp_path, {**FINALS, RETRY: LEFT, STRAY: RIGHT})
    assert run(starts, retries=None) == {}
    assert run(starts, retries=0) == {"start_receipts_over_expected": 2}


def test_matching_totals_still_expose_different_executions(tmp_path: Path) -> None:
    starts = archive(tmp_path, {FINAL_A: LEFT, RETRY: RIGHT, STRAY: LEFT})
    sources = {**FINALS, RETRY: RIGHT}
    assert run(starts, seat_slots=sources, account_slots=sources) == {
        "start_receipt_final_missing": 1,
        "start_receipt_without_seat_journal": 1,
        "seat_journal_without_start_receipt": 1,
        "start_receipt_without_account_attempt": 1,
        "account_attempt_without_start_receipt": 1,
    }


def test_each_slot_disagreement_is_reported_by_its_own_source(
    tmp_path: Path,
) -> None:
    starts = archive(tmp_path, {FINAL_A: RIGHT, FINAL_B: RIGHT, RETRY: LEFT})
    seats = {FINAL_A: RIGHT, FINAL_B: LEFT, RETRY: RIGHT}
    accounts = {FINAL_A: LEFT, FINAL_B: RIGHT, RETRY: LEFT}
    assert run(starts, seat_slots=seats, account_slots=accounts) == {
        "start_receipt_final_slot_mismatch": 1,
        "start_receipt_seat_slot_mismatch": 2,
        "start_receipt_account_slot_mismatch": 1,
    }


def test_known_coverage_is_compared_both_ways(tmp_path: Path) -> None:
    starts = archive(tmp_path, {FINAL_A: LEFT})
    assert run(starts) == {
        "start_receipt_final_missing": 1,
        "start_receipts_under_expected": 2,
    }
    assert run(starts, retries=None) == {"start_receipt_final_missing": 1}


def test_start_slots_beyond_enrollment(tmp_path: Path) -> None:
    starts = archive(tmp_path, {FINAL_A: LEFT, RETRY: THIRD, STRAY: FOURTH})
    finals = {FINAL_A: LEFT}
    assert run(starts, finals=finals) == {"start_receipt_slots_over_enrollment": 1}
    assert run(starts, finals=finals, registered=3, retries=0) == {}


def test_empty_requested_source_misses_every_start(tmp_path: Path) -> None:
    starts = archive(tmp_path, {**FINALS, RETRY: RIGHT})
    assert run(starts, seat_slots={}) == {"start_receipt_without_seat_journal": 3}
    assert run(tmp_path / "absent", seat_slots=FINALS, retries=0) == {
        "start_receipt_final_missing": 2,
        "start_receipts_under_expected": 2,
        "seat_journal_without_start_receipt": 2,
    }


def test_other_jobs_never_join(tmp_path: Path) -> None:
    starts = archive(tmp_path, {FINAL_A: LEFT})
    store(starts, FINAL_B, RIGHT, job=OTHER_JOB)
    store(starts, RETRY, LEFT, job=OTHER_JOB)
    assert run(starts, retries=0) == {
        "start_receipt_final_missing": 1,
        "start_receipts_under_expected": 1,
    }


def test_unsafe_archives_become_safe_malformed(tmp_path: Path) -> None:
    starts = archive(tmp_path, FINALS)
    (starts / "notes.txt").write_text("sk-live-secret")
    with pytest.raises(Malformed, match=input_witness.UNRELATED) as caught:
        run(starts)
    assert caught.value.__cause__ is None and str(tmp_path) not in str(caught.value)
    (starts / "notes.txt").unlink()
    starts.chmod(0o755)
    with pytest.raises(Malformed, match=input_witness.NOT_PRIVATE_DIR):
        run(starts)
    with pytest.raises(Malformed, match=input_witness.NOT_JOB):
        findings(
            JOB.upper(),
            2,
            1,
            FINALS,
            tmp_path / "absent",
            seat_slots=None,
            account_slots=None,
        )
