import json
from pathlib import Path
from typing import Any

import pytest

from examples.seat_handoffs.job import inspect_job
from examples.seat_handoffs.trial import MissingJournal, UnsealedDecision, read_trial

JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
RUN = "0b6f3c1e-8d2a-4f57-9a41-3c7e2d915b08"
OTHER_RUN = "5d0c9a8e-1f3b-4c6d-8e7f-90a1b2c3d4e5"
NATIVE = "7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
TASK = "revised-dispatch"
TRIAL = f"{TASK}__a1b2c3d"
EDGES = [
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
]
Json = dict[str, Any]
Steps = tuple[tuple[str, ...], ...]
STEPS: Steps = (
    ("sent", "initial"),
    ("returned", "initial"),
    ("acknowledged", "initial"),
    ("sent", "revision"),
    ("returned", "revision"),
    ("commit",),
    ("acknowledged", "revision"),
)
COUNTS = {"sent": 2, "returned": 2, "acknowledged": 2, "commit": 1}
FOREIGN: dict[str, Json] = {
    "schema_only": {"schema": "harbor.ship-handover.decision/v1"},
    "unrelated_verdict": {
        "schema": "harbor.joint-reopen/v3",
        "run": OTHER_RUN,
        "decision_correct": False,
        "reward": 0,
        "event_counts": {"sent": 9},
    },
    "sibling_controls_family": {
        "schema": "harbor.revised-dispatch.controls/v1",
        "run": RUN,
        "draft_changed": False,
        "decision_correct": False,
        "event_counts": COUNTS,
    },
}


def frame(record: Json) -> str:
    return json.dumps(record, separators=(",", ":"))


def journal(steps: Steps = STEPS) -> list[str]:
    start = {
        "seq": 0,
        "type": "start",
        "schema": "harbor.handoffs/v1",
        "run": RUN,
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


def closed(reward: int) -> list[str]:
    lines = journal()
    return lines + [frame({"seq": len(lines), "type": "closed", "reward": reward})]


def native(reward: int) -> Json:
    return {
        "id": NATIVE,
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": reward}},
    }


def revised(**overrides: Any) -> Json:
    record: Json = {
        "schema": "harbor.revised-dispatch.decision/v1",
        "run": RUN,
        "draft_changed": False,
        "decision_correct": True,
        "event_counts": COUNTS,
    }
    return record | overrides


def without(record: Json, key: str) -> Json:
    return {name: value for name, value in record.items() if name != key}


def write_trial(
    root: Path,
    *,
    sealed: list[str] | None,
    artifact: list[str] | None,
    result: Json | None,
    verdict: str | None,
) -> Path:
    files = {
        "verifier/handoffs.jsonl": None if sealed is None else "\n".join(sealed) + "\n",
        "artifacts/logs/world/handoffs.jsonl": None
        if artifact is None
        else "\n".join(artifact) + "\n",
        "result.json": None if result is None else json.dumps(result),
        "verifier/decision.json": verdict,
    }
    for name, text in files.items():
        if text is not None:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text, encoding="utf-8")
    return root


def sealed_trial(root: Path, verdict: str | None, reward: int = 1) -> Path:
    return write_trial(
        root,
        sealed=closed(reward),
        artifact=journal()[:4],
        result=native(reward),
        verdict=verdict,
    )


def open_trial(root: Path, verdict: str | None) -> Path:
    return write_trial(
        root, sealed=None, artifact=journal()[:4], result=native(1), verdict=verdict
    )


@pytest.mark.parametrize("reward", [0, 1])
@pytest.mark.parametrize("name", sorted(FOREIGN))
def test_foreign_decision_on_sealed_world_is_ignored_and_keeps_native_grade(
    tmp_path: Path, name: str, reward: int
) -> None:
    root = sealed_trial(tmp_path / "t", json.dumps(FOREIGN[name]), reward)
    findings, link = read_trial(root, boundary="returned")
    baseline_findings, baseline = read_trial(
        sealed_trial(tmp_path / "baseline", None, reward), boundary="returned"
    )
    assert link["decision"] is None
    assert (link["log"], link["native_grade_status"]) == ("verifier", "valid")
    assert link == baseline
    assert findings == baseline_findings
    assert {row.reward for row in findings} == {float(reward)}


@pytest.mark.parametrize("name", sorted(FOREIGN))
def test_foreign_decision_on_open_prefix_world_is_ignored(
    tmp_path: Path, name: str
) -> None:
    findings, link = read_trial(
        open_trial(tmp_path / "t", json.dumps(FOREIGN[name])), boundary="returned"
    )
    baseline_findings, baseline = read_trial(
        open_trial(tmp_path / "baseline", None), boundary="returned"
    )
    assert link["decision"] is None
    assert (link["log"], link["native_grade_status"]) == ("world", "valid")
    assert link == baseline
    assert findings == baseline_findings


def test_foreign_decision_does_not_hide_native_and_seal_disagreement(
    tmp_path: Path,
) -> None:
    root = write_trial(
        tmp_path / "t",
        sealed=closed(0),
        artifact=None,
        result=native(1),
        verdict=json.dumps(FOREIGN["schema_only"]),
    )
    with pytest.raises(ValueError, match="disagree on reward"):
        read_trial(root, boundary="returned")


def test_foreign_decision_without_any_handoff_log_is_missing_journal(
    tmp_path: Path,
) -> None:
    root = write_trial(
        tmp_path / "t",
        sealed=None,
        artifact=None,
        result=native(1),
        verdict=json.dumps(FOREIGN["schema_only"]),
    )
    with pytest.raises(MissingJournal):
        read_trial(root, boundary="returned")


REJECTED_ON_SEAL = {
    "bare_family": json.dumps(revised(schema="harbor.revised-dispatch.decision")),
    "empty_revised_version": json.dumps(
        revised(schema="harbor.revised-dispatch.decision/")
    ),
    "unknown_revised_version": json.dumps(
        revised(schema="harbor.revised-dispatch.decision/v3")
    ),
    "revised_v1_missing_key": json.dumps(without(revised(), "run")),
    "revised_v1_extra_key": json.dumps(revised(note="x")),
    "revised_wrong_grade": json.dumps(revised(decision_correct=False)),
    "schemaless": json.dumps(without(revised(), "schema")),
    "null_schema": json.dumps(revised(schema=None)),
    "numeric_schema": json.dumps(revised(schema=1)),
    "list_schema": json.dumps(revised(schema=["harbor.revised-dispatch.decision/v1"])),
    "not_json": "{",
    "not_object": json.dumps(["harbor.ship-handover.decision/v1"]),
    "duplicate_schema_key": '{"schema": "harbor.ship-handover.decision/v1", '
    '"schema": "harbor.revised-dispatch.decision/v1"}',
}


@pytest.mark.parametrize("name", sorted(REJECTED_ON_SEAL))
def test_claimed_or_untyped_decision_on_sealed_world_fails(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValueError) as caught:
        read_trial(
            sealed_trial(tmp_path / "t", REJECTED_ON_SEAL[name]), boundary="returned"
        )
    assert not isinstance(caught.value, (UnsealedDecision, MissingJournal))


@pytest.mark.parametrize(
    "verdict",
    [json.dumps(revised()), *REJECTED_ON_SEAL.values()],
    ids=["revised_v1", *REJECTED_ON_SEAL],
)
def test_revised_or_untyped_decision_without_seal_is_unsealed(
    tmp_path: Path, verdict: str
) -> None:
    with pytest.raises(UnsealedDecision):
        read_trial(open_trial(tmp_path / "t", verdict), boundary="returned")


def write_json(path: Path, record: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=4), encoding="utf-8")


def trial_lock() -> Json:
    return {
        "schema_version": 2,
        "task": {"name": TASK, "type": "local", "digest": "sha256:" + "a1" * 32},
        "agent": {"name": "oracle", "kwargs": {}},
    }


def job_with_trial(root: Path, **layout: Any) -> Path:
    write_json(root / "lock.json", {"schema_version": 3, "trials": [trial_lock()]})
    write_json(
        root / "result.json", {"schema_version": 1, "id": JOB, "n_total_trials": 1}
    )
    config = {
        "schema_version": 1,
        "trial_name": TRIAL,
        "job_id": JOB,
        "agent": {"name": "oracle", "kwargs": {}},
    }
    write_json(root / TRIAL / "config.json", config)
    write_json(root / TRIAL / "lock.json", trial_lock())
    trial = write_trial(root / TRIAL, result=None, **layout)
    write_json(
        trial / "result.json",
        native(1) | {"schema_version": 1, "trial_name": TRIAL, "config": config},
    )
    return root


def layout(sealed: list[str] | None, artifact: list[str] | None, verdict: Json) -> Json:
    return {"sealed": sealed, "artifact": artifact, "verdict": json.dumps(verdict)}


BARE_FAMILY = "harbor.revised-dispatch.decision"

JOB_CASES: dict[str, tuple[Json, str]] = {
    "revised_without_seal": (
        layout(None, journal()[:4], revised()),
        "unsealed_decision",
    ),
    "legacy_untyped_without_seal": (
        layout(None, journal()[:4], without(revised(), "schema")),
        "unsealed_decision",
    ),
    "foreign_with_open_prefix": (
        layout(None, journal()[:4], FOREIGN["unrelated_verdict"]),
        "partial_world",
    ),
    "foreign_only": (
        layout(None, None, FOREIGN["schema_only"]),
        "missing_journal",
    ),
    "foreign_with_seal": (
        layout(closed(1), journal()[:4], FOREIGN["unrelated_verdict"]),
        "usable",
    ),
    "invalid_in_scope_with_seal": (
        layout(closed(1), journal()[:4], revised(schema=BARE_FAMILY)),
        "invalid_evidence",
    ),
}


@pytest.mark.parametrize("name", sorted(JOB_CASES))
def test_job_status_for_decision_scope(tmp_path: Path, name: str) -> None:
    layout, status = JOB_CASES[name]
    root = job_with_trial(tmp_path / "job", **layout)
    report = inspect_job(root, boundary="returned")
    [row] = report["trials"]
    assert (row["directory"], row["status"]) == (TRIAL, status)
    assert (row["native_trial"], row["reward"]) == (NATIVE, 1)
    assert row["decision"] is None
