import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from examples.seatlab.workbench import input_keeper
from examples.seatlab.workbench.input_keeper import WARNING, InputStartKeeper
from examples.seatlab.workbench.input_witness import read_starts
from examples.seatlab.workbench.native_account import _slot
from examples.seatlab.workbench.private_receipt import _linkable
from harbor.models.job.lock import build_trial_lock
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, TrialResult
from harbor.trial.hooks import TrialEvent, TrialHookEvent

JOB = UUID("3f9a1c2e-6b4d-4e8f-9a0b-1c2d3e4f5a6b")
OTHER_JOB = UUID("7e2a4c90-3b1d-4f68-a5c2-d9e08f1b6a37")
SLOT = "relay__a1b2c3d"
SECRET = "PRIVATE-PROMPT-BODY"
linkat = pytest.mark.skipif(not _linkable(), reason="linkat unsupported")


class Job:
    def __init__(self, job_dir: Path, job_id: UUID = JOB) -> None:
        self.job_dir, self.id, self.started = job_dir, job_id, []

    def on_trial_started(self, callback):
        self.started.append(callback)
        return self


def layout(root: Path) -> tuple[Path, Path, Path]:
    task = root / "tasks" / "relay"
    task.mkdir(parents=True)
    (task / "task.toml").write_text('[task]\nname = "seatlab/relay"\n')
    (task / "instruction.md").write_text(f"{SECRET}\n")
    job_dir = root / "jobs" / "relay-job"
    job_dir.mkdir(parents=True)
    archive = root / "archive"
    archive.mkdir()
    os.chmod(archive, 0o700)
    return task, job_dir, archive


def start(
    task: Path,
    job_dir: Path,
    *,
    trial: UUID | None = None,
    event: TrialEvent = TrialEvent.START,
    finished: bool = False,
) -> TrialHookEvent:
    config = TrialConfig(
        task=TaskConfig(path=task),
        trial_name=SLOT,
        trials_dir=job_dir,
        job_id=JOB,
        participants=[
            Participant(seat="booker", name="oracle"),
            Participant(seat="reporter", name="nop"),
        ],
        environment=EnvironmentConfig(env={"SEATLAB_WORLD": SECRET}),
    )
    local = SimpleNamespace(path=task, content_hash=None, resolved_git_commit_id=None)
    lock = build_trial_lock(trial_config=config, task_download_result=local)
    (job_dir / SLOT).mkdir(exist_ok=True)
    (job_dir / SLOT / "lock.json").write_text(
        lock.model_dump_json(indent=4, exclude_none=True)
    )
    result = TrialResult(
        id=trial or uuid4(),
        task_name=config.task.get_task_id().get_name(),
        trial_name=SLOT,
        trial_uri=(job_dir / SLOT).as_uri(),
        task_id=config.task.get_task_id(),
        task_checksum="c" * 64,
        config=config,
        agent_info=AgentInfo(name="oracle", version="1"),
        finished_at=datetime.now(UTC) if finished else None,
    )
    return TrialHookEvent(
        event=event, task_name=result.task_name, config=config, result=result, lock=lock
    )


async def enrolled(tmp_path: Path) -> tuple[InputStartKeeper, Job, Path, Path, Path]:
    task, job_dir, archive = layout(tmp_path)
    keeper = InputStartKeeper(str(archive))
    job = Job(job_dir)
    await keeper.on_job_start(job)
    assert job.started == [keeper._capture]
    return keeper, job, task, job_dir, archive


@linkat
@pytest.mark.asyncio
async def test_start_keeps_one_encoded_witness_without_inputs(tmp_path, capsys):
    keeper, _, task, job_dir, archive = await enrolled(tmp_path)
    event = start(task, job_dir)
    await keeper._capture(event)
    await keeper._capture(event)
    witnesses = read_starts(archive, str(JOB))
    assert list(witnesses) == [str(event.result.id)]
    witness = witnesses[str(event.result.id)]
    assert (witness.job, witness.slot) == (str(JOB), _slot(SLOT))
    assert len(os.listdir(archive)) == 1
    kept = (archive / os.listdir(archive)[0]).read_text()
    for leaked in (SECRET, "oracle", "booker", str(task), str(job_dir), SLOT):
        assert leaked not in kept
    assert capsys.readouterr().err == ""


@linkat
@pytest.mark.asyncio
async def test_retries_keep_independent_witnesses_with_matching_input(tmp_path):
    keeper, _, task, job_dir, archive = await enrolled(tmp_path)
    first, second = start(task, job_dir), start(task, job_dir)
    await keeper._capture(first)
    await keeper._capture(second)
    witnesses = read_starts(archive, str(JOB))
    assert set(witnesses) == {str(first.result.id), str(second.result.id)}
    one, two = witnesses.values()
    assert (one.slot, one.input, one.binding) == (two.slot, two.input, two.binding)


@linkat
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late", [{"event": TrialEvent.END}, {"finished": True}], ids=["end", "finished"]
)
async def test_late_or_finished_events_are_refused_passively(tmp_path, capsys, late):
    keeper, _, task, job_dir, archive = await enrolled(tmp_path)
    await keeper._capture(start(task, job_dir, **late))
    assert os.listdir(archive) == []
    assert capsys.readouterr().err.strip() == WARNING


@linkat
@pytest.mark.asyncio
async def test_saved_lock_drift_is_refused(tmp_path, capsys):
    keeper, _, task, job_dir, archive = await enrolled(tmp_path)
    event = start(task, job_dir)
    (job_dir / SLOT / "lock.json").write_text("{}")
    await keeper._capture(event)
    assert os.listdir(archive) == []
    assert capsys.readouterr().err.strip() == WARNING


@linkat
@pytest.mark.asyncio
async def test_write_failure_warns_fixed_text_only(tmp_path, capsys, monkeypatch):
    keeper, _, task, job_dir, archive = await enrolled(tmp_path)

    def broken(*_args, **_kwargs):
        raise OSError(f"{archive} {SECRET}")

    monkeypatch.setattr(input_keeper, "put_once", broken)
    await keeper._capture(start(task, job_dir))
    os.chmod(archive, 0o755)
    monkeypatch.undo()
    await keeper._capture(start(task, job_dir))
    os.chmod(archive, 0o700)
    assert os.listdir(archive) == []
    assert capsys.readouterr().err.splitlines() == [WARNING, WARNING]


@linkat
@pytest.mark.asyncio
async def test_enrollment_refuses_reuse_nesting_and_foreign_files(tmp_path):
    keeper, job, _, job_dir, archive = await enrolled(tmp_path)
    with pytest.raises(ValueError, match="exactly one job"):
        await keeper.on_job_start(Job(job_dir, OTHER_JOB))
    assert len(job.started) == 1

    nested = job_dir / "archive"
    nested.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="must not nest"):
        await InputStartKeeper(str(nested)).on_job_start(Job(job_dir))

    (archive / "notes.txt").write_text(SECRET)
    os.chmod(archive / "notes.txt", 0o600)
    refused = Job(job_dir)
    with pytest.raises(ValueError, match="rejected") as caught:
        await InputStartKeeper(str(archive)).on_job_start(refused)
    assert refused.started == [] and SECRET not in str(caught.value)


def test_constructor_refuses_shared_or_indirect_archives(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "missing")
    for bad in (str(shared), str(alias), "relative", str(tmp_path / "absent")):
        with pytest.raises(ValueError, match="rejected"):
            InputStartKeeper(bad)
