"""Plan, check, or launch one first-attempt native Crucible committee cell."""

import argparse
import hashlib
import json
import os
import runpy
import stat
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any

from . import schedule

_MODULE = "examples.seatlab.committee.driver"
_SEATLAB = Path(__file__).resolve().parents[1]
_REPO = _SEATLAB.parents[1]
_TASK_STEM = "hidden_profile_unshared"
_TASK_NAME = "crucible/epistemic-crucible-hidden_profile_unshared"
_MODEL = "deepseek/deepseek-flash"
_IMPORT = "examples.seatlab.committee.seat:CommitteeSeat"
_PREFIX_IMPORT = "examples.seatlab.committee.prefix_seat:PrefixSeat"
_SEAT_SCHEMAS = {
    _IMPORT: "seatlab.committee-seat/v1",
    _PREFIX_IMPORT: "seatlab.prefix-seat/v1",
}
_HARBOR_VERSION = "0.26.0"
# Allow time for the stock environment build, seat execution, and verifier.
_TRIAL_TIMEOUT_SEC = 1800
_DOCKER_DESKTOP_PLUGINS = Path(
    "/Applications/Docker.app/Contents/Resources/cli-plugins"
)
_PUBLIC_ENV = frozenset(
    {
        "PATH",
        "DOCKER_HOST",
        "COMPOSE_BAKE",
        "COMPOSE_DISABLE_ENV_FILE",
        "HOME",
        "HARBOR_TELEMETRY",
        "LITELLM_LOCAL_MODEL_COST_MAP",
        "PYTHON_DOTENV_DISABLED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
        "PYTHONSAFEPATH",
        "PYTHONPATH",
    }
)
_HOST_BINDINGS = ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")


class _Rejected(ValueError):
    """An internal, fixed category safe to print without details."""


def _helpers() -> tuple[ModuleType, ModuleType]:
    # run_panel uses a bare sibling import when loaded as a module.
    sys.path.insert(0, str(_SEATLAB))
    try:
        from .. import run_panel, team_driver
    finally:
        sys.path.pop(0)
    return run_panel, team_driver


def _task_dir(path: Path) -> Path:
    path = path.expanduser()
    try:
        if path.name != _TASK_STEM or not stat.S_ISDIR(path.lstat().st_mode):
            raise _Rejected("stock_task_required")
        return path.resolve(strict=True)
    except OSError:
        raise _Rejected("stock_task_required") from None


def _git_object(kind: bytes, content: bytes) -> bytes:
    header = kind + b" " + str(len(content)).encode("ascii") + b"\0"
    return hashlib.sha1(header + content, usedforsecurity=False).digest()


def _git_tree(directory: Path) -> bytes:
    """Hash every entry as Git would, without consulting an index or filters."""
    entries: list[tuple[bytes, bytes]] = []
    for entry in directory.iterdir():
        name = os.fsencode(entry.name)
        mode = entry.lstat().st_mode
        if stat.S_ISDIR(mode):
            key, tag, digest = name + b"/", b"40000", _git_tree(entry)
        elif stat.S_ISREG(mode):
            key = name
            tag = b"100755" if mode & stat.S_IXUSR else b"100644"
            digest = _git_object(b"blob", entry.read_bytes())
        else:
            raise _Rejected("stock_task_tree_mismatch")
        entries.append((key, tag + b" " + name + b"\0" + digest))
    return _git_object(b"tree", b"".join(entry for _, entry in sorted(entries)))


def _check_task_tree(task: Path) -> None:
    if _git_tree(_task_dir(task)).hex() != schedule.TASK_TREE:
        raise _Rejected("stock_task_tree_mismatch")


def _check_runtime_task(task: Path) -> None:
    # This runs in the same installed venv as the selected Harbor entry point.
    if version("harbor") != _HARBOR_VERSION:
        raise _Rejected("harbor_preflight_failed")
    from harbor.models.task.task import Task

    from .seat import CommitteeSeat

    if CommitteeSeat.name() != "seatlab-committee":
        raise _Rejected("committee_adapter_unavailable")

    stock = Task(task)
    seats = stock.config.agents
    if (
        stock.name != _TASK_NAME
        or seats is None
        or set(seats) != set(schedule.ROLES)
        or any(not seat.under_test for seat in seats.values())
        or stock.checksum != schedule.TASK_CHECKSUM
    ):
        raise _Rejected("stock_task_runtime_mismatch")


def _installed_entrypoint(path: Path) -> tuple[Path, Path]:
    if not path.is_absolute():
        raise ValueError("absolute Harbor path required")
    harbor = path.resolve(strict=True)
    if (
        harbor.name != "harbor"
        or not harbor.is_file()
        or not os.access(harbor, os.X_OK)
    ):
        raise ValueError("installed Harbor entry point required")
    with harbor.open("rb") as entry:
        shebang = entry.readline(4096).rstrip(b"\r\n")
    if not shebang.startswith(b"#!/"):
        raise ValueError("absolute venv Python shebang required")
    python = Path(os.fsdecode(shebang[2:]))
    if (
        python.parent.resolve() != harbor.parent
        or not python.name.startswith("python")
        or not python.is_file()
        or not os.access(python, os.X_OK)
    ):
        raise ValueError("the Harbor entry point must use its own venv Python")
    venv = (harbor.parent.parent / "pyvenv.cfg").read_text().lower().splitlines()
    if not any(
        line.partition("=")[0].strip() == "include-system-site-packages"
        and line.partition("=")[2].strip() == "false"
        for line in venv
    ):
        raise ValueError("the Harbor venv must exclude system site packages")
    # Do not resolve the Python symlink: that loses its venv.
    return harbor, python


def _native_env(resolved: dict[str, str]) -> dict[str, str]:
    # Only use the local socket and executable search path from the host setup.
    env = {key: resolved[key] for key in ("PATH", "DOCKER_HOST") if key in resolved}
    env.update(
        HOME="/var/empty",
        COMPOSE_BAKE="false",
        COMPOSE_DISABLE_ENV_FILE="1",
        HARBOR_TELEMETRY="0",
        LITELLM_LOCAL_MODEL_COST_MAP="true",
        PYTHON_DOTENV_DISABLED="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHONSAFEPATH="1",
        PYTHONPATH=str(_REPO),
    )
    return env


def _host_command(python: Path, *args: str) -> list[str]:
    return [str(python), "-B", "-s", "-P", "-m", _MODULE, *args]


def _preflight(harbor: Path, task: Path) -> tuple[Path, Path, dict[str, str]]:
    _check_task_tree(task)
    panel, _ = _helpers()
    try:
        harbor, python = _installed_entrypoint(harbor)
        env = _native_env(panel._native_env(None))
        panel._check_native(harbor, env)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise _Rejected("harbor_preflight_failed") from None
    try:
        panel._capture(_host_command(python, "_host_preflight", str(task)), env)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise _Rejected("stock_task_runtime_mismatch") from None
    return harbor, python, env


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return value


def _host_trial(config: Path, harbor: Path) -> int:
    # Harbor's Docker subprocesses inherit os.environ. Bind both host values
    # before importing anything from Harbor, and never put them in the config.
    allowed = _PUBLIC_ENV.union(_HOST_BINDINGS)
    for key in os.environ.keys() - allowed:
        del os.environ[key]
    if not all(os.environ.get(name) for name in _HOST_BINDINGS):
        return 1
    from ..visibility import deepseek, spend

    deepseek.bind_host_key()
    spend.bind_host_spend_dir()
    if any(name in os.environ for name in _HOST_BINDINGS):
        return 1
    task = Path(_json_object(config)["task"]["path"])
    _check_task_tree(task)
    _check_runtime_task(task)
    argv = sys.argv
    try:
        sys.argv = [str(harbor), "trial", "start", "-c", str(config)]
        runpy.run_path(str(harbor), run_name="__main__")
    finally:
        sys.argv = argv
    return 0


def _launch(directory: Path, harbor: Path, python: Path, env: dict[str, str]) -> int:
    child_env = {key: value for key, value in env.items() if key in _PUBLIC_ENV}
    for name in _HOST_BINDINGS:
        value = os.environ.get(name)
        if value is None or value == "":
            raise _Rejected("host_binding_missing")
        child_env[name] = value
    with TemporaryDirectory(
        prefix=".committee-host-", dir=directory.parent.parent
    ) as home:
        child_env["HOME"] = home
        # Docker Desktop installs plugins outside the CLI's default system paths.
        # Give Compose its plugin path without copying the user's Docker config.
        if _DOCKER_DESKTOP_PLUGINS.is_dir():
            docker_config = Path(home) / ".docker"
            docker_config.mkdir(mode=0o700)
            (docker_config / "config.json").write_text(
                json.dumps({"cliPluginsExtraDirs": [str(_DOCKER_DESKTOP_PLUGINS)]})
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with os.fdopen(os.open(directory / "native.log", flags, 0o600), "wb") as log:
            os.fchmod(log.fileno(), 0o600)
            return subprocess.run(
                _host_command(
                    python, "_host_trial", str(directory / "config.json"), str(harbor)
                ),
                cwd=directory,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=_TRIAL_TIMEOUT_SEC,
                umask=0o077,
            ).returncode


def _trial_config(
    directory: Path, task: Path, cell: schedule.Cell, *, agent_import: str = _IMPORT
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(task)},
        "trial_name": "committee-" + cell["cell_id"],
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
                "import_path": agent_import,
                "model_name": _MODEL,
                "kwargs": {
                    "role": role,
                    "run_id": cell["cell_id"],
                    "arm": cell["arm"],
                    "map_index": cell["map_index"],
                },
            }
            for role in schedule.ROLES
        ],
    }


def _completed(config: dict[str, Any]) -> bool:
    """Recognize a returned native trial; the offline collector validates evidence."""
    trial = Path(config["trials_dir"]) / config["trial_name"]
    try:
        result = _json_object(trial / "result.json")
        seats = result["seat_results"]
        rewards = result["verifier_result"]["rewards"]
        reward = rewards["reward"]
        if (
            result["task_name"] != _TASK_NAME
            or result["task_checksum"] != schedule.TASK_CHECKSUM
            or result["trial_name"] != config["trial_name"]
            or not result["finished_at"]
            or result["exception_info"] is not None
            or result["reward_valid"] is not True
            or type(reward) not in (int, float)
            or reward not in (0, 1)
            or not isinstance(seats, list)
            or len(seats) != len(schedule.ROLES)
        ):
            return False
        expected = {
            p["seat"]: (p["kwargs"], _SEAT_SCHEMAS[p["import_path"]])
            for p in config["participants"]
        }
        for seat in seats:
            binding, schema = expected.pop(seat["seat"])
            record = seat["agent_result"]["metadata"]
            if (
                seat["under_test"] is not True
                or seat["exception_info"] is not None
                or record["schema"] != schema
                or record["status"] != "complete"
                or any(record[k] != v for k, v in binding.items())
            ):
                return False
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return False
    return not expected


def _run(
    out: Path,
    cell: schedule.Cell,
    task: Path,
    harbor: Path,
    *,
    agent_import: str = _IMPORT,
    checkpoint: tuple[str, str] | None = None,
) -> str | None:
    _, team = _helpers()
    task = _task_dir(task)
    root = team._root(out)
    if root.is_relative_to(task):
        raise _Rejected("private_output_invalid")
    root.mkdir(mode=0o700, exist_ok=True)
    team._check_path(root, 0o700, directory=True)
    directory = root / "cells" / cell["cell_id"]
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    team._check_path(directory.parent, 0o700, directory=True)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise _Rejected("attempt_already_claimed_do_not_retry") from None

    config = _trial_config(directory, task, cell, agent_import=agent_import)
    attempt = {
        "schema": "seatlab.committee-attempt/v1",
        **cell,
        "trial_name": config["trial_name"],
        "task_git_tree": schedule.TASK_TREE,
    }
    if checkpoint is not None:
        attempt["packet_sha256"], attempt["prefix_sha256"] = checkpoint
    outcome: dict[str, Any] = {
        "schema": "seatlab.committee-launch/v1",
        "returncode": None,
        "error": "private_attempt_failed",
    }
    try:
        team._seal(directory / "attempt.json", team._bytes(attempt))
        team._seal(directory / "config.json", team._bytes(config))
        Path(config["trials_dir"]).mkdir(mode=0o700)
        harbor, python, env = _preflight(harbor, task)
        outcome["error"] = "native_launch_failed"
        outcome["returncode"] = _launch(directory, harbor, python, env)
        if outcome["returncode"] != 0:
            outcome["error"] = "native_nonzero_exit"
        elif not _completed(config):
            outcome["error"] = "native_trial_incomplete"
        else:
            outcome["error"] = None
    except _Rejected as exc:
        outcome["error"] = str(exc)
    except subprocess.TimeoutExpired:
        outcome["error"] = "native_launch_timeout"
    except OSError:
        pass  # The fixed stage category hides host diagnostics.
    finally:
        team._seal(directory / "outcome.json", team._bytes(outcome))
    return outcome["error"]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["_host_trial"]:
        return _host_trial(Path(args[1]), Path(args[2])) if len(args) == 3 else 2
    if args[:1] == ["_host_preflight"]:
        if len(args) != 2:
            return 2
        _check_runtime_task(Path(args[1]))
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser(
        "plan", help="print the registered schedule without running it"
    )
    studies = {
        "sharing": ("seatlab.committee-plan/v1", schedule.grid()),
        "tally": ("seatlab.committee-tally-plan/v1", schedule.tally_grid()),
        "recommendation": (
            "seatlab.committee-recommendation-plan/v1",
            schedule.recommendation_grid(),
        ),
    }
    plan.add_argument("--study", choices=studies, default="sharing")
    preflight = commands.add_parser(
        "preflight", help="check stock task and installed Harbor"
    )
    run = commands.add_parser("run", help="claim and launch one cell without retry")
    for command in (preflight, run):
        command.add_argument("--harbor", type=Path, required=True)
        command.add_argument("--task", type=Path, required=True)
    cells = {
        cell["cell_id"]: cell
        for _, registered in studies.values()
        for cell in registered
    }
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--cell", choices=cells, required=True)
    parsed = parser.parse_args(args)
    error = None
    try:
        if parsed.command == "plan":
            print(
                json.dumps(
                    {
                        "schema": studies[parsed.study][0],
                        "task_git_tree": schedule.TASK_TREE,
                        "task_checksum": schedule.TASK_CHECKSUM,
                        "roles": schedule.ROLES,
                        "model": _MODEL,
                        "cells": studies[parsed.study][1],
                    },
                    indent=2,
                )
            )
        elif parsed.command == "preflight":
            _preflight(parsed.harbor, _task_dir(parsed.task))
        else:
            error = _run(parsed.out, cells[parsed.cell], parsed.task, parsed.harbor)
    except _Rejected as exc:
        error = str(exc)
    except (OSError, ValueError, RecursionError):
        error = "local_setup_failed"
    if error is not None:
        print(f"committee: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
