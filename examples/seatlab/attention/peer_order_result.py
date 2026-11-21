import argparse
import json
import os
import stat
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Never

from . import ranking

RECEIPT_SCHEMA = "seatlab.dispatch-peer-order/v1"
_ARMS = ("report", "utility")
_REPETITIONS = (0, 1)
_STATUSES = ("complete", "invalid", "missing", "not_attempted")
_ROW_KEYS = {
    "seed",
    "arm",
    "repetition",
    "status",
    "follows_best_visible",
    "optimal",
    "feasible",
}
_WORLD_COUNT = 16
_SHIPMENTS = 6
_SHIPMENTS_PER_ARM = _WORLD_COUNT * len(_REPETITIONS) * _SHIPMENTS
_MIN_DELTA = 20
_ALPHA_DENOMINATOR = 20
_MAX_RECEIPT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class _Counts:
    follows_best_visible: int
    optimal: int
    feasible: int


@dataclass(frozen=True, slots=True)
class _Cell:
    status: str
    counts: _Counts | None


_Identity = tuple[int, str, int]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError
    return fields


def _reject_constant(_: str) -> Never:
    raise ValueError


def _read_receipt(path: Path) -> Any:
    with open(
        path,
        "rb",
        opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK | os.O_NOFOLLOW),
    ) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_RECEIPT_BYTES:
            raise ValueError
        content = source.read(_MAX_RECEIPT_BYTES + 1)
    if len(content) > _MAX_RECEIPT_BYTES:
        raise ValueError
    return json.loads(
        content.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _SHIPMENTS:
        raise ValueError
    return value


def _cell(row: dict[str, Any]) -> _Cell:
    status = row["status"]
    if not isinstance(status, str) or status not in _STATUSES:
        raise ValueError
    follows, optimal, feasible = (
        row["follows_best_visible"],
        row["optimal"],
        row["feasible"],
    )
    if status != "complete":
        if follows is not None or optimal is not None or feasible is not None:
            raise ValueError
        return _Cell(status, None)
    counts = _Counts(_count(follows), _count(optimal), _count(feasible))
    if counts.optimal > counts.feasible:
        raise ValueError
    return _Cell(status, counts)


def _enroll(document: Any) -> dict[_Identity, _Cell]:
    seeds = {entry["seed"] for entry in ranking.grid()}
    expected = set(product(seeds, _ARMS, _REPETITIONS))
    if (
        len(seeds) != _WORLD_COUNT
        or not isinstance(document, dict)
        or document.keys() != {"schema", "cells"}
        or document["schema"] != RECEIPT_SCHEMA
        or not isinstance(document["cells"], list)
        or len(document["cells"]) != len(expected)
    ):
        raise ValueError
    cells: dict[_Identity, _Cell] = {}
    for row in document["cells"]:
        if not isinstance(row, dict) or row.keys() != _ROW_KEYS:
            raise ValueError
        seed, arm, repetition = row["seed"], row["arm"], row["repetition"]
        if (
            type(seed) is not int
            or not isinstance(arm, str)
            or type(repetition) is not int
        ):
            raise ValueError
        identity = (seed, arm, repetition)
        if identity not in expected:
            raise ValueError
        expected.remove(identity)
        cells[identity] = _cell(row)
    return cells


def _arm_summary(cells: list[_Cell]) -> dict[str, int]:
    coverage = Counter(cell.status for cell in cells)
    counts = [cell.counts for cell in cells if cell.counts is not None]
    return {
        "enrolled": len(cells),
        **{status: coverage[status] for status in _STATUSES},
        "eligible_shipments": len(counts) * _SHIPMENTS,
        "follows_best_visible": sum(count.follows_best_visible for count in counts),
        "optimal": sum(count.optimal for count in counts),
        "feasible": sum(count.feasible for count in counts),
    }


def _world_delta(cells: dict[_Identity, _Cell], seed: int) -> int | None:
    delta = 0
    for repetition in _REPETITIONS:
        report = cells[seed, "report", repetition].counts
        utility = cells[seed, "utility", repetition].counts
        if report is None or utility is None:
            return None
        delta += utility.follows_best_visible - report.follows_best_visible
    return delta


def _signflip_extremes(deltas: list[int], observed: int) -> int:
    return sum(
        abs(sum(draw)) >= abs(observed)
        for draw in product(*((-delta, delta) for delta in deltas))
    )


def _summarize(cells: dict[_Identity, _Cell]) -> dict[str, Any]:
    arms = {
        arm: _arm_summary(
            [cell for (_, treatment, _), cell in cells.items() if arm == treatment]
        )
        for arm in _ARMS
    }
    seeds = sorted({seed for seed, _, _ in cells})
    deltas = [
        delta for seed in seeds if (delta := _world_delta(cells, seed)) is not None
    ]
    complete = len(deltas) == _WORLD_COUNT
    difference = None
    p_value = None
    verdict = None
    if complete:
        observed = sum(deltas)
        numerator = _signflip_extremes(deltas, observed)
        denominator = 1 << _WORLD_COUNT
        difference = {"numerator": observed, "denominator": _SHIPMENTS_PER_ARM}
        p_value = {"numerator": numerator, "denominator": denominator}
        verdict = (
            "pass"
            if observed >= _MIN_DELTA and numerator * _ALPHA_DENOMINATOR <= denominator
            else "not_established"
        )
    return {
        "status": "complete" if complete else "incomplete",
        "verdict": verdict,
        "arms": arms,
        "primary": {
            "matched_worlds": len(deltas),
            "follows_best_visible_delta": difference,
            "p_value": p_value,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        result = _summarize(_enroll(_read_receipt(args.receipt)))
    except (OSError, ValueError, RecursionError):
        parser.error("cannot read a valid peer order receipt")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
