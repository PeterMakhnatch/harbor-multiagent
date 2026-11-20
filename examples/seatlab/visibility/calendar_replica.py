"""Run one registered calendar replica block over a private stdio exchange."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import shutil
import signal
import stat
import sys
from collections.abc import Generator
from contextlib import chdir, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, Never, override
from warnings import catch_warnings

from . import (
    calendar_driver,
    calendar_packet,
    calendar_protocol,
    calendar_replica_source,
    replica_io,
)

if TYPE_CHECKING:
    from harbor.models.trial.result import TrialResult

_SCHEMA = "seatlab.calendar-replica/v1"
_IMPORT = "examples.seatlab.visibility.calendar_replica_seat:RelayCalendarSeat"
_SEATS = ("p1", "p2")
_WORLDS = range(calendar_protocol._WORLD_COUNT)
_FILE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
_LOCKS = Path("/tmp") / f"seatlab-calendar-locks-{os.geteuid()}"
_Status = Literal["complete", "invalid_seed"]
_Seeds = tuple[tuple[int, ...], tuple[int, ...]]


class _Failed(ValueError):
    """A fixed error category for the terminal record."""


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        raise _Failed("invalid_arguments")

    @override
    def print_help(self, file: Any = None) -> None:
        super().print_help(sys.stderr if file is None else file)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(required=True)
    play = commands.add_parser("play", allow_abbrev=False)
    play.add_argument("--tasks", required=True, type=Path)
    play.add_argument("--out", required=True, type=Path)
    play.add_argument("--world", required=True, type=int, choices=_WORLDS)
    play.add_argument("--direction", required=True, type=int, choices=(0, 1))
    play.add_argument("--repetition", required=True, type=int, choices=(0, 1))
    return parser.parse_args(argv)


def _run_id(block: calendar_protocol.Block) -> str:
    return f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}"


def _outside_checkout(path: Path) -> None:
    if (
        not path.is_absolute()
        or path.resolve() != path
        or any((parent / ".git").exists() for parent in (path, *path.parents))
    ):
        raise _Failed("private_physical_path_required")


def _private_directory(details: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(details.st_mode)
        and stat.S_IMODE(details.st_mode) == 0o700
        and details.st_uid == os.geteuid()
    )


@contextmanager
def _coordinate_lease(block: calendar_protocol.Block) -> Generator[None]:
    """Two studies on one Docker daemon must not share a Compose project."""
    try:
        _LOCKS.mkdir(mode=0o700, exist_ok=True)
        directory = os.open(_LOCKS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if not _private_directory(os.fstat(directory)):
                raise _Failed("private_lock_required")
            fd = os.open(
                _run_id(block),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
        finally:
            os.close(directory)
    except OSError:
        raise _Failed("private_lock_required") from None
    try:
        info = os.fstat(fd)
        if not (
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
        ):
            raise _Failed("private_lock_required")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _Failed("coordinate_busy") from None
        yield
    finally:
        # Keep the inode: deleting it could let a second process lock a different file.
        os.close(fd)


def _source(tasks: Path, block: calendar_protocol.Block) -> Path:
    _outside_checkout(tasks)
    try:
        details = tasks.lstat()
    except OSError:
        raise _Failed("private_source_required") from None
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or not any(
            _private_directory(parent.lstat()) for parent in (tasks, *tasks.parents)
        )
    ):
        raise _Failed("private_source_required")
    return tasks / calendar_packet._task_name(block.world)


def _claim(out: Path, tasks: Path) -> None:
    _outside_checkout(out)
    if out.is_relative_to(tasks):
        raise _Failed("output_overlaps_source")
    try:
        fd = os.open(out.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise _Failed("private_output_parent_required") from None
    try:
        if not _private_directory(os.fstat(fd)):
            raise _Failed("private_output_parent_required")
        # mkdir is exclusive: never resume or replace an earlier attempt.
        os.mkdir(out.name, mode=0o700, dir_fd=fd)
    except FileExistsError:
        raise _Failed("output_exists") from None
    finally:
        os.close(fd)


@contextmanager
def _private_log(path: Path) -> Generator[None]:
    # Redirect descriptors, not just sys.stdout, so native output cannot reach JSONL.
    with os.fdopen(os.open(path, _FILE, 0o600), "wb") as log:
        sys.stdout.flush()
        sys.stderr.flush()
        stdout, stderr = os.dup(1), os.dup(2)
        try:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            yield
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os.dup2(stdout, 1)
                os.dup2(stderr, 2)
                os.close(stdout)
                os.close(stderr)


def _local_docker_host(host: str) -> str:
    if not host.startswith("unix:///") or any(c in host for c in "\r\n\0%?#"):
        raise _Failed("local_docker_required")
    try:
        socket = Path(host.removeprefix("unix://")).resolve(strict=True)
        if not stat.S_ISSOCK(socket.stat().st_mode):
            raise _Failed("local_docker_required")
    except OSError:
        raise _Failed("local_docker_required") from None
    if shutil.which("docker", path=calendar_driver._PATH) is None:
        raise _Failed("local_docker_required")
    return f"unix://{socket}"


def _configure_host(directory: Path) -> None:
    requested_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    os.environ.clear()
    docker_host = _local_docker_host(requested_host)
    home, tmp = directory / "home", directory / "tmp"
    home.mkdir(mode=0o700)
    tmp.mkdir(mode=0o700)
    plugins = calendar_driver._PLUGINS
    if (plugins / "docker-compose").is_file():
        docker = home / ".docker"
        docker.mkdir(mode=0o700)
        _write(docker / "config.json", {"cliPluginsExtraDirs": [str(plugins)]})
    os.environ.update(
        PATH=calendar_driver._PATH,
        HOME=str(home),
        TMPDIR=str(tmp),
        DOCKER_HOST=docker_host,
        COMPOSE_BAKE="false",
        COMPOSE_DISABLE_ENV_FILE="1",
        HARBOR_TELEMETRY="0",
        LITELLM_LOCAL_MODEL_COST_MAP="true",
        PYTHON_DOTENV_DISABLED="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.dont_write_bytecode = True


def _line(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _write(path: Path, value: object) -> None:
    with os.fdopen(os.open(path, _FILE, 0o600), "wb") as stream:
        stream.write(_line(value))


def _identity(
    task: Path, block: calendar_protocol.Block
) -> calendar_replica_source.Identity:
    try:
        return calendar_replica_source.verify(task, block.world)
    except (OSError, ValueError):
        raise _Failed("source_invalid") from None


def _seed_pair(
    exchange: replica_io.Exchange, block: calendar_protocol.Block
) -> _Seeds | None:
    world = calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)[
        block.world
    ]
    values: list[tuple[int, ...] | None] = []
    for seat, table in zip(_SEATS, world.tables, strict=True):
        try:
            text = exchange.ask(
                _run_id(block),
                f"cal_initial_{seat}",
                seat,
                calendar_protocol.initial_prompt(table),
            )
        except (OSError, ValueError):
            raise _Failed("transport_failed") from None
        try:
            values.append(calendar_protocol.decode_list(text))
        except ValueError:
            values.append(None)
    first, second = values
    return (first, second) if first is not None and second is not None else None


def _config(
    out: Path, task: Path, block: calendar_protocol.Block, arm: str, seeds: _Seeds
) -> dict[str, Any]:
    config = calendar_driver._trial_config(out, task, block, arm, seeds, control=False)
    config["trial_name"] = f"{_run_id(block)}-{arm}"
    config["trials_dir"] = str(out / "native")
    for participant in config["participants"]:
        participant.update(import_path=_IMPORT, model_name=None)
    return config


async def _run_trial(config: dict[str, Any], expected_checksum: str) -> TrialResult:
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    with catch_warnings(action="ignore", category=DeprecationWarning):
        trial = await Trial.create(TrialConfig.model_validate(config))
        if (
            trial.task.task_dir != Path(config["task"]["path"])
            or trial.task.checksum != expected_checksum
        ):
            raise _Failed("native_checksum_mismatch")
        return await trial.run()


def _object(path: Path) -> dict[str, Any]:
    if path.resolve() != path:
        raise _Failed("native_evidence_invalid")
    return calendar_packet._object(calendar_packet._json(calendar_packet._read(path)))


def _completed(
    result: TrialResult,
    config: dict[str, Any],
    block: calendar_protocol.Block,
    checksum: str,
) -> None:
    seats = result.seat_results or ()
    stages = {}
    for seat in seats:
        metadata = seat.agent_result.metadata if seat.agent_result else None
        calendar = (metadata or {}).get("calendar")
        if isinstance(calendar, dict):
            stages[seat.seat] = calendar.get("stage")
    if "model_failed" in stages.values():
        raise _Failed("transport_failed")
    rewards = result.verifier_result.rewards if result.verifier_result else None
    if (
        result.recorded_exceptions()
        or not result.finished_at
        or not result.reward_valid
        or rewards is None
        or rewards.keys() != {"reward"}
        or type(rewards["reward"]) not in (int, float)
        or rewards["reward"] not in (0, 1)
    ):
        raise _Failed("native_failed")
    if (
        len(seats) != 2
        or stages.keys() != set(_SEATS)
        or result.task_checksum != checksum
        or result.trial_name != config["trial_name"]
    ):
        raise _Failed("native_evidence_invalid")

    pair = (stages.get(block.sender), stages.get(block.receiver))
    invalid = pair in (
        ("model_invalid", "peer_model_invalid"),
        ("complete", "model_invalid"),
    )
    if not invalid and pair != ("complete", "complete"):
        raise _Failed("native_evidence_invalid")
    trial = Path(config["trials_dir"]) / config["trial_name"]
    try:
        score = _object(trial / "verifier" / "score.json")
        reward = _object(trial / "verifier" / "reward.json")
        value = rewards["reward"]
        if (
            type(score.get("score")) not in (int, float)
            or type(reward.get("reward")) not in (int, float)
            or score["score"] != value
            or reward != rewards
            or "error" not in score
            or score["error"] is not None
            or type(score.get("n_submissions")) is not int
            or score["n_submissions"] != int(not invalid)
            or "submitted_by" not in score
            or score["submitted_by"] != (None if invalid else block.receiver)
            or type(score.get("n_messages")) is not int
            or score.get("n_messages") != 2
            or (invalid and value != 0)
        ):
            raise _Failed("native_evidence_invalid")
        for name in calendar_driver._ARTIFACTS:
            artifact = trial / "artifacts" / name.removeprefix("/")
            details = artifact.lstat()
            if artifact.resolve() != artifact or not (
                stat.S_ISREG(details.st_mode) and details.st_size
            ):
                raise _Failed("native_evidence_invalid")
    except (OSError, ValueError):
        raise _Failed("native_evidence_invalid") from None


def _play(
    tasks: Path,
    out: Path,
    block: calendar_protocol.Block,
    input_stream: BinaryIO,
    output: BinaryIO,
) -> _Status:
    task = _source(tasks, block)
    _claim(out, tasks)
    host = out / "host"
    host.mkdir(mode=0o700)
    with _private_log(host / "native.log"), chdir(host):
        _configure_host(host)
        identity = _identity(task, block)
        _write(
            out / "block.json",
            {
                "schema": _SCHEMA,
                "world": block.world,
                "direction": block.direction,
                "repetition": block.repetition,
                "source": str(task),
                "native_sha256": identity.native_sha256,
                "semantics_sha256": identity.semantics_sha256,
            },
        )
        turns = out / "turns"
        turns.mkdir(mode=0o700)
        (out / "native").mkdir(mode=0o700)
        exchange = replica_io.Exchange(input_stream, output, turns)
        with replica_io.bind(exchange):
            seeds = _seed_pair(exchange, block)
            if seeds is None:
                return "invalid_seed"
            for arm in block.arms:
                if _identity(task, block) != identity:
                    raise _Failed("source_changed")
                config = _config(out, task, block, arm, seeds)
                try:
                    result = asyncio.run(_run_trial(config, identity.native_sha256))
                except _Failed:
                    raise
                # Harbor may raise from setup or teardown. Neither is a scored zero.
                except Exception:  # noqa: BLE001
                    raise _Failed("native_failed") from None
                _completed(result, config, block, identity.native_sha256)
                if _identity(task, block) != identity:
                    raise _Failed("source_changed")
    return "complete"


def main(argv: list[str] | None = None) -> int:
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0) as output:
        original_umask = os.umask(0o077)
        try:
            args = _parse(argv)
            block = calendar_protocol.Block(args.world, args.direction, args.repetition)
            with _coordinate_lease(block):
                status = _play(args.tasks, args.out, block, sys.stdin.buffer, output)
            end = {"type": "end", "status": status}
        except _Failed as exc:
            end = {"type": "end", "status": "failed", "error": str(exc)}
        except KeyboardInterrupt:
            end = {"type": "end", "status": "failed", "error": "interrupted"}
        # The stdio boundary must not expose private paths, data, or tracebacks.
        except Exception:  # noqa: BLE001
            end = {"type": "end", "status": "failed", "error": "replica_failed"}
        finally:
            os.umask(original_umask)
        try:
            output.write(_line(end))
        except OSError:
            return 1
    return int(end["status"] == "failed")


def _interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
