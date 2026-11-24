import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.attempt_receipt import SEALED, project, validate
from examples.seatlab.workbench import attempt_history
from examples.seatlab.workbench.account_contract import attempt, seat
from examples.seatlab.workbench.account_history import _canonical

ROOT = Path(__file__).resolve().parents[3]
INDEXED = ROOT / "examples/seatlab/results/seat-event-stream/native-jobs/retry/indexed"
CONTROL = ROOT / "examples/seat_handoffs/samples/confirm-stale.jsonl"
JOB = "a8af33b9-820a-48ff-a6e9-0e83e50a9d77"
FIRST = "d871136c10b543ea05ecb4315966ddd8b8f8eb225a7b927cb4726a8c88709615"
SECOND = "e2bedff937302090ed15e56a1d93491b59e35ec821d96549a243684c5d7e188f"
FINAL = "17dc6f76-ba03-4b13-bfc2-1c7e7d638a74"
EXECUTIONS = {
    FINAL: FIRST,
    "c16573ef-2524-4643-ba8c-b64f236f309b": FIRST,
    "0f1b62bf-5c80-481a-98ad-51cc31992a67": SECOND,
    "9086b777-d2be-4d57-b0ed-0965a4ed3bfe": SECOND,
}
FOREIGN_JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
UNSEEN = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
PUBLIC_ROLES = ("booker", "reporter")
TEST_ROLE_BINDING = {"reporter": "p1", "booker": "p2"}
TEST_CONTROL_SLOT = "relay-sentinel__a1b2c3d"
STATUSES = (
    "sealed",
    "world_prefix",
    "unavailable",
    "unusable",
    "no_handoff_capture",
    "slot_disagrees",
    "roster_disagrees",
    "roster_unknown",
    "handoff_only",
)
Json = dict[str, Any]


def run(capsys: pytest.CaptureFixture[str], *args: object) -> tuple[int, Json]:
    code = attempt_history.main([*map(str, args), "--json"])
    return code, json.loads(capsys.readouterr().out)


def private(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    path.chmod(0o700)
    return path


def bind_test_roles(bind_roles: bool) -> tuple[bytes, tuple[str, ...]]:
    data = CONTROL.read_bytes()
    if not bind_roles:
        return data, PUBLIC_ROLES
    for public, native in TEST_ROLE_BINDING.items():
        quoted = json.dumps(public).encode()
        assert quoted in data
        data = data.replace(quoted, json.dumps(native).encode())
    assert all(json.dumps(role).encode() not in data for role in PUBLIC_ROLES)
    return data, tuple(sorted(TEST_ROLE_BINDING.values()))


def derive_test_sealed(
    root: Path, *, job: str, slot: str, trial: str, bind_roles: bool = True
) -> Json:
    journal, roles = bind_test_roles(bind_roles)
    control = root / "test-control" / TEST_CONTROL_SLOT
    (control / SEALED[0]).mkdir(parents=True)
    control.joinpath(*SEALED).write_bytes(journal)
    digest = hashlib.sha256(os.fsencode(control.name)).hexdigest()
    row = attempt(trial, digest, "unknown", None, [seat(name) for name in roles])
    receipt = project(control, job=job, attempt=row)
    shutil.rmtree(control.parent)
    assert receipt["source"] == "sealed" and receipt["roster"] == sorted(roles)
    assert {e["sender"] for e in receipt["edges"]} | {
        e["receiver"] for e in receipt["edges"]
    } == set(roles)
    return validate({**receipt, "slot": slot})


def keep(directory: Path, receipt: Json) -> str:
    name = f"{receipt['job']}.{receipt['slot']}.{receipt['trial']}.handoffs.json"
    (directory / name).write_bytes(_canonical(receipt) + b"\n")
    return name


def counts(**nonzero: int) -> Json:
    return {name: nonzero.get(name, 0) for name in STATUSES}


def derived_job(root: Path, *, total: int) -> Path:
    document = json.loads((INDEXED / "job-result.json").read_bytes())
    document["n_total_trials"] = total
    document["stats"].update(n_retries=0, n_completed_trials=total)
    path = root / "test-derived-job-result.json"
    path.write_text(json.dumps(document))
    return path


def one_execution_archive(root: Path, journal: bytes | None) -> Path:
    archive = root / "archive"
    archive.mkdir()
    name = f"{JOB}.{FIRST}.{FINAL}.jsonl"
    if journal is None:
        shutil.copy2(INDEXED / "archive" / name, archive / name)
    else:
        (archive / name).write_bytes(journal)
    return archive


def test_published_history_without_handoff_receipts_is_incomplete(tmp_path, capsys):
    job, archive = INDEXED / "job-result.json", INDEXED / "archive"
    witness = INDEXED / "host-witness.json"
    code, legacy = run(capsys, job, archive, "--witness", witness)
    assert code == 0 and legacy == json.loads((INDEXED / "witnessed.json").read_bytes())
    handoffs = private(tmp_path, "handoffs")
    code, report = run(
        capsys, job, archive, "--witness", witness, "--handoffs", handoffs
    )
    assert code == 1
    coverage = report.pop("handoff_coverage")
    assert report == {**legacy, "complete": False}
    assert report["coverage_complete"] is True and report["witness"]["agrees"] is True
    assert coverage["complete"] is False
    assert coverage["counts"] == counts(no_handoff_capture=4)
    assert coverage["skipped"] == {
        "other_job": 0,
        "staging_published": 0,
        "staging_unpublished": 0,
    }
    assert [
        (e["execution"], e["status"], e["handoff_slot"]) for e in coverage["executions"]
    ] == [(execution, "no_handoff_capture", None) for execution in sorted(EXECUTIONS)]
    assert {s["slot_key"]: len(s["attempts"]) for s in report["slots"]} == {
        FIRST: 2,
        SECOND: 2,
    }
    assert set(coverage) == {"complete", "counts", "skipped", "executions", "caveat"}
    assert all(
        set(e) == {"execution", "status", "handoff_slot"}
        for e in coverage["executions"]
    )
    assert (
        attempt_history.main([str(job), str(archive), "--handoffs", str(handoffs)]) == 1
    )


@pytest.mark.parametrize(
    ("job", "slot", "trial", "bind_roles", "expected", "other_job", "handoff_slot"),
    [
        (JOB, FIRST, FINAL, True, counts(sealed=1, no_handoff_capture=3), 0, None),
        (
            JOB,
            FIRST,
            FINAL,
            False,
            counts(roster_disagrees=1, no_handoff_capture=3),
            0,
            None,
        ),
        (
            JOB,
            SECOND,
            FINAL,
            True,
            counts(slot_disagrees=1, no_handoff_capture=3),
            0,
            SECOND,
        ),
        (
            JOB,
            FIRST,
            UNSEEN,
            True,
            counts(handoff_only=1, no_handoff_capture=4),
            0,
            FIRST,
        ),
        (FOREIGN_JOB, FIRST, FINAL, True, counts(no_handoff_capture=4), 1, None),
    ],
    ids=["bound-roster", "public-roster", "wrong-slot", "no-lifecycle", "foreign-job"],
)
def test_one_derived_receipt_binds_only_its_own_job_slot_execution_and_roster(
    tmp_path, capsys, job, slot, trial, bind_roles, expected, other_job, handoff_slot
):
    handoffs = private(tmp_path, "handoffs")
    receipt = derive_test_sealed(
        tmp_path, job=job, slot=slot, trial=trial, bind_roles=bind_roles
    )
    keep(handoffs, receipt)
    code, report = run(
        capsys, INDEXED / "job-result.json", INDEXED / "archive", "--handoffs", handoffs
    )
    coverage = report["handoff_coverage"]
    assert code == 1 and report["complete"] is coverage["complete"] is False
    assert coverage["counts"] == expected
    assert coverage["skipped"]["other_job"] == other_job
    entry = {e["execution"]: e for e in coverage["executions"]}[trial]
    assert entry["handoff_slot"] == handoff_slot
    bound = (job, slot, receipt["roster"]) == (JOB, EXECUTIONS.get(trial), ["p1", "p2"])
    assert (entry["status"] == "sealed") is bound


def test_derived_single_slot_job_completes_only_with_its_bound_sealed_receipt(
    tmp_path, capsys
):
    job = derived_job(tmp_path, total=1)
    archive = one_execution_archive(tmp_path, None)
    assert attempt_history.main([str(job), str(archive)]) == 0
    capsys.readouterr()
    handoffs = private(tmp_path, "handoffs")
    code, report = run(capsys, job, archive, "--handoffs", handoffs)
    assert code == 1 and report["handoff_coverage"]["counts"] == counts(
        no_handoff_capture=1
    )
    name = keep(
        handoffs, derive_test_sealed(tmp_path, job=JOB, slot=FIRST, trial=FINAL)
    )
    (handoffs / f".{name}.{'0' * 32}.tmp").write_bytes(b"")
    code, report = run(capsys, job, archive, "--handoffs", handoffs)
    coverage = report["handoff_coverage"]
    assert code == 0 and report["complete"] is coverage["complete"] is True
    assert coverage["counts"] == counts(sealed=1)
    assert coverage["skipped"]["staging_published"] == 1
    unpublished = f".{JOB}.{FIRST}.{UNSEEN}.handoffs.json.{'1' * 32}.tmp"
    (handoffs / unpublished).write_bytes(b"")
    code, report = run(capsys, job, archive, "--handoffs", handoffs)
    assert code == 1 and report["handoff_coverage"]["complete"] is False
    assert report["handoff_coverage"]["skipped"]["staging_unpublished"] == 1


def test_receipt_never_binds_to_a_journal_without_a_header(tmp_path, capsys):
    job = derived_job(tmp_path, total=1)
    archive = one_execution_archive(tmp_path, b"")
    handoffs = private(tmp_path, "handoffs")
    keep(handoffs, derive_test_sealed(tmp_path, job=JOB, slot=FIRST, trial=FINAL))
    code, report = run(capsys, job, archive, "--handoffs", handoffs)
    assert code == 1 and report["handoff_coverage"]["complete"] is False
    assert report["handoff_coverage"]["counts"] == counts(roster_unknown=1)


def test_explicit_zero_expected_never_claims_handoff_coverage(tmp_path, capsys):
    job = derived_job(tmp_path, total=0)
    archive = private(tmp_path, "archive")
    handoffs = private(tmp_path, "handoffs")
    code, report = run(capsys, job, archive, "--handoffs", handoffs)
    assert code == 1 and report["expected_executions"] == 0
    assert report["handoff_coverage"]["complete"] is False
    assert report["handoff_coverage"]["counts"] == counts()
