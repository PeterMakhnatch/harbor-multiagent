import json
from importlib import import_module
from itertools import combinations
from typing import Any

_TASK = "examples.tasks.exhibit-relay"
make_case = import_module(f"{_TASK}.environment.exhibit_relay.cases").make_case
Session = import_module(f"{_TASK}.environment.exhibit_relay.engine").Session
act = import_module(f"{_TASK}.solution.policies").act


def _canonical(view: dict[str, Any]) -> bytes:
    return json.dumps(view, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _early(
    case: dict[str, Any], mode: str
) -> tuple[dict[str, Any], frozenset[str], frozenset[str]]:
    session = Session(case, mode)
    request = act(session.view("loader"), "portfolio")
    session.apply("loader", request)
    filing = act(session.view("registrar"), "portfolio")
    session.apply("registrar", filing)
    assert request["slips"] == sorted(request["slips"]), "noncanonical nominations"
    assert filing["ids"] == sorted(filing["ids"]), "noncanonical registrar file"
    return session.view("depot"), frozenset(request["slips"]), frozenset(filing["ids"])


def _at(cards: list[dict[str, Any]], key: str, epoch: int) -> dict[str, Any]:
    matches = [
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    assert len(matches) == 1, "nonunique epoch mapping"
    return matches[0]


def _requirements(
    case: dict[str, Any], nominated: frozenset[str], filed: frozenset[str]
) -> tuple[
    list[tuple[frozenset[str], int]], list[dict[str, Any]], list[dict[str, Any]]
]:
    briefs = case["briefs"]
    assert nominated == {brief["id"] for brief in briefs}, "incomplete nominations"
    requirements = []
    bundles = []
    marginals: dict[tuple[str, int], int] = {}
    required_registrar = set()
    for brief in briefs:
        epoch, weight = brief["epoch"], brief["weight"]
        sources = [_at(case["registrar"], item, epoch) for item in brief["items"]]
        required_registrar.update(card["id"] for card in sources)
        keys = sorted(card["value"] for card in sources)
        needed = frozenset(_at(case["depot"], key, epoch)["id"] for key in keys)
        assert len(needed) == 2, "duplicate required depot card"
        requirements.append((needed, weight))
        bundles.append({"epoch": epoch, "keys": keys, "weight": weight})
        for key in keys:
            intent = (key, epoch)
            marginals[intent] = marginals.get(intent, 0) + weight
    assert len(required_registrar) == 4 and required_registrar == filed, (
        "incomplete registrar coverage"
    )
    assert len(marginals) == 4 and set(marginals.values()) == {5}, (
        "incorrect marginal weights"
    )
    agenda = [
        {"key": key, "epoch": epoch, "weight": weight}
        for (key, epoch), weight in sorted(marginals.items())
    ]
    bundles.sort(key=lambda bundle: (bundle["epoch"], bundle["keys"]))
    return requirements, agenda, bundles


def _weight(
    requirements: list[tuple[frozenset[str], int]], selected: frozenset[str]
) -> int:
    return sum(weight for needed, weight in requirements if needed <= selected)


def certify(seed: int) -> dict[str, Any]:
    if not __debug__:
        raise ValueError("the design certificate requires enabled invariant checks")
    cases = [make_case(seed, "coupling", layout=layout) for layout in ("a", "b")]
    flat = [_early(case, "linked") for case in cases]
    grouped = [_early(case, "bundled") for case in cases]
    masked = [_early(case, "masked") for case in cases]
    assert all(prefix[1:] == flat[0][1:] for prefix in flat + masked + grouped), (
        "different upstream selections"
    )
    same_flat = _canonical(flat[0][0]) == _canonical(flat[1][0])
    assert same_flat, "flat depot views differ"
    projections = [
        {key: value for key, value in prefix[0].items() if key != "bundles"}
        for prefix in flat + grouped
    ]
    only_bundles = all(
        _canonical(view) == _canonical(projections[0]) for view in projections
    )
    only_bundles = only_bundles and grouped[0][0]["bundles"] != grouped[1][0]["bundles"]
    assert only_bundles, "grouped view differs beyond bundles or lacks distinction"
    assert _canonical(masked[0][0]) == _canonical(masked[1][0]), (
        "masked views identify the heavier pair"
    )
    for hidden, visible in zip(masked, grouped, strict=True):
        withheld = [{**row, "weight": 0} for row in visible[0]["bundles"]]
        assert hidden[0] == {**visible[0], "bundles": withheld}, (
            "mask changes more than weights"
        )
        assert len(_canonical(hidden[0])) == len(_canonical(visible[0])), (
            "different view byte budgets"
        )
    requirements = []
    for case, linked, bundled in zip(cases, flat, grouped, strict=True):
        needed, agenda, bundles = _requirements(case, linked[1], linked[2])
        assert linked[0]["bundles"] == [], "flat view exposes bundles"
        assert linked[0]["agenda"] == bundled[0]["agenda"] == agenda, (
            "incorrect marginals"
        )
        assert bundled[0]["bundles"] == bundles, "incorrect bundle projection"
        requirements.append(needed)
    total = sum(weight for needed in requirements for _, weight in needed)
    assert total == 20, "incorrect total forecast weight"
    view = flat[0][0]
    cards = sorted(card["id"] for card in view["cards"])
    assert (
        view["quota"] == 2 and 8 <= len(cards) <= 24 and len(set(cards)) == len(cards)
    ), "incorrect depot archive or cap"
    legal = [frozenset(ids) for size in range(3) for ids in combinations(cards, size)]
    common = max(
        sum(_weight(needed, selected) for needed in requirements) for selected in legal
    )
    assert common == 5, "incorrect common decision bound"
    grouped_weight = 0
    for prefix, needed in zip(grouped, requirements, strict=True):
        action = act(prefix[0], "portfolio")
        assert action["op"] == "file" and len(action["ids"]) == len(
            set(action["ids"])
        ), "invalid grouped role action"
        selected = frozenset(action["ids"])
        assert selected in legal, "illegal grouped file"
        achieved = _weight(needed, selected)
        assert (
            achieved == max(_weight(needed, candidate) for candidate in legal) == 4
        ), "grouped policy misses optimum"
        grouped_weight += achieved
    assert grouped_weight == 8, "incorrect grouped policy weight"
    return {
        "dev_seed": seed,
        "flat_common_weight": common,
        "grouped_role_weight": grouped_weight,
        "same_masked_view": True,
        "masked_and_grouped_equal_bytes": True,
        "total_weight": total,
        "legal_files": len(legal),
        "same_flat_view": same_flat,
        "same_marginals": True,
        "group_is_only_difference": only_bundles,
        "conclusion": "Preserve truthful bundles to increase attainable forecast coverage from 5/20 to 8/20 under fixed upstream selections. This is not a realized reward or a bound on arbitrary teams.",
    }
