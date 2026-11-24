from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import simulator
from examples.seatlab.market_handoff.timing_study import native_env, store
from examples.seatlab.report_budget import domain, stage

ROOT = Path(__file__).resolve().parents[3]
ISSUE = "seatlab.report-budget-issue/v1"
NATIVE = "examples.seatlab.report_budget.native"
NUMPY = "2.1.3"
CLAIMED = 3

if domain.CONTROL_WORLD != domain.WORLD_COUNT or tuple(domain.TARGETS) != (100, 160):
    raise RuntimeError("report-budget registration changed")


class Claimed(Exception):
    pass


def slots() -> list[tuple[int, int]]:
    return [
        (world, target)
        for world in range(domain.WORLD_COUNT)
        for target in domain.TARGETS
    ]


def cell(root: Path, world: int, target: int) -> Path:
    if type(world) is not int or not 0 <= world <= domain.CONTROL_WORLD:
        raise ValueError(f"world must be an int in 0..{domain.CONTROL_WORLD}")
    if target not in domain.TARGETS or type(target) is not int:
        raise ValueError(f"target must be one of {domain.TARGETS}")
    root = root.resolve(strict=True)
    info = root.stat()
    if not root.is_dir() or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError("use an owner-only directory outside the repository")
    if root == ROOT or ROOT in root.parents:
        raise ValueError(
            "store native inputs and subscription receipts outside the repository"
        )
    return root / f"w{world:02d}-t{target:03d}"


def executable(raw: Path, what: str) -> Path:
    path = Path(raw).expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"{what} must be an executable file")
    return path


def issue(stock: Any, target: Path, args: argparse.Namespace) -> dict[str, Any]:
    native_python = executable(args.native_python, "native Python")
    slot = executable(args.slot, "the Docker slot wrapper")
    client = None if args.control else executable(args.client, "trusted agy")
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise Claimed(target.name) from None
    store(
        target / "issued.json",
        {
            "schema": ISSUE,
            "world": args.world,
            "target": args.target,
            "control": args.control,
        },
    )
    try:
        _, binding = domain.make_company(stock, args.world)
    except domain.CompanyEndedBeforeDecision as exc:
        store(target / "predecision.json", exc.record)
        return {"cell": target.name, "predecision": True, "returncode": None}
    store(target / "company.json", binding)
    config = stage.stage(target / "native", binding, args.target, args.control, client)
    with (target / "native.log").open("xb") as log:
        proc = subprocess.run(
            [str(slot), str(native_python), "-B", "-m", NATIVE, str(config)],
            cwd=ROOT,
            env=native_env(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    store(target / "native-exit.json", {"returncode": proc.returncode})
    return {"cell": target.name, "predecision": False, "returncode": proc.returncode}


def inspect_cell(stock: Any, root: Path, world: int, target: int) -> dict[str, Any]:
    from . import evidence

    control = world == domain.CONTROL_WORLD
    return evidence.collect(stock, cell(root, world, target), world, target, control)


def export(stock: Any, root: Path) -> list[dict[str, Any]]:
    rows = [inspect_cell(stock, root, world, target) for world, target in slots()]
    if len(rows) != 2 * domain.WORLD_COUNT:
        raise AssertionError("export must hold every registered slot")
    return rows


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run once, inspect, or export the native sales-report budget comparison."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "inspect", "export"):
        sub = commands.add_parser(name)
        sub.add_argument("--stock", type=Path, required=True)
        sub.add_argument("--root", type=Path, required=True)
        if name == "export":
            continue
        sub.add_argument(
            "--world", type=int, choices=range(domain.CONTROL_WORLD + 1), required=True
        )
        sub.add_argument("--target", type=int, choices=domain.TARGETS, required=True)
        sub.add_argument("--control", action="store_true")
        if name == "run":
            sub.add_argument("--native-python", type=Path, required=True)
            sub.add_argument("--slot", type=Path, required=True)
            sub.add_argument("--client", type=Path)
    args = parser.parse_args(argv)
    if args.command == "export":
        return args
    if args.control != (args.world == domain.CONTROL_WORLD):
        parser.error(
            f"--control is mandatory exactly for company {domain.CONTROL_WORLD}"
        )
    if args.command == "run" and args.control and args.client is not None:
        parser.error("a control never uses a model client")
    if args.command == "run" and not args.control and args.client is None:
        parser.error("a live run requires --client")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    os.umask(0o077)
    stock = simulator.load_stock(args.stock.resolve(strict=True))
    if stock.numpy_version != NUMPY:
        raise ValueError(f"the registered experiment requires NumPy {NUMPY}")
    if args.command == "export":
        print(json.dumps(export(stock, args.root), sort_keys=True, allow_nan=False))
        return 0
    if args.command == "run":
        try:
            outcome = issue(stock, cell(args.root, args.world, args.target), args)
        except Claimed as exc:
            print(f"{exc} is already claimed; nothing reissued", file=sys.stderr)
            return CLAIMED
        print(json.dumps(outcome, sort_keys=True, allow_nan=False))
        return 0
    row = inspect_cell(stock, args.root, args.world, args.target)
    print(json.dumps(row, sort_keys=True, allow_nan=False))
    return 0 if row["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
