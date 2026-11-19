"""Prepare and run the 15 registered speaker-listener trials. No retries.

python study.py prepare ROOT --python /ABS/python
python study.py run ROOT CELL --harbor /ABS

program_study.py cannot run these cells: its plan schema and --node input
belong to the four RPS and Connect Four program trials. This command uses
that runner's claim, exclusive write, and scrubbed environment.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

W = Path(__file__).resolve().parent
P = W.parent
ACTOR = W / "actor.py"
REQUIREMENTS = P / "presets" / "simple-spread-requirements.txt"
IMPORT = "program_seat:ProgramSeat"
SCHEMA = "seatlab.pz-communication-plan/v1"
FACTORY = "mpe2.simple_speaker_listener_v4:parallel_env"
GAME = "simple_speaker_listener_v4"
SPEC_SCHEMA = "seatlab.pz-world/v1"
SPEC_KEYS = frozenset(
    {"schema", "api", "factory", "kwargs", "roles", "seed", "max_world_steps"}
)
ROLES = {"p0": "speaker_0", "p1": "listener_0"}
KWARGS = {"continuous_actions": False, "max_cycles": 20}
MAX_WORLD_STEPS = 128
SEEDS = (5, 19, 73)
TEAMS = (
    ("plain-plain", "plain", "plain"),
    ("rotate-rotate", "rotate", "rotate"),
    ("plain-rotate", "plain", "rotate"),
    ("rotate-plain", "rotate", "plain"),
    ("plain-blind", "plain", "blind"),
)
CELLS = tuple(
    (f"s{seed}-{team}", seed, team, speaker, listener)
    for seed in SEEDS
    for team, speaker, listener in TEAMS
)
REQUIRED_PINS = ("mpe2==1.1.1", "pettingzoo==1.26.1")
TASK_POLICIES = {"p0": "low", "p1": "low"}


def _load(path: Path, name: str) -> Any:
    loaded = importlib.util.spec_from_file_location(name, path)
    if loaded is None or loaded.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    return module


_SIBLING = _load(P / "study.py", "pz_seats_study_for_communication")
_MATERIALIZE = _load(P / "materialize.py", "pz_seats_materialize_for_communication")
ALLOWED = _SIBLING.ALLOWED
write_new = _SIBLING.write_new
materialize = _MATERIALIZE.materialize


def _exe(path: Path, flag: str) -> Path:
    if not path.is_absolute():
        raise SystemExit(f"{flag} must be an absolute path")
    resolved = path.resolve(strict=True)
    if not os.access(resolved, os.X_OK):
        raise SystemExit(f"{flag} must be an executable")
    return resolved


def _script(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise SystemExit(f"missing {resolved.name}")
    return resolved


def _pins(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"missing {path.name}")
    lines = path.read_text(encoding="utf-8").splitlines()
    missing = [pin for pin in REQUIRED_PINS if pin not in lines]
    if missing:
        raise SystemExit("requirements missing " + ", ".join(missing))


def world_spec(seed: int) -> dict[str, object]:
    if seed not in SEEDS:
        raise SystemExit(f"seed {seed} is not registered")
    spec: dict[str, object] = {
        "schema": SPEC_SCHEMA,
        "api": "parallel",
        "factory": FACTORY,
        "kwargs": dict(KWARGS),
        "roles": dict(ROLES),
        "seed": seed,
        "max_world_steps": MAX_WORLD_STEPS,
    }
    if set(spec) != SPEC_KEYS or spec["roles"] != ROLES or spec["kwargs"] != KWARGS:
        raise SystemExit("world spec is not the registered speaker-listener game")
    return spec


def _program(
    seat: str, python_path: str, script: str, codebook: str
) -> dict[str, object]:
    return {
        "seat": seat,
        "import_path": IMPORT,
        "model_name": None,
        "kwargs": {
            "argv": [python_path, script, codebook],
            "call_timeout_sec": 10,
        },
    }


def _same_spec(tasks: list[Path], seed: int) -> None:
    blobs = [(task / "environment" / "spec.json").read_bytes() for task in tasks]
    if len(blobs) != len(TEAMS) or len(set(blobs)) != 1:
        raise SystemExit(f"seed {seed} spec drifted across conventions")
    text = (tasks[0] / "environment" / "requirements.txt").read_text(encoding="utf-8")
    if any(pin not in text.splitlines() for pin in REQUIRED_PINS):
        raise SystemExit("task requirements do not keep the native pins")


def prepare(root: Path, python: Path) -> None:
    root = root.resolve()
    python_path = str(_exe(python, "--python"))
    script = str(_script(ACTOR))
    _pins(REQUIREMENTS)
    root.mkdir()
    suffix = secrets.token_hex(4)
    spec_for = {}
    for seed in SEEDS:
        path = root / "specs" / f"seed-{seed}.json"
        write_new(path, world_spec(seed))
        spec_for[seed] = path
    cells: list[dict[str, object]] = []
    tasks_by_seed: dict[int, list[Path]] = {seed: [] for seed in SEEDS}
    for cell_id, seed, team, speaker, listener in CELLS:
        name = f"{cell_id}-{suffix}"
        task = root / "tasks" / name
        materialize(spec_for[seed], task, dict(TASK_POLICIES), REQUIREMENTS)
        tasks_by_seed[seed].append(task)
        write_new(
            root / "configs" / f"{cell_id}.json",
            {
                "schema_version": 1,
                "task": {"path": str(task)},
                "trials_dir": str(root / "trials"),
                "trial_name": name,
                "participants": [
                    _program("p0", python_path, script, speaker),
                    _program("p1", python_path, script, listener),
                ],
                "environment": {"type": "docker", "force_build": True, "delete": True},
            },
        )
        cells.append(
            {
                "id": cell_id,
                "game": GAME,
                "seed": seed,
                "team": team,
                "speaker": speaker,
                "listener": listener,
                "spec": f"seed-{seed}.json",
                "trial_name": name,
            }
        )
    for seed, tasks in tasks_by_seed.items():
        _same_spec(tasks, seed)
    write_new(
        root / "plan.json",
        {
            "schema": SCHEMA,
            "factory": FACTORY,
            "continuous_actions": False,
            "max_cycles": KWARGS["max_cycles"],
            "seeds": list(SEEDS),
            "requirements": REQUIREMENTS.name,
            "cells": cells,
        },
    )


def _bound(saved: dict[str, Any], cell: dict[str, Any], script: str) -> None:
    rows = saved.get("participants")
    if type(rows) is not list or len(rows) != 2:
        raise SystemExit("cell config must name two program seats")
    expected = {"p0": cell.get("speaker"), "p1": cell.get("listener")}
    seen = set()
    for row in rows:
        kwargs = row.get("kwargs") if type(row) is dict else None
        argv = kwargs.get("argv") if type(kwargs) is dict else None
        seat = row.get("seat") if type(row) is dict else None
        if (
            type(row) is not dict
            or row.get("import_path") != IMPORT
            or row.get("model_name") is not None
            or seat not in expected
            or seat in seen
            or type(argv) is not list
            or len(argv) != 3
            or type(argv[0]) is not str
            or not Path(argv[0]).is_absolute()
            or argv[1] != script
            or argv[2] != expected[seat]
        ):
            raise SystemExit("cell config is not the registered ProgramSeat pair")
        seen.add(seat)


def run(root: Path, cell_id: str, harbor: Path) -> int:
    root = root.resolve()
    if not harbor.is_absolute():
        raise SystemExit("--harbor must be an absolute path")
    harbor = harbor.resolve(strict=True)
    script = str(_script(ACTOR))
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    cells = [cell for cell in plan.get("cells", []) if cell.get("id") == cell_id]
    if plan.get("schema") != SCHEMA or len(cells) != 1:
        raise SystemExit(f"unknown cell {cell_id}")
    cell = cells[0]
    cfg = root / "configs" / f"{cell_id}.json"
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    _bound(saved, cell, script)
    trial = root / "trials" / cell["trial_name"]
    if saved.get("trial_name") != cell["trial_name"] or trial.exists():
        raise SystemExit("native result or trial directory already exists")
    claim = root / "attempts" / cell_id
    try:
        claim.mkdir(parents=True)
    except FileExistsError:
        raise SystemExit("cell already claimed; preserve its results") from None
    env = {key: os.environ[key] for key in ALLOWED if key in os.environ}
    env["PYTHONPATH"] = str(P)
    with (claim / "stdout.log").open("xb") as log:
        result = subprocess.run(
            [str(harbor), "trial", "start", "-c", str(cfg)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    write_new(
        claim / "exit.json",
        {"exit_code": result.returncode, "trial_name": cell["trial_name"]},
    )
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("root", type=Path)
    prep.add_argument("--python", required=True, type=Path)
    runner = sub.add_parser("run")
    runner.add_argument("root", type=Path)
    runner.add_argument("cell", choices=tuple(row[0] for row in CELLS))
    runner.add_argument("--harbor", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root, args.python)
        return 0
    return run(args.root, args.cell, args.harbor)


if __name__ == "__main__":
    raise SystemExit(main())
