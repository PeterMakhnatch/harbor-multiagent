from typing import Any


def _guard(view: dict[str, Any], role: str, phase: str) -> None:
    if (
        view.get("role") != role
        or view.get("phase") != phase
        or view.get("turn") != role
    ):
        raise ValueError(f"expected {role} in {phase} phase")


def _current(
    cards: list[dict[str, Any]], key: str, epoch: int
) -> dict[str, Any] | None:
    if len(cards) > 24:
        raise ValueError("invalid archive size")
    matches = [
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    if len(matches) > 1:
        raise ValueError("ambiguous current record")
    return matches[0] if matches else None


def _file(view: dict[str, Any], keys: list[str], epoch: int) -> dict[str, Any]:
    if len(keys) != 2 or len(set(keys)) != 2 or view["quota"] < 2:
        raise ValueError("expected two distinct filing keys")
    ids = []
    for key in keys:
        card = _current(view["cards"], key, epoch)
        if card is None:
            raise ValueError("missing current filing record")
        ids.append(card["id"])
    if len(set(ids)) != 2:
        raise ValueError("duplicate filing record")
    return {"op": "file", "ids": sorted(ids)}


def nominate(view: dict[str, Any]) -> dict[str, Any]:
    _guard(view, "loader", "request")
    briefs = view["briefs"]
    if len(briefs) != 3 or view["nominate"] < 1:
        raise ValueError("expected public fork briefs")
    return {"op": "request", "slips": [min(brief["id"] for brief in briefs)]}


def registrar(view: dict[str, Any]) -> dict[str, Any]:
    _guard(view, "registrar", "registrar")
    if len(view["briefs"]) != 1:
        raise ValueError("expected a single nominated brief")
    brief = view["briefs"][0]
    return _file(view, brief["items"], brief["epoch"])


def depot(view: dict[str, Any], policy: str) -> dict[str, Any]:
    _guard(view, "depot", "depot")
    agenda = view["agenda"]
    if len(agenda) != 2 or agenda[0]["epoch"] != agenda[1]["epoch"]:
        raise ValueError("expected two agenda crates at one epoch")
    keys = {entry["key"] for entry in agenda}
    crates = view["crates"]
    if (
        len(crates) != 8
        or len(set(crates)) != 8
        or len(keys) != 2
        or not keys <= set(crates)
    ):
        raise ValueError("invalid agenda crate domain")
    if policy == "relevant":
        selected = sorted(keys)
    elif policy == "other":
        selected = sorted(set(crates) - keys)[:2]
    else:
        raise ValueError("invalid depot policy")
    return _file(view, selected, agenda[0]["epoch"])


def follow(view: dict[str, Any]) -> dict[str, Any] | None:
    _guard(view, "loader", "dispatch")
    order = view["order"]
    if order is None:
        return None
    items, epoch = order["items"], order["epoch"]
    if len(items) != 2 or len(set(items)) != 2:
        raise ValueError("invalid dispatch items")
    routes = {}
    for item in items:
        record = _current(view["registrar_file"], item, epoch)
        if record is None:
            return None
        location = _current(view["depot_file"], record["value"], epoch)
        if location is None:
            return None
        if location["value"] not in view["bays"]:
            raise ValueError("invalid dispatch bay")
        routes[item] = location["value"]
    if len(set(routes.values())) != 2:
        raise ValueError("duplicate dispatch bay")
    return {"op": "commit", "routes": dict(sorted(routes.items()))}
