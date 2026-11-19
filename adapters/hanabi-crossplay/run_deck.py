"""Run one prepared deck in stock Harbor. Each deck has an exclusive, permanent claim."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from study import PAIRINGS, write_new


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory from study.py prepare")
    parser.add_argument("deck", type=int)
    parser.add_argument(
        "--harbor", required=True, type=Path, help="stock Harbor executable"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    harbor = args.harbor.resolve(strict=True)
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    cells = [c for c in plan["cells"] if c["deck_id"] == args.deck]
    if plan["schema"] != "seatlab.hanabi-plan/v1" or [
        c["pairing"] for c in cells
    ] != list(PAIRINGS):
        parser.error("deck does not have its four registered pairings in order")
    jobs = [root / "configs" / f"d{args.deck:02}-{p}.json" for p in PAIRINGS]
    if not all(p.is_file() for p in jobs):
        parser.error("missing prepared trial configuration")
    claims = root / "attempts"
    claims.mkdir(exist_ok=True)
    directory = claims / f"d{args.deck:02}"
    if any((root / "trials" / c["trial_name"]).exists() for c in cells):
        parser.error(
            "a native trial directory already exists; refusing to replace the deck"
        )
    try:
        directory.mkdir()
    except FileExistsError:
        parser.error("deck already claimed; preserve its results")
    allowed = (
        "HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    successful_exits = True
    for cfg, cell in zip(jobs, cells, strict=True):
        label = cell["pairing"]
        with (directory / f"{label}.log").open("xb") as log:
            result = subprocess.run(
                [str(harbor), "trial", "start", "-c", str(cfg)],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        write_new(
            directory / f"{label}.json",
            {"trial_name": cell["trial_name"], "exit_code": result.returncode},
        )
        successful_exits &= result.returncode == 0
    return 0 if successful_exits else 1


if __name__ == "__main__":
    raise SystemExit(main())
