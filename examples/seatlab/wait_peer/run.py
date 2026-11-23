from __future__ import annotations

import argparse
import os
import runpy
import stat
import sys
from pathlib import Path
from typing import Any

from ..calendar_notices import heldout, transfer_registration
from ..market_handoff import native_stage
from ..visibility import calendar_driver
from ..visibility import calendar_packet as packet
from . import cohort, protocol

REGISTRATION_SCHEMA = "seatlab.wait-peer-registration/v1"
LAUNCH_SCHEMA = "seatlab.wait-peer-launch/v1"
SEAT_IMPORT = "examples.seatlab.wait_peer.seat:PeerSeat"
ARMS = ("auto", "neutral", "remind")
TAGS = ("live", "control-ok", "control-wrong")
SEATS = ("p1", "p2")
READ_ONLY = 0o444


def trial_name(world: int, arm: str, tag: str) -> str:
    if type(world) is not int or world < 0 or arm not in ARMS or tag not in TAGS:
        raise ValueError("invalid wait-peer trial identity")
    return f"swr-{world:03d}-{arm}-{tag}"


def trial_config(
    out: Path, task: Path, world: int, arm: str, tag: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task": {"path": str(task), "git_url": None, "name": None},
        "trial_name": trial_name(world, arm, tag),
        "trials_dir": str(out / "trials"),
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
                "kwargs": {"world": world, "arm": arm, "seat": seat},
            }
            for seat in SEATS
        ],
    }


def checked_sockets(sockets: Path, out: Path, tasks: Path) -> Path:
    if not sockets.is_absolute() or sockets.resolve() != sockets:
        raise ValueError("socket directory must be a physical absolute path")
    info = sockets.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
    ):
        raise ValueError(
            "socket directory must be a private 0700 directory owned by this user"
        )
    for a, b in ((sockets, out), (out, sockets), (sockets, tasks), (tasks, sockets)):
        if a.is_relative_to(b):
            raise ValueError("socket, task and output directories must be disjoint")
    return sockets


def content_bind(
    tasks: Path, selected: heldout._Cohort, rows: cohort.Rows, world: int
) -> dict[str, Any]:
    if type(world) is not int or not selected.first <= world <= selected.last:
        raise ValueError("world is outside the operator cohort")
    root = transfer_registration._schedule(tasks, selected)
    name = selected.names()[world]
    task = root / name
    calendar, semantics = rows[world - selected.first]
    source, tables = heldout._checked_stock(
        task, world, name, selected, expected_semantics_sha256=semantics
    )
    if heldout._calendar_digest(tables) != calendar:
        raise ValueError("task differs from the registered calendar identity")
    return {
        "cohort": selected,
        "task": task,
        "task_name": name,
        "task_checksum": source.native_sha256,
        "calendar_sha256": calendar,
        "stock_semantics_sha256": semantics,
    }


def verified_world(
    tasks: Path, manifest: Path, sha256: str, world: int
) -> dict[str, Any]:
    selected, rows = cohort.load(manifest, sha256)
    bound = content_bind(tasks, selected, rows, world)
    cohort.verify_origin(selected, sha256)
    name, checksum = bound["task_name"], bound["task_checksum"]
    if calendar_driver._stock(bound["task"], checksum) != f"flotilla/{name}":
        raise ValueError("stock task name differs from the registered world")
    raw = transfer_registration._read_manifest(manifest, sealed=False)
    if packet._digest(raw) != sha256:
        raise ValueError("manifest changed during verification")
    return {**bound, "manifest": raw}


def stage(
    out: Path, args: argparse.Namespace, stock: dict[str, Any]
) -> tuple[Path, str]:
    selected = stock["cohort"]
    name = trial_name(args.world, args.arm, args.tag)
    config = trial_config(out, stock["task"], args.world, args.arm, args.tag)
    sender, receiver = protocol.role_ids(args.world)
    arm_order = list(protocol.ordered_arms(args.world, selected.first))
    if args.arm not in arm_order or {sender, receiver} != set(SEATS):
        raise ValueError("arm or roles differ from the wait-peer protocol")
    config_bytes = native_stage.canonical(config)
    registration = {
        "schema": REGISTRATION_SCHEMA,
        "world": args.world,
        "arm": args.arm,
        "tag": args.tag,
        "trial_name": name,
        "sender": sender,
        "receiver": receiver,
        "arm_order": arm_order,
        "manifest_sha256": args.sha256,
        "instance_range": [selected.first, selected.last],
        "seed": selected.stock_limits[0],
        "calendar_sha256": stock["calendar_sha256"],
        "stock_semantics_sha256": stock["stock_semantics_sha256"],
        "task_name": stock["task_name"],
        "task_path": str(stock["task"]),
        "task_checksum": stock["task_checksum"],
        "config_sha256": native_stage.digest(config_bytes),
    }
    native_stage.create(out / "manifest.json", stock["manifest"], READ_ONLY)
    native_stage.create(out / "config.json", config_bytes, READ_ONLY)
    native_stage.create(
        out / "registration.json", native_stage.canonical(registration), READ_ONLY
    )
    return out / "config.json", name


def isolate(out: Path) -> None:
    env = calendar_driver._native_env(out)
    root = Path(__file__).resolve().parents[3]
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env["PYTHONPATH"]
    os.environ.clear()
    os.environ.update(env)
    sys.path[:0] = env["PYTHONPATH"].split(os.pathsep)
    os.chdir(out)


def launch(out: Path, harbor: Path, config: Path, name: str, sockets: Path) -> int:
    outcome: dict[str, Any] = {
        "schema": LAUNCH_SCHEMA,
        "trial_name": name,
        "returncode": None,
        "error": "native_launch_failed",
        "result_present": False,
    }
    argv = sys.argv
    try:
        from . import seat

        seat.bind(sockets)
        sys.argv = [str(harbor), "trial", "start", "-c", str(config)]
        try:
            runpy.run_path(str(harbor), run_name="__main__")
            outcome["returncode"] = 0
        except SystemExit as stop:
            code = stop.code
            outcome["returncode"] = (
                code if type(code) is int else (0 if code is None else 1)
            )
        outcome["error"] = None if outcome["returncode"] == 0 else "native_nonzero_exit"
    except KeyboardInterrupt:
        outcome["error"] = "native_interrupted"
        raise
    finally:
        sys.argv = argv
        outcome["result_present"] = (out / "trials" / name / "result.json").is_file()
        native_stage.create(
            out / "launch.json", native_stage.canonical(outcome), READ_ONLY
        )
    return 0 if outcome["error"] is None else 1


def summary(out: Path, name: str) -> dict[str, str]:
    trial = out / "trials" / name
    return {
        "trial_name": name,
        "config": str(out / "config.json"),
        "registration": str(out / "registration.json"),
        "manifest": str(out / "manifest.json"),
        "launch": str(out / "launch.json"),
        "trial_dir": str(trial),
        "result": str(trial / "result.json"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -B -m examples.seatlab.wait_peer.run", allow_abbrev=False
    )
    parser.add_argument("tasks", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sockets", type=Path, required=True)
    parser.add_argument("--tag", default="live", choices=TAGS)
    args = parser.parse_args(argv)
    try:
        packet._check_digest(args.sha256)
        tasks = heldout._physical_directory(args.tasks)
        harbor = calendar_driver._entrypoint(Path(sys.executable).parent / "harbor")
        manifest = args.manifest.absolute()
        out = native_stage.empty_out(args.out)
        sockets = checked_sockets(args.sockets, out, tasks)
        if out.is_relative_to(tasks) or tasks.is_relative_to(out):
            raise ValueError("task and output directories must be disjoint")
        isolate(out)
        stock = verified_world(tasks, manifest, args.sha256, args.world)
        config, name = stage(out, args, stock)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"wait-peer run: {error}", file=sys.stderr)
        return 2
    code = launch(out, harbor, config, name, sockets)
    print(native_stage.canonical(summary(out, name)).decode("ascii"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
