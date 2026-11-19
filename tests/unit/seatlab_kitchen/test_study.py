import importlib.util
import json
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from harbor.models.trial.config import TrialConfig

TASK = Path(__file__).resolve().parents[3] / "examples/tasks/cramped-room-parity"
ORDER = ["A-TT", "A-WT", "A-TW", "B-TW", "B-WT", "B-TT"]
ROLES = ("chef0", "chef1")
CASE = "a" * 64


@pytest.fixture
def study():
    spec = importlib.util.spec_from_file_location(
        "seatlab_study", TASK / "seatlab_study.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(path):
    return json.loads(path.read_bytes())


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


@pytest.fixture
def paths(tmp_path):
    root = tmp_path.resolve()
    harbor = root / "harbor"
    harbor.touch(mode=0o700)
    return harbor, root / "private", root / "portable"


def _fake_process(study, monkeypatch, harbor, out, *, behaviors=None, ids=None):
    """Synthetic subprocess boundary; never starts Harbor or Docker."""
    calls = []
    behaviors = behaviors or {}
    ids = ids or {}
    docker = {"DOCKER_HOST": "unix:///test-not-a-real-socket"}
    monkeypatch.setattr(study, "_local_docker_env", lambda: docker)

    def launch(argv, **kwargs):
        assert kwargs["cwd"] == out and kwargs["env"] == docker
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["check"] is False
        cell = ORDER[len(calls)]
        input_path = out / "configs" / f"{cell}.json"
        assert argv == [str(harbor), "trial", "start", "-c", str(input_path)]
        assert stat.S_IMODE(out.stat().st_mode) == 0o700
        plan = _json(out / "plan.json")
        assert [slot["id"] for slot in plan["cells"]] == ORDER
        assert len({slot["trial_name"] for slot in plan["cells"]}) == 6
        assert len(plan["comparisons"]) == 5
        assert plan["coordinator"] and plan["registered_at"]
        assert all((out / "configs" / f"{c}.json").is_file() for c in ORDER)
        assert (out / "process" / f"{cell}.launch.json").is_file()
        slot = plan["cells"][len(calls)]
        calls.append(cell)
        kwargs["stdout"].write(f"synthetic private stdout: {out}\n".encode())
        kwargs["stderr"].write(b"synthetic private stderr\n")
        if behaviors.get(cell) == "interrupt":
            raise KeyboardInterrupt
        if behaviors.get(cell) == "nonzero":
            return subprocess.CompletedProcess(argv, 7)
        config = TrialConfig.model_validate(_json(input_path))
        assert config.trial_name == slot["trial_name"]
        assert config.environment.type == "docker"
        assert (
            config.environment.force_build is True and config.environment.delete is True
        )
        assert config.source_trial is None and config.user_agent is None
        assert [(p.seat, p.name, p.model_name) for p in config.participants] == [
            ("chef0", "oracle", None),
            ("chef1", "oracle", None),
        ]
        frozen = out / "capsule/cramped-room-parity"
        assert config.task.path == frozen
        assert config.trials_dir == out / "trials"
        for name in ("solution/solve.sh", "tests/test.sh"):
            assert stat.S_IMODE((frozen / name).stat().st_mode) == stat.S_IMODE(
                (TASK / name).stat().st_mode
            )
            assert (frozen / name).read_bytes() == (TASK / name).read_bytes()
        for name, record in plan["capsule_files"].items():
            assert (
                sha256((out / "capsule" / name).read_bytes()).hexdigest()
                == record["sha256"]
            )
        expected = ["cramped-room-tape.override.yaml"]
        if cell.endswith("WT"):
            expected.append("cramped-room-wait.override.yaml")
        if cell.endswith("TW"):
            expected.append("cramped-room-chef1-wait.override.yaml")
        overlays = config.environment.extra_docker_compose
        assert [p.name for p in overlays] == expected
        assert all(p.parent == out / "capsule/overlays" for p in overlays)
        trial = config.trials_dir / config.trial_name
        trial.mkdir()
        (trial / "config.json").write_text(config.model_dump_json(exclude={"agent"}))
        resolved = config.model_dump(mode="json")
        if behaviors.get(cell) == "foreign_config":
            resolved["participants"][0]["model_name"] = "unexpected-model"
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "id": ids.get(cell, str(uuid4())),
                    "trial_name": trial.name,
                    "trial_uri": trial.as_uri(),
                    "task_id": {"path": str(frozen)},
                    "config": resolved,
                    "seat_results": [
                        {
                            "seat": role,
                            "under_test": True,
                            "agent_info": {"name": "oracle", "model_info": None},
                        }
                        for role in ROLES
                    ],
                }
            )
        )
        (trial / "lock.json").write_text(
            json.dumps(
                {
                    "task": {"type": "local", "path": str(frozen)},
                    "extra_docker_compose": [
                        {
                            "path": str(p),
                            "digest": "sha256:" + sha256(p.read_bytes()).hexdigest(),
                        }
                        for p in overlays
                    ],
                }
            )
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(study.subprocess, "run", launch)
    return calls


def _stub_exporter(out, *, invalid=(), wrong_policy=(), other_case=()):
    """Construct only boundary data, without pretending to verify a kitchen."""
    plan = _json(out / "plan.json")
    slots = {slot["trial_name"]: slot["id"] for slot in plan["cells"]}
    calls = []

    def export(trial, path):
        cell = slots[trial.name]
        calls.append(cell)
        assert trial.parent == out / "trials"
        assert path.name == f"{cell}.jsonl"
        assert path.parent.parent == out and not path.exists()
        if cell in invalid:
            raise ValueError(f"Synthetic legacy trial without receipts: {trial}")
        native_id = _json(trial / "result.json")["id"]
        case = "b" * 64 if cell in other_case else CASE
        hashes = {}
        with path.open("xb") as file:
            policies = "TT" if cell in wrong_policy else cell[2:]
            for role, policy in zip(ROLES, policies, strict=True):
                actions = plan["tape_actions"][role] if policy == "T" else ["stay"] * 38
                episode = {
                    "schema": "seatlab.episode/v1",
                    "id": f"kitchen:{native_id}:{role}",
                    "case_sha256": case,
                    "role": role,
                    "native_trial_id": native_id,
                    "closed": True,
                    "frames": [{"observed": {"action": action}} for action in actions],
                }
                hashes[role] = episode["episode_sha256"] = sha256(
                    _canonical(episode)
                ).hexdigest()
                file.write(_canonical(episode) + b"\n")
        return {
            "native_trial_id": native_id,
            "case_sha256": case,
            "reward": float(cell.endswith("TT")),
            "deliveries_by_seat": {"chef0": 0, "chef1": int(cell.endswith("TT"))},
            "episodes": 2,
            "turns_per_role": 38,
            "episode_sha256": hashes,
            "private_extra": str(trial),
        }

    return export, calls


def test_registers_then_runs_each_cell_once_and_exports_native_team_slots(
    study, paths, monkeypatch
):
    harbor, out, portable = paths
    calls = _fake_process(study, monkeypatch, harbor, out)
    assert study.main(["run", "--harbor", str(harbor), "--out", str(out)]) == 0
    assert calls == ORDER
    with pytest.raises(FileExistsError, match="already exists"):
        study.run(harbor, out)
    assert calls == ORDER

    exporter, exports = _stub_exporter(out)
    module = ModuleType("seatlab_export")
    vars(module)["export_trial"] = exporter
    monkeypatch.setitem(sys.modules, "seatlab_export", module)
    assert study.main(["collect", "--out", str(out), "--export", str(portable)]) == 0
    assert exports == ORDER
    summary = _json(portable / "index.json")
    assert summary["measured_trials"] == 6
    assert [cell["id"] for cell in summary["cells"]] == ORDER
    cells = {cell["id"]: cell for cell in summary["cells"]}
    assert len({cell["native_trial_id"] for cell in cells.values()}) == 6
    assert cells["A-WT"]["reward"] == 0 and cells["A-WT"]["status"] == "measured"
    assert cells["A-WT"]["deliveries_by_seat"] == {"chef0": 0, "chef1": 0}
    pairs = [
        ("A-TT", "A-WT"),
        ("A-TT", "A-TW"),
        ("B-TT", "B-WT"),
        ("B-TT", "B-TW"),
        ("A-TT", "B-TT"),
    ]
    expected_files = {"index.json", *(f"{cell}.jsonl" for cell in ORDER)}
    assert [(m["source"], m["target"]) for m in summary["comparisons"]] == pairs
    for entry, (source, target) in zip(summary["comparisons"], pairs, strict=True):
        name = f"{source}-vs-{target}.json"
        assert entry["manifest"] == name
        assert entry["source_native_trial_id"] == cells[source]["native_trial_id"]
        assert entry["target_native_trial_id"] == cells[target]["native_trial_id"]
        manifest = _json(portable / name)
        expected_files.add(name)
        assert manifest == {
            "schema": "seatlab.episode-pairs/v1",
            "pairs": [
                {
                    "id": f"{source}-vs-{target}-{role}",
                    "case_sha256": CASE,
                    "role": role,
                    "source": {
                        "native_trial_id": cells[source]["native_trial_id"],
                        "episode_sha256": cells[source]["episode_sha256"][role],
                    },
                    "target": {
                        "native_trial_id": cells[target]["native_trial_id"],
                        "episode_sha256": cells[target]["episode_sha256"][role],
                    },
                }
                for role in ROLES
            ],
        }
    assert {p.name for p in portable.iterdir()} == expected_files
    assert all(str(out).encode() not in p.read_bytes() for p in portable.iterdir())
    assert all(b"private_extra" not in p.read_bytes() for p in portable.iterdir())
    with pytest.raises(FileExistsError, match="already exists"):
        study.collect(out, portable, exporter=exporter)
    assert exports == ORDER


def test_partial_study_keeps_all_names_and_does_not_invent_a_case(
    study, paths, monkeypatch
):
    harbor, out, portable = paths
    calls = _fake_process(
        study,
        monkeypatch,
        harbor,
        out,
        behaviors={
            "A-TW": "nonzero",
            "B-TW": "interrupt",
        },
    )
    with pytest.raises(KeyboardInterrupt):
        study.run(harbor, out)
    assert calls == ORDER[:4]
    assert _json(out / "process/A-TW.result.json")["returncode"] == 7
    assert (
        _json(out / "process/B-TW.result.json")["process_error"] == "KeyboardInterrupt"
    )
    exporter, exports = _stub_exporter(out, invalid={"A-WT"})
    study.collect(out, portable, exporter=exporter)
    assert exports == ORDER[:2]
    summary = _json(portable / "index.json")
    cells = {cell["id"]: cell for cell in summary["cells"]}
    assert list(cells) == ORDER
    assert summary["measured_trials"] == 1
    assert cells["A-WT"]["reason"] == "invalid_native_evidence"
    assert cells["A-TW"]["reason"] == "process_failed"
    assert cells["B-WT"]["reason"] == "not_launched"
    assert set(p.name for p in portable.iterdir()) == {
        "index.json",
        "A-TT.jsonl",
        "A-TT-vs-A-WT.json",
        "A-TT-vs-A-TW.json",
        "A-TT-vs-B-TT.json",
    }
    for entry in summary["comparisons"]:
        if entry["source"] == "B-TT":
            assert entry["manifest"] is None and entry["reason"] == "no_trusted_case"
        else:
            manifest = _json(portable / entry["manifest"])
            assert len(manifest["pairs"]) == 2
            assert all(
                p["case_sha256"] == CASE and p["target"] is None
                for p in manifest["pairs"]
            )
            assert entry["source_native_trial_id"] == cells["A-TT"]["native_trial_id"]
            assert (
                entry["target_native_trial_id"]
                == cells[entry["target"]]["native_trial_id"]
            )


def test_duplicate_ids_wrong_policy_and_substituted_result_stay_missing(
    study, paths, monkeypatch
):
    harbor, out, portable = paths
    duplicate = str(uuid4())
    calls = _fake_process(
        study,
        monkeypatch,
        harbor,
        out,
        behaviors={"A-TW": "foreign_config"},
        ids={"A-TT": duplicate, "A-WT": duplicate},
    )
    assert study.run(harbor, out) == 1 and calls == ORDER
    slots = {
        slot["id"]: slot["trial_name"] for slot in _json(out / "plan.json")["cells"]
    }
    result_path = out / "trials" / slots["B-WT"] / "result.json"
    result_path.write_bytes(
        (out / "trials" / slots["B-TT"] / "result.json").read_bytes()
    )
    exporter, exports = _stub_exporter(out, wrong_policy={"B-TW"})
    study.collect(out, portable, exporter=exporter)
    summary = _json(portable / "index.json")
    assert exports == ["B-TW", "B-TT"]
    assert summary["measured_trials"] == 1
    assert [cell.get("reason") for cell in summary["cells"]] == [
        "duplicate_native_trial_id",
        "duplicate_native_trial_id",
        "invalid_native_registration",
        "invalid_native_evidence",
        "invalid_native_registration",
        None,
    ]
    assert not (portable / "B-TW.jsonl").exists()


def test_different_cases_do_not_create_pairs(study, paths, monkeypatch):
    harbor, out, portable = paths
    _fake_process(study, monkeypatch, harbor, out)
    assert study.run(harbor, out) == 0
    exporter, _ = _stub_exporter(out, other_case={"A-TT"})
    study.collect(out, portable, exporter=exporter)
    index = _json(portable / "index.json")
    assert index["measured_trials"] == 6
    assert [entry.get("reason") for entry in index["comparisons"]] == [
        "case_mismatch",
        "case_mismatch",
        None,
        None,
        "case_mismatch",
    ]
    assert not (portable / "A-TT-vs-B-TT.json").exists()


def test_changed_capsule_and_dangling_output_are_refused(study, paths, monkeypatch):
    harbor, out, portable = paths
    _fake_process(study, monkeypatch, harbor, out)
    assert study.run(harbor, out) == 0
    exporter, calls = _stub_exporter(out)
    portable.symlink_to(out.parent / "does-not-exist")
    with pytest.raises(FileExistsError, match="already exists"):
        study.collect(out, portable, exporter=exporter)
    assert portable.is_symlink()
    (out / "capsule/cramped-room-parity/tests/test.sh").write_text(
        "changed after registration"
    )
    with pytest.raises(ValueError, match="frozen task capsule"):
        study.collect(out, out.parent / "unwritten", exporter=exporter)
    assert not calls and not (out.parent / "unwritten").exists()


def test_refuses_remote_docker_and_relative_harbor(study, paths, monkeypatch):
    harbor, out, _ = paths
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "ssh://example.invalid")
    with pytest.raises(ValueError, match="absolute path"):
        study.run(Path("harbor"), out)
    with pytest.raises(ValueError, match="local Docker Unix socket"):
        study.run(harbor, out)
    assert not out.exists()
