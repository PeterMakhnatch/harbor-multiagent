from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .dispatch_issue import open_spool
from .dispatch_order_pair import OrderArm, OrderPair
from .dispatch_order_repeat import allocate
from .dispatch_order_score import _classify
from .receiver_panel import _print_json


def assignments(
    pairs: list[OrderPair], draws: int
) -> Iterator[tuple[OrderPair, int, OrderArm]]:
    for pair in pairs:
        for wave in range(2 * draws):
            yield pair, wave, allocate(pair, wave)


def within_limits(
    plan: dict[str, Any], max_requests: int, max_input_bytes: int | None
) -> bool:
    if max_requests < 0 or (max_input_bytes is not None and max_input_bytes < 0):
        raise ValueError("the request and message-byte limits cannot be negative")
    return plan["expected_first_replies"] <= max_requests and (
        max_input_bytes is None
        or plan["canonical_message_bytes"]["panel"] <= max_input_bytes
    )


def emit_wave(
    pairs: list[OrderPair], wave: int, shard: tuple[int, int], path: Path | None
) -> None:
    slot, size = shard
    with open_spool(path) if path is not None else nullcontext(None) as spool:
        for pair in pairs:
            if pair.block % size != slot:
                continue
            arm = allocate(pair, wave)
            if spool is not None and not spool.reserve(arm.id):
                continue
            _print_json(
                {
                    "id": arm.id,
                    "prompt_sha256": arm.prompt_sha256,
                    "messages": arm.messages,
                }
            )
            if spool is not None:
                sys.stdout.flush()


def dispatch_status(
    pairs: list[OrderPair], draws: int, path: Path, replies: dict[str, tuple[str, str]]
) -> tuple[dict[str, Any], int]:
    counts = {
        key: 0
        for key in (
            "unreserved_missing",
            "unreserved_valid",
            "unreserved_invalid",
            "reserved_missing",
            "reserved_valid",
            "reserved_invalid",
        )
    }
    subjects = []
    with open_spool(path) as spool:
        for pair, wave, arm in assignments(pairs, draws):
            reserved = spool.reserved(arm.id)
            reply, _ = _classify(replies.get(arm.id), len(pair.cases))
            counts[("reserved_" if reserved else "unreserved_") + reply] += 1
            subjects.append(
                {
                    "id": arm.id,
                    "block": pair.block,
                    "wave": wave,
                    "reserved": reserved,
                    "reply": reply,
                }
            )
    if counts["unreserved_valid"] or counts["unreserved_invalid"]:
        status = 1
    elif counts["reserved_valid"] < len(subjects):
        status = 3
    else:
        status = 0
    return {
        "schema": "seatlab.dispatch-order-reservations/v1",
        "counts": counts,
        "subjects": subjects,
    }, status
