import json
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.job import inspect_job

JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
OTHER_JOB = "8c7b6a5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d"
RUN_A = "0b6f3c1e-8d2a-4f57-9a41-3c7e2d915b08"
RUN_B = "5d0c9a8e-1f3b-4c6d-8e7f-90a1b2c3d4e5"
NATIVE_A = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
NATIVE_B = "2c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b"
NATIVE_C = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
SENTINEL = "sentinel-config-value-4c1d"
TASK = "revised-dispatch"
FIRST = f"{TASK}__a1b2c3d"
SECOND = f"{TASK}__e4f5a6b"
FOREIGN = f"{TASK}__f0e1d2c"
EDGES = [
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
]
Json = dict[str, Any]
Steps = tuple[tuple[str, ...], ...]
LATE: Steps = (
    ("sent", "initial"),
    ("returned", "initial"),
    ("acknowledged", "initial"),
    ("commit",),
    ("sent", "revision"),
    ("returned", "revision"),
)
OPEN: Steps = LATE[:2]


@pytest.fixture(autouse=True)
def foreign_cwd(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))


def frame(record: Json) -> str:
    return json.dumps(record, separators=(",", ":"))


def journal(run: str, steps: Steps) -> list[str]:
    start = {
        "seq": 0,
        "type": "start",
        "schema": "harbor.handoffs/v1",
        "run": run,
        "edges": EDGES,
        "acknowledgements": True,
    }
    lines = [frame(start)]
    for seq, (kind, *message) in enumerate(steps, start=1):
        record: Json = {"seq": seq, "type": kind}
        if kind == "sent":
            record |= {"message": message[0], "sender": "reporter"}
        elif kind == "commit":
            record |= {"receiver": "booker"}
        else:
            record |= {"message": message[0], "receiver": "booker"}
        lines.append(frame(record))
    return lines


def sealed(run: str, reward: int) -> list[str]:
    lines = journal(run, LATE)
    return lines + [frame({"seq": len(lines), "type": "closed", "reward": reward})]


def trial_lock() -> Json:
    return {
        "schema_version": 2,
        "task": {"name": TASK, "type": "local", "digest": "sha256:" + "a1" * 32},
        "agent": {"name": "oracle", "kwargs": {"note": SENTINEL}},
    }


def write_json(path: Path, record: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=4), encoding="utf-8")


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_job(root: Path, *, locked: int = 3, planned: int = 3) -> Path:
    write_json(
        root / "lock.json",
        {"schema_version": 3, "trials": [trial_lock() for _ in range(locked)]},
    )
    write_json(
        root / "result.json",
        {"schema_version": 1, "id": JOB, "n_total_trials": planned},
    )
    return root


def add_trial(
    job: Path,
    name: str,
    *,
    native: str | None,
    reward: int | None,
    job_id: str = JOB,
    world: list[str] | None = None,
    verifier: list[str] | None = None,
) -> None:
    trial = job / name
    config = {
        "schema_version": 1,
        "trial_name": name,
        "job_id": job_id,
        "agent": {"name": "oracle", "kwargs": {"note": SENTINEL}},
    }
    write_json(trial / "config.json", config)
    write_json(trial / "lock.json", trial_lock())
    if native is not None:
        write_json(
            trial / "result.json",
            {
                "schema_version": 1,
                "id": native,
                "trial_name": name,
                "config": config,
                "verifier_result": {"rewards": {"reward": reward}},
                "reward_valid": True,
            },
        )
    if world is not None:
        write_lines(trial / "artifacts" / "logs" / "world" / "handoffs.jsonl", world)
    if verifier is not None:
        write_lines(trial / "verifier" / "handoffs.jsonl", verifier)


def counts(report: Json) -> tuple[int, int, int, int, int]:
    return (
        report["registered_slots"],
        report["matched_trials"],
        report["missing_slots"],
        report["usable_trials"],
        report["valid_native_grades"],
    )


def rows(report: Json) -> dict[str, Json]:
    return {row["directory"]: row for row in report["trials"]}


def receipts(row: Json) -> list[str]:
    return [finding["receipt"] for finding in row["findings"]]


def inspect(job: Path) -> Json:
    report = inspect_job(job, boundary="acknowledged")
    assert report["schema"] == "harbor.handoff-job/v1"
    assert report["job_id"] == JOB
    assert report["boundary"] == "acknowledged"
    return report


def test_partial_job_keeps_zero_reward_and_counts_missing_slot(tmp_path: Path) -> None:
    job = make_job(tmp_path / "job")
    add_trial(job, FIRST, native=NATIVE_A, reward=0, verifier=sealed(RUN_A, 0))
    add_trial(job, SECOND, native=NATIVE_B, reward=1)
    report = inspect(job)
    assert counts(report) == (3, 2, 1, 1, 2)
    usable, missing = rows(report)[FIRST], rows(report)[SECOND]
    assert (usable["status"], usable["native_trial"], usable["reward"]) == (
        "usable",
        NATIVE_A,
        0,
    )
    assert receipts(usable) == ["confirmed", "absent"]
    assert (missing["status"], missing["native_trial"], missing["reward"]) == (
        "missing_journal",
        NATIVE_B,
        1,
    )
    assert missing["findings"] == []
    summary = report["summary"]
    assert (summary["trials"], summary["rows"]) == (1, 2)
    assert summary["receipt"]["absent"] == 1
    assert summary["reward"]["graded_trials"] == 1
    assert summary["reward"]["mean"] == 0.0
    assert SENTINEL not in json.dumps(report)


def test_missing_journal_without_native_result_has_no_reward(tmp_path: Path) -> None:
    job = make_job(tmp_path / "job")
    add_trial(job, FIRST, native=NATIVE_A, reward=1)
    add_trial(job, SECOND, native=None, reward=None)
    report = inspect(job)
    assert counts(report) == (3, 2, 1, 0, 1)
    graded, ungraded = rows(report)[FIRST], rows(report)[SECOND]
    assert (graded["status"], graded["native_trial"], graded["reward"]) == (
        "missing_journal",
        NATIVE_A,
        1,
    )
    assert (ungraded["status"], ungraded["native_trial"], ungraded["reward"]) == (
        "missing_journal",
        None,
        None,
    )
    assert graded["findings"] == ungraded["findings"] == []
    assert report["summary"]["trials"] == 0


def test_absent_trial_directories_keep_the_registered_denominator(
    tmp_path: Path,
) -> None:
    report = inspect(make_job(tmp_path / "job"))
    assert counts(report) == (3, 0, 3, 0, 0)
    assert report["trials"] == []
    assert report["summary"]["trials"] == 0


def test_open_journal_is_accounted_but_not_usable_or_negative(tmp_path: Path) -> None:
    job = make_job(tmp_path / "job")
    add_trial(job, FIRST, native=NATIVE_A, reward=1, world=journal(RUN_A, OPEN))
    report = inspect(job)
    assert counts(report) == (3, 1, 2, 0, 1)
    row = rows(report)[FIRST]
    assert row["status"] != "usable"
    assert row["native_trial"] == NATIVE_A
    assert receipts(row) == ["unknown", "unknown"]
    assert report["summary"]["trials"] == 0
    assert report["summary"]["receipt"]["absent"] == 0


def test_same_lock_trial_from_another_job_fills_no_slot(tmp_path: Path) -> None:
    job = make_job(tmp_path / "job")
    add_trial(job, FIRST, native=NATIVE_A, reward=0, verifier=sealed(RUN_A, 0))
    add_trial(
        job,
        FOREIGN,
        native=NATIVE_B,
        reward=1,
        job_id=OTHER_JOB,
        verifier=sealed(RUN_B, 1),
    )
    report = inspect(job)
    assert counts(report) == (3, 1, 2, 1, 1)
    assert list(rows(report)) == [FIRST]
    assert [item["directory"] for item in report["unassigned"]] == [FOREIGN]
    assert report["summary"]["trials"] == 1
    assert report["summary"]["reward"]["mean"] == 0.0


@pytest.mark.parametrize(
    ("natives", "runs"),
    [
        ((NATIVE_A, NATIVE_A), (RUN_A, RUN_B)),
        ((NATIVE_A, NATIVE_C), (RUN_A, RUN_A)),
        ((NATIVE_A, NATIVE_A), (RUN_A, None)),
    ],
    ids=["native_id", "world_run", "native_id_missing_journal"],
)
def test_duplicate_identity_fails_closed_on_both_rows(
    tmp_path: Path, natives: tuple[str, str], runs: tuple[str, str | None]
) -> None:
    job = make_job(tmp_path / "job")
    for name, native, run in zip((FIRST, SECOND), natives, runs, strict=True):
        log = None if run is None else sealed(run, 0)
        add_trial(job, name, native=native, reward=0, verifier=log)
    report = inspect(job)
    assert counts(report) == (3, 2, 1, 0, 0)
    assert {name: row["status"] for name, row in rows(report).items()} == {
        FIRST: "duplicate_identity",
        SECOND: "duplicate_identity",
    }
    assert report["summary"]["trials"] == 0


@pytest.mark.parametrize(("locked", "planned"), [(3, 2), (2, 3)])
def test_job_result_and_lock_denominator_mismatch_is_rejected(
    tmp_path: Path, locked: int, planned: int
) -> None:
    job = make_job(tmp_path / "job", locked=locked, planned=planned)
    add_trial(job, FIRST, native=NATIVE_A, reward=0, verifier=sealed(RUN_A, 0))
    with pytest.raises(ValueError):
        inspect_job(job, boundary="acknowledged")
