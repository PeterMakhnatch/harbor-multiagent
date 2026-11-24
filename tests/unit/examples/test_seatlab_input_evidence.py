import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from examples.seatlab.workbench import input_evidence, native_account
from examples.seatlab.workbench.input_evidence import (
    DUPLICATE,
    LOCK_COUNT,
    LOCK_FIELDS,
    LOCK_NATIVE,
    LOCK_TRIALS,
    LOCK_VERSION,
    MOVED,
    PIN_MISMATCH,
    PIN_SHAPE,
    RESULT_JSON,
    SURPLUS,
    TOTAL,
    TRIAL_INSTALL,
    TRIAL_REGRADE,
    TRIAL_TEAM,
    Final,
    read_job,
)
from examples.seatlab.workbench.seat_stream import Malformed
from harbor.models.job.config import RetryConfig
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)

JOB = "3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b"
OTHER_JOB = "8c7b6a5d-4e3f-4a1b-9c8d-7e6f5a4b3c2d"
FIRST = "1e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37"
SECOND = "7c4e6a8b-0d1f-4a3c-8e5b-7d9f1a3c5e7b"
THIRD = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
TASK = "/tasks/pair-review"
SECRET = "sk-live-7f3a9c2e5b1d4068"
Json = dict[str, Any]
Scenario = dict[str, Any]
Mutate = Callable[[Scenario], Scenario]


def seats() -> list[Participant]:
    return [
        Participant(seat="author", name="terminus-2", model_name="openai/gpt-5"),
        Participant(seat="critic", name="nop"),
    ]


def trial_lock(world: str, timeout: float = 1.0) -> Json:
    lock = TrialLock(
        task=TaskLock(
            name="org/pair-review",
            type="local",
            digest=f"sha256:{'a' * 64}",
            path=Path(TASK),
        ),
        timeout_multiplier=timeout,
        agent=AgentConfig(name="oracle"),
        participants=seats(),
        environment=EnvironmentConfig(env={"WORLD": world}),
        verifier=VerifierLock(),
    )
    return json.loads(lock.model_dump_json(indent=4, exclude_none=True))


def trial_result(trial: str, slot: str, world: str, reward: float) -> Json:
    config = TrialConfig(
        task=TaskConfig(path=Path(TASK)),
        trial_name=slot,
        job_id=UUID(JOB),
        agent=AgentConfig(name="oracle"),
        participants=seats(),
        environment=EnvironmentConfig(env={"WORLD": world}),
    )
    return {
        "id": trial,
        "task_name": "org/pair-review",
        "trial_name": slot,
        "trial_uri": f"file:///jobs/{slot}",
        "task_id": {"path": TASK},
        "task_checksum": "c" * 64,
        "config": json.loads(config.model_dump_json()),
        "agent_info": {"name": "oracle", "version": "1"},
        "reward_valid": True,
        "verifier_result": {"rewards": {"reward": reward}},
        "seat_results": [
            {"seat": "author", "under_test": True, "execution_disposition": "returned"},
            {
                "seat": "critic",
                "under_test": False,
                "execution_disposition": "returned",
            },
        ],
    }


def scenario() -> Scenario:
    calm, storm = trial_lock("calm"), trial_lock("storm")
    return {
        "header": {
            "schema_version": 3,
            "created_at": "2026-11-20T15:00:00Z",
            "harbor": {"version": "0.1.0"},
            "n_concurrent_trials": 2,
            "retry": json.loads(RetryConfig().model_dump_json()),
            "trials": [calm, json.loads(json.dumps(calm)), storm],
        },
        "summary": {
            "id": JOB,
            "n_total_trials": 3,
            "stats": {"n_retries": 0, "n_completed_trials": 2},
            "finished_at": None,
        },
        "dirs": {
            "pair__a1": {
                "lock": trial_lock("calm"),
                "result": trial_result(FIRST, "pair__a1", "calm", 1.0),
            },
            "pair__a3": {"lock": trial_lock("calm"), "result": None},
            "pair__b2": {
                "lock": trial_lock("storm"),
                "result": trial_result(SECOND, "pair__b2", "storm", 0),
            },
        },
    }


def write_job(root: Path, plan: Scenario) -> Path:
    root.mkdir()
    (root / "lock.json").write_text(json.dumps(plan["header"], indent=4))
    (root / "result.json").write_text(json.dumps(plan["summary"], indent=4))
    (root / "job.log").write_text("log")
    for name, files in plan["dirs"].items():
        (root / name).mkdir()
        for kind in ("lock", "result"):
            if files[kind] is not None:
                (root / name / f"{kind}.json").write_text(json.dumps(files[kind]))
    return root


def pin(job: Path) -> str:
    return f"sha256:{hashlib.sha256((job / 'lock.json').read_bytes()).hexdigest()}"


def test_reads_registered_cells_and_final_results(tmp_path: Path) -> None:
    job = write_job(tmp_path / "job", scenario())
    evidence = read_job(job, None)
    calm, again, storm = evidence.registered
    assert calm == again and calm != storm
    assert evidence.job == JOB
    assert evidence.lock_digest == pin(job)
    assert evidence.closed is False and evidence.retries == 0
    assert evidence.lock_without_result == 1
    assert evidence.finals == (
        Final(FIRST, calm, True, "one"),
        Final(SECOND, storm, True, "zero"),
    )
    assert read_job(job, pin(job)) == evidence


def test_checks_the_pin_before_any_trial_directory(tmp_path: Path) -> None:
    job = write_job(tmp_path / "job", scenario())
    (job / "pair__a1" / "result.json").write_text("{")
    with pytest.raises(Malformed) as caught:
        read_job(job, f"sha256:{'0' * 64}")
    assert str(caught.value) == PIN_MISMATCH
    with pytest.raises(Malformed) as caught:
        read_job(job, "sha256:short")
    assert str(caught.value) == PIN_SHAPE
    with pytest.raises(Malformed) as caught:
        read_job(job, pin(job))
    assert str(caught.value) == RESULT_JSON


def test_missing_trial_lock_keeps_the_final_unbound(tmp_path: Path) -> None:
    plan = scenario()
    plan["dirs"]["pair__a1"]["lock"] = None
    evidence = read_job(write_job(tmp_path / "job", plan), None)
    assert evidence.finals[0] == Final(FIRST, None, None, "one")
    assert evidence.finals[1].agrees is True


def test_result_inputs_that_differ_from_the_saved_lock_disagree(
    tmp_path: Path,
) -> None:
    plan = scenario()
    plan["dirs"]["pair__a1"]["result"] = trial_result(FIRST, "pair__a1", "storm", 1)
    evidence = read_job(write_job(tmp_path / "job", plan), None)
    assert [final.agrees for final in evidence.finals] == [False, True]
    assert evidence.finals[0].cell == evidence.registered[0]


def invalid(result: Json) -> Json:
    result["reward_valid"] = False
    return result


def half(result: Json) -> Json:
    result["verifier_result"]["rewards"]["reward"] = 0.5
    return result


def ungraded(result: Json) -> Json:
    result["reward_valid"] = False
    del result["verifier_result"]
    return result


@pytest.mark.parametrize(
    ("mutate", "grade"),
    [(invalid, "invalid"), (half, "other"), (ungraded, "unknown")],
)
def test_grades_need_a_valid_native_reward(
    tmp_path: Path, mutate: Callable[[Json], Json], grade: str
) -> None:
    plan = scenario()
    mutate(plan["dirs"]["pair__a1"]["result"])
    evidence = read_job(write_job(tmp_path / "job", plan), None)
    assert evidence.finals[0].grade == grade


def edit(*keys: str | int, value: Any) -> Mutate:
    def mutate(plan: Scenario) -> Scenario:
        target: Any = plan
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        return plan

    return mutate


def surplus(plan: Scenario) -> Scenario:
    plan["dirs"]["pair__c4"] = {
        "lock": trial_lock("storm"),
        "result": trial_result(THIRD, "pair__c4", "storm", 1),
    }
    return plan


def empty(plan: Scenario) -> Scenario:
    plan["header"]["trials"] = []
    plan["summary"]["n_total_trials"] = 0
    plan["summary"]["stats"]["n_completed_trials"] = 0
    plan["dirs"] = {}
    return plan


def solo(plan: Scenario) -> Scenario:
    del plan["header"]["trials"][2]["participants"][1]
    return plan


def overused(plan: Scenario) -> Scenario:
    plan["dirs"]["pair__a3"]["result"] = trial_result(THIRD, "pair__a3", "calm", 1)
    plan["dirs"]["pair__b2"]["lock"] = trial_lock("calm")
    return plan


@pytest.mark.parametrize(
    "mutate",
    [overused, edit("dirs", "pair__b2", "lock", value=trial_lock("storm", 2.0))],
)
def test_readable_unregistered_inputs_reach_the_mismatch_report(
    tmp_path: Path, mutate: Mutate
) -> None:
    from examples.seatlab.workbench.cohort import summarize

    result = summarize(read_job(write_job(tmp_path / "job", mutate(scenario())), None))
    assert result["status"] == "inconsistent"
    assert all(g["grades"] is None for g in result["groups"] if g["excess_lock_copies"])


REGRADE = {"action": "regrade", "type": "hub", "trial_id": THIRD}


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (edit("summary", "n_total_trials", value=2), LOCK_COUNT),
        (edit("header", "schema_version", value=2), LOCK_VERSION),
        (edit("header", "replay", value=True), LOCK_FIELDS),
        (edit("header", "n_concurrent_trials", value="two"), LOCK_NATIVE),
        (empty, LOCK_TRIALS),
        (solo, TRIAL_TEAM),
        (edit("header", "trials", 0, "install_only", value=True), TRIAL_INSTALL),
        (edit("header", "trials", 0, "source_trial", value=REGRADE), TRIAL_REGRADE),
        (edit("dirs", "pair__b2", "result", "id", value=FIRST), DUPLICATE),
        (surplus, SURPLUS),
        (
            edit("dirs", "pair__b2", "result", "config", "job_id", value=OTHER_JOB),
            "trial result belongs to another job",
        ),
        (
            edit("dirs", "pair__b2", "result", "trial_name", value="pair__a1"),
            "trial result names another directory",
        ),
    ],
)
def test_refuses_incompatible_jobs(tmp_path: Path, mutate: Mutate, reason: str) -> None:
    job = write_job(tmp_path / "job", mutate(scenario()))
    with pytest.raises(ValueError) as caught:
        read_job(job, None)
    assert str(caught.value) == reason


def test_a_lock_only_slot_counts_toward_enrollment(tmp_path: Path) -> None:
    plan = scenario()
    plan["dirs"]["pair__c4"] = {"lock": trial_lock("storm"), "result": None}
    with pytest.raises(Malformed) as caught:
        read_job(write_job(tmp_path / "job", plan), None)
    assert str(caught.value) == SURPLUS


@pytest.mark.parametrize("moved", ["result.json", "lock.json", "pair__a1/lock.json"])
def test_refuses_inputs_that_change_during_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moved: str
) -> None:
    job = write_job(tmp_path / "job", scenario())
    original = input_evidence.result_view

    def touch(document: Json, *, raw: bytes) -> str | None:
        found = os.lstat(job / moved)
        os.utime(job / moved, ns=(found.st_atime_ns, found.st_mtime_ns + 10**9))
        return original(document, raw=raw)

    monkeypatch.setattr(input_evidence, "result_view", touch)
    with pytest.raises(Malformed) as caught:
        read_job(job, None)
    assert str(caught.value) == MOVED


def test_bounds_the_cumulative_bytes_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = write_job(tmp_path / "job", scenario())
    header = (job / "lock.json").stat().st_size + (job / "result.json").stat().st_size
    monkeypatch.setattr(native_account, "MAX_TOTAL_BYTES", header + 8)
    with pytest.raises(Malformed) as caught:
        read_job(job, None)
    assert str(caught.value) == TOTAL


def test_errors_leak_no_names_or_values(tmp_path: Path) -> None:
    plan = scenario()
    slot = f"pair__{SECRET}"
    plan["dirs"][slot] = plan["dirs"].pop("pair__b2")
    plan["dirs"][slot]["result"] = trial_result(SECOND, slot, SECRET, 1)
    plan["dirs"][slot]["result"]["config"]["job_id"] = OTHER_JOB
    job = write_job(tmp_path / f"job-{SECRET}", plan)
    with pytest.raises(Malformed) as caught:
        read_job(job, None)
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
