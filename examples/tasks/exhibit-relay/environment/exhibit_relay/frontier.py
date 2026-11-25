from itertools import combinations
from typing import Any

from .cases import capacity, valid_mode


def _requirements(
    case: dict[str, Any],
) -> list[tuple[str, int, frozenset[str], frozenset[str]]]:
    registrar = {
        (card["key"], epoch): card
        for card in case["registrar"]
        for epoch in range(card["start"], card["end"] + 1)
    }
    depot = {
        (card["key"], epoch): card
        for card in case["depot"]
        for epoch in range(card["start"], card["end"] + 1)
    }
    requirements = []
    for brief in case["briefs"]:
        epoch = brief["epoch"]
        sources = [registrar[item, epoch] for item in brief["items"]]
        requirements.append(
            (
                brief["id"],
                brief["weight"],
                frozenset(card["id"] for card in sources),
                frozenset(depot[card["value"], epoch]["id"] for card in sources),
            )
        )
    return requirements


def _coverage(
    requirements: list[tuple[str, int, frozenset[str], frozenset[str]]],
    registrar_ids: frozenset[str],
    depot_ids: frozenset[str],
) -> dict[str, Any]:
    covered = [
        (brief_id, weight)
        for brief_id, weight, sources, destinations in requirements
        if sources <= registrar_ids and destinations <= depot_ids
    ]
    return {
        "weight": sum(weight for _, weight, _, _ in requirements),
        "covered_weight": sum(weight for _, weight in covered),
        "covered_briefs": sorted(brief_id for brief_id, _ in covered),
    }


def file_coverage(
    case: dict[str, Any], registrar_ids: list[str], depot_ids: list[str]
) -> dict[str, Any]:
    return _coverage(
        _requirements(case), frozenset(registrar_ids), frozenset(depot_ids)
    )


def analyze_frontier(case: dict[str, Any], mode: str) -> dict[str, Any]:
    if not valid_mode(case, mode):
        raise ValueError("invalid mode")
    requirements = _requirements(case)
    assert 1 <= len(requirements) <= 6
    registrar_budget = capacity(case, "registrar", mode)
    depot_budget = capacity(case, "depot", mode)
    best_key = None
    best = None
    max_size = 0 if mode == "silent" else len(requirements)
    for size in range(max_size + 1):
        for subset in combinations(requirements, size):
            sources = frozenset().union(*(row[2] for row in subset))
            destinations = frozenset().union(*(row[3] for row in subset))
            if len(sources) > registrar_budget or len(destinations) > depot_budget:
                continue
            coverage = _coverage(requirements, sources, destinations)
            registrar_ids = sorted(sources)
            depot_ids = sorted(destinations)
            key = (
                -coverage["covered_weight"],
                len(sources) + len(destinations),
                len(sources),
                registrar_ids,
                depot_ids,
            )
            if best_key is None or key < best_key:
                best_key = key
                best = {
                    "weight": coverage["weight"],
                    "ceiling_weight": coverage["covered_weight"],
                    "highest_single_weight": max(row[1] for row in requirements),
                    "covered_briefs": coverage["covered_briefs"],
                    "registrar_ids": registrar_ids,
                    "depot_ids": depot_ids,
                    "min_registrar": len(sources),
                    "min_depot": len(destinations),
                }
    assert best is not None
    return best
