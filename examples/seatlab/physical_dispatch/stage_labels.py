from typing import Any

from ..exhibit_authored import canonical, require
from .certificate import Session, grade
from .sender_coverage import local_coverage

SCHEMA = "seatlab.exhibit-relay-physical.stage-labels/v1"
CLASSES = ("current", "expired", "future", "expired_and_future", "other_key", "empty")
ROLES = ("loader", "registrar", "depot", "loader")
PHASES = ("request", "registrar", "depot", "dispatch", "done")
ITEMS = 2
CAVEAT = (
    "Delivered-record linkage for the realized order only. Null means unobserved; "
    "'empty' means an accepted filing with no records. An expired depot match can "
    "coincide with a live value and shows neither error nor use of a stale chain. "
    "These labels do not show attention, intent or policy."
)


def _replay(
    cut: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], int]:
    result = grade(cut)
    require(type(result) is dict, "invalid native grade")
    case = cut["case"]
    require(
        cut["mode"] == "linked" and case["profile"] == "fork", "not a linked fork cut"
    )
    events = cut["events"]
    count = len(events)
    require(0 <= count <= len(ROLES), "invalid accepted action count")
    require(
        (result["status"] == "complete") == (count == len(ROLES)),
        "native grade status differs from accepted action count",
    )
    session = Session(case, "linked")
    views: dict[str, dict[str, Any]] = {}
    for index in range(count + 1):
        if index < len(ROLES):
            view = session.view(ROLES[index])
            require(
                view["phase"] == PHASES[index] and view["turn"] == ROLES[index],
                "phase counter differs",
            )
            views[PHASES[index]] = view
        if index < count:
            require(events[index]["role"] == ROLES[index], "role order differs")
            session.apply(ROLES[index], events[index]["action"])
    require(session.view("loader")["phase"] == PHASES[count], "phase counter differs")
    require(
        canonical(session.snapshot()) == canonical(cut), "reconstructed cut mismatch"
    )
    return result, views, count


def _current(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any]:
    matches = [
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    require(len(matches) == 1, "expected one epoch-current record")
    return matches[0]


def _layer(cards: list[dict[str, Any]], key: str, epoch: int, quota: int) -> str:
    require(type(cards) is list and len(cards) <= quota, "unbounded delivered filing")
    if not cards:
        return "empty"
    keyed = [card for card in cards if card["key"] == key]
    if not keyed:
        return "other_key"
    if any(card["start"] <= epoch <= card["end"] for card in keyed):
        return "current"
    expired = any(card["end"] < epoch for card in keyed)
    future = any(card["start"] > epoch for card in keyed)
    require(expired or future, "unclassified delivered record")
    if expired and future:
        return "expired_and_future"
    return "expired" if expired else "future"


def _item(
    case: dict[str, Any],
    dispatch: dict[str, Any],
    agenda: list[dict[str, Any]],
    item: str,
    choice: str | None,
) -> dict[str, Any]:
    epoch = dispatch["order"]["epoch"]
    crate = _current(case["registrar"], item, epoch)
    bay = _current(case["depot"], crate["value"], epoch)
    registrar_file = dispatch["registrar_file"]
    depot_file = dispatch["depot_file"]
    registrar = _layer(registrar_file, item, epoch, case["quota"])
    depot = _layer(depot_file, crate["value"], epoch, case["quota"])
    if registrar == "current":
        require(
            _current(registrar_file, item, epoch)["id"] == crate["id"],
            "delivered registrar link differs from native record",
        )
    if depot == "current":
        require(
            _current(depot_file, crate["value"], epoch)["id"] == bay["id"],
            "delivered depot link differs from native record",
        )
    chain = registrar == depot == "current"
    expired = {
        card["value"]
        for card in depot_file
        if card["key"] == crate["value"] and card["end"] < epoch
    }
    stale = choice is not None and registrar == "current" and bool(expired)
    old_crates = {
        card["value"]
        for card in registrar_file
        if card["key"] == item and card["end"] < epoch
    }
    old_registrar_bays = {
        card["value"]
        for card in depot_file
        if card["key"] in old_crates and card["start"] <= epoch <= card["end"]
    }
    return {
        "registrar": registrar,
        "depot": depot,
        "on_depot_agenda": any(
            entry["key"] == crate["value"] and entry["epoch"] == epoch
            for entry in agenda
        ),
        "current_chain": chain,
        "choice_matches_expired_depot_chain": choice in expired if stale else None,
        "choice_matches_expired_registrar_current_depot": (
            choice in old_registrar_bays
            if choice is not None and old_registrar_bays
            else None
        ),
        "choice_matches_current_chain": (
            choice == bay["value"] if choice is not None and chain else None
        ),
        "physically_correct": None if choice is None else choice == bay["value"],
    }


def _check_grade(result: dict[str, Any], items: list[dict[str, Any]]) -> None:
    if result["status"] != "complete":
        require(result["correct_items"] is None, "incomplete grade has outcomes")
        return
    require(
        result["registrar_supported"]
        == sum(row["registrar"] == "current" for row in items)
        and result["depot_supported"] == sum(row["depot"] == "current" for row in items)
        and result["chains_supported"] == sum(row["current_chain"] for row in items)
        and result["correct_items"] == sum(row["physically_correct"] for row in items),
        "stage labels differ from native grade",
    )


def _versions(
    cut: dict[str, Any],
    result: dict[str, Any],
    views: dict[str, dict[str, Any]],
    count: int,
) -> dict[str, Any]:
    hidden = {
        "schema": SCHEMA,
        "accepted_actions": count,
        "order_revealed": False,
        "committed": None,
        "registrar": None,
        "depot": None,
        "on_depot_agenda": None,
        "items": None,
        "caveat": CAVEAT,
    }
    if count < 3:
        return hidden
    case = cut["case"]
    agenda = views["depot"]["agenda"]
    require(
        type(agenda) is list and len(agenda) <= ITEMS * case["nominate"],
        "unbounded depot agenda",
    )
    dispatch = views["dispatch"]
    order = dispatch["order"]
    require(
        order is not None
        and len(order["items"]) == ITEMS
        and len(set(order["items"])) == ITEMS,
        "dispatch order is not revealed",
    )
    routes = cut["events"][3]["action"]["routes"] if count == len(ROLES) else None
    items = [
        _item(case, dispatch, agenda, item, None if routes is None else routes[item])
        for item in order["items"]
    ]
    _check_grade(result, items)
    counts = {
        layer: {name: sum(row[layer] == name for row in items) for name in CLASSES}
        for layer in ("registrar", "depot")
    }
    require(
        all(sum(layer.values()) == ITEMS for layer in counts.values()),
        "layer counts differ from order size",
    )
    return hidden | {
        "order_revealed": True,
        "committed": routes is not None,
        **counts,
        "on_depot_agenda": sum(row["on_depot_agenda"] for row in items),
        "items": items,
    }


def summarize_versions(cut: dict[str, Any]) -> dict[str, Any]:
    return _versions(cut, *_replay(cut))


def summarize_handoffs(cut: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result, views, count = _replay(cut)
    return _versions(cut, result, views, count), local_coverage(cut["events"], views)
