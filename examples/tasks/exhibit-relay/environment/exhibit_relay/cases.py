import hashlib
import random
from itertools import combinations, pairwise
from typing import Any, TypeGuard, cast

PROFILES = {"fork": (8, 3, 2, 3), "mesh": (10, 4, 3, 6), "coupling": (8, 2, 4, 4)}


def capacity(case: dict[str, Any], role: str, mode: str) -> int:
    if mode == "open":
        return 40
    if role == "registrar" and case["profile"] == "coupling":
        return 4
    return case["quota"]


def valid_mode(case: dict[str, Any], mode: str) -> bool:
    return mode in ("linked", "independent", "open", "silent") or (
        mode in ("masked", "bundled") and case["profile"] == "coupling"
    )


_CASE_FIELDS = frozenset(
    {
        "version",
        "profile",
        "quota",
        "nominate",
        "exhibits",
        "crates",
        "bays",
        "briefs",
        "realized",
        "registrar",
        "depot",
    }
)
_BRIEF_FIELDS = frozenset({"id", "epoch", "items", "weight"})
_CARD_FIELDS = frozenset({"id", "key", "value", "start", "end"})


def _rng(seed: int, profile: str, domain: str) -> random.Random:
    digest = hashlib.sha256(
        f"exhibit-relay/v1/{profile}/{seed}/{domain}".encode()
    ).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _labels(rng: random.Random, count: int, prefix: str) -> list[str]:
    return [f"{prefix}-{n}" for n in rng.sample(range(100, 1000), count)]


def _history(
    seed: int, profile: str, role: str, keys: list[str], values: list[str]
) -> list[dict[str, Any]]:
    permutations = []
    for epoch in range(3):
        permutation = list(values)
        _rng(seed, profile, f"{role}/mapping/{epoch}").shuffle(permutation)
        permutations.append(permutation)
    prefix = "R" if role == "registrar" else "D"
    ids = iter(_labels(_rng(seed, profile, f"{role}/ids"), 3 * len(keys), prefix))
    cards = []
    for index, key in enumerate(keys):
        for epoch in range(3):
            value = permutations[epoch][index]
            if epoch and permutations[epoch - 1][index] == value:
                cards[-1]["end"] = epoch
            else:
                cards.append(
                    {
                        "id": next(ids),
                        "key": key,
                        "value": value,
                        "start": epoch,
                        "end": epoch,
                    }
                )
    _rng(seed, profile, f"{role}/order").shuffle(cards)
    return cards


def _briefs(
    seed: int, profile: str, exhibits: list[str], layout: str
) -> list[dict[str, Any]]:
    structure = _rng(seed, profile, "brief/structure")
    weights = _rng(seed, profile, "brief/weights")
    items = list(exhibits)
    structure.shuffle(items)
    epochs = list(range(3))
    structure.shuffle(epochs)
    if profile == "fork":
        specs = [(epochs[0], 0, 1), (epochs[0], 0, 2), (epochs[1], 3, 4)]
        brief_weights = [(3, 3, 4), (2, 2, 6)][weights.randrange(2)]
    elif profile == "coupling":
        specs = [(epochs[0], a, b) for a, b in ((0, 1), (2, 3), (0, 2), (1, 3))]
        if layout == "draw":
            layout = weights.choice(("a", "b"))
        brief_weights = (4, 4, 1, 1) if layout == "a" else (1, 1, 4, 4)
    else:
        specs = [
            (epochs[0], 0, 1),
            (epochs[0], 1, 2),
            (epochs[1], 0, 3),
            (epochs[1], 3, 4),
            (epochs[2], 2, 5),
            (epochs[2], 5, 6),
        ]
        brief_weights = weights.sample(range(1, 13), len(specs))
    ids = _labels(_rng(seed, profile, "brief/ids"), len(specs), "T")
    briefs = []
    for brief_id, (epoch, left, right), weight in zip(
        ids, specs, brief_weights, strict=True
    ):
        pair = [items[left], items[right]]
        structure.shuffle(pair)
        briefs.append({"id": brief_id, "epoch": epoch, "items": pair, "weight": weight})
    _rng(seed, profile, "brief/order").shuffle(briefs)
    return briefs


def make_case(
    seed: int, profile: str = "fork", *, layout: str = "draw"
) -> dict[str, Any]:
    if (
        type(seed) is not int
        or not 0 <= seed < 2**63
        or type(profile) is not str
        or profile not in PROFILES
    ):
        raise ValueError("invalid seed or profile")
    if layout not in ("draw", "a", "b") or (layout != "draw" and profile != "coupling"):
        raise ValueError("invalid layout for profile")
    count, quota, nominate, _ = PROFILES[profile]
    exhibits, crates, bays = [
        _labels(_rng(seed, profile, domain), count, prefix)
        for domain, prefix in (("exhibits", "E"), ("crates", "C"), ("bays", "B"))
    ]
    briefs = _briefs(seed, profile, exhibits, layout)
    ticket = _rng(seed, profile, "realized/lottery").randrange(
        sum(brief["weight"] for brief in briefs)
    )
    realized = None
    for brief in briefs:
        ticket -= brief["weight"]
        if ticket < 0:
            realized = brief["id"]
            break
    assert realized is not None
    case = {
        "version": 1,
        "profile": profile,
        "quota": quota,
        "nominate": nominate,
        "exhibits": exhibits,
        "crates": crates,
        "bays": bays,
        "briefs": briefs,
        "realized": realized,
        "registrar": _history(seed, profile, "registrar", exhibits, crates),
        "depot": _history(seed, profile, "depot", crates, bays),
    }
    validate_case(case)
    return case


def _schema(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or len(value) != len(fields) or value.keys() != fields:
        raise ValueError("invalid record fields")
    return cast(dict[str, Any], value)


def _identity(value: object) -> TypeGuard[str]:
    return (
        type(value) is str
        and 1 <= len(value) <= 32
        and value.isascii()
        and all(ch.isalnum() or ch in "-_" for ch in value)
    )


def _labels_valid(values: object, count: int) -> set[str]:
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("invalid labels")
    labels: set[str] = set()
    for value in values:
        if not _identity(value):
            raise ValueError("invalid label")
        labels.add(value)
    if len(labels) != count:
        raise ValueError("duplicate labels")
    return labels


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _brief_valid(brief: dict[str, Any], exhibits: set[str]) -> None:
    _schema(brief, _BRIEF_FIELDS)
    if not _identity(brief["id"]) or not _integer(brief["epoch"], 0, 2):
        raise ValueError("invalid brief identity or epoch")
    items = _labels_valid(brief["items"], 2)
    if (
        not items <= exhibits
        or type(brief["weight"]) is not int
        or brief["weight"] <= 0
    ):
        raise ValueError("invalid brief items or weight")


def _fork_valid(briefs: list[dict[str, Any]]) -> None:
    for first, second in combinations(briefs, 2):
        if first["epoch"] != second["epoch"]:
            continue
        left, right = set(first["items"]), set(second["items"])
        third = next(
            brief for brief in briefs if brief is not first and brief is not second
        )
        if (
            len(left & right) == 1
            and third["epoch"] != first["epoch"]
            and not (left | right) & set(third["items"])
            and (first["weight"], second["weight"], third["weight"])
            in ((3, 3, 4), (2, 2, 6))
        ):
            return
    raise ValueError("invalid fork structure")


def _coupling_valid(briefs: list[dict[str, Any]]) -> None:
    if len({brief["epoch"] for brief in briefs}) != 1:
        raise ValueError("pairings must have one dispatch epoch")
    matchings = []
    for weight in (1, 4):
        pairs = [set(b["items"]) for b in briefs if b["weight"] == weight]
        if len(pairs) != 2 or pairs[0] & pairs[1]:
            raise ValueError("each weight class must be a matching")
        matchings.append(pairs[0] | pairs[1])
    if matchings[0] != matchings[1]:
        raise ValueError("pairings must use the same four exhibits")


def _mesh_valid(briefs: list[dict[str, Any]]) -> None:
    same_epoch = False
    cross_epoch = False
    for first, second in combinations(briefs, 2):
        if set(first["items"]) & set(second["items"]):
            if first["epoch"] == second["epoch"]:
                same_epoch = True
            else:
                cross_epoch = True
    if (
        not same_epoch
        or not cross_epoch
        or len({brief["weight"] for brief in briefs}) < 2
    ):
        raise ValueError("invalid mesh structure")


def _history_valid(cards: object, keys: set[str], values: set[str]) -> set[str]:
    if type(cards) is not list or not len(keys) <= len(cards) <= 3 * len(keys):
        raise ValueError("invalid history size")
    epochs = [{}, {}, {}]
    ids = set()
    for raw in cards:
        card = _schema(raw, _CARD_FIELDS)
        identity, key, value, start, end = (
            card[field] for field in ("id", "key", "value", "start", "end")
        )
        if not all(_identity(label) for label in (identity, key, value)):
            raise ValueError("invalid card labels")
        if identity in ids or key not in keys or value not in values:
            raise ValueError("invalid card identity or domain")
        if not _integer(start, 0, 2) or not _integer(end, start, 2):
            raise ValueError("invalid card interval")
        ids.add(identity)
        for epoch in range(start, end + 1):
            if key in epochs[epoch]:
                raise ValueError("overlapping card intervals")
            epochs[epoch][key] = (value, identity)
    for epoch in epochs:
        if epoch.keys() != keys or {value for value, _ in epoch.values()} != values:
            raise ValueError("non-bijective epoch")
    for previous, current in pairwise(epochs):
        for key in keys:
            if (
                previous[key][0] == current[key][0]
                and previous[key][1] != current[key][1]
            ):
                raise ValueError("uncoalesced card intervals")
    return ids


def validate_case(case: dict[str, Any]) -> None:
    _schema(case, _CASE_FIELDS)
    profile = case["profile"]
    if (
        not _integer(case["version"], 1, 1)
        or type(profile) is not str
        or profile not in PROFILES
    ):
        raise ValueError("invalid version or profile")
    count, quota, nominate, brief_count = PROFILES[profile]
    if not _integer(case["quota"], quota, quota) or not _integer(
        case["nominate"], nominate, nominate
    ):
        raise ValueError("invalid profile caps")
    exhibits, crates, bays = (
        _labels_valid(case[domain], count) for domain in ("exhibits", "crates", "bays")
    )
    briefs = case["briefs"]
    if type(briefs) is not list or len(briefs) != brief_count:
        raise ValueError("invalid brief count")
    for brief in briefs:
        _brief_valid(brief, exhibits)
    brief_ids = {brief["id"] for brief in briefs}
    signatures = {(brief["epoch"], frozenset(brief["items"])) for brief in briefs}
    if len(brief_ids) != brief_count or len(signatures) != brief_count:
        raise ValueError("duplicate briefs")
    if not _identity(case["realized"]) or case["realized"] not in brief_ids:
        raise ValueError("invalid realized brief")
    if profile == "fork":
        _fork_valid(briefs)
    elif profile == "coupling":
        _coupling_valid(briefs)
    else:
        _mesh_valid(briefs)
    registrar = _history_valid(case["registrar"], exhibits, crates)
    depot = _history_valid(case["depot"], crates, bays)
    domains = (exhibits, crates, bays, brief_ids, registrar, depot)
    if len(set.union(*domains)) != sum(map(len, domains)):
        raise ValueError("colliding label domains")
