import copy
import json
import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from examples.native_study import native
from examples.seatlab.dispatch_recall import participant
from examples.seatlab.dispatch_recall.collect import (
    INFRASTRUCTURE,
    PRIVATE_CUT,
    UNSET,
    collect,
    outcomes,
)
from examples.seatlab.dispatch_recall.design import design
from examples.seatlab.dispatch_recall.native import Session, diagnose
from examples.seatlab.dispatch_recall.prepare import (
    CONFIG,
    LOCK,
    ORACLE_ROSTER,
    RECEIPT,
    TASK_DIR,
    prepare,
)
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import (
    AgentInfo,
    ExceptionInfo,
    SeatResult,
    TimingInfo,
    TrialResult,
)
from harbor.models.verifier.result import VerifierResult

TASK = Path(__file__).resolve().parents[3] / "examples" / "tasks" / "dispatch-recall"
VERDICT = "harbor.dispatch-recall.verdict/v1"
ADAPTER = "seatlab-dispatch-recall-participant"


@pytest.fixture
def task(tmp_path: Path) -> Path:
    copy_dir = tmp_path / "task" / TASK_DIR
    shutil.copytree(
        TASK, copy_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    return copy_dir


@pytest.fixture
def plan() -> dict[str, Any]:
    return design([11, 12], 5)


@pytest.fixture
def endpoints() -> Iterator[dict[str, str]]:
    owner = Path(tempfile.mkdtemp(prefix="dr.", dir="/tmp")).resolve()
    try:
        yield {turn: str(owner / turn) for turn in participant.TURNS}
    finally:
        shutil.rmtree(owner, ignore_errors=True)


def _slot(
    plan: dict[str, Any], task: Path, out: Path, roster: dict[str, Any], index: int = 0
) -> tuple[dict[str, Any], str]:
    job = plan["jobs"][index]
    pin = prepare(plan, job["job_id"], task, roster, out)
    return job, pin["receipt_sha256"]


def _play(job: dict[str, Any], commit: str | None = "correct") -> dict[str, Any]:
    s = job["session"]
    session = Session(s["seed"], s["transition"], s["timing"])
    first = session.view("loader")
    session.apply("loader", {"op": "stage", "bay": first["initial_bay"]})
    current = session.view("depot")["current_bay"]
    session.apply("depot", {"op": "report", "bay": current})
    if commit is not None:
        other = next(bay for bay in first["bays"] if bay != current)
        bay = current if commit == "correct" else other
        ticket = session.view("loader")["ticket"]
        session.apply("loader", {"op": "commit", "bay": bay, "ticket": ticket})
        ticket = session.view("loader")["ticket"]
        session.apply("loader", {"op": "close", "ticket": ticket})
    return session.snapshot()


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")


def _verifier(folder: Path, cut: dict[str, Any] | None) -> dict[str, Any] | None:
    folder.mkdir(parents=True)
    if cut is None:
        _write(folder / "verdict.json", INFRASTRUCTURE)
        return None
    reward = 0 if cut["reward"] is None else cut["reward"]
    _write(folder / PRIVATE_CUT, cut)
    _write(folder / "diagnostics.json", diagnose(cut))
    _write(
        folder / "verdict.json",
        {
            "schema": VERDICT,
            "disposition": "missing_commit" if cut["reward"] is None else "committed",
            "reward": reward,
            "closed": len(cut["actions"]) == 4,
        },
    )
    _write(folder / "reward.json", {"reward": reward})
    return {"reward": reward}


def _archive(
    out: Path,
    cut: dict[str, Any] | None,
    agent: str = "oracle",
    finished: bool = True,
    seat_error: str | None = None,
) -> Path:
    config = TrialConfig.model_validate(json.loads((out / CONFIG).read_text()))
    lock = json.loads((out / LOCK).read_text())
    receipt = json.loads((out / RECEIPT).read_text())
    trial = Path(config.trials_dir) / config.trial_name
    trial.mkdir(parents=True)
    (trial / "config.json").write_text(
        config.model_dump_json(indent=4, exclude={"agent"})
    )
    (trial / "lock.json").write_text((out / LOCK).read_text())
    if not finished:
        return trial
    rewards = _verifier(trial / "verifier", cut)
    now = datetime.now(UTC)
    info = AgentInfo(name=agent, version="1.0.0")
    failure = ExceptionInfo(
        exception_type="RewardFileNotFoundError",
        exception_message="private detail",
        exception_traceback="",
        occurred_at=now,
    )
    seat_failure = None
    if seat_error is not None:
        seat_failure = ExceptionInfo(
            exception_type=seat_error,
            exception_message="private seat detail",
            exception_traceback="",
            occurred_at=now,
        )
    trial_failure = failure if rewards is None else seat_failure
    result = TrialResult(
        task_name=receipt["task_name"],
        trial_name=config.trial_name,
        trial_uri=trial.as_uri(),
        task_id=config.task.get_task_id(),
        source=config.task.source,
        task_checksum=receipt["task_checksum"],
        config=config,
        agent_info=info,
        verifier_result=None if rewards is None else VerifierResult(rewards=rewards),
        verifier_environment_mode=lock["verifier"].get("environment_mode"),
        exception_info=trial_failure,
        started_at=now,
        finished_at=now,
        verifier=TimingInfo(started_at=now, finished_at=now),
        seat_results=[
            SeatResult(
                seat=seat,
                under_test=True,
                agent_info=info,
                exception_info=seat_failure if seat == "loader" else None,
            )
            for seat in ("loader", "depot")
        ],
    )
    (trial / "result.json").write_text(result.model_dump_json(indent=4))
    return trial


def test_slot_without_a_trial_is_no_trial(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    found = collect(plan, job["job_id"], out, pin)
    assert found["status"] == "no_trial" and found["cut"] is None
    assert found["evidence"] == "scripted_control"
    assert found["provenance"]["receipt_sha256"] == pin
    assert "seed" not in json.dumps(found["record"])
    assert all(found["record"][key] is None for key in UNSET)


def test_other_receipt_pin_is_refused(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, _ = _slot(plan, task, out, ORACLE_ROSTER)
    with pytest.raises(ValueError, match="expected receipt"):
        collect(plan, job["job_id"], out, "0" * 64)


def test_task_change_after_preparation_is_refused(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    (task / "instructions" / "loader.md").write_text("changed\n")
    with pytest.raises(ValueError, match="prepared"):
        collect(plan, job["job_id"], out, pin)


def test_trial_without_result_is_unfinished(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    _archive(out, None, finished=False)
    found = collect(plan, job["job_id"], out, pin)
    assert found["status"] == "unfinished" and found["cut"] is None
    assert found["record"]["exceptions"] is None and found["record"]["physical"] is None
    assert set(found["provenance"]["trial"]) == {"config", "lock"}


def test_scripted_committed_cut_is_verified_and_labeled(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    cut = _play(job)
    _archive(out, cut)
    found = collect(plan, job["job_id"], out, pin)
    record = found["record"]
    assert found["status"] == "verified" and found["cut"] == cut
    assert record["physical"] == {"status": "correct", "reward": 1}
    assert record["disposition"] == "committed" and record["closed"] is True
    assert record["owner_requests"] == {"status": "not_applicable"}
    assert record["exceptions"] == [] and record["reward_valid"] is True
    assert set(found["provenance"]["trial"]) == {
        "config",
        "lock",
        "result",
        "private_cut",
        "reward",
        "verdict",
        "diagnostics",
    }
    with pytest.raises(ValueError, match="scripted control"):
        outcomes(plan, [found])


def test_missing_commit_grade_is_not_a_physical_zero(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    _archive(out, _play(job, commit=None))
    record = collect(plan, job["job_id"], out, pin)["record"]
    assert record["native_reward"] == 0 and record["disposition"] == "missing_commit"
    assert record["physical"] == {"status": "uncommitted", "reward": None}
    assert record["native_reward_meaning"].startswith("grade_zero_without_commit")


def test_no_commit_with_seat_exception_keeps_grade_and_exception(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    _archive(out, _play(job, commit=None), seat_error="AgentTimeoutError")
    found = collect(plan, job["job_id"], out, pin)
    record = found["record"]
    assert found["status"] == "verified"
    assert record["disposition"] == "missing_commit" and record["native_reward"] == 0
    assert record["physical"] == {"status": "uncommitted", "reward": None}
    assert record["exceptions"] == [
        {"where": "trial", "type": "AgentTimeoutError"},
        {"where": "loader", "type": "AgentTimeoutError"},
    ]
    assert "private seat detail" not in json.dumps(found)


def test_infrastructure_error_without_cut_reports_exceptions(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    _archive(out, None)
    found = collect(plan, job["job_id"], out, pin)
    record = found["record"]
    assert found["status"] == "no_retained_cut" and found["cut"] is None
    assert record["disposition"] == "infrastructure_error"
    assert record["exceptions"] == [
        {"where": "trial", "type": "RewardFileNotFoundError"}
    ]
    assert "private detail" not in json.dumps(found)


def test_reward_file_contradicting_the_cut_is_rejected(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    trial = _archive(out, _play(job, commit="wrong"))
    _write(trial / "verifier" / "reward.json", {"reward": 1})
    found = collect(plan, job["job_id"], out, pin)
    assert found["status"] == "rejected" and found["cut"] is None
    assert "reward" in found["record"]["reason"]


def test_malformed_retained_cut_is_rejected_not_missing(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    trial = _archive(out, _play(job))
    (trial / "verifier" / PRIVATE_CUT).write_text("{")
    found = collect(plan, job["job_id"], out, pin)
    assert found["status"] == "rejected"
    assert found["record"]["reason"] == "native record is not valid JSON"


def test_cut_off_the_seeded_layout_is_rejected(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    s = job["session"]
    foreign = {**job, "session": {**s, "seed": s["seed"] + 1000}}
    cut = {**_play(foreign), "seed": s["seed"]}
    _archive(out, cut)
    found = collect(plan, job["job_id"], out, pin)
    assert found["status"] == "rejected"
    assert found["record"]["reason"] == "cut layout differs from the seeded layout"


def test_symlinked_trial_is_rejected(
    task: Path, plan: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "prepared"
    job, pin = _slot(plan, task, out, ORACLE_ROSTER)
    trial = _archive(out, _play(job))
    moved = tmp_path / "elsewhere"
    trial.rename(moved)
    trial.symlink_to(moved, target_is_directory=True)
    assert collect(plan, job["job_id"], out, pin)["status"] == "rejected"


def test_host_adapter_cut_feeds_assess_with_unconfirmed_owner_requests(
    task: Path, plan: dict[str, Any], tmp_path: Path, endpoints: dict[str, str]
) -> None:
    out = tmp_path / "prepared"
    roster = {"participants": participant.roster(native(), endpoints)}
    job, pin = _slot(plan, task, out, roster)
    cut = _play(job)
    _archive(out, cut, agent=ADAPTER)
    found = collect(plan, job["job_id"], out, pin)
    assert found["evidence"] == "free_team" and found["status"] == "verified"
    assert found["record"]["owner_requests"]["counts"]["confirmed"] == 0
    assert outcomes(plan, [found]) == {job["job_id"]: cut}
    other = copy.deepcopy(found)
    other["job_id"] = plan["jobs"][1]["job_id"]
    other["provenance"]["team_agents"] = {"loader": {}, "depot": {}}
    with pytest.raises(ValueError, match="mix"):
        outcomes(plan, [found, other])


def test_rejected_archive_blocks_assessment(
    task: Path, plan: dict[str, Any], tmp_path: Path, endpoints: dict[str, str]
) -> None:
    out = tmp_path / "prepared"
    roster = {"participants": participant.roster(native(), endpoints)}
    job, pin = _slot(plan, task, out, roster)
    trial = _archive(out, _play(job), agent=ADAPTER)
    (trial / "verifier" / "diagnostics.json").write_text("{}\n")
    found = collect(plan, job["job_id"], out, pin)
    assert found["status"] == "rejected" and found["evidence"] == "free_team"
    with pytest.raises(ValueError, match="rejected"):
        outcomes(plan, [found])


def test_host_adapter_stays_collectable_after_owner_sockets_are_removed(
    task: Path, plan: dict[str, Any], tmp_path: Path, endpoints: dict[str, str]
) -> None:
    out = tmp_path / "prepared"
    roster = {"participants": participant.roster(native(), endpoints)}
    job, pin = _slot(plan, task, out, roster)
    _archive(out, _play(job), agent=ADAPTER)
    shutil.rmtree(Path(endpoints["stage"]).parent)
    assert collect(plan, job["job_id"], out, pin)["status"] == "verified"
