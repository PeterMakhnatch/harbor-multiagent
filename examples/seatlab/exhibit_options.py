from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any

from .exhibit_saved import accepted_prefix, owner_view, read_cut

SCHEMA = "seatlab.exhibit-options/v1"


def _card(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any]:
    return next(
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    )


def _true_requirements(
    case: dict[str, Any], registrar_ids: set[str]
) -> list[tuple[int, tuple[str | None, ...]]]:
    requirements = []
    for brief in case["briefs"]:
        epoch = brief["epoch"]
        items = []
        for item in brief["items"]:
            source = _card(case["registrar"], item, epoch)
            target = _card(case["depot"], source["value"], epoch)
            items.append(target["id"] if source["id"] in registrar_ids else None)
        requirements.append((brief["weight"], tuple(items)))
    return requirements


def _proxy_requirements(
    view: dict[str, Any],
) -> tuple[list[tuple[int, frozenset[str]]], list[tuple[int, frozenset[str]]]]:
    agenda = {(row["key"], row["epoch"]) for row in view["agenda"] if row["weight"] > 0}
    proxy = []
    agenda_backed = []
    for row in view["bundles"]:
        if row["weight"] <= 0:
            continue
        epoch = row["epoch"]
        pair = frozenset(_card(view["cards"], key, epoch)["id"] for key in row["keys"])
        requirement = (row["weight"], pair)
        proxy.append(requirement)
        if all((key, epoch) in agenda for key in row["keys"]):
            agenda_backed.append(requirement)
    return proxy, agenda_backed


def _true_score(
    selected: frozenset[str],
    truth: list[tuple[int, tuple[str | None, ...]]],
) -> tuple[int, int]:
    complete = item = 0
    for weight, requirements in truth:
        count = sum(
            target is not None and target in selected for target in requirements
        )
        complete += weight if count == len(requirements) else 0
        item += count * weight
    return complete, item


def _proxy_score(
    selected: frozenset[str], proxy: list[tuple[int, frozenset[str]]]
) -> int | None:
    return sum(weight for weight, pair in proxy if pair <= selected) if proxy else None


def _ranked_options(scores: list[tuple[int, int | None]]) -> dict[str, int] | None:
    ranks = [rank for _, rank in scores if rank is not None]
    if not ranks:
        return None
    maximum = max(ranks)
    tied = [complete for complete, rank in scores if rank == maximum]
    return {
        "max_score": maximum,
        "tied_file_count": len(tied),
        "min_true_C_weight": min(tied),
        "max_true_C_weight": max(tied),
    }


def analyze(cut: dict[str, Any]) -> dict[str, Any]:
    prefix = accepted_prefix(cut)
    case, mode, events = prefix["case"], prefix["mode"], cut["events"]
    view = owner_view(prefix, mode)
    registrar = prefix["events"][1]["action"]
    truth = _true_requirements(case, set(registrar["ids"]))
    proxy, agenda_backed = _proxy_requirements(view)
    ids = sorted(card["id"] for card in view["cards"])
    assert case["quota"] == view["quota"] == 2 and len(ids) <= 24
    assert sum(weight for weight, _ in truth) == 10
    scores = {}
    for size in range(3):
        for selection in combinations(ids, size):
            selected = frozenset(selection)
            complete, item = _true_score(selected, truth)
            scores[selected] = (
                complete,
                item,
                _proxy_score(selected, proxy),
                _proxy_score(selected, agenda_backed),
            )
    assert 1 <= len(scores) <= 301
    ranked = _ranked_options(
        [(complete, rank) for complete, _, rank, _ in scores.values()]
    )
    agenda_ranked = _ranked_options(
        [(complete, rank) for complete, _, _, rank in scores.values()]
    )
    actual = None
    if len(events) >= 3:
        complete, item, rank, agenda_rank = scores[
            frozenset(events[2]["action"]["ids"])
        ]
        actual = {
            "C_weight": complete,
            "P_item_weight": item,
            "proxy_score": rank,
            "attains_proxy_optimum": rank == ranked["max_score"]
            if ranked is not None
            else None,
            "agenda_backed_proxy_score": agenda_rank,
            "attains_agenda_backed_optimum": agenda_rank == agenda_ranked["max_score"]
            if agenda_ranked is not None
            else None,
        }
    return {
        "schema": SCHEMA,
        "retained_source": "declared-local",
        "mode": mode,
        "accepted_event_count": len(events),
        "total_weight": sum(weight for weight, _ in truth),
        "total_item_weight": sum(weight * len(items) for weight, items in truth),
        "legal_file_count": len(scores),
        "true_max_C_weight": max(complete for complete, _, _, _ in scores.values()),
        "proxy": ranked,
        "agenda_backed_proxy": agenda_ranked,
        "proxy_definition": "Sum of positive delivered reported pair weights for which both epoch-current private depot cards are selected, regardless of forecast truth or registrar filing.",
        "agenda_backed_proxy_definition": "Same sum, restricted to reported pairs with both crate and epoch entries in the positive flat depot agenda.",
        "scope": "Enumeration conditional on the actual accepted prefix; explicit diagnostic proxy, not a model, estimate, or evidence of cognitive use.",
        "actual": actual,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enumerate depot options at a retained accepted authored prefix"
    )
    parser.add_argument("cut", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = analyze(read_cut(args.cut))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        OverflowError,
        RecursionError,
    ) as exc:
        parser.exit(2, f"Invalid retained cut ({type(exc).__name__}).\n")
    print(
        json.dumps(
            result, sort_keys=True, indent=None if args.json else 2, allow_nan=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
