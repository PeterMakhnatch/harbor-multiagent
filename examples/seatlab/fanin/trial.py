from __future__ import annotations

import argparse
import contextlib
import io
import os
import runpy
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn, override
from warnings import catch_warnings

from ..market_handoff import native_stage
from ..visibility import calendar_driver
from ..wait_peer import run as wait_run
from . import protocol

SCHEMA = "seatlab.fanin-launch/v1"
SEAT_IMPORT = "examples.seatlab.fanin.seat:FanInSeat"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELAY = HERE.parent / "visibility" / "local_seat.py"
AGY_SEAT = HERE.parent / "visibility" / "agy_seat.py"
CONTROL = HERE / "control.py"
JOIN_OWNER = HERE / "join_owner.py"
COMMIT = "71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3"
GENERATOR = (
    "flotilla/build_tasks.py",
    "flotilla/families/schedule.py",
    "flotilla/world/oracle.py",
    "flotilla/world/score.py",
    "flotilla/world/world.py",
)
SEED = 1709000311
PLAY_SECONDS = 600
WORLDS = protocol.STUDY_WORLDS
TIMING_WORLDS = protocol.TIMING_WORLDS
CONTROLS = ("correct", "inexact", "wrong")
RECOVERY_CONTROLS = ("correct", "inexact")
LIVE_TAG = "live"
OWNER_ENV = ("HOME", "PATH", "LANG", "TMPDIR")
SOCKET_ROOT = "/tmp"
MAX_SOCKET_PATH_BYTES = 103
MAX_ARCHIVE_BYTES = 1 << 20
MAX_ARCHIVE_MEMBERS = 16
GIT_SECONDS = 60.0
GENERATE_SECONDS = 120.0
READY_SECONDS = 10.0
SETTLE_SECONDS = 5.0
POLL_SECONDS = 0.2
RELAY_STOP = ((signal.SIGTERM, 10.0), (signal.SIGKILL, 10.0))
READY = b"local_seat: ready\n"
ACCEPTED_EXIT = {0: True, 2: False, 3: False, 4: True, 5: True, 6: True, 7: True}
READ_ONLY = 0o444
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
SETUP_FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    RecursionError,
    subprocess.SubprocessError,
    tarfile.TarError,
)
NATIVE_FAILURES = (
    *SETUP_FAILURES,
    RuntimeError,
    ImportError,
    LookupError,
    AttributeError,
)


class Failed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise Failed("invalid_arguments")


@dataclass(frozen=True)
class Plan:
    world: int
    arm: str
    tag: str
    out: Path
    harbor: Path
    python: Path
    agy: Path | None
    control: str | None
    owner_env: dict[str, str]

    @property
    def trial_name(self) -> str:
        return f"sf4-{self.world}-{self.arm}-{self.tag}"

    @property
    def admission(self) -> Path | None:
        if self.agy is None or self.arm not in protocol.TIMING_ARMS:
            return None
        return self.out / "admission"


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt()


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(
        prog="python -B -m examples.seatlab.fanin.trial", allow_abbrev=False
    )
    parser.add_argument("--world", type=int, required=True, choices=WORLDS)
    parser.add_argument("--arm", required=True, choices=protocol.STUDY_ARMS)
    parser.add_argument("--out", type=Path, required=True)
    seat = parser.add_mutually_exclusive_group(required=True)
    seat.add_argument("--control", choices=CONTROLS)
    seat.add_argument("--agy", type=Path)
    args = parser.parse_args(argv)
    if not protocol.admitted(args.world, args.arm) or (
        args.arm in protocol.RECOVERY_ARMS
        and args.control is not None
        and args.control not in RECOVERY_CONTROLS
    ):
        raise Failed("invalid_arguments")
    return args


def regular(path: Path, reason: str) -> Path:
    try:
        info = path.lstat()
    except OSError:
        raise Failed(reason) from None
    if not stat.S_ISREG(info.st_mode):
        raise Failed(reason)
    return path


def executable(path: Path, reason: str) -> Path:
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise Failed(reason)
    return path


def new_out(out: Path) -> Path:
    out = out.absolute()
    if out.name in ("", ".", ".."):
        raise Failed("out_invalid")
    try:
        parent = out.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Failed("out_invalid") from None
    if not stat.S_ISDIR(parent.lstat().st_mode):
        raise Failed("out_invalid")
    cell = parent / out.name
    if os.path.lexists(cell):
        raise Failed("out_exists")
    if cell.is_relative_to(ROOT):
        raise Failed("out_inside_repository")
    return cell


def checked(args: argparse.Namespace) -> Plan:
    regular(RELAY, "relay_missing")
    owner_env = {key: os.environ[key] for key in OWNER_ENV if os.environ.get(key)}
    if args.agy is not None:
        regular(AGY_SEAT, "seat_missing")
        if "HOME" not in owner_env:
            raise Failed("home_missing")
    else:
        regular(CONTROL, "control_missing")
    if args.arm in protocol.RECOVERY_ARMS:
        regular(JOIN_OWNER, "owner_missing")
    harbor = calendar_driver._entrypoint(Path(sys.executable).parent / "harbor")
    return Plan(
        world=args.world,
        arm=args.arm,
        tag=LIVE_TAG if args.agy is not None else f"control-{args.control}",
        out=new_out(args.out),
        harbor=harbor,
        python=executable(Path(sys.executable), "python_invalid"),
        agy=None if args.agy is None else executable(args.agy, "agy_invalid"),
        control=args.control,
        owner_env=owner_env,
    )


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise Failed("private_directory_unsafe")
    return path


def generator_archive() -> bytes:
    git = shutil.which("git", path=calendar_driver._PATH)
    if git is None:
        raise Failed("generator_unavailable")
    env = {
        "PATH": calendar_driver._PATH,
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    done = subprocess.run(
        [git, "-C", str(ROOT), "archive", "--format=tar", COMMIT, *GENERATOR],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=GIT_SECONDS,
        check=False,
    )
    if done.returncode != 0 or not done.stdout or len(done.stdout) > MAX_ARCHIVE_BYTES:
        raise Failed("generator_unavailable")
    return done.stdout


def extract_generator(data: bytes, private: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        if archive.pax_headers.get("comment") != COMMIT:
            raise Failed("generator_unexpected")
        files = sorted(member.name for member in members if member.isfile())
        if (
            len(members) > MAX_ARCHIVE_MEMBERS
            or files != sorted(GENERATOR)
            or any(not (m.isfile() or m.isdir()) for m in members)
        ):
            raise Failed("generator_unexpected")
        archive.extractall(private, filter="data")
    return regular(private / GENERATOR[0], "generator_unexpected")


def generate(python: Path, tasks: Path, world: int) -> Path:
    with tempfile.TemporaryDirectory(prefix="sfg-") as raw:
        private = Path(raw).resolve(strict=True)
        script = extract_generator(generator_archive(), private)
        argv = [
            str(python),
            "-I",
            "-B",
            "-S",
            str(script),
            "--family",
            "schedule",
            "--instances",
            str(world),
            "--seats",
            "4",
            "--difficulty",
            "easy",
            "--seed",
            str(SEED),
            "--conditions",
            "talk",
            "--play-timeout",
            str(PLAY_SECONDS),
            "--out",
            str(tasks),
        ]
        done = subprocess.run(
            argv,
            cwd=private,
            env={"PATH": calendar_driver._PATH, "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=GENERATE_SECONDS,
            check=False,
        )
    if done.returncode != 0 or not (tasks / "schedule").is_dir():
        raise Failed("generator_failed")
    name = f"schedule-{world}-talk"
    if sorted(os.listdir(tasks)) != ["schedule"] or sorted(
        os.listdir(tasks / "schedule")
    ) != [name]:
        raise Failed("generator_failed")
    task = tasks / "schedule" / name
    if task.resolve(strict=True) != task or not stat.S_ISDIR(task.lstat().st_mode):
        raise Failed("generator_failed")
    return task


def checked_task(task: Path, world: int) -> tuple[str, str]:
    from harbor.models.task.task import Task

    with catch_warnings(action="ignore", category=DeprecationWarning):
        stock = Task(task)
    config = stock.config
    want = {
        "family": "schedule",
        "variant": "",
        "instance": world,
        "condition": "talk",
        "n_seats": 4,
        "team_seats": 4,
        "difficulty": "easy",
        "seed": SEED,
        "play_timeout_sec": float(PLAY_SECONDS),
    }
    if (
        stock.name != f"flotilla/schedule-{world}-talk"
        or set(config.agents) != set(protocol.SEATS)
        or not all(seat.under_test for seat in config.agents.values())
        or stock.has_steps
        or any(config.metadata.get(key) != value for key, value in want.items())
    ):
        raise Failed("task_contract_mismatch")
    return stock.name, stock.checksum


def trial_config(plan: Plan, task: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(task), "git_url": None, "name": None},
        "trial_name": plan.trial_name,
        "trials_dir": str(plan.out / "trials"),
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
                "name": None,
                "import_path": SEAT_IMPORT,
                "model_name": None,
                "kwargs": {"world": plan.world, "arm": plan.arm, "seat": seat},
            }
            for seat in protocol.SEATS
        ],
    }


def stage(plan: Plan, task: Path) -> tuple[Path, str]:
    roles = protocol.order(plan.world)
    if set(roles) != set(protocol.SEATS) or len(roles) != len(protocol.SEATS):
        raise Failed("roles_invalid")
    data = native_stage.canonical(trial_config(plan, task))
    config = plan.out / "config.json"
    native_stage.create(config, data, READ_ONLY)
    return config, native_stage.digest(data)


def owner_argv(plan: Plan, seat: str) -> list[str]:
    record = str(plan.out / "owner" / seat)
    if plan.arm in protocol.RECOVERY_ARMS and seat == protocol.order(plan.world)[0]:
        return [
            str(plan.python),
            "-IS",
            str(JOIN_OWNER),
            "--mode",
            plan.arm,
            "--booker",
            seat,
            "--record",
            record,
        ]
    if plan.agy is not None:
        note: tuple[str, ...] = ()
        if plan.admission is not None:
            note = ("--admission-note", str(plan.admission / f"{seat}.json"))
        return [
            str(plan.python),
            "-IS",
            str(AGY_SEAT),
            "--personal-quota",
            "--agy",
            str(plan.agy),
            "--record",
            record,
            *note,
        ]
    if plan.control is None:
        raise Failed("seat_unset")
    return [
        str(plan.python),
        "-IS",
        str(CONTROL),
        "--mode",
        plan.control,
        "--world",
        str(plan.world),
        "--seat",
        seat,
        "--record",
        record,
    ]


def start_relay(
    plan: Plan, seat: str, sockets: Path, logs: Path
) -> subprocess.Popen[bytes]:
    argv = [
        str(plan.python),
        "-IS",
        str(RELAY),
        "serve",
        str(sockets / f"{seat}.sock"),
        "--",
        *owner_argv(plan, seat),
    ]
    log = os.open(logs / f"relay-{seat}.log", CREATE, 0o600)
    try:
        return subprocess.Popen(
            argv,
            cwd=logs,
            env=plan.owner_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    finally:
        os.close(log)


def said_ready(log: Path) -> bool:
    fd = os.open(log, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return os.read(fd, len(READY)) == READY
    finally:
        os.close(fd)


def await_ready(relays: dict[str, subprocess.Popen[bytes]], logs: Path) -> None:
    deadline = time.monotonic() + READY_SECONDS
    for _ in range(int(READY_SECONDS / POLL_SECONDS) + 1):
        waiting = [s for s in relays if not said_ready(logs / f"relay-{s}.log")]
        if not waiting:
            return
        if any(relays[s].poll() is not None for s in waiting):
            raise Failed("relay_not_ready")
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    raise Failed("relay_not_ready")


def stop(child: subprocess.Popen[bytes]) -> bool:
    if child.poll() is not None:
        return False
    for signum, grace in RELAY_STOP:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            child.poll()
            return True
        except PermissionError:
            if child.poll() is None:
                child.send_signal(signum)
        try:
            child.wait(timeout=grace)
            return True
        except subprocess.TimeoutExpired:
            continue
    return True


def reap(
    plan: Plan,
    relays: dict[str, subprocess.Popen[bytes]],
    sockets: Path,
    status: dict[str, Any],
) -> None:
    deadline = time.monotonic() + SETTLE_SECONDS
    for seat in protocol.SEATS:
        if seat in relays:
            with contextlib.suppress(subprocess.TimeoutExpired):
                relays[seat].wait(timeout=max(0.0, deadline - time.monotonic()))
    owners = status["owners"]
    for seat in protocol.SEATS:
        if seat not in relays:
            continue
        child = relays[seat]
        alive = child.poll() is None
        owners[seat]["accepted"] = (
            not os.path.lexists(sockets / f"{seat}.sock")
            if alive
            else ACCEPTED_EXIT.get(child.returncode)
        )
        owners[seat]["relay_stopped"] = stop(child)
        owners[seat]["relay_returncode"] = child.returncode
    for seat in protocol.SEATS:
        owners[seat]["record_present"] = os.path.lexists(plan.out / "owner" / seat)
    status["accepted_seats"] = [
        s for s in protocol.SEATS if owners[s]["accepted"] is True
    ]
    status["reaped"] = all(child.returncode is not None for child in relays.values())


def native(harbor: Path, config: Path, status: dict[str, Any]) -> None:
    argv = sys.argv
    status["error"] = "native_launch_failed"
    sys.argv = [str(harbor), "trial", "start", "-c", str(config)]
    try:
        runpy.run_path(str(harbor), run_name="__main__")
        status["returncode"] = 0
    except SystemExit as exit_:
        code = exit_.code
        status["returncode"] = code if type(code) is int else (0 if code is None else 1)
    except NATIVE_FAILURES as error:
        status["exception"] = type(error).__name__
        return
    finally:
        sys.argv = argv
    status["error"] = None if status["returncode"] == 0 else "native_nonzero_exit"


def run_team(plan: Plan, config: Path, tasks: Path, status: dict[str, Any]) -> None:
    relays: dict[str, subprocess.Popen[bytes]] = {}
    root = Path(tempfile.mkdtemp(prefix="sfi-", dir=SOCKET_ROOT)).resolve(strict=True)
    sockets = root / "s"
    try:
        private_directory(sockets)
        logs = private_directory(root / "l")
        wait_run.checked_sockets(sockets, plan.out, tasks)
        if any(
            len(os.fsencode(sockets / f"{seat}.sock")) > MAX_SOCKET_PATH_BYTES
            for seat in protocol.SEATS
        ):
            raise Failed("socket_path_too_long")
        for seat in protocol.SEATS:
            relays[seat] = start_relay(plan, seat, sockets, logs)
        await_ready(relays, logs)
        from . import seat as fanin_seat

        fanin_seat.bind(sockets)
        native(plan.harbor, config, status)
    finally:
        try:
            reap(plan, relays, sockets, status)
        finally:
            shutil.rmtree(root, ignore_errors=True)


def execute(plan: Plan, status: dict[str, Any]) -> None:
    private_directory(plan.out / "owner")
    if plan.admission is not None:
        private_directory(plan.admission)
    tasks = plan.out / "tasks"
    task = generate(plan.python, tasks, plan.world)
    wait_run.isolate(plan.out)
    status["task_name"], status["task_checksum"] = checked_task(task, plan.world)
    config, status["config_sha256"] = stage(plan, task)
    run_team(plan, config, tasks, status)
    if not status["reaped"] and status["error"] is None:
        status["error"] = "relays_unreaped"


def initial_status(plan: Plan) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "world": plan.world,
        "arm": plan.arm,
        "tag": plan.tag,
        "trial_name": plan.trial_name,
        "generator_commit": COMMIT,
        "seed": SEED,
        "task_name": None,
        "task_checksum": None,
        "config_sha256": None,
        "returncode": None,
        "error": "setup_failed",
        "exception": None,
        "result_present": False,
        "reaped": True,
        "owners": {
            seat: {
                "accepted": False,
                "relay_returncode": None,
                "relay_stopped": False,
                "record_present": False,
            }
            for seat in protocol.SEATS
        },
        "accepted_seats": [],
    }


def reason(error: BaseException) -> str:
    if isinstance(error, Failed):
        return error.reason
    if isinstance(error, calendar_driver._Rejected):
        return str(error)
    return type(error).__name__


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _interrupt)
    try:
        plan = checked(parse(argv))
        private_directory(plan.out)
    except (Failed, *SETUP_FAILURES) as error:
        print(f"fanin trial: {reason(error)}", file=sys.stderr)
        return 2
    status = initial_status(plan)
    try:
        execute(plan, status)
    except (Failed, calendar_driver._Rejected) as failure:
        status["error"] = reason(failure)
    except KeyboardInterrupt:
        status["error"] = "native_interrupted"
    except SETUP_FAILURES as error:
        status["error"] = status["error"] or "driver_failed"
        status["exception"] = status["exception"] or type(error).__name__
    finally:
        trial = plan.out / "trials" / plan.trial_name / "result.json"
        status["result_present"] = trial.is_file()
        line = native_stage.canonical(status)
        native_stage.create(plan.out / "launch.json", line, READ_ONLY)
        print(line.decode("ascii"))
    return 0 if status["error"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
