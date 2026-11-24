import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.attempt_keeper import capture
from examples.seat_handoffs.attempt_receipt import SEALED, WORLD, project
from examples.seatlab.workbench.account_contract import attempt, packet, seat
from examples.seatlab.workbench.handoffs import main
from examples.seatlab.workbench.native_account import project_result
from examples.seatlab.workbench.team_account import main as account_main

SAMPLES = Path(__file__).resolve().parents[3] / "examples" / "seat_handoffs" / "samples"
STALE = (SAMPLES / "confirm-stale.jsonl").read_bytes().splitlines()
LATE = (SAMPLES / "confirm-late.jsonl").read_bytes().splitlines()
STALE_RUN = "eca63cf9-c1eb-4fa5-93a8-f7de5f3c13ee"
LATE_RUN = "5325bfa9-c062-4075-b3c3-546a1865f986"
JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
FINAL = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
EARLIER = "2c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b"
STRAY = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
DERIVED_WORLDS = tuple(f"00000000-0000-4000-8000-{n:012x}" for n in range(1, 5))
SLOT, SLOT_B = "relay-sentinel__a1b2c3d", "relay-sentinel__e4f5a6b"
ROSTER = ("booker", "reporter")
PRIVATE = ("initial", "revision", STALE_RUN, LATE_RUN)
FINISHED = {"finished_at": "2026-11-20T15:04:00+00:00"}
CHANGED = ["returned.receipt", "acknowledged.receipt"]
CHANGED_EDGES = {"comparable": 2, "same": 1, "different": 1, "unknown": 0}
NO_EDGES = {"comparable": 0, "same": 0, "different": 0, "unknown": 0}
Json = dict[str, Any]
linkat = pytest.mark.skipif(
    os.link not in os.supports_dir_fd or os.link not in os.supports_follow_symlinks,
    reason="linkat unsupported",
)


def digest(slot: str) -> str:
    return hashlib.sha256(os.fsencode(slot)).hexdigest()


def derived(lines: list[bytes], world: str, *, acks: bool = True) -> list[bytes]:
    rows = [json.loads(line) for line in lines]
    rows[0]["run"] = world
    if not acks:
        del rows[0]["acknowledgements"]
        rows = [row for row in rows if row["type"] != "acknowledged"]
    for seq, row in enumerate(rows):
        row["seq"] = seq
    return [json.dumps(row, separators=(",", ":")).encode() for row in rows]


def execution(
    root: Path,
    slot: str,
    *,
    sealed: list[bytes] | None = None,
    world: list[bytes] | None = None,
) -> Path:
    path = root / slot
    path.mkdir(parents=True)
    for parts, lines in ((SEALED, sealed), (WORLD, world)):
        if lines is not None:
            target = path.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"".join(line + b"\n" for line in lines))
    return path


def row_for(
    trial: str, slot: str, grade: str = "valid", reward: float | None = 1.0
) -> Json:
    return attempt(trial, digest(slot), grade, reward, [seat(name) for name in ROSTER])


def keep(archive: Path, path: Path, row: Json, *, named: str | None = None) -> None:
    receipt = project(path, job=JOB, attempt=row)
    name = f"{JOB}.{receipt['slot']}.{named or receipt['trial']}.handoffs.json"
    text = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    (archive / name).write_bytes((text + "\n").encode("ascii"))


def packet_file(
    root: Path, finals: list[Json], *, registered: int = 1, retries: int = 1
) -> Path:
    path = root / "packet.json"
    path.write_text(
        json.dumps(
            packet("native-job", JOB, registered, retries, finals, job_closed=True)
        )
    )
    return path


def plain_archive(root: Path) -> Path:
    archive = root / "archive"
    archive.mkdir()
    return archive


def compare(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, Json]:
    code = main([str(arg) for arg in argv] + ["--json"])
    out = capsys.readouterr().out
    assert code in (0, 1)
    return code, json.loads(out)


def native_execution(root: Path, archive: Path, trial: str, lines: list[bytes]) -> None:
    path = execution(root, SLOT, sealed=lines)
    document = {
        "id": trial,
        "trial_name": SLOT,
        "config": {
            "job_id": JOB,
            "trial_name": SLOT,
            "participants": [{"seat": name} for name in ROSTER],
        },
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": 1.0}},
    }
    (path / "result.json").write_text(json.dumps(document))
    row = project_result(document, job_id=JOB, slot_name=SLOT)
    capture(str(archive), path, job_id=JOB, attempt=row)


def native_job(
    tmp_path: Path, final: list[bytes], earlier: list[bytes], closure: Json
) -> tuple[Path, Path]:
    archive = tmp_path / "archive"
    archive.mkdir()
    archive.chmod(0o700)
    job = tmp_path / "job"
    job.mkdir()
    summary = {
        "id": JOB,
        "n_total_trials": 1,
        "stats": {"n_retries": 1, "n_completed_trials": 1},
        **closure,
    }
    (job / "result.json").write_text(json.dumps(summary))
    native_execution(tmp_path / "earlier", archive, EARLIER, earlier)
    native_execution(job, archive, FINAL, final)
    return job, archive


def native_source(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, job: Path, mode: str
) -> list[str]:
    if mode == "job":
        return [str(job)]
    assert account_main(["native", str(job), "--packet"]) == 0
    path = tmp_path / "packet.json"
    path.write_text(capsys.readouterr().out)
    return [str(path), "--packet"]


@linkat
@pytest.mark.parametrize("mode", ["job", "packet"])
@pytest.mark.parametrize(
    ("final", "earlier"),
    [(LATE, STALE), (STALE, LATE)],
    ids=["late-final", "stale-final"],
)
def test_equal_valid_rewards_keep_a_handoff_change_against_the_native_final(
    capsys, tmp_path, mode, final, earlier
):
    job, archive = native_job(tmp_path, final, earlier, FINISHED)
    source = native_source(capsys, tmp_path, job, mode)
    code, found = compare(capsys, source[0], archive, *source[1:])
    assert code == 0 and found["job_closed"] is True
    grades = {"valid": 1, "invalid": 0, "unknown": 0}
    assert found["packet"] == {
        "registered": 1,
        "present": 1,
        "final": True,
        "missing_finals": 0,
        "grades": grades,
    }
    assert found["executions"] == {
        "reported_retries": 1,
        "expected": 2,
        "known": 2,
        "captured": 2,
        "unaccounted": 0,
        "counts_agree": True,
        "over_expected": False,
    }
    comparison = {
        "trial": EARLIER,
        "source": "sealed",
        "status": "different",
        "reason": None,
        "edges": CHANGED_EDGES,
        "changed": CHANGED,
        "score": "same",
    }
    assert found["slots"] == [
        {
            "slot": digest(SLOT),
            "packet_trial": FINAL,
            "packet_capture": "sealed",
            "other_receipts": 1,
            "comparisons": [comparison],
        }
    ]
    assert found["comparisons"] == {
        "same": 0,
        "different": 1,
        "unknown": 0,
        "not_comparable": 0,
    }
    assert found["scores"] == {"same": 1, "different": 0, "unknown": 0}
    assert found["same_score_different_handoffs"] == 1
    assert (found["coverage_complete"], found["all_expected_sealed"]) == (True, True)


AGREEMENT = [
    pytest.param(
        STALE,
        {"sealed": derived(STALE, DERIVED_WORLDS[0])},
        "same",
        2,
        0,
        [],
        True,
        id="sealed-agree",
    ),
    pytest.param(
        derived(STALE, DERIVED_WORLDS[1], acks=False),
        {"sealed": derived(STALE, DERIVED_WORLDS[2], acks=False)},
        "unknown",
        0,
        0,
        [],
        True,
        id="acks-unsupported",
    ),
    pytest.param(
        STALE,
        {"world": derived(STALE[:6], DERIVED_WORLDS[3])},
        "unknown",
        0,
        0,
        [],
        False,
        id="open-prefix",
    ),
    pytest.param(
        STALE,
        {"world": LATE[:8]},
        "different",
        0,
        1,
        CHANGED,
        False,
        id="prefix-positive",
    ),
]


@pytest.mark.parametrize(
    ("final", "earlier", "status", "same", "different", "changed", "all_sealed"),
    AGREEMENT,
)
def test_only_two_sealed_journals_can_agree(
    capsys, tmp_path, final, earlier, status, same, different, changed, all_sealed
):
    archive = plain_archive(tmp_path)
    final_row = row_for(FINAL, SLOT)
    keep(archive, execution(tmp_path / "final", SLOT, sealed=final), final_row)
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT, **earlier),
        row_for(EARLIER, SLOT),
    )
    code, found = compare(
        capsys, packet_file(tmp_path, [final_row]), archive, "--packet"
    )
    assert code == 0
    assert (found["coverage_complete"], found["all_expected_sealed"]) == (
        True,
        all_sealed,
    )
    edges = {
        "comparable": 2,
        "same": same,
        "different": different,
        "unknown": 2 - same - different,
    }
    [slot] = found["slots"]
    assert (slot["packet_trial"], slot["packet_capture"]) == (FINAL, "sealed")
    assert slot["comparisons"] == [
        {
            "trial": EARLIER,
            "source": "sealed" if "sealed" in earlier else "world_prefix",
            "status": status,
            "reason": "partial_evidence" if status == "unknown" else None,
            "edges": edges,
            "changed": changed,
            "score": "same",
        }
    ]
    assert found["same_score_different_handoffs"] == int(status == "different")


@linkat
@pytest.mark.parametrize("mode", ["job", "packet"])
@pytest.mark.parametrize(
    ("closure", "closed"),
    [({"finished_at": None}, False), ({}, None)],
    ids=["open", "unknown"],
)
def test_open_or_unknown_job_compares_nothing_and_has_no_denominator(
    capsys, tmp_path, mode, closure, closed
):
    job, archive = native_job(tmp_path, LATE, STALE, closure)
    source = native_source(capsys, tmp_path, job, mode)
    code, found = compare(capsys, source[0], archive, *source[1:])
    assert code == 1 and found["job_closed"] is closed
    assert (found["packet"]["final"], found["packet"]["missing_finals"]) == (
        False,
        None,
    )
    assert found["executions"] == {
        "reported_retries": 1,
        "expected": None,
        "known": 2,
        "captured": 2,
        "unaccounted": None,
        "counts_agree": None,
        "over_expected": None,
    }
    assert found["slots"][0]["comparisons"] == [
        {
            "trial": EARLIER,
            "source": "sealed",
            "status": "unknown",
            "reason": "job_not_closed",
            "edges": NO_EDGES,
            "changed": [],
            "score": "unknown",
        }
    ]
    assert (
        found["comparisons"]["different"] == found["same_score_different_handoffs"] == 0
    )
    assert (found["coverage_complete"], found["all_expected_sealed"]) == (False, False)


def test_withheld_earlier_capture_keeps_the_published_retry_gap(capsys, tmp_path):
    archive = plain_archive(tmp_path)
    final_row = row_for(FINAL, SLOT)
    keep(archive, execution(tmp_path / "final", SLOT, sealed=LATE), final_row)
    code, found = compare(
        capsys, packet_file(tmp_path, [final_row]), archive, "--packet"
    )
    assert code == 1
    assert found["executions"] == {
        "reported_retries": 1,
        "expected": 2,
        "known": 1,
        "captured": 1,
        "unaccounted": 1,
        "counts_agree": False,
        "over_expected": False,
    }
    assert found["slots"] == [
        {
            "slot": digest(SLOT),
            "packet_trial": FINAL,
            "packet_capture": "sealed",
            "other_receipts": 0,
            "comparisons": [],
        }
    ]
    assert found["comparisons"] == {
        "same": 0,
        "different": 0,
        "unknown": 0,
        "not_comparable": 0,
    }
    assert (found["coverage_complete"], found["all_expected_sealed"]) == (False, False)


def test_missing_final_keeps_an_unlabelled_slot_and_packet_only_grades(
    capsys, tmp_path
):
    archive = plain_archive(tmp_path)
    final_row = row_for(FINAL, SLOT)
    keep(archive, execution(tmp_path / "final", SLOT, sealed=LATE), final_row)
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT_B, sealed=STALE),
        row_for(EARLIER, SLOT_B),
    )
    code, found = compare(
        capsys, packet_file(tmp_path, [final_row], registered=2), archive, "--packet"
    )
    assert code == 1
    grades = {"valid": 1, "invalid": 0, "unknown": 0}
    assert found["packet"] == {
        "registered": 2,
        "present": 1,
        "final": True,
        "missing_finals": 1,
        "grades": grades,
    }
    assert found["executions"] == {
        "reported_retries": 1,
        "expected": 3,
        "known": 2,
        "captured": 2,
        "unaccounted": 1,
        "counts_agree": False,
        "over_expected": False,
    }
    slots = {slot["slot"]: slot for slot in found["slots"]}
    assert slots[digest(SLOT_B)] == {
        "slot": digest(SLOT_B),
        "packet_trial": None,
        "packet_capture": None,
        "other_receipts": 1,
        "comparisons": [
            {
                "trial": EARLIER,
                "source": "sealed",
                "status": "unknown",
                "reason": "no_final",
                "edges": NO_EDGES,
                "changed": [],
                "score": "unknown",
            }
        ],
    }
    assert found["captures"]["other_without_packet_attempt"] == 1
    assert found["scores"] == {"same": 0, "different": 0, "unknown": 1}
    assert found["coverage_complete"] is False


GAPS = [
    pytest.param(
        None,
        "valid",
        "uncaptured",
        ("unknown", "final_uncaptured", NO_EDGES, [], "same"),
        False,
        id="final-withheld",
    ),
    pytest.param(
        {},
        "valid",
        "unavailable",
        ("unknown", "final_unavailable", NO_EDGES, [], "same"),
        True,
        id="final-unavailable",
    ),
    pytest.param(
        {"sealed": LATE},
        "invalid",
        "sealed",
        ("different", None, CHANGED_EDGES, CHANGED, "unknown"),
        True,
        id="earlier-invalid",
    ),
]


@pytest.mark.parametrize(
    ("final", "earlier_grade", "packet_capture", "outcome", "complete"), GAPS
)
def test_final_gaps_and_grades_stay_explicit_under_require_sealed(
    capsys, tmp_path, final, earlier_grade, packet_capture, outcome, complete
):
    archive = plain_archive(tmp_path)
    final_row = row_for(FINAL, SLOT)
    if final is not None:
        keep(archive, execution(tmp_path / "final", SLOT, **final), final_row)
    reward = 1.0 if earlier_grade == "valid" else None
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT, sealed=STALE),
        row_for(EARLIER, SLOT, earlier_grade, reward),
    )
    code, found = compare(
        capsys,
        packet_file(tmp_path, [final_row]),
        archive,
        "--packet",
        "--require-sealed",
    )
    sealed = packet_capture == "sealed"
    assert code == (0 if sealed else 1)
    assert (found["coverage_complete"], found["all_expected_sealed"]) == (
        complete,
        sealed,
    )
    assert found["packet"]["grades"] == {"valid": 1, "invalid": 0, "unknown": 0}
    assert (found["executions"]["expected"], found["executions"]["known"]) == (2, 2)
    status, reason, edges, changed, score = outcome
    [slot] = found["slots"]
    assert (slot["packet_trial"], slot["packet_capture"]) == (FINAL, packet_capture)
    assert slot["comparisons"] == [
        {
            "trial": EARLIER,
            "source": "sealed",
            "status": status,
            "reason": reason,
            "edges": edges,
            "changed": changed,
            "score": score,
        }
    ]
    assert found["same_score_different_handoffs"] == 0


def keep_final(tmp_path: Path, archive: Path) -> None:
    keep(
        archive, execution(tmp_path / "final", SLOT, sealed=STALE), row_for(FINAL, SLOT)
    )


def same_world_sealed(tmp_path: Path, archive: Path) -> None:
    keep_final(tmp_path, archive)
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT, sealed=STALE),
        row_for(EARLIER, SLOT),
    )


def same_world_prefix(tmp_path: Path, archive: Path) -> None:
    keep_final(tmp_path, archive)
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT, world=STALE[:6]),
        row_for(EARLIER, SLOT),
    )


def same_world_other_slot(tmp_path: Path, archive: Path) -> None:
    keep_final(tmp_path, archive)
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT_B, sealed=STALE),
        row_for(EARLIER, SLOT_B),
    )


def trial_in_two_slots(tmp_path: Path, archive: Path) -> None:
    keep_final(tmp_path, archive)
    keep(
        archive,
        execution(tmp_path / "other", SLOT_B, sealed=LATE),
        row_for(FINAL, SLOT_B),
    )


def name_bound_to_another_trial(tmp_path: Path, archive: Path) -> None:
    keep_final(tmp_path, archive)
    keep(
        archive,
        execution(tmp_path / "earlier", SLOT, sealed=LATE),
        row_for(EARLIER, SLOT),
        named=STRAY,
    )


def final_in_another_slot(tmp_path: Path, archive: Path) -> None:
    keep(
        archive,
        execution(tmp_path / "final", SLOT_B, sealed=STALE),
        row_for(FINAL, SLOT_B),
    )


def final_with_another_grade(tmp_path: Path, archive: Path) -> None:
    keep(
        archive,
        execution(tmp_path / "final", SLOT, sealed=STALE),
        row_for(FINAL, SLOT, "invalid", None),
    )


@pytest.mark.parametrize(
    "build",
    [
        same_world_sealed,
        same_world_prefix,
        same_world_other_slot,
        trial_in_two_slots,
        name_bound_to_another_trial,
        final_in_another_slot,
        final_with_another_grade,
    ],
)
def test_conflicting_slot_or_uuid_bindings_are_rejected(
    capsys, tmp_path, build: Callable[[Path, Path], None]
):
    archive = plain_archive(tmp_path)
    build(tmp_path, archive)
    source = packet_file(tmp_path, [row_for(FINAL, SLOT)], registered=2)
    code = main([str(source), str(archive), "--packet", "--json"])
    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert not any(secret in captured.err for secret in PRIVATE)
