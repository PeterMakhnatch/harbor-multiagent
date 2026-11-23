"""Run one certified-reporter fan-in team once, as one operator command.

python3.12 -BS -m examples.seatlab.fanin.reporter_worker --world W --arm all|stop \\
  --out NEW_ABS_DIR --harbor-python ABS --slot ABS (--agy ABS | --control correct|inexact)

Run it from the checkout root. OUT must be a new absolute path outside the checkout. Its
parent must already exist as a private directory that you own. Every check runs before
OUT is created. Creating OUT issues the first attempt; a used OUT is never reused, also
after a failure or an interruption.

The command first waits, at most one hour, for the site slot wrapper to admit one team
process. Only that admitted process creates the sockets, starts the listeners and runs
the native trial, so the 10-minute listener window does not include the queue. The slot
wrapper gets only HOME, PATH, TMPDIR, a fixed LANG, the package path and a local
DOCKER_HOST, if one is set.

Four single-use local_seat listeners answer the native seats through a short private
socket directory under /tmp. The booker seat always runs certified_booker.py. With --agy
each reporter starts one signed-in personal-quota session only when it is asked and keeps
its receipt under OUT/owners. With --control correct the reporters run example_owner.py.
With --control inexact the first reporter runs control.py --mode inexact, recorded under
OUT/control, and the other reporters run example_owner.py. Owners get only HOME, PATH,
TMPDIR and a fixed LANG.

The native trial runs once, with the Harbor Python, under OUT/native. The command never
retries. Afterwards OUT/external.json holds the external evidence row and, with --agy,
OUT/provenance.json binds the three reporters to their owner receipts. Private logs stay
in OUT/logs. Stdout is one path-free summary line.

Exit 0: graded and audited. Exit 1: issued, but ungraded, unaudited or interrupted.
Exit 2: refused before issuance.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, override

from ..visibility import calendar_driver
from ..visibility import calendar_packet as packet
from ..wait_peer import worker
from ..wait_peer.receipt import Bad, canonical
from . import external_evidence, external_owner, owner_provenance, protocol

SCHEMA = "seatlab.fanin-reporter-work/v1"
MODULE = "examples.seatlab.fanin.reporter_worker"
TEAM = "team"
HERE = Path(__file__).resolve().parent
ROOT = worker.ROOT
RELAY = worker.RELAY
AGY_SEAT = worker.AGY_SEAT
BOOKER = HERE / "certified_booker.py"
EXAMPLE_OWNER = HERE / "example_owner.py"
CONTROL = HERE / "control.py"
TRIAL_MODULE = "examples.seatlab.fanin.trial"
TRIAL = HERE / "trial.py"
SEATS = protocol.SEATS
ARMS = protocol.ARMS
CONTROLS = ("correct", "inexact")
LANG = "C.UTF-8"
OWNER_PASS = ("HOME", "PATH", "TMPDIR")
PYTHONPATH = os.pathsep.join(
    str(path)
    for path in (ROOT, ROOT / "src", ROOT / "examples", ROOT / "examples" / "seatlab")
)
SOCKET_PREFIX = "srw-"
ADMITTED = "admitted"
TEAM_STATUS = "team.json"
TEAM_KEYS = {
    "reason",
    "error",
    "native_returncode",
    "native_stopped",
    "listeners",
    "listeners_stopped",
    "reaped",
    "sockets_removed",
}
MAX_TEAM_BYTES = 1 << 16
QUEUE_SECONDS = 3600.0
TEAM_SECONDS = worker.NATIVE_SECONDS + 600.0
TEAM_STOP = ((signal.SIGTERM, 300.0), (signal.SIGKILL, 30.0))
PUBLISHED = 0o444
PRIVATE_LOG = 0o600
EVIDENCE_FAILURES = (
    Bad,
    packet._Invalid,
    ValueError,
    OSError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
    RecursionError,
)
SUMMARY_KEYS = (
    "schema",
    "world",
    "arm",
    "mode",
    "outcome",
    "reason",
    "complete",
    "reward",
    "incomplete_reason",
    "submissions",
    "skipped_reporters",
    "attested_sessions",
    "fully_accounted",
    "native_returncode",
    "elapsed_seconds",
)
assert ARMS == ("all", "stop")
assert len(SEATS) == 4
assert TEAM_STOP[0][1] > worker.NATIVE_STOP[0][1] + worker.NATIVE_STOP[1][1]


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise worker.Refused("invalid_arguments")


@dataclass(frozen=True)
class Plan:
    world: int
    arm: str
    out: Path
    slot: Path | None
    harbor: Path
    python: Path
    agy: Path | None
    control: str | None
    owner_env: dict[str, str]
    native_env: dict[str, str]

    @property
    def mode(self) -> str:
        return "agy" if self.agy is not None else f"control-{self.control}"

    @property
    def order(self) -> tuple[str, str, str, str]:
        return protocol.order(self.world)


def parse(argv: list[str], team: bool) -> argparse.Namespace:
    parser = _Parser(
        prog=f"python3.12 -BS -m {MODULE}",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--harbor-python", type=Path, required=True)
    if not team:
        parser.add_argument("--slot", type=Path, required=True)
    seat = parser.add_mutually_exclusive_group(required=True)
    seat.add_argument("--agy", type=Path)
    seat.add_argument("--control", choices=CONTROLS)
    return parser.parse_args(argv)


def private_parent(out: Path) -> Path:
    info = out.parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise worker.Refused("out_parent_not_private")
    return out


def issued_cell(out: Path) -> Path:
    cell = worker.physical(out, "out_invalid")
    info = cell.lstat()
    if (
        cell != out
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise worker.Refused("out_invalid")
    worker.real_directory(cell / "logs", "out_invalid")
    return cell


def environments(agy: bool) -> tuple[dict[str, str], dict[str, str]]:
    owner = {key: os.environ[key] for key in OWNER_PASS if os.environ.get(key)}
    owner["LANG"] = LANG
    if agy and "HOME" not in owner:
        raise worker.Refused("home_missing")
    native = worker.native_env()
    native.update(PATH=calendar_driver._PATH, LANG=LANG, PYTHONPATH=PYTHONPATH)
    return owner, native


def checked(args: argparse.Namespace, team: bool) -> Plan:
    if not protocol.external_admitted(args.world, args.arm) or args.arm not in ARMS:
        raise worker.Refused("world_arm_pairing")
    if os.name != "posix" or not args.out.is_absolute():
        raise worker.Refused("out_invalid")
    programs = [RELAY, BOOKER, TRIAL, AGY_SEAT if args.agy else EXAMPLE_OWNER]
    if args.control == "inexact":
        programs.append(CONTROL)
    for program in programs:
        worker.regular(program, "seat_program_missing")
    if team:
        out = issued_cell(args.out)
    else:
        out = private_parent(worker.new_cell(args.out))
    owner_env, native_env = environments(args.agy is not None)
    return Plan(
        world=args.world,
        arm=args.arm,
        out=out,
        slot=None if team else worker.executable(args.slot, "slot_invalid"),
        harbor=worker.executable(args.harbor_python, "harbor_python_invalid"),
        python=worker.executable(Path(sys.executable), "python_invalid"),
        agy=None if args.agy is None else worker.executable(args.agy, "agy_invalid"),
        control=args.control,
        owner_env=owner_env,
        native_env=native_env,
    )


def owner_argv(plan: Plan, seat: str) -> list[str]:
    python = [str(plan.python), "-IS"]
    if seat == plan.order[0]:
        return [*python, str(BOOKER), "--arm", plan.arm, "--seat", seat]
    if plan.agy is not None:
        record = plan.out / "owners" / seat
        return [
            *python,
            str(AGY_SEAT),
            "--personal-quota",
            "--agy",
            str(plan.agy),
            "--record",
            str(record),
        ]
    if plan.control == "inexact" and seat == plan.order[1]:
        return [
            *python,
            str(CONTROL),
            "--mode",
            "inexact",
            "--world",
            str(plan.world),
            "--seat",
            seat,
            "--record",
            str(plan.out / "control" / seat),
        ]
    return [*python, str(EXAMPLE_OWNER), "--mode", "correct"]


def spawn(
    argv: list[str], cwd: Path, env: dict[str, str], log: Path
) -> subprocess.Popen[bytes]:
    fd = os.open(log, worker.CREATE, PRIVATE_LOG)
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        os.close(fd)


def start_listener(
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
    return spawn(argv, logs, plan.owner_env, logs / f"relay-{seat}.log")


def await_listeners(listeners: dict[str, subprocess.Popen[bytes]], logs: Path) -> None:
    deadline = time.monotonic() + worker.READY_SECONDS
    for _ in range(int(worker.READY_SECONDS / worker.POLL_SECONDS) + 1):
        waiting = [s for s in SEATS if not worker.said_ready(logs / f"relay-{s}.log")]
        if not waiting:
            return
        if any(listeners[s].poll() is not None for s in waiting):
            raise worker.Refused("listener_not_ready")
        if time.monotonic() >= deadline:
            break
        time.sleep(worker.POLL_SECONDS)
    raise worker.Refused("listener_not_ready")


def admitted(plan: Plan, sockets: Path) -> dict[str, tuple[int, int]]:
    try:
        return external_owner.admit(sockets, out=plan.out / "native")
    except (OSError, ValueError):
        raise worker.Refused("listeners_refused") from None


def launch_native(plan: Plan, sockets: Path, logs: Path) -> subprocess.Popen[bytes]:
    argv = [
        str(plan.harbor),
        "-B",
        "-m",
        TRIAL_MODULE,
        "--world",
        str(plan.world),
        "--arm",
        plan.arm,
        "--sockets",
        str(sockets),
        "--out",
        str(plan.out / "native"),
    ]
    return spawn(argv, ROOT, plan.native_env, logs / "native.log")


def bounded_exit(child: subprocess.Popen[bytes], seconds: float, reason: str) -> int:
    deadline = time.monotonic() + seconds
    for _ in range(int(seconds / worker.POLL_SECONDS) + 1):
        code = child.poll()
        if code is not None:
            return code
        if time.monotonic() >= deadline:
            break
        time.sleep(worker.POLL_SECONDS)
    raise worker.Refused(reason)


def reap(
    native: subprocess.Popen[bytes] | None,
    listeners: dict[str, subprocess.Popen[bytes]],
    status: dict[str, Any],
) -> None:
    status["native_stopped"] = native is not None and worker.stop(
        native, worker.NATIVE_STOP
    )
    deadline = time.monotonic() + worker.SETTLE_SECONDS
    for seat in SEATS:
        if seat in listeners:
            with contextlib.suppress(subprocess.TimeoutExpired):
                listeners[seat].wait(timeout=max(0.0, deadline - time.monotonic()))
    status["listeners_stopped"] = [
        seat
        for seat in SEATS
        if seat in listeners and worker.stop(listeners[seat], worker.RELAY_STOP)
    ]
    status["listeners"] = {
        seat: listeners[seat].returncode if seat in listeners else None
        for seat in SEATS
    }
    children = [*listeners.values(), *([native] if native is not None else [])]
    status["reaped"] = all(child.returncode is not None for child in children)


def release(sockets: Path, identities: dict[str, tuple[int, int]]) -> bool:
    for seat in SEATS:
        path = sockets / f"{seat}.sock"
        with contextlib.suppress(OSError):
            info = path.lstat()
            if (
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.geteuid()
                and identities.get(seat, (info.st_dev, info.st_ino))
                == (info.st_dev, info.st_ino)
            ):
                path.unlink()
    with contextlib.suppress(OSError):
        sockets.rmdir()
    return not os.path.lexists(sockets)


def socket_directory() -> Path:
    sockets = Path(tempfile.mkdtemp(prefix=SOCKET_PREFIX, dir=worker.SOCKET_ROOT))
    return sockets.resolve(strict=True)


def run_team(plan: Plan, logs: Path, status: dict[str, Any]) -> None:
    native: subprocess.Popen[bytes] | None = None
    listeners: dict[str, subprocess.Popen[bytes]] = {}
    identities: dict[str, tuple[int, int]] = {}
    with worker.held():
        sockets = socket_directory()
    try:
        longest = max(len(os.fsencode(sockets / f"{s}.sock")) for s in SEATS)
        if longest > worker.MAX_SOCKET_PATH_BYTES:
            raise worker.Refused("socket_path_too_long")
        for seat in SEATS:
            with worker.held():
                listeners[seat] = start_listener(plan, seat, sockets, logs)
        await_listeners(listeners, logs)
        identities = admitted(plan, sockets)
        with worker.held():
            native = launch_native(plan, sockets, logs)
        status["native_returncode"] = bounded_exit(
            native, worker.NATIVE_SECONDS, "native_timed_out"
        )
    finally:
        with worker.held():
            reap(native, listeners, status)
            status["sockets_removed"] = release(sockets, identities)
    if not status["reaped"]:
        raise worker.Refused("children_unreaped")


def team_main(argv: list[str]) -> int:
    status: dict[str, Any] = {"reason": None, "native_returncode": None}
    logs: Path | None = None
    try:
        plan = checked(parse(argv, team=True), team=True)
        with worker.held():
            worker.create(plan.out / "logs" / ADMITTED, b"", PRIVATE_LOG)
            logs = plan.out / "logs"
        run_team(plan, logs, status)
    except worker.Refused as refusal:
        status["reason"] = refusal.reason
    except worker.Stopped:
        status["reason"] = "interrupted"
    except worker.FAILURES as error:
        status["reason"] = "error"
        status["error"] = type(error).__name__
    if logs is not None:
        with contextlib.suppress(OSError):
            worker.create(logs / TEAM_STATUS, canonical(status) + b"\n", PRIVATE_LOG)
    else:
        print(f"reporter team: {status['reason']}", file=sys.stderr)
    return 0 if status["reason"] is None else 1


def team_argv(plan: Plan) -> list[str]:
    assert plan.slot is not None
    seat = ["--agy", str(plan.agy)] if plan.agy is not None else []
    if plan.control is not None:
        seat = ["--control", plan.control]
    return [
        str(plan.slot),
        str(plan.python),
        "-BS",
        "-m",
        MODULE,
        TEAM,
        "--world",
        str(plan.world),
        "--arm",
        plan.arm,
        "--out",
        str(plan.out),
        "--harbor-python",
        str(plan.harbor),
        *seat,
    ]


def slot_env(plan: Plan) -> dict[str, str]:
    env = dict(plan.owner_env)
    env.update(PYTHONPATH=PYTHONPATH, PYTHONDONTWRITEBYTECODE="1")
    if "DOCKER_HOST" in plan.native_env:
        env["DOCKER_HOST"] = plan.native_env["DOCKER_HOST"]
    return env


def await_admission(team: subprocess.Popen[bytes], logs: Path) -> None:
    deadline = time.monotonic() + QUEUE_SECONDS
    for _ in range(int(QUEUE_SECONDS / worker.POLL_SECONDS) + 1):
        if os.path.lexists(logs / ADMITTED) or team.poll() is not None:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(worker.POLL_SECONDS)
    raise worker.Refused("slot_queue_timeout")


def team_result(logs: Path, status: dict[str, Any]) -> None:
    path = logs / TEAM_STATUS
    if not os.path.lexists(path):
        if not os.path.lexists(logs / ADMITTED):
            raise worker.Refused("slot_refused")
        raise worker.Refused("team_status_missing")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw = os.read(fd, MAX_TEAM_BYTES + 1)
    finally:
        os.close(fd)
    value = json.loads(raw) if len(raw) <= MAX_TEAM_BYTES else None
    if type(value) is not dict or not set(value) <= TEAM_KEYS:
        raise worker.Refused("team_status_invalid")
    reason = value.pop("reason", None)
    status.update(value)
    if reason is not None:
        raise worker.Refused(str(reason))


def run_slot(plan: Plan, logs: Path, status: dict[str, Any]) -> None:
    team: subprocess.Popen[bytes] | None = None
    try:
        with worker.held():
            team = spawn(team_argv(plan), ROOT, slot_env(plan), logs / "team.log")
        await_admission(team, logs)
        status["slot_returncode"] = bounded_exit(team, TEAM_SECONDS, "team_timed_out")
    finally:
        with worker.held():
            status["slot_stopped"] = team is not None and worker.stop(team, TEAM_STOP)
    team_result(logs, status)


def category(error: BaseException) -> str:
    return str(error) if isinstance(error, Bad) else type(error).__name__


def publish(path: Path, row: dict[str, Any]) -> None:
    worker.create(path, canonical(row) + b"\n", PUBLISHED)


def external(plan: Plan, cell: Path, status: dict[str, Any]) -> bool:
    try:
        row = external_evidence.audit(cell, plan.world, plan.arm)
    except EVIDENCE_FAILURES as error:
        status["reason"] = "evidence_failed"
        status["evidence_error"] = category(error)
        return False
    publish(plan.out / "external.json", row)
    seats = row["seats"] or []
    status.update(
        complete=row["complete"],
        reward=row["native_reward"],
        incomplete_reason=row["incomplete_reason"],
        submissions=row["submissions"],
        skipped_reporters=sum(seat["action"] == "skip" for seat in seats),
    )
    return True


def provenance(plan: Plan, cell: Path, status: dict[str, Any]) -> bool:
    try:
        owners = (plan.out / "owners").resolve(strict=True)
        row = owner_provenance.audit(
            cell, plan.world, plan.arm, owners, list(plan.order[1:])
        )
    except EVIDENCE_FAILURES as error:
        status["provenance_error"] = category(error)
        return False
    publish(plan.out / "provenance.json", row)
    status.update(
        attested_sessions=row["attested_sessions"],
        attested_tokens=row["attested_tokens"],
        fully_accounted=row["fully_accounted"],
    )
    return True


def audit(plan: Plan, status: dict[str, Any]) -> None:
    cell = plan.out / "native"
    if not os.path.lexists(cell):
        status["reason"] = "native_missing"
        return
    cell = cell.resolve(strict=True)
    if not external(plan, cell, status):
        return
    attested = plan.agy is None or provenance(plan, cell, status)
    if status["reward"] is None:
        status["reason"] = "native_ungraded"
    elif not attested:
        status["reason"] = "provenance_failed"
    else:
        status["outcome"] = "graded"
        status["reason"] = None


def issue(plan: Plan, status: dict[str, Any]) -> Path:
    worker.private_directory(plan.out)
    status["issued"] = True
    logs = worker.private_directory(plan.out / "logs")
    if plan.agy is not None:
        worker.private_directory(plan.out / "owners")
    if plan.control == "inexact":
        worker.private_directory(plan.out / "control")
    return logs


def emit(status: dict[str, Any], logs: Path | None) -> None:
    if logs is not None:
        with contextlib.suppress(OSError):
            worker.create(logs / "status.json", canonical(status) + b"\n", PRIVATE_LOG)
    summary = {key: status.get(key) for key in SUMMARY_KEYS}
    with contextlib.suppress(OSError):
        sys.stdout.buffer.write(canonical(summary) + b"\n")
        sys.stdout.buffer.flush()


def operator_main(argv: list[str]) -> int:
    started = time.monotonic()
    status: dict[str, Any] = {
        "schema": SCHEMA,
        "outcome": "refused",
        "reason": None,
        "issued": False,
    }
    logs: Path | None = None
    try:
        plan = checked(parse(argv, team=False), team=False)
        status.update(world=plan.world, arm=plan.arm, mode=plan.mode)
        with worker.held():
            logs = issue(plan, status)
        status["outcome"] = "failed"
        run_slot(plan, logs, status)
        audit(plan, status)
    except worker.Refused as refusal:
        status["reason"] = refusal.reason
    except worker.Stopped:
        status["reason"] = "interrupted"
    except worker.FAILURES as error:
        status["reason"] = "error"
        status["error"] = type(error).__name__
    status["elapsed_seconds"] = int(time.monotonic() - started)
    emit(status, logs)
    if not status["issued"]:
        return 2
    return 0 if status["outcome"] == "graded" else 1


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    for signum in worker.SIGNALS:
        signal.signal(signum, worker._interrupt)
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == [TEAM]:
        return team_main(args[1:])
    return operator_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
