import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..native_study import (
    native,
    normalize,
    record_bytes,
    require,
    task_identity,
    trial_request,
)
from ..seat_checkpoints.materialize import sync_directory
from ..ship_inflight.materialize import publish
from .exhibit_enroll import ARMS, PROFILE, digest, target, write_private
from .exhibit_plan import load_plan
from .exhibit_prepare import NAME, ROLES, TASK, locked_arm

DEPOT_OWNER = "examples.seatlab.exhibit_owner:RelayOwner"


def fixed_request(
    harbor, unit: dict[str, Any], arm: str, socket: Path, trials: Path
) -> dict[str, Any]:
    ticket = unit["id"]
    members = [
        normalize(harbor, {"name": "oracle", "seat": role})
        for role in ROLES
        if role != "depot"
    ]
    members.append(
        normalize(
            harbor,
            {
                "import_path": DEPOT_OWNER,
                "seat": "depot",
                "kwargs": {"role": "depot", "owner_sockets": [str(socket)]},
            },
        )
    )
    world = {
        "EXHIBIT_PROFILE": PROFILE,
        "EXHIBIT_LAYOUT": unit["layout"],
        "EXHIBIT_MODE": arm,
        "EXHIBIT_SEED": str(unit["seed"]),
    }
    request = trial_request(harbor, TASK, trials, ticket, members, world)
    request["verifier"] = {"env": {"EXHIBIT_RETAIN_CUT": "1"}}
    return request


def bind(
    plan: Path, ticket: str, arm: str, owner_socket: Path, out: Path
) -> dict[str, Any]:
    manifest, schedule, enrollment = load_plan(plan)
    require(arm in ARMS, "unknown registered arm")
    unit = next((case for case in schedule["cases"] if case["id"] == ticket), None)
    require(unit is not None, "unknown registered case")
    assert unit is not None
    case = next(pair for pair in manifest["pairs"] if pair["id"] == ticket)
    socket = Path(owner_socket)
    require(
        socket.is_absolute()
        and ".." not in socket.parts
        and 0 < len(os.fsencode(socket)) < 104,
        "owner endpoint requires a short absolute Unix path",
    )
    parent = socket.parent.resolve(strict=True)
    require(
        parent.is_dir() and parent.stat().st_mode & 0o077 == 0,
        "owner directory must be private",
    )
    require(not socket.is_symlink(), "owner endpoint must not be a symlink")
    require(not socket.exists() or socket.is_socket(), "not a Unix endpoint")
    out = target(out)
    require(
        not out.is_relative_to(plan.resolve(strict=True)),
        "keep trial inputs separate from the plan",
    )
    harbor = native()
    request = fixed_request(harbor, unit, arm, socket, out / "trials")
    native_identity = task_identity(harbor, TASK, NAME)
    lock, bound = locked_arm(harbor, TASK, request, native_identity, ticket)
    require(bound.source.lock_covers_roles, "native lock omits a declared role")
    config_bytes, lock_bytes = record_bytes(request), record_bytes(lock)
    receipt = {
        "schema": "seatlab.exhibit-bind/v1",
        "pair": ticket,
        "arm": arm,
        "design": "fixed_prefix_depot",
        "enrollment_status": enrollment["status"],
        "schedule_digest": enrollment["schedule_digest"],
        "case_commitment": case["case_commitment"],
        "config_digest": digest(config_bytes),
        "lock_digest": digest(lock_bytes),
        "task_digest": native_identity["digest"],
        "effective_instructions": bound.source.effective_roles,
        "native_admission": "accepted",
        "executions": 0,
        "provider_authenticated": False,
    }
    stage = Path(tempfile.mkdtemp(prefix=".exhibit-bind-", dir=out.parent))
    try:
        for name, data in (
            ("config.json", config_bytes),
            ("lock.json", lock_bytes),
            ("receipt.json", record_bytes(receipt)),
        ):
            write_private(stage / name, data)
        require(load_plan(plan)[2] == enrollment, "plan changed during preparation")
        sync_directory(stage)
        publish(stage, out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    sync_directory(out.parent)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a private fixed-prefix native trial config. Never launches."
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("ticket")
    parser.add_argument("arm", choices=ARMS)
    parser.add_argument("socket", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    try:
        result = bind(args.plan, args.ticket, args.arm, args.socket, args.out)
    except (ValueError, OSError) as error:
        parser.exit(2, f"exhibit bind: {error}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
