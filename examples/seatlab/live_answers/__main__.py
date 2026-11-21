import argparse
import json
import os
import runpy
import signal
import subprocess
import sys
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any

from ..visibility import calendar_driver, calendar_replica_source, spend
from . import audit
from . import protocol as p

MODULE = "examples.seatlab.live_answers"
SCHEMA = "seatlab.native-calendar-answers/v1"
REPAIR_SCHEMA = "seatlab.native-calendar-answer-repair/v1"
REPAIR_POLICY = "directed-flotilla-messages; sender-report-treatment; same-final-prompt"
BINDINGS = ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")


def _schema(cell: p.Cell) -> str:
    return REPAIR_SCHEMA if cell.arm in p.REPAIR_ARMS else SCHEMA


def _team():
    return calendar_driver._team_helpers()


def _private_dir(path: Path) -> Path:
    calendar_driver._physical(path)
    _team()._check_path(path, 0o700, directory=True)
    audit.require(
        not path.is_relative_to(calendar_driver._REPO),
        "job_path_must_not_be_in_checkout",
    )
    return path


def _directory(job: Path, cell: p.Cell) -> Path:
    return job / ("controls" if cell.control else "cells") / cell.run


def _config(directory: Path, task: Path, cell: p.Cell) -> dict[str, Any]:
    suffix = p.digest(os.fsencode(directory))[:12]
    return {
        "schema_version": 1,
        "trial_name": f"{cell.run}-{suffix}",
        "trials_dir": str(directory / "trials"),
        "task": {"path": str(task)},
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        "participants": [
            {
                "seat": seat,
                "import_path": p.AGENT_IMPORT,
                **({} if cell.control else {"model_name": p.MODEL}),
                "kwargs": {
                    "world": cell.world,
                    "arm": cell.arm,
                    "seat": seat,
                    "control": cell.control,
                },
            }
            for seat in p.SEATS
        ],
    }


def _seal(path: Path, data: dict[str, Any]) -> str:
    payload = (p.wire(data) + "\n").encode()
    _team()._seal(path, payload)
    return p.digest(payload)


def _sealed(path: Path) -> dict[str, Any]:
    return json.loads(_team()._read_sealed(path))


def _source(task: Path, cell: p.Cell) -> dict[str, str]:
    verified = calendar_replica_source.verify(task, cell.index)
    return {
        "task_sha256": verified.native_sha256,
        "semantics_sha256": verified.semantics_sha256,
    }


def _host_trial(directory: Path, harbor: Path) -> int:
    for name in os.environ.keys() - (calendar_driver._PUBLIC_ENV | set(BINDINGS)):
        del os.environ[name]
    _private_dir(directory)
    attempt = _sealed(directory / "attempt.json")
    cell = p.Cell(attempt["world"], attempt["arm"], attempt["control"])
    if not cell.control:
        from ..visibility import deepseek

        audit.require(
            all(os.environ.get(name) for name in BINDINGS), "host_binding_missing"
        )
        deepseek.bind_host_key()
        spend.bind_host_spend_dir()
    audit.require(
        not any(name in os.environ for name in BINDINGS), "host_binding_not_isolated"
    )
    config_path = directory / "config.json"
    config_bytes = _team()._read_sealed(config_path)
    config = json.loads(config_bytes)
    task = Path(config["task"]["path"])
    audit.require(
        attempt["schema"] == _schema(cell)
        and attempt["run"] == cell.run
        and _directory(directory.parent.parent, cell) == directory
        and attempt["config_sha256"] == p.digest(config_bytes)
        and config == _config(directory, task, cell)
        and attempt["source"] == _source(task, cell),
        "host_trial_binding",
    )
    from harbor.models.task.task import Task

    stock = Task(task)
    audit.require(
        stock.name == f"flotilla/{cell.task_name}"
        and stock.checksum == attempt["source"]["task_sha256"]
        and not stock.has_steps
        and set(stock.config.agents) == set(p.SEATS)
        and all(seat.under_test for seat in stock.config.agents.values())
        and tuple(stock.config.artifacts)
        == ("/logs/world/events.jsonl", "/logs/world/messages.jsonl"),
        "native_task_binding",
    )
    sys.argv = [str(harbor), "trial", "start", "-c", str(config_path)]
    runpy.run_path(str(harbor), run_name="__main__")
    return 0


def _stop(child: subprocess.Popen) -> None:
    try:
        os.killpg(child.pid, signal.SIGTERM)
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
    except ProcessLookupError:
        pass


def _launch(
    directory: Path, harbor: Path, env: dict[str, str], spend_dir: Path | None
) -> int:
    if spend_dir is not None:
        key = os.environ.get("DEEPSEEK_API_KEY")
        audit.require(bool(key), "host_binding_missing")
        env = env | {"DEEPSEEK_API_KEY": key, "SEATLAB_SPEND_DIR": str(spend_dir)}
    command = [
        sys.executable,
        "-B",
        "-s",
        "-P",
        "-m",
        MODULE,
        "_host_trial",
        str(directory),
        str(harbor),
    ]
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with (
        os.fdopen(os.open(directory / "native.log", flags, 0o600), "wb") as log,
        subprocess.Popen(
            command,
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            umask=0o077,
        ) as child,
    ):
        try:
            return child.wait(timeout=1800)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            _stop(child)
            raise


def _previous(job: Path, cell: p.Cell) -> dict[str, str] | None:
    first_arm = cell.first
    if cell.arm == first_arm:
        return None
    first = p.Cell(cell.world, first_arm, cell.control)
    directory = _directory(job, first)
    audit.require(
        (directory / "outcome.json").is_file(), "enrolled_first_arm_not_terminal"
    )
    attempt = _sealed(directory / "attempt.json")
    outcome = _sealed(directory / "outcome.json")
    binding = (_schema(first), first.run)
    audit.require(
        (attempt.get("schema"), attempt.get("run")) == binding
        and (outcome.get("schema"), outcome.get("run")) == binding,
        "first_arm_binding",
    )
    return attempt.get("source")


def _run(
    job: Path, tasks: Path, harbor: Path, cell: p.Cell, spend_dir: Path | None
) -> None:
    job = _private_dir(job)
    tasks = calendar_driver._physical(tasks)
    audit.require(
        not job.is_relative_to(tasks) and not tasks.is_relative_to(job),
        "job_overlaps_tasks",
    )
    old_source = _previous(job, cell)
    directory = _directory(job, cell)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    _private_dir(directory.parent)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("attempt_already_claimed_do_not_retry") from None
    attempt: dict[str, Any] = {
        "schema": _schema(cell),
        "world": cell.world,
        "arm": cell.arm,
        "control": cell.control,
        "run": cell.run,
        "source": None,
        "config_sha256": None,
    }
    outcome: dict[str, Any] = {
        "schema": _schema(cell),
        "run": cell.run,
        "returncode": None,
        "error": "source_setup_failed",
    }
    try:
        task = tasks / cell.task_name
        attempt["source"] = _source(task, cell)
        if old_source is not None and old_source != attempt["source"]:
            raise ValueError("matched_task_changed")
        outcome["error"] = "native_config_failed"
        config = _config(directory, task, cell)
        attempt["config_sha256"] = _seal(directory / "config.json", config)
        _seal(directory / "attempt.json", attempt)
        (directory / "trials").mkdir(mode=0o700)
        outcome["error"] = "native_preflight_failed"
        harbor = calendar_driver._entrypoint(harbor)
        if spend_dir is not None:
            _private_dir(spend_dir)
        env = calendar_driver._native_env(directory)
        env["PYTHONPATH"] = os.pathsep.join(
            (str(calendar_driver._REPO / "src"), env["PYTHONPATH"])
        )
        _team().run_panel._check_native(harbor, env)
        outcome["error"] = "native_launch_failed"
        outcome["returncode"] = _launch(directory, harbor, env, spend_dir)
        outcome["error"] = "native_nonzero_exit" if outcome["returncode"] != 0 else None
    except subprocess.TimeoutExpired:
        outcome["error"] = "native_timeout"
    except KeyboardInterrupt:
        outcome["error"] = "native_interrupted"
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        pass
    finally:
        if not (directory / "attempt.json").exists():
            _seal(directory / "attempt.json", attempt)
        _seal(directory / "outcome.json", outcome)


def _empty(
    cell: p.Cell, attempted: bool, ledger: list[dict[str, Any]]
) -> dict[str, Any]:
    schema = _schema(cell)
    return {
        "schema": schema,
        "world": cell.world,
        "arm": cell.arm,
        "control": cell.control,
        "final_receiver": cell.receiver,
        "peer": cell.peer,
        "first_arm": cell.first,
        "run": cell.run,
        "message_policy": REPAIR_POLICY if schema == REPAIR_SCHEMA else p.POLICY,
        "attempted": attempted,
        "status": "incomplete_attempt" if attempted else "not_attempted",
        "launch": None,
        "trial_uuid": None,
        "source": None,
        "native_reward": None,
        "evidence_error": None,
        "native_reward_valid": False,
        "protocol_bound": False,
        "protocol_complete": False,
        "success": False,
        "question": None,
        "answer": None,
        "choice": None,
        "submitted": None,
        "submit_event": None,
        "answer_received_by_seat": False,
        "answer_at_final_prompt": "not_rendered",
        "final_prompt_answers": None,
        "messages": {},
        "turns": {},
        "seats": {},
        "artifact_sha256": {},
        "ledger_calls": [row for row in ledger if row["run"] == cell.run],
        **(
            dict.fromkeys(
                ("draft_matches_sender", "delivered_matches_sender", "target_nominated")
            )
            if schema == REPAIR_SCHEMA
            else {}
        ),
    }


def collect_cell(
    job: Path, cell: p.Cell, ledger: list[dict[str, Any]]
) -> dict[str, Any]:
    directory = _directory(job, cell)
    row = _empty(cell, directory.exists(), ledger)
    if not row["attempted"]:
        return row
    try:
        _private_dir(directory)
        attempt = _sealed(directory / "attempt.json")
        audit.require(
            (
                attempt["schema"],
                attempt["world"],
                attempt["arm"],
                attempt["control"],
                attempt["run"],
            )
            == (row["schema"], cell.world, cell.arm, cell.control, cell.run),
            "attempt_binding",
        )
        row["source"] = attempt["source"]
        row["artifact_sha256"]["attempt"] = p.digest(
            audit.raw(directory / "attempt.json")
        )
        if (directory / "outcome.json").exists():
            outcome = _sealed(directory / "outcome.json")
            audit.require(
                (outcome["schema"], outcome["run"]) == (row["schema"], cell.run),
                "outcome_binding",
            )
            row["launch"] = {key: outcome[key] for key in ("error", "returncode")}
            row["artifact_sha256"]["outcome"] = p.digest(
                audit.raw(directory / "outcome.json")
            )
            row["status"] = outcome["error"] or "missing_native_result"
        if attempt["config_sha256"] is None:
            return row
        config_bytes = _team()._read_sealed(directory / "config.json")
        audit.require(p.digest(config_bytes) == attempt["config_sha256"], "config_hash")
        row["artifact_sha256"]["config"] = attempt["config_sha256"]
        config = json.loads(config_bytes)
        audit.require(
            config == _config(directory, Path(config["task"]["path"]), cell),
            "config_binding",
        )
        trial = Path(config["trials_dir"]) / config["trial_name"]
        if not (trial / "result.json").is_file():
            return row
        audit.require(list(trial.parent.iterdir()) == [trial], "extra_trial")
        audit.inspect(
            trial, config, cell, attempt["source"]["task_sha256"], ledger, row
        )
        row["status"] = "complete" if row["protocol_complete"] else "protocol_failure"
    except audit.BindingError as exc:
        row.update(status="invalid_or_missing_evidence", evidence_error=str(exc))
    except (ValueError, OSError, KeyError, TypeError, IndexError, AttributeError):
        row["status"] = "invalid_or_missing_evidence"
        row["evidence_error"] = "missing_or_invalid_artifact"
    return row


def _verified_stock_reward(row: dict[str, Any]) -> bool:
    return (
        row["attempted"]
        and row["protocol_bound"]
        and row["native_reward_valid"]
        and row["native_reward"] in (0, 1)
    )


def _repair_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    attempted = sum(row["attempted"] for row in rows)
    verified = [row["native_reward"] for row in rows if _verified_stock_reward(row)]
    return {
        "enrolled": len(rows),
        "attempted": attempted,
        "unattempted": len(rows) - attempted,
        "verified_stock_wins": verified.count(1),
        "verified_stock_losses": verified.count(0),
        "missing_or_unverified_reward": attempted - len(verified),
        "protocol_complete": sum(row["protocol_complete"] for row in rows),
    }


def _repair_pair_eligible(row: dict[str, Any]) -> bool:
    if not (row["protocol_complete"] and _verified_stock_reward(row)):
        return False
    turns = row["turns"]
    if turns.keys() != set(p.PHASES):
        return False
    calls = row["ledger_calls"]
    if row["control"]:
        return not calls and all(
            turn[key] is None
            for turn in turns.values()
            for key in ("ledger", "ledger_role", "usage", "served_model")
        )
    return len(calls) == len(p.PHASES) and all(
        (call := turns[phase]["ledger"]) is not None
        and call["role"] == role
        and call["run"] == row["run"]
        and call["estimated_usd_high"] is not None
        and call in calls
        for phase, role in p.LEDGER_ROLES.items()
    )


def _paired_repair(
    worlds_enrolled: int, differences: list[int], *, control: bool
) -> dict[str, Any]:
    local_wins = differences.count(1)
    model_wins = differences.count(-1)
    probability = None
    if len(differences) == worlds_enrolled == len(p.WORLDS):
        discordant = local_wins + model_wins
        tail = sum(comb(discordant, k) for k in range(min(local_wins, model_wins) + 1))
        exact = min(Fraction(1), Fraction(2 * tail, 2**discordant))
        probability = {"numerator": exact.numerator, "denominator": exact.denominator}
    return {
        "control": control,
        "worlds_enrolled": worlds_enrolled,
        "matched": len(differences),
        "local_wins": local_wins,
        "model_wins": model_wins,
        "ties": differences.count(0),
        "two_sided_sign_p": probability,
    }


def _report(
    job: Path, cells: tuple[p.Cell, ...], ledger: list[dict[str, Any]]
) -> dict[str, Any]:
    rows = [collect_cell(job, cell, ledger) for cell in cells]
    uuids = [row["trial_uuid"] for row in rows if row["trial_uuid"] is not None]
    audit.require(len(uuids) == len(set(uuids)), "duplicate_native_trial_uuid")
    schema = rows[0]["schema"]
    repair = schema == REPAIR_SCHEMA
    if repair:
        audit.require(
            all(row["control"] == rows[0]["control"] for row in rows),
            "mixed_repair_controls",
        )
    arms = p.REPAIR_ARMS if repair else p.ARMS
    results = {}
    for arm in arms:
        selected = [row for row in rows if row["arm"] == arm]
        if repair:
            results[arm] = _repair_counts(selected)
            continue
        attempted = sum(row["attempted"] for row in selected)
        successes = sum(row["success"] for row in selected)
        results[arm] = {
            "enrolled": len(selected),
            "attempted": attempted,
            "first_attempt_successes": successes,
            "first_attempt_failures": attempted - successes,
            "not_attempted": len(selected) - attempted,
        }
    worlds = {}
    differences: list[int] = []
    for world in p.WORLDS:
        pair = {row["arm"]: row for row in rows if row["world"] == world}
        if pair.keys() != set(arms):
            continue
        left, right = (pair[arm] for arm in arms)
        matched: dict[str, Any] = {
            "matched_source": left["source"] is not None
            and left["source"] == right["source"],
            "both_attempted": left["attempted"] and right["attempted"],
        }
        if repair:
            matched["arms"] = {
                arm: {
                    field: row[field]
                    for field in ("native_reward", "protocol_complete", "success")
                }
                for arm, row in pair.items()
            }
            model, local = pair["model-report"], pair["local-report"]
            if (
                matched["matched_source"]
                and _repair_pair_eligible(model)
                and _repair_pair_eligible(local)
            ):
                difference = local["native_reward"] - model["native_reward"]
                differences.append(int(difference))
        else:
            matched["visible_success"] = left["success"]
            matched["withheld_success"] = right["success"]
        worlds[world] = matched
    report: dict[str, Any] = {
        "schema": schema,
        "counts": results,
        "worlds": worlds,
        "cells": rows,
    }
    if repair:
        report["paired"] = _paired_repair(
            len(worlds), differences, control=rows[0]["control"]
        )
    return report


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or read native live calendar answer cells."
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in (
        "run",
        "control",
        "read",
        "run-repair",
        "control-repair",
        "collect-repair",
    ):
        collect = mode in ("read", "collect-repair")
        control = mode in ("control", "control-repair")
        repair = mode.endswith("-repair")
        cmd = sub.add_parser(mode)
        cmd.set_defaults(collect=collect, controls=control, repair=repair)
        cmd.add_argument("--job", type=Path, required=True)
        cmd.add_argument("--world", choices=p.WORLDS, required=not collect)
        cmd.add_argument(
            "--arm", choices=p.REPAIR_ARMS if repair else p.ARMS, required=not collect
        )
        if not control:
            cmd.add_argument("--spend-dir", type=Path)
        if not collect:
            cmd.add_argument("--tasks", type=Path, required=True)
            cmd.add_argument(
                "--harbor", type=Path, default=Path(sys.executable).with_name("harbor")
            )
        else:
            cmd.add_argument("--controls", action="store_true")
    args = parser.parse_args(argv)
    if args.collect and (args.world is None) != (args.arm is None):
        parser.error("provide both --world and --arm, or neither")
    if not args.controls and args.spend_dir is None:
        parser.error("--spend-dir is required for enrolled cells")
    return args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_host_trial"]:
        if len(argv) != 3:
            return 2
        return _host_trial(Path(argv[1]), Path(argv[2]))
    args = _arguments(argv)
    try:
        job = _private_dir(args.job)
        grids = p.repair_grids if args.repair else p.grids
        cells = (
            (p.Cell(args.world, args.arm, args.controls),)
            if args.world
            else grids(control=args.controls)
        )
        spend_dir = None if args.controls else _private_dir(args.spend_dir)
        if not args.collect:
            _run(job, args.tasks, args.harbor, cells[0], spend_dir)
        ledger = []
        if spend_dir is not None:
            prefix = "narepair-" if args.repair else "nanswer-"
            runs = {cell.run for cell in cells}
            if len(cells) == 1:
                prefix = cells[0].run
            ledger = [
                row
                for row in spend.report(spend_dir, run_prefix=prefix)["calls"]
                if row["run"] in runs
            ]
        report = _report(job, cells, ledger)
        print(json.dumps(report, sort_keys=True))
        return int(not args.collect and not report["cells"][0]["protocol_complete"])
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        command = "collect-repair" if args.repair else "read"
        print(
            "Native answer command rejected. "
            f"Preserve the first attempt and use {command}.",
            file=sys.stderr,
        )
        return 1


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
