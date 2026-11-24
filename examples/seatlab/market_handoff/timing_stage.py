from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import decisions, native_stage, timing_world

TEMPLATE = Path(__file__).resolve().parent / "timing_task"
TIMING_FILES = (
    "task.toml",
    "environment/docker-compose.yaml",
    "environment/serve.py",
    "instructions/sales.md",
    "tests/test.sh",
    "tests/score.py",
)
STOCK_FILES = ("environment/Dockerfile", "environment/player/Dockerfile")
FINANCE_INSTRUCTION = "instructions/finance.md"
SCENARIO = "environment/scenario.json"
VERIFIER_SCENARIO = "tests/scenario.json"
SCENARIO_SCHEMA = "seatlab.price-timing-scenario/v1"
SEAT_IMPORT = "examples.seatlab.market_handoff.timing_actor:TimingSeat"
SALES = "sales"
FINANCE = ("finance_a", "finance_b")
SEATS = (SALES, *FINANCE)
REPEATS = 2
EVIDENCE = "artifacts/logs/world/timing.json"
JOURNAL = "artifacts/logs/world/timing-journal.json"
REWARD = "verifier/reward.json"
MAX_TEMPLATE_BYTES = native_stage.MAX_TEMPLATE_BYTES


def trial_name(world: int, repeat: int) -> str:
    if type(world) is not int or not 0 <= world <= timing_world.CONTROL_WORLD:
        native_stage.fail(f"world must be an int in 0..{timing_world.CONTROL_WORLD}")
    if type(repeat) is not int or not 0 <= repeat < REPEATS:
        native_stage.fail(f"repeat must be an int in 0..{REPEATS - 1}")
    return f"price-w{world:02d}-r{repeat}"


def trial_paths(out: Path, world: int, repeat: int) -> dict[str, Path]:
    trial = out / "trials" / trial_name(world, repeat)
    return {
        "trial": trial,
        "result": trial / "result.json",
        "evidence": trial / EVIDENCE,
        "journal": trial / JOURNAL,
        "reward": trial / REWARD,
    }


def owner_root(out: Path, seat: str) -> Path:
    if seat not in SEATS:
        native_stage.fail(f"unknown timing seat: {seat!r}")
    return out / "private" / seat


def _template(name: str) -> bytes:
    path = TEMPLATE / name
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_TEMPLATE_BYTES:
        native_stage.fail(
            f"{path}: template file must be a regular file under "
            f"{MAX_TEMPLATE_BYTES} bytes"
        )
    data = path.read_bytes()
    if len(data) != details.st_size:
        native_stage.fail(f"{path}: template changed while reading")
    return data


def _checked_binding(binding: dict[str, Any], world: int) -> None:
    if not isinstance(binding, dict) or binding.keys() != timing_world.BINDING_KEYS:
        native_stage.fail("binding keys do not match timing_world.BINDING_KEYS")
    if binding["world"] != world or type(binding["world"]) is not int:
        native_stage.fail("binding belongs to another world")
    if (
        binding["world_seed"] != timing_world.world_seed(world)
        or binding["truth_seed"] != timing_world.truth_seed(world)
        or binding["shape"] != timing_world.SHAPES[world]
        or binding["stock_commit"] != timing_world.PIN
    ):
        native_stage.fail("binding does not match the registered world table")
    checkpoint = binding["checkpoint_sha256"]
    if not isinstance(checkpoint, str) or not decisions.HEX64.fullmatch(checkpoint):
        native_stage.fail("binding checkpoint_sha256 must be lowercase SHA-256 hex")
    for role in ("sales", "finance"):
        view = binding[role]
        if not isinstance(view, dict) or not view:
            native_stage.fail(f"binding {role} view must be a non-empty object")


def _checked_client(client: Path | None, control: bool) -> str | None:
    if control:
        if client is not None:
            native_stage.fail("control stages no client")
        return None
    text = str(client)
    if (
        not isinstance(client, Path)
        or not client.is_absolute()
        or os.path.normpath(text) != text
    ):
        native_stage.fail("client must be an absolute normalized host path")
    if not (client.is_file() and os.access(client, os.X_OK)):
        native_stage.fail("client must be an executable host file")
    return text


def scenario(binding: dict[str, Any], early: str) -> dict[str, Any]:
    if early not in FINANCE:
        native_stage.fail(f"early seat must be one of {FINANCE}")
    return {
        "schema": SCENARIO_SCHEMA,
        "early": early,
        "checkpoint_sha256": binding["checkpoint_sha256"],
        "sales": binding["sales"],
        "finance": binding["finance"],
    }


def task_files(scenario_bytes: bytes) -> dict[str, bytes]:
    files = {name: _template(name) for name in TIMING_FILES}
    files.update({name: native_stage.template_bytes(name) for name in STOCK_FILES})
    finance = _template(FINANCE_INSTRUCTION)
    files.update({f"instructions/{seat}.md": finance for seat in FINANCE})
    files[SCENARIO] = files[VERIFIER_SCENARIO] = scenario_bytes
    return files


def _seat_kwargs(
    out: Path, seat: str, client: str | None, control: bool
) -> dict[str, Any]:
    assert type(control) is bool and (client is None) == control
    owner = str(owner_root(out, seat))
    if client is None:
        return {"owner": owner, "control": control}
    return {"client": client, "owner": owner, "control": control}


def participants(out: Path, client: str | None, control: bool) -> list[dict[str, Any]]:
    return [
        {
            "seat": seat,
            "name": None,
            "import_path": SEAT_IMPORT,
            "model_name": None,
            "kwargs": _seat_kwargs(out, seat, client, control),
        }
        for seat in SEATS
    ]


def trial_config(
    out: Path, name: str, client: str | None, control: bool
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
        "participants": participants(out, client, control),
    }


def stage(
    out: Path,
    world: int,
    repeat: int,
    binding: dict[str, Any],
    early: str,
    client: Path | None,
    control: bool = False,
) -> Path:
    if type(control) is not bool:
        native_stage.fail("control must be a bool")
    name = trial_name(world, repeat)
    if control != (world == timing_world.CONTROL_WORLD):
        native_stage.fail(f"control mode is exactly world {timing_world.CONTROL_WORLD}")
    _checked_binding(binding, world)
    client_text = _checked_client(client, control)
    scenario_bytes = native_stage.canonical(scenario(binding, early))
    files = task_files(scenario_bytes)
    out = native_stage.empty_out(out)
    out.chmod(0o700)
    (out / "private").mkdir(mode=0o700)
    config = trial_config(out, name, client_text, control)
    native_stage.write_task(out / "task", files)
    native_stage.create(out / "config.json", native_stage.canonical(config), 0o600)
    return out / "config.json"
