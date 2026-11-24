from __future__ import annotations

from fractions import Fraction
from typing import Any

from .capsule import view_hash
from .dispatch_order_pair import OrderArm, OrderPair, report_bests
from .dispatch_order_score import _classify

_SCHEMA = "seatlab.dispatch-order-repeat/v1"
_REPEAT_DOMAIN = "seatlab.dispatch-order-independent-repeat/v1"
_OUTCOMES = (
    "chronological_latest",
    "reversed_latest",
    "chronological_optimal",
    "reversed_optimal",
    "chronological_feasible",
    "reversed_feasible",
)


def repeat_arm(arm: OrderArm) -> OrderArm:
    route = view_hash([_REPEAT_DOMAIN, arm.id])
    return OrderArm(route, arm.prompt_sha256, arm.messages)


def allocate(pair: OrderPair, wave: int) -> OrderArm:
    if type(wave) is not int or not 0 <= wave < 4:
        raise ValueError("invalid repeat wave")
    position = wave if wave < 2 else 3 - wave
    arm = pair.chronological if position == pair.block % 2 else pair.reversed
    return arm if wave < 2 else repeat_arm(arm)


def _count_repeats(pair: OrderPair, draws: list[dict[int, str]]) -> dict[str, int]:
    n = len(pair.cases)
    counts = dict.fromkeys(_OUTCOMES, 0)
    counts.update(
        shipments=n,
        informative=0,
        within_comparisons=2 * n,
        cross_comparisons=4 * n,
        within_disagreements=0,
        cross_disagreements=0,
        informative_within_disagreements=0,
        informative_cross_disagreements=0,
    )
    for case in pair.cases:
        i = case["id"]
        a, b, c, d = (draw[i] for draw in draws)
        previous, latest = report_bests(case)
        stock = case["after"]
        optimal = max(stock, key=case["utilities"].__getitem__)
        within = (a != c) + (b != d)
        cross = (a != b) + (a != d) + (c != b) + (c != d)
        counts["within_disagreements"] += within
        counts["cross_disagreements"] += cross
        if previous != latest:
            counts["informative"] += 1
            counts["informative_within_disagreements"] += within
            counts["informative_cross_disagreements"] += cross
        counts["chronological_latest"] += (a == latest) + (c == latest)
        counts["reversed_latest"] += (b == latest) + (d == latest)
        counts["chronological_optimal"] += (a == optimal) + (c == optimal)
        counts["reversed_optimal"] += (b == optimal) + (d == optimal)
        counts["chronological_feasible"] += (a in stock) + (c in stock)
        counts["reversed_feasible"] += (b in stock) + (d in stock)
    return counts


def _routes(pairs: list[OrderPair]) -> dict[int, tuple[str, str, str, str]]:
    by_block: dict[int, tuple[str, str, str, str]] = {}
    seen: set[str] = set()
    for pair in pairs:
        if pair.block in by_block:
            raise ValueError("duplicate block")
        chronological, reverse = pair.chronological, pair.reversed
        routes = (
            chronological.id,
            reverse.id,
            repeat_arm(chronological).id,
            repeat_arm(reverse).id,
        )
        for route in routes:
            if route in seen:
                raise ValueError("duplicate reply route")
            seen.add(route)
        by_block[pair.block] = routes
    return by_block


def score_repeats(
    pairs: list[OrderPair], replies: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    routes = _routes(pairs)
    if replies.keys() - {route for block in routes.values() for route in block}:
        raise ValueError("unknown reply route")
    coverage = {
        "expected_worlds": len(pairs),
        "expected_replies": 4 * len(pairs),
        "valid": 0,
        "invalid": 0,
        "missing": 0,
        "complete_worlds": 0,
    }
    worlds: list[dict[str, object]] = []
    totals: dict[str, int] | None = None
    for pair in sorted(pairs, key=lambda pair: pair.block):
        states: list[str] = []
        draws: list[dict[int, str]] = []
        shipments = len(pair.cases)
        for route in routes[pair.block]:
            state, choices = _classify(replies.get(route), shipments)
            coverage[state] += 1
            states.append(state)
            if choices is not None:
                draws.append(choices)
        counts = None
        if len(draws) == 4:
            counts = _count_repeats(pair, draws)
            coverage["complete_worlds"] += 1
            if totals is None:
                totals = counts.copy()
            else:
                for key, count in counts.items():
                    totals[key] += count
        worlds.append({"block": pair.block, "states": states, "counts": counts})
    return {
        "schema": _SCHEMA,
        "coverage": coverage,
        "worlds": worlds,
        "complete_totals": totals,
    }


def equal_world_excess(report: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "worlds": None,
        "informative_worlds": None,
        "all": None,
        "informative": None,
    }
    if report["coverage"]["complete_worlds"] != report["coverage"]["expected_worlds"]:
        return result

    values: dict[str, list[Fraction]] = {"all": [], "informative": []}
    for world in report["worlds"]:
        counts = world["counts"]
        for outcome, size, prefix in (
            ("all", "shipments", ""),
            ("informative", "informative", "informative_"),
        ):
            n = counts[size]
            if n:
                within = counts[f"{prefix}within_disagreements"]
                cross = counts[f"{prefix}cross_disagreements"]
                values[outcome].append(Fraction(cross - 2 * within, 4 * n))
    for outcome, rates in values.items():
        if rates:
            mean = sum(rates, Fraction()) / len(rates)
            result[outcome] = {
                "numerator": mean.numerator,
                "denominator": mean.denominator,
            }
    result["worlds"] = len(values["all"])
    result["informative_worlds"] = len(values["informative"])
    return result
