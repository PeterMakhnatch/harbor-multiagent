import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.decision_receipt import (
    control_inputs,
    control_seat,
    decision_verify,
    materialize,
    qualify,
)
from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import build_trial_lock
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import AgentInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True))


def prepare(root: Path) -> tuple[Path, Path, str]:
    task, workspace = root / "task", root / "work"
    materialize.materialize(task)
    assert control_inputs.main([str(task), str(workspace)]) == 0
    raw = (workspace / "enrollment.json").read_bytes()
    return task, workspace, "sha256:" + hashlib.sha256(raw).hexdigest()


def save_verifier(folder: Path, fate: str, cite: bool, world: UUID) -> None:
    events = [("handoff", "original"), ("inbox", "original"), ("resolve", "close")]
    cut = {
        "schema": decision_verify.CUT_SCHEMA,
        "run": str(world),
        "fate": fate,
        "channel": "deliver",
        "replace": None,
        "settled": [],
        "drained": ["original"] if fate == "settles" else [],
        "cited_at_decision": "original" if cite else None,
        "events": [
            {"seq": i, "kind": k, "choice": choice}
            for i, (k, choice) in enumerate(events, 1)
        ],
    }
    replay = decision_verify.evaluate(cut)
    save(folder / decision_verify.CUT_NAME, cut)
    save(folder / decision_verify.OBSERVATION_NAME, replay)
    save(folder / "reward.json", {"reward": int(fate == "settles")})


def save_native(
    enrolled: control_inputs.Enrollment, case: dict[str, Any], i: int
) -> None:
    ticket = case["ticket"]
    folder = enrolled.trials / ticket
    (folder / "verifier").mkdir(parents=True)
    request = TrialConfig.model_validate(enrolled.requests[ticket])
    (folder / "config.json").write_text(
        request.model_dump_json(indent=4, exclude={"agent"})
    )
    local = SimpleNamespace(
        path=request.task.path, content_hash=None, resolved_git_commit_id=None
    )
    lock = build_trial_lock(trial_config=request, task_download_result=local)
    (folder / "lock.json").write_text(lock.model_dump_json(indent=4, exclude_none=True))
    roles = (("outgoing", "oracle"), ("relief", "decision-receipt-control"))
    seats = [
        SeatResult(
            seat=s,
            under_test=True,
            agent_info=AgentInfo(name=a, version="1.0.0"),
            agent_result=(
                AgentContext(
                    metadata={
                        control_seat.METADATA_KEY: {
                            "schema": control_seat.RECORD_SCHEMA,
                            "seat": "relief",
                            "cite": case["cite"],
                            "client_sha256": control_seat.CLIENT_SHA256,
                            "outcome": "sealed",
                        }
                    }
                )
                if s == "relief"
                else None
            ),
            execution_disposition="returned",
        )
        for s, a in roles
    ]
    result = TrialResult(
        id=UUID(int=100 + i, version=4),
        task_name=enrolled.task["name"],
        trial_name=ticket,
        trial_uri=folder.as_uri(),
        task_id=request.task.get_task_id(),
        task_checksum=enrolled.task["checksum"],
        config=request,
        agent_info=AgentInfo(name="oracle", version="1.0.0"),
        seat_results=seats,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 2, tzinfo=UTC),
        verifier_result=VerifierResult(
            rewards={"reward": float(case["fate"] == "settles")}
        ),
    )
    (folder / "result.json").write_text(result.model_dump_json(indent=4))
    events = (
        "start",
        "environment-start",
        "agent-start",
        "agent-end",
        "verification-start",
        "end",
    )
    (folder / "trial-events.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "event": event,
                    "timestamp": f"2026-01-0{2 if event == 'end' else 1}T00:00:0{n}Z",
                },
                separators=(",", ":"),
            )
            + "\n"
            for n, event in enumerate(events)
        )
    )
    save_verifier(
        folder / "verifier", case["fate"], case["cite"], UUID(int=200 + i, version=4)
    )


def report(
    capsys: pytest.CaptureFixture[str], task: Path, work: Path, pin: str
) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = qualify.main([str(task), str(work), "--expected-enrollment", pin, "--json"])
    captured = capsys.readouterr()
    assert not captured.err and str(work) not in captured.out
    return code, json.loads(captured.out)


@pytest.mark.unit
def test_qualifies_only_four_independent_controls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task, workspace, pin = prepare(tmp_path)
    enrolled = control_inputs.load(task, workspace, pin)
    code, first = report(capsys, task, workspace, pin)
    assert (code, first["status"], first["counts"]["missing"]) == (
        3,
        "awaiting_evidence",
        4,
    )
    for i, case in enumerate(control_inputs.cases()):
        save_native(enrolled, case, i)
    code, complete = report(capsys, task, workspace, pin)
    assert (code, complete["status"], complete["counts"]["verified"]) == (
        0,
        "qualified",
        4,
    )
    assert {
        (r["fate"], r["request_citation"], r["reward"], r["citation_status"])
        for r in complete["cases"]
    } == {
        (f, c, int(f == "settles"), "cited_at_decision" if c else "returned_not_cited")
        for f in ("settles", "fails")
        for c in (False, True)
    }
    rows = list(control_inputs.cases())
    other = enrolled.trials / rows[1]["ticket"] / "verifier"
    save_verifier(other, rows[1]["fate"], rows[1]["cite"], UUID(int=200, version=4))
    code, duplicate = report(capsys, task, workspace, pin)
    assert (
        code,
        duplicate["status"],
        duplicate["reused_world_ids"],
        duplicate["reused_trial_ids"],
    ) == (1, "inconsistent", 1, 0)
    assert duplicate["counts"]["conflict"] == 2
    assert all(
        r["reward"] is None for r in duplicate["cases"] if r["status"] == "conflict"
    )


@pytest.mark.unit
def test_unexpected_entry_and_changed_enrollment_do_not_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task, workspace, pin = prepare(tmp_path)
    trials = control_inputs.load(task, workspace, pin).trials
    (trials / "not-enrolled").mkdir(parents=True)
    code, extra = report(capsys, task, workspace, pin)
    assert (
        code,
        extra["status"],
        extra["unexpected_entries"],
        extra["counts"]["missing"],
    ) == (1, "inconsistent", True, 4)
    enrolled = workspace / "enrollment.json"
    enrolled.write_bytes(enrolled.read_bytes() + b"\n")
    assert (
        qualify.main(
            [str(task), str(workspace), "--expected-enrollment", pin, "--json"]
        )
        == 2
    )
    out, err = capsys.readouterr()
    assert not out and "Qualification refused" in err and str(workspace) not in err


@pytest.mark.unit
def test_qualifier_holds_an_unenrolled_native_lock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    task, workspace, pin = prepare(tmp_path)
    enrolled = control_inputs.load(task, workspace, pin)
    case = control_inputs.cases()[0]
    save_native(enrolled, case, 0)
    path = enrolled.trials / case["ticket"] / "lock.json"
    lock = json.loads(path.read_bytes())
    assert lock["verifier"]["disable"] is False
    lock["verifier"]["disable"] = True
    save(path, lock)
    code, held = report(capsys, task, workspace, pin)
    row = next(row for row in held["cases"] if row["ticket"] == case["ticket"])
    assert (code, held["status"], row["status"], row["reward"]) == (
        1,
        "inconsistent",
        "conflict",
        None,
    )


@pytest.mark.unit
def test_missing_runtime_is_not_a_failed_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    task, workspace, pin = prepare(tmp_path)

    def missing():
        raise SystemExit("installed fork unavailable")

    monkeypatch.setattr(control_inputs, "lock_native", missing)
    capsys.readouterr()
    assert qualify.main([str(task), str(workspace), "--expected-enrollment", pin]) == 2
    out, err = capsys.readouterr()
    assert not out and "Qualification refused" in err
