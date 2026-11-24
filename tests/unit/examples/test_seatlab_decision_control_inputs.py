import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examples.native_study import native, record_bytes, task_identity
from examples.seatlab.decision_receipt import control_inputs as inputs
from examples.seatlab.decision_receipt import materialize as decision
from harbor.models.job.lock import build_trial_lock
from harbor.models.task.config import TaskConfig as TaskDefinitionConfig
from harbor.models.task.verifier_mode import resolve_task_verifier_mode
from harbor.publisher.packager import Packager

CONTROL = "examples.seatlab.decision_receipt.control_seat:DecisionReceiptControl"
TICKETS = ("dr-settles-plain", "dr-settles-cited", "dr-fails-plain", "dr-fails-cited")


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(lock: dict[str, Any]) -> str:
    text = json.dumps(lock, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return digest(text.encode("ascii"))


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def private(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    folder = private(tmp_path, "harness")
    files = {name: folder / name for name, _ in inputs.HARNESS}
    for name, path in files.items():
        path.write_bytes(f"CONTROL_FILE = {name!r}\n".encode())
    monkeypatch.setattr(inputs, "HARNESS", tuple(files.items()))
    return files


@pytest.fixture
def task(tmp_path: Path) -> Path:
    out = private(tmp_path, "tasks") / "ship-decision-receipt"
    decision.materialize(decision.target(str(out)))
    return out


def emitted(tmp_path: Path, task: Path) -> tuple[Path, dict[str, Any]]:
    out = private(tmp_path, "runs") / "controls"
    return out, inputs.emit(task, inputs.workspace_path(str(out), task))


def config(out: Path, ticket: str) -> dict[str, Any]:
    return json.loads((out / "configs" / f"{ticket}.json").read_bytes())


def rewrite(out: Path, enrollment: dict[str, Any]) -> str:
    data = record_bytes(enrollment)
    (out / "enrollment.json").write_bytes(data)
    return digest(data)


def trial_lock(task: Path, request: dict[str, Any]) -> dict[str, Any]:
    trial = native().TrialConfig.model_validate(request)
    local = SimpleNamespace(path=task, content_hash=None, resolved_git_commit_id=None)
    lock = build_trial_lock(trial_config=trial, task_download_result=local)
    return json.loads(lock.model_dump_json(indent=4, exclude_none=True))


@pytest.mark.unit
def test_cases_are_four_public_rows() -> None:
    rows = inputs.cases()
    assert [row["ticket"] for row in rows] == list(TICKETS)
    assert [(row["fate"], row["cite"]) for row in rows] == [
        ("settles", False),
        ("settles", True),
        ("fails", False),
        ("fails", True),
    ]
    assert all(set(row) == {"ticket", "fate", "cite"} for row in rows)
    assert all(type(row["cite"]) is bool for row in rows)
    rows[0]["cite"] = True
    assert inputs.cases()[0]["cite"] is False


@pytest.mark.unit
def test_emit_publishes_private_native_configs(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    out, record = emitted(tmp_path, task)
    assert mode(out) == 0o700 and mode(out / "configs") == 0o700
    assert sorted(os.listdir(out)) == ["configs", "enrollment.json"]
    assert sorted(os.listdir(out / "configs")) == sorted(f"{t}.json" for t in TICKETS)
    assert not os.path.lexists(out / "trials")
    assert os.listdir(out.parent) == [out.name]
    raw = (out / "enrollment.json").read_bytes()
    assert mode(out / "enrollment.json") == 0o600
    assert record["enrollment"] == digest(raw) and record["kind"] == inputs.KIND
    enrollment = json.loads(raw)
    harbor = native()
    assert enrollment["task"] == task_identity(harbor, task, decision.TASK_NAME)
    assert enrollment["harness"] == {
        name: digest(path.read_bytes()) for name, path in harness.items()
    }
    assert enrollment["native"]["caveat"] == inputs.NATIVE_CAVEAT
    assert enrollment["trials"] == str(out / "trials")
    for case in inputs.cases():
        path = out / "configs" / f"{case['ticket']}.json"
        assert mode(path) == 0o600
        assert enrollment["configs"][case["ticket"]] == digest(path.read_bytes())
        request = config(out, case["ticket"])
        assert request["trial_name"] == case["ticket"]
        assert request["task"] == {"path": str(task)}
        assert request["trials_dir"] == str(out / "trials")
        assert request["environment"]["env"] == {
            "SH_FATE": case["fate"],
            "SH_CHANNEL": "deliver",
        }
        outgoing, relief = request["participants"]
        assert outgoing["seat"] == "outgoing" and outgoing["name"] == "oracle"
        assert outgoing["env"] == {"SH_POLICY": "original"}
        assert "import_path" not in outgoing and "model_name" not in outgoing
        assert relief["seat"] == "relief" and relief["import_path"] == CONTROL
        assert relief["kwargs"] == {"cite": case["cite"]}
        assert "name" not in relief and "env" not in relief
        harbor.TrialConfig.model_validate(request)


@pytest.mark.unit
def test_pairs_differ_only_in_relief_cite(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    out, _ = emitted(tmp_path, task)
    for fate in inputs.FATES:
        plain = config(out, f"dr-{fate}-plain")
        cited = config(out, f"dr-{fate}-cited")
        assert plain["participants"][1]["kwargs"].pop("cite") is False
        assert cited["participants"][1]["kwargs"].pop("cite") is True
        plain.pop("trial_name")
        cited.pop("trial_name")
        assert plain == cited


@pytest.mark.unit
def test_load_reopens_with_held_digest(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    out, record = emitted(tmp_path, task)
    enrollment = inputs.load(task, out, record["enrollment"])
    assert enrollment.pin == record["enrollment"]
    assert enrollment.task == record["task"]
    files = {
        relative: digest((task / relative).read_bytes())
        for relative in decision.FINAL_MODES
    }
    assert enrollment.tree == record["tree"]
    assert enrollment.tree == decision.tree_digest(decision.FINAL_MODES, files)
    assert enrollment.trials == out / "trials"
    assert enrollment.requests == {ticket: config(out, ticket) for ticket in TICKETS}
    (out / "trials").mkdir(mode=0o000)
    try:
        assert inputs.load(task, out, record["enrollment"]).pin == record["enrollment"]
    finally:
        (out / "trials").chmod(0o700)


@pytest.mark.unit
def test_held_digest_is_checked_before_configs(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    out, record = emitted(tmp_path, task)
    for bad in ("", "sha256:abc", record["enrollment"].upper(), None):
        with pytest.raises(ValueError, match="held enrollment digest"):
            inputs.load(task, out, bad)
    for path in (out / "configs").iterdir():
        path.unlink()
    (out / "configs").rmdir()
    with pytest.raises(ValueError, match="held digest"):
        inputs.load(task, out, "sha256:" + "0" * 64)


@pytest.mark.unit
@pytest.mark.parametrize("field", ["config", "trials", "harness", "cases"])
def test_self_described_alternatives_are_refused(
    tmp_path: Path, task: Path, harness: dict[str, Path], field: str
) -> None:
    out, _ = emitted(tmp_path, task)
    enrollment = json.loads((out / "enrollment.json").read_bytes())
    if field == "config":
        request = config(out, "dr-fails-plain")
        request["participants"][1]["kwargs"]["cite"] = True
        data = record_bytes(request)
        (out / "configs" / "dr-fails-plain.json").write_bytes(data)
        enrollment["configs"]["dr-fails-plain"] = digest(data)
        message = "differs from the plan"
    elif field == "trials":
        enrollment["trials"] = str(tmp_path / "elsewhere")
        message = "another trials path"
    elif field == "harness":
        enrollment["harness"]["control_seat.py"] = "sha256:" + "1" * 64
        message = "control harness differs"
    else:
        enrollment["cases"][0]["cite"] = True
        message = "cases differ"
    pin = rewrite(out, enrollment)
    with pytest.raises(ValueError, match=message):
        inputs.load(task, out, pin)


@pytest.mark.unit
@pytest.mark.parametrize("change", ["harness", "mode", "content", "extra", "dir"])
def test_load_rechecks_current_task_and_harness(
    tmp_path: Path, task: Path, harness: dict[str, Path], change: str
) -> None:
    out, record = emitted(tmp_path, task)
    if change == "harness":
        harness["control_client.py"].write_bytes(b"CONTROL_FILE = 'other'\n")
    elif change == "mode":
        (task / "tests" / "test.sh").chmod(0o644)
    elif change == "content":
        with (task / "README.md").open("ab") as handle:
            handle.write(b"\n")
    elif change == "extra":
        (task / "extra.txt").write_bytes(b"extra\n")
    else:
        (task / "tests").chmod(0o700)
    with pytest.raises(ValueError):
        inputs.load(task, out, record["enrollment"])


@pytest.mark.unit
@pytest.mark.parametrize("change", ["mode", "content", "extra", "symlink"])
def test_emit_refuses_out_of_stock_task(
    tmp_path: Path, task: Path, harness: dict[str, Path], change: str
) -> None:
    if change == "mode":
        (task / "solution" / "solve.sh").chmod(0o644)
    elif change == "content":
        (task / "instructions" / "relief.md").write_bytes(b"other\n")
    elif change == "extra":
        (task / "tests" / "extra.py").write_bytes(b"extra = 1\n")
    else:
        target = task / "tests" / "verify.py"
        moved = tmp_path / "verify.py"
        target.rename(moved)
        target.symlink_to(moved)
    parent = private(tmp_path, "runs")
    with pytest.raises(ValueError):
        inputs.emit(task, inputs.workspace_path(str(parent / "controls"), task))
    assert os.listdir(parent) == []


@pytest.mark.unit
def test_paths_and_cli_are_strict(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    parent = private(tmp_path, "runs")
    (parent / "taken").mkdir(mode=0o700)
    link = tmp_path / "task-link"
    link.symlink_to(task)
    parent_link = tmp_path / "runs-link"
    parent_link.symlink_to(parent)
    for text in (str(link), str(task) + "/", "tasks/ship-decision-receipt"):
        with pytest.raises(ValueError):
            inputs.task_path(text)
    refused = (
        (str(parent / "taken"), "already exists"),
        (str(parent_link / "controls"), "without symlinks"),
        (str(task / "controls"), "outside TASK"),
        (str(decision.CHECKOUT / "controls-refused"), "outside the checkout"),
        ("runs/controls", "normalized absolute"),
        (str(parent / ".." / "runs" / "controls"), "normalized absolute"),
    )
    for text, message in refused:
        with pytest.raises(ValueError, match=message):
            inputs.workspace_path(text, task)
    assert inputs.main([]) == 2
    assert inputs.main([str(task)]) == 2
    assert inputs.main([str(task), str(parent / "a"), str(parent / "b")]) == 2
    assert inputs.main([str(link), str(parent / "controls")]) == 1
    assert inputs.main([str(task), str(parent / "taken")]) == 1
    assert sorted(os.listdir(parent)) == ["taken"]
    assert not os.path.lexists(decision.CHECKOUT / "controls-refused")


@pytest.mark.unit
def test_cli_emits_reopenable_workspace(
    tmp_path: Path,
    task: Path,
    harness: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = private(tmp_path, "runs") / "controls"
    assert inputs.main([str(task), str(out)]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["schema"] == inputs.EMIT_SCHEMA and record["kind"] == inputs.KIND
    assert record["configs"] == {
        ticket: str(out / "configs" / f"{ticket}.json") for ticket in TICKETS
    }
    assert inputs.load(task, out, record["enrollment"]).trials == out / "trials"


@pytest.mark.unit
def test_harness_must_be_present_and_plain(
    tmp_path: Path,
    task: Path,
    harness: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = private(tmp_path, "runs")
    link = tmp_path / "client-link.py"
    link.symlink_to(harness["control_client.py"])
    for client in (tmp_path / "absent.py", link):
        pairs = (
            ("control_seat.py", harness["control_seat.py"]),
            ("control_client.py", client),
        )
        monkeypatch.setattr(inputs, "HARNESS", pairs)
        assert inputs.main([str(task), str(parent / "controls")]) == 1
        assert os.listdir(parent) == []


@pytest.mark.unit
def test_failed_publication_leaves_nothing(
    tmp_path: Path,
    task: Path,
    harness: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = private(tmp_path, "runs")
    original = inputs._write

    def failing(path: Path, data: bytes, written: list[Path]) -> None:
        if path.name == inputs.ENROLLMENT:
            raise OSError("storage failed")
        original(path, data, written)

    monkeypatch.setattr(inputs, "_write", failing)
    with pytest.raises(OSError, match="storage failed"):
        inputs.emit(task, inputs.workspace_path(str(parent / "controls"), task))
    assert os.listdir(parent) == []


@pytest.mark.unit
def test_output_claimed_during_staging_is_kept(
    tmp_path: Path,
    task: Path,
    harness: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = private(tmp_path, "runs")
    out = parent / "controls"
    original = inputs.harness

    def racing() -> dict[str, str]:
        out.mkdir(mode=0o700)
        (out / "theirs.txt").write_bytes(b"theirs\n")
        return original()

    monkeypatch.setattr(inputs, "harness", racing)
    with pytest.raises(FileExistsError):
        inputs.emit(task, inputs.workspace_path(str(out), task))
    assert os.listdir(parent) == [out.name]
    assert os.listdir(out) == ["theirs.txt"]


@pytest.mark.unit
def test_locks_pin_the_native_trial_lock_of_each_config(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    out, record = emitted(tmp_path, task)
    enrollment = json.loads((out / "enrollment.json").read_bytes())
    loaded = inputs.load(task, out, record["enrollment"])
    assert list(enrollment["locks"]) == list(TICKETS)
    assert list(loaded.locks) == list(TICKETS)
    toml = TaskDefinitionConfig.model_validate_toml((task / "task.toml").read_text())
    mode = resolve_task_verifier_mode(toml).value
    for ticket in TICKETS:
        request = config(out, ticket)
        lock = trial_lock(task, request)
        pinned = enrollment["locks"][ticket]
        assert record_bytes(pinned) == record_bytes(lock)
        assert loaded.locks[ticket] == canonical(lock)
        assert loaded.locks[ticket] == inputs.lock_digest(pinned)
        assert pinned["schema_version"] == 2
        assert pinned["task"]["type"] == "local"
        assert pinned["task"]["path"] == str(task)
        assert pinned["task"]["name"] == task.name
        assert pinned["task"]["digest"] == record["task"]["digest"]
        assert type(pinned["timeout_multiplier"]) is float
        assert pinned["timeout_multiplier"] == 1.0
        assert pinned["install_only"] is False
        default = native().TrialConfig.model_validate(request).agent
        assert pinned["agent"] == json.loads(default.model_dump_json(exclude_none=True))
        assert pinned["verifier"]["environment_mode"] == mode
        assert pinned["verifier"]["disable"] is False
        assert pinned["participants"] == request["participants"]
        assert pinned["environment"]["env"] == request["environment"]["env"]
    assert len(set(loaded.locks.values())) == len(TICKETS)


@pytest.mark.unit
def test_native_package_hash_runs_five_times_at_emit_and_never_at_load(
    tmp_path: Path,
    task: Path,
    harness: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    original = Packager.compute_content_hash

    def counted(task_dir: Path) -> tuple[str, list[Path]]:
        calls.append(task_dir)
        return original(task_dir)

    monkeypatch.setattr(Packager, "compute_content_hash", staticmethod(counted))
    out, record = emitted(tmp_path, task)
    assert calls == [task] * 5
    inputs.load(task, out, record["enrollment"])
    assert calls == [task] * 5


@pytest.mark.unit
def test_load_keeps_pinned_lock_bytes_without_rebuilding(
    tmp_path: Path, task: Path, harness: dict[str, Path]
) -> None:
    out, _ = emitted(tmp_path, task)
    enrollment = json.loads((out / "enrollment.json").read_bytes())
    enrollment["locks"]["dr-settles-cited"]["timeout_multiplier"] = 2.0
    pin = rewrite(out, enrollment)
    loaded = inputs.load(task, out, pin)
    changed = enrollment["locks"]["dr-settles-cited"]
    assert loaded.locks["dr-settles-cited"] == canonical(changed)
    rebuilt = trial_lock(task, config(out, "dr-settles-cited"))
    assert loaded.locks["dr-settles-cited"] != canonical(rebuilt)


@pytest.mark.unit
def test_lock_digest_ignores_key_order_and_keeps_list_order_and_types() -> None:
    lock = {"b": {"y": 1.0, "x": [1, 2]}, "a": True}
    reordered = {"a": True, "b": {"x": [1, 2], "y": 1.0}}
    assert inputs.lock_digest(lock) == inputs.lock_digest(reordered)
    assert inputs.lock_digest(lock) == digest(b'{"a":true,"b":{"x":[1,2],"y":1.0}}')
    assert inputs.lock_digest(lock) == canonical(lock)
    others = (
        {"a": True, "b": {"x": [2, 1], "y": 1.0}},
        {"a": True, "b": {"x": [1, 2], "y": 1}},
        {"a": 1, "b": {"x": [1, 2], "y": 1.0}},
        {"a": True, "b": {"x": [1, 2], "y": 1.0}, "c": None},
    )
    for other in others:
        assert inputs.lock_digest(other) != inputs.lock_digest(lock)
    for bad in ([], "lock", {"a": float("nan")}, {"a": object()}):
        with pytest.raises(ValueError):
            inputs.lock_digest(bad)


@pytest.mark.unit
@pytest.mark.parametrize("field", sorted(inputs.NATIVE_FIELDS - {"caveat"}))
def test_enrolled_native_drift_is_reported_apart(
    tmp_path: Path, task: Path, harness: dict[str, Path], field: str
) -> None:
    out, _ = emitted(tmp_path, task)
    enrollment = json.loads((out / "enrollment.json").read_bytes())
    assert set(enrollment["native"]) == inputs.NATIVE_FIELDS
    stale = "0.0.0+stale" if field == "harbor" else "sha256:" + "3" * 64
    enrollment["native"][field] = stale
    pin = rewrite(out, enrollment)
    with pytest.raises(inputs.NativeDrift, match=field):
        inputs.load(task, out, pin)


@pytest.mark.unit
def test_installed_native_drift_is_not_a_control_mismatch(
    tmp_path: Path,
    task: Path,
    harness: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, record = emitted(tmp_path, task)
    with monkeypatch.context() as patch:
        patch.setattr(inputs.metadata, "version", lambda name: "0.0.0+drift")
        with pytest.raises(inputs.NativeDrift, match="harbor"):
            inputs.load(task, out, record["enrollment"])
    harness["control_seat.py"].write_bytes(b"CONTROL_FILE = 'other'\n")
    with pytest.raises(ValueError, match="control harness differs") as caught:
        inputs.load(task, out, record["enrollment"])
    assert not isinstance(caught.value, inputs.NativeDrift)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("missing", "enrollment locks fields differ"),
        ("extra", "enrollment locks fields differ"),
        ("shape", "is not a native TrialLock"),
        ("scalar", "native TrialLock dump"),
        ("unknown", "native TrialLock"),
        ("version", "is not version 2"),
        ("digest", "not the enrolled local task"),
        ("path", "not the enrolled local task"),
        ("world", "world is not the config world"),
        ("team", "participants are not the config team"),
        ("verifier", "lacks the verifier mode"),
    ],
)
def test_load_refuses_malformed_or_unbound_locks(
    tmp_path: Path, task: Path, harness: dict[str, Path], change: str, message: str
) -> None:
    out, _ = emitted(tmp_path, task)
    enrollment = json.loads((out / "enrollment.json").read_bytes())
    locks = enrollment["locks"]
    lock = locks["dr-fails-plain"]
    if change == "missing":
        del locks["dr-fails-cited"]
    elif change == "extra":
        locks["dr-other"] = lock
    elif change == "shape":
        lock["agent"] = "oracle"
    elif change == "scalar":
        lock["timeout_multiplier"] = 1
    elif change == "unknown":
        lock["surprise"] = 1
    elif change == "version":
        lock["schema_version"] = 3
    elif change == "digest":
        lock["task"]["digest"] = "sha256:" + "2" * 64
    elif change == "path":
        lock["task"]["path"] = str(tmp_path / "ship-decision-receipt")
    elif change == "world":
        lock["environment"]["env"]["SH_FATE"] = "settles"
    elif change == "team":
        locks["dr-fails-plain"] = locks["dr-fails-cited"]
    else:
        del lock["verifier"]["environment_mode"]
    pin = rewrite(out, enrollment)
    with pytest.raises(ValueError, match=message):
        inputs.load(task, out, pin)
