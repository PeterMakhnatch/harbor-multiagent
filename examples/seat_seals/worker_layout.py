import os
from pathlib import Path
from typing import Any

from ..native_study import (
    DIGEST,
    MAX_SHARDS,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    record_bytes,
    require,
    write_exclusive,
)
from ..seat_handoffs.native_records import read_record, record_digest
from ..shift_board.plan import TICKET
from .contract import MAX_CELLS, MODES, pairs, validate_registration

SCHEMA = "harbor.seat-seal.workers/v1"
PAIRED = "paired"
CROSSED = "crossed"
LAYOUTS = (PAIRED, CROSSED)
PLAN_FIELDS = frozenset(
    {"schema", "registration", "task_files", "layout", "workers", "blocks"}
)
BLOCK_FIELDS = frozenset(MODES)
MAX_BLOCKS = MAX_CELLS // len(MODES)
MAX_WORKERS = MAX_SHARDS
MAX_PATH_BYTES = 4096
MAX_PLAN_BYTES = 64 << 10
MAX_RECORD_BYTES = 2 << 20
RECORD_MODE = 0o600

Plan = dict[str, Any]


def _check_path(path: object, label: str) -> Path:
    require(isinstance(path, Path), f"{label} path is not a Path")
    assert isinstance(path, Path)
    size = len(os.fsencode(path))
    require(0 < size <= MAX_PATH_BYTES, f"{label} path exceeds {MAX_PATH_BYTES} bytes")
    return path


def digest(record: dict[str, Any]) -> str:
    as_record(record, "digested record")
    try:
        value = "sha256:" + record_digest(record)
    except TypeError as error:
        raise ValueError("digested record is not plain JSON") from error
    assert DIGEST.fullmatch(value) is not None
    return value


def _check_block(value: object, seen: set[str]) -> tuple[str, str]:
    block = as_record(value, "plan block", BLOCK_FIELDS)
    first = as_text(block["continue"], "plan continue ticket", TICKET)
    second = as_text(block["seal"], "plan seal ticket", TICKET)
    require(first != second, "plan block repeats one ticket")
    require(first not in seen and second not in seen, "plan repeats a ticket")
    seen.add(first)
    seen.add(second)
    return first, second


def validate_plan(value: object) -> Plan:
    plan = as_record(value, "worker plan", PLAN_FIELDS)
    require(plan["schema"] == SCHEMA, "worker plan schema differs")
    as_text(plan["registration"], "plan registration digest", DIGEST)
    as_text(plan["task_files"], "plan task files digest", DIGEST)
    as_choice(plan["layout"], "plan layout", LAYOUTS)
    blocks = as_list(plan["blocks"], "plan blocks", 1, MAX_BLOCKS)
    as_int(plan["workers"], "plan workers", 1, min(MAX_WORKERS, len(blocks)))
    seen: set[str] = set()
    previous = ""
    for block in blocks:
        lowest = min(_check_block(block, seen))
        require(lowest > previous, "plan blocks are not ordered by lower ticket")
        previous = lowest
    assert len(seen) == 2 * len(blocks)
    return plan


def _owners(plan: Plan, index: int) -> tuple[int, int]:
    workers = plan["workers"]
    assert 0 <= index < len(plan["blocks"]) and 1 <= workers <= MAX_WORKERS
    first = index % workers
    second = first if plan["layout"] == PAIRED else (first + 1) % workers
    return first, second


def assigned(plan: Plan, worker: int) -> list[str]:
    validate_plan(plan)
    as_int(worker, "worker", 0, plan["workers"] - 1)
    tickets: list[str] = []
    for index, block in enumerate(plan["blocks"]):
        first, second = _owners(plan, index)
        if first == worker:
            tickets.append(block["continue"])
        if second == worker:
            tickets.append(block["seal"])
    assert 1 <= len(tickets) <= 2 * len(plan["blocks"])
    return sorted(tickets)


def _check_partition(plan: Plan) -> None:
    every = [ticket for block in plan["blocks"] for ticket in block.values()]
    owned = [
        ticket for worker in range(plan["workers"]) for ticket in assigned(plan, worker)
    ]
    assert len(owned) == len(every) and set(owned) == set(every)


def make_plan(registration: dict[str, Any], workers: int, layout: str) -> Plan:
    registration = validate_registration(registration)
    layout = as_choice(layout, "worker layout", LAYOUTS)
    grouped = pairs(registration["cells"])
    require(1 <= len(grouped) <= MAX_BLOCKS, "registration block count is invalid")
    blocks = sorted(
        [{mode: arms[mode]["ticket"] for mode in MODES} for arms in grouped.values()],
        key=lambda block: min(block.values()),
    )
    as_int(workers, "workers", 1, min(MAX_WORKERS, len(blocks)))
    plan = validate_plan(
        {
            "schema": SCHEMA,
            "registration": digest(registration),
            "task_files": registration["task_files"],
            "layout": layout,
            "workers": workers,
            "blocks": blocks,
        }
    )
    _check_partition(plan)
    require(len(record_bytes(plan)) <= MAX_PLAN_BYTES, "worker plan exceeds its bound")
    return plan


def load_plan(path: Path, *, expected: str | None = None) -> Plan:
    path = _check_path(path, "worker plan")
    if expected is not None:
        as_text(expected, "expected plan digest", DIGEST)
    try:
        record = read_record(path, MAX_PLAN_BYTES)
    except OSError as error:
        raise ValueError(f"worker plan {path} is not a plain file") from error
    require(record is not None, f"worker plan {path} does not exist")
    assert record is not None
    plan = validate_plan(record)
    require(
        expected is None or digest(plan) == expected,
        "worker plan digest differs from the expected digest",
    )
    return plan


def write_record(path: Path, record: dict[str, Any]) -> None:
    path = _check_path(path, "record")
    as_record(record, "written record")
    try:
        size = len(record_bytes(record))
    except (TypeError, RecursionError) as error:
        raise ValueError("written record is not plain JSON") from error
    require(size <= MAX_RECORD_BYTES, f"record exceeds {MAX_RECORD_BYTES} bytes")
    write_exclusive(path, record, RECORD_MODE)
