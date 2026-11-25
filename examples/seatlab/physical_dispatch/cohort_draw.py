from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import re
from typing import Any, cast

from .certificate import case_commitment, case_maps, make_case

SCHEMA = "seatlab.physical-dispatch.cohort-draw.v1"
CELLS = ("original-other", "original-relevant", "swapped-other", "swapped-relevant")
MAPS = ("original", "swapped")
_PERMUTATIONS = tuple(itertools.permutations(range(4)))
_FIELDS = frozenset(
    {
        "schema",
        "cases",
        "secret",
        "development",
        "candidates_checked",
        "rejections",
        "stream_receipt",
    }
)
_CASE_FIELDS = frozenset({"id", "seed", "draw_index", "case_commitments", "order"})
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SECRET = re.compile(r"[0-9a-f]{64}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _record(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(fields) or set(value) != fields:
        raise ValueError("invalid design object fields")
    return cast(dict[str, Any], value)


def _integer(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _text(value: object, pattern: re.Pattern[str], length: int) -> bool:
    return (
        type(value) is str
        and len(value) == length
        and pattern.fullmatch(value) is not None
    )


def _sample(secret: bytes, domain: bytes, index: int, attempt: int = 0) -> bytes:
    return hmac.digest(
        secret,
        SCHEMA.encode("ascii")
        + b"\x00"
        + domain
        + b"\x00"
        + index.to_bytes(8, "big")
        + attempt.to_bytes(8, "big"),
        "sha256",
    )


def _permutation(secret: bytes, domain: bytes, block: int) -> tuple[int, ...]:
    limit = 2**256 - 2**256 % len(_PERMUTATIONS)
    for attempt in range(64):
        value = int.from_bytes(_sample(secret, domain, block, attempt), "big")
        if value < limit:
            return _PERMUTATIONS[value % len(_PERMUTATIONS)]
    raise ValueError("permutation sampling exhausted")


def _orders(secret: bytes, block: int) -> list[list[str]]:
    labels = _permutation(secret, b"williams-labels", block)
    rows = _permutation(secret, b"williams-row-assignment", block)
    orders = [
        [CELLS[labels[(row + offset) % 4]] for offset in (0, 1, 3, 2)] for row in rows
    ]
    _require(
        all({row[position] for row in orders} == set(CELLS) for position in range(4)),
        "unbalanced Williams positions",
    )
    pairs = {pair for row in orders for pair in itertools.pairwise(row)}
    _require(
        pairs == set(itertools.permutations(CELLS, 2)), "unbalanced Williams adjacency"
    )
    return orders


def make_design(n: int, secret: bytes, *, development: bool) -> dict[str, Any]:
    _require(_integer(n, 4, 64) and n % 4 == 0, "invalid cohort size")
    _require(type(secret) is bytes and len(secret) == 32, "invalid cohort secret")
    _require(type(development) is bool, "invalid development flag")
    cases: list[dict[str, Any]] = []
    seen_seeds: set[int] = set()
    seen_commitments: set[str] = set()
    stream = hashlib.sha256(SCHEMA.encode("ascii") + b"\x00candidate-stream\x00")
    orders = [order for block in range(n // 4) for order in _orders(secret, block)]
    for index in range(1, 64 * n + 1):
        sample = _sample(secret, b"candidate-seed", index)
        stream.update(sample)
        seed = int.from_bytes(sample[:8], "big") & (2**63 - 1)
        _require(seed not in seen_seeds, "candidate seed collision")
        seen_seeds.add(seed)
        base = make_case(seed, "fork", layout="draw")
        if base["realized"] != min(brief["id"] for brief in base["briefs"]):
            continue
        commitments = {
            name: case_commitment(case) for name, case in case_maps(base).items()
        }
        for commitment in commitments.values():
            _require(_text(commitment, _DIGEST, 71), "invalid native map commitment")
            _require(commitment not in seen_commitments, "map commitment collision")
            seen_commitments.add(commitment)
        cases.append(
            {
                "id": f"case-{len(cases) + 1:04d}",
                "seed": seed,
                "draw_index": index,
                "case_commitments": commitments,
                "order": orders[len(cases)],
            }
        )
        if len(cases) == n:
            return {
                "schema": SCHEMA,
                "cases": cases,
                "secret": secret.hex(),
                "development": development,
                "candidates_checked": index,
                "rejections": {"non_focal": index - n},
                "stream_receipt": "sha256:" + stream.hexdigest(),
            }
    raise ValueError("candidate sampling exhausted")


def _validate_case(raw: object, ordinal: int, previous_index: int, checked: int) -> int:
    case = _record(raw, _CASE_FIELDS)
    _require(
        type(case["id"]) is str and case["id"] == f"case-{ordinal:04d}",
        "invalid case id",
    )
    _require(_integer(case["seed"], 0, 2**63 - 1), "invalid case seed")
    _require(
        _integer(case["draw_index"], previous_index + 1, checked), "invalid draw index"
    )
    commitments = _record(case["case_commitments"], frozenset(MAPS))
    _require(
        all(_text(value, _DIGEST, 71) for value in commitments.values()),
        "invalid case commitment",
    )
    order = case["order"]
    _require(type(order) is list and len(order) == 4, "invalid condition order")
    _require(
        all(type(cell) is str and cell in CELLS for cell in order),
        "invalid condition label",
    )
    _require(len(set(order)) == 4, "duplicate condition label")
    return case["draw_index"]


def validate_design(raw: object) -> dict[str, Any]:
    design = _record(raw, _FIELDS)
    _require(
        type(design["schema"]) is str and design["schema"] == SCHEMA,
        "invalid design schema",
    )
    _require(_text(design["secret"], _SECRET, 64), "invalid design secret")
    _require(type(design["development"]) is bool, "invalid development flag")
    cases = design["cases"]
    _require(
        type(cases) is list and 4 <= len(cases) <= 64 and len(cases) % 4 == 0,
        "invalid cohort size",
    )
    n = len(cases)
    checked = design["candidates_checked"]
    _require(_integer(checked, n, 64 * n), "invalid candidate count")
    rejections = _record(design["rejections"], frozenset({"non_focal"}))
    _require(
        _integer(rejections["non_focal"], checked - n, checked - n),
        "invalid rejection count",
    )
    _require(_text(design["stream_receipt"], _DIGEST, 71), "invalid stream receipt")
    previous_index = 0
    for ordinal, case in enumerate(cases, 1):
        previous_index = _validate_case(case, ordinal, previous_index, checked)
    _require(previous_index == checked, "invalid terminal draw index")
    expected = make_design(
        n, bytes.fromhex(design["secret"]), development=design["development"]
    )
    _require(
        _canonical(design) == _canonical(expected),
        "design differs from exact derivation",
    )
    return expected
