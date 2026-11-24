"""Run a bounded four-seat reporter campaign by hand, one cell per command.

python3.12 -BS -m examples.seatlab.fanin.reporter_queue init ROOT --first N --count N \\
  --mode correct|inexact|personal
python3.12 -BS -m examples.seatlab.fanin.reporter_queue status ROOT [--sha256 DIGEST]
python3.12 -BS -m examples.seatlab.fanin.reporter_queue one ROOT --sha256 DIGEST \\
  [--shard I/N] --harbor-python ABS --slot ABS (--control correct|inexact | --agy ABS)

Run it from the checkout root. ROOT is a canonical private directory outside the checkout.
`one` takes the first world of its shard ((world - first) % N == I) whose nonblocking
world lock is free and whose next arm is fresh: the first arm only when both arms are
missing, the second only when the first is reported (also with grade 0). It publishes
ROOT/wW-ARM.issued before it starts reporter_worker into ROOT/wW-ARM, holds the world
lock until the worker exits, and never issues a marked cell again. Worker output stays in
private ROOT/wW-ARM.out and ROOT/wW-ARM.log; ROOT/wW-ARM.exit records the return code.
`--control` must match the registered typed control. `--agy` requires a personal campaign.
The worker gets only HOME, PATH, LANG, TMPDIR and a local DOCKER_HOST. Locks are host-local.

Stdout is one path-free JSON line. Exit 0: initialized, viewed or reported. Exit 1: issued
but unresolved. Exit 2: refused before issuance. Exit 3: no available work in the shard.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..wait_peer import campaign, worker
from . import reporter_ledger as ledger
from . import reporter_worker

SCHEMA = "seatlab.fanin-reporter-queue/v1"
PROG = "python3.12 -BS -m examples.seatlab.fanin.reporter_queue"
LOWER = ("-BS", "-m", "examples.seatlab.fanin.reporter_worker")
LOCK = "queue.lock"
MODES = ledger.MODES
MAX_SHARDS = 64
SUMMARY = ("outcome", "reason", "reward", "complete")
SUMMARY_BYTES = 1 << 16
Refused = worker.Refused


def root_dir(raw: Path) -> Path:
    given = raw.expanduser().absolute()
    resolved = campaign.campaign_dir(given, create=False)
    if resolved != given:
        raise Refused("root_not_canonical")
    return resolved


def registered(root: Path, sha256: str | None) -> dict[str, Any]:
    if sha256 is not None and worker.SHA256.fullmatch(sha256) is None:
        raise Refused("sha256_invalid")
    reg = ledger.load(root, sha256)
    assert type(reg["first"]) is int and type(reg["count"]) is int
    assert reg["mode"] in MODES and 1 <= reg["count"] <= MAX_SHARDS
    assert sha256 is None or reg["sha256"] == sha256
    return reg


def open_lock(root: Path) -> int:
    lock = os.open(root / LOCK, campaign.LOCK_FLAGS, 0o600)
    info = os.fstat(lock)
    if not stat.S_ISREG(info.st_mode) or not campaign.private(0o600, info):
        os.close(lock)
        raise Refused("lock_unsafe")
    return lock


def next_arm(root: Path, reg: dict[str, Any], world: int) -> str | None:
    states = {
        arm: ledger.cell_state(root, reg, world, arm) for arm in ledger.arm_order(world)
    }
    return ledger.eligible(world, states)


def claim(
    root: Path, reg: dict[str, Any], lock: int, shard: tuple[int, int]
) -> tuple[int, str] | None:
    for index in range(reg["count"]):
        if index % shard[1] != shard[0] or not campaign.try_lock(lock, index):
            continue
        world = reg["first"] + index
        arm = next_arm(root, reg, world)
        if arm is not None:
            return world, arm
        fcntl.lockf(lock, fcntl.LOCK_UN, 1, index, os.SEEK_SET)
    return None


def lower_argv(args: argparse.Namespace, world: int, arm: str, out: Path) -> list[str]:
    seat = ("--agy", args.agy) if args.agy is not None else ("--control", args.control)
    flat = ("--world", world, "--arm", arm, "--out", out, *seat)
    paths = ("--harbor-python", args.harbor_python, "--slot", args.slot)
    return [str(item) for item in (*flat, *paths)]


def runnable(args: argparse.Namespace, reg: dict[str, Any]) -> None:
    if reg["mode"] != ("personal" if args.agy is not None else args.control):
        raise Refused("mode_mismatch")
    worker.executable(Path(sys.executable), "python_invalid")
    worker.executable(args.harbor_python, "harbor_python_invalid")
    worker.executable(args.slot, "slot_invalid")
    if args.agy is not None:
        worker.executable(args.agy, "agy_invalid")
    worker.native_env()


def private_log(path: Path, stack: contextlib.ExitStack) -> int:
    fd = os.open(path, worker.CREATE, 0o600)
    stack.callback(os.close, fd)
    return fd


def spawn(root: Path, cell: str, tail: list[str]) -> subprocess.Popen[bytes]:
    env = {key: os.environ[key] for key in campaign.CHILD_ENV if os.environ.get(key)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with contextlib.ExitStack() as stack:
        return subprocess.Popen(
            [sys.executable, *LOWER, *tail],
            cwd=worker.ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=private_log(root / f"{cell}.out", stack),
            stderr=private_log(root / f"{cell}.log", stack),
            start_new_session=True,
        )


def launch(root: Path, cell: str, tail: list[str]) -> int | None:
    record: dict[str, Any] = {"returncode": None, "spawned": False, "terminated": False}
    try:
        if campaign.STATE["signal"] is None:
            child = spawn(root, cell, tail)
            record["spawned"] = True
            record["returncode"], record["terminated"] = campaign.supervise(child)
    finally:
        with contextlib.suppress(OSError):
            campaign.publish(root, f"{cell}.exit", worker.canonical(record) + b"\n")
    return record["returncode"]


def clean(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, str) and worker.WORD.fullmatch(value):
        return value
    return "redacted"


def summary(root: Path, cell: str) -> dict[str, Any] | None:
    try:
        raw = campaign.owned(root / f"{cell}.out", SUMMARY_BYTES)
        row = json.loads(raw.splitlines()[-1])
    except (Refused, OSError, ValueError, IndexError):
        return None
    if not isinstance(row, dict):
        return None
    picked = {key: clean(row.get(key)) for key in SUMMARY}
    picked["incomplete_reason"] = ledger.stock_reason(row.get("incomplete_reason"))
    return picked


def one(args: argparse.Namespace, out: dict[str, Any]) -> int:
    root = root_dir(args.root)
    reg = registered(root, args.sha256)
    out["registration"] = reg
    runnable(args, reg)
    lock = open_lock(root)
    try:
        picked = claim(root, reg, lock, args.shard)
        if picked is None:
            out["work"] = "no_available"
            return 3
        world, arm = picked
        cell = campaign.name(world, arm)
        tail = lower_argv(args, world, arm, root / cell)
        reporter_worker.checked(reporter_worker.parse(tail, team=False), team=False)
        if campaign.STATE["signal"] is not None:
            raise Refused("interrupted")
        out.update(world=world, arm=arm, cell=cell)
        campaign.publish(root, f"{cell}.issued", ledger.issue_bytes(reg, world, arm))
        out["returncode"] = launch(root, cell, tail)
        out["summary"] = summary(root, cell)
        out["state"] = ledger.cell_state(root, reg, world, arm)
    finally:
        os.close(lock)
    reported = out["state"] == "reported"
    out["work"] = "reported" if reported else "unresolved"
    return 0 if reported else 1


def shard(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", text)
    if match is None or not 0 <= int(match[1]) < int(match[2]) <= MAX_SHARDS:
        raise argparse.ArgumentTypeError("shard")
    return int(match[1]), int(match[2])


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = worker._Parser(
        prog=PROG,
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", allow_abbrev=False)
    view = commands.add_parser("status", allow_abbrev=False)
    run = commands.add_parser("one", allow_abbrev=False)
    for command in (init, view, run):
        command.add_argument("root", type=Path)
    init.add_argument("--first", type=int, required=True)
    init.add_argument("--count", type=int, required=True)
    init.add_argument("--mode", choices=MODES, required=True)
    view.add_argument("--sha256")
    run.add_argument("--sha256", required=True)
    run.add_argument("--shard", type=shard, default=(0, 1))
    run.add_argument("--harbor-python", type=Path, required=True)
    run.add_argument("--slot", type=Path, required=True)
    seat = run.add_mutually_exclusive_group(required=True)
    seat.add_argument("--control", choices=reporter_worker.CONTROLS)
    seat.add_argument("--agy", type=Path)
    return parser.parse_args(argv)


def command(args: argparse.Namespace, out: dict[str, Any]) -> int:
    out["command"] = args.command
    if args.command == "init":
        root = args.root.expanduser().absolute()
        out["registration"] = ledger.initialize(root, args.first, args.count, args.mode)
        out["work"] = "initialized"
        return 0
    if args.command == "status":
        root = root_dir(args.root)
        out["registration"] = registered(root, args.sha256)
        out["status"] = ledger.status(root, out["registration"])
        out["work"] = "view"
        return 0
    return one(args, out)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    for signum in worker.SIGNALS:
        signal.signal(signum, campaign._signalled)
    out: dict[str, Any] = {"schema": SCHEMA, "command": None, "work": "refused"}
    out["reason"] = None
    code = 2
    try:
        code = command(parse(argv), out)
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
