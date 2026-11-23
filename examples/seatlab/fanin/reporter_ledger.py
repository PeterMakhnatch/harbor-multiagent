from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from ..visibility import calendar_packet as packet
from ..wait_peer import campaign, worker
from . import external_evidence as ext
from . import owner_provenance, protocol, screen

SCHEMA = "seatlab.fanin-reporter-campaign/v1"
ISSUE_SCHEMA = "seatlab.fanin-reporter-issue/v1"
STATUS_SCHEMA = "seatlab.fanin-reporter-campaign-status/v1"
WORKER = "examples.seatlab.fanin.reporter_worker"
MANIFEST, QUEUE_LOCK = "campaign.json", "queue.lock"
EXTERNAL, PROVENANCE = "external.json", "provenance.json"
NATIVE, OWNERS = "native", "owners"
STAGING = ".register-"
ARMS = protocol.ARMS
MODES = ("correct", "inexact", "personal")
STATES = ("missing", "issued", "reported", "mismatch")
GRADES = (0, 1)
FIRST = protocol.FIRST
LAST = protocol.EXTERNAL_WORLDS.stop - 1
MAX_COUNT = 64
PRIVATE_DIR = 0o700
REGISTERED = 0o400
SEALED = 0o444
REG_KEYS = frozenset({"schema", "sha256", "first", "count", "mode"})
Refused = campaign.Refused
Cell = tuple[str, int | None, bool | None]
ISSUED: Cell = ("issued", None, None)
MISMATCH: Cell = ("mismatch", None, None)
EVIDENCE_FAILURES = (
    Refused,
    packet._Invalid,
    OSError,
    ValueError,
    KeyError,
    TypeError,
    IndexError,
    AttributeError,
    RecursionError,
)
assert ARMS == ("all", "stop") and FIRST == 1200 and LAST == 999_999
assert len(protocol.SEATS) == 4


def checked(first: int, count: int, mode: str) -> None:
    valid = (
        type(first) is int
        and type(count) is int
        and 1 <= count <= MAX_COUNT
        and first >= FIRST
        and first + count - 1 <= LAST
        and type(mode) is str
        and mode in MODES
    )
    if not valid:
        raise Refused("invalid_arguments")


def manifest(first: int, count: int, mode: str) -> bytes:
    checked(first, count, mode)
    record = {
        "schema": SCHEMA,
        "first": first,
        "count": count,
        "last": first + count - 1,
        "mode": mode,
        "arms": list(ARMS),
        "first_arm": {"even_world": ARMS[0], "odd_world": ARMS[1]},
        "seats": {"booker": 1, "reporters": len(protocol.SEATS) - 1},
        "source": {"generator_commit": screen.GENERATOR_COMMIT, "seed": screen.SEED},
        "worker": WORKER,
        "evidence": {
            "external": ext.SCHEMA,
            "provenance": owner_provenance.SCHEMA if mode == "personal" else None,
        },
    }
    return worker.canonical(record) + b"\n"


def canonical_root(root: Path, create: bool) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise Refused("campaign_unsafe")
    if campaign.campaign_dir(root, create) != root:
        raise Refused("campaign_unsafe")
    return root


def registered_bytes(root: Path) -> bytes:
    path = root / MANIFEST
    try:
        raw = campaign.owned(path)
        info = path.lstat()
    except OSError:
        raise Refused("registration_invalid") from None
    if not campaign.private(REGISTERED, info):
        raise Refused("registration_invalid")
    return raw


def parsed(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        first, count, mode = value["first"], value["count"], value["mode"]
        if raw != manifest(first, count, mode):
            raise ValueError
    except (Refused, ValueError, KeyError, TypeError, RecursionError):
        raise Refused("registration_invalid") from None
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "schema": SCHEMA,
        "sha256": digest,
        "first": first,
        "count": count,
        "mode": mode,
    }


def fresh(root: Path) -> None:
    try:
        names = os.listdir(root)
    except OSError:
        raise Refused("campaign_unsafe") from None
    allowed = (MANIFEST, QUEUE_LOCK)
    if any(name not in allowed and not name.startswith(STAGING) for name in names):
        raise Refused("campaign_unsafe")


def link_file(root: Path, name: str, data: bytes) -> None:
    with tempfile.TemporaryDirectory(dir=root, prefix=STAGING) as scratch:
        staged = Path(scratch) / name
        worker.create(staged, data, REGISTERED)
        try:
            os.link(staged, root / name, follow_symlinks=False)
        except FileExistsError:
            return
        campaign.sync_dir(root)


def initialize(root: Path, first: int, count: int, mode: str) -> dict[str, Any]:
    want = manifest(first, count, mode)
    root = canonical_root(root, create=True)
    if not os.path.lexists(root / MANIFEST):
        fresh(root)
        link_file(root, MANIFEST, want)
    if registered_bytes(root) != want:
        raise Refused("registration_mismatch")
    return load(root, hashlib.sha256(want).hexdigest())


def load(root: Path, sha256: str | None) -> dict[str, Any]:
    if sha256 is not None and (
        type(sha256) is not str or worker.SHA256.fullmatch(sha256) is None
    ):
        raise Refused("sha256_invalid")
    reg = parsed(registered_bytes(canonical_root(root, create=False)))
    if sha256 is not None and reg["sha256"] != sha256:
        raise Refused("manifest_digest_mismatch")
    return reg


def bound(root: Path, reg: dict[str, Any]) -> Path:
    if not isinstance(reg, dict) or reg.keys() != REG_KEYS:
        raise Refused("registration_invalid")
    root = canonical_root(root, create=False)
    if load(root, reg["sha256"]) != reg:
        raise Refused("registration_mismatch")
    return root


def arm_order(world: int) -> tuple[str, str]:
    if type(world) is not int or not FIRST <= world <= LAST:
        raise Refused("invalid_arguments")
    return (ARMS[0], ARMS[1]) if world % 2 == 0 else (ARMS[1], ARMS[0])


def issue_bytes(reg: dict[str, Any], world: int, arm: str) -> bytes:
    order = arm_order(world)
    worlds = range(reg["first"], reg["first"] + reg["count"])
    if world not in worlds or type(arm) is not str or arm not in order:
        raise Refused("invalid_arguments")
    record = {
        "schema": ISSUE_SCHEMA,
        "world": world,
        "arm": arm,
        "manifest": reg["sha256"],
        "mode": reg["mode"],
    }
    return worker.canonical(record) + b"\n"


def sealed(path: Path) -> bytes:
    with open(path, "rb", opener=lambda raw, _: os.open(raw, campaign.READ)) as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not campaign.private(SEALED, info)
        ):
            raise Refused("state_unsafe")
        data = handle.read(worker.MAX_RECEIPT_BYTES + 1)
    if not data or len(data) > worker.MAX_RECEIPT_BYTES:
        raise Refused("state_unsafe")
    return data


def owned_dir(path: Path, mode: int | None) -> bool:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        return False
    return mode is None or stat.S_IMODE(info.st_mode) == mode


def uuid4(value: object) -> bool:
    try:
        identifier = UUID(value) if type(value) is str else None
    except ValueError:
        return False
    return (
        identifier is not None and identifier.version == 4 and str(identifier) == value
    )


def graded_row(row: Any, raw: bytes, world: int, arm: str) -> bool:
    source = row["source"]
    return (
        raw == worker.canonical(row) + b"\n"
        and row["schema"] == ext.SCHEMA
        and row["tag"] == ext.TAG
        and type(row["world"]) is int
        and row["world"] == world
        and row["arm"] == arm
        and isinstance(source, dict)
        and source["generator_commit"] == screen.GENERATOR_COMMIT
        and type(source["seed"]) is int
        and source["seed"] == screen.SEED
        and source["stock_verified"] is True
        and uuid4(row["trial_id"])
        and type(row["native_reward"]) is int
        and row["native_reward"] in GRADES
    )


def provenance(
    child: Path, native: Path, ext_raw: bytes, row: dict[str, Any]
) -> bool | None:
    raw = sealed(child / PROVENANCE)
    value = json.loads(raw)
    world, arm = row["world"], row["arm"]
    reporters = list(protocol.order(world)[1:])
    owners = child / OWNERS
    seats = value["seats"]
    valid = (
        isinstance(value, dict)
        and raw == worker.canonical(value) + b"\n"
        and value.keys() == owner_provenance.ROW_KEYS
        and value["schema"] == owner_provenance.SCHEMA
        and type(value["world"]) is int
        and (value["world"], value["arm"]) == (world, arm)
        and value["trial_id"] == row["trial_id"]
        and value["external_row_sha256"] == hashlib.sha256(ext_raw).hexdigest()
        and type(seats) is list
        and [seat["seat"] for seat in seats] == reporters
        and value["served_model"] is None
        and type(value["fully_accounted"]) is bool
        and owned_dir(owners, PRIVATE_DIR)
    )
    if not valid:
        return None
    again = owner_provenance.audit(native, world, arm, owners, reporters)
    if worker.canonical(again) + b"\n" != raw:
        return None
    return value["fully_accounted"]


def reported(child: Path, reg: dict[str, Any], world: int, arm: str) -> Cell:
    personal = reg["mode"] == "personal"
    has_external = os.path.lexists(child / EXTERNAL)
    has_provenance = os.path.lexists(child / PROVENANCE)
    if not personal and (has_provenance or os.path.lexists(child / OWNERS)):
        return MISMATCH
    if not has_external:
        return MISMATCH if has_provenance else ISSUED
    control = child / "control"
    if os.path.lexists(control) != (reg["mode"] == "inexact"):
        return MISMATCH
    if reg["mode"] == "inexact" and not owned_dir(control, PRIVATE_DIR):
        return MISMATCH
    if personal and not has_provenance:
        return ISSUED
    raw = sealed(child / EXTERNAL)
    row = json.loads(raw)
    native = child / NATIVE
    if not isinstance(row, dict) or not graded_row(row, raw, world, arm):
        return MISMATCH
    if not owned_dir(native, None):
        return MISMATCH
    if worker.canonical(ext.audit(native, world, arm)) + b"\n" != raw:
        return MISMATCH
    if not personal:
        return ("reported", row["native_reward"], None)
    accounted = provenance(child, native, raw, row)
    if accounted is None:
        return MISMATCH
    return ("reported", row["native_reward"], accounted)


def evaluate(root: Path, reg: dict[str, Any], world: int, arm: str) -> Cell:
    want = issue_bytes(reg, world, arm)
    label = campaign.name(world, arm)
    child, marker = root / label, root / f"{label}.issued"
    try:
        marked = os.path.lexists(marker)
        if marked and (
            campaign.owned(marker) != want
            or not campaign.private(REGISTERED, marker.lstat())
        ):
            return MISMATCH
        if not os.path.lexists(child):
            return ISSUED if marked else ("missing", None, None)
        if not marked or not owned_dir(child, PRIVATE_DIR):
            return MISMATCH
        return reported(child, reg, world, arm)
    except EVIDENCE_FAILURES:
        return MISMATCH


def cell_state(root: Path, reg: dict[str, Any], world: int, arm: str) -> str:
    state = evaluate(bound(root, reg), reg, world, arm)[0]
    assert state in STATES
    return state


def eligible(world: int, states: dict[str, str]) -> str | None:
    order = arm_order(world)
    for index, arm in enumerate(order):
        if states[arm] != "reported":
            later = order[index:]
            return arm if all(states[each] == "missing" for each in later) else None
    return None


def status(root: Path, reg: dict[str, Any]) -> dict[str, Any]:
    root = bound(root, reg)
    worlds: list[dict[str, Any]] = []
    cells: list[Cell] = []
    upcoming: list[dict[str, Any]] = []
    for world in range(reg["first"], reg["first"] + reg["count"]):
        order = arm_order(world)
        found = {arm: evaluate(root, reg, world, arm) for arm in order}
        cells.extend(found.values())
        worlds.append(
            {
                "world": world,
                "cells": [
                    {"arm": arm, "state": s, "grade": g, "accounted": a}
                    for arm, (s, g, a) in found.items()
                ],
            }
        )
        arm = eligible(world, {each: cell[0] for each, cell in found.items()})
        if arm is not None:
            upcoming.append({"world": world, "arm": arm})
    done = [cell for cell in cells if cell[0] == "reported"]
    counts: dict[str, int | None] = {s: sum(c[0] == s for c in cells) for s in STATES}
    counts.update({f"grade_{g}": sum(c[1] == g for c in done) for g in GRADES})
    personal = reg["mode"] == "personal"
    counts["accounted"] = sum(c[2] is True for c in done) if personal else None
    assert len(cells) == 2 * reg["count"]
    return {
        "schema": STATUS_SCHEMA,
        "registration": dict(reg),
        "arms": list(ARMS),
        "worlds": worlds,
        "counts": counts,
        "next": upcoming,
    }
