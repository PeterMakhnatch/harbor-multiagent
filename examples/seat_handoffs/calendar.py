from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from examples.seat_handoffs.core import (
    Finding,
    canonical_uuid,
    reject_constant,
    unique_object,
)

SOURCE_REPOSITORY = "https://github.com/PeterMakhnatch/harbor-multiagent"
SOURCE_BRANCH = "experiments/seatlab-report-budget"
SOURCE_COMMIT = "eead793e3e59c42b4ea619a1ce10cab65f147c92"
SOURCE_PATH = "examples/seatlab/results/wait-for-peer/calendar-subscription-rows.json"
SOURCE_SHA256 = "968e4a2cfb5f00692b8b689c63881a718fd825ec996cc09e0417cfacb3b41f68"

MESSAGE = "report"
ROWS_SCHEMA = "seatlab.wait-peer-rows/v1"
RECEIPT_SCHEMA = "seatlab.wait-peer-receipt/v1"
MAX_BYTES = 1 << 20
BOUND_CLAIMS = (
    "native_trial_complete",
    "stock_reward_verified",
    "all_prompts_reconstructed",
    "owner_answers_bound",
    "report_delivered_before_book",
)


def inspect(path: Path) -> list[Finding]:
    file = path / SOURCE_PATH if path.is_dir() else path
    if not file.is_file():
        raise ValueError(f"calendar rows file is missing: {file.name}")
    if file.stat().st_size > MAX_BYTES:
        raise ValueError("unsupported calendar release: larger than the pinned file")
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError(
            "unsupported calendar release: only "
            f"{SOURCE_COMMIT}:{SOURCE_PATH} (sha256 {SOURCE_SHA256}) is supported"
        )
    text = raw.decode("utf-8")
    document = json.loads(
        text, object_pairs_hook=unique_object, parse_constant=reject_constant
    )
    if not isinstance(document, dict) or document.get("schema") != ROWS_SCHEMA:
        raise ValueError("calendar archive has an unexpected schema")
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("calendar archive has no live rows")
    return [_finding(row) for row in rows]


def _finding(row: Any) -> Finding:
    _require(
        isinstance(row, dict)
        and row.get("schema") == RECEIPT_SCHEMA
        and row.get("tag") == "live"
        and row.get("complete") is True
        and canonical_uuid(row.get("trial_uuid")),
        "calendar row is not a complete live native trial",
    )
    claims, turns = row.get("claims"), row.get("turns")
    book = turns.get("book") if isinstance(turns, dict) else None
    _require(
        isinstance(claims, dict)
        and all(claims.get(name) is True for name in BOUND_CLAIMS)
        and isinstance(book, dict)
        and book.get("turn_source") == "owner_subscription_cli"
        and book.get("owner_status") == "bound",
        "calendar row lacks a bound receiver booking request",
    )
    booked, reward = row.get("booked_slot"), row.get("native_reward")
    correct = row.get("booked_correct")
    _require(
        row.get("delivered") is True
        and type(booked) is int
        and type(reward) is int
        and reward in (0, 1)
        and correct is (reward == 1),
        "calendar row delivery, booking and native grade disagree",
    )
    return Finding(
        trial=row["trial_uuid"],
        message=MESSAGE,
        sender=row.get("sender"),
        receiver=row.get("receiver"),
        receipt="confirmed",
        timing="before",
        commit="recorded",
        reward=reward,
        basis="model_request",
    )


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)
