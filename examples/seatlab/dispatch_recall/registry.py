from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from ...native_study import record_bytes, require
from ..exhibit_capture import read_record
from ..exhibit_pairs import DIR_FLAGS
from .collect import TRIALS, prepared_slot
from .design import check_plan
from .prepare import CONFIG, FILES, RECEIPT

SCHEMA = "seatlab.dispatch-recall.registry/v1"
STATUS = "sealed_not_run"
ENDPOINTS = "owner_endpoints"
PINNED = (
    "task_name",
    "native_digest",
    "task_checksum",
    "task_tree_digest",
    "source_digest",
    "host_harness_digest",
)
SHARED = (
    "schema",
    "plan_sha256",
    "team",
    *PINNED,
    "role_file_digests",
    "role_instruction_digests",
    "world_scope",
    "verifier_env",
    "claims",
)
MANIFEST_KEYS = frozenset(
    {
        "schema",
        "status",
        "plan_sha256",
        "planned_jobs",
        "team_kind",
        "pins",
        "jobs",
        "executions",
    }
)
SEALED = frozenset(FILES)
STARTED = SEALED | {TRIALS}
PRIVATE_DIR = 0o700

assert TRIALS not in SEALED


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _open_root(root: Any) -> tuple[Path, int]:
    require(isinstance(root, Path), "registry root must be a path")
    root = root.expanduser().absolute()
    return root, os.open(root, DIR_FLAGS)


def _listing(root_fd: int, plan: dict[str, Any]) -> list[str]:
    planned = [job["job_id"] for job in plan["jobs"]]
    require(planned, "the plan assigns no cells")
    present = set(os.listdir(root_fd))
    missing = [job_id for job_id in planned if job_id not in present]
    unknown = sorted(present - set(planned))
    require(not missing, f"unprepared cells: {missing[:3]}")
    require(not unknown, f"registry root holds unplanned entries: {unknown[:3]}")
    return planned


def _folder(root_fd: int, job_id: str, allowed: frozenset[str]) -> int:
    folder = os.open(job_id, DIR_FLAGS, dir_fd=root_fd)
    try:
        info = os.fstat(folder)
        require(
            stat.S_IMODE(info.st_mode) == PRIVATE_DIR and info.st_uid == os.geteuid(),
            f"{job_id} is not a private prepared directory",
        )
        names = set(os.listdir(folder))
        require(
            TRIALS not in names or TRIALS in allowed,
            f"a trial already started in {job_id}",
        )
        require(
            SEALED <= names <= allowed, f"{job_id} is not exactly one prepared cell"
        )
    except BaseException:
        os.close(folder)
        raise
    return folder


def _sockets(built: dict[str, dict[str, Any]], job_id: str) -> list[str]:
    found: list[str] = []
    for member in built[CONFIG]["participants"]:
        found.extend(member.get("kwargs", {}).get("owner_sockets", {}).values())
    endpoints = built[RECEIPT]["team"][ENDPOINTS]
    require(
        type(endpoints) is dict and sorted(endpoints.values()) == sorted(found),
        f"{job_id} receipt endpoints differ from its configured owner sockets",
    )
    return found


def _shared(receipt: dict[str, Any], key: str) -> str:
    value = receipt[key]
    if key == "team":
        value = {name: part for name, part in value.items() if name != ENDPOINTS}
    return _text(value)


def _manifest(
    plan: dict[str, Any], slots: list[tuple[dict[str, Any], str]]
) -> dict[str, Any]:
    digest = hashlib.sha256(record_bytes(plan)).hexdigest()
    jobs = plan["jobs"]
    require(len(slots) == len(jobs) > 0, "every planned cell needs one receipt")
    first = slots[0][0]
    for job, (receipt, _) in zip(jobs, slots, strict=True):
        require(
            receipt["job_id"] == job["job_id"] and receipt["plan_sha256"] == digest,
            f"receipt of {job['job_id']} binds another cell or plan",
        )
        mixed = [key for key in SHARED if _shared(receipt, key) != _shared(first, key)]
        require(not mixed, f"{job['job_id']} differs from the study in {mixed}")
    return {
        "schema": SCHEMA,
        "status": STATUS,
        "plan_sha256": digest,
        "planned_jobs": len(jobs),
        "team_kind": first["team"]["kind"],
        "pins": {key: first[key] for key in PINNED},
        "jobs": [
            {"job_id": job["job_id"], "receipt_sha256": pin}
            for job, (_, pin) in zip(jobs, slots, strict=True)
        ],
        "executions": 0,
    }


def seal(plan: dict[str, Any], root: Path) -> dict[str, Any]:
    check_plan(plan)
    root, root_fd = _open_root(root)
    slots: list[tuple[dict[str, Any], str]] = []
    sockets: list[str] = []
    try:
        for job_id in _listing(root_fd, plan):
            folder = _folder(root_fd, job_id, SEALED)
            try:
                built, pin = prepared_slot(plan, job_id, folder, root / job_id)
            finally:
                os.close(folder)
            sockets.extend(_sockets(built, job_id))
            slots.append((built[RECEIPT], pin))
    finally:
        os.close(root_fd)
    require(
        len(set(sockets)) == len(sockets),
        "an owner socket is bound to more than one cell",
    )
    return _manifest(plan, slots)


def checked_entries(
    plan: dict[str, Any], root: Path, manifest: Any
) -> list[tuple[str, Path, str]]:
    check_plan(plan)
    require(
        type(manifest) is dict
        and set(manifest) == MANIFEST_KEYS
        and manifest["schema"] == SCHEMA,
        "not a sealed dispatch-recall registry",
    )
    root, root_fd = _open_root(root)
    slots: list[tuple[dict[str, Any], str]] = []
    try:
        for job_id in _listing(root_fd, plan):
            folder = _folder(root_fd, job_id, STARTED)
            try:
                receipt, data = read_record(folder, RECEIPT)
            finally:
                os.close(folder)
            slots.append((receipt, hashlib.sha256(data).hexdigest()))
    finally:
        os.close(root_fd)
    require(
        _text(_manifest(plan, slots)) == _text(manifest),
        "prepared cells differ from the sealed registry",
    )
    return [
        (job["job_id"], root / job["job_id"], pin)
        for job, (_, pin) in zip(plan["jobs"], slots, strict=True)
    ]
