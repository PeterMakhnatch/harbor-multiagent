import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any

from harbor.models.trial.config import TrialConfig
from harbor.trial.seats import SeatsTrial
from harbor.trial.trial import Trial

from ..native_study import native, require, task_files_digest, task_identity, trial_request, write_exclusive
from ..shift_board.collect import _environment
from .collect import load_registration
from .gates import HELD, Qualification
from .observe import MAX_INTERVAL_SEC, MIN_INTERVAL_SEC, attach
from .register import CONTROL, QUESTION, TASK, control_team, trusted_json

CONTROL_INTERVAL = 0.4
MAX_SAMPLES = 32
TIMEOUT_SECONDS = 7.0


def inputs(args: argparse.Namespace) -> tuple[TrialConfig, dict[str, Any], bool, float, str]:
    study = load_registration(args.registration)
    harbor = native()
    task = args.task.resolve(strict=True)
    root = args.trials.resolve(strict=True)
    require(root.is_dir() and not root.is_relative_to(task) and not task.is_relative_to(root),
            "use an existing trials directory separate from the task")
    require(task.name == TASK.split("/")[1] and task_identity(harbor, task, TASK) == study["task"],
            "local task name, digest or checksum differs from the registration")
    files = task_files_digest(task, study["task"])
    require(files == study["task_files"], "full local task tree differs from the registration")
    matched = [r for r in study["cells"] if r["ticket"] == args.ticket]
    require(len(matched) == 1, "ticket not registered")
    cell = matched[0]
    team = study["teams"][cell["team"]]
    is_control = all(p.get("import_path") == CONTROL for p in team)
    if is_control:
        arms = trusted_json(QUESTION)["arms"]
        label = cell["team"]
        require(label in arms, "this launcher only drives the published scripted schedules")
        expected = {p["seat"]: p["kwargs"] for p in control_team(arms[label])}
        require({p["seat"]: p["kwargs"] for p in team} == expected and cell["target"] == "planner"
                and cell["observe"] == (label != "off"), "control roster or observation schedule differs")
        require(args.interval is None, "the registered controls have a fixed interval")
    else:
        require(all("name" in p for p in team) and args.allow_named_seats,
                "named seats can spend provider credit; pass --allow-named-seats for an authorized roster")
    interval = args.interval if args.interval is not None else (CONTROL_INTERVAL if is_control else 10.0)
    require(math.isfinite(interval) and MIN_INTERVAL_SEC <= interval <= MAX_INTERVAL_SEC,
            "invalid probe interval")
    request = trial_request(harbor, task, root, args.ticket, team, _environment(cell))
    return TrialConfig.model_validate(request), cell, is_control, interval, files


async def run(args: argparse.Namespace) -> bool:
    config, cell, is_control, interval, files = inputs(args)
    directory = config.trials_dir / config.trial_name
    directory.mkdir(mode=0o700, exist_ok=False)
    trial = await Trial.create(config)
    if not isinstance(trial, SeatsTrial):
        raise TypeError("checkpoint registration did not create a native SeatsTrial")
    write_exclusive(directory / "seat-cut-attempt.json", {
        "schema": "harbor.seat-cut.attempt/v1", "ticket": config.trial_name,
        "trial_id": str(trial.id), "team": cell["team"], "target": cell["target"],
        "peer": cell["peer"], "observe": cell["observe"], "task_files": files,
        "interval_seconds": interval if cell["observe"] else None,
        "max_samples": MAX_SAMPLES if cell["observe"] else 0,
        "timeout_seconds": TIMEOUT_SECONDS if cell["observe"] else None,
    }, 0o600)
    if cell["observe"]:
        attach(trial, target=cell["target"], peer=cell["peer"], interval_seconds=interval,
               max_samples=MAX_SAMPLES, timeout_seconds=TIMEOUT_SECONDS)
    gates = Qualification(trial, cell["team"]) if is_control and cell["team"] in HELD else None
    try:
        result = await trial.run()
    finally:
        schedule_ok = await gates.finish() if gates is not None else True
    clean = result.exception_info is None and all(r.exception_info is None for r in result.seat_results or [])
    print(json.dumps({"ticket": args.ticket, "native_clean": clean,
                      "reward_valid": result.reward_valid, "schedule_complete": schedule_ok}))
    return clean and result.reward_valid and schedule_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Claim and run exactly one registered native trial. Never retries.")
    parser.add_argument("registration", type=Path)
    parser.add_argument("task", type=Path)
    parser.add_argument("trials", type=Path)
    parser.add_argument("ticket")
    parser.add_argument("--allow-named-seats", action="store_true")
    parser.add_argument("--interval", type=float, help="world sampling in seconds for a named team")
    args = parser.parse_args()
    try:
        ok = asyncio.run(run(args))
    except (FileExistsError, ValueError) as error:
        parser.exit(2, f"run: {error}\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
