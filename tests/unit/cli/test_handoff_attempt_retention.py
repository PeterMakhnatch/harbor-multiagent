import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.attempt_keeper import capture
from examples.seat_handoffs.attempt_receipt import (
    SCHEMA,
    SEALED,
    WORLD,
    edge_digest,
    project,
    validate,
)
from examples.seatlab.workbench.account_contract import attempt, seat
from examples.seatlab.workbench.private_receipt import Conflict
from examples.seatlab.workbench.seat_stream import Malformed

SAMPLES = Path(__file__).resolve().parents[3] / "examples" / "seat_handoffs" / "samples"
STALE = (SAMPLES / "confirm-stale.jsonl").read_bytes().splitlines()
LATE = (SAMPLES / "confirm-late.jsonl").read_bytes().splitlines()
STALE_RUN = "eca63cf9-c1eb-4fa5-93a8-f7de5f3c13ee"
LATE_RUN = "5325bfa9-c062-4075-b3c3-546a1865f986"
JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
TRIAL = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
RETRY = "2c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b"
ROSTER = ("booker", "reporter")
SECRET = "PRIVATE-PROMPT-BODY"
INITIAL, REVISION = edge_digest("initial"), edge_digest("revision")
Json = dict[str, Any]
linkat = pytest.mark.skipif(
    os.link not in os.supports_dir_fd or os.link not in os.supports_follow_symlinks,
    reason="linkat unsupported",
)


def journal(lines: list[bytes]) -> bytes:
    return b"".join(line + b"\n" for line in lines)


def trial_dir(
    parent: Path, slot: str, *, sealed: bytes | None = None, world: bytes | None = None
) -> Path:
    path = parent / slot
    path.mkdir(parents=True)
    for parts, data in ((SEALED, sealed), (WORLD, world)):
        if data is not None:
            target = path.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return path


def attempt_for(
    path: Path,
    trial: str = TRIAL,
    grade: str = "valid",
    reward: float | None = 1.0,
    seats: tuple[str, ...] = ROSTER,
) -> Json:
    slot = hashlib.sha256(os.fsencode(path.name)).hexdigest()
    return attempt(trial, slot, grade, reward, [seat(name) for name in seats])


def private_archive(root: Path) -> Path:
    archive = root / "archive"
    archive.mkdir()
    archive.chmod(0o700)
    return archive


def by_message(receipt: Json) -> dict[str, Json]:
    return {edge["message"]: edge for edge in receipt["edges"]}


def unusable(path: Path, seats: tuple[str, ...] = ROSTER) -> Json:
    return {
        "schema": SCHEMA,
        "job": JOB,
        "slot": attempt_for(path)["slot"],
        "trial": TRIAL,
        "roster": sorted(seats),
        "grade_state": "valid",
        "reward": 1.0,
        "source": "unusable",
        "world": None,
        "edges": [],
    }


def test_equal_grades_keep_a_changed_handoff_timing(tmp_path):
    stale = trial_dir(
        tmp_path / "job", "relay-sentinel__a1b2c3d", sealed=journal(STALE)
    )
    late = trial_dir(tmp_path / "job", "relay-sentinel__e4f5a6b", sealed=journal(LATE))
    first = project(stale, job=JOB, attempt=attempt_for(stale))
    second = project(late, job=JOB, attempt=attempt_for(late, RETRY))
    assert validate(first) == first and validate(second) == second
    assert [(r["grade_state"], r["reward"], r["source"]) for r in (first, second)] == [
        ("valid", 1.0, "sealed")
    ] * 2
    assert (first["world"], second["world"]) == (STALE_RUN, LATE_RUN)
    old, new = by_message(first), by_message(second)
    assert old[INITIAL] == new[INITIAL]
    assert (
        old[INITIAL]["returned"]
        == old[INITIAL]["acknowledged"]
        == {"receipt": "confirmed", "timing": "before"}
    )
    assert old[REVISION]["commit"] == new[REVISION]["commit"] == "recorded"
    assert (
        old[REVISION]["returned"]
        == old[REVISION]["acknowledged"]
        == {"receipt": "absent", "timing": "unknown"}
    )
    assert (
        new[REVISION]["returned"]
        == new[REVISION]["acknowledged"]
        == {"receipt": "confirmed", "timing": "after"}
    )
    text = json.dumps([first, second])
    assert "initial" not in text and "revision" not in text


@linkat
def test_capture_keeps_one_file_per_attempt_identity_and_never_overwrites(tmp_path):
    archive = private_archive(tmp_path)
    path = trial_dir(tmp_path / "job", "relay-sentinel__a1b2c3d", sealed=journal(STALE))
    row = attempt_for(path)
    name = capture(str(archive), path, job_id=JOB, attempt=row)
    assert name == f"{JOB}.{row['slot']}.{TRIAL}.handoffs.json"
    kept = (archive / name).read_bytes()
    receipt = project(path, job=JOB, attempt=row)
    assert kept == (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    assert stat.S_IMODE((archive / name).stat().st_mode) == 0o600
    assert capture(str(archive), path, job_id=JOB, attempt=row) == name
    with pytest.raises(Conflict):
        capture(
            str(archive),
            path,
            job_id=JOB,
            attempt=attempt_for(path, grade="invalid", reward=None),
        )
    (path / "verifier" / "handoffs.jsonl").write_bytes(journal(LATE))
    with pytest.raises(Conflict):
        capture(str(archive), path, job_id=JOB, attempt=row)
    assert (archive / name).read_bytes() == kept
    assert os.listdir(archive) == [name]


def test_truncated_world_prefix_stays_unknown_where_the_seal_proves_absence(tmp_path):
    parent = tmp_path / "job"
    world = trial_dir(
        parent, "relay-sentinel__a1b2c3d", world=journal(STALE[:6]) + STALE[6][:12]
    )
    sealed = trial_dir(
        parent,
        "relay-sentinel__e4f5a6b",
        sealed=journal(STALE),
        world=journal(STALE[:6]),
    )
    split = trial_dir(
        parent,
        "relay-sentinel__c7d8e9f",
        sealed=journal(STALE),
        world=journal(LATE[:6]),
    )
    empty = trial_dir(parent, "relay-sentinel__f0a1b2c")
    seen, proof = (project(p, job=JOB, attempt=attempt_for(p)) for p in (world, sealed))
    assert (seen["source"], proof["source"]) == ("world_prefix", "sealed")
    assert seen["world"] == proof["world"] == STALE_RUN
    hint, proven = by_message(seen), by_message(proof)
    assert hint[INITIAL] == proven[INITIAL]
    assert hint[REVISION]["commit"] == proven[REVISION]["commit"] == "recorded"
    assert (
        hint[REVISION]["returned"]
        == hint[REVISION]["acknowledged"]
        == {"receipt": "unknown", "timing": "unknown"}
    )
    assert (
        proven[REVISION]["returned"]
        == proven[REVISION]["acknowledged"]
        == {"receipt": "absent", "timing": "unknown"}
    )
    assert project(split, job=JOB, attempt=attempt_for(split)) == unusable(split)
    gone = project(empty, job=JOB, attempt=attempt_for(empty))
    assert (gone["source"], gone["world"], gone["edges"]) == ("unavailable", None, [])
    forged = json.loads(json.dumps(seen))
    for edge in forged["edges"]:
        if edge["message"] == REVISION:
            edge["returned"]["receipt"] = "absent"
    with pytest.raises(Malformed):
        validate(forged)


@linkat
@pytest.mark.parametrize("seq", [0, 1, 4, 6])
def test_untrusted_extra_journal_fields_capture_only_an_unusable_receipt(tmp_path, seq):
    records = [json.loads(line) for line in STALE]
    records[seq]["body"] = SECRET
    lines = [json.dumps(record, separators=(",", ":")).encode() for record in records]
    path = trial_dir(tmp_path / "job", "relay-sentinel__a1b2c3d", sealed=journal(lines))
    archive = private_archive(tmp_path)
    name = capture(str(archive), path, job_id=JOB, attempt=attempt_for(path))
    kept = (archive / name).read_bytes()
    assert json.loads(kept) == unusable(path)
    assert SECRET.encode() not in kept and STALE_RUN.encode() not in kept


def test_validate_refuses_untrusted_extra_receipt_fields(tmp_path):
    path = trial_dir(tmp_path / "job", "relay-sentinel__a1b2c3d", sealed=journal(STALE))
    receipt = project(path, job=JOB, attempt=attempt_for(path))
    top, edge, boundary = (json.loads(json.dumps(receipt)) for _ in range(3))
    top["body"] = SECRET
    edge["edges"][0]["body"] = SECRET
    boundary["edges"][0]["returned"]["body"] = SECRET
    for forged in (top, edge, boundary):
        with pytest.raises(Malformed):
            validate(forged)


def test_roster_mismatch_and_symlinks_cannot_inject_journal_data(tmp_path):
    parent = tmp_path / "job"
    outside = trial_dir(
        tmp_path / "outside",
        "relay-sentinel__a1b2c3d",
        sealed=journal(STALE),
        world=journal(STALE[:6]),
    )
    elsewhere = trial_dir(
        tmp_path / "elsewhere", "relay-sentinel__d4e5f6a", sealed=journal(STALE)
    )
    cases: list[tuple[Path, tuple[str, ...]]] = []
    for slot, parts, target in (
        ("relay-sentinel__e4f5a6b", SEALED, outside.joinpath(*SEALED)),
        ("relay-sentinel__c7d8e9f", SEALED[:1], outside / SEALED[0]),
        ("relay-sentinel__f0a1b2c", WORLD, outside.joinpath(*WORLD)),
    ):
        path = trial_dir(parent, slot)
        link = path.joinpath(*parts)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        cases.append((path, ROSTER))
    (parent / elsewhere.name).symlink_to(elsewhere, target_is_directory=True)
    cases.append((parent / elsewhere.name, ROSTER))
    cases.append(
        (
            trial_dir(parent, "relay-sentinel__b9c0d1e", sealed=journal(STALE)),
            ("auditor", "booker"),
        )
    )
    for path, seats in cases:
        receipt = project(path, job=JOB, attempt=attempt_for(path, seats=seats))
        assert receipt == unusable(path, seats)
    archive = private_archive(tmp_path)
    stray = attempt_for(parent / "relay-sentinel__0000000")
    with pytest.raises(Malformed):
        capture(str(archive), outside, job_id=JOB, attempt=stray)
    assert os.listdir(archive) == []
