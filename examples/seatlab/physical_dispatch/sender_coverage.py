from collections import Counter
from typing import Any

from ..exhibit_authored import require


def _filing(view: dict[str, Any], action: dict[str, Any]) -> dict[str, int]:
    role = view["role"]
    if role == "registrar":
        targets = {
            (item, brief["epoch"])
            for brief in view["briefs"]
            for item in brief["items"]
        }
    else:
        require(role == "depot", "expected a filing role")
        targets = {(entry["key"], entry["epoch"]) for entry in view["agenda"]}
    require(
        len(targets) <= 2 * view["nominate"], "sender target count exceeds nomination"
    )
    capacity = view["quota"]
    files = set(action["ids"])
    require(
        action["op"] == "file" and len(files) <= capacity,
        "filing exceeds sender capacity",
    )
    needed: Counter[str] = Counter()
    for key, epoch in targets:
        matches = [
            card["id"]
            for card in view["cards"]
            if card["key"] == key and card["start"] <= epoch <= card["end"]
        ]
        require(
            len(matches) == 1,
            "sender lacks a unique current record for a visible target",
        )
        needed[matches[0]] += 1
    filed = sum(n for card, n in needed.items() if card in files)
    ceiling = sum(n for _, n in needed.most_common(capacity))
    require(0 <= filed <= ceiling <= len(targets), "sender capacity invariant failed")
    return {
        "target_facts": len(targets),
        "records_needed": len(needed),
        "capacity": capacity,
        "records_filed": len(files),
        "target_records_filed": len(files.intersection(needed)),
        "target_facts_filed": filed,
        "max_target_facts_at_capacity": ceiling,
    }


def local_coverage(
    events: list[dict[str, Any]], views: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    count = len(events)
    require(0 <= count <= 4, "invalid number of accepted actions")
    nomination = None
    if count >= 1:
        first = views["request"]
        briefs = first["briefs"]
        slips = set(events[0]["action"]["slips"])
        require(len(slips) <= first["nominate"], "nomination exceeds capacity")
        nomination = {
            "briefs_available": len(briefs),
            "capacity": first["nominate"],
            "briefs_requested": len(slips),
            "available_weight": sum(b["weight"] for b in briefs),
            "requested_weight": sum(b["weight"] for b in briefs if b["id"] in slips),
            "realized_brief_requested": views["dispatch"]["order"]["id"] in slips
            if count >= 3
            else None,
        }
    return {
        "schema": "seatlab.exhibit-relay-physical.sender-coverage/v1",
        "nomination": nomination,
        "registrar": _filing(views["registrar"], events[1]["action"])
        if count >= 2
        else None,
        "depot": _filing(views["depot"], events[2]["action"]) if count >= 3 else None,
        "scope": "Distinct (key, epoch) targets in each sender's own view, not the eventual order. Filing coverage and its capacity bound are unweighted. They do not measure strategy, attention, or how much a choice changed reward.",
    }
