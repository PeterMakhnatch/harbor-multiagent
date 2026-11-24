import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...native_study import MAX_TASK_ENTRIES
from ...seat_handoffs.native_records import read_record_with_digest
from . import isolate
from .seat_stream import SEAT, Record

RECEIPT_SCHEMA = "seatlab.workbench.worker-plan/v1"
CHECK_SCHEMA = "seatlab.workbench.worker-plan-match/v1"
SIDES = ("left", "right")
MAX_BYTES = 64 << 10
MAX_PATH_BYTES = 4096
MAX_COPIES = 64
MAX_CONCURRENCY = 1024
MAX_RETRIES = 16
ROLE_MODE = "role_instruction"
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "prepared_input",
        "trial_lock_record",
        "copies",
        "concurrency",
        "max_retries",
    }
)
REFERENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "target",
        "other_changes",
        "native_task_digest_changed",
        "native_lock_covers_roles",
        "unpackaged_non_role_files",
        "inputs",
        "trial_lock_records",
        "comparison",
        "matches_expected",
        "reasons",
        "caveat",
        "input_basis",
        "input_note",
    }
)
TARGET_FIELDS = frozenset(
    {"kind", "name", "present_in_both", "changed", "native_loaded_text_changed"}
)
CAVEAT = (
    "Coordinator check of one pinned prospective isolate result and two worker plan receipts. "
    "It trusts what each worker claims: it does not authenticate a worker, observe deployed bytes "
    "or show a registered run. It ran 0 jobs and 0 trials and shows no cost, grade or causal effect."
)

BAD_ARGUMENTS = "worker plan check arguments are outside the profile"
BAD_PIN = "reference pin is not a canonical sha256 pin"
UNREADABLE = "cannot read the {} as a bounded regular JSON file"
WRONG_BYTES = "reference bytes do not match the pinned digest"
NOT_REFERENCE = "reference is not a successful prospective isolate result"
BAD_COMPARISON = "reference comparison does not match its prepared inputs"
BAD_RECEIPT = "{} worker receipt is not an exact worker plan receipt"

PREPARED_DIFFERS = "{} worker prepared an input other than the reference {} input"
COPIES_DIFFER = "{} worker schedules copies other than the requested copies"
CONCURRENCY_DIFFERS = (
    "{} worker requests concurrency other than the requested concurrency"
)
RETRIES_DIFFER = "{} worker requests a retry limit other than the requested limit"
SAME_RECORD = "both workers report the same native trial-lock record"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _count(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _pin(value: Any) -> bool:
    return type(value) is str and isolate.PIN.fullmatch(value) is not None


def _pair(value: Any, accept: Callable[[Any], bool]) -> tuple[Any, Any]:
    _require(type(value) is dict and value.keys() == set(SIDES), NOT_REFERENCE)
    _require(all(accept(value[side]) for side in SIDES), NOT_REFERENCE)
    return value["left"], value["right"]


def _read(path: Any, label: str) -> tuple[Record, str]:
    _require(isinstance(path, Path), BAD_ARGUMENTS)
    _require(0 < len(os.fsencode(path)) <= MAX_PATH_BYTES, BAD_ARGUMENTS)
    try:
        found = read_record_with_digest(path, MAX_BYTES)
    except (OSError, ValueError, RecursionError):
        found = None
    if found is None:
        raise ValueError(UNREADABLE.format(label))
    record, sha256 = found
    assert type(record) is dict and len(sha256) == 64
    return record, sha256


def _target(target: Any) -> tuple[str, str]:
    _require(type(target) is dict and target.keys() == TARGET_FIELDS, NOT_REFERENCE)
    mode, name = target["kind"], target["name"]
    _require(type(mode) is str and mode in isolate.MODES, NOT_REFERENCE)
    pattern = isolate.WORLD_KEY if mode == "world_setting" else SEAT
    _require(type(name) is str and pattern.fullmatch(name) is not None, NOT_REFERENCE)
    _require(target["present_in_both"] is True, NOT_REFERENCE)
    _require(target["changed"] is True, NOT_REFERENCE)
    loaded = target["native_loaded_text_changed"]
    _require(loaded is (True if mode == ROLE_MODE else None), NOT_REFERENCE)
    return mode, name


def _packaging(record: Record, mode: str) -> None:
    covers = _pair(record["native_lock_covers_roles"], lambda v: type(v) is bool)
    _pair(
        record["unpackaged_non_role_files"],
        lambda v: _count(v, 0, MAX_TASK_ENTRIES),
    )
    digest_changed = record["native_task_digest_changed"]
    _require(type(digest_changed) is bool, NOT_REFERENCE)
    if mode == ROLE_MODE:
        _require(covers == (True, True) and digest_changed, NOT_REFERENCE)
    else:
        _require(not digest_changed, NOT_REFERENCE)


def _reference(record: Record) -> tuple[tuple[str, str], str]:
    _require(record.keys() == REFERENCE_FIELDS, NOT_REFERENCE)
    for field, value in (
        ("schema", isolate.SCHEMA),
        ("input_basis", isolate.INPUT_BASIS),
        ("status", "isolated"),
    ):
        _require(type(record[field]) is str and record[field] == value, NOT_REFERENCE)
    _require(
        all(type(record[field]) is str for field in ("input_note", "caveat")),
        NOT_REFERENCE,
    )
    _require(type(record["reasons"]) is list and not record["reasons"], NOT_REFERENCE)
    other = record["other_changes"]
    _require(type(other) is dict and not other, NOT_REFERENCE)
    matched_pin = record["matches_expected"]
    _require(matched_pin is None or matched_pin is True, NOT_REFERENCE)
    mode, name = _target(record["target"])
    _packaging(record, mode)
    inputs = _pair(record["inputs"], _pin)
    keys = _pair(record["trial_lock_records"], _pin)
    _require(inputs[0] != inputs[1] and keys[0] != keys[1], NOT_REFERENCE)
    comparison = record["comparison"]
    _require(_pin(comparison), NOT_REFERENCE)
    expected = isolate.comparison_digest(
        mode, name, {"left": inputs[0], "right": inputs[1]}
    )
    _require(comparison == expected, BAD_COMPARISON)
    return inputs, comparison


def _receipt(record: Record, side: str) -> tuple[str, str, int, int, int]:
    bad = BAD_RECEIPT.format(side)
    _require(record.keys() == RECEIPT_FIELDS, bad)
    _require(type(record["schema"]) is str, bad)
    _require(record["schema"] == RECEIPT_SCHEMA, bad)
    _require(_pin(record["prepared_input"]), bad)
    _require(_pin(record["trial_lock_record"]), bad)
    _require(_count(record["copies"], 1, MAX_COPIES), bad)
    _require(_count(record["concurrency"], 1, MAX_CONCURRENCY), bad)
    _require(_count(record["max_retries"], 0, MAX_RETRIES), bad)
    return (
        record["prepared_input"],
        record["trial_lock_record"],
        record["copies"],
        record["concurrency"],
        record["max_retries"],
    )


def _reasons(
    inputs: tuple[str, str],
    receipts: list[tuple[str, str, int, int, int]],
    schedule: tuple[int, int, int],
) -> list[str]:
    assert len(receipts) == len(SIDES) == len(inputs)
    reasons: list[str] = []
    for side, expected, receipt in zip(SIDES, inputs, receipts, strict=True):
        prepared, _, copies, concurrency, retries = receipt
        if prepared != expected:
            reasons.append(PREPARED_DIFFERS.format(side, side))
        for found, wanted, reason in zip(
            (copies, concurrency, retries),
            schedule,
            (COPIES_DIFFER, CONCURRENCY_DIFFERS, RETRIES_DIFFER),
            strict=True,
        ):
            if found != wanted:
                reasons.append(reason.format(side))
    if receipts[0][1] == receipts[1][1]:
        reasons.append(SAME_RECORD)
    return reasons


def check(
    reference: Path,
    left: Path,
    right: Path,
    reference_sha256: str,
    copies: int,
    concurrency: int,
    max_retries: int,
) -> Record:
    _require(_pin(reference_sha256), BAD_PIN)
    _require(_count(copies, 1, MAX_COPIES), BAD_ARGUMENTS)
    _require(_count(concurrency, 1, MAX_CONCURRENCY), BAD_ARGUMENTS)
    _require(_count(max_retries, 0, MAX_RETRIES), BAD_ARGUMENTS)
    record, sha256 = _read(reference, "reference")
    _require("sha256:" + sha256 == reference_sha256, WRONG_BYTES)
    inputs, comparison = _reference(record)
    receipts = [
        _receipt(_read(path, f"{side} worker receipt")[0], side)
        for side, path in zip(SIDES, (left, right), strict=True)
    ]
    schedule = (copies, concurrency, max_retries)
    reasons = _reasons(inputs, receipts, schedule)
    matched = not reasons
    keys = {side: receipt[1] for side, receipt in zip(SIDES, receipts, strict=True)}
    assert not matched or keys["left"] != keys["right"]
    return {
        "schema": CHECK_SCHEMA,
        "status": "matches_reference" if matched else "different_plan",
        "reasons": reasons,
        "reference_sha256": reference_sha256,
        "comparison": comparison,
        "copies_per_arm": copies,
        "concurrency": concurrency,
        "max_retries": max_retries,
        "trial_lock_records": keys if matched else None,
        "caveat": CAVEAT,
    }
