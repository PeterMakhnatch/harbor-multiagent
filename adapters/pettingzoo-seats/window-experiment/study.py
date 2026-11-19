"""Prepare and run the six registered window trials. No retries.

python study.py prepare ROOT --python ABS_PYTHON
python study.py run ROOT CELL --harbor ABS --seatlab-src ABS
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
IMPORT = "window_seat:WindowProgramSeat"
SCHEMA = "seatlab.pz-window-plan/v1"
SPEC_KEYS = frozenset(
    {"schema", "api", "factory", "kwargs", "roles", "seed", "max_world_steps"}
)
# id, predictor prior records, cycle first nonzero move. Cycle depth stays 2.
CELLS = (
    ("ascending-w0", 0, 1),
    ("ascending-w1", 1, 1),
    ("ascending-w4", 4, 1),
    ("mirrored-w0", 0, 2),
    ("mirrored-w1", 1, 2),
    ("mirrored-w4", 4, 2),
)
CYCLE_DEPTH = 2
PREDICTOR = W / "predictor.py"
CYCLE = W / "cycle.py"
SPEC = W / "rps-twelve.json"


def _load(path: Path, name: str) -> Any:
    loaded = importlib.util.spec_from_file_location(name, path)
    if loaded is None or loaded.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    return module


_SIBLING = _load(P / "study.py", "pz_seats_study")
_MATERIALIZE = _load(P / "materialize.py", "pz_seats_materialize")
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


def _directory(path: Path, flag: str) -> Path:
    if not path.is_absolute():
        raise SystemExit(f"{flag} must be an absolute path")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise SystemExit(f"{flag} must be a directory")
    return resolved


def _registered_spec() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    preset = json.loads((P / "presets" / "rps.json").read_text(encoding="utf-8"))
    if (
        type(spec) is not dict
        or set(spec) != SPEC_KEYS
        or spec.get("schema") != "seatlab.pz-world/v1"
        or spec.get("api") != "parallel"
        or spec.get("factory") != preset.get("factory")
        or spec.get("roles") != preset.get("roles")
        or spec.get("seed") != 19
        or spec.get("max_world_steps") != 128
        or spec.get("kwargs") != {"max_cycles": 12}
    ):
        raise SystemExit("rps-twelve.json is not the registered rps_v2 game")


def _seat(seat: str, argv: list[str], depth: int) -> dict[str, object]:
    if type(depth) is not int or not 0 <= depth <= 16:
        raise SystemExit("depth must be an int from 0 to 16")
    return {
        "seat": seat,
        "import_path": IMPORT,
        "model_name": None,
        "kwargs": {"argv": argv, "depth": depth},
    }


def prepare(root: Path, python: Path) -> None:
    root = root.resolve()
    python_path = str(_exe(python, "--python"))
    _registered_spec()
    for path in (PREDICTOR, CYCLE):
        if not path.is_file():
            raise SystemExit(f"missing {path.name}")
    root.mkdir()
    suffix = secrets.token_hex(4)
    predictor, cycle = str(PREDICTOR.resolve()), str(CYCLE.resolve())
    policies = {"p0": "low", "p1": "low"}
    cells = []
    for cell_id, depth, first in CELLS:
        name = f"{cell_id}-{suffix}"
        task = root / "tasks" / name
        materialize(SPEC, task, policies, None)
        write_new(
            root / "configs" / f"{cell_id}.json",
            {
                "schema_version": 1,
                "task": {"path": str(task)},
                "trials_dir": str(root / "trials"),
                "trial_name": name,
                "participants": [
                    _seat("p0", [python_path, predictor], depth),
                    _seat("p1", [python_path, cycle, str(first)], CYCLE_DEPTH),
                ],
                "environment": {"type": "docker", "force_build": True, "delete": True},
            },
        )
        cells.append(
            {
                "id": cell_id,
                "game": "rps_v2",
                "policies": policies,
                "predictor_depth": depth,
                "cycle_first": first,
                "cycle_depth": CYCLE_DEPTH,
                "trial_name": name,
            }
        )
    write_new(root / "plan.json", {"schema": SCHEMA, "cells": cells})


def run(root: Path, cell_id: str, harbor: Path, seatlab_src: Path) -> int:
    root = root.resolve()
    harbor = _exe(harbor, "--harbor")
    seatlab_src = _directory(seatlab_src, "--seatlab-src")
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    cells = [cell for cell in plan.get("cells", []) if cell.get("id") == cell_id]
    if plan.get("schema") != SCHEMA or len(cells) != 1:
        raise SystemExit(f"unknown cell {cell_id}")
    cell = cells[0]
    cfg = root / "configs" / f"{cell_id}.json"
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    trial = root / "trials" / cell["trial_name"]
    if saved.get("trial_name") != cell["trial_name"] or trial.exists():
        raise SystemExit("native result or trial directory already exists")
    claim = root / "attempts" / cell_id
    try:
        claim.mkdir(parents=True)
    except FileExistsError:
        raise SystemExit("cell already claimed; preserve its results") from None
    env = {key: os.environ[key] for key in ALLOWED if key in os.environ}
    env["PYTHONPATH"] = ":".join((str(W), str(P), str(seatlab_src)))
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
    runner.add_argument("--seatlab-src", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root, args.python)
        return 0
    return run(args.root, args.cell, args.harbor, args.seatlab_src)


if __name__ == "__main__":
    raise SystemExit(main())
