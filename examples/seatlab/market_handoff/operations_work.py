from __future__ import annotations

import argparse
import errno
import fcntl
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import decisions, operations_cell, operations_grade, simulator
from .simulator import SimulatorError

SPLITS = operations_grade.SPLITS
CALIBRATE, HOLDOUT = SPLITS
WORLDS = operations_grade.WORLDS
LOCK_NAME = "worlds.lock"
FREEZE_NAME = "freeze.json"
ROW_GLOB = "*.operations.json"
BUSY_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN})
FAILURES = (SimulatorError, ValueError, OSError, OverflowError, RecursionError,
            subprocess.SubprocessError)
OUTCOMES = {"computed": "computed", "complete": "already_complete", "busy": "busy"}
LIMITATIONS = (
    "Local trusted filesystem with POSIX record locks; storage is not adversarial. "
    "Layout: CAMPAIGN/calibrate, CAMPAIGN/holdout, CAMPAIGN/freeze.json."
)
REMAINING = "absent rows; status does not observe whether a worker is computing them"
Record = dict[str, Any]

assert operations_cell.SPLITS == SPLITS and simulator.WORLDS == WORLDS == 12
assert operations_cell.filename(HOLDOUT, 3) == "holdout-03.operations.json"


def expected(split: str) -> list[str]:
    assert split in SPLITS
    return [operations_cell.filename(split, world) for world in range(WORLDS)]


def unexpected(directory: Path, split: str) -> list[str]:
    found = {path.name for path in directory.glob(ROW_GLOB)}
    return sorted(found - set(expected(split)))


def store(path: Path, data: bytes) -> str:
    assert data.endswith(b"\n")
    try:
        return simulator.write_once(path, data)
    except FileExistsError:
        return simulator.write_once(path, data)


def present(directory: Path, split: str, world: int) -> bool:
    if not os.path.lexists(directory / operations_cell.filename(split, world)):
        return False
    operations_grade.read_row(directory, split, world)
    return True


def require_seal(campaign: Path) -> str:
    calibrate, path = campaign / CALIBRATE, campaign / FREEZE_NAME
    if not os.path.lexists(path):
        raise SimulatorError(f"{path}: holdout needs an existing freeze; run freeze first")
    if path.is_symlink() or not path.is_file():
        raise SimulatorError(f"{path}: freeze is not a regular file")
    operations_grade.separate(calibrate, campaign / HOLDOUT)
    _, sha = operations_grade.sealed(calibrate, path)
    return sha


def prepare(campaign: Path, split: str) -> Path:
    directory = campaign / split
    campaign.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise SimulatorError(f"{directory}: split directory is a symlink")
    directory.mkdir(exist_ok=True)
    if not directory.is_dir():
        raise SimulatorError(f"{directory}: split path is not a directory")
    operations_cell.require_one_split(directory, split)
    stray = unexpected(directory, split)
    if stray:
        raise SimulatorError(f"{directory}: unexpected row files {stray}")
    return directory


def open_lock(directory: Path) -> int:
    path = directory / LOCK_NAME
    if path.is_symlink() or (os.path.lexists(path) and not path.is_file()):
        raise SimulatorError(f"{path}: lock path is not a regular file")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as error:
        raise SimulatorError(f"{path}: cannot open lock file: {error}") from error
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise SimulatorError(f"{path}: lock path is not a regular file")
    return fd


def claim(fd: int, world: int) -> bool:
    assert fd >= 0 and 0 <= world < WORLDS
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, world, os.SEEK_SET)
    except OSError as error:
        if error.errno in BUSY_ERRNOS:
            return False
        raise
    return True


def compute(directory: Path, split: str, world: int, upstream: Path,
            stock: list[SimpleNamespace]) -> None:
    assert len(stock) <= 1
    if not stock:
        stock.append(simulator.load_stock(upstream))
    data = simulator.encode(operations_cell.run_cell(stock[0], split, world))
    store(directory / operations_cell.filename(split, world), data)
    operations_grade.read_row(directory, split, world)


def visit(fd: int, directory: Path, split: str, world: int, upstream: Path,
          stock: list[SimpleNamespace]) -> str:
    if present(directory, split, world):
        return "complete"
    if not claim(fd, world):
        return "busy"
    try:
        if present(directory, split, world):
            return "complete"
        compute(directory, split, world, upstream, stock)
        return "computed"
    finally:
        fcntl.lockf(fd, fcntl.LOCK_UN, 1, world, os.SEEK_SET)


def work(upstream: Path, campaign: Path, split: str, report: Record) -> None:
    limit = report["limit"]
    assert split in SPLITS and type(limit) is int and 1 <= limit <= WORLDS
    if split == HOLDOUT:
        report["freeze_sha256"] = require_seal(campaign)
    directory = prepare(campaign, split)
    stock: list[SimpleNamespace] = []
    fd = open_lock(directory)
    world = 0
    try:
        while world < WORLDS and len(report["computed"]) < limit:
            outcome = visit(fd, directory, split, world, upstream, stock)
            report[OUTCOMES[outcome]].append(world)
            world += 1
        report["unvisited"] = list(range(world, WORLDS))
    except BaseException:
        report["failed"] = [world]
        report["unvisited"] = list(range(world + 1, WORLDS))
        raise
    finally:
        os.close(fd)
    visited = sum(len(report[key]) for key in OUTCOMES.values())
    assert visited + len(report["unvisited"]) == WORLDS
    assert len(report["computed"]) <= limit and len(stock) <= 1


def freeze(campaign: Path) -> Record:
    calibrate, path = campaign / CALIBRATE, campaign / FREEZE_NAME
    derived = operations_grade.freeze(calibrate)
    if not os.path.lexists(path) and next((campaign / HOLDOUT).glob(ROW_GLOB), None):
        raise SimulatorError(f"{campaign / HOLDOUT}: holdout rows exist before any seal")
    text = decisions.canonical(derived)
    status = store(path, (text + "\n").encode("utf-8"))
    _, sha = operations_grade.sealed(calibrate, path)
    assert sha == decisions.sha(text)
    return {"command": "freeze", "campaign": str(campaign), "file": str(path),
            "status": status, "freeze_sha256": sha, "fixed": derived["fixed"]["label"]}


def seal_status(campaign: Path) -> Record:
    path = campaign / FREEZE_NAME
    if not os.path.lexists(path):
        return {"state": "missing"}
    if path.is_symlink() or not path.is_file():
        return {"state": "invalid", "error": f"{path}: not a regular file"}
    try:
        _, sha = operations_grade.sealed(campaign / CALIBRATE, path)
    except FAILURES as error:
        return {"state": "invalid", "error": str(error)}
    return {"state": "valid", "freeze_sha256": sha}


def lock_status(directory: Path) -> str:
    path = directory / LOCK_NAME
    if not os.path.lexists(path):
        return "absent"
    return "regular" if stat.S_ISREG(os.lstat(path).st_mode) else "not regular"


def split_status(campaign: Path, split: str) -> Record:
    directory = campaign / split
    if not os.path.lexists(directory):
        return {"directory": "absent", "completed": [], "invalid": {},
                "remaining": list(range(WORLDS)), "unexpected": [], "lock": "absent"}
    if directory.is_symlink() or not directory.is_dir():
        return {"directory": "not a directory", "completed": [], "invalid": {},
                "remaining": [], "unexpected": [], "lock": "absent"}
    completed: list[int] = []
    invalid: dict[str, str] = {}
    remaining: list[int] = []
    for world in range(WORLDS):
        try:
            (completed if present(directory, split, world) else remaining).append(world)
        except FAILURES as error:
            invalid[str(world)] = str(error)
    assert len(completed) + len(invalid) + len(remaining) == WORLDS
    return {"directory": "present", "completed": completed, "invalid": invalid,
            "remaining": remaining, "unexpected": unexpected(directory, split),
            "lock": lock_status(directory)}


def status(campaign: Path) -> Record:
    seal = seal_status(campaign)
    splits = {split: split_status(campaign, split) for split in SPLITS}
    held = splits[HOLDOUT]
    opened = bool(held["completed"] or held["invalid"] or held["unexpected"])
    done = all(len(item["completed"]) == WORLDS and not item["unexpected"]
               for item in splits.values())
    return {"command": "status", "campaign": str(campaign), "seal": seal, "splits": splits,
            "remaining_means": REMAINING,
            "holdout_rows_without_valid_seal": opened and seal["state"] != "valid",
            "complete": done and seal["state"] == "valid"}


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.market_handoff.operations_work",
        description=LIMITATIONS)
    commands = parser.add_subparsers(dest="command", required=True)
    working = commands.add_parser("work")
    working.add_argument("upstream_root", type=Path)
    working.add_argument("campaign", type=Path)
    working.add_argument("--split", required=True, choices=SPLITS)
    working.add_argument("--max-worlds", type=int, default=1,
                         choices=range(1, WORLDS + 1), metavar="1..12")
    commands.add_parser("freeze").add_argument("campaign", type=Path)
    commands.add_parser("status").add_argument("campaign", type=Path)
    return parser.parse_args(argv)


def run_work(args: argparse.Namespace) -> Record:
    root, campaign = simulator.outside_upstream(args.upstream_root, args.campaign)
    report: Record = {"command": "work", "split": args.split, "campaign": str(campaign),
                      "limit": args.max_worlds, "computed": [], "already_complete": [],
                      "busy": [], "unvisited": list(range(WORLDS))}
    try:
        work(root, campaign, args.split, report)
    except FAILURES as error:
        raise SystemExit(
            f"market_handoff.operations_work: {error}; partial {simulator.canonical(report)}"
        ) from error
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.command == "work":
        report = run_work(args)
    else:
        campaign = args.campaign.resolve()
        try:
            report = freeze(campaign) if args.command == "freeze" else status(campaign)
        except FAILURES as error:
            raise SystemExit(f"market_handoff.operations_work: {error}") from error
    print(simulator.canonical(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
