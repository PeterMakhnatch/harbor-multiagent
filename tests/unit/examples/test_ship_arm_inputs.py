import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from examples.native_study import record_bytes
from examples.ship_handover import arm_inputs
from harbor.models.job.lock import TrialLock, build_trial_lock
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.tasks.client import TaskDownloadResult

TASK_ROOT = "examples/tasks/ship-handover"
LOCKS = {
    "recovery-deliver.json": ("deliver", ("oracle", "oracle")),
    "sender-only-deliver.json": ("deliver", ("oracle", "nop")),
    "recovery-withhold.json": ("withhold", ("oracle", "oracle")),
}


@pytest.fixture(autouse=True)
def foreign_cwd(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))


def native_lock(lock: TrialLock, monkeypatch) -> TrialLock:
    monkeypatch.chdir(arm_inputs.REPO)
    config = TrialConfig(
        task=TaskConfig(path=Path(TASK_ROOT)),
        environment=lock.environment,
        participants=lock.participants,
    )
    local = TaskDownloadResult(path=Path(TASK_ROOT), download_time_sec=0.0, cached=True)
    return build_trial_lock(trial_config=config, task_download_result=local)


def test_writes_exactly_the_three_native_arm_locks(tmp_path: Path, monkeypatch):
    out = tmp_path / "arms"
    prepared = arm_inputs.prepare(out)
    assert sorted(path.name for path in out.iterdir()) == sorted(LOCKS)
    assert {name: entry["task_root"] for name, entry in prepared.items()} == {
        name: TASK_ROOT for name in LOCKS
    }
    for name, (channel, seats) in LOCKS.items():
        raw = (out / name).read_bytes()
        assert str(arm_inputs.REPO).encode() not in raw and b"${" not in raw
        lock = TrialLock.model_validate_json(raw)
        assert record_bytes(lock.model_dump(mode="json", exclude_none=True)) == raw
        assert lock.schema_version == 2
        assert lock.task.type == "local" and lock.task.name == "ship-handover"
        assert lock.task.path == Path(TASK_ROOT)
        assert lock.environment.env == {"SH_PRIOR": "committed", "SH_CHANNEL": channel}
        assert tuple(p.name for p in lock.participants) == seats
        assert all(p.model_name is None for p in lock.participants)
        assert native_lock(lock, monkeypatch) == lock
        assert prepared[name]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_cli_prints_only_the_task_root_map(tmp_path: Path, capsys):
    assert arm_inputs.main([str(tmp_path / "arms")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {name: {"task_root": TASK_ROOT} for name in LOCKS}


def test_existing_output_is_refused_untouched(tmp_path: Path, capsys):
    out = tmp_path / "arms"
    out.mkdir()
    (out / "recovery-deliver.json").write_text("mine")
    assert arm_inputs.main([str(out)]) == 2
    error = capsys.readouterr().err
    assert error == f"error: {arm_inputs.OUT_EXISTS}\n"
    assert [path.name for path in out.iterdir()] == ["recovery-deliver.json"]
    assert (out / "recovery-deliver.json").read_text() == "mine"


def test_unpinned_registration_is_refused(tmp_path: Path, monkeypatch):
    copied = tmp_path / "registrations"
    shutil.copytree(arm_inputs.REGISTRATIONS, copied)
    path = copied / "stock-sender-only.json"
    record = json.loads(path.read_text())
    record["teams"]["sender-only"][1]["name"] = "oracle"
    path.write_text(json.dumps(record, indent=2) + "\n")
    monkeypatch.setattr(arm_inputs, "REGISTRATIONS", copied)
    out = tmp_path / "arms"
    with pytest.raises(ValueError, match=arm_inputs.UNPINNED):
        arm_inputs.prepare(out)
    assert not out.exists()


@pytest.mark.parametrize("change", ["edit", "extra"])
def test_stock_package_drift_is_refused(tmp_path: Path, monkeypatch, change: str):
    task = tmp_path / "ship-handover"
    shutil.copytree(arm_inputs.TASK_DIR, task)
    if change == "edit":
        with (task / "tests" / "test.sh").open("a") as handle:
            handle.write("\n")
    else:
        (task / "tests" / "extra.txt").write_text("unpinned\n")
    monkeypatch.setattr(arm_inputs, "TASK_DIR", task)
    out = tmp_path / "arms"
    with pytest.raises(ValueError, match=arm_inputs.UNPINNED):
        arm_inputs.prepare(out)
    assert not out.exists()


def test_failed_write_removes_only_its_own_stage(tmp_path: Path, monkeypatch):
    original = arm_inputs.write_exclusive
    calls: list[str] = []

    def flaky(path: Path, data: dict[str, Any], mode: int) -> None:
        calls.append(path.name)
        if len(calls) == 3:
            raise OSError("disk full")
        original(path, data, mode)

    monkeypatch.setattr(arm_inputs, "write_exclusive", flaky)
    out = tmp_path / "arms"
    with pytest.raises(ValueError, match=arm_inputs.WRITE_FAILED):
        arm_inputs.prepare(out)
    assert len(calls) == 3
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
