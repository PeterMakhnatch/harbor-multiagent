import argparse
import json
import os
import runpy
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..live_answers import __main__ as live
from ..visibility import calendar_driver, calendar_packet, calendar_replica_source
from . import protocol as p
from . import team_source, team_views

MODULE = "examples.seatlab.calendar_notices"
SCHEMA = "seatlab.native-calendar-notices/v1"
BINDINGS = ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")
POLICIES = ("oracle", "wrong")

_Cell = p.Cell | team_views.Cell
_Tables = tuple[calendar_packet.Table, calendar_packet.Table]


def _require(ok: bool, category: str) -> None:
    if not ok:
        raise ValueError(category)


def _private_dir(path: Path) -> Path:
    calendar_driver._physical(path)
    calendar_driver._team_helpers()._check_path(path, 0o700, directory=True)
    _require(not path.is_relative_to(calendar_driver._REPO), "job_inside_checkout")
    return path


def _directory(job: Path, cell: _Cell) -> Path:
    return job / ("cells" if cell.control == "live" else "controls") / cell.run


def _schema(cell: _Cell) -> str:
    if isinstance(cell, team_views.JoinCell):
        return team_views.JOIN_SCHEMA
    if isinstance(cell, team_views.RoleCell):
        return team_views.ROLE_SCHEMA
    return team_views.SCHEMA if isinstance(cell, team_views.Cell) else SCHEMA


def _cell_from_attempt(attempt: dict[str, Any]) -> _Cell:
    schema = attempt["schema"]
    if schema == SCHEMA:
        cell_type = p.Cell
    elif schema == team_views.SCHEMA:
        cell_type = team_views.Cell
    elif schema == team_views.ROLE_SCHEMA:
        cell_type = team_views.RoleCell
    elif schema == team_views.JOIN_SCHEMA:
        cell_type = team_views.JoinCell
    else:
        raise ValueError("invalid_attempt_schema")
    return cell_type(attempt["world"], attempt["arm"], attempt["control"])


def _source(task: Path, cell: _Cell) -> tuple[dict[str, str], _Tables | None]:
    tables = None
    if isinstance(cell, team_views.Cell):
        identity, tables = team_source._checked(task, cell)
    else:
        identity = calendar_replica_source.verify(task, cell.index)
    return (
        {
            "task_sha256": identity.native_sha256,
            "semantics_sha256": identity.semantics_sha256,
        },
        tables,
    )


def _seat_kwargs(cell: _Cell, seat: str, tables: _Tables | None) -> dict[str, Any]:
    if isinstance(cell, team_views.Cell):
        if tables is None:
            raise ValueError("missing_private_tables")
        return team_source._seat_kwargs(tables, cell, seat)
    return {
        "world": cell.world,
        "arm": cell.arm,
        "seat": seat,
        "control": cell.control,
    }


def _config(
    directory: Path, task: Path, cell: _Cell, tables: _Tables | None
) -> dict[str, Any]:
    variant = team_views if isinstance(cell, team_views.Cell) else p
    model = {"model_name": variant.MODEL} if cell.control == "live" else {}
    return {
        "schema_version": 1,
        "trial_name": cell.run,
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
                "import_path": variant.AGENT_IMPORT,
                **model,
                "kwargs": _seat_kwargs(cell, seat, tables),
            }
            for seat in variant.SEATS
        ],
    }


def _attempt(
    cell: _Cell, source: dict[str, str] | None = None, config_sha256: str | None = None
) -> dict[str, Any]:
    return {
        "schema": _schema(cell),
        "world": cell.world,
        "arm": cell.arm,
        "control": cell.control,
        "run": cell.run,
        "source": source,
        "config_sha256": config_sha256,
    }


def _check_binding(
    directory: Path,
    cell: _Cell,
    task: Path,
    source: dict[str, str],
    tables: _Tables | None,
    attempt: dict[str, Any],
    config: bytes,
) -> None:
    _require(
        directory == _directory(directory.parent.parent, cell)
        and attempt == _attempt(cell, source, p.digest(config))
        and config == (p.wire(_config(directory, task, cell, tables)) + "\n").encode(),
        "attempt_binding_invalid",
    )


def _check_first(
    job: Path, cell: _Cell, task: Path, source: dict[str, str], tables: _Tables | None
) -> None:
    if cell.control != "live" or cell.arm == cell.first:
        return
    first = type(cell)(cell.world, cell.first, "live")
    directory = _private_dir(_directory(job, first))
    team = calendar_driver._team_helpers()
    outcome = live._sealed(directory / "outcome.json")
    _require(
        outcome.get("schema") == _schema(first)
        and outcome.get("run") == first.run
        and set(outcome) == {"schema", "run", "returncode", "error"},
        "first_arm_not_terminal",
    )
    _check_binding(
        directory,
        first,
        task,
        source,
        tables,
        live._sealed(directory / "attempt.json"),
        team._read_sealed(directory / "config.json"),
    )


def _bind_host(cell: _Cell) -> None:
    bindings = set(BINDINGS) if cell.control == "live" else set()
    allowed = calendar_driver._PUBLIC_ENV | bindings
    for name in os.environ.keys() - allowed:
        del os.environ[name]
    _require(
        set(os.environ) == allowed,
        "missing_host_environment",
    )
    if bindings:
        from ..visibility import deepseek, spend

        _require(bool(os.environ[BINDINGS[0]]), "host_key_missing")
        root = _private_dir(Path(os.environ[BINDINGS[1]]))
        calendar_driver._team_helpers()._check_path(
            root / "direct-flash.sqlite3", 0o600
        )
        deepseek.bind_host_key()
        spend.bind_host_spend_dir()
    _require(
        not any(key in os.environ for key in BINDINGS), "host_binding_not_isolated"
    )


def _host_trial(directory: Path, harbor: Path) -> int:
    try:
        for path in (directory.parent.parent, directory.parent, directory):
            _private_dir(path)
        _require(Path.cwd() == directory, "host_working_directory_mismatch")
        _require(not os.path.lexists(directory / "outcome.json"), "attempt_is_terminal")
        attempt = live._sealed(directory / "attempt.json")
        cell = _cell_from_attempt(attempt)
        _bind_host(cell)
        config_path = directory / "config.json"
        team = calendar_driver._team_helpers()
        config = team._read_sealed(config_path)
        task = Path(json.loads(config)["task"]["path"])
        source, tables = _source(task, cell)
        _check_binding(directory, cell, task, source, tables, attempt, config)
        _check_first(directory.parent.parent, cell, task, source, tables)
        (directory / "trials").mkdir(mode=0o700)
        harbor = calendar_driver._entrypoint(harbor)
        team.run_panel._check_native(harbor, dict(os.environ))
        _require(
            calendar_driver._stock(task, source["task_sha256"])
            == f"flotilla/{cell.task_name}",
            "native_task_binding_invalid",
        )
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        print("Native calendar notice host setup rejected.", file=sys.stderr)
        return 1
    sys.argv = [str(harbor), "trial", "start", "-c", str(config_path)]
    runpy.run_path(str(harbor), run_name="__main__")
    return 0


def _launch(directory: Path, harbor: Path, spend_dir: Path | None) -> int:
    env = calendar_driver._native_env(directory)
    env["PYTHONPATH"] = os.pathsep.join(
        (str(calendar_driver._REPO / "src"), env["PYTHONPATH"])
    )
    _require(set(env) == calendar_driver._PUBLIC_ENV, "unexpected_native_environment")
    if spend_dir is not None:
        key = os.environ.get(BINDINGS[0])
        if not key:
            raise ValueError("host_key_missing")
        env.update({BINDINGS[0]: key, BINDINGS[1]: str(_private_dir(spend_dir))})
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
            return child.wait(timeout=calendar_driver._TIMEOUT)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            live._stop(child)
            raise


def _run(
    job: Path, tasks: Path, harbor: Path, cell: _Cell, spend_dir: Path | None
) -> dict[str, Any]:
    job = _private_dir(job)
    directory = _directory(job, cell)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    _private_dir(directory.parent)
    directory.mkdir(mode=0o700)
    attempt = _attempt(cell)
    outcome: dict[str, Any] = {
        "schema": _schema(cell),
        "run": cell.run,
        "returncode": None,
        "error": "source_setup_failed",
    }
    try:
        tasks = calendar_driver._physical(tasks)
        _require(
            not job.is_relative_to(tasks) and not tasks.is_relative_to(job),
            "job_overlaps_tasks",
        )
        task = tasks / cell.task_name
        source, tables = _source(task, cell)
        attempt["source"] = source
        outcome["error"] = "native_config_failed"
        attempt["config_sha256"] = live._seal(
            directory / "config.json", _config(directory, task, cell, tables)
        )
        live._seal(directory / "attempt.json", attempt)
        outcome["error"] = "first_arm_binding_failed"
        _check_first(job, cell, task, source, tables)
        outcome["error"] = "native_launch_failed"
        code = _launch(directory, harbor, spend_dir)
        outcome["returncode"] = code
        outcome["error"] = "native_nonzero_exit" if code else None
    except subprocess.TimeoutExpired:
        outcome["error"] = "native_timeout"
    except KeyboardInterrupt:
        outcome["error"] = "native_interrupted"
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        print(f"Native calendar notice stopped: {outcome['error']}.", file=sys.stderr)
    finally:
        try:
            if not os.path.lexists(directory / "attempt.json"):
                live._seal(directory / "attempt.json", attempt)
        finally:
            live._seal(directory / "outcome.json", outcome)
    return outcome


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch one native calendar notice trial."
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    role_worlds = team_views.WORLDS
    role_arms = team_views.ROLE_ARMS
    join_worlds = team_views.JOIN_WORLDS
    join_arms = team_views.JOIN_ARMS
    for mode, cell_type, worlds, arms in (
        ("run", p.Cell, p.WORLDS, p.ARMS),
        ("control", p.Cell, p.WORLDS, p.ARMS),
        ("views-run", team_views.Cell, team_views.WORLDS, team_views.ARMS),
        ("views-control", team_views.Cell, team_views.WORLDS, team_views.ARMS),
        ("role-views-run", team_views.RoleCell, role_worlds, role_arms),
        ("role-views-control", team_views.RoleCell, role_worlds, role_arms),
        ("run-visible", team_views.JoinCell, join_worlds, join_arms),
        ("control-visible", team_views.JoinCell, join_worlds, join_arms),
    ):
        command = sub.add_parser(mode)
        command.set_defaults(cell_type=cell_type)
        command.add_argument("--job", type=Path, required=True)
        command.add_argument("--tasks", type=Path, required=True)
        command.add_argument("--world", choices=worlds, required=True)
        command.add_argument("--arm", choices=arms, required=True)
        command.add_argument(
            "--harbor", type=Path, default=Path(sys.executable).with_name("harbor")
        )
        if mode in ("run", "views-run", "role-views-run", "run-visible"):
            command.add_argument("--spend-dir", type=Path, required=True)
        else:
            command.add_argument("--policy", choices=POLICIES, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["_host_trial"]:
        return _host_trial(Path(argv[1]), Path(argv[2])) if len(argv) == 3 else 2
    args = _arguments(argv)
    try:
        cell = args.cell_type(args.world, args.arm, getattr(args, "policy", "live"))
        outcome = _run(
            args.job, args.tasks, args.harbor, cell, getattr(args, "spend_dir", None)
        )
    except FileExistsError:
        print(
            "Native calendar notice attempt already claimed. Do not retry.",
            file=sys.stderr,
        )
        return 1
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        RuntimeError,
        KeyboardInterrupt,
    ):
        print("Native calendar notice command rejected.", file=sys.stderr)
        return 1
    print(p.wire(outcome))
    return int(outcome["error"] is not None)


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
