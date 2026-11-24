import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any

from harbor.models.trial.config import TrialConfig
from harbor.trial.seats import SeatsTrial
from harbor.trial.trial import Trial

from ..native_study import (
    native,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ..shift_board.collect import _environment
from .collect import load_registration
from .gates import HELD, Qualification
from .lifecycle import terminal_state
from .observe import MAX_INTERVAL_SEC, MIN_INTERVAL_SEC, attach
from .register import (
    CONTROL,
    QUESTION,
    ROLES,
    TASK,
    control_team,
    timeout_question,
    trusted_json,
)
from .timeout_contract import (
    AGENT_TIMEOUT_MULTIPLIER,
    AGENT_TIMEOUT_SECONDS,
    NATIVE_DEADLINE,
    TIMEOUTS,
    UNOBSERVED,
)
from .timeout_gates import Deadline

CONTROL_INTERVAL = 0.4
MAX_SAMPLES = 32
TIMEOUT_SECONDS = 7.0


def control_arm(label: str) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    if label in TIMEOUTS:
        return timeout_question()[1][label], TIMEOUTS[label]
    arms = trusted_json(QUESTION)["arms"]
    require(label in arms, "this launcher only drives the published scripted schedules")
    return arms[label], None


def inputs(
    args: argparse.Namespace,
) -> tuple[TrialConfig, dict[str, Any], bool, float, str, tuple[str, ...] | None]:
    study = load_registration(args.registration)
    harbor = native()
    task = args.task.resolve(strict=True)
    root = args.trials.resolve(strict=True)
    require(
        root.is_dir()
        and not root.is_relative_to(task)
        and not task.is_relative_to(root),
        "use an existing trials directory separate from the task",
    )
    require(
        task.name == TASK.split("/")[1]
        and task_identity(harbor, task, TASK) == study["task"],
        "local task name, digest or checksum differs from the registration",
    )
    files = task_files_digest(task, study["task"])
    require(
        files == study["task_files"],
        "full local task tree differs from the registration",
    )
    matched = [r for r in study["cells"] if r["ticket"] == args.ticket]
    require(len(matched) == 1, "ticket not registered")
    cell = matched[0]
    team = study["teams"][cell["team"]]
    is_control = all(p.get("import_path") == CONTROL for p in team)
    timeouts: tuple[str, ...] | None = None
    if is_control:
        label = cell["team"]
        arm, timeouts = control_arm(label)
        expected = {p["seat"]: p["kwargs"] for p in control_team(arm)}
        require(
            {p["seat"]: p["kwargs"] for p in team} == expected
            and cell["target"] == "planner"
            and cell["observe"] == (label not in {"off", *UNOBSERVED}),
            "control roster or observation schedule differs",
        )
        require(
            timeouts is None or cell["seed"] in arm["seeds"],
            "deadline arm seed differs from its frozen schedule",
        )
        require(args.interval is None, "the registered controls have a fixed interval")
    else:
        require(
            all("name" in p for p in team) and args.allow_named_seats,
            "named seats can spend provider credit; pass --allow-named-seats for an authorized roster",
        )
    interval = (
        args.interval
        if args.interval is not None
        else (CONTROL_INTERVAL if is_control else 10.0)
    )
    require(
        math.isfinite(interval) and MIN_INTERVAL_SEC <= interval <= MAX_INTERVAL_SEC,
        "invalid probe interval",
    )
    request = trial_request(harbor, task, root, args.ticket, team, _environment(cell))
    if timeouts is not None:
        request["agent_timeout_multiplier"] = AGENT_TIMEOUT_MULTIPLIER
    config = TrialConfig.model_validate(request)
    require(
        config.timeout_multiplier == 1.0
        and config.verifier_timeout_multiplier is None
        and config.agent_timeout_multiplier
        == (AGENT_TIMEOUT_MULTIPLIER if timeouts is not None else None),
        "native deadline binding differs from the registered schedule",
    )
    return config, cell, is_control, interval, files, timeouts


def classify(result: Any) -> tuple[list[str], list[str]]:
    seats = {r.seat: r for r in result.seat_results or []}
    actual: list[str] = []
    unexpected: list[str] = [f"{role}:missing" for role in ROLES if role not in seats]
    unexpected += [f"{seat}:unknown" for seat in seats if seat not in ROLES]
    for role in (role for role in ROLES if role in seats):
        info = seats[role].exception_info
        if info is not None and info.exception_type != NATIVE_DEADLINE:
            unexpected.append(f"{role}:{info.exception_type}")
            continue
        if info is not None:
            actual.append(role)
        disposition = seats[role].execution_disposition
        if disposition != ("returned" if info is None else "stop_requested"):
            unexpected.append(f"{role}:disposition:{disposition}")
    trial = result.exception_info
    if trial is None or trial.exception_type != NATIVE_DEADLINE:
        unexpected.append(f"trial:{trial.exception_type if trial else None}")
    return actual, unexpected


def native_report(result: Any) -> tuple[bool, dict[str, Any]]:
    seats = result.seat_results or []
    error_free = result.exception_info is None and all(
        r.exception_info is None for r in seats
    )
    terminals = {
        r.seat: terminal_state(
            r.execution_disposition, r.exception_info is not None, legacy=False
        )
        for r in seats
    }
    return error_free, {
        "native_error_free": error_free,
        "seat_dispositions": {r.seat: r.execution_disposition for r in seats},
        "seat_terminals": terminals,
        "reward_valid": result.reward_valid,
    }


def attempt(
    config: TrialConfig,
    trial: SeatsTrial,
    cell: dict[str, Any],
    files: str,
    interval: float,
    deadline: bool,
) -> dict[str, Any]:
    record = {
        "schema": "harbor.seat-cut.attempt/v1",
        "ticket": config.trial_name,
        "trial_id": str(trial.id),
        "team": cell["team"],
        "target": cell["target"],
        "peer": cell["peer"],
        "observe": cell["observe"],
        "task_files": files,
        "interval_seconds": interval if cell["observe"] else None,
        "max_samples": MAX_SAMPLES if cell["observe"] else 0,
        "timeout_seconds": TIMEOUT_SECONDS if cell["observe"] else None,
    }
    if not deadline:
        return record
    require(
        trial.agent_timeout_sec == AGENT_TIMEOUT_SECONDS,
        "native agent deadline is not the registered 30 seconds",
    )
    return {
        **record,
        "schema": "harbor.seat-cut.attempt/v2",
        "agent_timeout_multiplier": AGENT_TIMEOUT_MULTIPLIER,
        "agent_timeout_seconds": AGENT_TIMEOUT_SECONDS,
    }


def outcome(
    ticket: str, result: Any, schedule_ok: bool, timeouts: tuple[str, ...] | None
) -> bool:
    error_free, native_fields = native_report(result)
    report: dict[str, Any] = {
        "ticket": ticket,
        **native_fields,
        "schedule_complete": schedule_ok,
    }
    if timeouts is None:
        print(json.dumps(report))
        return error_free and result.reward_valid and schedule_ok
    actual, unexpected = classify(result)
    expected = not error_free and actual == list(timeouts) and not unexpected
    report |= {
        "expected_timeouts": list(timeouts),
        "actual_timeouts": actual,
        "unexpected_errors": unexpected,
        "expected_timeout": expected,
    }
    print(json.dumps(report))
    return expected and result.reward_valid and schedule_ok


async def run(args: argparse.Namespace) -> bool:
    config, cell, is_control, interval, files, timeouts = inputs(args)
    directory = config.trials_dir / config.trial_name
    directory.mkdir(mode=0o700, exist_ok=False)
    trial = await Trial.create(config)
    if not isinstance(trial, SeatsTrial):
        raise TypeError("checkpoint registration did not create a native SeatsTrial")
    write_exclusive(
        directory / "seat-cut-attempt.json",
        attempt(config, trial, cell, files, interval, timeouts is not None),
        0o600,
    )
    if cell["observe"]:
        attach(
            trial,
            target=cell["target"],
            peer=cell["peer"],
            interval_seconds=interval,
            max_samples=MAX_SAMPLES,
            timeout_seconds=TIMEOUT_SECONDS,
        )
    gates: Qualification | None = None
    if timeouts is not None:
        gates = Deadline(trial, cell["team"])
    elif is_control and cell["team"] in HELD:
        gates = Qualification(trial, cell["team"])
    try:
        result = await trial.run()
    finally:
        schedule_ok = await gates.finish() if gates is not None else True
    return outcome(args.ticket, result, schedule_ok, timeouts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claim and run exactly one registered native trial. Never retries."
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
    except (FileExistsError, ValueError) as error:
        parser.exit(2, f"run: {error}\n")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
