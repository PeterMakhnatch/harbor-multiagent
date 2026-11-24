import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.workbench import attention, attention_details, attention_starts
from examples.seatlab.workbench.account_contract import packet
from examples.seatlab.workbench.input_witness import NOT_PRIVATE_DIR, Witness, encode
from tests.unit.examples.test_seatlab_attention_details import (
    EARLIER,
    FINALS,
    FIRST,
    INDEXED,
    JOB,
    SECOND,
    earlier_account,
    excerpt,
    keep_account,
    native_row,
    private,
)
from tests.unit.examples.test_seatlab_attention_spool import (
    JOB_A,
    JOB_B,
    TRIALS,
    native_job,
    run,
    watch,
    write_spool,
)

INPUT = "sha256:" + "1" * 64
BINDING = "sha256:" + "2" * 64
OTHER_SLOT = "3" * 64
Json = dict[str, Any]


@pytest.fixture(autouse=True)
def foreign_cwd(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))


def slot_of(job_id: str) -> str:
    return hashlib.sha256(os.fsencode(f"dispatch__{job_id[:7]}")).hexdigest()


def keep_start(directory: Path, job: str, trial: str, slot: str) -> None:
    name, body = encode(Witness(job, slot, trial, INPUT, BINDING))
    path = directory / name
    path.write_bytes(body)
    path.chmod(0o600)


def counted_retries(job: Path, retries: int) -> None:
    result = job / "result.json"
    envelope = json.loads(result.read_bytes())
    envelope["stats"]["n_retries"] = retries
    result.write_text(json.dumps(envelope), encoding="utf-8")


def closed_job(root: Path) -> Path:
    job = native_job(root, "jobs/a", JOB_A, "closed")
    counted_retries(job, 0)
    return job


def report(spool: Path, capsys) -> tuple[int, Json]:
    code, out, err = run(spool, capsys, "--json")
    assert err == ""
    return code, json.loads(out)


def baseline(root: Path, capsys) -> dict[str, int]:
    spool = write_spool(root, [{"job": "../jobs/a"}])
    code, found = report(spool, capsys)
    (row,) = found["jobs"]
    assert row["status"] == "closed" and "starts" not in row["checks"]
    assert found["caveat"] == attention.CAVEAT
    assert code == (1 if row["findings"] else 0)
    assert not set(row["findings"]) & set(attention_starts.ACTIONS)
    return row["findings"]


def test_old_rows_keep_their_shape_and_text(tmp_path, capsys):
    closed_job(tmp_path)
    before = baseline(tmp_path, capsys)
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "seats": "../seats"}])
    private(tmp_path, "seats")

    code, found = report(spool, capsys)

    (row,) = found["jobs"]
    assert row["checks"].keys() == {"native_result", *attention.CAPABILITIES}
    assert set(row["findings"]) - set(before) <= set(attention_details.ACTIONS)
    assert not set(row["findings"]) & set(attention_starts.ACTIONS)
    assert found["caveat"] == attention.CAVEAT
    text = run(spool, capsys)[1]
    assert attention.STARTS_CAVEAT not in text and "starts" not in text
    assert code == 1


def test_matching_start_adds_no_finding(tmp_path, capsys):
    closed_job(tmp_path)
    before = baseline(tmp_path, capsys)
    starts = private(tmp_path, "starts")
    keep_start(starts, JOB_A, TRIALS[JOB_A], slot_of(JOB_A))
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "starts": "../starts"}])

    code, found = report(spool, capsys)

    (row,) = found["jobs"]
    assert row["findings"] == before
    assert row["checks"]["starts"] == "requested"
    assert found["caveat"] == f"{attention.CAVEAT} {attention.STARTS_CAVEAT}"
    assert code == (1 if before else 0)
    text = run(spool, capsys)[1]
    assert "starts" in text.splitlines()[2]
    assert str(tmp_path) not in text and TRIALS[JOB_A] not in text


@pytest.mark.parametrize("created", [True, False], ids=["empty", "never-created"])
def test_closed_row_missing_start_needs_attention(tmp_path, capsys, created):
    closed_job(tmp_path)
    before = baseline(tmp_path, capsys)
    if created:
        private(tmp_path, "starts")
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "starts": "../starts"}])

    code, found = report(spool, capsys)

    (row,) = found["jobs"]
    assert code == 1 and row["outcome"] == "needs_attention"
    assert row["findings"] == {
        **before,
        "start_receipt_final_missing": 1,
        "start_receipts_under_expected": 1,
    }
    text = run(spool, capsys)[1]
    assert "start_receipt_final_missing" in text
    assert str(tmp_path) not in text and slot_of(JOB_A) not in text


def test_closed_row_with_contradictory_start_needs_attention(tmp_path, capsys):
    closed_job(tmp_path)
    before = baseline(tmp_path, capsys)
    starts = private(tmp_path, "starts")
    keep_start(starts, JOB_A, TRIALS[JOB_A], OTHER_SLOT)
    keep_start(starts, JOB_B, TRIALS[JOB_B], slot_of(JOB_A))
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "starts": "../starts"}])

    code, found = report(spool, capsys)

    (row,) = found["jobs"]
    assert code == 1
    assert row["findings"] == {
        **before,
        "start_receipt_final_slot_mismatch": 1,
        "start_receipt_slots_over_enrollment": 1,
    }
    assert found["actions"] == row["findings"]
    out = run(spool, capsys)[1]
    assert OTHER_SLOT not in out and JOB_B not in out


def test_unknown_retries_are_not_counted_twice(tmp_path, capsys):
    native_job(tmp_path, "jobs/a", JOB_A, "closed")
    starts = private(tmp_path, "starts")
    keep_start(starts, JOB_A, TRIALS[JOB_A], slot_of(JOB_A))
    plain = baseline(tmp_path, capsys)
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "starts": "../starts"}])

    found = report(spool, capsys)[1]

    (row,) = found["jobs"]
    assert row["retries"] is None
    assert row["findings"] == {**plain, "incomplete_native_retries_unreported": 1}


def test_invalid_start_archive_is_rejected_without_private_paths(tmp_path, capsys):
    closed_job(tmp_path)
    starts = private(tmp_path, "starts")
    keep_start(starts, JOB_A, TRIALS[JOB_A], slot_of(JOB_A))
    starts.chmod(0o750)
    spool = write_spool(tmp_path, [{"job": "../jobs/a", "starts": "../starts"}])

    code, out, err = run(spool, capsys, "--json")

    assert (code, out) == (2, "")
    assert err == f"Attention input rejected: spool row 0 {NOT_PRIVATE_DIR}.\n"
    (starts / os.listdir(starts)[0]).chmod(0o644)
    starts.chmod(0o700)
    code, out, err = run(spool, capsys)
    assert (code, out) == (2, "")
    assert err.startswith("Attention input rejected: spool row 0 start receipt")
    assert str(tmp_path) not in err and TRIALS[JOB_A] not in err


def test_open_and_absent_rows_never_read_start_archives(tmp_path, capsys, monkeypatch):
    native_job(tmp_path, "jobs/b", JOB_B, "open")
    root = tmp_path / "starts"
    keep_start(private(tmp_path, "starts"), JOB_B, TRIALS[JOB_B], slot_of(JOB_B))
    rows = [
        {"job": "../jobs/b", "starts": "../starts"},
        {"job": "../jobs/gone", "starts": "../starts", "seats": "../seats"},
    ]
    spool = write_spool(tmp_path, rows)
    touched = watch(monkeypatch, root)

    code, found = report(spool, capsys)

    assert touched == []
    assert code == 3 and found["status"] == "outstanding"
    checks = {row["status"]: row["checks"] for row in found["jobs"]}
    assert checks["open"]["starts"] == checks["absent"]["starts"] == "not_run"
    assert all(row["findings"] == {} for row in found["jobs"])
    text = run(spool, capsys)[1]
    assert touched == []
    assert "not run: native_result, starts" in text
    assert "not run: native_result, seats, starts" in text


def indexed_job(root: Path) -> tuple[Path, Json]:
    job = private(root, "job")
    shutil.copyfile(INDEXED / "job-result.json", job / "result.json")
    finals = [native_row(excerpt(FINALS[slot]), slot) for slot in (FIRST, SECOND)]
    return job, packet("native-job", JOB, 2, 2, finals, job_closed=True)


def kept_accounts(root: Path, final: Json) -> Path:
    accounts = private(root, "accounts")
    for row in final["attempts"]:
        keep_account(accounts, row)
    for slot in (FIRST, SECOND):
        keep_account(accounts, earlier_account(slot, priced=True))
    return accounts


def every_start(root: Path) -> Path:
    starts = private(root, "starts")
    for slot in (FIRST, SECOND):
        keep_start(starts, JOB, FINALS[slot], slot)
        keep_start(starts, JOB, EARLIER[slot], slot)
    return starts


def test_retained_seats_accounts_and_starts_agree_without_findings(tmp_path):
    job, final = indexed_job(tmp_path)
    sources = {
        "seats": INDEXED / "archive",
        "accounts": kept_accounts(tmp_path, final),
        "handoffs": None,
        "witness": None,
    }
    starts = every_start(tmp_path)
    assert attention_details.analyze(job, final, **sources) == {}
    assert attention_details.analyze(job, final, starts=starts, **sources) == {}

    next(starts.glob(f"*.{EARLIER[SECOND]}.start.json")).unlink()
    assert attention_details.analyze(job, final, starts=starts, **sources) == {
        "account_attempt_without_start_receipt": 1,
        "seat_journal_without_start_receipt": 1,
        "start_receipts_under_expected": 1,
    }
    keep_start(starts, JOB, EARLIER[SECOND], FIRST)
    found = attention_details.analyze(job, final, starts=starts, **sources)
    assert found == {
        "start_receipt_account_slot_mismatch": 1,
        "start_receipt_seat_slot_mismatch": 1,
    }
