from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...native_study import (
    MAX_TASK_BYTES,
    _task_file_sha,
    native,
    normalize,
    record_bytes,
    require,
    task_files_digest,
    task_identity,
    trial_request,
    write_exclusive,
)
from ...seat_checkpoints.materialize import sync_directory
from ...ship_inflight.materialize import publish
from ..exhibit_harness import fingerprint
from ..exhibit_prepare import CHECKOUT, ROLES, locked_arm
from ..workbench.arm_sources import bounded_tree, require_unchanged
from ..workbench.isolate import Arm, compare
from .certificate import case_commitment, native_pair
from .stage import check_stage

NAME = "harbor/exhibit-relay-physical"
MAPS = ("original", "swapped")
SETTING = "EXHIBIT_BAY_MAP"


def host_fingerprint(root: Path) -> str:
    outer = hashlib.sha256(
        ("seatlab.physical-host/v1\n" + fingerprint(root) + "\n").encode("ascii")
    )
    budget = MAX_TASK_BYTES
    for name in (
        "__main__",
        "certificate",
        "cohort",
        "cohort_draw",
        "cohort_scores",
        "order_evidence",
        "contrast",
        "physical_evidence",
        "physical_history",
        "physical_inputs",
        "policies",
        "preflight",
        "receiver",
        "receiver_plan",
        "stage",
        "worlds",
    ):
        rel = f"examples/seatlab/physical_dispatch/{name}.py"
        digest, size = _task_file_sha(root / rel, budget)
        outer.update(f"{rel}\0{digest}\n".encode("ascii"))
        budget -= size
    return "sha256:" + outer.hexdigest()


def _output_path(out: Path, root: Path) -> Path:
    out = out.expanduser().absolute()
    require(out.name not in ("", ".", ".."), "output must be a new directory")
    out = out.parent.resolve(strict=True) / out.name
    require(not out.is_relative_to(CHECKOUT), "output must be outside checkout")
    require(not out.is_relative_to(root), "output must be outside task")
    require(not os.path.lexists(out), "output already exists")
    return out


def _digest(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(record_bytes(record)).hexdigest()


def prepare(task: Path, out: Path, seed: int) -> dict[str, Any]:
    require(type(seed) is int and 0 <= seed < 2**63, "invalid fixed seed")
    root, stamps = bounded_tree(task.expanduser().absolute())
    stage_digest = check_stage(root)
    out = _output_path(out, root)
    harbor = native()
    identity = task_identity(harbor, root, NAME)
    tree_digest = task_files_digest(root, identity)
    host_digest = host_fingerprint(CHECKOUT)
    team = [normalize(harbor, {"name": "oracle", "seat": role}) for role in ROLES]
    require(all(member.get("model_name") is None for member in team), "model declared")
    arms: list[Arm] = []
    records: dict[str, dict[str, Any]] = {}
    inputs: dict[str, Any] = {}
    base, worlds = native_pair(seed)
    commitments = {name: case_commitment(case) for name, case in worlds.items()}
    require(
        len(set(commitments.values())) == 2,
        "private maps have the same case commitment",
    )
    for map_name in MAPS:
        ticket = f"exhibit-physical-{seed}-{map_name}"
        env = {
            "EXHIBIT_SEED": str(seed),
            "EXHIBIT_PROFILE": "fork",
            "EXHIBIT_MODE": "linked",
            "EXHIBIT_LAYOUT": "draw",
            SETTING: map_name,
        }
        request = trial_request(harbor, root, out / "trials", ticket, team, env)
        lock, arm = locked_arm(harbor, root, request, identity, ticket)
        arms.append(arm)
        config_name, lock_name = f"{map_name}.config.json", f"{map_name}.lock.json"
        records[config_name], records[lock_name] = request, lock
        inputs[map_name] = {
            "case_commitment": commitments[map_name],
            "config": config_name,
            "lock": lock_name,
            "config_digest": _digest(request),
            "lock_digest": _digest(lock),
        }
    isolation = compare(arms[0], arms[1], "world_setting", SETTING, None)
    require(isolation["status"] == "isolated", "native inputs are not isolated")
    source = arms[0].source
    receipt = {
        "schema": "seatlab.exhibit-relay-physical.preflight/v1",
        "status": "prepared_not_enrolled",
        "native_admission": "accepted",
        "input_basis": "prospective_local_configs",
        "executions": 0,
        "effect_estimated": False,
        "provider_identities": [],
        "seed": seed,
        "task_name": identity["name"],
        "natural_focal_draw": base["realized"]
        == min(brief["id"] for brief in base["briefs"]),
        "profile": "fork",
        "mode": "linked",
        "layout": "draw",
        "native_digest": identity["digest"],
        "task_tree_digest": tree_digest,
        "host_source_digest": host_digest,
        "stage_digest": stage_digest,
        "role_count": len(ROLES),
        "role_file_digests": source.roles,
        "role_instruction_digests": source.effective_roles,
        "native_lock_covers_roles": source.lock_covers_roles,
        "role_binding": "native_and_independent"
        if source.lock_covers_roles
        else "independent_only",
        "arms": inputs,
        "comparison": isolation["comparison"],
        "comparison_caveat": isolation["caveat"],
    }
    records["receipt.json"] = receipt
    _publish(out, records, root, stamps, identity, tree_digest, stage_digest)
    return receipt


def _publish(
    out: Path,
    records: dict[str, dict[str, Any]],
    root: Path,
    stamps: dict[str, Any],
    identity: dict[str, str],
    tree_digest: str,
    stage_digest: str,
) -> None:
    stage = Path(tempfile.mkdtemp(prefix=".exhibit-physical-", dir=out.parent))
    try:
        for name, record in records.items():
            write_exclusive(stage / name, record, 0o600)
        sync_directory(stage)
        require(
            host_fingerprint(CHECKOUT) == records["receipt.json"]["host_source_digest"],
            "host implementation changed",
        )
        require(check_stage(root) == stage_digest, "stage identity changed")
        require(task_files_digest(root, identity) == tree_digest, "task tree changed")
        require_unchanged(root, stamps)
        publish(stage, out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    sync_directory(out.parent)
