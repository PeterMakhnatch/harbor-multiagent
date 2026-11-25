import json
import stat
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from examples.seatlab.physical_dispatch import certificate, preflight, stage

SOURCE = Path(__file__).resolve().parents[3] / "examples/tasks/exhibit-relay"
ROLES = ("loader", "registrar", "depot")
SETTING = "EXHIBIT_BAY_MAP"
CHANGED = {
    "README.md",
    "task.toml",
    "environment/docker-compose.yaml",
    "environment/serve.py",
}
ADDED = {"environment/exhibit_relay/physical_cases.py"}


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def files(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), mode(path))
        for path in root.rglob("*")
        if path.is_file()
    }


def document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage_stock_and_native_role_contract(tmp_path: Path) -> None:
    before = files(SOURCE)
    task = tmp_path.resolve() / "task"
    receipt = stage.materialize(task)
    actual = files(task)
    assert files(SOURCE) == before
    assert set(actual) - set(before) == ADDED
    assert not set(before) - set(actual)
    assert {name for name in before if before[name][0] != actual[name][0]} == CHANGED
    assert mode(task) == 0o700
    assert receipt["stage_digest"] == stage.check_stage(task)
    for name in (
        *(f"instructions/{role}.md" for role in ROLES),
        "environment/seat/Dockerfile",
        "environment/exhibit_relay/grading.py",
        "environment/exhibit_relay/engine.py",
        "tests/verify.py",
        "tests/test.sh",
    ):
        assert actual[name][0] == before[name][0]
        assert bool(actual[name][1] & 0o111) == bool(before[name][1] & 0o111)
    stock = tomllib.loads(before["task.toml"][0].decode())
    revised = tomllib.loads(actual["task.toml"][0].decode())
    assert revised["task"]["name"] == "harbor/exhibit-relay-physical"
    revised["task"]["name"] = stock["task"]["name"]
    revised["task"]["description"] = stock["task"]["description"]
    assert revised["environment"]["env"].pop(SETTING) == "original"
    assert revised == stock
    assert set(revised["agents"]) == set(ROLES)
    filename = "environment/docker-compose.yaml"
    base_compose = yaml.safe_load(before[filename][0])
    new_compose = yaml.safe_load(actual[filename][0])
    services = new_compose["services"]
    assert set(services) == {"main", *ROLES}
    assert (
        services["main"]["environment"].pop(SETTING) == "${EXHIBIT_BAY_MAP:-original}"
    )
    assert new_compose == base_compose
    for role in ROLES:
        service = services[role]
        assert SETTING not in service.get("environment", {})
        assert service["network_mode"] == "none"
        assert service["user"] == "player"
        assert service["volumes"] == [f"{role}-rpc:/run/relay-seat:ro"]


def test_stage_and_preflight_reject_mutation(tmp_path: Path) -> None:
    task = tmp_path.resolve() / "task"
    stage.materialize(task)
    instruction = task / "instructions/loader.md"
    instruction.write_bytes(instruction.read_bytes() + b"\nChanged instruction.\n")
    with pytest.raises(ValueError):
        stage.check_stage(task)
    output = tmp_path.resolve() / "preflight"
    with pytest.raises(ValueError):
        preflight.prepare(task, output, seed=0)
    assert not output.exists()


def test_stage_and_preflight_preserve_existing_destination(tmp_path: Path) -> None:
    task = tmp_path.resolve() / "task"
    stage.materialize(task)
    occupied = tmp_path.resolve() / "occupied"
    occupied.mkdir()
    occupant = occupied / "keep.txt"
    occupant.write_bytes(b"existing data\n")
    before = files(occupied)
    with pytest.raises(ValueError):
        stage.materialize(occupied)
    assert files(occupied) == before
    with pytest.raises(ValueError):
        preflight.prepare(task, occupied, seed=0)
    assert files(occupied) == before


def test_preflight_native_inputs_are_isolated_and_private(tmp_path: Path) -> None:
    task = tmp_path.resolve() / "task"
    stage.materialize(task)
    before = files(task)
    output = tmp_path.resolve() / "preflight"
    seed = certificate.certify(0)["development_seed"]
    receipt = preflight.prepare(task, output, seed)
    assert receipt == document(output / "receipt.json")
    assert receipt["status"] == "prepared_not_enrolled"
    assert receipt["native_admission"] == "accepted"
    assert receipt["executions"] == 0
    assert receipt["provider_identities"] == []
    assert receipt["effect_estimated"] is False
    assert receipt["seed"] == seed
    assert receipt["role_count"] == len(ROLES)
    assert receipt["stage_digest"] == stage.check_stage(task)
    assert mode(output) == 0o700
    assert {path.name for path in output.iterdir()} == {
        "receipt.json",
        "original.config.json",
        "original.lock.json",
        "swapped.config.json",
        "swapped.lock.json",
    }
    assert all(mode(path) == 0o600 for path in output.iterdir())
    configs, locks = [], []
    for map_name in ("original", "swapped"):
        config = document(output / f"{map_name}.config.json")
        lock = document(output / f"{map_name}.lock.json")
        for record in (config, lock):
            env = record["environment"]["env"]
            assert env.pop(SETTING) == map_name
            assert env == {
                "EXHIBIT_SEED": str(seed),
                "EXHIBIT_PROFILE": "fork",
                "EXHIBIT_MODE": "linked",
                "EXHIBIT_LAYOUT": "draw",
            }
            assert tuple(member["seat"] for member in record["participants"]) == ROLES
            assert all(
                member.get("model_name") is None for member in record["participants"]
            )
        assert config["task"]["path"] == str(task)
        assert receipt["task_name"] == "harbor/exhibit-relay-physical"
        assert lock["task"]["type"] == "local"
        assert lock["task"]["name"] == task.name
        assert lock["task"]["path"] == str(task)
        assert lock["task"]["digest"] == receipt["native_digest"]
        assert config.pop("trial_name") == f"exhibit-physical-{seed}-{map_name}"
        configs.append(config)
        locks.append(lock)
    base, maps = certificate.native_pair(seed)
    assert base["realized"] == min(brief["id"] for brief in base["briefs"])
    assert receipt["natural_focal_draw"]
    assert {receipt["arms"][k]["case_commitment"] for k in maps} == {
        certificate.case_commitment(case) for case in maps.values()
    }
    assert configs[0] == configs[1]
    assert locks[0] == locks[1]
    assert files(task) == before
    assert not (output / "trials").exists()
