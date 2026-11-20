"""Run one reserved two-seat Dispatch Handoff cell in native Harbor.

From the fork root:

PYTHONPATH=examples/seatlab:examples:. python -m examples.seatlab.visibility.native_pairs plan

The grid is fixed. An external scheduler owns cell order and Docker slots.
A stopped Codex subscription plan is historical only. It is not a fallback.
"""

import argparse
import asyncio
import logging
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, cast, override

import evidence
import native_model_agent
import run_panel
import seat_agent
import team_driver
import team_report
from capsule import encode_view, view_hash
from examples.seatlab.visibility import deepseek
from export_capsules import reconstructed_capsules

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

SEEDS = (3101, 3103, 3109, 3119)
REPETITIONS = range(3)
EPISODES = 4
DECLARED_MODEL = "deepseek/deepseek-flash"
_SERVED_ENGINES = frozenset({"deepseek-flash", "deepseek-v4.1-flash-1017"})
_ROLES = ("inventory", "dispatch")
_CONDITIONS = ("fresh", "silent")
_AGENT_NAME = "seatlab-direct-flash"
_AGENT_VERSION = "1.0.0"
_AGENT_IMPORT = "examples.seatlab.visibility.native_pairs:DirectFlashSeat"
_SEAT_SCHEMA = "seatlab.visibility-native-seat/v1"
_RECEIPT_SCHEMA = "seatlab.visibility-native-cell/v1"


class Cell(team_driver.Cell):
    repetition: int


def grid() -> list[Cell]:
    """Return the reserved cells in the order for the external scheduler."""
    return [
        {
            "cell_id": f"s{seed}-r{repetition}-{condition}",
            "seed": seed,
            "repetition": repetition,
            "condition": condition,
            "pairing": "MM",
        }
        for index, seed in enumerate(SEEDS)
        for repetition in REPETITIONS
        for condition in _CONDITIONS[:: 1 if (index + repetition) % 2 == 0 else -1]
    ]


def _prompt(role: str, cases: list[dict[str, Any]]) -> str:
    # The native view includes the treatment label for validation and binding.
    # The model sees only its scoped cases and identical role instructions.
    return (
        seat_agent._RULES[role]
        + " Output only a JSON object. Do not include explanations.\n"
        + encode_view({"role": role, "cases": cases}).decode("ascii")
    )


def _prompt_hash(prompt: str) -> str:
    return run_panel._sha256(prompt.encode("utf-8"))


def _decision_hash(role: str, rows: list[dict[str, Any]]) -> str:
    """Use the same row, revision and bay order as the native world dossier."""
    ordered = sorted(rows, key=lambda row: row["id"])
    if role == "inventory":
        ordered = [
            {
                "id": row["id"],
                "reports": [
                    {
                        "revision": report["revision"],
                        "available": sorted(report["available"]),
                    }
                    for report in sorted(
                        row["reports"], key=lambda report: report["revision"]
                    )
                ],
            }
            for row in ordered
        ]
    return view_hash(ordered)


def _ask_once(prompt: str, role: str, metadata: dict[str, Any]) -> str:
    metadata.update(stage="direct_pending", model_turns=1)
    try:
        turn = deepseek.turn(prompt, run_id=metadata["run_id"], role=role)
    except deepseek.SeatUnavailable as error:
        stage = error.category
        if not stage.isidentifier() or stage != stage.lower():
            stage = "direct_unavailable"
        native_model_agent._fail(metadata, stage)
    except Exception:
        native_model_agent._fail(metadata, "direct_failed")
    metadata["requested_model"] = deepseek.MODEL
    metadata["served_model"] = turn.served_model
    return turn.text


class DirectFlashSeat(BaseAgent):
    """Commit one attested direct response through a real scoped seat."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        if run_id not in {cell["cell_id"] for cell in grid()} or kwargs:
            raise ValueError("Direct flash seat requires a reserved cell id")
        if model_name not in (None, DECLARED_MODEL) or extra_env:
            raise ValueError("Direct flash seat does not accept overrides")
        super().__init__(logs_dir=logs_dir, model_name=DECLARED_MODEL, logger=logger)
        self._run_id = run_id
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        # This is a fixed host CLI route, not a LiteLLM provider alias.
        provider, name = model_name.split("/", 1)
        return provider, name

    @staticmethod
    @override
    def name() -> str:
        return _AGENT_NAME

    @override
    def version(self) -> str:
        return _AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Direct flash seat already started")
        self._started = True
        metadata: dict[str, Any] = {
            "schema": _SEAT_SCHEMA,
            "role": None,
            "stage": "waiting_view",
            "model_turns": 0,
            "requested_model": deepseek.MODEL,
            "run_id": None,
            "served_model": None,
            "view_sha256": None,
            "prompt_sha256": None,
            "decision_sha256": None,
        }
        context.metadata = metadata
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service not in _ROLES
        ):
            native_model_agent._fail(metadata, "invalid_seat")
        role = environment.service
        metadata["role"] = role
        metadata["run_id"] = self._run_id
        try:
            view = await seat_agent._view(environment)
            cases = seat_agent._checked_view(view, role)
            if len(cases) != EPISODES or view["condition"] not in _CONDITIONS:
                raise ValueError("Unregistered native scenario")
            digest = view_hash(view)
            prompt = _prompt(role, cases)
        except (OSError, ValueError, RecursionError, TypeError, seat_agent._Unmeasured):
            native_model_agent._fail(metadata, "invalid_view")
        metadata["prompt_sha256"] = _prompt_hash(prompt)
        response = await asyncio.to_thread(_ask_once, prompt, role, metadata)
        try:
            if not isinstance(response, str) or len(response) > 8192:
                raise ValueError("Invalid model response")
            submission = seat_agent._json_object(response, "subscription response")
            seat_agent._checked_submission(submission, role, EPISODES)
            metadata["decision_sha256"] = _decision_hash(role, submission["rows"])
        except (ValueError, RecursionError, seat_agent._Unmeasured):
            native_model_agent._fail(metadata, "invalid_model_decisions")
        metadata["stage"] = "submission_unacknowledged"
        try:
            status, body = await seat_agent._world_request(
                environment, "/submit", submission, view_sha256=digest
            )
            accepted = seat_agent._json_object(body, "world submission")
        except Exception:
            native_model_agent._fail(metadata, "submission_unacknowledged")
        if (
            status != 200
            or accepted.keys() != {"accepted"}
            or accepted["accepted"] is not True
        ):
            native_model_agent._fail(metadata, "submission_unacknowledged")
        metadata.update(stage="complete", view_sha256=digest)


def _trial_config(root: Path, cell: Cell) -> dict[str, Any]:
    config = team_driver._trial_config(root, cell)
    config["agent_timeout_multiplier"] = 2.0
    for participant in config["participants"]:
        participant.update(
            import_path=_AGENT_IMPORT,
            model_name=DECLARED_MODEL,
            kwargs={"run_id": cell["cell_id"]},
        )
    return config


def _host_python() -> str:
    return sys.executable


def _host_env(base: dict[str, str]) -> dict[str, str]:
    """Pass the native allowlist plus the two paid host values."""
    env = dict(base)
    spend = os.environ.get("SEATLAB_SPEND_DIR")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if spend:
        env["SEATLAB_SPEND_DIR"] = spend
    if key:
        env["DEEPSEEK_API_KEY"] = key
    env["PYTHONPATH"] = os.pathsep.join(
        (
            str(run_panel.PANEL_DIR),
            str(run_panel.EXAMPLES_DIR),
            str(run_panel.EXAMPLES_DIR.parent),
            str(run_panel.EXAMPLES_DIR.parent / "src"),
        )
    )
    return env


def _preflight(harbor: Path) -> tuple[Path, dict[str, str]]:
    if not harbor.is_absolute():
        raise ValueError("--harbor must be an absolute path")
    harbor = harbor.resolve(strict=True)
    env = _host_env(run_panel._native_env(None))
    env.pop("DEEPSEEK_API_KEY", None)
    run_panel._check_native(harbor, env)
    return harbor, env


def host_trial(config: Path, harbor: Path) -> int:
    """Bind the host key, then run Harbor in this same Python process."""
    deepseek.bind_host_key()
    os.environ.pop("DEEPSEEK_API_KEY", None)
    sys.argv = [str(harbor), "trial", "start", "-c", str(config)]
    runpy.run_path(str(harbor), run_name="__main__")
    return 0


def _launch(directory: Path, harbor: Path, env: dict[str, str]) -> int:
    # The reservation exists before the host child starts.
    (directory / "trials").mkdir(mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    child_env = _host_env(env)
    with os.fdopen(os.open(directory / "native.log", flags, 0o600), "wb") as log:
        return subprocess.run(
            [
                _host_python(),
                "-m",
                "examples.seatlab.visibility.native_pairs",
                "_host_trial",
                str(directory / "config.json"),
                str(harbor),
            ],
            cwd=directory,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=team_driver._TRIAL_TIMEOUT_SEC,
            umask=0o077,
        ).returncode


def _inspect(
    trial: Path, config: dict[str, Any], cell: Cell, row: dict[str, Any]
) -> None:
    """Attach native evidence. Only complete, bound model decisions are valid."""
    result_path = trial / "result.json"
    dossier_path = trial / "artifacts/logs/world/handoff.json"
    if not all(
        team_report._within_trial(path, trial) for path in (result_path, dossier_path)
    ):
        raise ValueError("Native outputs must remain inside the trial")
    native = team_report._read_object(result_path)
    team_report._check_config(native.get("config"), config)
    if (
        native.get("trial_name") != config["trial_name"]
        or native.get("trial_uri") != trial.as_uri()
        or native.get("task_id") != {"path": config["task"]["path"]}
    ):
        raise ValueError("Native result belongs to a different trial")
    row["trial_id"] = evidence._trial_id(native)
    row["task_name"] = native.get("task_name")
    records = evidence._by_role(native.get("seat_results"), "seat_results")
    for role, record in records.items():
        context = record.get("agent_result")
        row["seats"][role] = {
            "agent_info": record.get("agent_info"),
            "invocation": context.get("metadata")
            if isinstance(context, dict)
            else None,
        }
    verifier = native.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if type(reward) in (float, int) and 0 <= reward <= 1:
        row["native_score"] = reward
    if row["native_returncode"] != 0:
        raise ValueError("Native Harbor process did not succeed")
    receipt, world = evidence.read_trial_with_world(trial)
    score = receipt["score"]
    if (
        receipt["trial_id"] != row["trial_id"]
        or receipt["seed"] != cell["seed"]
        or receipt["condition"] != cell["condition"]
        or score["episodes"] != EPISODES
        or receipt["fingerprint"]
        != team_report._registered_fingerprint(cell["seed"], EPISODES)
    ):
        raise ValueError("Native world differs from the reserved scenario")
    provider, name = DECLARED_MODEL.split("/", 1)
    for role, capsule in reconstructed_capsules(world).items():
        seat = row["seats"][role]
        field = "bay" if role == "dispatch" else "reports"
        dossier_field = "chosen" if role == "dispatch" else field
        decisions = [
            {"id": case["id"], field: case[dossier_field]} for case in world["cases"]
        ]
        scoped_view = cast(dict[str, Any], capsule["view"])
        expected_identity = {
            "name": _AGENT_NAME,
            "version": _AGENT_VERSION,
            "model_info": {"name": name, "provider": provider},
        }
        invocation = seat["invocation"]
        served = (
            invocation.get("served_model") if isinstance(invocation, dict) else None
        )
        expected_invocation = {
            "schema": _SEAT_SCHEMA,
            "role": role,
            "stage": "complete",
            "model_turns": 1,
            "run_id": cell["cell_id"],
            "requested_model": deepseek.MODEL,
            "served_model": served,
            "view_sha256": capsule["sha256"],
            "prompt_sha256": _prompt_hash(_prompt(role, scoped_view["cases"])),
            "decision_sha256": _decision_hash(role, decisions),
        }
        if (
            seat["agent_info"] != expected_identity
            or capsule["basis"] != "bound"
            or not isinstance(served, str)
            or served.casefold() not in _SERVED_ENGINES
            or not run_panel._config_matches(invocation, expected_invocation)
        ):
            raise ValueError(
                "Native seat did not attest exactly one bound model decision"
            )
    row.update(
        outcome_valid=True,
        error=None,
        fingerprint=receipt["fingerprint"],
        actions=[
            {**grade, "choice": case["chosen"]}
            for grade, case in zip(score["rows"], world["cases"], strict=True)
        ],
    )


def _run(root: Path, cell: Cell, harbor: Path) -> bool:
    root = team_driver._root(root)
    root.mkdir(mode=0o700, exist_ok=True)
    team_driver._check_path(root, 0o700, directory=True)
    directory = team_driver._cell_dir(root, cell)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    team_driver._check_path(directory.parent, 0o700, directory=True)
    # A cell directory is an exclusive first-attempt claim.
    directory.mkdir(mode=0o700)
    config = _trial_config(root, cell)
    row: dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        **cell,
        "requested_model": deepseek.MODEL,
        "episodes": EPISODES,
        "reserved_model_turns": 2,
        "trial_name": config["trial_name"],
        "task_name": None,
        "trial_id": None,
        "native_returncode": None,
        "native_score": None,
        "outcome_valid": False,
        "error": "preparation_failed",
        "fingerprint": None,
        "actions": None,
        "seats": dict.fromkeys(_ROLES),
    }
    team_driver._seal(directory / "attempt.json", team_driver._bytes(row))
    try:
        sources = run_panel._snapshot("arrival")
        plan = run_panel._make_plan(
            list(SEEDS), list(_CONDITIONS), EPISODES, "arrival", {}
        )
        run_panel._prepare_cell(directory, plan, cell, sources)
        team_driver._seal(directory / "config.json", team_driver._bytes(config))
        row["error"] = "preflight_failed"
        harbor, env = _preflight(harbor)
        row["error"] = "native_launch_failed"
        row["native_returncode"] = _launch(directory, harbor, env)
        row["error"] = "native_evidence_invalid"
        _inspect(team_driver._trial_dir(root, cell), config, cell, row)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        subprocess.TimeoutExpired,
        deepseek.SeatUnavailable,
    ):
        pass  # Keep the reserved category, not private diagnostics.
    team_driver._seal(directory / "receipt.json", team_driver._bytes(row))
    return row["outcome_valid"] is True


def collect_cell(root: Path, cell: Cell) -> dict[str, Any]:
    """Read one existing attempt offline. Never create or reclaim a cell."""
    root = team_driver._root(root)
    directory = team_driver._cell_dir(root, cell)
    if not directory.is_dir():
        raise ValueError("attempt directory is missing; collect does not create one")
    receipt_path = directory / "receipt.json"
    if receipt_path.exists():
        receipt = team_report._read_object(receipt_path)
        receipt.setdefault("recovery", "existing_receipt")
        return receipt
    trial = team_driver._trial_dir(root, cell)
    if not (trial / "result.json").is_file():
        return {
            "schema": _RECEIPT_SCHEMA,
            "cell_id": cell["cell_id"],
            "outcome_valid": False,
            "recovery": "unresolved",
            "error": "native_result_missing",
        }
    row: dict[str, Any] = {
        "schema": _RECEIPT_SCHEMA,
        **cell,
        "outcome_valid": False,
        "recovery": "unresolved",
        "error": "native_evidence_invalid",
        "native_returncode": None,
        "seats": dict.fromkeys(_ROLES),
    }
    outcome = directory / "outcome.json"
    if outcome.exists():
        row["native_returncode"] = team_driver._outcome(outcome)["returncode"]
    config = team_report._read_object(directory / "config.json")
    try:
        _inspect(trial, config, cell, row)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return row
    row["recovery"] = "orphan_native_result"
    team_driver._seal(receipt_path, team_driver._bytes(row))
    return row


def main(argv: list[str] | None = None) -> int:
    # Read the process arguments once. A hidden host child has no parser choice.
    command_args = list(sys.argv[1:] if argv is None else argv)
    if command_args[:1] == ["_host_trial"]:
        if len(command_args) != 3:
            return 2
        return host_trial(Path(command_args[1]), Path(command_args[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "plan", help="print the reserved single-cell schedule; make no calls"
    )
    preflight = commands.add_parser(
        "preflight", help="check the local native Harbor route; no paid call"
    )
    preflight.add_argument("--harbor", type=Path, required=True)
    preflight.add_argument(
        "--native-only",
        action="store_true",
        help="check Harbor only; do not call the paid route",
    )
    run = commands.add_parser(
        "run", help="claim and run one reserved cell without retry"
    )
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--harbor", type=Path, required=True)
    cells = {cell["cell_id"]: cell for cell in grid()}
    run.add_argument("--cell", required=True, choices=cells)
    collect = commands.add_parser(
        "collect", help="read one existing attempt offline; never create a cell"
    )
    collect.add_argument("--out", type=Path, required=True)
    collect.add_argument("--cell", required=True, choices=cells)
    args = parser.parse_args(command_args)
    try:
        if args.command == "plan":
            print(
                run_panel._json(
                    {
                        "episodes": EPISODES,
                        "model": deepseek.MODEL,
                        "cells": list(cells.values()),
                    }
                ),
                end="",
            )
        elif args.command == "preflight":
            if not args.native_only:
                raise ValueError("paid preflight is owned by the direct adapter")
            _preflight(args.harbor)
        elif args.command == "collect":
            receipt = collect_cell(args.out, cells[args.cell])
            print(run_panel._json(receipt), end="")
            return 0 if receipt.get("outcome_valid") is True else 1
        else:
            return 0 if _run(args.out, cells[args.cell], args.harbor) else 1
    except FileExistsError:
        parser.exit(
            1,
            f"{parser.prog}: a cell or reserved output already exists; do not retry\n",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired, deepseek.SeatUnavailable):
        parser.exit(1, f"{parser.prog}: local setup or preflight failed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
