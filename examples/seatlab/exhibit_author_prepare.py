from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..native_study import (
    native,
    normalize,
    record_bytes,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ..seat_checkpoints.materialize import sync_directory
from ..ship_inflight.materialize import publish
from .exhibit_bind import DEPOT_OWNER
from .exhibit_enroll import LAYOUTS, PROFILE, digest, target
from .exhibit_harness import fingerprint
from .exhibit_prepare import CHECKOUT, NAME, ROLES, TASK, locked_arm
from .visibility import local_seat
from .workbench.arm_sources import bounded_tree, require_unchanged
from .workbench.isolate import Arm, compare

ARMS = ("authored", "author-masked")
TURNS = ("loader_request", "loader_dispatch", "registrar", "depot")
OWNER_ROLES = {
    "loader": ("loader_request", "loader_dispatch"),
    "registrar": ("registrar",),
    "depot": ("depot",),
}
REUSE_RULE = (
    "Before each arm, bind the same four paths to fresh isolated owners. "
    "Remove all previous arm owner state; never share owner state across arms. "
    "This preflight does not bind, observe or authenticate any owner."
)


def future_endpoints(
    supplied: dict[str, Path], out: Path
) -> tuple[dict[str, str], dict[str, tuple[int, int]]]:
    require(supplied.keys() == set(TURNS), "expected four owner turn endpoints")
    paths: dict[str, str] = {}
    parents: dict[str, tuple[int, int]] = {}
    aliases: set[tuple[int, int, str]] = set()
    for turn in TURNS:
        path = supplied[turn]
        require(
            path.is_absolute()
            and ".." not in path.parts
            and 0 < len(os.fsencode(path)) < 104,
            f"{turn}: expected a short absolute Unix path",
        )
        try:
            canonical = Path(local_seat._socket_path(str(path)))
        except local_seat._Fail as error:
            raise ValueError(f"{turn}: {error.message}") from None
        require(
            not canonical.is_relative_to(CHECKOUT)
            and not canonical.is_relative_to(out),
            f"{turn}: endpoint overlaps task or trial workspace",
        )
        require(not os.path.lexists(canonical), f"{turn}: endpoint must be absent")
        info = canonical.parent.stat()
        pin = (info.st_dev, info.st_ino)
        alias = (*pin, canonical.name.casefold())
        require(alias not in aliases, "owner turn endpoints must be distinct")
        aliases.add(alias)
        paths[turn] = str(canonical)
        parents[turn] = pin
    require(len(set(paths.values())) == len(TURNS), "owner path alias")
    return paths, parents


def owner_team(harbor: SimpleNamespace, paths: dict[str, str]) -> list[dict[str, Any]]:
    require(tuple(OWNER_ROLES) == ROLES, "adapter roster differs from native roster")
    team = []
    for role in ROLES:
        kwargs = {"role": role, "owner_sockets": [paths[t] for t in OWNER_ROLES[role]]}
        member = normalize(
            harbor, {"seat": role, "import_path": DEPOT_OWNER, "kwargs": kwargs}
        )
        require(
            member.get("seat") == role
            and member.get("import_path") == DEPOT_OWNER
            and member.get("kwargs") == kwargs
            and member.get("name") is None
            and member.get("model_name") is None,
            "native participant changed the owner binding",
        )
        team.append(member)
    return team


def prepare(
    out: Path, seed: int, layout: str, supplied: dict[str, Path]
) -> dict[str, Any]:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid development seed")
    require(layout in LAYOUTS, "invalid layout")
    out = target(out)
    endpoints, parents = future_endpoints(supplied, out)
    root, stamps = bounded_tree(TASK)
    harbor = native()
    identity = task_identity(harbor, root, NAME)
    tree_digest = task_files_digest(root, identity)
    team = owner_team(harbor, endpoints)
    arms: list[Arm] = []
    records: dict[str, dict[str, Any]] = {}
    inputs: dict[str, Any] = {}
    for mode in ARMS:
        ticket = f"dev-exhibit-author-{seed}-{layout}-{mode}"
        env = {
            "EXHIBIT_PROFILE": PROFILE,
            "EXHIBIT_LAYOUT": layout,
            "EXHIBIT_SEED": str(seed),
            "EXHIBIT_MODE": mode,
        }
        request = trial_request(harbor, root, out / "trials", ticket, team, env)
        request["verifier"] = {"env": {"EXHIBIT_RETAIN_CUT": "1"}}
        lock, arm = locked_arm(harbor, root, request, identity, ticket)
        require(arm.source.lock_covers_roles, "native lock omits a declared role")
        arms.append(arm)
        config_name, lock_name = f"{mode}.config.json", f"{mode}.lock.json"
        records[config_name], records[lock_name] = request, lock
        inputs[mode] = {
            "trial_name": ticket,
            "config": config_name,
            "lock": lock_name,
            "config_digest": digest(record_bytes(request)),
            "lock_digest": digest(record_bytes(lock)),
        }
    isolation = compare(arms[0], arms[1], "world_setting", "EXHIBIT_MODE", None)
    require(isolation["status"] == "isolated", "native inputs are not isolated")
    require_unchanged(root, stamps)
    require(task_files_digest(root, identity) == tree_digest, "task tree changed")
    receipt = {
        "schema": "seatlab.exhibit-author-preflight/v1",
        "status": "prepared_not_enrolled",
        "development_only": True,
        "fixed_prefix_eligible": False,
        "native_admission": "accepted",
        "executions": 0,
        "provider_authenticated": False,
        "provider_identities": [],
        "effect_estimated": False,
        "profile": PROFILE,
        "seed": seed,
        "layout": layout,
        "native_digest": identity["digest"],
        "task_tree_digest": tree_digest,
        "role_count": len(ROLES),
        "role_binding": "native_and_independent",
        "role_file_digests": arms[0].source.roles,
        "role_instruction_digests": arms[0].source.effective_roles,
        "native_lock_covers_roles": True,
        "owner_turns": list(TURNS),
        "owner_rebinding_rule": REUSE_RULE,
        "host_harness_digest": fingerprint(CHECKOUT),
        "arms": inputs,
        "comparison": isolation["comparison"],
        "comparison_caveat": isolation["caveat"],
    }
    records["receipt.json"] = receipt
    publish_inputs(out, records, root, stamps, supplied, endpoints, parents)
    return receipt


def publish_inputs(
    out: Path,
    records: dict[str, dict[str, Any]],
    root: Path,
    stamps: dict[str, Any],
    supplied: dict[str, Path],
    endpoints: dict[str, str],
    parents: dict[str, tuple[int, int]],
) -> None:
    stage = Path(tempfile.mkdtemp(prefix=".exhibit-author-", dir=out.parent))
    try:
        for name, record in records.items():
            write_exclusive(stage / name, record, 0o600)
        sync_directory(stage)
        require_unchanged(root, stamps)
        require(
            future_endpoints(supplied, out) == (endpoints, parents),
            "owner endpoints changed during preparation",
        )
        publish(stage, out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    sync_directory(out.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Prepare two development-only native Exhibit Relay configs and locks. Never launches.",
        epilog=REUSE_RULE,
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--layout", choices=LAYOUTS, required=True)
    for turn in TURNS:
        parser.add_argument("--" + turn.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = prepare(
            args.output,
            args.seed,
            args.layout,
            {turn: getattr(args, turn) for turn in TURNS},
        )
    except (ValueError, OSError) as error:
        parser.exit(2, f"exhibit author preflight: {error}\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
