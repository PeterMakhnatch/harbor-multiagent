from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import (
    decisions,
    native_stage,
    observations,
    timing_messages,
    timing_stage,
)
from examples.seatlab.report_budget import domain

TEMPLATE = Path(__file__).resolve().parent / "task"
TASK_FILES = (
    "task.toml",
    "environment/Dockerfile",
    "environment/serve.py",
    "instructions/sales.md",
    "instructions/finance.md",
    "tests/test.sh",
    "tests/score.py",
)
STOCK_FILES = ("environment/docker-compose.yaml", "environment/player/Dockerfile")
PARSER_SOURCES = {
    "market_handoff/timing_messages.py": Path(timing_messages.__file__),
    "market_handoff/observations.py": Path(observations.__file__),
}
PARSER_HOMES = ("environment", "tests")
SCENARIO = "environment/scenario.json"
VERIFIER_SCENARIO = "tests/scenario.json"
SCENARIO_SCHEMA = "seatlab.report-budget-scenario/v1"
LOG_SCHEMA = "seatlab.report-budget-native/v1"
SEAT_IMPORT = "examples.seatlab.report_budget.actor:BudgetSeat"
SEATS = domain.SEATS
WORLD_URL = "http://world:8080"
LOG = "artifacts/logs/world/budget.json"
REWARD = "verifier/reward.json"
MAX_TEMPLATE_BYTES = native_stage.MAX_TEMPLATE_BYTES

assert SEATS == ("sales", "finance")


def trial_name(world: int, target: int) -> str:
    if type(world) is not int or not 0 <= world <= domain.CONTROL_WORLD:
        native_stage.fail(f"world must be an int in 0..{domain.CONTROL_WORLD}")
    if type(target) is not int or target not in domain.TARGETS:
        native_stage.fail(f"target must be one of {domain.TARGETS}")
    return f"budget-w{world:02d}-t{target:03d}"


def trial_paths(out: Path, world: int, target: int) -> dict[str, Path]:
    trial = out / "trials" / trial_name(world, target)
    return {
        "trial": trial,
        "result": trial / "result.json",
        "log": trial / LOG,
        "reward": trial / REWARD,
    }


def owner_root(out: Path, seat: str) -> Path:
    if seat not in SEATS:
        native_stage.fail(f"unknown budget seat: {seat!r}")
    return out / "private" / seat


def _regular(path: Path) -> bytes:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_TEMPLATE_BYTES:
        native_stage.fail(
            f"{path}: source must be a regular file under {MAX_TEMPLATE_BYTES} bytes"
        )
    data = path.read_bytes()
    if len(data) != details.st_size:
        native_stage.fail(f"{path}: source changed while reading")
    return data


def _checked_binding(binding: dict[str, Any]) -> int:
    if not isinstance(binding, dict) or binding.keys() != domain.BINDING_KEYS:
        native_stage.fail("binding keys do not match domain.BINDING_KEYS")
    world = binding["world"]
    if type(world) is not int or not 0 <= world <= domain.CONTROL_WORLD:
        native_stage.fail(f"binding world must be an int in 0..{domain.CONTROL_WORLD}")
    if (
        binding["world_seed"] != domain.world_seed(world)
        or binding["truth_seed"] != domain.truth_seed(world)
        or binding["shape"] != domain.shape(world)
        or binding["stock_commit"] != domain.PIN
    ):
        native_stage.fail("binding does not match the registered company table")
    checkpoint = binding["checkpoint_sha256"]
    if not isinstance(checkpoint, str) or not decisions.HEX64.fullmatch(checkpoint):
        native_stage.fail("binding checkpoint_sha256 must be lowercase SHA-256 hex")
    for seat in SEATS:
        view = binding[seat]
        if not isinstance(view, dict) or not view:
            native_stage.fail(f"binding {seat} view must be a non-empty object")
    return world


def scenario(binding: dict[str, Any], target: int) -> dict[str, Any]:
    return {
        "schema": SCENARIO_SCHEMA,
        "world": binding["world"],
        "target": target,
        "checkpoint_sha256": binding["checkpoint_sha256"],
        "sales": binding["sales"],
        "finance": binding["finance"],
    }


def task_files(scenario_bytes: bytes) -> dict[str, bytes]:
    files = {name: _regular(TEMPLATE / name) for name in TASK_FILES}
    files.update({name: native_stage.template_bytes(name) for name in STOCK_FILES})
    for name, source in PARSER_SOURCES.items():
        data = _regular(source)
        files.update({f"{home}/{name}": data for home in PARSER_HOMES})
    files[SCENARIO] = files[VERIFIER_SCENARIO] = scenario_bytes
    return files


def _seat_kwargs(
    out: Path, seat: str, target: int, client: str | None, control: bool
) -> dict[str, Any]:
    assert type(control) is bool and (client is None) == control
    owner = str(owner_root(out, seat))
    if client is None:
        return {"owner": owner, "control": control, "target": target}
    return {"owner": owner, "control": control, "target": target, "client": client}


def participants(
    out: Path, target: int, client: str | None, control: bool
) -> list[dict[str, Any]]:
    return [
        {
            "seat": seat,
            "name": None,
            "import_path": SEAT_IMPORT,
            "model_name": None,
            "kwargs": _seat_kwargs(out, seat, target, client, control),
        }
        for seat in SEATS
    ]


def trial_config(
    out: Path, name: str, target: int, client: str | None, control: bool
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(out / "task"), "git_url": None, "name": None},
        "trial_name": name,
        "trials_dir": str(out / "trials"),
        "agent_timeout_multiplier": 2.0,
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": participants(out, target, client, control),
    }


def stage(
    out: Path,
    binding: dict[str, Any],
    target: int,
    control: bool,
    client: Path | None,
) -> Path:
    if type(control) is not bool:
        native_stage.fail("control must be a bool")
    world = _checked_binding(binding)
    name = trial_name(world, target)
    if control != (world == domain.CONTROL_WORLD):
        native_stage.fail(f"control mode is exactly world {domain.CONTROL_WORLD}")
    client_text = timing_stage._checked_client(client, control)
    files = task_files(native_stage.canonical(scenario(binding, target)))
    out = native_stage.empty_out(out)
    out.chmod(0o700)
    (out / "private").mkdir(mode=0o700)
    config = trial_config(out, name, target, client_text, control)
    native_stage.write_task(out / "task", files)
    native_stage.create(out / "config.json", native_stage.canonical(config), 0o600)
    return out / "config.json"
