from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..native_study import (
    native,
    normalize,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ..seat_checkpoints.materialize import sync_directory
from ..ship_inflight.materialize import publish
from .decision_receipt.control_inputs import check_lock, lock_native
from .workbench.arm_runtime import inspect_record
from .workbench.arm_sources import bounded_tree, inspect_source, require_unchanged
from .workbench.isolate import Arm, compare

CHECKOUT = Path(__file__).resolve().parents[2]
TASK = CHECKOUT / "examples/tasks/exhibit-relay"
NAME = "harbor/exhibit-relay"
ROLES = ("loader", "registrar", "depot")
PROFILES = ("fork", "mesh", "coupling")


def locked_arm(
    harbor: SimpleNamespace,
    root: Path,
    request: dict[str, Any],
    identity: dict[str, str],
    ticket: str,
) -> tuple[dict[str, Any], Arm]:
    from harbor.trial.seats import check_launch

    locking = lock_native()
    config = harbor.TrialConfig.model_validate(request)
    check_launch(config, harbor.Task(root))
    resolved = SimpleNamespace(
        path=root, content_hash=None, resolved_git_commit_id=None
    )
    model = locking.build_trial_lock(trial_config=config, task_download_result=resolved)
    lock = check_lock(
        locking,
        json.loads(model.model_dump_json(exclude_none=True)),
        request,
        identity["digest"],
        ticket,
    )
    runtime = inspect_record(lock)
    source = inspect_source(root, runtime.task)
    require(
        source.seats == runtime.seats == tuple(sorted(ROLES)), "role roster differs"
    )
    return lock, Arm(source, runtime)


def prepare(out: Path, seed: int, profile: str) -> dict[str, Any]:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid development seed")
    require(profile in PROFILES, "invalid profile")
    out = out.expanduser().absolute()
    require(out.name not in ("", ".", ".."), "output must be a new directory")
    out = out.parent.resolve(strict=True) / out.name
    require(not out.is_relative_to(CHECKOUT), "output must be outside checkout")
    require(not os.path.lexists(out), "output already exists")
    root, stamps = bounded_tree(TASK)
    harbor = native()
    identity = task_identity(harbor, root, NAME)
    tree_digest = task_files_digest(root, identity)
    team = [normalize(harbor, {"name": "oracle", "seat": role}) for role in ROLES]
    require(all(member.get("model_name") is None for member in team), "model declared")
    modes = (
        ("masked", "bundled") if profile == "coupling" else ("independent", "linked")
    )
    stage = Path(tempfile.mkdtemp(prefix=".exhibit-prepare-", dir=out.parent))
    arms = []
    paths = {}
    published = False
    try:
        for mode in modes:
            ticket = f"exhibit-{profile}-{mode}"
            env = {
                "EXHIBIT_SEED": str(seed),
                "EXHIBIT_PROFILE": profile,
                "EXHIBIT_MODE": mode,
                "EXHIBIT_LAYOUT": "draw",
            }
            request = trial_request(harbor, root, out / "trials", ticket, team, env)
            lock, arm = locked_arm(harbor, root, request, identity, ticket)
            arms.append(arm)
            config_name, lock_name = f"{mode}.config.json", f"{mode}.lock.json"
            write_exclusive(stage / config_name, request, 0o600)
            write_exclusive(stage / lock_name, lock, 0o600)
            paths[mode] = {
                "config": str(out / config_name),
                "lock": str(out / lock_name),
            }
        isolation = compare(arms[0], arms[1], "world_setting", "EXHIBIT_MODE", None)
        require(isolation["status"] == "isolated", "native inputs are not isolated")
        require_unchanged(root, stamps)
        require(task_files_digest(root, identity) == tree_digest, "task tree changed")
        sync_directory(stage)
        publish(stage, out)
        published = True
    finally:
        if not published:
            shutil.rmtree(stage)
    sync_directory(out.parent)
    return {
        "schema": "seatlab.exhibit-relay.preflight/v1",
        "status": "prepared_not_enrolled",
        "profile": profile,
        "native_digest": identity["digest"],
        "task_tree_digest": tree_digest,
        "role_count": len(ROLES),
        "native_lock_covers_roles": arms[0].source.lock_covers_roles,
        "role_file_digests": arms[0].source.roles,
        "role_instruction_digests": arms[0].source.effective_roles,
        "role_binding": "native_and_independent"
        if arms[0].source.lock_covers_roles
        else "independent_only",
        "comparison": isolation["comparison"],
        "paths": paths,
        "executions": 0,
        "provider_identities": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare local native reference configs and locks; never launch."
    )
    parser.add_argument("out", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--profile", choices=PROFILES, default="fork")
    args = parser.parse_args(argv)
    try:
        record = prepare(args.out, args.seed, args.profile)
    except (OSError, ValueError) as error:
        print(f"exhibit preflight: {error}", file=sys.stderr)
        return 1
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
