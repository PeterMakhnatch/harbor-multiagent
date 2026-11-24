import hashlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from examples.seatlab.workbench.__main__ import main as workbench
from examples.seatlab.workbench.cohort import main as cohort
from examples.seatlab.workbench.input_evidence import MAX_TRIALS
from examples.seatlab.workbench.input_keeper import InputStartKeeper
from examples.seatlab.workbench.input_keys import cell
from examples.seatlab.workbench.paired_jobs import main
from harbor.models.job.result import JobResult, JobStats
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, ModelInfo, SeatResult, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.trial.hooks import TrialHookEvent
from tests.unit.examples.test_seatlab_cohort_start_contract import (
    FINISHED,
    MODELS,
    SECRET,
    STARTED,
    clone,
    enroll,
    rewrite,
    saved,
    start,
    tally,
    trial_lock,
)
from tests.unit.examples.test_seatlab_input_keeper import Job, linkat

if TYPE_CHECKING:
    from harbor.job import Job as HarborJob

SCHEMA = "seatlab.workbench.paired-cohort/v1"
LEFT_JOB = UUID("2b6d8f1a-4c3e-4a5b-9d7f-8e1c3a5b7d9f")
RIGHT_JOB = UUID("e7c1a3f5-9b2d-4e6f-8a0c-5d3f1b9e7a2c")
RIVAL = "google/gemini-2.5-pro"
SIDES = ("left", "right")
EXITS = {"covered": 0, "inconsistent": 1, "incomplete": 3}
FIELDS = ("input", "job", "job_lock", "registered", "retained", "attributed", "grades")
Json = dict[str, Any]


class Plan(NamedTuple):
    reporter: str
    timeout: float


BASE = Plan(MODELS["reporter"], 1.0)
SWAP = Plan(RIVAL, 1.0)
SLOW = Plan(MODELS["reporter"], 1.5)
THIRD = Plan(RIVAL, 2.0)


class Arm(NamedTuple):
    root: Path
    job: Path
    starts: Path
    job_id: UUID
    pin: str
    key: str
    slots: tuple[str, ...]
    trials: tuple[str, ...]


def task_dir(root: Path) -> Path:
    task = root / "tasks" / "relay"
    if not task.exists():
        source = Path(__file__).resolve().parents[3] / "examples/tasks/revised-dispatch"
        shutil.copytree(source, task, ignore=shutil.ignore_patterns("__pycache__"))
    return task


def configure(
    task: Path, job: Path, job_id: UUID, slot: str, plan: Plan
) -> TrialConfig:
    models = {**MODELS, "reporter": plan.reporter}
    return TrialConfig(
        task=TaskConfig(path=task),
        trial_name=slot,
        trials_dir=job,
        job_id=job_id,
        agent_timeout_multiplier=plan.timeout,
        participants=[
            Participant(seat=seat, name="terminus-2", model_name=model)
            for seat, model in models.items()
        ],
        environment=EnvironmentConfig(env={"SEATLAB_WORLD": SECRET}),
    )


def finish(event: TrialHookEvent, reward: int) -> TrialResult:
    seats = []
    for member in event.config.participants:
        assert member.model_name is not None
        provider, name = member.model_name.split("/")
        info = ModelInfo(name=name, provider=provider)
        seats.append(
            SeatResult(
                seat=member.seat,
                under_test=member.seat == "booker",
                agent_info=AgentInfo(name="terminus-2", version="1", model_info=info),
                execution_disposition="returned",
            )
        )
    return event.result.model_copy(
        update={
            "finished_at": FINISHED,
            "verifier_result": VerifierResult(rewards={"reward": reward}),
            "seat_results": seats,
        }
    )


def close(job: Path, job_id: UUID, total: int) -> None:
    root = JobResult(
        id=job_id,
        started_at=STARTED,
        finished_at=FINISHED,
        n_total_trials=total,
        stats=JobStats(n_completed_trials=total, n_retries=0),
    )
    (job / "result.json").write_text(root.model_dump_json(indent=4))


def digest(job: Path) -> str:
    return "sha256:" + hashlib.sha256((job / "lock.json").read_bytes()).hexdigest()


async def arm(
    root: Path,
    side: str,
    job_id: UUID,
    plans: Sequence[Plan],
    rewards: Sequence[int],
    trials: Sequence[str] | None = None,
) -> Arm:
    assert 0 < len(plans) == len(rewards)
    task = task_dir(root)
    job = root / "jobs" / f"{side}-arm"
    job.mkdir(parents=True)
    starts = root / "starts" / side
    starts.mkdir(parents=True)
    os.chmod(starts, 0o700)
    slots = tuple(f"relay__{side}{index}c9e4" for index in range(len(plans)))
    executions = tuple(
        trials
        if trials is not None
        else (
            str(uuid5(NAMESPACE_URL, f"seatlab-paired/{side}/{index}"))
            for index in range(len(plans))
        )
    )
    configs = [
        configure(task, job, job_id, slot, plan)
        for slot, plan in zip(slots, plans, strict=True)
    ]
    locks = [trial_lock(task, config) for config in configs]
    enroll(job, locks)
    keeper = InputStartKeeper(str(starts))
    host = Job(job, job_id)
    await keeper.on_job_start(cast("HarborJob", host))
    for slot, config, lock, trial, reward in zip(
        slots, configs, locks, executions, rewards, strict=True
    ):
        (job / slot).mkdir()
        (job / slot / "lock.json").write_text(
            lock.model_dump_json(indent=4, exclude_none=True)
        )
        event = start(config, lock, UUID(trial))
        for callback in host.started:
            await callback(event)
        (job / slot / "result.json").write_text(
            finish(event, reward).model_dump_json(indent=4)
        )
    close(job, job_id, len(plans))
    assert len(os.listdir(starts)) == len(plans)
    key = cell(saved(job, slots[0])).key
    return Arm(root, job, starts, job_id, digest(job), key, slots, executions)


async def pair(
    root: Path,
    *,
    right: Plan = SWAP,
    left_rewards: Sequence[int] = (0, 0),
    right_rewards: Sequence[int] = (1, 0),
) -> tuple[Arm, Arm]:
    built = (
        await arm(root, "left", LEFT_JOB, [BASE] * len(left_rewards), left_rewards),
        await arm(
            root, "right", RIGHT_JOB, [right] * len(right_rewards), right_rewards
        ),
    )
    assert built[0].key != built[1].key and built[0].pin != built[1].pin
    return built


def command(
    jobs: tuple[Path, Path],
    keys: tuple[str, str],
    pins: tuple[str, str],
    copies: int | str = 2,
    starts: tuple[Path, Path] | None = None,
) -> list[str]:
    argv = [str(jobs[0]), str(jobs[1]), "--pair", *keys, "--copies", str(copies)]
    argv += ["--job-locks", *pins]
    if starts is not None:
        argv += ["--starts", str(starts[0]), str(starts[1])]
    return argv


def standard(
    left: Arm, right: Arm, *, copies: int = 2, starts: bool = False
) -> list[str]:
    return command(
        (left.job, right.job),
        (left.key, right.key),
        (left.pin, right.pin),
        copies,
        (left.starts, right.starts) if starts else None,
    )


def invoke(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as stop:
        assert type(stop.code) is int
        return stop.code


def private(*arms: Arm) -> tuple[str, ...]:
    values = [SECRET, str(arms[0].root), "terminus-2", "relay__", RIVAL]
    values += MODELS.values()
    for built in arms:
        values += [*built.slots, *built.trials, str(built.job), str(built.starts)]
    return tuple(values)


def clean(text: str, *arms: Arm) -> None:
    for leaked in private(*arms):
        assert leaked not in text


def report(
    capsys: pytest.CaptureFixture[str], argv: list[str], *arms: Arm
) -> tuple[int, Json]:
    code = invoke([*argv, "--json"])
    out, err = capsys.readouterr()
    assert err == ""
    clean(out, *arms)
    found = json.loads(out)
    assert found["schema"] == SCHEMA
    assert code == EXITS[found["status"]]
    assert set(found["arms"]) == set(SIDES)
    for side in SIDES:
        assert set(FIELDS) <= found["arms"][side].keys()
    return code, found


def view(found: Json, side: str) -> Json:
    return {field: found["arms"][side][field] for field in FIELDS}


def withheld(found: Json) -> None:
    assert found["status"] != "covered"
    for side in SIDES:
        assert found["arms"][side]["grades"] is None


def refused(capsys: pytest.CaptureFixture[str], argv: list[str], *arms: Arm) -> None:
    code = invoke(argv)
    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err.strip()
    clean(err, *arms)


@linkat
@pytest.mark.asyncio
async def test_two_jobs_with_one_changed_input_grade_each_arm_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = await pair(tmp_path)
    assert (
        cell(saved(left.job, left.slots[0])).view
        != cell(saved(right.job, right.slots[0])).view
    )
    code, found = report(capsys, standard(left, right), left, right)
    assert (code, found["status"], found["copies_per_arm"]) == (0, "covered", 2)
    expected = {"left": (left, tally(zero=2)), "right": (right, tally(zero=1, one=1))}
    for side, (built, grades) in expected.items():
        assert view(found, side) == {
            "input": built.key,
            "job": str(built.job_id),
            "job_lock": built.pin,
            "registered": 2,
            "retained": 2,
            "attributed": 2,
            "grades": grades,
        }
    code, bound = report(capsys, standard(left, right, starts=True), left, right)
    assert (code, bound["status"]) == (0, "covered")
    assert [view(bound, side) for side in SIDES] == [view(found, s) for s in SIDES]
    backward = command(
        (right.job, left.job), (right.key, left.key), (right.pin, left.pin)
    )
    code, swapped = report(capsys, backward, left, right)
    assert (code, swapped["status"]) == (0, "covered")
    assert view(swapped, "left") == view(found, "right")
    assert view(swapped, "right") == view(found, "left")
    assert workbench(["cohort-jobs", *standard(left, right), "--json"]) == 0
    out, err = capsys.readouterr()
    assert (err, json.loads(out)) == ("", found)
    assert invoke(standard(left, right)) == 0
    out, err = capsys.readouterr()
    assert err == ""
    clean(out, left, right)
    for public in (
        left.key,
        right.key,
        left.pin,
        right.pin,
        str(LEFT_JOB),
        str(RIGHT_JOB),
    ):
        assert public in out


def swapped_jobs(left: Arm, right: Arm) -> tuple[tuple[Path, Path], tuple[str, str]]:
    return (right.job, left.job), (right.pin, left.pin)


def same_job_twice(left: Arm, right: Arm) -> tuple[tuple[Path, Path], tuple[str, str]]:
    return (left.job, left.job), (left.pin, left.pin)


def worker_copy(left: Arm, right: Arm) -> tuple[tuple[Path, Path], tuple[str, str]]:
    copy = clone(left.job, left.root / "scratch" / "worker-copy")
    assert digest(copy) == left.pin
    return (left.job, copy), (left.pin, left.pin)


def relabeled_copy(left: Arm, right: Arm) -> tuple[tuple[Path, Path], tuple[str, str]]:
    copy = clone(left.job, left.root / "scratch" / "relabeled-copy")
    rewrite(copy / "result.json", lambda root: root.update(id=str(RIGHT_JOB)))
    for slot in left.slots:
        rewrite(
            copy / slot / "result.json",
            lambda document: document["config"].update(job_id=str(RIGHT_JOB)),
        )
    assert digest(copy) == left.pin
    return (left.job, copy), (left.pin, left.pin)


@linkat
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "place",
    [swapped_jobs, same_job_twice, worker_copy, relabeled_copy],
    ids=["swapped_jobs", "same_job_twice", "worker_copy", "relabeled_copy"],
)
async def test_wrong_job_on_a_side_withholds_both_arms(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    place: Callable[[Arm, Arm], tuple[tuple[Path, Path], tuple[str, str]]],
) -> None:
    left, right = await pair(tmp_path)
    jobs, pins = place(left, right)
    argv = command(jobs, (left.key, right.key), pins)
    code, found = report(capsys, argv, left, right)
    assert (code, found["status"]) == (1, "inconsistent")
    withheld(found)


async def extra_left(root: Path) -> tuple[Arm, Arm]:
    return (
        await arm(root, "left", LEFT_JOB, (BASE, BASE, SWAP), (0, 0, 1)),
        await arm(root, "right", RIGHT_JOB, (SWAP, SWAP), (1, 0)),
    )


async def extra_right(root: Path) -> tuple[Arm, Arm]:
    return (
        await arm(root, "left", LEFT_JOB, (BASE, BASE), (0, 0)),
        await arm(root, "right", RIGHT_JOB, (SWAP, SWAP, THIRD), (1, 0, 1)),
    )


async def shared_job_uuid(root: Path) -> tuple[Arm, Arm]:
    left = await arm(root, "left", LEFT_JOB, (BASE, BASE), (0, 0))
    right = await arm(root, "right", LEFT_JOB, (SWAP, SWAP), (1, 0))
    assert left.job_id == right.job_id
    return left, right


async def reused_executions(root: Path) -> tuple[Arm, Arm]:
    left = await arm(root, "left", LEFT_JOB, (BASE, BASE), (0, 0))
    right = await arm(root, "right", RIGHT_JOB, (SWAP, SWAP), (1, 0), left.trials)
    assert left.trials == right.trials
    return left, right


@linkat
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "build",
    [extra_left, extra_right, shared_job_uuid, reused_executions],
    ids=["extra_input_left", "extra_input_right", "job_uuid", "execution_uuid"],
)
async def test_extra_input_or_reused_identity_withholds_both_arms(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    build: Callable[[Path], Awaitable[tuple[Arm, Arm]]],
) -> None:
    left, right = await build(tmp_path)
    assert left.key != right.key and left.pin != right.pin
    for starts in (False, True):
        argv = standard(left, right, starts=starts)
        code, found = report(capsys, argv, left, right)
        assert (code, found["status"]) == (1, "inconsistent")
        withheld(found)


@linkat
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left_rewards", "right_rewards", "copies"),
    [((0, 0), (1, 0, 1), 2), ((0, 0), (1,), 2), ((0, 0), (1, 0), 3)],
    ids=["right_has_more", "right_is_short", "plan_is_longer"],
)
async def test_unequal_or_shortened_repeats_are_not_balanced(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    left_rewards: tuple[int, ...],
    right_rewards: tuple[int, ...],
    copies: int,
) -> None:
    left, right = await pair(
        tmp_path, left_rewards=left_rewards, right_rewards=right_rewards
    )
    code, found = report(capsys, standard(left, right, copies=copies), left, right)
    assert code != 0 and found["copies_per_arm"] == copies
    withheld(found)
    assert found["arms"]["left"]["registered"] == len(left_rewards)
    assert found["arms"]["right"]["registered"] == len(right_rewards)


@linkat
@pytest.mark.asyncio
async def test_missing_final_is_never_a_retained_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = await pair(tmp_path, right_rewards=(0, 1))
    code, whole = report(capsys, standard(left, right), left, right)
    assert code == 0
    assert view(whole, "right")["grades"] == tally(zero=1, one=1)
    partial = clone(right.job, tmp_path / "scratch" / "right-arm")
    (partial / right.slots[0] / "result.json").unlink()
    assert digest(partial) == right.pin
    argv = command((left.job, partial), (left.key, right.key), (left.pin, right.pin))
    code, found = report(capsys, argv, left, right)
    assert (code, found["status"]) == (3, "incomplete")
    lost = found["arms"]["right"]
    assert (lost["registered"], lost["retained"]) == (2, 1)
    assert (lost["missing_finals"], lost["finals_without_lock"]) == (1, 0)
    assert lost["grades"] in (None, tally(one=1))
    assert lost["attributed"] in (None, 0, 1)
    assert view(found, "left")["grades"] in (None, tally(zero=2))


@linkat
@pytest.mark.asyncio
async def test_final_without_its_trial_lock_withholds_only_that_arm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = await pair(tmp_path, right_rewards=(0, 1))
    code, whole = report(capsys, standard(left, right), left, right)
    assert code == 0
    unlocked = clone(right.job, tmp_path / "scratch" / "right-arm")
    (unlocked / right.slots[0] / "lock.json").unlink()
    assert (unlocked / right.slots[0] / "result.json").is_file()
    assert digest(unlocked) == right.pin
    argv = command((left.job, unlocked), (left.key, right.key), (left.pin, right.pin))
    code, found = report(capsys, argv, left, right)
    assert (code, found["status"]) == (3, "incomplete")
    loose = found["arms"]["right"]
    assert (loose["registered"], loose["retained"]) == (2, 2)
    assert (loose["missing_finals"], loose["finals_without_lock"]) == (0, 1)
    assert loose["grades"] in (None, tally(one=1))
    assert view(found, "left") == view(whole, "left")
    assert found["arms"]["left"]["finals_without_lock"] == 0


@linkat
@pytest.mark.asyncio
async def test_shared_result_view_needs_matching_starts_not_separate_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = await pair(tmp_path, right=SLOW, right_rewards=(1, 1))
    cells = (
        cell(saved(left.job, left.slots[0])),
        cell(saved(right.job, right.slots[0])),
    )
    assert cells[0].key != cells[1].key and cells[0].view == cells[1].view
    for built in (left, right):
        alone = cohort([str(built.job), "--expected-job-lock", built.pin, "--json"])
        out, err = capsys.readouterr()
        assert (alone, err, json.loads(out)["status"]) == (0, "", "covered")
    code, blind = report(capsys, standard(left, right), left, right)
    assert (code, blind["status"]) == (3, "incomplete")
    withheld(blind)
    assert invoke(standard(left, right)) == 3
    out, err = capsys.readouterr()
    assert err == "" and "withheld" in out
    clean(out, left, right)
    code, bound = report(capsys, standard(left, right, starts=True), left, right)
    assert (code, bound["status"]) == (0, "covered")
    for side, grades in zip(SIDES, (tally(zero=2), tally(one=2)), strict=True):
        assert (view(bound, side)["attributed"], view(bound, side)["grades"]) == (
            2,
            grades,
        )
    crossed = command(
        (left.job, right.job),
        (left.key, right.key),
        (left.pin, right.pin),
        starts=(right.starts, left.starts),
    )
    code, foreign = report(capsys, crossed, left, right)
    assert (code, foreign["status"]) == (3, "incomplete")
    withheld(foreign)


@linkat
@pytest.mark.asyncio
async def test_wrong_pin_or_malformed_option_exits_two_without_private_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = await pair(tmp_path)
    jobs, keys = (left.job, right.job), (left.key, right.key)
    pins = (left.pin, right.pin)
    edited = clone(right.job, tmp_path / "scratch" / "edited-right")
    rewrite(
        edited / "lock.json", lambda document: document.update(n_concurrent_trials=3)
    )
    assert digest(edited) != right.pin
    good = standard(left, right)
    cases = [
        command(jobs, keys, (right.pin, left.pin)),
        command(jobs, keys, (left.pin, left.pin)),
        command(jobs, keys, (left.pin, "sha256:" + "0" * 64)),
        command((left.job, edited), keys, pins),
        command((left.job, tmp_path / "jobs" / "absent"), keys, pins),
        command(jobs, keys, ("sha256:" + left.pin[7:].upper(), right.pin)),
        command(jobs, (left.key, left.key), pins),
        command(jobs, (left.key[:-1], right.key), pins),
        command(jobs, keys, pins, 0),
        command(jobs, keys, pins, MAX_TRIALS + 1),
        command(jobs, keys, pins, "two"),
        good[: good.index("--job-locks")],
        [*good, "--starts", str(left.starts)],
        [str(left.job), *good],
        [*good, "--copies", "2"],
        [*good, "--expected-job-lock", left.pin],
    ]
    for argv in cases:
        refused(capsys, [*argv, "--json"], left, right)
    code, found = report(capsys, good, left, right)
    assert (code, found["status"]) == (0, "covered")
