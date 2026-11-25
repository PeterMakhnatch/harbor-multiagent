from __future__ import annotations

import json
import os
import stat
import sys
from collections import Counter
from importlib import import_module
from pathlib import Path
from typing import Any

from ..native_study import record_bytes
from .exhibit_enroll import (
    ARMS,
    LAYOUTS,
    PROFILE,
    RECEIPT_SCHEMA,
    TASK_MODULE,
    digest,
    task_snapshot,
)
from .exhibit_pairs import (
    DIR_FLAGS,
    FILE_FLAGS,
    MAX_JSON,
    integer,
    object_keys,
    reject_constant,
    reject_float,
    require,
    unique_object,
    validate_manifest,
)
from .workbench.arm_sources import require_unchanged

SCHEDULE_SCHEMA = "seatlab.exhibit-schedule/v1"


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_private(root: int, name: str) -> tuple[object, str, tuple[int, ...]]:
    fd = os.open(name, FILE_FLAGS, dir_fd=root)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(
            stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600,
            f"{name} must be a private regular file",
        )
        require(0 < info.st_size <= MAX_JSON, f"{name} size limit")
        data = source.read(MAX_JSON + 1)
        stamp = _stamp(info)
        require(
            len(data) == info.st_size and _stamp(os.fstat(source.fileno())) == stamp,
            f"{name} changed during read",
        )
    try:
        raw = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=reject_float,
        )
    except (RecursionError, OverflowError) as error:
        raise ValueError(f"{name} invalid JSON") from error
    return raw, digest(data), stamp


def _validate_schedule(
    raw: object, manifest: dict[str, Any], manifest_digest: str
) -> dict[str, Any]:
    schedule = object_keys(
        raw,
        {
            "schema",
            "profile",
            "arms",
            "status",
            "enrollable",
            "development_seed",
            "cases",
            "schedule",
            "manifest_digest",
            "local_task_tree_digest",
            "evidence",
        },
    )
    require(schedule["schema"] == SCHEDULE_SCHEMA, "invalid schedule schema")
    require(
        manifest["profile"] == schedule["profile"] == PROFILE
        and manifest["arms"] == schedule["arms"] == list(ARMS),
        "invalid enrollment contrast",
    )
    require(schedule["manifest_digest"] == manifest_digest, "manifest digest differs")
    seed = schedule["development_seed"]
    if seed is not None:
        integer(seed, 0, 2**63 - 1)
    require(schedule["enrollable"] is (seed is None), "invalid development flag")
    status = "private_random_plan" if seed is None else "development_only"
    require(schedule["status"] == status, "invalid enrollment status")
    evidence = schedule["evidence"]
    require(
        type(evidence) is str and 0 < len(evidence) <= 1024,
        "invalid evidence description",
    )
    count = len(manifest["pairs"])
    require(4 <= count <= 256 and count % 4 == 0, "invalid pair count")
    require(
        type(schedule["cases"]) is list and len(schedule["cases"]) == count,
        "invalid case roster length",
    )
    require(
        type(schedule["schedule"]) is list and len(schedule["schedule"]) == 2 * count,
        "invalid execution schedule length",
    )
    return schedule


def _case_map(
    manifest: dict[str, Any], schedule: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        make_case = import_module(f"{TASK_MODULE}.cases").make_case
        case_commitment = import_module(f"{TASK_MODULE}.diagnostics").case_commitment
    finally:
        sys.dont_write_bytecode = previous
    cases = {}
    seeds = set()
    cells = Counter()
    for index, (pair, case) in enumerate(
        zip(manifest["pairs"], schedule["cases"], strict=True), 1
    ):
        object_keys(case, {"id", "seed", "layout", "order"})
        ticket = f"pair-{index:04d}"
        require(pair["id"] == case["id"] == ticket, "invalid case identity")
        require(
            pair["left"] == f"outcomes/{ticket}/{ARMS[0]}"
            and pair["right"] == f"outcomes/{ticket}/{ARMS[1]}",
            "noncanonical outcome paths",
        )
        seed = integer(case["seed"], 0, 2**63 - 1)
        require(seed not in seeds, "duplicate world seed")
        seeds.add(seed)
        layout, order = case["layout"], case["order"]
        require(layout in LAYOUTS, "invalid layout")
        require(order in (list(ARMS), list(reversed(ARMS))), "invalid assigned order")
        cells[layout, order[0]] += 1
        require(
            case_commitment(make_case(seed, PROFILE, layout=layout))
            == pair["case_commitment"],
            "generated world commitment differs",
        )
        cases[ticket] = case
    require(
        cells
        == Counter(
            {(layout, arm): len(cases) // 4 for layout in LAYOUTS for arm in ARMS}
        ),
        "unbalanced layout and first-arm cells",
    )
    return cases


def _validate_execution_order(
    schedule: dict[str, Any], cases: dict[str, dict[str, Any]]
) -> None:
    rows = schedule["schedule"]
    seen = set()
    for index in range(0, len(rows), 2):
        first = object_keys(rows[index], {"position", "id", "arm"})
        second = object_keys(rows[index + 1], {"position", "id", "arm"})
        ticket = first["id"]
        require(
            type(ticket) is str and ticket in cases and ticket not in seen,
            "invalid or repeated scheduled pair",
        )
        seen.add(ticket)
        require(second["id"] == ticket, "scheduled pair must be contiguous")
        require(
            [first["arm"], second["arm"]] == cases[ticket]["order"],
            "execution order differs",
        )
        require(
            integer(first["position"], 1, len(rows)) == index + 1
            and integer(second["position"], 1, len(rows)) == index + 2,
            "execution positions must be contiguous",
        )
    require(seen == cases.keys(), "execution schedule omits pairs")


def _validate_receipt(
    raw: object, schedule: dict[str, Any], schedule_digest: str, count: int
) -> dict[str, Any]:
    expected = {
        "schema": RECEIPT_SCHEMA,
        "profile": PROFILE,
        "arms": list(ARMS),
        "status": schedule["status"],
        "enrollable": schedule["enrollable"],
        "pair_count": count,
        "planned_arm_count": 2 * count,
        "layout_first_arm_cell_count": count // 4,
        "manifest_digest": schedule["manifest_digest"],
        "schedule_digest": schedule_digest,
        "local_task_tree_digest": schedule["local_task_tree_digest"],
        "evidence": schedule["evidence"],
        "operator_authenticated": False,
        "executions": 0,
        "funds_spent_usd": 0,
    }
    receipt = object_keys(raw, set(expected))
    require(
        record_bytes(receipt) == record_bytes(expected), "enrollment receipt differs"
    )
    return receipt


def load_plan(plan: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = os.open(plan, DIR_FLAGS)
    try:
        directory = os.fstat(root)
        require(
            stat.S_IMODE(directory.st_mode) == 0o700, "plan directory must be private"
        )
        raw_manifest, manifest_digest, manifest_stamp = _read_private(
            root, "manifest.json"
        )
        raw_schedule, schedule_digest, schedule_stamp = _read_private(
            root, "schedule.json"
        )
        raw_receipt, _, receipt_stamp = _read_private(root, "receipt.json")
        manifest = validate_manifest(raw_manifest)
        schedule = _validate_schedule(raw_schedule, manifest, manifest_digest)
        receipt = _validate_receipt(
            raw_receipt, schedule, schedule_digest, len(manifest["pairs"])
        )
        tree_digest, task_root, stamps = task_snapshot()
        require(
            schedule["local_task_tree_digest"] == tree_digest,
            "local task tree digest differs",
        )
        cases = _case_map(manifest, schedule)
        _validate_execution_order(schedule, cases)
        require_unchanged(task_root, stamps)
        for name, stamp in (
            ("manifest.json", manifest_stamp),
            ("schedule.json", schedule_stamp),
            ("receipt.json", receipt_stamp),
        ):
            require(
                _stamp(os.stat(name, dir_fd=root, follow_symlinks=False)) == stamp,
                f"{name} changed during validation",
            )
        latest = os.stat(plan, follow_symlinks=False)
        require(
            (latest.st_dev, latest.st_ino, latest.st_mode)
            == (directory.st_dev, directory.st_ino, directory.st_mode),
            "plan directory changed",
        )
        return manifest, schedule, receipt
    finally:
        os.close(root)
