"""Reserve and launch one stock Harbor calendar trial through the external scheduler."""

import argparse
import hashlib
import json
import os
import runpy
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID
from warnings import catch_warnings

from . import calendar_packet, calendar_protocol

_MODULE = "examples.seatlab.visibility.calendar_driver"
_AGENT = "examples.seatlab.visibility.calendar_seat:CalendarSeat"
_MODEL = "deepseek/deepseek-flash"
_SEATS = ("p1", "p2")
_ARTIFACTS = ("/logs/world/events.jsonl", "/logs/world/messages.jsonl")
_ATTEMPT = "seatlab.calendar-attempt/v1"
_OUTCOME = "seatlab.calendar-launch/v1"
_TIMEOUT = 1800
_SEATLAB = Path(__file__).resolve().parents[1]
_REPO = _SEATLAB.parents[1]
_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_PLUGINS = Path("/Applications/Docker.app/Contents/Resources/cli-plugins")
_BINDINGS = ("DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR")
_PUBLIC_ENV = frozenset(
    (
        "PATH",
        "HOME",
        "TMPDIR",
        "DOCKER_HOST",
        "COMPOSE_BAKE",
        "COMPOSE_DISABLE_ENV_FILE",
        "HARBOR_TELEMETRY",
        "LITELLM_LOCAL_MODEL_COST_MAP",
        "PYTHONPATH",
        "PYTHON_DOTENV_DISABLED",
        "PYTHONDONTWRITEBYTECODE",
    )
)


class _Rejected(ValueError):
    """A fixed category that may be written outside private logs."""


def _team_helpers():
    # These existing SeatLab helpers use bare sibling imports.
    if str(_SEATLAB) not in sys.path:
        sys.path.insert(0, str(_SEATLAB))
    from .. import team_driver

    return team_driver


def _physical(path: Path) -> Path:
    if not path.is_absolute() or path.resolve() != path:
        raise _Rejected("physical_absolute_path_required")
    return path


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes) -> dict[str, Any]:
    value = json.loads(data)
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return value


def _object(path: Path) -> dict[str, Any]:
    if path.resolve() != path or not stat.S_ISREG(path.lstat().st_mode):
        raise _Rejected("native_evidence_path_invalid")
    return _json(path.read_bytes())


def _stock(task: Path, checksum: str) -> str:
    from harbor.models.task.task import Task

    _physical(task)
    calendar_packet.require_native_task_checksum(task, checksum)
    with catch_warnings(action="ignore", category=DeprecationWarning):
        stock = Task(task)
        if stock.checksum != checksum:
            raise _Rejected("stock_task_checksum_mismatch")
    seats = stock.config.agents
    if (
        set(seats) != set(_SEATS)
        or not all(seat.under_test for seat in seats.values())
        or stock.has_steps
        or tuple(stock.config.artifacts) != _ARTIFACTS
    ):
        raise _Rejected("stock_task_contract_mismatch")
    return stock.name


def _entrypoint(path: Path) -> Path:
    harbor = _physical(path)
    venv = harbor.parent.parent
    if (
        harbor.name != "harbor"
        or harbor.parent.name != "bin"
        or not harbor.is_file()
        or not os.access(harbor, os.X_OK)
        or venv != Path(sys.prefix).resolve()
        or sys.prefix == sys.base_prefix
    ):
        raise _Rejected("use_the_selected_harbor_venv")
    with harbor.open("rb") as entry:
        shebang = entry.readline(4096).rstrip(b"\r\n")
    # Keep the interpreter's venv path, not the target of its symlink.
    if shebang != b"#!" + os.fsencode(sys.executable):
        raise _Rejected("use_the_selected_harbor_venv")
    settings = (venv / "pyvenv.cfg").read_text().lower().splitlines()
    if not any(
        line.replace(" ", "") == "include-system-site-packages=false"
        for line in settings
    ):
        raise _Rejected("use_the_selected_harbor_venv")
    return harbor


def _native_env(directory: Path) -> dict[str, str]:
    host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    if not host.startswith("unix:///") or any(c in host for c in "\r\n\0%?#"):
        raise _Rejected("local_docker_socket_required")
    socket = Path(host.removeprefix("unix://")).resolve(strict=True)
    if (
        not stat.S_ISSOCK(socket.stat().st_mode)
        or shutil.which("docker", path=_PATH) is None
    ):
        raise _Rejected("local_docker_socket_required")
    home = directory / "home"
    home.mkdir(mode=0o700)
    tmp = directory / "tmp"
    tmp.mkdir(mode=0o700)
    if (_PLUGINS / "docker-compose").is_file():
        docker_config = home / ".docker"
        docker_config.mkdir(mode=0o700)
        team = _team_helpers()
        team._seal(
            docker_config / "config.json",
            team._bytes({"cliPluginsExtraDirs": [str(_PLUGINS)]}),
        )
    return {
        "PATH": _PATH,
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "DOCKER_HOST": f"unix://{socket}",
        "COMPOSE_BAKE": "false",
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "HARBOR_TELEMETRY": "0",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
        "PYTHONPATH": os.pathsep.join(map(str, (_REPO, _SEATLAB, _SEATLAB.parent))),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _trial_config(
    directory: Path,
    task: Path,
    block: calendar_protocol.Block,
    arm: str,
    seeds: tuple[tuple[int, ...], tuple[int, ...]],
    *,
    control: bool,
) -> dict[str, Any]:
    run = f"{block.id}-{arm}"
    # Harbor uses trial_name as part of the Docker Compose project name.
    trial_name = f"calendar-control-{run}" if control else f"calendar-{run}"
    return {
        "schema_version": 1,
        "task": {"path": str(task)},
        "trial_name": trial_name,
        "trials_dir": str(directory / "trials"),
        "environment": {
            "type": "docker",
            "force_build": True,
            "delete": True,
            "stream": False,
        },
        "verifier": {"disable": False},
        # The unchanged task already declares both world logs. Do not add duplicates.
        "participants": [
            {
                "seat": seat,
                "import_path": _AGENT,
                **({} if control else {"model_name": _MODEL}),
                "kwargs": {
                    "world": block.world,
                    "direction": block.direction,
                    "repetition": block.repetition,
                    "arm": arm,
                    "seat": seat,
                    "own_starts": list(seed),
                    "control": control,
                },
            }
            for seat, seed in zip(_SEATS, seeds, strict=True)
        ],
    }


def _seed_hashes(study: Path, block: calendar_protocol.Block) -> dict[str, str]:
    team = _team_helpers()
    root = study / "seeds" / block.id
    for directory in (root.parent, root):
        team._check_path(directory, 0o700, directory=True)
    hashes = {}
    for seat in _SEATS:
        path = root / f"{seat}.json"
        team._check_path(path, 0o600)
        hashes[seat] = _digest(path.read_bytes())
    return hashes


def _check_previous_arm(
    directory: Path, block: calendar_protocol.Block, arm: str, hashes: dict[str, str]
) -> None:
    first, second = block.arms
    if arm == second:
        team = _team_helpers()
        prior_run = f"{block.id}-{first}"
        prior = directory.parent / prior_run
        team._check_path(prior, 0o700, directory=True)
        attempt = _json(team._read_sealed(prior / "attempt.json"))
        outcome = _json(team._read_sealed(prior / "outcome.json"))
        if (
            attempt.get("schema") != _ATTEMPT
            or attempt.get("run") != prior_run
            or attempt.get("seed_sha256") != hashes
            or outcome.get("schema") != _OUTCOME
            or outcome.get("run") != prior_run
            or outcome.get("returncode") != 0
            or outcome.get("status") not in {"complete", "invalid_model"}
            or outcome.get("error") is not None
        ):
            raise _Rejected("first_arm_not_confirmed_with_same_seeds")


def _host_trial(directory: Path, harbor: Path) -> int:
    # Do not import Harbor until the direct bindings have left os.environ.
    for name in os.environ.keys() - (_PUBLIC_ENV | set(_BINDINGS)):
        del os.environ[name]
    team = _team_helpers()
    _physical(directory)
    team._check_path(directory, 0o700, directory=True)
    attempt = _json(team._read_sealed(directory / "attempt.json"))
    config_path = directory / "config.json"
    config_data = team._read_sealed(config_path)
    if (
        attempt["schema"] != _ATTEMPT
        or _digest(config_data) != attempt["config_sha256"]
    ):
        raise _Rejected("sealed_config_mismatch")
    mode = attempt["mode"]
    if mode == "run":
        from . import deepseek, spend

        if not all(os.environ.get(name) for name in _BINDINGS):
            raise _Rejected("host_binding_missing")
        deepseek.bind_host_key()
        spend.bind_host_spend_dir()
    elif mode != "control":
        raise _Rejected("invalid_launch_mode")
    if any(name in os.environ for name in _BINDINGS):
        raise _Rejected("host_binding_not_isolated")

    config = _json(config_data)
    block = next(
        block for block in calendar_protocol.blocks() if block.id == attempt["block"]
    )
    arm = attempt["arm"]
    run = f"{block.id}-{arm}"
    worlds = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)
    world = worlds[block.world]
    task = Path(config["task"]["path"])
    control = mode == "control"
    if (
        arm not in block.arms
        or attempt["run"] != run
        or directory.name != run
        or directory.parent.name != ("controls" if control else "cells")
        or attempt["world"] != world.index
        or attempt["sender"] != block.sender
        or attempt["receiver"] != block.receiver
        or attempt["packet_sha256"] != calendar_packet.PACKET_SHA256
        or world.task_checksum != attempt["task_checksum"]
        or task.name != f"schedule-{world.index:03d}-talk"
    ):
        raise _Rejected("stock_task_binding_mismatch")
    seeds: tuple[tuple[int, ...], tuple[int, ...]] = ((), ())
    if not control:
        study = directory.parent.parent
        if _seed_hashes(study, block) != attempt["seed_sha256"]:
            raise _Rejected("seed_pair_changed")
        seed_dir = study / "seeds" / block.id
        p1, p2 = (
            calendar_protocol.decode_list(
                json.dumps(
                    {"starts": _object(seed_dir / f"{seat}.json")["value"]},
                    separators=(",", ":"),
                )
            )
            for seat in _SEATS
        )
        seeds = (p1, p2)
    expected = _trial_config(directory, task, block, arm, seeds, control=control)
    if config != expected or attempt["trial_name"] != config["trial_name"]:
        raise _Rejected("sealed_actor_binding_mismatch")
    _stock(task, world.task_checksum)
    argv = sys.argv
    try:
        sys.argv = [str(harbor), "trial", "start", "-c", str(config_path)]
        runpy.run_path(str(harbor), run_name="__main__")
    finally:
        sys.argv = argv
    return 0


def _launch(
    directory: Path, harbor: Path, env: dict[str, str], spend_dir: Path | None
) -> int:
    child_env = {name: env[name] for name in _PUBLIC_ENV if name in env}
    if spend_dir is not None:
        key = os.environ.get(_BINDINGS[0])
        if key is None or key == "":
            raise _Rejected("host_binding_missing")
        child_env.update(DEEPSEEK_API_KEY=key, SEATLAB_SPEND_DIR=str(spend_dir))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with (
        os.fdopen(os.open(directory / "native.log", flags, 0o600), "wb") as log,
        subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-s",
                "-P",
                "-m",
                _MODULE,
                "_host_trial",
                str(directory),
                str(harbor),
            ],
            cwd=directory,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            umask=0o077,
        ) as child,
    ):
        try:
            return child.wait(timeout=_TIMEOUT)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise


def _inspect(config: dict[str, Any], attempt: dict[str, Any], task_name: str) -> str:
    from . import calendar_seat

    group = Path(config["trials_dir"])
    trial = group / config["trial_name"]
    if list(group.iterdir()) != [trial] or trial.resolve() != trial:
        raise _Rejected("native_trial_directory_invalid")
    result = _object(trial / "result.json")
    UUID(result["id"])
    reward = result["verifier_result"]["rewards"]["reward"]
    actual_config = result["config"]
    participants = actual_config["participants"]
    if (
        result["trial_name"] != attempt["trial_name"]
        or result["task_name"] != task_name
        or result["task_checksum"] != attempt["task_checksum"]
        or not result["finished_at"]
        or result["exception_info"] is not None
        or result["reward_valid"] is not True
        or type(reward) not in (int, float)
        or reward not in (0, 1)
        or actual_config["task"]["path"] != config["task"]["path"]
        or actual_config["trials_dir"] != config["trials_dir"]
        or actual_config["environment"]["type"] != "docker"
        or actual_config["verifier"]["disable"] is not False
        or len(participants) != len(_SEATS)
    ):
        raise _Rejected("native_trial_binding_invalid")
    for expected in config["participants"]:
        (actual,) = (p for p in participants if p["seat"] == expected["seat"])
        if any(
            actual.get(key) != expected.get(key)
            for key in ("import_path", "model_name", "kwargs")
        ):
            raise _Rejected("native_roster_mismatch")

    verifier = trial / "verifier"
    score = _object(verifier / "score.json")
    frozen = _object(verifier / "world-state.json")
    if (
        _object(verifier / "reward.json")["reward"] != reward
        or score["score"] != reward
        or score["error"] is not None
        or len(frozen["submissions"]) != score["n_submissions"]
        or score["n_messages"] != 2
    ):
        raise _Rejected("native_verifier_evidence_invalid")
    for source in _ARTIFACTS:
        artifact = trial / "artifacts" / source.removeprefix("/")
        if (
            artifact.resolve() != artifact
            or not artifact.is_file()
            or not artifact.stat().st_size
        ):
            raise _Rejected("native_artifact_missing")
    _object(trial / "lock.json")
    _object(trial / "config.json")

    stages = {}
    for seat in result["seat_results"]:
        name = seat["seat"]
        info = seat["agent_info"]
        meta = seat["agent_result"]["metadata"]["calendar"]
        if (
            name not in _SEATS
            or name in stages
            or seat["under_test"] is not True
            or seat["exception_info"] is not None
            or (info["name"], info["version"])
            != (calendar_seat.AGENT_NAME, calendar_seat.AGENT_VERSION)
            or meta["schema"] != calendar_seat.SEAT_SCHEMA
            or meta["control"] is not (attempt["mode"] == "control")
            or meta["run"] != attempt["run"]
            or meta["seat"] != name
        ):
            raise _Rejected("native_actor_binding_invalid")
        stages[name] = meta["stage"]
        if meta["stage"] != "peer_model_invalid":
            receipt_name = (
                calendar_seat.CONTROL_FILE
                if meta["control"]
                else calendar_seat.TURN_FILE
            )
            _object(trial / "seats" / name / "agent" / receipt_name)
    sender, receiver = attempt["sender"], attempt["receiver"]
    pair = (stages[sender], stages[receiver])
    if (
        pair == ("complete", "complete")
        and score["n_submissions"] == 1
        and score["submitted_by"] == receiver
    ):
        if attempt["mode"] == "control" and reward != 1:
            raise _Rejected("control_reward_invalid")
        return "complete"
    if (
        pair in {("model_invalid", "peer_model_invalid"), ("complete", "model_invalid")}
        and attempt["mode"] == "run"
        and reward == 0
        and score["n_submissions"] == 0
    ):
        return "invalid_model"
    raise _Rejected("native_actor_outcome_invalid")


def _run(
    *,
    tasks: Path,
    harbor: Path,
    study: Path,
    spend_dir: Path | None,
    block: calendar_protocol.Block,
    arm: str,
) -> dict[str, Any]:
    team = _team_helpers()
    from ..team_driver import _root

    study = _root(_physical(study))
    tasks = _physical(tasks)
    if not tasks.is_dir() or study.is_relative_to(tasks):
        raise _Rejected("unsafe_task_or_study_directory")
    study.mkdir(mode=0o700, exist_ok=True)
    team._check_path(study, 0o700, directory=True)
    control = spend_dir is None
    run = f"{block.id}-{arm}"
    parent = study / ("controls" if control else "cells")
    if parent.is_relative_to(tasks):
        raise _Rejected("unsafe_task_or_study_directory")
    parent.mkdir(mode=0o700, exist_ok=True)
    team._check_path(parent, 0o700, directory=True)
    directory = parent / run
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise _Rejected("attempt_already_claimed_do_not_retry") from None

    attempt: dict[str, Any] = {
        "schema": _ATTEMPT,
        "mode": "control" if control else "run",
        "run": run,
        "block": block.id,
        "world": block.world,
        "arm": arm,
        "sender": block.sender,
        "receiver": block.receiver,
        "trial_name": None,
        "packet_sha256": calendar_packet.PACKET_SHA256,
        "task_checksum": None,
        "seed_sha256": None,
        "config_sha256": None,
    }
    outcome: dict[str, Any] = {
        "schema": _OUTCOME,
        "run": run,
        "returncode": None,
        "status": "failed",
        "error": "native_preparation_failed",
    }
    attempt_path = directory / "attempt.json"
    try:
        worlds = calendar_packet.load(
            calendar_packet.PACKET, calendar_packet.PACKET_SHA256
        )
        world = worlds[block.world]
        attempt["task_checksum"] = world.task_checksum
        task = tasks / f"schedule-{world.index:03d}-talk"
        task_name = _stock(task, world.task_checksum)
        seeds: tuple[tuple[int, ...], tuple[int, ...]] = ((), ())
        if spend_dir is not None:
            from . import calendar_turns

            spend_dir = _physical(spend_dir)
            if spend_dir.is_relative_to(tasks):
                raise _Rejected("unsafe_spend_directory")
            team._check_path(spend_dir, 0o700, directory=True)
            before = _seed_hashes(study, block)
            seeds = calendar_turns.load_seed_pair(study, block, world, spend_dir)
            if _seed_hashes(study, block) != before:
                raise _Rejected("seed_pair_changed")
            _check_previous_arm(directory, block, arm, before)
            attempt["seed_sha256"] = before
        config = _trial_config(directory, task, block, arm, seeds, control=control)
        config_data = team._bytes(config)
        attempt.update(
            trial_name=config["trial_name"], config_sha256=_digest(config_data)
        )
        team._seal(attempt_path, team._bytes(attempt))
        team._seal(directory / "config.json", config_data)
        (directory / "trials").mkdir(mode=0o700)
        harbor = _entrypoint(harbor)
        env = _native_env(directory)
        team.run_panel._check_native(harbor, env)
        outcome["error"] = "native_launch_failed"
        outcome["returncode"] = _launch(directory, harbor, env, spend_dir)
        if outcome["returncode"] != 0:
            outcome["error"] = "native_nonzero_exit"
        else:
            outcome["error"] = "native_evidence_invalid"
            outcome["status"] = _inspect(config, attempt, task_name)
            outcome["error"] = None
    except _Rejected as exc:
        outcome["error"] = str(exc)
    except subprocess.TimeoutExpired:
        outcome["error"] = "native_timeout"
    except KeyboardInterrupt:
        outcome["error"] = "native_interrupted"
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        pass  # Keep a fixed category, not task contents or host diagnostics.
    finally:
        if not attempt_path.exists():
            team._seal(attempt_path, team._bytes(attempt))
        team._seal(directory / "outcome.json", team._bytes(outcome))
    return outcome


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["_host_trial"]:
        if len(args) != 3:
            return 2
        return _host_trial(Path(args[1]), Path(args[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    blocks = {block.id: block for block in calendar_protocol.blocks()}
    for mode in ("run", "control"):
        command = sub.add_parser(mode)
        command.add_argument("--tasks", type=Path, required=True)
        command.add_argument("--harbor", type=Path, required=True)
        command.add_argument("--study", type=Path, required=True)
        command.add_argument("--block", choices=blocks, required=True)
        command.add_argument("--arm", choices=("blind", "peer"), required=True)
        if mode == "run":
            command.add_argument("--spend-dir", type=Path, required=True)
    options = parser.parse_args(args)
    try:
        outcome = _run(
            tasks=options.tasks,
            harbor=options.harbor,
            study=options.study,
            spend_dir=getattr(options, "spend_dir", None),
            block=blocks[options.block],
            arm=options.arm,
        )
    except _Rejected as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    except (OSError, ValueError):
        parser.exit(1, f"{parser.prog}: private launch setup failed\n")
    print(json.dumps(outcome, sort_keys=True))
    return int(outcome["error"] is not None)


def _interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
