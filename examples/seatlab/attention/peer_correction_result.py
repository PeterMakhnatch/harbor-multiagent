import argparse
import json
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations, product
from pathlib import Path
from typing import Any

from .peer_order_result import _read_receipt, _signflip_extremes

RECEIPT_SCHEMA = "seatlab.dispatch-peer-correction/v1"
_SEEDS = range(517301, 517317)
_REPETITIONS = (0, 1)
_OPENED = 2
_UNOPENED = 4
_SHIPMENTS = _OPENED + _UNOPENED
_SHIPMENTS_PER_POLICY = len(_SEEDS) * len(_REPETITIONS) * _SHIPMENTS
_STATUSES = ("skipped", "valid", "invalid", "missing", "not_attempted")
_ROW_KEYS = {
    "seed",
    "repetition",
    "marked_opened",
    "marked_unopened",
    "status",
    "kept",
    "free",
    "selective",
}
_MIN_OPTIMAL_DELTA = 8
_ALPHA_DENOMINATOR = 20


@dataclass(frozen=True, slots=True)
class _Grade:
    optimal: int
    feasible: int


@dataclass(frozen=True, slots=True)
class _Cell:
    marked_opened: int
    marked_unopened: int
    status: str
    kept: _Grade
    free: _Grade
    selective: _Grade | None


_Identity = tuple[int, int]


def _count(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError
    return value


def _grade(value: object) -> _Grade:
    if not isinstance(value, dict) or value.keys() != {"optimal", "feasible"}:
        raise ValueError
    feasible = _count(value["feasible"], _SHIPMENTS)
    return _Grade(_count(value["optimal"], feasible), feasible)


def _cell(row: dict[str, Any]) -> _Cell:
    opened = _count(row["marked_opened"], _OPENED)
    unopened = _count(row["marked_unopened"], _UNOPENED)
    marked = opened + unopened
    status = row["status"]
    if (
        not isinstance(status, str)
        or status not in _STATUSES
        or (status == "skipped") != (marked == 0)
    ):
        raise ValueError
    kept, free = _grade(row["kept"]), _grade(row["free"])
    if status in ("missing", "not_attempted"):
        if row["selective"] is not None:
            raise ValueError
        selective = None
    else:
        selective = _grade(row["selective"])
        if status in ("skipped", "invalid") and selective != kept:
            raise ValueError
    known_grades = (kept, free) if selective is None else (kept, free, selective)
    for left, right in combinations(known_grades, 2):
        optimal_delta = left.optimal - right.optimal
        feasible_delta = left.feasible - right.feasible
        suboptimal_delta = feasible_delta - optimal_delta
        if max(abs(optimal_delta), abs(feasible_delta), abs(suboptimal_delta)) > marked:
            raise ValueError
    return _Cell(opened, unopened, status, kept, free, selective)


def _enroll(document: object) -> dict[_Identity, _Cell]:
    expected = set(product(_SEEDS, _REPETITIONS))
    if (
        not isinstance(document, dict)
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
        seed, repetition = row["seed"], row["repetition"]
        if type(seed) is not int or type(repetition) is not int:
            raise ValueError
        identity = (seed, repetition)
        if identity not in expected:
            raise ValueError
        expected.remove(identity)
        cells[identity] = _cell(row)
    return cells


def _totals(grades: Iterable[_Grade]) -> dict[str, int]:
    shipments = optimal = feasible = 0
    for grade in grades:
        shipments += _SHIPMENTS
        optimal += grade.optimal
        feasible += grade.feasible
    return {"shipments": shipments, "optimal": optimal, "feasible": feasible}


def _revision_summary(cells: Collection[_Cell], *, opened: bool) -> dict[str, Any]:
    proposals: Counter[str] = Counter()
    shipments: Counter[str] = Counter()
    for cell in cells:
        marked = cell.marked_opened if opened else cell.marked_unopened
        if marked:
            proposals[cell.status] += 1
            shipments[cell.status] += marked
    return {
        "shipments": len(cells) * (_OPENED if opened else _UNOPENED),
        "marked_shipments": sum(shipments.values()),
        "triggered_proposals": sum(proposals.values()),
        "proposals_by_status": {status: proposals[status] for status in _STATUSES},
        "marked_shipments_by_status": {
            status: shipments[status] for status in _STATUSES
        },
    }


def _world_delta(cells: dict[_Identity, _Cell], seed: int) -> dict[str, int] | None:
    minus_free = minus_kept = 0
    for repetition in _REPETITIONS:
        cell = cells[seed, repetition]
        if cell.selective is None:
            return None
        minus_free += cell.selective.optimal - cell.free.optimal
        minus_kept += cell.selective.optimal - cell.kept.optimal
    return {
        "seed": seed,
        "selective_minus_free": minus_free,
        "selective_minus_kept": minus_kept,
    }


def _summarize(cells: dict[_Identity, _Cell]) -> dict[str, Any]:
    coverage = Counter(cell.status for cell in cells.values())
    triggered = len(cells) - coverage["skipped"]
    worlds = [
        delta for seed in _SEEDS if (delta := _world_delta(cells, seed)) is not None
    ]
    complete = len(worlds) == len(_SEEDS)
    primary: dict[str, Any] = {
        "matched_worlds": len(worlds),
        "selective_minus_free": None,
        "p_value": None,
        "selective_minus_kept": None,
    }
    blocks = verdict = None
    if complete:
        deltas = [world["selective_minus_free"] for world in worlds]
        observed = sum(deltas)
        kept_delta = sum(world["selective_minus_kept"] for world in worlds)
        extremes = _signflip_extremes(deltas, observed)
        permutations = 1 << len(worlds)
        primary["selective_minus_free"] = {
            "numerator": observed,
            "denominator": _SHIPMENTS_PER_POLICY,
        }
        primary["p_value"] = {"numerator": extremes, "denominator": permutations}
        primary["selective_minus_kept"] = {
            "numerator": kept_delta,
            "denominator": _SHIPMENTS_PER_POLICY,
        }
        blocks = {
            "up": sum(delta > 0 for delta in deltas),
            "down": sum(delta < 0 for delta in deltas),
            "same": deltas.count(0),
            "deltas": worlds,
        }
        if triggered == 0:
            verdict = "no_calls"
        elif (
            observed >= _MIN_OPTIMAL_DELTA
            and extremes * _ALPHA_DENOMINATOR <= permutations
            and kept_delta >= 0
        ):
            verdict = "pass"
        else:
            verdict = "not_established"
    return {
        "status": "complete" if complete else "incomplete",
        "verdict": verdict,
        "coverage": {
            "enrolled": len(cells),
            "triggered": triggered,
            **{status: coverage[status] for status in _STATUSES},
        },
        "by_displayed_revision": {
            "opened": _revision_summary(cells.values(), opened=True),
            "unopened": _revision_summary(cells.values(), opened=False),
        },
        "policies": {
            "kept": _totals(cell.kept for cell in cells.values()),
            "free": _totals(cell.free for cell in cells.values()),
            "selective": _totals(
                cell.selective for cell in cells.values() if cell.selective is not None
            ),
        },
        "primary": primary,
        "worlds": blocks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        result = _summarize(_enroll(_read_receipt(args.receipt)))
    except (OSError, ValueError, RecursionError):
        parser.error("cannot read a valid peer correction receipt")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
