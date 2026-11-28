import os
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..model import _encode
from ..provenance import _emit, _read_action, offer, reread
from ..revised import cases
from .manifest import digest_line, load, pick, publish_private

START_SCHEMA = "seatlab.receiver-study.start/v1"
RESULT_SCHEMA = "seatlab.receiver-study.result/v1"


def _frames(plan: dict[str, Any], assignment: dict[str, Any]) -> tuple[bytes, bytes]:
    matches = [
        case
        for case in cases(assignment["seed"], plan["sender"])
        if case.name == assignment["world"]
    ]
    if len(matches) != 1:
        raise ValueError("native world mismatch")
    case = matches[0]
    if (
        type(case.scores) is not dict
        or any(type(value) is not int for value in case.scores.values())
        or case.scores != assignment["scores"]
    ):
        raise ValueError("native score mismatch")
    initial = offer(
        case,
        assignment["age"],
        assignment["arm"],
        assignment["coin"],
        Fraction(plan["price"]),
    )
    reply = reread(case)
    if (
        digest_line(initial) != assignment["offer_sha256"]
        or digest_line(reply) != assignment["read_sha256"]
    ):
        raise ValueError("native wire mismatch")
    return initial, reply


def serve(root: Path, expected: str, ticket: str) -> None:
    root = root.expanduser().absolute()
    plan = load(root, expected)
    assignment = pick(plan, ticket)
    start_path = root / "attempts" / f"{ticket}.start.json"
    result_path = root / "attempts" / f"{ticket}.result.json"
    if os.path.lexists(start_path) or os.path.lexists(result_path):
        raise ValueError("ticket already claimed or has an existing result")
    initial, reply = _frames(plan, assignment)
    identity = {"plan_sha256": expected, "ticket": ticket}
    publish_private(start_path, _encode({"schema": START_SCHEMA, **identity}) + b"\n")
    _emit(initial)
    actions: list[dict[str, str]] = []
    slot = _read_action()
    read_sha256 = None
    if slot is None:
        actions.append({"op": "read"})
        _emit(reply)
        read_sha256 = assignment["read_sha256"]
        slot = _read_action()
        if slot is None:
            raise ValueError("repeat reads are not allowed")
    actions.append({"op": "commit", "slot": slot})
    result = {
        "schema": RESULT_SCHEMA,
        **identity,
        "offer_sha256": assignment["offer_sha256"],
        "read_sha256": read_sha256,
        "actions": actions,
    }
    publish_private(result_path, _encode(result) + b"\n")
