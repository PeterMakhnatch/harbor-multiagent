"""Prepare and run the four registered external-program trials. No retries.

python program_study.py prepare ROOT --node /ABS/node --python /ABS/python
python program_study.py run ROOT CELL --harbor /ABS
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from materialize import materialize  # noqa: E402
from study import ALLOWED, write_new  # noqa: E402

SCHEMA = "seatlab.pz-program-plan/v1"
JS = HERE / "programs" / "copy-or-high.mjs"
BEAT = HERE / "programs" / "beat.py"
INVALID = HERE / "programs" / "invalid.py"


def _exe(path: Path, flag: str) -> Path:
    if not path.is_absolute():
        raise SystemExit(f"{flag} must be an absolute path")
    resolved = path.resolve(strict=True)
    if not os.access(resolved, os.X_OK):
        raise SystemExit(f"{flag} must be an executable")
    return resolved


def _program(seat: str, argv: list[str]) -> dict[str, object]:
    return {
        "seat": seat,
        "import_path": "program_seat:ProgramSeat",
        "model_name": None,
        "kwargs": {"argv": argv, "call_timeout_sec": 10},
    }


def _oracle(seat: str) -> dict[str, object]:
    return {"seat": seat, "name": "oracle"}


def prepare(root: Path, node: Path, python: Path) -> None:
    root = root.resolve()
    node = _exe(node, "--node")
    python = _exe(python, "--python")
    for path in (JS, BEAT, INVALID):
        if not path.is_file():
            raise SystemExit(f"missing {path.name}")
    root.mkdir()
    suffix = secrets.token_hex(4)
    js, beat, invalid = str(JS.resolve()), str(BEAT.resolve()), str(INVALID.resolve())
    rows = (
        (
            "program-mixed",
            "rps_v2",
            "rps.json",
            {"p0": "low", "p1": "rps-beat-last"},
            "rps-copy-beat",
            [_program("p0", [str(node), js]), _oracle("p1")],
        ),
        (
            "program-both",
            "rps_v2",
            "rps.json",
            {"p0": "low", "p1": "low"},
            "rps-copy-beat",
            [_program("p0", [str(node), js]), _program("p1", [str(python), beat])],
        ),
        (
            "program-second",
            "connect_four_v3",
            "connect-four.json",
            {"p0": "low", "p1": "low"},
            "connect-lh",
            [_oracle("p0"), _program("p1", [str(node), js, "high"])],
        ),
        (
            "program-invalid",
            "rps_v2",
            "rps.json",
            {"p0": "low", "p1": "low"},
            None,
            [_program("p0", [str(python), invalid]), _oracle("p1")],
        ),
    )
    cells = []
    for cell_id, game, spec_name, policies, reference, participants in rows:
        spec = HERE / "presets" / spec_name
        if json.loads(spec.read_text(encoding="utf-8")).get("seed") != 19:
            raise SystemExit(f"{spec_name} seed must stay 19")
        name = f"{cell_id}-{suffix}"
        task = root / "tasks" / name
        materialize(spec, task, policies, None)
        write_new(
            root / "configs" / f"{cell_id}.json",
            {
                "schema_version": 1,
                "task": {"path": str(task)},
                "trials_dir": str(root / "trials"),
                "trial_name": name,
                "participants": participants,
                "environment": {"type": "docker", "force_build": True, "delete": True},
            },
        )
        cells.append(
            {
                "id": cell_id,
                "game": game,
                "policies": policies,
                "reference": reference,
                "trial_name": name,
            }
        )
    write_new(root / "plan.json", {"schema": SCHEMA, "cells": cells})


def run(root: Path, cell_id: str, harbor: Path) -> int:
    root = root.resolve()
    if not harbor.is_absolute():
        raise SystemExit("--harbor must be an absolute path")
    harbor = harbor.resolve(strict=True)
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
    env["PYTHONPATH"] = str(HERE)
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
    prep.add_argument("--node", required=True, type=Path)
    prep.add_argument("--python", required=True, type=Path)
    runner = sub.add_parser("run")
    runner.add_argument("root", type=Path)
    runner.add_argument("cell")
    runner.add_argument("--harbor", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root, args.node, args.python)
        return 0
    return run(args.root, args.cell, args.harbor)


if __name__ == "__main__":
    raise SystemExit(main())
