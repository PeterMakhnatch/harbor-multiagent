from typing import Any, cast

SCHEMA = "seatlab.physical-dispatch.route-evidence/v1"
DOMAIN_MIN = 2
DOMAIN_MAX = 10
EPOCHS = (0, 1, 2)
RECORDS_PER_KEY = len(EPOCHS)
CHECKS_MAX = 10_000
CARD_FIELDS = frozenset({"id", "key", "value", "start", "end"})
ACTION_FIELDS = frozenset({"op", "routes"})
CHOICES = ("entailed", "compatible_not_entailed", "contradicted")
SCOPE = (
    "Counts describe only the delivered current facts. They ignore sender selection, "
    "expired and future rows, identifier meaning and probabilities. The stronger model "
    "assumes two bijections; the role instructions do not promise them."
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _record(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("expected a JSON object")
    return cast("dict[str, Any]", value)


def _identity(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 32
        and value.isascii()
        and all(ch.isalnum() or ch in "-_" for ch in value)
    )


def _field(record: dict[str, Any], key: str) -> Any:
    _require(key in record, f"missing {key}")
    return record[key]


def _labels(values: object, name: str) -> tuple[str, ...]:
    _require(
        type(values) is list and DOMAIN_MIN <= len(values) <= DOMAIN_MAX,
        f"{name} list outside bounded domain",
    )
    assert isinstance(values, list)
    _require(all(_identity(value) for value in values), f"invalid {name} label")
    labels = tuple(sorted(values))
    _require(len(set(labels)) == len(labels), f"duplicate {name} label")
    return labels


def check_domains(
    view: object,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    view = _record(view)
    exhibits = _labels(_field(view, "exhibits"), "exhibit")
    crates = _labels(_field(view, "crates"), "crate")
    bays = _labels(_field(view, "bays"), "bay")
    _require(len(exhibits) == len(crates) == len(bays), "domains must have equal size")
    return exhibits, crates, bays


def _guard(view: dict[str, Any]) -> None:
    _require(
        _field(view, "role") == "loader"
        and _field(view, "phase") == "dispatch"
        and _field(view, "turn") == "loader",
        "expected the final loader dispatch view",
    )


def _order(
    view: dict[str, Any], exhibits: tuple[str, ...]
) -> tuple[tuple[str, str], int]:
    order = _field(view, "order")
    _require(type(order) is dict, "missing realized order")
    items = _field(order, "items")
    epoch = _field(order, "epoch")
    _require(
        type(items) is list
        and len(items) == 2
        and all(_identity(item) and item in exhibits for item in items)
        and items[0] != items[1],
        "order items must be two distinct exhibits",
    )
    _require(type(epoch) is int and epoch in EPOCHS, "invalid order epoch")
    first, second = sorted(items)
    return (first, second), epoch


def _card(
    card: object, keys: tuple[str, ...], values: tuple[str, ...], name: str
) -> tuple[str, str, str, int, int]:
    card = _record(card)
    _require(card.keys() == CARD_FIELDS, f"invalid {name} fields")
    identity, key, value, start, end = (
        card[field] for field in ("id", "key", "value", "start", "end")
    )
    _require(_identity(identity), f"invalid {name} card id")
    _require(
        _identity(key) and key in keys and _identity(value) and value in values,
        f"{name} card outside its domain",
    )
    _require(
        type(start) is int
        and type(end) is int
        and EPOCHS[0] <= start <= end <= EPOCHS[-1],
        f"invalid {name} card interval",
    )
    return identity, key, value, start, end


def _current(
    cards: object,
    keys: tuple[str, ...],
    values: tuple[str, ...],
    epoch: int,
    name: str,
) -> dict[str, str]:
    _require(type(cards) is list, f"{name} must be a list")
    assert isinstance(cards, list)
    _require(len(cards) <= RECORDS_PER_KEY * len(keys), f"{name} overflow")
    ids: set[str] = set()
    mapping: dict[str, str] = {}
    for card in cards:
        identity, key, value, start, end = _card(card, keys, values, name)
        _require(identity not in ids, f"duplicate {name} card id")
        ids.add(identity)
        if start <= epoch <= end:
            _require(key not in mapping, f"{name} assigns one key twice")
            _require(value not in mapping.values(), f"{name} assigns one value twice")
            mapping[key] = value
    assert len(mapping) <= len(keys) and len(ids) == len(cards)
    assert len(set(mapping.values())) == len(mapping)
    return mapping


def _options(
    known: dict[str, str], key: str, domain: tuple[str, ...], injective: bool
) -> tuple[str, ...]:
    if key in known:
        return (known[key],)
    if not injective:
        return domain
    used = set(known.values())
    return tuple(value for value in domain if value not in used)


def _shapes(
    items: tuple[str, str],
    registrar: dict[str, str],
    depot: dict[str, str],
    crates: tuple[str, ...],
    injective: bool,
) -> tuple[set[tuple[str | None, str | None, bool]], int]:
    first = _options(registrar, items[0], crates, injective)
    second = _options(registrar, items[1], crates, injective)
    assert first and second
    shapes: set[tuple[str | None, str | None, bool]] = set()
    checks = 0
    for left in first:
        for right in second:
            checks += 1
            if injective and left == right:
                continue
            shapes.add((depot.get(left), depot.get(right), left == right))
    assert checks <= DOMAIN_MAX * DOMAIN_MAX
    return shapes, checks


def _routes(
    items: tuple[str, str],
    registrar: dict[str, str],
    depot: dict[str, str],
    domains: tuple[tuple[str, ...], tuple[str, ...]],
    injective: bool,
) -> tuple[set[tuple[str, str]], int]:
    crates, bays = domains
    shapes, checks = _shapes(items, registrar, depot, crates, injective)
    used = set(depot.values()) if injective else set()
    free = tuple(bay for bay in bays if bay not in used)
    routes: set[tuple[str, str]] = set()
    for left, right, same in shapes:
        assert not (injective and same)
        first = free if left is None else (left,)
        second = free if right is None else (right,)
        for bay in first:
            if same:
                checks += 1
                routes.add((bay, bay))
                continue
            for other in second:
                checks += 1
                if injective and bay == other:
                    continue
                routes.add((bay, other))
    assert routes, "a valid view always admits one route pair"
    assert all(left in bays and right in bays for left, right in routes)
    return routes, checks


def _evidence(view: object) -> dict[str, Any]:
    view = _record(view)
    exhibits, crates, bays = check_domains(view)
    _guard(view)
    items, epoch = _order(view, exhibits)
    registrar = _current(
        _field(view, "registrar_file"), exhibits, crates, epoch, "registrar_file"
    )
    depot = _current(_field(view, "depot_file"), crates, bays, epoch, "depot_file")
    functions, function_checks = _routes(items, registrar, depot, (crates, bays), False)
    bijections, bijection_checks = _routes(
        items, registrar, depot, (crates, bays), True
    )
    assert function_checks + bijection_checks <= CHECKS_MAX
    assert bijections <= functions
    chains = sum(1 for item in items if registrar.get(item) in depot)
    assert 0 <= chains <= 2
    return {
        "items": items,
        "bays": bays,
        "chains": chains,
        "functions": functions,
        "bijections": bijections,
    }


def route_sets(
    view: dict[str, Any],
) -> tuple[tuple[str, str], set[tuple[str, str]], set[tuple[str, str]]]:
    evidence = _evidence(view)
    return evidence["items"], evidence["functions"], evidence["bijections"]


def _accepted(
    action: object, items: tuple[str, str], bays: tuple[str, ...]
) -> tuple[str, str]:
    action = _record(action)
    _require(action.keys() == ACTION_FIELDS, "invalid accepted action")
    routes = _record(action["routes"])
    _require(
        action["op"] == "commit" and len(routes) == 2 and set(routes) == set(items),
        "accepted action must commit both order items",
    )
    _require(
        all(type(bay) is str and bay in bays for bay in routes.values()),
        "accepted action uses an unknown bay",
    )
    return routes[items[0]], routes[items[1]]


def _grade(
    routes: set[tuple[str, str]], choice: tuple[str, str] | None
) -> dict[str, Any]:
    assert routes
    if choice is None:
        verdict = None
    elif choice not in routes:
        verdict = "contradicted"
    elif len(routes) == 1:
        verdict = "entailed"
    else:
        verdict = "compatible_not_entailed"
    assert verdict is None or verdict in CHOICES
    return {"compatible_routes": len(routes), "choice": verdict}


def assess(view: dict[str, Any], action: dict[str, Any] | None) -> dict[str, Any]:
    evidence = _evidence(view)
    bays = evidence["bays"]
    choice = None if action is None else _accepted(action, evidence["items"], bays)
    return {
        "schema": SCHEMA,
        "scope": SCOPE,
        "delivered_chain_items": evidence["chains"],
        "legal_actions": len(bays) * len(bays),
        "card_functions": _grade(evidence["functions"], choice),
        "generator_bijections": _grade(evidence["bijections"], choice),
    }
