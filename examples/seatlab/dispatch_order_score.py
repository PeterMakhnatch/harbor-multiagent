from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from .capsule import _reject_constant, _unique_object
from .dispatch_order_pair import report_bests

if TYPE_CHECKING:
    from .dispatch_order_pair import OrderPair


_BAYS = frozenset("ABCD")
_State = Literal["valid", "invalid", "missing"]


def _decode_rows(text: str, shipments: int) -> dict[int, str]:
    value = json.loads(
        text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(value, dict) or value.keys() != {"rows"}:
        raise ValueError("expected exactly the rows field")
    rows = value["rows"]
    if not isinstance(rows, list) or len(rows) != shipments:
        raise ValueError("expected one row per shipment")
    choices: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict) or row.keys() != {"id", "bay"}:
            raise ValueError("expected exactly the id and bay fields")
        subject, bay = row["id"], row["bay"]
        if (
            type(subject) is not int
            or not 0 <= subject < shipments
            or subject in choices
        ):
            raise ValueError("expected distinct shipment IDs in range")
        if type(bay) is not str or bay not in _BAYS:
            raise ValueError("expected a stock bay")
        choices[subject] = bay
    return choices


def _classify(
    observed: tuple[str, str] | None, shipments: int
) -> tuple[_State, dict[int, str] | None]:
    if observed is None:
        return "missing", None
    try:
        return "valid", _decode_rows(observed[0], shipments)
    except (ValueError, RecursionError):
        return "invalid", None


def _count(
    pair: OrderPair, chronological: dict[int, str], reverse: dict[int, str]
) -> dict[str, int]:
    counts = {
        "shipments": len(pair.cases),
        "different_recommendations": 0,
        "changed_choice": 0,
        "changed_on_different_recommendations": 0,
        "chronological_latest": 0,
        "reversed_latest": 0,
        "chronological_optimal": 0,
        "reversed_optimal": 0,
        "chronological_feasible": 0,
        "reversed_feasible": 0,
    }
    for case in pair.cases:
        subject = case["id"]
        earlier, latest = report_bests(case)
        after = case["after"]
        optimal = max(after, key=case["utilities"].__getitem__)
        first, second = chronological[subject], reverse[subject]
        different = earlier != latest
        changed = first != second
        counts["different_recommendations"] += different
        counts["changed_choice"] += changed
        counts["changed_on_different_recommendations"] += different and changed
        counts["chronological_latest"] += first == latest
        counts["reversed_latest"] += second == latest
        counts["chronological_optimal"] += first == optimal
        counts["reversed_optimal"] += second == optimal
        counts["chronological_feasible"] += first in after
        counts["reversed_feasible"] += second in after
    return counts


def _check_routes(pairs: list[OrderPair], replies: dict[str, tuple[str, str]]) -> None:
    blocks: set[int] = set()
    routes: set[str] = set()
    for pair in pairs:
        if pair.block in blocks:
            raise ValueError("duplicate block")
        blocks.add(pair.block)
        for arm in (pair.chronological, pair.reversed):
            if arm.id in routes:
                raise ValueError("duplicate reply route")
            routes.add(arm.id)
    if not replies.keys() <= routes:
        raise ValueError("unknown reply route")


def score_pairs(
    pairs: list[OrderPair], replies: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    _check_routes(pairs, replies)
    coverage = {
        "expected_replies": 2 * len(pairs),
        "valid": 0,
        "invalid": 0,
        "missing": 0,
        "paired_worlds": 0,
        "expected_worlds": len(pairs),
    }
    worlds: list[dict[str, object]] = []
    totals: dict[str, int] | None = None
    for pair in sorted(pairs, key=lambda pair: pair.block):
        shipments = len(pair.cases)
        chrono_state, chronological = _classify(
            replies.get(pair.chronological.id), shipments
        )
        reverse_state, reverse = _classify(replies.get(pair.reversed.id), shipments)
        coverage[chrono_state] += 1
        coverage[reverse_state] += 1
        counts = None
        if chronological is not None and reverse is not None:
            counts = _count(pair, chronological, reverse)
            coverage["paired_worlds"] += 1
            if totals is None:
                totals = counts.copy()
            else:
                for field, count in counts.items():
                    totals[field] += count
        worlds.append(
            {
                "block": pair.block,
                "chronological": chrono_state,
                "reversed": reverse_state,
                "counts": counts,
            }
        )
    return {
        "schema": "seatlab.dispatch-order-score/v1",
        "coverage": coverage,
        "worlds": worlds,
        "paired_totals": totals,
    }
