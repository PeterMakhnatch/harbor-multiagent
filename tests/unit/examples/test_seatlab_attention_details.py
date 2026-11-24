import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.attempt_receipt import SEALED, project
from examples.seat_handoffs.attempt_receipt import validate as validate_receipt
from examples.seatlab.workbench import native_account
from examples.seatlab.workbench.account_contract import attempt, packet, seat
from examples.seatlab.workbench.account_history import _canonical
from examples.seatlab.workbench.account_keeper import encode
from examples.seatlab.workbench.attention_details import ACTIONS, analyze
from examples.seatlab.workbench.seat_stream import Malformed

ROOT = Path(__file__).resolve().parents[3]
INDEXED = ROOT / "examples/seatlab/results/seat-event-stream/native-jobs/retry/indexed"
CONTROL = ROOT / "examples/seat_handoffs/samples/confirm-stale.jsonl"
JOB = "a8af33b9-820a-48ff-a6e9-0e83e50a9d77"
FIRST = "d871136c10b543ea05ecb4315966ddd8b8f8eb225a7b927cb4726a8c88709615"
SECOND = "e2bedff937302090ed15e56a1d93491b59e35ec821d96549a243684c5d7e188f"
FINALS = {
    FIRST: "17dc6f76-ba03-4b13-bfc2-1c7e7d638a74",
    SECOND: "0f1b62bf-5c80-481a-98ad-51cc31992a67",
}
EARLIER = {
    FIRST: "c16573ef-2524-4643-ba8c-b64f236f309b",
    SECOND: "9086b777-d2be-4d57-b0ed-0965a4ed3bfe",
}
UNGRADED = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
ROLES = {"reporter": "p1", "booker": "p2"}
NATIVE_ROSTER = ("p1", "p2")
FINISHED = "2026-11-24T08:08:14.967150"
NOTHING: dict[str, Path | None] = {
    "seats": None,
    "handoffs": None,
    "accounts": None,
    "witness": None,
}
Json = dict[str, Any]


def check(result: dict[str, int]) -> dict[str, int]:
    assert list(result) == sorted(result) and set(result) <= set(ACTIONS)
    assert all(type(count) is int and count > 0 for count in result.values())
    exported = json.dumps(result)
    for private in (JOB, FIRST, SECOND, *FINALS.values(), *EARLIER.values(), "/"):
        assert private not in exported
    return result


def run(job: Path, final: Json, **requested: Path | None) -> dict[str, int]:
    return check(analyze(job, final, **{**NOTHING, **requested}))


def excerpt(trial: str) -> Json:
    return json.loads((INDEXED / "final" / f"{trial}.json").read_bytes())


def native_row(document: Json, slot: str) -> Json:
    config = {**document["config"], "trial_name": "excerpt"}
    named = {**document, "trial_name": "excerpt", "config": config}
    row = native_account.project_result(named, job_id=JOB, slot_name="excerpt")
    return attempt(row["trial"], slot, row["grade_state"], row["reward"], row["seats"])


def private(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    path.chmod(0o700)
    return path


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[Path, Json]:
    job = private(tmp_path, "job")
    shutil.copyfile(INDEXED / "job-result.json", job / "result.json")
    finals = [native_row(excerpt(FINALS[slot]), slot) for slot in (FIRST, SECOND)]
    return job, packet("native-job", JOB, 2, 2, finals, job_closed=True)


def archive_without(root: Path, *removed: str, journals: Json | None = None) -> Path:
    archive = private(root, "seats")
    for source in sorted((INDEXED / "archive").iterdir()):
        trial = source.name.split(".")[2]
        if trial in removed:
            continue
        target = archive / source.name
        shutil.copy2(source, target)
        if journals is not None and trial in journals:
            target.write_bytes(journals[trial](source.read_bytes()))
    return archive


def sealed_bytes(bind: bool) -> bytes:
    data = CONTROL.read_bytes()
    if not bind:
        return data
    for public, native in ROLES.items():
        data = data.replace(json.dumps(public).encode(), json.dumps(native).encode())
    return data


def receipt(
    root: Path,
    trial: str,
    slot: str,
    *,
    journal: bytes | None,
    roster: tuple[str, ...] = NATIVE_ROSTER,
    graded: bool = False,
) -> Json:
    control = root / "control" / "relay-sentinel__a1b2c3d"
    control.mkdir(parents=True)
    if journal is not None:
        (control / SEALED[0]).mkdir()
        control.joinpath(*SEALED).write_bytes(journal)
    digest = hashlib.sha256(os.fsencode(control.name)).hexdigest()
    grade, reward = ("valid", 1.0) if graded else ("unknown", None)
    row = attempt(trial, digest, grade, reward, [seat(name) for name in roster])
    kept = project(control, job=JOB, attempt=row)
    shutil.rmtree(control.parent)
    return validate_receipt({**kept, "slot": slot})


def keep(directory: Path, row: Json) -> str:
    name = f"{row['job']}.{row['slot']}.{row['trial']}.handoffs.json"
    (directory / name).write_bytes(_canonical(row) + b"\n")
    return name


def keep_account(directory: Path, row: Json) -> str:
    name, data = encode(JOB, row)
    (directory / name).write_bytes(data)
    return name


def native_job(root: Path, documents: list[Json], stats: Json) -> Path:
    job = private(root, "native")
    summary = {
        "id": JOB,
        "started_at": "2026-11-24T08:07:00.367349",
        "finished_at": FINISHED,
        "n_total_trials": 4,
        "stats": stats,
    }
    (job / "result.json").write_text(json.dumps(summary))
    for index, document in enumerate(documents):
        name = f"hello-seats__{index}"
        config = {**document["config"], "trial_name": name}
        (job / name).mkdir()
        (job / name / "result.json").write_text(
            json.dumps({**document, "trial_name": name, "config": config})
        )
    return job


def native_documents() -> list[Json]:
    invalid = {**excerpt(FINALS[SECOND]), "reward_valid": False}
    ungraded = {
        "id": UNGRADED,
        "schema_version": 1,
        "config": {
            "job_id": JOB,
            "participants": [{"seat": name} for name in NATIVE_ROSTER],
        },
    }
    return [excerpt(FINALS[FIRST]), invalid, ungraded]


def test_native_job_base_findings_need_no_instrumentation(tmp_path):
    stats = {"n_completed_trials": 4, "n_retries": 0}
    job = native_job(tmp_path, native_documents(), stats)
    final = native_account.build(str(job))
    assert run(job, final) == {
        "grade_invalid": 1,
        "grade_unknown": 1,
        "result_without_seat_results": 1,
        "slot_without_result": 1,
    }


def test_unreported_retries_mark_only_requested_capabilities_incomplete(tmp_path):
    job = native_job(tmp_path, native_documents(), {"n_completed_trials": 4})
    final = native_account.build(str(job))
    assert final["job_closed"] is True and final["retries"] is None
    base = run(job, final)
    assert "incomplete_native_retries_unreported" not in base
    accounts = private(tmp_path, "accounts")
    assert run(job, final, accounts=accounts) == {
        **base,
        "account_final_missing": 3,
        "incomplete_account_retry_coverage": 1,
        "incomplete_native_retries_unreported": 1,
    }
    seats = run(job, final, seats=private(tmp_path, "seats"))
    assert seats == {
        **base,
        "incomplete_native_retries_unreported": 1,
        "seat_journal_final_missing": 3,
        "seat_slot_without_journal": 4,
    }


def test_witnessed_native_seat_archive_needs_no_attention(indexed):
    job, final = indexed
    seats = INDEXED / "archive"
    assert run(job, final) == {}
    assert run(job, final, seats=seats) == {}
    assert run(job, final, seats=seats, witness=INDEXED / "host-witness.json") == {}


def test_job_reporting_only_n_trials_uses_the_closed_packet_counts(indexed):
    job, final = indexed
    summary = json.loads((job / "result.json").read_bytes())
    summary["stats"]["n_trials"] = summary["stats"].pop("n_completed_trials")
    (job / "result.json").write_text(json.dumps(summary))
    assert run(job, final, seats=INDEXED / "archive") == {}


def test_relabelled_witness_is_a_slot_mismatch(indexed):
    job, final = indexed
    witness = INDEXED / "challenge-witness.json"
    found = run(job, final, seats=INDEXED / "archive", witness=witness)
    assert found == {"witness_slot_mismatch": 1}


@pytest.mark.parametrize(
    ("removed", "expected"),
    [
        ((EARLIER[SECOND],), {"seat_journal_missing": 1}),
        (
            (FINALS[FIRST],),
            {"seat_journal_final_missing": 1, "seat_journal_missing": 1},
        ),
        (
            (FINALS[SECOND], EARLIER[SECOND]),
            {
                "seat_journal_final_missing": 1,
                "seat_journal_missing": 2,
                "seat_slot_without_journal": 1,
            },
        ),
    ],
    ids=["earlier", "final", "slot"],
)
def test_withheld_journals_are_missing_not_retryable(
    tmp_path, indexed, removed, expected
):
    job, final = indexed
    seats = archive_without(tmp_path, *removed)
    assert run(job, final, seats=seats) == expected
    witness = INDEXED / "host-witness.json"
    witnessed = run(job, final, seats=seats, witness=witness)
    assert witnessed == {**expected, "seat_journal_missing_witnessed": len(removed)}


def test_unfinished_journals_stay_distinct(tmp_path, indexed):
    job, final = indexed
    journals = {
        EARLIER[FIRST]: lambda data: b"".join(data.splitlines(keepends=True)[:3]),
        EARLIER[SECOND]: lambda data: b"",
    }
    seats = archive_without(tmp_path, journals=journals)
    assert run(job, final, seats=seats) == {
        "seat_journal_open": 1,
        "seat_journal_without_header": 1,
    }


def test_final_journal_in_another_slot_is_a_slot_mismatch(tmp_path, indexed):
    job, final = indexed
    by_trial = {row["trial"]: row for row in final["attempts"]}
    moved = {**by_trial[FINALS[FIRST]], "slot": SECOND}
    other = {**by_trial[FINALS[SECOND]], "slot": FIRST}
    swapped = packet("native-job", JOB, 2, 2, [moved, other], job_closed=True)
    assert run(job, swapped, seats=INDEXED / "archive") == {
        "seat_journal_final_slot_mismatch": 2
    }


def renamed_final(final: Json) -> Json:
    document = excerpt(FINALS[FIRST])
    for entry in document["seat_results"]:
        if entry["seat"] == "p2":
            entry["seat"] = "p3"
    renamed = native_row(document, FIRST)
    rest = [row for row in final["attempts"] if row["trial"] != renamed["trial"]]
    assert len(rest) == 1 and renamed["trial"] == FINALS[FIRST]
    return packet("native-job", JOB, 2, 2, [renamed, *rest], job_closed=True)


def test_final_roster_absent_from_its_journal_header_is_not_cleared(tmp_path, indexed):
    job, final = indexed
    renamed = renamed_final(final)
    seats = INDEXED / "archive"
    assert run(job, renamed) == {}
    assert run(job, renamed, seats=seats) == {"seat_journal_final_roster_mismatch": 1}
    handoffs = private(tmp_path, "handoffs")
    assert run(job, renamed, seats=seats, handoffs=handoffs) == {
        "handoff_receipt_missing": 4,
        "seat_journal_final_roster_mismatch": 1,
    }
    headless = archive_without(tmp_path, journals={FINALS[FIRST]: lambda data: b""})
    assert run(job, renamed, seats=headless) == {"seat_journal_without_header": 1}


@pytest.mark.parametrize(
    ("kept", "expected"),
    [
        ([], {"handoff_receipt_missing": 4}),
        (
            [(FINALS[FIRST], FIRST, True, NATIVE_ROSTER)],
            {"handoff_receipt_missing": 3},
        ),
        (
            [
                (FINALS[FIRST], FIRST, True, NATIVE_ROSTER),
                (EARLIER[SECOND], SECOND, None, NATIVE_ROSTER),
            ],
            {"handoff_receipt_missing": 2, "handoff_receipt_unavailable": 1},
        ),
        (
            [(FINALS[FIRST], FIRST, False, ("booker", "reporter"))],
            {"handoff_receipt_missing": 3, "handoff_receipt_roster_mismatch": 1},
        ),
        (
            [(FINALS[FIRST], SECOND, True, NATIVE_ROSTER)],
            {"handoff_receipt_missing": 3, "handoff_receipt_slot_mismatch": 1},
        ),
    ],
    ids=["none", "sealed", "unavailable", "roster", "slot"],
)
def test_seat_bound_handoffs_keep_missing_unsealed_and_mismatch_apart(
    tmp_path, indexed, kept, expected
):
    job, final = indexed
    handoffs = private(tmp_path, "handoffs")
    for trial, slot, bind, roster in kept:
        journal = None if bind is None else sealed_bytes(bind)
        keep(handoffs, receipt(tmp_path, trial, slot, journal=journal, roster=roster))
    assert run(job, final, seats=INDEXED / "archive", handoffs=handoffs) == expected


def test_only_unpublished_handoff_staging_needs_attention(tmp_path, indexed):
    job, final = indexed
    handoffs = private(tmp_path, "handoffs")
    row = receipt(tmp_path, FINALS[FIRST], FIRST, journal=sealed_bytes(True))
    name = keep(handoffs, row)
    (handoffs / f".{name}.{'0' * 32}.tmp").write_bytes(b"")
    seats = INDEXED / "archive"
    assert run(job, final, seats=seats, handoffs=handoffs) == {
        "handoff_receipt_missing": 3
    }
    unpublished = f".{JOB}.{SECOND}.{EARLIER[SECOND]}.handoffs.json.{'1' * 32}.tmp"
    (handoffs / unpublished).write_bytes(b"")
    assert run(job, final, seats=seats, handoffs=handoffs) == {
        "handoff_receipt_missing": 3,
        "handoff_staging_unpublished": 1,
    }


def test_handoffs_without_seat_archive_use_packet_finals(tmp_path, indexed):
    job, final = indexed
    handoffs = private(tmp_path, "handoffs")
    assert run(job, final, handoffs=handoffs) == {"handoff_receipt_missing": 4}
    graded = receipt(
        tmp_path, FINALS[FIRST], FIRST, journal=sealed_bytes(True), graded=True
    )
    keep(handoffs, graded)
    keep(handoffs, receipt(tmp_path, EARLIER[FIRST], FIRST, journal=None))
    assert run(job, final, handoffs=handoffs) == {
        "handoff_receipt_missing": 2,
        "handoff_receipt_unavailable": 1,
    }


def test_final_receipt_disagreeing_with_native_grade_is_rejected(tmp_path, indexed):
    job, final = indexed
    handoffs = private(tmp_path, "handoffs")
    keep(handoffs, receipt(tmp_path, FINALS[FIRST], FIRST, journal=None))
    with pytest.raises(Malformed):
        analyze(job, final, **{**NOTHING, "handoffs": handoffs})


def earlier_account(slot: str, *, priced: bool) -> Json:
    usd = ("0.01", "0") if priced else (None, None)
    seats = [seat(name, harness_usd=u) for name, u in zip(NATIVE_ROSTER, usd)]
    return attempt(EARLIER[slot], slot, "unknown", None, seats)


def test_account_archive_counts_missing_unpriced_and_unconfirmed(tmp_path, indexed):
    job, final = indexed
    accounts = private(tmp_path, "accounts")
    for row in final["attempts"]:
        keep_account(accounts, row)
    assert run(job, final, accounts=accounts) == {"account_attempt_missing": 2}
    for slot in (FIRST, SECOND):
        keep_account(accounts, earlier_account(slot, priced=False))
    assert run(job, final, accounts=accounts) == {"account_attempt_unpriced": 2}
    for slot in (FIRST, SECOND):
        keep_account(accounts, earlier_account(slot, priced=True))
    assert run(job, final, accounts=accounts) == {}
    changed = json.loads(json.dumps(final["attempts"][1]))
    changed["seats"][0]["harness_usd"] = "0.5"
    keep_account(accounts, changed)
    assert run(job, final, accounts=accounts) == {
        "account_attempt_final_unconfirmed": 1,
        "account_final_mismatch": 1,
    }
    staging = f".{JOB}.{FIRST}.{UNGRADED}.json.{'2' * 32}.tmp"
    (accounts / staging).write_bytes(b"")
    assert run(job, final, accounts=accounts) == {
        "account_attempt_final_unconfirmed": 1,
        "account_final_mismatch": 1,
        "account_staging_unpublished": 1,
    }


def test_open_or_replaced_jobs_and_unseated_witness_are_rejected(tmp_path, indexed):
    job, final = indexed
    witness = INDEXED / "host-witness.json"
    for closure in (False, None):
        reopened = {**final, "job_closed": closure}
        with pytest.raises(Malformed):
            analyze(job, reopened, **NOTHING)
    with pytest.raises(Malformed):
        analyze(job, final, **{**NOTHING, "witness": witness})
    summary = json.loads((job / "result.json").read_bytes())
    summary["stats"]["n_retries"] = 3
    (job / "result.json").write_text(json.dumps(summary))
    with pytest.raises(Malformed):
        analyze(job, final, **NOTHING)
