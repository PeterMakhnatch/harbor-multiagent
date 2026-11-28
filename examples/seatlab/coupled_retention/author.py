import base64
import json
from typing import Any, Never

from ..decision_boundary.inputs import read_input
from .records import Cell

SCHEMA = "seatlab.two-seat-game/v1"
MAX_BYTES = 4 << 20


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate field")
    return fields


def _reject(_: str) -> Never:
    raise ValueError("numbers must be finite integers")


def document(data: bytes) -> dict[str, Any]:
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError("invalid document size")
    obj = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique,
        parse_constant=_reject,
        parse_float=_reject,
    )
    if type(obj) is not dict:
        raise ValueError("object required")
    return obj


def _labels(labels: Any) -> int:
    if type(labels) is not list or not 1 <= len(labels) <= 64:
        raise ValueError("invalid action roster")
    if any(
        type(label) is not str
        or not 0 < len(label) <= 128
        or not label.isprintable()
        or label != label.strip()
        for label in labels
    ) or len(set(labels)) != len(labels):
        raise ValueError("invalid action label")
    return len(labels)


def _wire(text: Any) -> bytes:
    if type(text) is not str or len(text) > 4 * ((8192 + 2) // 3):
        raise ValueError("invalid private observation")
    data = base64.b64decode(text, validate=True)
    if len(data) > 8192 or base64.b64encode(data).decode("ascii") != text:
        raise ValueError("noncanonical private observation")
    return data


def census(path: str) -> list[Cell]:
    root = document(read_input(path))
    if (
        root.keys() != {"schema", "east_actions", "west_actions", "rows"}
        or root["schema"] != SCHEMA
    ):
        raise ValueError("unexpected game fields")
    n, m = _labels(root["east_actions"]), _labels(root["west_actions"])
    if type(root["rows"]) is not list or not 0 < len(root["rows"]) <= 1024:
        raise ValueError("invalid game size")
    cells: list[Cell] = []
    for row in root["rows"]:
        if type(row) is not dict or row.keys() != {
            "weight",
            "east_b64",
            "west_b64",
            "scores",
        }:
            raise ValueError("invalid game row")
        scores = row["scores"]
        if (
            type(scores) is not list
            or len(scores) != n
            or not all(type(line) is list and len(line) == m for line in scores)
        ):
            raise ValueError("payoffs do not match named actions")
        cells.append(
            Cell(
                row["weight"],
                _wire(row["east_b64"]),
                _wire(row["west_b64"]),
                tuple(tuple(line) for line in scores),
            )
        )
    return cells


def vote(path: str, actions: tuple[str, ...]) -> int:
    raw = read_input(path)
    if not 0 < len(raw) <= 256:
        raise ValueError("invalid vote size")
    result = document(raw)
    if (
        result.keys() != {"slot"}
        or type(result["slot"]) is not str
        or result["slot"] not in actions
    ):
        raise ValueError("expected one sealed vote")
    return actions.index(result["slot"])
