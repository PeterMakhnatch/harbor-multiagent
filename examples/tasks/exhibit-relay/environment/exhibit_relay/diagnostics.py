import hashlib
import json
from typing import Any

from .frontier import analyze_frontier
from .grading import grade

_STAGES = ("request", "registrar", "depot", "dispatch", "done")


def _filing(
    case: dict[str, Any], actions: list[dict[str, Any]], index: int, role: str
) -> list[dict[str, Any]] | None:
    if len(actions) <= index:
        return None
    selected = set(actions[index]["ids"])
    return [card for card in case[role] if card["id"] in selected]


def _card(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any] | None:
    return next(
        (
            card
            for card in cards
            if card["key"] == key and card["start"] <= epoch <= card["end"]
        ),
        None,
    )


def case_commitment(case: dict[str, Any]) -> str:
    body = json.dumps(case, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(body.encode("ascii")).hexdigest()


def observation(cut: dict[str, Any]) -> dict[str, Any]:
    result = grade(cut)
    case = cut["case"]
    mode = cut["mode"]
    frontier = analyze_frontier(case, mode)
    actions = [event["action"] for event in cut["events"]]
    nominated = set(actions[0]["slips"]) if actions else set()
    registrar = _filing(case, actions, 1, "registrar")
    depot = _filing(case, actions, 2, "depot")
    nominated_weight = 0
    potential_weight = 0
    missing_weight = 0
    supported_weight = 0
    supported_item_weight = 0
    for brief in case["briefs"]:
        weight = brief["weight"]
        is_nominated = brief["id"] in nominated
        if is_nominated:
            nominated_weight += weight
        supported_items = 0
        for item in brief["items"]:
            if registrar is None:
                continue
            card = _card(registrar, item, brief["epoch"])
            if card is None:
                continue
            if is_nominated:
                potential_weight += weight
            if depot is None:
                continue
            if _card(depot, card["value"], brief["epoch"]) is None:
                if is_nominated:
                    missing_weight += weight
            elif mode != "silent":
                supported_items += 1
        supported_item_weight += supported_items * weight
        if supported_items == len(brief["items"]):
            supported_weight += weight
    return {
        "schema": "harbor.exhibit-relay.observation/v1",
        "mode": mode,
        "case_commitment": case_commitment(case),
        "profile": case["profile"],
        **result,
        "handoff": {
            "total_weight": frontier["weight"],
            "explicit_ceiling_weight": frontier["ceiling_weight"],
            "nominated_weight": nominated_weight if actions else None,
            "nominated_count": len(nominated) if actions else None,
            "registrar_filed": len(registrar) if registrar is not None else None,
            "depot_filed": len(depot) if depot is not None else None,
            "potential_intent_weight": potential_weight
            if registrar is not None
            else None,
            "missing_depot_intent_weight": missing_weight
            if depot is not None
            else None,
            "visible_supported_weight": supported_weight if depot is not None else None,
            "visible_item_weight": supported_item_weight if depot is not None else None,
            "stage": _STAGES[len(actions)],
        },
    }
