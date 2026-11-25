from copy import deepcopy
from typing import Any, cast

MAPS = ("original", "swapped")
CHANNELS = ("neither", "registrar", "depot", "both")
FILES = ("registrar_file", "depot_file")
HIDDEN = {
    "neither": FILES,
    "registrar": ("depot_file",),
    "depot": ("registrar_file",),
    "both": (),
}
CARD_FIELDS = frozenset({"id", "key", "value", "start", "end"})
MAX_CARDS = 24


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _current(cards: object, key: str, epoch: int) -> str:
    if type(cards) is not list or not 8 <= len(cards) <= MAX_CARDS:
        raise ValueError("invalid archive size")
    match: str | None = None
    for raw in cast(list[object], cards):
        _require(type(raw) is dict, "invalid archive record")
        card = cast(dict[str, Any], raw)
        if card["key"] == key and card["start"] <= epoch <= card["end"]:
            if match is not None or type(card["value"]) is not str:
                raise ValueError("expected one record")
            match = card["value"]
    if match is None:
        raise ValueError("expected one record")
    return match


def _targets(case: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    _require(
        type(case) is dict
        and case.get("profile") == "fork"
        and type(case.get("briefs")) is list
        and len(case["briefs"]) == 3,
        "expected a native fork case",
    )
    focal = min(case["briefs"], key=lambda brief: brief["id"])
    epoch = focal["epoch"]
    _require(len(focal["items"]) == 2, "expected two focal exhibits")
    crates = [_current(case["registrar"], item, epoch) for item in focal["items"]]
    bays = [_current(case["depot"], crate, epoch) for crate in crates]
    _require(
        len(set(crates)) == 2 and set(crates) <= set(case["crates"]),
        "expected distinct focal crates",
    )
    _require(
        len(set(bays)) == 2 and set(bays) <= set(case["bays"]),
        "expected distinct focal bays",
    )
    return crates, bays, focal


def _swap(cards: list[dict[str, Any]], pair: list[str]) -> list[dict[str, Any]]:
    left, right = pair
    exchange = {left: right, right: left}
    return [
        {**card, "value": exchange.get(card["value"], card["value"])} for card in cards
    ]


def _same_shape(
    before: list[dict[str, Any]], after: list[dict[str, Any]], pair: list[str]
) -> bool:
    return len(before) == len(after) and all(
        {**old, "value": None} == {**new, "value": None}
        and (old["value"] == new["value"]) == (old["value"] not in pair)
        for old, new in zip(before, after, strict=True)
    )


def select(case: dict[str, Any], registry_map: str, bay_map: str) -> dict[str, Any]:
    _require(registry_map in MAPS, "invalid registry map")
    _require(bay_map in MAPS, "invalid bay map")
    crates, bays, focal = _targets(case)
    selected = deepcopy(case)
    registry = registry_map == "swapped"
    bay = bay_map == "swapped"
    if registry:
        selected["registrar"] = _swap(selected["registrar"], crates)
    if bay:
        selected["depot"] = _swap(selected["depot"], bays)
    _require(
        {k: v for k, v in selected.items() if k not in ("registrar", "depot")}
        == {k: v for k, v in case.items() if k not in ("registrar", "depot")},
        "world map changed a public case field",
    )
    _require(
        selected["registrar"] == case["registrar"]
        if not registry
        else _same_shape(case["registrar"], selected["registrar"], crates),
        "registry map changed more than two crate values",
    )
    _require(
        selected["depot"] == case["depot"]
        if not bay
        else _same_shape(case["depot"], selected["depot"], bays),
        "bay map changed more than two bay values",
    )
    epoch = focal["epoch"]
    for index, item in enumerate(focal["items"]):
        crate = _current(selected["registrar"], item, epoch)
        _require(crate == crates[index ^ registry], "registry map did not apply")
        destination = _current(selected["depot"], crate, epoch)
        _require(destination == bays[index ^ registry ^ bay], "joint map did not apply")
    return selected


def _mask(card: object) -> dict[str, Any]:
    _require(type(card) is dict, "invalid filed record")
    record = cast(dict[str, Any], card)
    _require(record.keys() == CARD_FIELDS, "invalid filed record")
    value = record["value"]
    _require(type(value) is str and value != "", "invalid filed value")
    text = cast(str, value)
    masked: dict[str, Any] = {**record, "value": "?" * len(text.encode("utf-8"))}
    _require(
        len(masked["value"].encode("utf-8")) == len(text.encode("utf-8")),
        "mask changed value length",
    )
    return masked


def delivery(view: dict[str, Any], channels: str) -> dict[str, Any]:
    _require(channels in CHANNELS, "invalid delivery channels")
    _require(type(view) is dict, "invalid view")
    files = [view.get(field) for field in FILES]
    _require(
        all(type(cards) is list and len(cards) <= MAX_CARDS for cards in files),
        "invalid filed records",
    )
    if any(files):
        _require(
            view.get("role") == "loader" and view.get("phase") in ("dispatch", "done"),
            "filed records outside loader dispatch",
        )
    delivered = deepcopy(view)
    for field in HIDDEN[channels]:
        delivered[field] = [_mask(card) for card in delivered[field]]
    return delivered
