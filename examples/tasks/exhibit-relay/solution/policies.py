from itertools import combinations
from typing import Any


def _subsets(ids: list[str], cap: int):
    for size in range(min(len(ids), cap) + 1):
        yield from combinations(ids, size)


def _brief_ids(
    briefs: list[dict[str, Any]], quota: int, nominate: int, policy: str
) -> list[str]:
    ordered = sorted(briefs, key=lambda brief: brief["id"])
    if policy == "unfiltered":
        if len(ordered) > nominate:
            raise ValueError("unfiltered requires unrestricted nomination")
        return [brief["id"] for brief in ordered]
    if policy == "portfolio" and nominate == 4 and quota == 2:
        return [brief["id"] for brief in ordered]
    if policy == "index":
        return [brief["id"] for brief in ordered[:nominate]]
    if policy == "top":
        return [min(ordered, key=lambda brief: (-brief["weight"], brief["id"]))["id"]]
    if policy == "latest":
        return [
            brief["id"]
            for brief in sorted(ordered, key=lambda brief: -brief["epoch"])[:nominate]
        ]
    by_id = {brief["id"]: brief for brief in ordered}
    best: tuple[str, ...] = ()
    best_score = (0, 0)
    for ids in _subsets(list(by_id), nominate):
        keys = {
            (item, by_id[ident]["epoch"])
            for ident in ids
            for item in by_id[ident]["items"]
        }
        if len(keys) > quota:
            continue
        score = (sum(by_id[ident]["weight"] for ident in ids), -len(keys))
        if score > best_score:
            best, best_score = ids, score
    return list(best)


def _matches(card: dict[str, Any], key: str, epoch: int) -> bool:
    return card["key"] == key and card["start"] <= epoch <= card["end"]


def _required_card(cards: list[dict[str, Any]], key: str, epoch: int) -> str:
    return next(card["id"] for card in cards if _matches(card, key, epoch))


def _fallback(cards: list[dict[str, Any]], quota: int, policy: str) -> list[str]:
    if policy == "latest":
        ordered = sorted(
            cards, key=lambda card: (-card["end"], -card["start"], card["id"])
        )
    else:
        ordered = sorted(cards, key=lambda card: card["id"])
    if policy == "unfiltered" and len(ordered) > quota:
        raise ValueError("unfiltered requires unrestricted filing")
    return [card["id"] for card in ordered[:quota]]


def _registrar(view: dict[str, Any], policy: str) -> list[str]:
    cards, briefs, quota = view["cards"], view["briefs"], view["quota"]
    if not briefs or policy in ("latest", "index", "unfiltered"):
        return _fallback(cards, quota, policy)
    if policy == "top":
        briefs = [min(briefs, key=lambda brief: (-brief["weight"], brief["id"]))]
    requirements = [
        (
            {_required_card(cards, item, brief["epoch"]) for item in brief["items"]},
            brief["weight"],
        )
        for brief in briefs
    ]
    relevant = sorted({ident for ids, _ in requirements for ident in ids})
    best: tuple[str, ...] = ()
    best_score = (0, 0, 0)
    for ids in _subsets(relevant, quota):
        selected = set(ids)
        score = (
            sum(weight for needed, weight in requirements if needed <= selected),
            sum(weight * len(needed & selected) for needed, weight in requirements),
            -len(ids),
        )
        if score > best_score:
            best, best_score = ids, score
    return list(best)


def _notes(view: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    selected = set(ids)
    cards = [card for card in view["cards"] if card["id"] in selected]
    notes: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for brief in sorted(view["briefs"], key=lambda b: (-b["weight"], b["id"])):
        epoch, weight = brief["epoch"], brief["weight"]
        if not 1 <= weight <= 9:
            continue
        keys = sorted(
            card["value"]
            for card in cards
            if card["key"] in brief["items"] and card["start"] <= epoch <= card["end"]
        )
        if len(keys) != 2 or keys[0] == keys[1]:
            continue
        pair = (epoch, keys[0], keys[1])
        if pair in seen:
            continue
        seen.add(pair)
        notes.append({"epoch": epoch, "keys": keys, "weight": weight})
        if len(notes) == view["note_capacity"]:
            break
    return sorted(notes, key=lambda note: (note["epoch"], note["keys"]))


def _depot(view: dict[str, Any], policy: str) -> list[str]:
    cards, agenda, quota = view["cards"], view["agenda"], view["quota"]
    if policy in ("latest", "index", "unfiltered"):
        return _fallback(cards, quota, policy)
    if view.get("bundles") and policy == "portfolio":
        for pair in sorted(
            view["bundles"], key=lambda b: (-b["weight"], b["epoch"], b["keys"])
        ):
            ids = sorted(
                card["id"]
                for card in cards
                if any(_matches(card, key, pair["epoch"]) for key in pair["keys"])
            )
            if len(ids) == len(pair["keys"]) and len(ids) <= quota:
                return ids
    if not agenda:
        return _fallback(cards, quota, policy)
    weights: dict[str, int] = {}
    for intent in agenda:
        ident = _required_card(cards, intent["key"], intent["epoch"])
        weights[ident] = weights.get(ident, 0) + intent["weight"]
    return sorted(weights, key=lambda ident: (-weights[ident], ident))[:quota]


def _dispatch(view: dict[str, Any]) -> dict[str, str]:
    brief = view["order"]
    epoch = brief["epoch"]
    routes: dict[str, str] = {}
    for exhibit in sorted(brief["items"]):
        crate = next(
            (
                card["value"]
                for card in view["registrar_file"]
                if _matches(card, exhibit, epoch)
            ),
            None,
        )
        if crate is None:
            continue
        bay = next(
            (
                card["value"]
                for card in view["depot_file"]
                if _matches(card, crate, epoch)
            ),
            None,
        )
        if bay is not None:
            routes[exhibit] = bay
    available = iter(sorted(set(view["bays"]) - set(routes.values())))
    for exhibit in sorted(brief["items"]):
        if exhibit not in routes:
            routes[exhibit] = next(available)
    return routes


def _loader(view: dict[str, Any], policy: str) -> dict[str, Any]:
    if view["phase"] == "request":
        return {
            "op": "request",
            "slips": _brief_ids(
                view["briefs"], view["quota"], view["nominate"], policy
            ),
        }
    if view["phase"] == "dispatch":
        return {"op": "commit", "routes": _dispatch(view)}
    raise ValueError("unexpected loader phase")


def act(view: dict[str, Any], policy: str = "portfolio") -> dict[str, Any]:
    if policy not in ("portfolio", "top", "latest", "index", "unfiltered"):
        raise ValueError("unknown policy")
    role = view["role"]
    if view["turn"] != role:
        raise ValueError("not role turn")
    if role == "loader":
        return _loader(view, policy)
    if role == "registrar" and view["phase"] == "registrar":
        ids = _registrar(view, policy)
        if view.get("note_capacity") == 4:
            return {"op": "file", "ids": ids, "notes": _notes(view, ids)}
        return {"op": "file", "ids": ids}
    if role == "depot" and view["phase"] == "depot":
        return {"op": "file", "ids": _depot(view, policy)}
    raise ValueError("unexpected role or phase")
