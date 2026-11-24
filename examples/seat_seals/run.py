import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from harbor.models.trial.config import TrialConfig
from harbor.trial.seats import SeatsTrial
from harbor.trial.trial import Trial

from ..native_evidence import Invalid
from ..native_study import (
    native,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ..seat_checkpoints.observe import MAX_INTERVAL_SEC, MIN_INTERVAL_SEC
from ..seat_checkpoints.run import native_report
from .contract import (
    LOCK_TASK_NAME,
    NATURAL,
    SCRIPTED,
    TASK_NAME,
    WINDOW,
    environment,
    is_control,
    load_registration,
)
from .seal_host import SealQualification, attach_seal

ATTEMPT_SCHEMA = "harbor.seat-seal.attempt/v1"
CONTROL_INTERVAL = 0.4
NAMED_INTERVAL = 10.0
MAX_SAMPLES = 32
TIMEOUT_SECONDS = 7.0


class AlreadyStarted(FileExistsError):
    pass


def reserve(directory: Path) -> None:
    try:
        directory.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError:
        raise AlreadyStarted(
            f"trial {directory.name} already has an entry; no trial was started"
        ) from None


def trusted_task(
    harbor: SimpleNamespace, study: dict[str, Any], task: Path, root: Path
) -> str:
    require(
        root.is_dir()
        and not root.is_relative_to(task)
        and not task.is_relative_to(root),
        "use an existing trials directory separate from the task",
    )
    require(
        task.name == LOCK_TASK_NAME
        and task_identity(harbor, task, TASK_NAME) == study["task"],
        "local task name, digest or checksum differs from the registration",
    )
    files = task_files_digest(task, study["task"])
    require(
        files == study["task_files"],
        "full local task tree differs from the registration",
    )
    return files


def named_keys(team: list[dict[str, Any]]) -> None:
    assert len(team) == 2
    for member in team:
        name = member.get("model_api_key_name")
        require(
            name is None or os.environ.get(name, "").strip() != "",
            f"{member['seat']} selects {name}, which is unset or blank",
        )


def inputs(
    args: argparse.Namespace,
) -> tuple[TrialConfig, dict[str, Any], bool, float, str]:
    study = load_registration(args.registration)
    harbor = native()
    task = args.task.resolve(strict=True)
    root = args.trials.resolve(strict=True)
    files = trusted_task(harbor, study, task, root)
    matched = [cell for cell in study["cells"] if cell["ticket"] == args.ticket]
    require(len(matched) == 1, "ticket not registered")
    cell = matched[0]
    team = study["teams"][cell["team"]]
    control = is_control(study)
    if control:
        require(cell["schedule"] in SCRIPTED, "controls run a published schedule")
        require(args.interval is None, "the registered controls have a fixed interval")
        require(not args.allow_named_seats, "controls start no named seat")
    else:
        require(cell["schedule"] == NATURAL, "named rosters run the natural schedule")
        require(
            args.allow_named_seats,
            "named seats can spend provider credit; pass --allow-named-seats for an authorized roster",
        )
        named_keys(team)
    interval = (
        CONTROL_INTERVAL
        if control
        else (NAMED_INTERVAL if args.interval is None else args.interval)
    )
    require(
        math.isfinite(interval) and MIN_INTERVAL_SEC <= interval <= MAX_INTERVAL_SEC,
        "invalid probe interval",
    )
    request = trial_request(harbor, task, root, args.ticket, team, environment(cell))
    require(
        task_files_digest(task, study["task"]) == files,
        "task changed before the trial started",
    )
    return TrialConfig.model_validate(request), cell, control, interval, files


async def run(args: argparse.Namespace, *, summary_to_stderr: bool = False) -> bool:
    config, cell, control, interval, files = inputs(args)
    directory = config.trials_dir / config.trial_name
    reserve(directory)
    trial = await Trial.create(config)
    if not isinstance(trial, SeatsTrial):
        raise TypeError("seal registration did not create a native SeatsTrial")
    write_exclusive(
        directory / "seat-seal-attempt.json",
        {
            "schema": ATTEMPT_SCHEMA,
            "ticket": config.trial_name,
            "trial_id": str(trial.id),
            "team": cell["team"],
            "target": cell["target"],
            "peer": cell["peer"],
            "schedule": cell["schedule"],
            "mode": cell["mode"],
            "task_files": files,
            "interval_seconds": interval,
            "max_samples": MAX_SAMPLES,
            "timeout_seconds": TIMEOUT_SECONDS,
        },
        0o600,
    )
    permit = asyncio.Event() if cell["schedule"] == WINDOW else None
    attach_seal(
        trial,
        target=cell["target"],
        peer=cell["peer"],
        interval_seconds=interval,
        max_samples=MAX_SAMPLES,
        timeout_seconds=TIMEOUT_SECONDS,
        decision_permit=permit,
    )
    gates = (
        SealQualification(trial, cell["schedule"], decision_permit=permit)
        if control
        else None
    )
    try:
        result = await trial.run()
    finally:
        schedule_ok = await gates.finish() if gates is not None else True
    error_free, native_fields = native_report(result)
    print(
        json.dumps(
            {
                "ticket": args.ticket,
                **native_fields,
                "schedule_complete": schedule_ok,
            }
        ),
        file=sys.stderr if summary_to_stderr else sys.stdout,
    )
    return error_free and result.reward_valid and schedule_ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claim and run exactly one registered native seal trial. Never retries."
    )
    parser.add_argument("registration", type=Path)
    parser.add_argument("task", type=Path)
    parser.add_argument("trials", type=Path)
    parser.add_argument("ticket")
    parser.add_argument("--allow-named-seats", action="store_true")
    parser.add_argument(
        "--interval", type=float, help="world sampling in seconds for a named team"
    )
    args = parser.parse_args()
    try:
        ok = asyncio.run(run(args))
    except (FileExistsError, ValueError, Invalid) as error:
        parser.exit(2, f"run: {error}\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
