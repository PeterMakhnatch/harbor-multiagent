from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn, override

from . import cohort, protocol, run

SCHEMA = "seatlab.wait-peer-work/v1"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELAY = HERE.parent / "visibility" / "local_seat.py"
AGY_SEAT = HERE.parent / "visibility" / "agy_seat.py"
CONTROL = HERE / "control.py"
PHASES = protocol.PHASES
CONTROL_TAGS = {"correct": "control-ok", "wrong": "control-wrong"}
LIVE_TAG = "live"
SOCKET_ROOT = "/tmp"
MAX_SOCKET_PATH_BYTES = 103
NATIVE_SECONDS = 7200.0
READY_SECONDS = 10.0
SETTLE_SECONDS = 5.0
RECEIPT_SECONDS = 300.0
POLL_SECONDS = 0.2
NATIVE_STOP = ((signal.SIGTERM, 120.0), (signal.SIGKILL, 30.0))
RELAY_STOP = ((signal.SIGTERM, 10.0), (signal.SIGKILL, 10.0))
MAX_RECEIPT_BYTES = 1 << 20
MAX_TRIAL_ENTRIES = 16
READY = b"local_seat: ready\n"
CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
OWNER_ENV = ("HOME", "PATH", "LANG", "TMPDIR")
NATIVE_ENV = ("HOME", "PATH", "LANG", "TMPDIR")
SHA256 = re.compile(r"[0-9a-f]{64}")
WORD = re.compile(r"[a-z][a-z0-9_-]{0,39}")
FAILURES = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    RecursionError,
    subprocess.SubprocessError,
)
SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
STATE = {"held": False, "pending": False}


class Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Stopped(BaseException):
    pass


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise Refused("invalid_arguments")


@dataclass(frozen=True)
class Plan:
    tasks: Path
    task: Path
    manifest: Path
    sha256: str
    world: int
    arm: str
    tag: str
    slot: Path
    harbor: Path
    python: Path
    agy: Path | None
    control: str | None
    cell: Path

    @property
    def trial_name(self) -> str:
        return f"swr-{self.world:03d}-{self.arm}-{self.tag}"


def _interrupt(signum: int, frame: FrameType | None) -> None:
    if STATE["held"]:
        STATE["pending"] = True
        return
    raise Stopped()


@contextlib.contextmanager
def held() -> Iterator[None]:
    STATE["held"] = True
    try:
        yield
    finally:
        STATE["held"] = False
    if STATE["pending"]:
        STATE["pending"] = False
        raise Stopped()


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(
        prog="python -BS -m examples.seatlab.wait_peer.worker",
        description="Claim one new cell, run both native seats once, and verify the first outcome.",
        allow_abbrev=False,
    )
    parser.add_argument("tasks", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--arm", required=True, choices=protocol.ARMS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slot", type=Path, required=True)
    parser.add_argument("--harbor-python", type=Path, required=True)
    seat = parser.add_mutually_exclusive_group(required=True)
    seat.add_argument("--agy", type=Path)
    seat.add_argument("--control", choices=tuple(CONTROL_TAGS))
    return parser.parse_args(argv)


def executable(path: Path, reason: str) -> Path:
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise Refused(reason)
    return path


def regular(path: Path, reason: str) -> Path:
    try:
        info = path.lstat()
    except OSError:
        raise Refused(reason) from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused(reason)
    return path


def real_directory(path: Path, reason: str) -> Path:
    try:
        info = path.lstat()
    except OSError:
        raise Refused(reason) from None
    if not stat.S_ISDIR(info.st_mode):
        raise Refused(reason)
    return path


def physical(path: Path, reason: str) -> Path:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Refused(reason) from None


def new_cell(out: Path) -> Path:
    out = out.absolute()
    if out.name in ("", ".", ".."):
        raise Refused("out_invalid")
    cell = real_directory(physical(out.parent, "out_invalid"), "out_invalid") / out.name
    if os.path.lexists(cell):
        raise Refused("out_exists")
    if cell.is_relative_to(ROOT):
        raise Refused("out_inside_repository")
    return cell


def bound_task(tasks: Path, manifest: Path, sha256: str, world: int) -> Path:
    try:
        selected, rows = cohort.load(manifest, sha256)
    except FAILURES:
        raise Refused("manifest_invalid") from None
    if not selected.first <= world <= selected.last:
        raise Refused("world_outside_cohort")
    try:
        task = run.content_bind(tasks, selected, rows, world)["task"]
    except FAILURES:
        raise Refused("task_mismatch") from None
    try:
        cohort.verify_origin(selected, sha256)
    except cohort.Unverified as failure:
        raise Refused(failure.reason) from None
    return task


def checked(args: argparse.Namespace) -> Plan:
    if SHA256.fullmatch(args.sha256) is None:
        raise Refused("sha256_invalid")
    tasks = real_directory(physical(args.tasks, "tasks_invalid"), "tasks_invalid")
    manifest = regular(args.manifest.absolute(), "manifest_invalid")
    task = bound_task(tasks, manifest, args.sha256, args.world)
    cell = new_cell(args.out)
    if cell.is_relative_to(tasks) or tasks.is_relative_to(cell):
        raise Refused("out_overlaps_tasks")
    regular(RELAY, "relay_missing")
    if args.agy is not None:
        regular(AGY_SEAT, "seat_missing")
        if not os.environ.get("HOME"):
            raise Refused("home_missing")
    else:
        regular(CONTROL, "control_missing")
    return Plan(
        tasks=tasks,
        task=task,
        manifest=manifest,
        sha256=args.sha256,
        world=args.world,
        arm=args.arm,
        tag=LIVE_TAG if args.agy is not None else CONTROL_TAGS[args.control],
        slot=executable(args.slot, "slot_invalid"),
        harbor=executable(args.harbor_python, "harbor_python_invalid"),
        python=executable(Path(sys.executable), "python_invalid"),
        agy=None if args.agy is None else executable(args.agy, "agy_invalid"),
        control=args.control,
        cell=cell,
    )


def native_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in NATIVE_ENV if os.environ.get(key)}
    host = os.environ.get("DOCKER_HOST")
    if host is not None:
        if not host.startswith("unix:///") or any(c in host for c in "\r\n\0%?#"):
            raise Refused("docker_host_not_local")
        socket = Path(host.removeprefix("unix://")).resolve(strict=True)
        if not stat.S_ISSOCK(socket.stat().st_mode):
            raise Refused("docker_host_not_local")
        env["DOCKER_HOST"] = f"unix://{socket}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def owner_env() -> dict[str, str]:
    return {key: os.environ[key] for key in OWNER_ENV if os.environ.get(key)}


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise Refused("private_directory_unsafe")
    return path


def make_cell(cell: Path) -> Path:
    private_directory(cell)
    private_directory(cell / "owner")
    return private_directory(cell / "logs")


def private_roots(raw: str) -> tuple[Path, Path]:
    root = Path(raw).resolve(strict=True)
    info = root.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused("private_directory_unsafe")
    sockets = private_directory(root / "s")
    longest = max(len(os.fsencode(sockets / f"{phase}.sock")) for phase in PHASES)
    if longest > MAX_SOCKET_PATH_BYTES:
        raise Refused("socket_path_too_long")
    return sockets, private_directory(root / "w")


def owner_argv(plan: Plan, phase: str) -> list[str]:
    if plan.agy is not None:
        return [
            str(plan.python),
            "-IS",
            str(AGY_SEAT),
            "--personal-quota",
            "--agy",
            str(plan.agy),
            "--record",
            str(plan.cell / "owner" / phase),
        ]
    if plan.control is None:
        raise Refused("seat_unset")
    return [str(plan.python), "-IS", str(CONTROL), phase, plan.control]


def start_relay(
    plan: Plan, phase: str, sockets: Path, work: Path, logs: Path
) -> subprocess.Popen[bytes]:
    argv = [
        str(plan.python),
        "-IS",
        str(RELAY),
        "serve",
        str(sockets / f"{phase}.sock"),
        "--",
        *owner_argv(plan, phase),
    ]
    log = os.open(logs / f"relay-{phase}.log", CREATE, 0o600)
    try:
        return subprocess.Popen(
            argv,
            cwd=work,
            env=owner_env(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    finally:
        os.close(log)


def launch_native(
    plan: Plan, sockets: Path, logs: Path, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    argv = [
        str(plan.slot),
        str(plan.harbor),
        "-B",
        "-m",
        "examples.seatlab.wait_peer.run",
        str(plan.tasks),
        str(plan.manifest),
        "--sha256",
        plan.sha256,
        "--world",
        str(plan.world),
        "--arm",
        plan.arm,
        "--out",
        str(plan.cell / "native"),
        "--sockets",
        str(sockets),
    ]
    if plan.control is not None:
        argv += ["--tag", plan.tag]
    log = os.open(logs / "native.log", CREATE, 0o600)
    try:
        return subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
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
        waiting = [p for p in PHASES if not said_ready(logs / f"relay-{p}.log")]
        if not waiting:
            return
        if any(relays[p].poll() is not None for p in waiting):
            raise Refused("relay_not_ready")
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    raise Refused("relay_not_ready")


def staged(plan: Plan) -> bool:
    try:
        return stat.S_ISREG((plan.cell / "native" / "config.json").lstat().st_mode)
    except FileNotFoundError:
        return False


def supervise(
    plan: Plan,
    native: subprocess.Popen[bytes],
    relays: dict[str, subprocess.Popen[bytes]],
    roots: tuple[Path, Path],
    logs: Path,
) -> int:
    deadline = time.monotonic() + NATIVE_SECONDS
    for _ in range(int(NATIVE_SECONDS / POLL_SECONDS) + 1):
        code = native.poll()
        if code is not None:
            return code
        if not relays and staged(plan):
            for phase in PHASES:
                with held():
                    relays[phase] = start_relay(plan, phase, *roots, logs)
            await_ready(relays, logs)
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    raise Refused("native_timed_out")


def stop(
    child: subprocess.Popen[bytes], steps: tuple[tuple[signal.Signals, float], ...]
) -> bool:
    if child.poll() is not None:
        return False
    for signum, grace in steps:
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
    native: subprocess.Popen[bytes] | None,
    relays: dict[str, subprocess.Popen[bytes]],
    status: dict[str, Any],
) -> None:
    status["native_stopped"] = native is not None and stop(native, NATIVE_STOP)
    deadline = time.monotonic() + SETTLE_SECONDS
    for phase in PHASES:
        if phase in relays:
            with contextlib.suppress(subprocess.TimeoutExpired):
                relays[phase].wait(timeout=max(0.0, deadline - time.monotonic()))
    stopped = [
        phase for phase in PHASES if phase in relays and stop(relays[phase], RELAY_STOP)
    ]
    status["relays_stopped"] = stopped
    status["relays"] = {
        phase: (relays[phase].returncode if phase in relays else None)
        for phase in PHASES
    }
    status["reaped"] = all(
        child.returncode is not None
        for child in [*relays.values(), *([native] if native is not None else [])]
    )


def run_native(
    plan: Plan, env: dict[str, str], logs: Path, status: dict[str, Any]
) -> int:
    native: subprocess.Popen[bytes] | None = None
    relays: dict[str, subprocess.Popen[bytes]] = {}
    with tempfile.TemporaryDirectory(
        prefix="swp-", dir=SOCKET_ROOT, ignore_cleanup_errors=True
    ) as raw:
        roots = private_roots(raw)
        try:
            with held():
                native = launch_native(plan, roots[0], logs, env)
            code = supervise(plan, native, relays, roots, logs)
        finally:
            with held():
                reap(native, relays, status)
    if not status["reaped"]:
        raise Refused("children_unreaped")
    status["native_returncode"] = code
    return code


def trial_directory(plan: Plan) -> Path:
    trials = real_directory(plan.cell / "native" / "trials", "trial_missing")
    with os.scandir(trials) as listing:
        entries = list(itertools.islice(listing, MAX_TRIAL_ENTRIES + 1))
    if len(entries) > MAX_TRIAL_ENTRIES or any(e.is_symlink() for e in entries):
        raise Refused("trial_not_singular")
    names = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
    if names != [plan.trial_name]:
        raise Refused("trial_not_singular")
    trial = trials / plan.trial_name
    if trial.resolve(strict=True) != trial:
        raise Refused("trial_not_canonical")
    return trial


def receipt_argv(plan: Plan, trial: Path) -> list[str]:
    argv = [
        str(plan.python),
        "-BS",
        "-m",
        "examples.seatlab.wait_peer.receipt",
        str(trial),
        "--task",
        str(plan.task),
        "--world",
        str(plan.world),
        "--arm",
        plan.arm,
    ]
    if plan.agy is not None:
        argv += ["--owner", str(plan.cell / "owner")]
    return argv


def run_receipt(plan: Plan, trial: Path, logs: Path) -> tuple[int, bytes]:
    env = {key: os.environ[key] for key in ("PATH", "LANG") if os.environ.get(key)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = os.open(logs / "receipt.out", CREATE, 0o600)
    try:
        err = os.open(logs / "receipt.log", CREATE, 0o600)
        try:
            done = subprocess.run(
                receipt_argv(plan, trial),
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                timeout=RECEIPT_SECONDS,
                check=False,
            )
        finally:
            os.close(err)
    finally:
        os.close(out)
    fd = os.open(logs / "receipt.out", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(fd, MAX_RECEIPT_BYTES + 1)
    finally:
        os.close(fd)
    return done.returncode, raw


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) != len(dict(pairs)):
        raise Refused("receipt_invalid")
    return dict(pairs)


def _no_constants(name: str) -> NoReturn:
    raise Refused("receipt_invalid")


def checked_receipt(plan: Plan, raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_RECEIPT_BYTES:
        raise Refused("receipt_invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_no_constants
        )
    except (ValueError, RecursionError):
        raise Refused("receipt_invalid") from None
    if type(value) is not dict:
        raise Refused("receipt_invalid")
    identity = {
        "schema": "seatlab.wait-peer-receipt/v1",
        "world": plan.world,
        "arm": plan.arm,
        "tag": plan.tag,
    }
    if any(value.get(key) != want for key, want in identity.items()):
        raise Refused("receipt_mismatch")
    complete = value.get("complete")
    claims = value.get("claims")
    if type(complete) is not bool or not isinstance(claims, dict):
        raise Refused("receipt_invalid")
    if complete and (
        type(value.get("native_reward")) is not int
        or value["native_reward"] not in (0, 1)
        or claims.get("stock_reward_verified") is not True
        or claims.get("all_prompts_reconstructed") is not True
        or (plan.tag == LIVE_TAG and claims.get("owner_answers_bound") is not True)
    ):
        raise Refused("receipt_unverified")
    if not complete and value.get("native_reward") is not None:
        raise Refused("receipt_invalid")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def create(path: Path, data: bytes, mode: int) -> None:
    fd = os.open(path, CREATE, mode)
    with os.fdopen(fd, "wb") as output:
        os.fchmod(output.fileno(), mode)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def work(plan: Plan, env: dict[str, str], logs: Path, status: dict[str, Any]) -> int:
    status["outcome"] = "failed"
    code = run_native(plan, env, logs, status)
    try:
        trial = trial_directory(plan)
    except Refused:
        if code != 0:
            raise Refused("native_failed") from None
        raise
    receipt_code, raw = run_receipt(plan, trial, logs)
    status["receipt_returncode"] = receipt_code
    if receipt_code != 0:
        raise Refused("receipt_rejected")
    receipt = checked_receipt(plan, raw)
    status["receipt_status"] = "measured" if receipt["complete"] else "incomplete"
    if code != 0:
        raise Refused("native_failed")
    create(plan.cell / "receipt.json", canonical(receipt) + b"\n", 0o444)
    if not receipt["complete"]:
        status["outcome"] = "incomplete"
        status["reason"] = "native_incomplete"
        return 3
    status["outcome"] = "complete"
    status["reason"] = None
    return 0


def emit(status: dict[str, Any], logs: Path | None) -> None:
    line = canonical(status) + b"\n"
    if logs is not None:
        with contextlib.suppress(OSError):
            create(logs / "worker.json", line, 0o600)
    with contextlib.suppress(OSError):
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    status: dict[str, Any] = {"schema": SCHEMA, "outcome": "refused", "reason": None}
    logs: Path | None = None
    code = 2
    for signum in SIGNALS:
        signal.signal(signum, _interrupt)
    try:
        plan = checked(parse(argv))
        status.update(world=plan.world, arm=plan.arm, tag=plan.tag)
        env = native_env()
        with held():
            logs = make_cell(plan.cell)
        code = work(plan, env, logs, status)
    except Refused as refusal:
        status["reason"] = refusal.reason
        code = 2 if logs is None else 1
    except Stopped:
        status["reason"] = "interrupted"
        code = 1
    except FAILURES as error:
        status["reason"] = "error"
        status["error"] = type(error).__name__
        code = 2 if logs is None else 1
    status["elapsed_seconds"] = int(time.monotonic() - started)
    emit(status, logs)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
