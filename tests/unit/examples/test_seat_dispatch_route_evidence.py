import json
import random
from collections.abc import Sequence
from functools import cache
from itertools import permutations, product
from typing import Any

import pytest

from examples.seatlab.physical_dispatch import certificate, policies
from examples.seatlab.physical_dispatch.route_evidence import assess, route_sets

E = ("E-101", "E-102", "E-103")
C = ("C-201", "C-202", "C-203")
B = ("B-301", "B-302", "B-303")
Pair = tuple[str, str]
Record = dict[str, Any]
CardSpec = tuple[int, int] | tuple[int, int, int, int]
MODELS = ("card_functions", "generator_bijections")


def view(
    n: int, epoch: int, reg: Sequence[CardSpec], dep: Sequence[CardSpec], items=(0, 1)
) -> dict[str, Any]:
    def file(
        rows: Sequence[CardSpec],
        prefix: str,
        keys: tuple[str, ...],
        values: tuple[str, ...],
    ) -> list[Record]:
        cards = []
        for k, row in enumerate(rows):
            x, y, start, end = (*row, 0, 2)[:4]
            cards.append(
                {
                    "id": f"{prefix}-{501 + k}",
                    "key": keys[x],
                    "value": values[y],
                    "start": start,
                    "end": end,
                }
            )
        return cards

    order = {"id": "T-401", "epoch": epoch, "items": [E[i] for i in items], "weight": 3}
    return {
        "role": "loader",
        "phase": "dispatch",
        "turn": "loader",
        "quota": 3,
        "nominate": 2,
        "exhibits": list(E[:n]),
        "crates": list(C[:n]),
        "bays": list(B[:n]),
        "briefs": [dict(order)],
        "cards": [],
        "agenda": [],
        "registrar_file": file(reg, "R", E, C),
        "depot_file": file(dep, "D", C, B),
        "order": order,
    }


def active(cards: list[Record], epoch: int) -> dict[str, str]:
    return {c["key"]: c["value"] for c in cards if c["start"] <= epoch <= c["end"]}


def extensions(
    domain: list[str], codomain: list[str], known: dict[str, str], bijective: bool
) -> list[dict[str, str]]:
    rows = (
        permutations(codomain) if bijective else product(codomain, repeat=len(domain))
    )
    maps = [dict(zip(domain, row, strict=True)) for row in rows]
    return [m for m in maps if all(m[k] == v for k, v in known.items())]


def reference(v: Record) -> tuple[Pair, set[Pair], set[Pair], int]:
    first, second = sorted(v["order"]["items"])
    items = (first, second)
    epoch = v["order"]["epoch"]
    rf, df = active(v["registrar_file"], epoch), active(v["depot_file"], epoch)
    sets = []
    for bijective in (False, True):
        gs = extensions(v["crates"], v["bays"], df, bijective)
        sets.append(
            {
                (g[f[first]], g[f[second]])
                for f in extensions(v["exhibits"], v["crates"], rf, bijective)
                for g in gs
            }
        )
    chains = sum(rf.get(i) in df for i in items)
    return items, sets[0], sets[1], chains


def choice(pairs: set[Pair], pair: Pair | None) -> str | None:
    if pair is None:
        return None
    if pairs == {pair}:
        return "entailed"
    return "compatible_not_entailed" if pair in pairs else "contradicted"


def commit(items: Pair, pair: Pair | None) -> Record | None:
    if pair is None:
        return None
    return {"op": "commit", "routes": dict(zip(items, pair, strict=True))}


def check(v: Record) -> tuple[set[Pair], set[Pair]]:
    before = json.dumps(v, sort_keys=True)
    items, functions, bijections, chains = reference(v)
    assert route_sets(v) == (items, functions, bijections)
    assert bijections <= functions
    hidden = [*v["exhibits"], *v["crates"], *v["bays"], v["order"]["id"]]
    hidden += [c["id"] for c in v["registrar_file"] + v["depot_file"]]
    for pair in [None, *product(v["bays"], repeat=2)]:
        out = assess(v, commit(items, pair))
        assert out["delivered_chain_items"] == chains
        assert out["legal_actions"] == len(v["bays"]) ** 2
        for key, pairs in zip(MODELS, (functions, bijections), strict=True):
            expected = {"compatible_routes": len(pairs), "choice": choice(pairs, pair)}
            assert out[key] == expected
        public = json.dumps(out)
        assert not any(label in public for label in hidden)
    assert json.dumps(v, sort_keys=True) == before
    return functions, bijections


CASES = {
    "no_records_2": ((2, 0, [], []), 4, 2, 0),
    "no_records_3": ((3, 0, [], []), 9, 6, 0),
    "same_possible_intermediate": ((3, 1, [(0, 0)], [(0, 0)]), 3, 2, 1),
    "irrelevant_cards": ((3, 1, [(2, 2)], [(2, 2)]), 9, 2, 0),
    "stale_endpoints_inclusive": (
        (
            3,
            1,
            [(0, 0, 1, 2), (1, 1, 0, 1), (1, 2, 2, 2)],
            [(0, 1, 1, 2), (1, 0, 0, 1), (2, 2, 0, 0)],
        ),
        1,
        1,
        2,
    ),
    "both_active": ((2, 2, [(0, 1), (1, 0)], [(0, 0), (1, 1)]), 1, 1, 2),
    "elimination_depot": ((2, 0, [(0, 0), (1, 1)], [(0, 0)]), 2, 1, 1),
    "elimination_registrar": ((2, 0, [(0, 0)], [(0, 0), (1, 1)]), 2, 1, 1),
    "forced_undocumented": ((3, 2, [(0, 0), (2, 2)], [(0, 0), (1, 1)]), 3, 1, 1),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_small_worlds_match_independent_enumeration(name: str) -> None:
    args, functions, bijections, chains = CASES[name]
    v = view(*args)
    found = check(v)
    assert tuple(map(len, found)) == (functions, bijections)
    assert assess(v, None)["delivered_chain_items"] == chains


def test_scientific_relationships_between_named_snapshots() -> None:
    sets = {name: check(view(*args)) for name, (args, *_) in CASES.items()}
    assert sets["irrelevant_cards"][0] == sets["no_records_3"][0]
    assert sets["irrelevant_cards"][1] < sets["no_records_3"][1]
    assert (B[0], B[0]) in sets["same_possible_intermediate"][0]
    assert (B[0], B[0]) not in sets["same_possible_intermediate"][1]
    assert sets["stale_endpoints_inclusive"] == ({(B[1], B[0])}, {(B[1], B[0])})
    stale_only = view(3, 1, [(1, 2, 2, 2)], [(2, 2, 0, 0)])
    assert check(stale_only) == sets["no_records_3"]
    assert sets["elimination_depot"] == (
        {(B[0], B[0]), (B[0], B[1])},
        {(B[0], B[1])},
    )
    assert sets["forced_undocumented"][1] == {(B[0], B[1])}
    v = view(*CASES["elimination_depot"][0])
    labels = [
        tuple(assess(v, commit(E[:2], p))[k]["choice"] for k in MODELS)
        for p in ((B[0], B[0]), (B[0], B[1]), (B[1], B[0]))
    ]
    assert labels == [
        ("compatible_not_entailed", "contradicted"),
        ("compatible_not_entailed", "entailed"),
        ("contradicted", "contradicted"),
    ]


def relabel(v: Record, seed: int) -> tuple[Record, dict[str, str]]:
    rng = random.Random(seed)
    names: dict[str, str] = {}
    for domain, prefix in (("exhibits", "E"), ("crates", "C"), ("bays", "B")):
        fresh = rng.sample(range(600, 1000), len(v[domain]))
        names.update(zip(v[domain], (f"{prefix}-{k}" for k in fresh), strict=True))

    def cards(rows: list[Record], prefix: str) -> list[Record]:
        ids = rng.sample(range(100, 1000), len(rows))
        out = [
            {**c, "id": f"{prefix}-{i}", "key": names[c["key"]]}
            | {"value": names[c["value"]]}
            for i, c in zip(ids, rows, strict=True)
        ]
        return rng.sample(out, len(out))

    order = {
        "id": "T-977",
        "epoch": v["order"]["epoch"],
        "items": [names[i] for i in reversed(v["order"]["items"])],
        "weight": 7,
    }
    domains = ("exhibits", "crates", "bays")
    out = {
        **v,
        **{d: rng.sample([names[x] for x in v[d]], len(v[d])) for d in domains},
        "briefs": [dict(order)],
        "registrar_file": cards(v["registrar_file"], "R"),
        "depot_file": cards(v["depot_file"], "D"),
        "order": order,
    }
    return out, names


@pytest.mark.parametrize("name", sorted(CASES))
def test_labels_order_ids_and_weights_supply_no_constraints(name: str) -> None:
    v = view(*CASES[name][0])
    items, functions, bijections = route_sets(v)
    for seed in range(3):
        w, names = relabel(v, seed)
        moved = (names[items[0]], names[items[1]])
        flip = moved != tuple(sorted(moved))

        expected = (
            tuple(sorted(moved)),
            *(
                {
                    (names[right], names[left]) if flip else (names[left], names[right])
                    for left, right in group
                }
                for group in (functions, bijections)
            ),
        )
        assert route_sets(w) == expected
        check(w)


def snapshots() -> list[tuple[Record, Pair]]:
    rng = random.Random(20261125)
    out = []
    for n in (2, 3):
        orders = list(permutations(range(n)))
        shown = list(product((0, 1), repeat=n))
        for f, g, show_r, show_d in product(orders, orders, shown, shown):
            if n == 3 and rng.random() > 0.05:
                continue
            epoch = rng.randrange(3)
            items = tuple(rng.sample(range(n), 2))
            reg = [(x, f[x], epoch, epoch) for x in range(n) if show_r[x]]
            dep = [(y, g[y], epoch, epoch) for y in range(n) if show_d[y]]
            span = (0, epoch - 1) if epoch else (1, 2)
            for rows, truth in ((reg, f), (dep, g)):
                key = rng.randrange(n)
                wrong = [y for y in range(n) if y != truth[key]]
                if len(rows) < 3:
                    rows.append((key, rng.choice(wrong), *span))
            a, b = sorted(items)
            route = (B[g[f[a]]], B[g[f[b]]])
            out.append((view(n, epoch, reg, dep, items), route))
    return out


def test_bounded_partial_snapshots_of_bijective_truths() -> None:
    cases = snapshots()
    assert 100 <= len(cases) <= 400
    for v, truth in cases:
        _, bijections = check(v)
        assert truth in bijections


@cache
def native(map_name: str, policy: str) -> tuple[Record, set[Pair], dict[Pair, int]]:
    _, maps = certificate.native_pair(10)
    prefix, v = certificate._prefix(maps[map_name], policy)
    first, second = sorted(v["order"]["items"])
    items = (first, second)
    rewards = {}
    for pair in product(v["bays"], repeat=2):
        action = commit(items, pair)
        assert action is not None
        cut = certificate._commit(maps[map_name], prefix, action)
        result = certificate.grade(cut)
        assert result["status"] == "complete"
        rewards[pair] = result["reward"]
    winners = {pair for pair, reward in rewards.items() if reward == 1}
    assert len(winners) == 1
    return v, winners, rewards


@pytest.mark.parametrize("map_name", ("original", "swapped"))
@pytest.mark.parametrize("policy", ("other", "relevant"))
def test_native_prefix_counts_and_soundness(map_name: str, policy: str) -> None:
    v, winners, rewards = native(map_name, policy)
    (winner,) = winners
    items, functions, bijections = route_sets(v)
    assert items == tuple(sorted(v["order"]["items"]))
    if policy == "other":
        used = set(active(v["depot_file"], v["order"]["epoch"]).values())
        assert functions == set(product(v["bays"], repeat=2))
        assert bijections == set(permutations(set(v["bays"]) - used, 2))
    else:
        action = policies.follow(v)
        assert action is not None
        followed = action["routes"]
        assert functions == bijections == {tuple(followed[i] for i in items)}
    assert winner in bijections <= functions
    assert all(rewards[p] == 0 for p in set(rewards) - bijections)
    expected = (64, 30, 0) if policy == "other" else (1, 1, 2)
    label = "compatible_not_entailed" if policy == "other" else "entailed"
    for pair in (None, winner):
        out = assess(v, commit(items, pair))
        counts = (
            out["card_functions"]["compatible_routes"],
            out["generator_bijections"]["compatible_routes"],
            out["delivered_chain_items"],
        )
        assert counts == expected and out["legal_actions"] == 64
        choices = [out[k]["choice"] for k in MODELS]
        assert choices == [None if pair is None else label] * 2


def test_native_other_views_hide_the_swap() -> None:
    original, (left,), _ = native("original", "other")
    swapped, (right,), _ = native("swapped", "other")
    assert original == swapped and left != right
    assert route_sets(original) == route_sets(swapped)
