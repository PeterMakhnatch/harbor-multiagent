"""Prepare and run the 8 frozen PettingZoo seat trials. No retries."""

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

# Protocol order. l/h/copy/beat are low/high/rps-copy/rps-beat-last.
CELLS = (
    ("rps-ll-a", "rps_v2", "rps.json", "low", "low"),
    ("rps-ll-b", "rps_v2", "rps.json", "low", "low"),
    ("rps-lh", "rps_v2", "rps.json", "low", "high"),
    ("rps-copy-beat", "rps_v2", "rps.json", "rps-copy", "rps-beat-last"),
    ("connect-ll-a", "connect_four_v3", "connect-four.json", "low", "low"),
    ("connect-ll-b", "connect_four_v3", "connect-four.json", "low", "low"),
    ("connect-lh", "connect_four_v3", "connect-four.json", "low", "high"),
    ("connect-hl", "connect_four_v3", "connect-four.json", "high", "low"),
)
ALLOWED = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT")
PARTICLE = (
    ("spread-lll", "simple_spread_v3", {"p0": "low", "p1": "low", "p2": "low"}),
    ("spread-llh", "simple_spread_v3", {"p0": "low", "p1": "low", "p2": "high"}),
)
SCHEMAS = frozenset({"seatlab.pz-plan/v1", "seatlab.pz-particle-plan/v1"})


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")


def prepare(root: Path) -> None:
    root = root.resolve()
    root.mkdir()
    suffix = secrets.token_hex(4)
    cells = []
    for cell_id, game, spec, p0, p1 in CELLS:
        name = f"{cell_id}-{suffix}"
        policies = {"p0": p0, "p1": p1}
        task = root / "tasks" / name
        materialize(HERE / "presets" / spec, task, policies, None)
        write_new(
            root / "configs" / f"{cell_id}.json",
            {
                "schema_version": 1,
                "task": {"path": str(task)},
                "trials_dir": str(root / "trials"),
                "trial_name": name,
                "participants": [
                    {"seat": seat, "name": "oracle"} for seat in ("p0", "p1")
                ],
                "environment": {"type": "docker", "force_build": True, "delete": True},
            },
        )
        cells.append(
            {"id": cell_id, "game": game, "policies": policies, "trial_name": name}
        )
    write_new(root / "plan.json", {"schema": "seatlab.pz-plan/v1", "cells": cells})


def prepare_particle(root: Path) -> None:
    root = root.resolve()
    root.mkdir()
    suffix = secrets.token_hex(4)
    spec = HERE / "presets" / "simple-spread.json"
    requirements = HERE / "presets" / "simple-spread-requirements.txt"
    cells = []
    for cell_id, game, policies in PARTICLE:
        name = f"{cell_id}-{suffix}"
        bound = dict(policies)
        materialize(spec, root / "tasks" / name, bound, requirements)
        write_new(
            root / "configs" / f"{cell_id}.json",
            {
                "schema_version": 1,
                "task": {"path": str(root / "tasks" / name)},
                "trials_dir": str(root / "trials"),
                "trial_name": name,
                "participants": [
                    {"seat": seat, "name": "oracle"} for seat in sorted(bound)
                ],
                "environment": {"type": "docker", "force_build": True, "delete": True},
            },
        )
        cells.append(
            {"id": cell_id, "game": game, "policies": bound, "trial_name": name}
        )
    write_new(
        root / "plan.json", {"schema": "seatlab.pz-particle-plan/v1", "cells": cells}
    )


def run(root: Path, cell_id: str, harbor: Path) -> int:
    root = root.resolve()
    if not harbor.is_absolute():
        raise SystemExit("--harbor must be an absolute path")
    harbor = harbor.resolve(strict=True)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    cells = [cell for cell in plan.get("cells", []) if cell.get("id") == cell_id]
    if plan.get("schema") not in SCHEMAS or len(cells) != 1:
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
    prep.add_argument("--suite", choices=("particle",))
    runner = sub.add_parser("run")
    runner.add_argument("root", type=Path)
    runner.add_argument("id")
    runner.add_argument("--harbor", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        if args.suite == "particle":
            prepare_particle(args.root)
        else:
            prepare(args.root)
        return 0
    return run(args.root, args.id, args.harbor)


if __name__ == "__main__":
    raise SystemExit(main())
