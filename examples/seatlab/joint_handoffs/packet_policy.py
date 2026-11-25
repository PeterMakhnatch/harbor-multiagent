from fractions import Fraction
from itertools import permutations, product
from typing import Any

from ...native_study import require
from ..decision_boundary.core import World
from . import world

SCHEMA = "seatlab.joint-handoffs.packet-policy/v1"
Map = tuple[str, str]
_WORLDS = frozenset(
    (registry, bay) for registry, bay in product(world.MAPS, world.MAPS)
)
_BAYS = 8
_LABELS = frozenset(
    f"{left}:{right}" for left, right in product(range(_BAYS), repeat=2)
)
RULES = {
    "chains": "both visible current record chains give each requested exhibit its bay",
    "destinations": "the 2 distinct visible current depot bays go to the 2 requested exhibits in either order with equal chance",
    "public": "each ordered pair of 2 distinct public bays has equal chance",
}
EXECUTION_ASSUMPTION = "The rule assumes that the 2 depot filings are the current records of the requested order's 2 crates and that those 2 bays differ. The depot-only rule cannot check the first part from its delivered view."
INSTRUCTION_PROMISE = "The loader instructions say that records may be incomplete or absent, that a value of only `?` characters is unknown, and that the public bay list is available. They do not promise that depot filings target the requested order or that the 2 bays differ."
SCOPE = "Exact row-mass-weighted native reward of 1 explicit receiver rule that reads only the delivered loader view. It is 1 legal rule among many and is not a model result."


def _withheld(value: object) -> bool:
    return type(value) is str and value != "" and set(value) == {"?"}


def _values(cards: list[dict[str, Any]], domain: list[str]) -> list[str] | None:
    hidden = [_withheld(card["value"]) for card in cards]
    require(len(set(hidden)) == 1, "a filing mixes withheld and delivered values")
    if hidden[0]:
        return None
    values = [card["value"] for card in cards]
    require(all(value in domain for value in values), "a delivered value is not public")
    return values


def _shape(view: dict[str, Any]) -> tuple[list[str], list[str]]:
    require(type(view) is dict, "invalid delivered view")
    require(
        view.get("role") == "loader" and view.get("phase") == "dispatch",
        "expected the loader dispatch view",
    )
    order, bays, crates = view.get("order"), view.get("bays"), view.get("crates")
    if not isinstance(order, dict) or not order:
        raise ValueError("expected a requested order")
    items, epoch = order.get("items"), order.get("epoch")
    require(
        type(items) is list
        and len(items) == 2
        and all(type(item) is str for item in items)
        and len(set(items)) == 2
        and type(epoch) is int,
        "expected 2 distinct requested exhibits at one epoch",
    )
    if type(bays) is not list or type(crates) is not list:
        raise ValueError("expected public bay and crate lists")
    require(
        len(bays) == _BAYS == len(set(bays)) and all(type(bay) is str for bay in bays),
        "expected 8 distinct public bays",
    )
    require(
        all(type(crate) is str for crate in crates) and len(crates) == len(set(crates)),
        "expected distinct public crates",
    )
    for field in world.FILES:
        cards = view.get(field)
        if type(cards) is not list or len(cards) != 2:
            raise ValueError("expected 2 filed records each")
        require(
            all(
                type(card) is dict
                and card.keys() == world.CARD_FIELDS
                and type(card["start"]) is int
                and type(card["end"]) is int
                and card["start"] <= epoch <= card["end"]
                for card in cards
            ),
            "expected current filed records",
        )
    registrar, depot = view["registrar_file"], view["depot_file"]
    require(
        sorted(card["key"] for card in registrar) == sorted(items),
        "registrar records do not match the requested exhibits",
    )
    keys = [card["key"] for card in depot]
    require(len(set(keys)) == 2 and set(keys) <= set(crates), "invalid depot keys")
    return sorted(items), sorted(bays)


def candidates(view: dict[str, Any]) -> tuple[str, list[str]]:
    items, bays = _shape(view)
    registrar, depot = view["registrar_file"], view["depot_file"]
    destinations = _values(depot, bays)
    crates = _values(registrar, view["crates"])
    if destinations is None:
        routes = list(permutations(bays, 2))
        rule = "public"
    else:
        require(len(set(destinations)) == 2, "visible depot bays collide")
        if crates is None:
            routes = list(permutations(destinations, 2))
            rule = "destinations"
        else:
            located = {card["key"]: card["value"] for card in depot}
            chosen = {card["key"]: card["value"] for card in registrar}
            require(
                set(chosen.values()) == set(located),
                "visible record chains are incomplete",
            )
            routes = [tuple(located[chosen[item]] for item in items)]
            rule = "chains"
    index = {bay: offset for offset, bay in enumerate(bays)}
    labels = [f"{index[first]}:{index[second]}" for first, second in routes]
    require(1 <= len(labels) == len(set(labels)), "duplicate candidate actions")
    return rule, labels


def _rows(finals: dict[Map, dict[str, Any]], rows: dict[Map, World]) -> None:
    require(
        type(finals) is dict
        and type(rows) is dict
        and set(finals) == set(rows) == _WORLDS,
        "expected the 4 registry/bay maps",
    )
    for row in rows.values():
        require(type(row) is World and type(row.mass) is int and row.mass >= 1, "row")
        require(row.scores.keys() == _LABELS, "expected the native 64-way score table")
        require(
            all(
                type(score) is int and score in (0, 1) for score in row.scores.values()
            ),
            "expected native 0/1 rewards",
        )


def policy_values(
    finals: dict[Map, dict[str, Any]], rows: dict[Map, World]
) -> dict[str, Any]:
    _rows(finals, rows)
    total = sum(row.mass for row in rows.values())
    arms = {}
    for channel in world.CHANNELS:
        value, counts, used = Fraction(0), 0, set()
        for key in sorted(_WORLDS):
            rule, labels = candidates(world.delivery(finals[key], channel))
            scores = rows[key].scores
            share = Fraction(sum(scores[label] for label in labels), len(labels))
            value += share * Fraction(rows[key].mass, total)
            counts += len(labels)
            used.add(rule)
        require(0 <= value <= 1, "invalid policy value")
        arms[channel] = {
            "rules": sorted(used),
            "candidate_actions": counts,
            "value": str(value),
        }
    return {
        "schema": SCHEMA,
        "rules": dict(RULES),
        "execution_assumption": EXECUTION_ASSUMPTION,
        "instruction_promise": INSTRUCTION_PROMISE,
        "scope": SCOPE,
        "model_calls": 0,
        "random_samples": 0,
        "worlds": len(_WORLDS),
        "arms": arms,
    }
