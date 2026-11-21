import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from ..committee import driver, schedule
from ..visibility import deepseek, spend
from . import protocol

_IMPORT = "examples.seatlab.coalitions.seat:"
_MODEL = f"deepseek/{deepseek.MODEL}"
_CELL_MAX_CALLS = 4


def _trial_config(directory: Path, task: Path, cell: protocol.Cell) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(task)},
        "trial_name": cell["cell_id"],
        "trials_dir": str(directory / "trials"),
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": [
            {
                "seat": role,
                "import_path": _IMPORT
                + (
                    "FixedCoalitionHelper"
                    if role in protocol.HELPERS
                    else "CoalitionSeat"
                ),
                "model_name": None if role in protocol.HELPERS else _MODEL,
                "kwargs": {"role": role, "run_id": cell["cell_id"]},
            }
            for role in schedule.ROLES
        ],
    }


def _attempt(cell: protocol.Cell) -> dict[str, Any]:
    study_max_calls = _CELL_MAX_CALLS * len(protocol.CELLS)
    hold = Decimal(spend._usd(spend._HOLD))
    return {
        "schema": "seatlab.coalition-attempt/v1",
        **cell,
        "trial_name": cell["cell_id"],
        "task_commit": protocol.TASK_COMMIT,
        "task_git_tree": schedule.TASK_TREE,
        "task_checksum": schedule.TASK_CHECKSUM,
        "task_flags": {
            "task_name": driver._TASK_NAME,
            "under_test": dict.fromkeys(schedule.ROLES, True),
            "verifier_enabled": True,
            "artifacts": ["/logs/world/events.jsonl", "/logs/world/state.json"],
        },
        "budget": {
            "model": _MODEL,
            "revision": deepseek.REVISION,
            "max_calls": _CELL_MAX_CALLS,
            "study_max_calls": study_max_calls,
            "reserve_usd_per_call": str(hold),
            "max_reserved_usd": str(_CELL_MAX_CALLS * hold),
            "study_max_reserved_usd": str(study_max_calls * hold),
            "request_max_bytes": deepseek._MAX_PAYLOAD_BYTES,
            "max_output_tokens_per_call": 512,
            "ledger_run_prefix": cell["cell_id"] + "-",
        },
    }


def _prepare_host(task: Path) -> dict[str, str]:
    if not all(os.environ.get(name) for name in driver._HOST_BINDINGS):
        raise driver._Rejected("host_binding_missing")
    deepseek.bind_host_key()
    try:
        spend.bind_host_spend_dir()
        with spend._connect() as db:
            if db.execute("SELECT COUNT(*) FROM budget").fetchone()[0] != 1:
                raise spend.SpendRefused("invalid_budget")
    except (OSError, spend.SpendRefused, sqlite3.Error):
        raise driver._Rejected("host_spend_unavailable") from None
    if any(name in os.environ for name in driver._HOST_BINDINGS):
        raise driver._Rejected("unbound_host_environment")
    current_python = Path(sys.executable)
    if sys.prefix == sys.base_prefix:
        raise driver._Rejected("native_fork_venv_required")
    _, python, env = driver._preflight(current_python.with_name("harbor"), task)
    if python.parent.resolve() != current_python.parent.resolve():
        raise driver._Rejected("native_fork_venv_required")
    return env


async def _native(config: dict[str, Any]) -> None:
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.seats import SeatsTrial
    from harbor.trial.trial import Trial

    from .seat import CoalitionSeat, Exchange, FixedCoalitionHelper

    async with asyncio.timeout(driver._TRIAL_TIMEOUT_SEC):
        trial = await Trial.create(TrialConfig.model_validate(config))
        if not isinstance(trial, SeatsTrial):
            raise driver._Rejected("native_seat_trial_required")
        if tuple(seat.name for seat in trial.seats) != schedule.ROLES:
            raise driver._Rejected("wrong_native_roster")
        exchange = Exchange(config["trial_name"])
        for seat in trial.seats:
            agent = seat.agent
            expected = (
                FixedCoalitionHelper if seat.name in protocol.HELPERS else CoalitionSeat
            )
            if (
                not isinstance(agent, (FixedCoalitionHelper, CoalitionSeat))
                or type(agent) is not expected
                or not seat.under_test
            ):
                raise driver._Rejected("wrong_native_roster")
            agent.bind_exchange(exchange)
        await trial.run()


def _launch(directory: Path, config: dict[str, Any], env: dict[str, str]) -> None:
    with TemporaryDirectory(prefix=".coalition-host-", dir=directory) as home:
        plugins = driver._DOCKER_DESKTOP_PLUGINS
        if plugins.is_dir():
            docker_config = Path(home) / ".docker"
            docker_config.mkdir(mode=0o700)
            (docker_config / "config.json").write_text(
                json.dumps({"cliPluginsExtraDirs": [str(plugins)]}), encoding="utf-8"
            )
        os.environ.clear()
        os.environ.update(env, HOME=home)
        with (
            (directory / "native.log").open("x", encoding="utf-8") as log,
            redirect_stdout(log),
            redirect_stderr(log),
        ):
            asyncio.run(_native(config))


def _read_outcome(config: dict[str, Any], outcome: dict[str, Any]) -> None:
    path = Path(config["trials_dir"]) / config["trial_name"] / "result.json"
    try:
        result = driver._json_object(path)
        if (
            result["task_name"] != driver._TASK_NAME
            or result["task_checksum"] != schedule.TASK_CHECKSUM
            or result["trial_name"] != config["trial_name"]
        ):
            return
        outcome["trial_id"] = str(UUID(result["id"]))
        outcome["verifier_finished"] = bool(result["verifier"]["finished_at"])
        seats = result["seat_results"]
        if (
            outcome["failure"] != "native_trial_incomplete"
            or not result["finished_at"]
            or not outcome["verifier_finished"]
            or result["exception_info"] is not None
            or result["reward_valid"] is not True
            or not isinstance(seats, list)
            or len(seats) != len(schedule.ROLES)
            or {seat["seat"] for seat in seats} != set(schedule.ROLES)
        ):
            return
        for seat in seats:
            record = seat["agent_result"]["metadata"]
            if (
                seat["exception_info"] is not None
                or record["status"] != "complete"
                or record["done"] is not True
                or record["run_id"] != config["trial_name"]
                or record["role"] != seat["seat"]
            ):
                return
        outcome.update(status="complete", failure=None)
    except (KeyError, OSError, TypeError, ValueError, RecursionError):
        return


def _run(out: Path, cell: protocol.Cell, task: Path) -> str | None:
    _, team = driver._helpers()
    task = driver._task_dir(task)
    root = team._root(out)
    if root.is_relative_to(task):
        raise driver._Rejected("private_output_invalid")
    root.mkdir(mode=0o700, exist_ok=True)
    team._check_path(root, 0o700, directory=True)
    cells = root / "cells"
    cells.mkdir(mode=0o700, exist_ok=True)
    team._check_path(cells, 0o700, directory=True)
    directory = cells / cell["cell_id"]
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise driver._Rejected("attempt_already_claimed_do_not_retry") from None
    config = _trial_config(directory, task, cell)
    outcome: dict[str, Any] = {
        "schema": "seatlab.coalition-launch/v1",
        "status": "failed",
        "failure": "private_attempt_failed",
        "trial_id": None,
        "verifier_finished": False,
    }
    try:
        team._seal(directory / "attempt.json", team._bytes(_attempt(cell)))
        team._seal(directory / "config.json", team._bytes(config))
        Path(config["trials_dir"]).mkdir(mode=0o700)
        env = _prepare_host(task)
        outcome["failure"] = "native_launch_failed"
        _launch(directory, config, env)
        outcome["failure"] = "native_trial_incomplete"
    except driver._Rejected as exc:
        outcome["failure"] = str(exc)
    except (TimeoutError, subprocess.TimeoutExpired):
        outcome["failure"] = "native_launch_timeout"
    except KeyboardInterrupt:
        outcome["failure"] = "native_launch_interrupted"
    except (OSError, RecursionError, TypeError, ValueError, deepseek.SeatUnavailable):
        outcome["failure"] = (
            "native_launch_failed"
            if outcome["failure"] == "native_launch_failed"
            else "private_attempt_failed"
        )
    finally:
        _read_outcome(config, outcome)
        team._seal(directory / "outcome.json", team._bytes(outcome))
    return outcome["failure"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Claim and run one native coalition cell."
    )
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cell", choices=tuple(protocol.CELLS), required=True)
    args = parser.parse_args(argv)
    mask = os.umask(0o077)
    try:
        failure = _run(args.out, protocol.CELLS[args.cell], args.task)
    except driver._Rejected as exc:
        failure = str(exc)
    except (OSError, ValueError, RecursionError):
        failure = "local_setup_failed"
    finally:
        os.umask(mask)
    if failure is not None:
        print(f"coalition: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
