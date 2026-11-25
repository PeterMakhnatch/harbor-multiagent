from typing import Any

from .cases import capacity, valid_mode, validate_case

_STEPS = (
    ("loader", "request"),
    ("registrar", "file"),
    ("depot", "file"),
    ("loader", "commit"),
)


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("invalid relay evidence")


def _object(value: object, keys: set[str]) -> None:
    _require(type(value) is dict and value.keys() == keys)


def _ids(value: object, allowed: set[str], cap: int) -> None:
    if not isinstance(value, list) or len(value) > cap:
        raise ValueError("invalid relay selection")
    _require(all(type(item) is str and item in allowed for item in value))
    _require(len(set(value)) == len(value) and value == sorted(value))


def _validate_event(
    event: dict[str, Any],
    index: int,
    case: dict[str, Any],
    mode: str,
    order: dict[str, Any],
) -> None:
    _object(event, {"seq", "role", "action"})
    role, op = _STEPS[index]
    _require(type(event["seq"]) is int and event["seq"] == index + 1)
    _require(type(event["role"]) is str and event["role"] == role)
    action = event["action"]
    field = ("slips", "ids", "ids", "routes")[index]
    _object(action, {"op", field})
    _require(type(action["op"]) is str and action["op"] == op)
    if index == 0:
        allowed = {brief["id"] for brief in case["briefs"]}
        cap = len(allowed) if mode == "open" else case["nominate"]
        _ids(action[field], allowed, cap)
    elif index < 3:
        allowed = {card["id"] for card in case[role]}
        cap = capacity(case, role, mode)
        _ids(action[field], allowed, cap)
    else:
        routes = action[field]
        _object(routes, set(order["items"]))
        _require(all(type(key) is str for key in routes))
        _require(
            all(type(bay) is str and bay in case["bays"] for bay in routes.values())
        )


def _result() -> dict[str, Any]:
    return {
        "status": "incomplete",
        "reward": None,
        "correct_items": None,
        "total_items": 2,
        "registrar_supported": None,
        "depot_supported": None,
        "chains_supported": None,
        "dispatch_supported": None,
    }


def _current_card(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any]:
    matches = [
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    _require(len(matches) == 1)
    return matches[0]


def grade(cut: dict[str, Any]) -> dict[str, Any]:
    _object(cut, {"version", "case", "mode", "events"})
    _require(type(cut["version"]) is int and cut["version"] == 1)
    mode = cut["mode"]
    _require(type(mode) is str)
    events = cut["events"]
    _require(type(events) is list and len(events) <= len(_STEPS))
    case = cut["case"]
    validate_case(case)
    _require(valid_mode(case, mode))
    order = next(brief for brief in case["briefs"] if brief["id"] == case["realized"])
    for index, event in enumerate(events):
        _validate_event(event, index, case, mode, order)
    result = _result()
    if len(events) != len(_STEPS):
        return result

    registrar_ids = set(events[1]["action"]["ids"]) if mode != "silent" else set()
    depot_ids = set(events[2]["action"]["ids"]) if mode != "silent" else set()
    routes = events[3]["action"]["routes"]
    epoch = order["epoch"]
    correct = registrar = depot = chains = dispatch = 0
    for exhibit in order["items"]:
        crate_card = _current_card(case["registrar"], exhibit, epoch)
        bay_card = _current_card(case["depot"], crate_card["value"], epoch)
        is_correct = routes[exhibit] == bay_card["value"]
        has_registrar = crate_card["id"] in registrar_ids
        has_depot = bay_card["id"] in depot_ids
        has_chain = has_registrar and has_depot
        correct += is_correct
        registrar += has_registrar
        depot += has_depot
        chains += has_chain
        dispatch += has_chain and is_correct
    result.update(
        status="complete",
        reward=int(correct == 2),
        correct_items=correct,
        registrar_supported=registrar,
        depot_supported=depot,
        chains_supported=chains,
        dispatch_supported=dispatch,
    )
    return result
