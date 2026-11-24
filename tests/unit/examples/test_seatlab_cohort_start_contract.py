import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from uuid import UUID

import pytest

from examples.seatlab.workbench.arm_runtime import GROUPS, inspect_record
from examples.seatlab.workbench.cohort import GRADES, main
from examples.seatlab.workbench.input_keeper import InputStartKeeper
from examples.seatlab.workbench.input_keys import cell, declared_view, result_view
from examples.seatlab.workbench.input_witness import (
    Witness,
    binding,
    encode,
    read_starts,
)
from examples.seatlab.workbench.native_account import _slot, project_result
from examples.seatlab.workbench.private_receipt import put_once
from harbor.models.job.config import RetryConfig
from harbor.models.job.lock import (
    HarborLockInfo,
    JobLock,
    TrialLock,
    build_trial_lock,
)
from harbor.models.job.result import JobResult, JobStats
from harbor.models.task.task import Task
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, ModelInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.seats import check_launch
from tests.unit.examples.test_seatlab_input_keeper import Job, linkat

if TYPE_CHECKING:
    from harbor.job import Job as HarborJob

JOB = UUID("5d1e7a3c-9b2f-4c6d-8e0a-1f3b5c7d9e2a")
OTHER_JOB = UUID("c4a8e2f6-1d3b-4a5c-9e7f-0b2d4f6a8c1e")
LEFT, RIGHT = "relay__a1b2c3d", "relay__e5f6a7b"
SLOTS = (LEFT, RIGHT)
TRIALS = {
    LEFT: UUID("0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0"),
    RIGHT: UUID("9e8d7c6b-5a49-4837-a625-14f3e2d1c0b9"),
}
ABANDONED = UUID("7b3c9d1e-2f4a-4b6c-8d0e-3a5c7e9f1b2d")
AGENT_TIMEOUT_MULTIPLIER = {LEFT: 1.0, RIGHT: 1.5}
REWARD = {LEFT: 0, RIGHT: 1}
MODELS = {"booker": "openai/gpt-5", "reporter": "anthropic/claude-sonnet-4-5"}
SECRET = "PRIVATE-WORLD-BRIEF-4e91"
STARTED = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
FINISHED = datetime(2026, 11, 24, 15, 30, tzinfo=UTC)
QUIET = {"matched": 0, "missing": 0, "conflicts": 0, "unverifiable": 0}
Json = dict[str, Any]


class Team(NamedTuple):
    root: Path
    task: Path
    job: Path
    archive: Path
    keeper: InputStartKeeper
    events: dict[UUID, TrialHookEvent]
    keys: tuple[str, str]


def layout(root: Path) -> tuple[Path, Path, Path]:
    task = root / "tasks" / "relay"
    source = Path(__file__).resolve().parents[3] / "examples/tasks/revised-dispatch"
    shutil.copytree(source, task, ignore=shutil.ignore_patterns("__pycache__"))
    job = root / "jobs" / "relay-pair"
    job.mkdir(parents=True)
    archive = root / "archive"
    archive.mkdir()
    os.chmod(archive, 0o700)
    return task, job, archive


def trial_config(task: Path, job: Path, slot: str) -> TrialConfig:
    return TrialConfig(
        task=TaskConfig(path=task),
        trial_name=slot,
        trials_dir=job,
        job_id=JOB,
        agent_timeout_multiplier=AGENT_TIMEOUT_MULTIPLIER[slot],
        participants=[
            Participant(seat=seat, name="terminus-2", model_name=model)
            for seat, model in MODELS.items()
        ],
        environment=EnvironmentConfig(env={"SEATLAB_WORLD": SECRET}),
    )


def trial_lock(task: Path, config: TrialConfig) -> TrialLock:
    local = SimpleNamespace(path=task, content_hash=None, resolved_git_commit_id=None)
    return build_trial_lock(trial_config=config, task_download_result=local)


def start(config: TrialConfig, lock: TrialLock, trial: UUID) -> TrialHookEvent:
    task_id = config.task.get_task_id()
    assert config.task.path is not None
    native_task = Task(config.task.path)
    check_launch(config, native_task)
    result = TrialResult(
        id=trial,
        task_name=native_task.name,
        trial_name=config.trial_name,
        trial_uri=(config.trials_dir / config.trial_name).as_uri(),
        task_id=task_id,
        task_checksum=native_task.checksum,
        config=config,
        agent_info=AgentInfo(name="terminus-2", version="1"),
        started_at=STARTED,
    )
    return TrialHookEvent(
        event=TrialEvent.START,
        task_name=result.task_name,
        config=config,
        result=result,
        lock=lock,
    )


def final(event: TrialHookEvent) -> TrialResult:
    seats = []
    for seat, model in MODELS.items():
        provider, name = model.split("/")
        info = ModelInfo(name=name, provider=provider)
        seats.append(
            SeatResult(
                seat=seat,
                under_test=seat == "booker",
                agent_info=AgentInfo(name="terminus-2", version="1", model_info=info),
                execution_disposition="returned",
            )
        )
    reward = REWARD[event.config.trial_name]
    return event.result.model_copy(
        update={
            "finished_at": FINISHED,
            "verifier_result": VerifierResult(rewards={"reward": reward}),
            "seat_results": seats,
        }
    )


def enroll(job: Path, locks: list[TrialLock]) -> None:
    header = JobLock(
        created_at=STARTED,
        harbor=HarborLockInfo(version="0.1.0"),
        n_concurrent_trials=2,
        retry=RetryConfig(),
        trials=locks,
    )
    (job / "lock.json").write_text(header.model_dump_json(indent=4, exclude_none=True))


def close(job: Path, retries: int) -> None:
    root = JobResult(
        id=JOB,
        started_at=STARTED,
        finished_at=FINISHED,
        n_total_trials=2,
        stats=JobStats(n_completed_trials=2, n_retries=retries),
    )
    (job / "result.json").write_text(root.model_dump_json(indent=4))


def saved(job: Path, slot: str, name: str = "lock.json") -> Json:
    return json.loads((job / slot / name).read_bytes())


async def team(root: Path, *, abandoned: bool = False) -> Team:
    task, job, archive = layout(root)
    configs = {slot: trial_config(task, job, slot) for slot in SLOTS}
    locks = {slot: trial_lock(task, configs[slot]) for slot in SLOTS}
    enroll(job, [locks[slot] for slot in SLOTS])
    keeper = InputStartKeeper(str(archive))
    host = Job(job, JOB)
    await keeper.on_job_start(cast("HarborJob", host))
    assert host.started == [keeper._capture]
    events: dict[UUID, TrialHookEvent] = {}
    for slot in SLOTS:
        (job / slot).mkdir()
        (job / slot / "lock.json").write_text(
            locks[slot].model_dump_json(indent=4, exclude_none=True)
        )
        retried = abandoned and slot == LEFT
        for trial in [ABANDONED, TRIALS[slot]] if retried else [TRIALS[slot]]:
            event = start(configs[slot], locks[slot], trial)
            for callback in host.started:
                await callback(event)
            events[trial] = event
        (job / slot / "result.json").write_text(
            final(events[TRIALS[slot]]).model_dump_json(indent=4)
        )
    close(job, int(abandoned))
    assert len(os.listdir(archive)) == len(events)
    keys = (cell(saved(job, LEFT)).key, cell(saved(job, RIGHT)).key)
    return Team(root, task, job, archive, keeper, events, keys)


def clone(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def rewrite(path: Path, change: Callable[[Json], None]) -> None:
    document = json.loads(path.read_bytes())
    change(document)
    path.write_text(json.dumps(document, indent=4))


def tally(**counts: int) -> dict[str, int]:
    return {grade: counts.get(grade, 0) for grade in GRADES}


def group(report: Json, key: str) -> Json:
    [found] = [row for row in report["groups"] if row["input"] == key]
    return found


def cohort(
    capsys: pytest.CaptureFixture[str],
    built: Team,
    *,
    job: Path | None = None,
    starts: Path | None = None,
) -> tuple[int, Json]:
    argv = [str(job or built.job), "--pair", *built.keys]
    if starts is not None:
        argv += ["--starts", str(starts)]
    code = main([*argv, "--json"])
    out, err = capsys.readouterr()
    assert err == ""
    private = (SECRET, str(built.root), "terminus-2", *SLOTS, *MODELS.values())
    for leaked in (*private, *(str(trial) for trial in built.events)):
        assert leaked not in out
    return code, json.loads(out)


@linkat
@pytest.mark.asyncio
async def test_two_full_locks_share_one_declared_view_and_native_identity(
    tmp_path: Path,
) -> None:
    built = await team(tmp_path)
    left, right = saved(built.job, LEFT), saved(built.job, RIGHT)
    assert json.loads((built.job / "lock.json").read_bytes())["trials"] == [left, right]
    header = JobLock.model_validate_json((built.job / "lock.json").read_bytes())
    assert (header.schema_version, len(header.trials)) == (3, 2)
    root = JobResult.model_validate_json((built.job / "result.json").read_bytes())
    assert (root.id, root.n_total_trials, root.finished_at) == (JOB, 2, FINISHED)
    assert root.stats.n_completed_trials == 2
    cells = (cell(left), cell(right))
    assert cells[0].key != cells[1].key and cells[0].view == cells[1].view
    runtimes = (inspect_record(left), inspect_record(right))
    assert {g for g in GROUPS if runtimes[0].groups[g] != runtimes[1].groups[g]} == {
        "timeouts"
    }
    assert (runtimes[0].order, runtimes[0].participants, runtimes[0].env) == (
        runtimes[1].order,
        runtimes[1].participants,
        runtimes[1].env,
    )
    witnesses = read_starts(built.archive, str(JOB))
    for slot, found in zip(SLOTS, cells, strict=True):
        document = saved(built.job, slot, "result.json")
        event, trial = built.events[TRIALS[slot]], str(TRIALS[slot])
        assert result_view(document) == declared_view(document) == found.view
        bound = binding(document)
        assert bound is not None
        assert witnesses[trial] == Witness(
            str(JOB), _slot(slot), trial, found.key, bound
        )
        assert event.lock.model_dump(mode="json", exclude_none=True) == saved(
            built.job, slot
        )
        assert document["config"] == event.config.model_dump(mode="json")
        native_task = Task(built.task)
        assert document["task_name"] == native_task.name
        assert document["task_checksum"] == native_task.checksum
        row = project_result(document, job_id=str(JOB), slot_name=slot)
        assert (row["trial"], row["slot"], row["grade_state"], row["reward"]) == (
            str(event.trial_id),
            _slot(slot),
            "valid",
            REWARD[slot],
        )
        assert [
            (s["name"], s["under_test"], s["disposition"], s["harness"])
            + (f"{s['reported_provider']}/{s['reported_model']}",)
            for s in row["seats"]
        ] == [
            (seat, seat == "booker", "returned", "terminus-2", model)
            for seat, model in MODELS.items()
        ]
    assert witnesses[str(TRIALS[LEFT])].binding != witnesses[str(TRIALS[RIGHT])].binding
    for receipt in built.archive.iterdir():
        kept = receipt.read_text()
        for leaked in (SECRET, str(built.root), "terminus-2", *SLOTS, *MODELS.values()):
            assert leaked not in kept


@linkat
@pytest.mark.asyncio
async def test_start_receipts_release_grades_for_an_indistinguishable_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built = await team(tmp_path)
    code, plain = cohort(capsys, built)
    assert (code, plain["status"], plain["start_archive"]) == (3, "incomplete", None)
    assert plain["pair"]["status"] == "indistinguishable"
    for row in plain["groups"]:
        assert row["shared_result_view"] and row["starts"] is None
        assert (row["grades"], row["unattributed"]) == (None, 1)
    code, bound = cohort(capsys, built, starts=built.archive)
    assert (code, bound["status"], bound["job_closed"]) == (0, "covered", True)
    assert bound["pair"] == {
        "left": built.keys[0],
        "right": built.keys[1],
        "status": "exact_with_start_receipts",
    }
    assert bound["start_archive"] == {
        "rows": 2,
        **QUIET,
        "matched": 2,
        "without_final": 0,
    }
    for key, grades in zip(built.keys, (tally(zero=1), tally(one=1)), strict=True):
        row = group(bound, key)
        assert (row["grades"], row["unattributed"]) == (grades, 0)
        assert row["starts"] == {**QUIET, "matched": 1}


@linkat
@pytest.mark.asyncio
async def test_deleted_receipt_withholds_only_its_own_grade(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built = await team(tmp_path)
    starts = clone(built.archive, tmp_path / "scratch" / "starts")
    [gone] = [p for p in starts.iterdir() if str(TRIALS[LEFT]) in p.name]
    gone.unlink()
    code, report = cohort(capsys, built, starts=starts)
    assert (code, report["status"]) == (3, "incomplete")
    assert report["pair"]["status"] == "indistinguishable"
    assert report["start_archive"] == {
        "rows": 1,
        **QUIET,
        "matched": 1,
        "missing": 1,
        "without_final": 0,
    }
    left, right = group(report, built.keys[0]), group(report, built.keys[1])
    assert (left["grades"], left["unattributed"]) == (None, 1)
    assert left["starts"] == {**QUIET, "missing": 1}
    assert (right["grades"], right["unattributed"]) == (tally(one=1), 0)
    assert len(os.listdir(built.archive)) == 2


def scatter(document: Json, root: Path) -> None:
    document["config"]["trials_dir"] = str(root / "elsewhere")


def borrow(document: Json, root: Path) -> None:
    document["config"]["agent_timeout_multiplier"] = AGENT_TIMEOUT_MULTIPLIER[RIGHT]


@linkat
@pytest.mark.asyncio
@pytest.mark.parametrize("change", [scatter, borrow], ids=["trials_dir", "timeouts"])
async def test_config_change_outside_the_view_conflicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[Json, Path], None],
) -> None:
    built = await team(tmp_path)
    job = clone(built.job, tmp_path / "scratch" / "job")
    path = job / LEFT / "result.json"
    before = json.loads(path.read_bytes())
    rewrite(path, lambda document: change(document, tmp_path))
    after = json.loads(path.read_bytes())
    assert result_view(after) == result_view(before) is not None
    assert binding(after) != binding(before)
    assert project_result(after, job_id=str(JOB), slot_name=LEFT) == project_result(
        before, job_id=str(JOB), slot_name=LEFT
    )
    code, report = cohort(capsys, built, job=job, starts=built.archive)
    assert (code, report["status"]) == (1, "inconsistent")
    assert report["start_archive"] == {
        "rows": 2,
        **QUIET,
        "matched": 1,
        "conflicts": 1,
        "without_final": 0,
    }
    left = group(report, built.keys[0])
    assert left["grades"] is None and left["starts"] == {**QUIET, "conflicts": 1}
    assert group(report, built.keys[1])["grades"] == tally(one=1)
    assert report["pair"]["status"] == "indistinguishable"


def adopt(document: Json) -> None:
    document["config"]["job_id"] = str(OTHER_JOB)


@linkat
@pytest.mark.asyncio
async def test_receipts_from_another_job_never_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built = await team(tmp_path)
    with pytest.raises(ValueError, match="exactly one job"):
        await built.keeper.on_job_start(cast("HarborJob", Job(built.job, OTHER_JOB)))
    job = clone(built.job, tmp_path / "scratch" / "job")
    rewrite(job / "result.json", lambda root: root.update(id=str(OTHER_JOB)))
    for slot in SLOTS:
        rewrite(job / slot / "result.json", adopt)
    code, foreign = cohort(capsys, built, job=job, starts=built.archive)
    assert (code, foreign["job"]) == (3, str(OTHER_JOB))
    assert foreign["start_archive"] == {
        "rows": 0,
        **QUIET,
        "missing": 2,
        "without_final": 0,
    }
    assert foreign["pair"]["status"] == "indistinguishable"
    assert all(row["grades"] is None for row in foreign["groups"])
    relabeled = tmp_path / "relabeled"
    relabeled.mkdir()
    os.chmod(relabeled, 0o700)
    for witness in read_starts(built.archive, str(JOB)).values():
        put_once(str(relabeled), *encode(witness._replace(job=str(OTHER_JOB))))
    code, forged = cohort(capsys, built, job=job, starts=relabeled)
    assert (code, forged["status"]) == (1, "inconsistent")
    assert forged["start_archive"] == {
        "rows": 2,
        **QUIET,
        "conflicts": 2,
        "without_final": 0,
    }
    assert all(row["grades"] is None for row in forged["groups"])


@linkat
@pytest.mark.asyncio
async def test_extra_start_in_a_slot_is_without_final_not_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    built = await team(tmp_path, abandoned=True)
    witnesses = read_starts(built.archive, str(JOB))
    assert set(witnesses) == {str(trial) for trial in built.events}
    assert witnesses[str(ABANDONED)].slot == witnesses[str(TRIALS[LEFT])].slot
    assert saved(built.job, LEFT, "result.json")["id"] == str(TRIALS[LEFT])
    code, report = cohort(capsys, built, starts=built.archive)
    assert (code, report["status"], report["job_reported_retries"]) == (0, "covered", 1)
    assert report["pair"]["status"] == "exact_with_start_receipts"
    assert report["start_archive"] == {
        "rows": 3,
        **QUIET,
        "matched": 2,
        "without_final": 1,
    }
    assert group(report, built.keys[0])["grades"] == tally(zero=1)
    assert group(report, built.keys[1])["grades"] == tally(one=1)
