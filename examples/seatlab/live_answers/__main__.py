import argparse
import json
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..visibility import calendar_driver, calendar_replica_source, spend
from . import audit
from . import protocol as p

MODULE = "examples.seatlab.live_answers"
SCHEMA = "seatlab.native-calendar-answers/v1"
BINDINGS = ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")


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
        attempt["schema"] == SCHEMA
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
    if cell.arm == cell.first:
        return None
    first = p.Cell(cell.world, cell.first, cell.control)
    directory = _directory(job, first)
    audit.require(
        (directory / "outcome.json").is_file(), "enrolled_first_arm_not_terminal"
    )
    attempt = _sealed(directory / "attempt.json")
    audit.require(attempt.get("run") == first.run, "first_arm_binding")
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
        "schema": SCHEMA,
        "world": cell.world,
        "arm": cell.arm,
        "control": cell.control,
        "run": cell.run,
        "source": None,
        "config_sha256": None,
    }
    outcome: dict[str, Any] = {
        "schema": SCHEMA,
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
    return {
        "schema": SCHEMA,
        "world": cell.world,
        "arm": cell.arm,
        "control": cell.control,
        "final_receiver": cell.receiver,
        "peer": cell.peer,
        "first_arm": cell.first,
        "run": cell.run,
        "message_policy": p.POLICY,
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
            == (SCHEMA, cell.world, cell.arm, cell.control, cell.run),
            "attempt_binding",
        )
        row["source"] = attempt["source"]
        row["artifact_sha256"]["attempt"] = p.digest(
            audit.raw(directory / "attempt.json")
        )
        if (directory / "outcome.json").exists():
            outcome = _sealed(directory / "outcome.json")
            audit.require(
                (outcome["schema"], outcome["run"]) == (SCHEMA, cell.run),
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


def _report(
    job: Path, cells: tuple[p.Cell, ...], ledger: list[dict[str, Any]]
) -> dict[str, Any]:
    rows = [collect_cell(job, cell, ledger) for cell in cells]
    uuids = [row["trial_uuid"] for row in rows if row["trial_uuid"] is not None]
    audit.require(len(uuids) == len(set(uuids)), "duplicate_native_trial_uuid")
    results = {}
    for arm in p.ARMS:
        selected = [row for row in rows if row["arm"] == arm]
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
    for world in p.WORLDS:
        pair = {row["arm"]: row for row in rows if row["world"] == world}
        if set(pair) != set(p.ARMS):
            continue
        visible, withheld = (pair[arm] for arm in p.ARMS)
        worlds[world] = {
            "matched_source": visible["source"] is not None
            and visible["source"] == withheld["source"],
            "both_attempted": visible["attempted"] and withheld["attempted"],
            "visible_success": visible["success"],
            "withheld_success": withheld["success"],
        }
    return {"schema": SCHEMA, "counts": results, "worlds": worlds, "cells": rows}


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or read native live calendar answer cells."
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("run", "control", "read"):
        cmd = sub.add_parser(mode)
        cmd.add_argument("--job", type=Path, required=True)
        cmd.add_argument("--world", choices=p.WORLDS, required=mode != "read")
        cmd.add_argument("--arm", choices=p.ARMS, required=mode != "read")
        if mode != "control":
            cmd.add_argument("--spend-dir", type=Path)
        if mode != "read":
            cmd.add_argument("--tasks", type=Path, required=True)
            cmd.add_argument(
                "--harbor", type=Path, default=Path(sys.executable).with_name("harbor")
            )
        else:
            cmd.add_argument("--controls", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "read" and (args.world is None) != (args.arm is None):
        parser.error("provide both --world and --arm, or neither")
    if (
        args.mode == "run" or (args.mode == "read" and not args.controls)
    ) and args.spend_dir is None:
        parser.error("--spend-dir is required for enrolled cells")
    return args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_host_trial"]:
        if len(argv) != 3:
            return 2
        return _host_trial(Path(argv[1]), Path(argv[2]))
    args = _arguments(argv)
    control = args.mode == "control" or getattr(args, "controls", False)
    try:
        job = _private_dir(args.job)
        cells = (
            (p.Cell(args.world, args.arm, control),)
            if args.world
            else p.grids(control=control)
        )
        spend_dir = None if control else _private_dir(args.spend_dir)
        if args.mode != "read":
            _run(job, args.tasks, args.harbor, cells[0], spend_dir)
        ledger = (
            spend.report(spend_dir, run_prefix="nanswer-")["calls"] if spend_dir else []
        )
        report = _report(job, cells, ledger)
        print(json.dumps(report, sort_keys=True))
        return int(args.mode != "read" and not report["cells"][0]["protocol_complete"])
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        print(
            "Native answer command rejected. Preserve the first attempt and use read.",
            file=sys.stderr,
        )
        return 1


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
