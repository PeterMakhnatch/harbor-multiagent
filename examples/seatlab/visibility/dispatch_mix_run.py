"""Run and report the registered native Dispatch Handoff mixed rosters."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Never, override

from .. import claude_replay, run_panel, team_driver
from . import dispatch_mix_plan as plan

_AGENT_IMPORT = "examples.seatlab.visibility.dispatch_mix_seat:MixedSeat"
_PLAN_SCHEMA = "seatlab.dispatch-mix-plan/v1"
_ATTEMPT_SCHEMA = "seatlab.dispatch-mix-attempt/v1"
_REPORT_SCHEMA = "seatlab.dispatch-mix-report/v1"
_SCOPE = "Native Dispatch Handoff mixed-roster trials on 4 registered seeds."
_BAD_EVIDENCE = (OSError, ValueError, KeyError, TypeError, OverflowError, RuntimeError)


def config(root: Path, cell: plan.Cell) -> dict[str, Any]:
    if (
        cell not in plan.grid()
        or type(cell["seed"]) is not int
        or type(cell["repetition"]) is not int
    ):
        raise ValueError("cell is not registered for the mixed roster")
    base = cell.copy()
    base["pairing"] = "MM"
    registered = team_driver._trial_config(root, base)
    for participant in registered["participants"]:
        participant.update(
            import_path=_AGENT_IMPORT,
            model_name=plan.model_for(cell, participant["seat"]),
            kwargs={"run_id": cell["cell_id"]},
        )
    return registered


def _attempt(cell: plan.Cell, config_bytes: bytes) -> bytes:
    return team_driver._bytes(
        {
            "schema": _ATTEMPT_SCHEMA,
            **cell,
            "episodes": plan.EPISODES,
            "reserved_model_turns": len(plan.ROLES),
            "config_sha256": run_panel._sha256(config_bytes),
        }
    )


def _claim(root: Path, cell: plan.Cell) -> Path:
    root.mkdir(mode=0o700, exist_ok=True)
    team_driver._check_path(root, 0o700, directory=True)
    directory = team_driver._cell_dir(root, cell)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    team_driver._check_path(directory.parent, 0o700, directory=True)
    directory.mkdir(mode=0o700)
    team_driver._check_path(directory, 0o700, directory=True)
    return directory


def _prepare(directory: Path, cell: plan.Cell) -> None:
    sources = run_panel._snapshot("arrival")
    panel = run_panel._make_plan(
        list(plan.SEEDS), ["fresh", "reordered"], plan.EPISODES, "arrival", {}
    )
    run_panel._prepare_cell(directory, panel, cell, sources)


def _launch(directory: Path, harbor: Path) -> int | None:
    from . import native_pairs

    try:
        harbor, env = native_pairs._preflight(harbor)
        if claude_replay._preflight(claude_replay._invoke) != "verified":
            return None
        return native_pairs._launch(directory, harbor, env)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def run_cell(root: Path, cell: plan.Cell, harbor: Path) -> bool:
    """Reserve one attempt, then run and validate the native result once."""
    try:
        root = team_driver._root(root)
        config_bytes = team_driver._bytes(config(root, cell))
        directory = _claim(root, cell)
        team_driver._seal(directory / "attempt.json", _attempt(cell, config_bytes))
        team_driver._seal(directory / "config.json", config_bytes)
        _prepare(directory, cell)
        returncode = _launch(directory, harbor)
        team_driver._seal(
            directory / "outcome.json",
            team_driver._bytes(
                {
                    "returncode": returncode,
                    "launch_error": (
                        None if returncode is not None else "native_launch_failed"
                    ),
                }
            ),
        )
        return returncode == 0 and bool(_collect_cell(root, cell)["status"] == "valid")
    except _BAD_EVIDENCE:
        return False


def _empty(cell: plan.Cell, status: str) -> dict[str, Any]:
    return {
        **cell,
        "status": status,
        "native_returncode": None,
        "native_score": None,
        "inventory_exact": None,
        "actions": None,
        "fingerprint": None,
        "seats": dict.fromkeys(plan.ROLES),
    }


def _check_trial_paths(root: Path, cell: plan.Cell) -> None:
    trial = team_driver._trial_dir(root, cell)
    if not os.path.lexists(trial.parent):
        return
    team_driver._check_path(trial.parent, 0o700, directory=True)
    if any(entry != trial for entry in trial.parent.iterdir()):
        raise ValueError("unexpected native trial path")
    if os.path.lexists(trial):
        team_driver._check_path(trial, 0o700, directory=True, native_mount=True)


def _collect_cell(root: Path, cell: plan.Cell) -> dict[str, Any]:
    directory = team_driver._cell_dir(root, cell)
    if not os.path.lexists(directory):
        return _empty(cell, "missing")
    try:
        team_driver._check_path(directory, 0o700, directory=True)
        attempt = directory / "attempt.json"
        if not os.path.lexists(attempt):
            status = "invalid_evidence" if any(directory.iterdir()) else "incomplete"
            return _empty(cell, status)
        expected = config(root, cell)
        config_bytes = team_driver._bytes(expected)
        if team_driver._read_sealed(attempt) != _attempt(cell, config_bytes):
            return _empty(cell, "invalid_evidence")
        path = directory / "config.json"
        outcome = directory / "outcome.json"
        log = directory / "native.log"
        if not os.path.lexists(path):
            invalid = any(
                os.path.lexists(entry) for entry in (outcome, log, directory / "trials")
            )
            return _empty(cell, "invalid_evidence" if invalid else "incomplete")
        if team_driver._read_sealed(path) != config_bytes:
            return _empty(cell, "invalid_evidence")
        if os.path.lexists(log):
            team_driver._check_path(log, 0o600)
        _check_trial_paths(root, cell)
        returncode = (
            team_driver._outcome(outcome)["returncode"]
            if os.path.lexists(outcome)
            else None
        )
        from .dispatch_mix_evidence import inspect_cell

        return inspect_cell(root, cell, expected, returncode)
    except _BAD_EVIDENCE:
        return _empty(cell, "invalid_evidence")


def report(root: Path, spend_dir: Path | None = None) -> dict[str, Any]:
    """Read every registered cell. Do not modify the study or launch a trial."""
    from . import dispatch_mix_summary

    root = team_driver._root(root)
    if os.path.lexists(root):
        team_driver._check_path(root, 0o700, directory=True)
        if os.path.lexists(root / "cells"):
            team_driver._check_path(root / "cells", 0o700, directory=True)
    rows = [_collect_cell(root, cell) for cell in plan.grid()]
    meter = None
    if spend_dir is not None:
        from .dispatch_mix_evidence import reconcile

        meter = reconcile(rows, spend_dir)
    return {
        "schema": _REPORT_SCHEMA,
        "scope": _SCOPE,
        "cells": rows,
        "meter": meter,
        "outcomes": dispatch_mix_summary.summarize(rows),
    }


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        self.exit(2, "Invalid mixed-roster arguments.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="dispatch_mix_run", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--index", type=int, required=True)
    run.add_argument("--harbor", type=Path, required=True)
    collect = commands.add_parser("report")
    collect.add_argument("--root", type=Path, required=True)
    collect.add_argument("--spend-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            document = {
                "schema": _PLAN_SCHEMA,
                "episodes": plan.EPISODES,
                "cells": plan.grid(),
            }
        elif args.command == "run":
            cells = plan.grid()
            if not 0 <= args.index < len(cells) or not args.harbor.is_absolute():
                parser.error("invalid index or Harbor path")
            if not run_cell(args.root, cells[args.index], args.harbor):
                print(
                    "Mixed-roster cell did not complete. Do not retry.", file=sys.stderr
                )
                return 1
            return 0
        else:
            document = report(args.root, args.spend_dir)
        print(run_panel._json(document), end="")
        return 0
    except Exception:
        print("Mixed-roster command failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
