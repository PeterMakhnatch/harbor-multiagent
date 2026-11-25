import copy
import hashlib
import json
import os
import socket
import stat
import tempfile
from collections.abc import Iterator
from itertools import product
from pathlib import Path
from typing import Any

import pytest
import yaml

from examples.seatlab.exhibit_author_prepare import TURNS
from examples.seatlab.joint_handoffs import stage, world
from examples.seatlab.joint_handoffs.prepare import (
    PRIVATE_KEYS,
    RECEIVER,
    TRIAL_NAME,
    natural_focal,
    prepare,
    prepare_receiver,
    server_only,
)
from examples.seatlab.physical_dispatch.certificate import make_case
from examples.seatlab.workbench.arm_runtime import inspect_record
from examples.seatlab.workbench.arm_sources import inspect_source
from examples.seatlab.workbench.isolate import Arm, compare

ROLES = ("loader", "registrar", "depot")
OWNER = "examples.seatlab.exhibit_owner:RelayOwner"
FILES = {"config.json", "lock.json", "receipt.json"}
CELLS = tuple(product(world.MAPS, world.MAPS, world.CHANNELS))


def _seed(focal: bool) -> int:
    for seed in range(512):
        base = make_case(seed, "fork", layout="draw")
        if natural_focal(base) is not focal:
            continue
        try:
            for registry, bay in product(world.MAPS, repeat=2):
                world.select(base, registry, bay)
        except ValueError:
            continue
        return seed
    raise AssertionError("no admissible stock draw")


@pytest.fixture(scope="module")
def place() -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="jplan-") as workspace:
        root = Path(workspace).resolve()
        os.chmod(root, 0o700)
        task = root / "task"
        stage.materialize(task)
        yield root, task


def _sockets(root: Path, name: str) -> dict[str, Path]:
    folder = root / name
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    return {turn: folder / turn.replace("_", "-") for turn in TURNS}


def _owner(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    return folder / "owner"


def _published(out: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    assert {entry.name for entry in out.iterdir()} == FILES
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    records = {}
    for entry in out.iterdir():
        assert stat.S_IMODE(entry.stat().st_mode) == 0o600
        records[entry.name] = entry.read_bytes()
    assert json.loads(records["receipt.json"]) == receipt
    for name in ("config", "lock"):
        data = records[receipt[name]]
        assert receipt[f"{name}_digest"] == "sha256:" + hashlib.sha256(data).hexdigest()
    return {name: json.loads(data) for name, data in records.items()}


def _common(receipt: dict[str, Any], task: Path) -> None:
    assert receipt["status"] == "prepared_not_run"
    assert receipt["native_admission"] == "accepted"
    assert receipt["executions"] == receipt["model_calls"] == 0
    assert receipt["owner_contacted"] is False
    assert receipt["provider_authenticated"] is False
    assert receipt["provider_identities"] == []
    assert receipt["claims"]["holdout"] == "not_claimed"
    assert receipt["claims"]["provider_identity"] == "not_bound"
    assert receipt["claims"]["receiver_study_enrollment"] == "not_enrolled"
    assert receipt["claims"]["completed_results_audit"] == "not_performed"
    assert receipt["trial_name"] == TRIAL_NAME
    assert receipt["stage_digest"] == stage.check_stage(task)
    assert receipt["native_lock_covers_roles"] is True
    assert receipt["verifier_env"] == {"EXHIBIT_RETAIN_CUT": "1"}
    for role in ROLES:
        text = (task / "instructions" / f"{role}.md").read_bytes()
        assert receipt["role_file_digests"][role].endswith(
            hashlib.sha256(text).hexdigest()
        )
    assert set(receipt["role_instruction_digests"]) == set(ROLES)


def _neutral(config: dict[str, Any]) -> dict[str, Any]:
    rest = copy.deepcopy(config)
    rest.pop("trials_dir")
    env = rest["environment"]["env"]
    for key in PRIVATE_KEYS:
        env.pop(key)
    return rest


def _arm(task: Path, lock: dict[str, Any]) -> Arm:
    runtime = inspect_record(lock)
    return Arm(inspect_source(task, runtime.task), runtime)


def test_condition_reaches_only_the_server_service(place):
    _, task = place
    keys = server_only(task)
    compose = yaml.safe_load((task / "environment/docker-compose.yaml").read_text())
    services = compose["services"]
    assert set(services) == {"main", *ROLES}
    assert set(PRIVATE_KEYS) <= set(keys) <= set(services["main"]["environment"])
    for role in ROLES:
        assert "environment" not in services[role]
        assert not any(key in yaml.safe_dump(services[role]) for key in keys)


def test_team_plan_binds_owner_roles_and_hides_condition(place):
    root, task = place
    paths = _sockets(root, "team")
    nonfocal, focal = _seed(False), _seed(True)
    plain, swapped = ("original", "original"), ("swapped", "swapped")
    first = prepare(task, root / "a", nonfocal, *plain, "neither", paths)
    second = prepare(task, root / "b", nonfocal, *plain, "both", paths)
    third = prepare(task, root / "c", focal, *swapped, "registrar", paths)
    receipts = (first, second, third)
    records = [_published(root / n, r) for n, r in zip("abc", receipts, strict=True)]
    for receipt in receipts:
        _common(receipt, task)
        assert receipt["design"] == "autonomous_team"
        assert receipt["fixed_prefix_eligible"] is False
        assert receipt["claims"]["draw"] == "any_stock_draw"
        assert receipt["owner_turns"] == list(TURNS)
        assert receipt["team"] == {
            "roles": list(ROLES),
            "agent": OWNER,
            "autonomous": True,
        }
    assert first["natural_focal_draw"] is False and third["natural_focal_draw"] is True
    assert all(not os.path.lexists(path) for path in paths.values())
    configs = [record["config.json"] for record in records]
    for config in configs:
        assert config["trial_name"] == TRIAL_NAME
        assert config["task"] == {"path": str(task)}
        sockets = {
            p["seat"]: p["kwargs"]["owner_sockets"] for p in config["participants"]
        }
        assert sockets == {
            "loader": [str(paths["loader_request"]), str(paths["loader_dispatch"])],
            "registrar": [str(paths["registrar"])],
            "depot": [str(paths["depot"])],
        }
        assert all(p["import_path"] == OWNER for p in config["participants"])
        assert set(config["participants"][0]["kwargs"]) == {"role", "owner_sockets"}
        shown = json.dumps(config["participants"])
        assert "EXHIBIT_" not in shown and "swapped" not in shown
    assert configs[0]["participants"] == configs[1]["participants"]
    assert configs[0]["participants"] == configs[2]["participants"]
    assert _neutral(configs[0]) == _neutral(configs[1]) == _neutral(configs[2])
    assert configs[2]["environment"]["env"]["EXHIBIT_REGISTRY_MAP"] == "swapped"
    assert first["role_instruction_digests"] == third["role_instruction_digests"]
    locks = [record["lock.json"] for record in records]
    assert locks[0]["participants"] == locks[2]["participants"]
    isolation = compare(
        _arm(task, locks[0]),
        _arm(task, locks[1]),
        "world_setting",
        "EXHIBIT_CHANNELS",
        None,
    )
    assert isolation["status"] == "isolated", isolation["reasons"]


def test_team_plan_refuses_reused_occupied_endpoints_and_existing_output(place):
    root, task = place
    paths = _sockets(root, "refuse")
    seed = _seed(False)
    with pytest.raises(ValueError, match="distinct"):
        prepare(
            task,
            root / "reused",
            seed,
            "original",
            "swapped",
            "depot",
            {**paths, "depot": paths["registrar"]},
        )
    assert not os.path.lexists(root / "reused")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(paths["depot"]))
        with pytest.raises(ValueError, match="absent"):
            prepare(task, root / "busy", seed, "original", "swapped", "depot", paths)
    finally:
        listener.close()
        paths["depot"].unlink()
    assert not os.path.lexists(root / "busy")
    out = root / "kept"
    prepare(task, out, seed, "original", "swapped", "depot", paths)
    before = {entry.name: entry.read_bytes() for entry in out.iterdir()}
    with pytest.raises(ValueError, match="already exists"):
        prepare(task, out, seed, "swapped", "original", "both", paths)
    assert {entry.name: entry.read_bytes() for entry in out.iterdir()} == before


def test_receiver_plan_fixes_prefix_for_all_cells_and_one_final_owner(place):
    root, task = place
    owner = _owner(root, "receiver")
    seed = _seed(True)
    configs, locks = {}, {}
    for index, cell in enumerate(CELLS):
        out = root / f"r{index}"
        receipt = prepare_receiver(task, out, seed, *cell, owner)
        records = _published(out, receipt)
        _common(receipt, task)
        assert receipt["design"] == "fixed_prefix_receiver"
        assert receipt["fixed_prefix_eligible"] is True
        assert receipt["natural_focal_draw"] is True
        assert receipt["claims"]["draw"] == "natural_focal_only"
        assert receipt["owner_turns"] == ["loader_dispatch"]
        assert receipt["owner_endpoints"] == {"loader_dispatch": str(owner)}
        assert receipt["condition"] == dict(
            zip(("registry_map", "bay_map", "channels"), cell, strict=True)
        )
        configs[cell], locks[cell] = records["config.json"], records["lock.json"]
    assert not os.path.lexists(owner)
    team = configs[CELLS[0]]["participants"]
    assert [(p["seat"], p["import_path"], p["kwargs"]) for p in team] == [
        ("loader", RECEIVER, {"role": "loader", "owner_socket": str(owner)}),
        ("registrar", RECEIVER, {"role": "registrar"}),
        ("depot", RECEIVER, {"role": "depot", "filing": "relevant"}),
    ]
    neutral = _neutral(configs[CELLS[0]])
    for cell in CELLS:
        assert configs[cell]["participants"] == team
        assert locks[cell]["participants"] == locks[CELLS[0]]["participants"]
        assert _neutral(configs[cell]) == neutral
        env = configs[cell]["environment"]["env"]
        assert (
            env["EXHIBIT_REGISTRY_MAP"],
            env["EXHIBIT_BAY_MAP"],
            env["EXHIBIT_CHANNELS"],
        ) == cell
    commitments = {
        prepared["case_commitment"]
        for prepared in (
            json.loads((root / f"r{index}" / "receipt.json").read_bytes())
            for index in range(len(CELLS))
        )
    }
    assert len(commitments) == 4
    left = ("original", "original", "both")
    right = ("swapped", "original", "both")
    isolation = compare(
        _arm(task, locks[left]),
        _arm(task, locks[right]),
        "world_setting",
        "EXHIBIT_REGISTRY_MAP",
        None,
    )
    assert isolation["status"] == "isolated", isolation["reasons"]


def test_receiver_plan_refuses_nonfocal_seed_occupied_owner_and_existing_output(place):
    root, task = place
    owner = _owner(root, "receiver-refuse")
    with pytest.raises(ValueError, match="naturally focal"):
        prepare_receiver(
            task, root / "nonfocal", _seed(False), "original", "original", "both", owner
        )
    assert not os.path.lexists(root / "nonfocal")
    seed = _seed(True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(owner))
        with pytest.raises(ValueError, match="absent"):
            prepare_receiver(
                task, root / "taken", seed, "original", "original", "both", owner
            )
    finally:
        listener.close()
        owner.unlink()
    assert not os.path.lexists(root / "taken")
    out = root / "held"
    prepare_receiver(task, out, seed, "swapped", "swapped", "neither", owner)
    before = {entry.name: entry.read_bytes() for entry in out.iterdir()}
    with pytest.raises(ValueError, match="already exists"):
        prepare_receiver(task, out, seed, "original", "original", "both", owner)
    assert {entry.name: entry.read_bytes() for entry in out.iterdir()} == before
