from typing import Any


def _destination(cards: list[dict[str, Any]], key: str, epoch: int) -> str:
    if not 8 <= len(cards) <= 24:
        raise ValueError("invalid native archive size")
    matches = [
        card["value"]
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    if len(matches) != 1 or type(matches[0]) is not str:
        raise ValueError("expected one active native destination")
    return matches[0]


def select(case: dict[str, Any], map_name: str) -> dict[str, Any]:
    if map_name not in ("original", "swapped"):
        raise ValueError("invalid bay map")
    if case.get("profile") != "fork" or len(case["briefs"]) != 3:
        raise ValueError("expected a native fork case")
    if map_name == "original":
        return case
    focal = min(case["briefs"], key=lambda brief: brief["id"])
    epoch = focal["epoch"]
    crates = [_destination(case["registrar"], item, epoch) for item in focal["items"]]
    left, right = (_destination(case["depot"], crate, epoch) for crate in crates)
    if left == right or left not in case["bays"] or right not in case["bays"]:
        raise ValueError("expected distinct physical destinations")
    swap = {left: right, right: left}
    return {
        **case,
        "depot": [
            {**card, "value": swap[card["value"]]} if card["value"] in swap else card
            for card in case["depot"]
        ],
    }
