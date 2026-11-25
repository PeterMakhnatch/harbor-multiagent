from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any

from ..native_study import MAX_TASK_BYTES, _task_file_sha, record_bytes
from ..seat_checkpoints.materialize import sync_directory
from ..ship_inflight.materialize import publish
from .exhibit_harness import fingerprint
from .exhibit_pairs import SCHEMA, require, validate_manifest
from .workbench.arm_sources import bounded_tree, require_unchanged

CHECKOUT = Path(__file__).resolve().parents[2]
TASK = CHECKOUT / "examples/tasks/exhibit-relay"
TASK_MODULE = "examples.tasks.exhibit-relay.environment.exhibit_relay"
PROFILE = "coupling"
ARMS = ("masked", "bundled")
LAYOUTS = ("a", "b")
DESIGN_SCHEMA = "seatlab.exhibit-schedule/v1"
RECEIPT_SCHEMA = "seatlab.exhibit-enrollment.receipt/v1"


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def target(out: Path) -> Path:
    out = out.expanduser().absolute()
    require(out.name not in ("", ".", ".."), "output must be a new directory")
    out = out.parent.resolve(strict=True) / out.name
    require(not out.is_relative_to(CHECKOUT), "output must be outside checkout")
    require(
        not any(os.path.lexists(parent / ".git") for parent in out.parents),
        "output must be outside git checkouts",
    )
    require(not os.path.lexists(out), "output already exists")
    return out


def task_snapshot() -> tuple[str, Path, dict[str, Any]]:
    root, stamps = bounded_tree(TASK)
    budget = MAX_TASK_BYTES
    outer = hashlib.sha256(b"seatlab.exhibit-enrollment.local-task-tree/v1\n")
    for relative, stamp in sorted(stamps.items()):
        if relative.endswith("/"):
            continue
        checksum, size = _task_file_sha(root / relative, budget)
        budget -= size
        mode = 0o755 if stamp[2] & 0o111 else 0o644
        outer.update(f"{relative}\0{mode:o}\0{checksum}\n".encode("ascii"))
    require_unchanged(root, stamps)
    return "sha256:" + outer.hexdigest(), root, stamps


def draw_design(
    pairs: int, development_seed: int | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    require(
        type(pairs) is int and 4 <= pairs <= 256 and pairs % 4 == 0,
        "pairs must be in 4..256 and divisible by 4",
    )
    require(
        development_seed is None
        or (type(development_seed) is int and 0 <= development_seed < 2**63),
        "invalid development seed",
    )
    rng = (
        random.SystemRandom()
        if development_seed is None
        else random.Random(development_seed)
    )
    seeds = [rng.getrandbits(63) for _ in range(pairs)]
    require(len(set(seeds)) == pairs, "seed collision; enrollment refused")
    strata = [(layout, first) for layout in LAYOUTS for first in ARMS] * (pairs // 4)
    rng.shuffle(strata)
    order = list(range(pairs))
    rng.shuffle(order)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        make_case = import_module(f"{TASK_MODULE}.cases").make_case
        case_commitment = import_module(f"{TASK_MODULE}.diagnostics").case_commitment
    finally:
        sys.dont_write_bytecode = previous
    roster = []
    assignments: list[dict[str, Any]] = []
    for index, (seed, (layout, first)) in enumerate(zip(seeds, strata, strict=True)):
        ticket = f"pair-{index + 1:04d}"
        commitment = case_commitment(make_case(seed, PROFILE, layout=layout))
        paths = {arm: f"outcomes/{ticket}/{arm}" for arm in ARMS}
        roster.append(
            {
                "id": ticket,
                "case_commitment": commitment,
                "left": paths[ARMS[0]],
                "right": paths[ARMS[1]],
            }
        )
        assignments.append(
            {
                "id": ticket,
                "seed": seed,
                "layout": layout,
                "order": [first, ARMS[1] if first == ARMS[0] else ARMS[0]],
            }
        )
    schedule = []
    for index in order:
        assignment = assignments[index]
        for arm in assignment["order"]:
            schedule.append(
                {"position": len(schedule) + 1, "id": assignment["id"], "arm": arm}
            )
    manifest = {
        "schema": SCHEMA,
        "profile": PROFILE,
        "arms": list(ARMS),
        "pairs": roster,
    }
    validate_manifest(manifest)
    design = {
        "schema": DESIGN_SCHEMA,
        "profile": PROFILE,
        "arms": list(ARMS),
        "status": "private_random_plan"
        if development_seed is None
        else "development_only",
        "enrollable": development_seed is None,
        "development_seed": development_seed,
        "cases": assignments,
        "schedule": schedule,
    }
    return manifest, design


def write_private(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def enroll(
    out: Path, pairs: int, development_seed: int | None = None
) -> dict[str, Any]:
    out = target(out)
    tree_digest, root, stamps = task_snapshot()
    host_digest = fingerprint(CHECKOUT)
    manifest, design = draw_design(pairs, development_seed)
    manifest_bytes = record_bytes(manifest)
    design.update(
        {
            "manifest_digest": digest(manifest_bytes),
            "local_task_tree_digest": tree_digest,
            "host_harness_digest": host_digest,
            "evidence": "private design and roster schedule only; not native trial configs or run identity",
        }
    )
    design_bytes = record_bytes(design)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "profile": PROFILE,
        "arms": list(ARMS),
        "status": design["status"],
        "enrollable": design["enrollable"],
        "pair_count": pairs,
        "planned_arm_count": 2 * pairs,
        "layout_first_arm_cell_count": pairs // 4,
        "manifest_digest": digest(manifest_bytes),
        "schedule_digest": digest(design_bytes),
        "host_harness_digest": host_digest,
        "local_task_tree_digest": tree_digest,
        "evidence": design["evidence"],
        "operator_authenticated": False,
        "executions": 0,
        "funds_spent_usd": 0,
    }
    require_unchanged(root, stamps)
    stage = Path(tempfile.mkdtemp(prefix=".exhibit-enroll-", dir=out.parent))
    published = False
    try:
        os.chmod(stage, 0o700)
        (stage / "outcomes").mkdir(mode=0o700)
        for pair in manifest["pairs"]:
            (stage / "outcomes" / pair["id"]).mkdir(mode=0o700)
        write_private(stage / "manifest.json", manifest_bytes)
        write_private(stage / "schedule.json", design_bytes)
        write_private(stage / "receipt.json", record_bytes(receipt))
        sync_directory(stage)
        publish(stage, out)
        published = True
    finally:
        if not published:
            shutil.rmtree(stage)
    sync_directory(out.parent)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a private Exhibit Relay matched-pair enrollment; never execute trials."
    )
    parser.add_argument("out", type=Path)
    parser.add_argument("--pairs", type=int, required=True)
    parser.add_argument("--development-seed", type=int)
    args = parser.parse_args(argv)
    if not 4 <= args.pairs <= 256 or args.pairs % 4:
        parser.error("--pairs must be in 4..256 and divisible by 4")
    if args.development_seed is not None and not 0 <= args.development_seed < 2**63:
        parser.error("--development-seed must be a nonnegative 63-bit integer")
    try:
        receipt = enroll(args.out, args.pairs, args.development_seed)
    except (ValueError, OSError, RuntimeError, ImportError):
        print("Enrollment failed; no successful receipt issued.", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
