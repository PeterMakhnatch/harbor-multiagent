from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import Any, cast

from . import protocol, worker

SCHEMA = "seatlab.wait-peer-campaign/v1"
ISSUE_SCHEMA = "seatlab.wait-peer-issue/v1"
RESULT_SCHEMA = "seatlab.wait-peer-campaign-result/v1"
STATUS_SCHEMA = "seatlab.wait-peer-campaign-status/v1"
WORLDS, ARMS = tuple(worker.WORLDS), protocol.ARMS
TAGS = (worker.LIVE_TAG, *worker.CONTROL_TAGS.values())
REGISTRATION, LOCK, RECEIPT = "campaign.json", "campaign.lock", "receipt.json"
STATES = ("complete", "incomplete", "issued", "unmarked", "mismatch", "missing")
BUSY = frozenset({errno.EACCES, errno.EAGAIN})
CHILD_ENV = ("HOME", "PATH", "LANG", "TMPDIR", "DOCKER_HOST")
WORKER = ("-BS", "-m", "examples.seatlab.wait_peer.worker")
MAX_BYTES = 1 << 22
CHILD_SECONDS = 8100.0
GRACE_SECONDS = 300.0
POLL_SECONDS = 1.0
REGISTER_TRIES = 100
READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
STATE: dict[str, int | None] = {"signal": None}
Refused = worker.Refused

assert len(WORLDS) == protocol.COUNT == 8 and len(ARMS) == 3


def _signalled(signum: int, frame: FrameType | None) -> None:
    STATE["signal"] = signum


def name(world: int, arm: str) -> str:
    return f"w{world}-{arm}"


def owned(path: Path, limit: int = MAX_BYTES) -> bytes:
    with open(path, "rb", opener=lambda raw, _: os.open(raw, READ)) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise Refused("state_unsafe")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise Refused("state_unsafe")
    return data


def publish(campaign: Path, filename: str, data: bytes) -> None:
    worker.create(campaign / filename, data, 0o400)
    fd = os.open(campaign, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private(mode: int, info: os.stat_result) -> bool:
    return info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == mode


def campaign_dir(raw: Path, create: bool) -> Path:
    path = raw.expanduser().absolute()
    if create:
        with contextlib.suppress(FileExistsError):
            path.mkdir(mode=0o700)
            path.chmod(0o700)
    try:
        info, resolved = path.lstat(), path.resolve(strict=True)
    except OSError:
        raise Refused("campaign_unsafe") from None
    if not stat.S_ISDIR(info.st_mode) or not private(0o700, info):
        raise Refused("campaign_unsafe")
    if resolved.is_relative_to(worker.ROOT):
        raise Refused("campaign_inside_repository")
    return resolved


def registration(tag: str, sha256: str) -> bytes:
    record = {"schema": SCHEMA, "tag": tag, "sha256": sha256}
    return worker.canonical(record) + b"\n"


def issue(reg: dict[str, str], world: int, arm: str) -> bytes:
    record = {"schema": ISSUE_SCHEMA, "world": world, "arm": arm, **reg}
    return worker.canonical(record) + b"\n"


def registered(campaign: Path) -> dict[str, str] | None:
    path = campaign / REGISTRATION
    if not os.path.lexists(path):
        return None
    raw = owned(path)
    try:
        value = json.loads(raw)
        tag, sha256 = value["tag"], value["sha256"]
        valid = tag in TAGS and worker.SHA256.fullmatch(sha256) is not None
        if not valid or raw != registration(tag, sha256):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise Refused("registration_invalid") from None
    return {"tag": tag, "sha256": sha256}


def cell_state(campaign: Path, reg: dict[str, str] | None, world: int, arm: str) -> str:
    cell = campaign / name(world, arm)
    marker = campaign / f"{cell.name}.issued"
    marked = os.path.lexists(marker)
    try:
        if marked and (reg is None or owned(marker) != issue(reg, world, arm)):
            return "mismatch"
        if not os.path.lexists(cell):
            return "issued" if marked else "missing"
        info = cell.lstat()
        if reg is None or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            return "mismatch"
        if not os.path.lexists(cell / RECEIPT):
            return "issued" if marked else "unmarked"
        plan = cast(worker.Plan, SimpleNamespace(world=world, arm=arm, tag=reg["tag"]))
        raw = owned(cell / RECEIPT, worker.MAX_RECEIPT_BYTES)
        receipt = worker.checked_receipt(plan, raw)
        inputs = receipt.get("inputs")
        if not isinstance(inputs, dict) or inputs.get("manifest") != reg["sha256"]:
            return "mismatch"
    except (Refused, OSError):
        return "mismatch"
    return "complete" if receipt["complete"] is True else "incomplete"


def world_view(
    campaign: Path, reg: dict[str, str] | None, world: int
) -> tuple[dict[str, str], str | None]:
    order = protocol.ordered_arms(world)
    states = {arm: cell_state(campaign, reg, world, arm) for arm in order}
    for index, arm in enumerate(order):
        if states[arm] != "complete":
            fresh = all(states[later] == "missing" for later in order[index:])
            return states, arm if fresh else None
    return states, None


def exit_code(campaign: Path, cell: str) -> Any:
    try:
        return json.loads(owned(campaign / f"{cell}.exit"))["returncode"]
    except (Refused, OSError, ValueError, KeyError, TypeError):
        return "invalid"


def status(campaign: Path) -> dict[str, Any]:
    reg = registered(campaign)
    cells: dict[str, str] = {}
    backlog: list[str] = []
    blocked: list[int] = []
    for world in WORLDS:
        states, arm = world_view(campaign, reg, world)
        cells.update({name(world, each): state for each, state in states.items()})
        if arm is not None:
            backlog.append(name(world, arm))
        elif set(states.values()) != {"complete"}:
            blocked.append(world)
    done = [cell for cell, state in cells.items() if state == "complete"]
    exits = [cell for cell in cells if os.path.lexists(campaign / f"{cell}.exit")]
    return {
        "schema": STATUS_SCHEMA,
        "registration": reg,
        "cells": cells,
        "counts": {state: list(cells.values()).count(state) for state in STATES},
        "coverage": {arm: sum(c.endswith(f"-{arm}") for c in done) for arm in ARMS},
        "worlds_complete": [w for w in WORLDS if all(name(w, a) in done for a in ARMS)],
        "backlog": backlog,
        "blocked": blocked,
        "exits": {cell: exit_code(campaign, cell) for cell in exits},
    }


def try_lock(fd: int, index: int) -> bool:
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, index, os.SEEK_SET)
    except OSError as error:
        if error.errno in BUSY:
            return False
        raise
    return True


def register(campaign: Path, lock: int, want: bytes) -> None:
    for _ in range(REGISTER_TRIES):
        if try_lock(lock, len(WORLDS)):
            break
        time.sleep(0.05)
    else:
        raise Refused("campaign_busy")
    try:
        if not os.path.lexists(campaign / REGISTRATION):
            publish(campaign, REGISTRATION, want)
        if owned(campaign / REGISTRATION) != want:
            raise Refused("registration_mismatch")
    finally:
        fcntl.lockf(lock, fcntl.LOCK_UN, 1, len(WORLDS), os.SEEK_SET)


def prepare(args: argparse.Namespace) -> tuple[Path, dict[str, str], int]:
    if worker.SHA256.fullmatch(args.sha256) is None:
        raise Refused("sha256_invalid")
    tasks = worker.physical(args.tasks, "tasks_invalid")
    args.tasks = worker.real_directory(tasks, "tasks_invalid")
    campaign = campaign_dir(args.campaign, create=True)
    if campaign.is_relative_to(args.tasks) or args.tasks.is_relative_to(campaign):
        raise Refused("campaign_overlaps_tasks")
    args.manifest = args.manifest.absolute()
    if hashlib.sha256(owned(args.manifest)).hexdigest() != args.sha256:
        raise Refused("manifest_digest_mismatch")
    tag = worker.LIVE_TAG if args.agy is not None else worker.CONTROL_TAGS[args.control]
    lock = os.open(campaign / LOCK, LOCK_FLAGS, 0o600)
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or not private(0o600, info):
        raise Refused("lock_unsafe")
    register(campaign, lock, registration(tag, args.sha256))
    return campaign, {"tag": tag, "sha256": args.sha256}, lock


def claim(
    shard: tuple[int, int], campaign: Path, reg: dict[str, str], lock: int
) -> tuple[int, str] | None:
    for index, world in enumerate(WORLDS):
        if index % shard[1] != shard[0] or not try_lock(lock, index):
            continue
        _, arm = world_view(campaign, reg, world)
        if arm is not None:
            return world, arm
        fcntl.lockf(lock, fcntl.LOCK_UN, 1, index, os.SEEK_SET)
    return None


def child_argv(args: argparse.Namespace, world: int, arm: str, cell: Path) -> list[str]:
    seat = ("--agy", args.agy) if args.agy is not None else ("--control", args.control)
    options = (
        ("--sha256", args.sha256, "--world", world, "--arm", arm, "--out", cell),
        ("--slot", args.slot, "--harbor-python", args.harbor_python, *seat),
    )
    flat = [str(item) for group in options for item in group]
    return [str(args.tasks), str(args.manifest), *flat]


def supervise(child: subprocess.Popen[bytes]) -> tuple[int | None, bool]:
    deadline = time.monotonic() + CHILD_SECONDS
    termed: float | None = None
    for _ in range(int((CHILD_SECONDS + GRACE_SECONDS) / POLL_SECONDS) + 2):
        late = STATE["signal"] is not None or time.monotonic() >= deadline
        if termed is None and late:
            with contextlib.suppress(ProcessLookupError):
                child.send_signal(signal.SIGTERM)
            termed = time.monotonic()
        with contextlib.suppress(subprocess.TimeoutExpired):
            return child.wait(timeout=POLL_SECONDS), termed is not None
        if termed is not None and time.monotonic() >= termed + GRACE_SECONDS:
            break
    return child.poll(), termed is not None


def launch(campaign: Path, cell: str, tail: list[str]) -> int | None:
    record: dict[str, Any] = {"returncode": None, "spawned": False, "terminated": False}
    env = {key: os.environ[key] for key in CHILD_ENV if os.environ.get(key)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        if STATE["signal"] is None:
            log = os.open(campaign / f"{cell}.log", worker.CREATE, 0o600)
            try:
                child = subprocess.Popen(
                    [sys.executable, *WORKER, *tail],
                    cwd=worker.ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                os.close(log)
            record["spawned"] = True
            record["returncode"], record["terminated"] = supervise(child)
    finally:
        with contextlib.suppress(OSError):
            publish(campaign, f"{cell}.exit", worker.canonical(record) + b"\n")
    return record["returncode"]


def one(args: argparse.Namespace, out: dict[str, Any]) -> int:
    campaign, reg, lock = prepare(args)
    picked = claim(args.shard, campaign, reg, lock)
    if picked is None:
        out["work"] = "no_available"
        return 3
    world, arm = picked
    cell = campaign / name(world, arm)
    tail = child_argv(args, world, arm, cell)
    worker.checked(worker.parse(tail))
    worker.native_env()
    if STATE["signal"] is not None:
        raise Refused("interrupted")
    out["cell"] = cell.name
    publish(campaign, f"{cell.name}.issued", issue(reg, world, arm))
    out["returncode"] = launch(campaign, cell.name, tail)
    out["state"] = cell_state(campaign, reg, world, arm)
    complete = out["returncode"] == 0 and out["state"] == "complete"
    out["work"] = "complete" if complete else "unresolved"
    return 0 if complete else 1


def shard(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"([0-7])/([1-8])", text)
    if match is None or int(match[1]) >= int(match[2]):
        raise argparse.ArgumentTypeError("shard")
    return int(match[1]), int(match[2])


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = worker._Parser(
        prog="python3.12 -BS -m examples.seatlab.wait_peer.campaign", allow_abbrev=False
    )
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("status", allow_abbrev=False)
    run = commands.add_parser("one", allow_abbrev=False)
    for command in (view, run):
        command.add_argument("campaign", type=Path)
    for option in ("--tasks", "--manifest", "--slot", "--harbor-python"):
        run.add_argument(option, type=Path, required=True)
    run.add_argument("--sha256", required=True)
    run.add_argument("--shard", type=shard, default=(0, 1))
    seat = run.add_mutually_exclusive_group(required=True)
    seat.add_argument("--agy", type=Path)
    seat.add_argument("--control", choices=tuple(worker.CONTROL_TAGS))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for signum in worker.SIGNALS:
        signal.signal(signum, _signalled)
    out: dict[str, Any] = {"schema": RESULT_SCHEMA, "work": "refused"}
    out.update(dict.fromkeys(("returncode", "cell", "state", "reason")))
    code = 2
    try:
        args = parse(argv)
        if args.command == "status":
            out, code = status(campaign_dir(args.campaign, create=False)), 0
        else:
            code = one(args, out)
    except Refused as refusal:
        out["reason"] = refusal.reason
    except worker.FAILURES as error:
        out["reason"] = f"error:{type(error).__name__}"
    if out.get("cell") is not None and out["work"] == "refused":
        out["work"], code = "unresolved", 1
    sys.stdout.buffer.write(worker.canonical(out) + b"\n")
    sys.stdout.buffer.flush()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
