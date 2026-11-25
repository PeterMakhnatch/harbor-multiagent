import dataclasses
import itertools
import random
from collections.abc import Callable, Hashable
from fractions import Fraction
from typing import Any, NamedTuple

import pytest

from examples.seatlab.decision_boundary.core import BoundaryError, World
from examples.seatlab.decision_boundary.joint import qualify

Arms = dict[str, list[World]]
Partition = set[frozenset[str]]
Report = dict[str, Any]
Cell = tuple[str, int, int, int]
Scorer = Callable[[str, int, int, int], dict[str, int]]
Weigher = Callable[[str, int, int, int], int]
Label = Callable[[World], Hashable]

ARMS = ("neither", "first", "second", "both")
CRATES = ("crate-A", "crate-B")
BAYS = ("bay-X", "bay-Y")
FIELDS = ("shift", "registrar", "depot")
MASK = "#"
HIDDEN = {
    "neither": frozenset({"registrar", "depot"}),
    "first": frozenset({"depot"}),
    "second": frozenset({"registrar"}),
    "both": frozenset(),
}
RELATIONS = (
    ("neither_to_first", "neither", "first"),
    ("neither_to_second", "neither", "second"),
    ("first_to_both", "first", "both"),
    ("second_to_both", "second", "both"),
    ("neither_to_both", "neither", "both"),
    ("first_to_second", "first", "second"),
)
MAX_POLICIES = 1 << 14
SHIFT_MASSES = {
    ("early", 0, 0): 1,
    ("early", 0, 1): 2,
    ("early", 1, 0): 4,
    ("early", 1, 1): 6,
    ("late", 0, 0): 5,
    ("late", 0, 1): 1,
    ("late", 1, 0): 1,
    ("late", 1, 1): 2,
}


class ArmValue(NamedTuple):
    observed: Fraction
    unobserved: Fraction
    informed: Fraction
    gap: Fraction
    observations: int
    ambiguous_classes: int


def name_of(shift: str, crate: int, bay: int, lift: int) -> str:
    return f"{shift}-{CRATES[crate][-1]}{BAYS[bay][-1]}-{lift}"


def render(filing: dict[str, str], hidden: frozenset[str]) -> bytes:
    full = ";".join(f"{field}={filing[field]}" for field in FIELDS)
    shown = ";".join(
        f"{field}={MASK if field in hidden else filing[field]}" for field in FIELDS
    )
    assert all(filing[field] != MASK for field in FIELDS)
    assert (shown == full) == (not hidden)
    return shown.encode()


def unit_mass(shift: str, crate: int, bay: int, lift: int) -> int:
    return 1


def shift_mass(shift: str, crate: int, bay: int, lift: int) -> int:
    return SHIFT_MASSES[(shift, crate, bay)]


def orbit_mass(shift: str, crate: int, bay: int, lift: int) -> int:
    return 3 if shift == "late" else 1


def warehouse(
    score: Scorer,
    mass: Weigher = unit_mass,
    shifts: tuple[str, ...] = ("early",),
    lifts: tuple[int, ...] = (0,),
    hidden: dict[str, frozenset[str]] = HIDDEN,
) -> Arms:
    rows = [
        (
            name_of(shift, crate, bay, lift),
            mass(shift, crate, bay, lift),
            {"shift": shift, "registrar": CRATES[crate], "depot": BAYS[bay]},
            score(shift, crate, bay, lift),
        )
        for shift in shifts
        for crate in (0, 1)
        for bay in (0, 1)
        for lift in lifts
    ]
    return {
        arm: [
            World(name, weight, render(filing, hidden[arm]), dict(scores))
            for name, weight, filing, scores in rows
        ]
        for arm in ARMS
    }


def best_total(worlds: list[World], key: Label) -> int:
    actions = tuple(worlds[0].scores)
    labels = tuple(dict.fromkeys(key(world) for world in worlds))
    assert len(actions) ** len(labels) <= MAX_POLICIES
    return max(
        sum(
            world.mass * world.scores[policy[labels.index(key(world))]]
            for world in worlds
        )
        for policy in itertools.product(actions, repeat=len(labels))
    )


def per_world_best(worlds: list[World]) -> int:
    return sum(world.mass * max(world.scores.values()) for world in worlds)


def reference_arm(worlds: list[World], scale: int) -> ArmValue:
    denominator = scale * sum(world.mass for world in worlds)
    unobserved = best_total(worlds, lambda world: b"")
    observed = best_total(worlds, lambda world: world.observation)
    informed = per_world_best(worlds)
    classes = tuple(dict.fromkeys(world.observation for world in worlds))
    ambiguous = 0
    for observation in classes:
        members = [world for world in worlds if world.observation == observation]
        ambiguous += per_world_best(members) > best_total(members, lambda world: b"")
    return ArmValue(
        observed=Fraction(observed, denominator),
        unobserved=Fraction(unobserved, denominator),
        informed=Fraction(informed, denominator),
        gap=Fraction(informed - observed, denominator),
        observations=len(classes),
        ambiguous_classes=ambiguous,
    )


def blocks(
    worlds: list[World], key: Label = lambda world: world.observation
) -> Partition:
    grouped: dict[Hashable, set[str]] = {}
    for world in worlds:
        grouped.setdefault(key(world), set()).add(world.name)
    return {frozenset(block) for block in grouped.values()}


def finer(fine: Partition, coarse: Partition) -> bool:
    return all(any(block <= other for other in coarse) for block in fine)


def relation(before: list[World], after: list[World]) -> str:
    old, new = blocks(before), blocks(after)
    splits_only = finer(new, old)
    merges_only = finer(old, new)
    if splits_only and merges_only:
        return "equivalent"
    if splits_only:
        return "refinement"
    if merges_only:
        return "coarsening"
    return "incomparable"


def meet(arms: Arms) -> Partition:
    first = {world.name: world.observation for world in arms["first"]}
    second = {world.name: world.observation for world in arms["second"]}
    return blocks(
        arms["neither"], key=lambda world: (first[world.name], second[world.name])
    )


def reference_valid(arms: dict[str, list[World]]) -> bool:
    public = blocks(arms["neither"])
    coarsened = all(finer(blocks(arms[arm]), public) for arm in ARMS[1:])
    return coarsened and blocks(arms["both"]) == meet(arms)


def sign(value: Fraction) -> str:
    return "positive" if value > 0 else "negative" if value < 0 else "zero"


def reference(arms: Arms, scale: int) -> Report:
    per_arm = {arm: reference_arm(arms[arm], scale) for arm in ARMS}
    neither, first, second, both = (per_arm[arm].observed for arm in ARMS)
    interaction = both - first - second + neither
    worlds = arms["neither"]
    return {
        "schema": "seatlab.joint-value/v1",
        "worlds": len(worlds),
        "actions": len(worlds[0].scores),
        "mass": sum(world.mass for world in worlds),
        "scale": scale,
        "arms": {
            arm: {
                field: str(value) if type(value) is Fraction else value
                for field, value in per_arm[arm]._asdict().items()
            }
            for arm in ARMS
        },
        "relations": {
            key: relation(arms[before], arms[after]) for key, before, after in RELATIONS
        },
        "joint_partition": "meet",
        "values": {arm: str(per_arm[arm].observed) for arm in ARMS},
        "gains": {
            "first_over_neither": str(first - neither),
            "second_over_neither": str(second - neither),
            "both_over_neither": str(both - neither),
        },
        "conditional_gains": {
            "first_given_second": str(both - second),
            "second_given_first": str(both - first),
        },
        "interaction": str(interaction),
        "interaction_sign": sign(interaction),
        "each_source_adds_value_given_other": both > first and both > second,
    }


def check(arms: Arms, scale: int) -> Report:
    assert reference_valid(arms)
    result = qualify(arms, scale)
    assert result == reference(arms, scale)
    return result


def summary(result: Report) -> Report:
    return {
        key: result[key]
        for key in (
            "values",
            "gains",
            "conditional_gains",
            "interaction",
            "interaction_sign",
            "each_source_adds_value_given_other",
        )
    }


def complement(shift: str, crate: int, bay: int, lift: int) -> dict[str, int]:
    straight = int((crate == bay) != (shift == "late"))
    return {"straight": straight, "cross": 1 - straight}


def redundant(swapped: bool) -> Scorer:
    def score(shift: str, crate: int, bay: int, lift: int) -> dict[str, int]:
        decisive, partial = (bay, crate) if swapped else (crate, bay)
        return {
            "load": int(decisive == 0),
            "hold": int(decisive == 1),
            "relay": int(partial == 0),
        }

    return score


def substitutes(shift: str, crate: int, bay: int, lift: int) -> dict[str, int]:
    return {"load": 0 if crate == bay == 1 else 4, "hold": 3}


def shift_score(shift: str, crate: int, bay: int, lift: int) -> dict[str, int]:
    if shift == "late":
        return {"straight": 0, "cross": 0, "hold": 2}
    straight = 2 * int(crate == bay)
    return {"straight": straight, "cross": 2 - straight, "hold": 1}


def lift_score(shift: str, crate: int, bay: int, lift: int) -> dict[str, int]:
    straight = int((crate == bay) != (lift == 1))
    return {"straight": straight, "cross": 1 - straight}


def swap_cell(name: str, position: int, letters: str) -> str:
    shift, cell, lift = name.split("-")
    swapped = letters[1 - letters.index(cell[position])]
    cell = cell[:position] + swapped + cell[position + 1 :]
    return f"{shift}-{cell}-{lift}"


def orbit_value(
    worlds: list[World], scale: int, swap: Callable[[str], str]
) -> Fraction:
    by_name = {world.name: world for world in worlds}
    for world in worlds:
        partner = by_name[swap(world.name)]
        assert partner.name != world.name
        assert by_name[swap(partner.name)] is world
        assert partner.observation == world.observation
        assert partner.mass == world.mass
        assert all(
            world.scores[action] + partner.scores[action] == scale
            and world.scores[action] in (0, scale)
            for action in world.scores
        )
    won = sum(world.mass * scale for world in worlds)
    return Fraction(won, 2 * scale * sum(world.mass for world in worlds))


def crate_swap(name: str) -> str:
    return swap_cell(name, 0, "AB")


def bay_swap(name: str) -> str:
    return swap_cell(name, 1, "XY")


def test_complement_values_follow_swap_orbits_without_policy_search():
    arms = warehouse(complement, mass=orbit_mass, shifts=("early", "late"))
    both = arms["both"]
    assert len(blocks(both)) == len(both)
    assert all(max(world.scores.values()) == 1 for world in both)
    derived = {
        "neither": orbit_value(arms["neither"], 1, crate_swap),
        "first": orbit_value(arms["first"], 1, bay_swap),
        "second": orbit_value(arms["second"], 1, crate_swap),
        "both": Fraction(1),
    }
    result = check(arms, 1)
    assert result["values"] == {arm: str(value) for arm, value in derived.items()}
    assert summary(result) == {
        "values": {"neither": "1/2", "first": "1/2", "second": "1/2", "both": "1"},
        "gains": {
            "first_over_neither": "0",
            "second_over_neither": "0",
            "both_over_neither": "1/2",
        },
        "conditional_gains": {"first_given_second": "1/2", "second_given_first": "1/2"},
        "interaction": "1/2",
        "interaction_sign": "positive",
        "each_source_adds_value_given_other": True,
    }
    assert result["relations"] == {
        "neither_to_first": "refinement",
        "neither_to_second": "refinement",
        "first_to_both": "refinement",
        "second_to_both": "refinement",
        "neither_to_both": "refinement",
        "first_to_second": "incomparable",
    }


@pytest.mark.parametrize(
    "swapped,expected",
    [
        (
            False,
            {
                "values": {
                    "neither": "1/2",
                    "first": "1",
                    "second": "3/4",
                    "both": "1",
                },
                "gains": {
                    "first_over_neither": "1/2",
                    "second_over_neither": "1/4",
                    "both_over_neither": "1/2",
                },
                "conditional_gains": {
                    "first_given_second": "1/4",
                    "second_given_first": "0",
                },
            },
        ),
        (
            True,
            {
                "values": {
                    "neither": "1/2",
                    "first": "3/4",
                    "second": "1",
                    "both": "1",
                },
                "gains": {
                    "first_over_neither": "1/4",
                    "second_over_neither": "1/2",
                    "both_over_neither": "1/2",
                },
                "conditional_gains": {
                    "first_given_second": "0",
                    "second_given_first": "1/4",
                },
            },
        ),
    ],
)
def test_directional_necessity_versus_redundant_source(swapped, expected):
    result = check(warehouse(redundant(swapped)), 1)
    assert summary(result) == {
        **expected,
        "interaction": "-1/4",
        "interaction_sign": "negative",
        "each_source_adds_value_given_other": False,
    }


def test_negative_interaction_when_each_source_is_still_needed():
    result = check(warehouse(substitutes), 4)
    assert summary(result) == {
        "values": {"neither": "3/4", "first": "7/8", "second": "7/8", "both": "15/16"},
        "gains": {
            "first_over_neither": "1/8",
            "second_over_neither": "1/8",
            "both_over_neither": "3/16",
        },
        "conditional_gains": {
            "first_given_second": "1/16",
            "second_given_first": "1/16",
        },
        "interaction": "-1/16",
        "interaction_sign": "negative",
        "each_source_adds_value_given_other": True,
    }
    assert result["arms"]["both"]["gap"] == "0"
    assert result["arms"]["neither"]["informed"] == "15/16"


def test_nonuniform_masses_and_nonconstant_public_baseline():
    arms = warehouse(shift_score, mass=shift_mass, shifts=("early", "late"))
    result = check(arms, 2)
    assert result["mass"] == 22
    assert summary(result) == {
        "values": {"neither": "8/11", "first": "17/22", "second": "19/22", "both": "1"},
        "gains": {
            "first_over_neither": "1/22",
            "second_over_neither": "3/22",
            "both_over_neither": "3/11",
        },
        "conditional_gains": {
            "first_given_second": "3/22",
            "second_given_first": "5/22",
        },
        "interaction": "1/11",
        "interaction_sign": "positive",
        "each_source_adds_value_given_other": True,
    }
    assert {arm: row["observations"] for arm, row in result["arms"].items()} == {
        "neither": 2,
        "first": 4,
        "second": 4,
        "both": 8,
    }
    assert {arm: row["ambiguous_classes"] for arm, row in result["arms"].items()} == {
        "neither": 1,
        "first": 2,
        "second": 2,
        "both": 0,
    }
    assert {row["unobserved"] for row in result["arms"].values()} == {"31/44"}
    assert Fraction(result["values"]["neither"]) > Fraction("31/44")

    sealed = {**HIDDEN, "neither": frozenset(FIELDS)}
    constant = warehouse(shift_score, shift_mass, ("early", "late"), hidden=sealed)
    assert len(blocks(constant["neither"])) == 1
    contrast = check(constant, 2)
    assert {arm: contrast["values"][arm] for arm in ARMS[1:]} == {
        arm: result["values"][arm] for arm in ARMS[1:]
    }
    assert contrast["values"]["neither"] == "31/44"
    assert contrast["gains"] == {
        "first_over_neither": "3/44",
        "second_over_neither": "7/44",
        "both_over_neither": "13/44",
    }
    assert contrast["conditional_gains"] == result["conditional_gains"]
    assert contrast["interaction"] == "3/44"


def swap_bytes(observation: bytes, left: bytes, right: bytes) -> bytes:
    assert b"\x00" not in observation
    return (
        observation.replace(left, b"\x00").replace(right, left).replace(b"\x00", right)
    )


def remap(worlds: list[World], transform: Callable[[bytes], bytes]) -> list[World]:
    return [
        dataclasses.replace(world, observation=transform(world.observation))
        for world in worlds
    ]


def reverse_menu(worlds: list[World], rows: range) -> list[World]:
    return [
        dataclasses.replace(world, scores=dict(reversed(list(world.scores.items()))))
        if index in rows
        else world
        for index, world in enumerate(worlds)
    ]


def test_row_order_menu_insertion_order_and_record_relabeling_keep_result():
    arms = warehouse(shift_score, mass=shift_mass, shifts=("early", "late"))
    original = check(arms, 2)
    crate_a, crate_b = (crate.encode() for crate in CRATES)
    bay_x, bay_y = (bay.encode() for bay in BAYS)
    first = remap(arms["first"][::-1], lambda obs: swap_bytes(obs, crate_a, crate_b))
    second = remap(arms["second"], lambda obs: swap_bytes(obs, bay_x, bay_y))
    relabeled = {
        "neither": arms["neither"],
        "first": reverse_menu(first, range(1, len(first), 2)),
        "second": reverse_menu(second, range(len(second))),
        "both": remap(arms["both"][::-1], lambda obs: b"joint:" + obs[::-1]),
    }
    assert relabeled["first"][0].observation != arms["first"][-1].observation
    assert tuple(relabeled["first"][0].scores) == tuple(arms["neither"][0].scores)
    assert tuple(relabeled["first"][1].scores) != tuple(arms["neither"][0].scores)
    assert tuple(relabeled["second"][0].scores) != tuple(arms["neither"][0].scores)
    assert all(
        world.scores.keys() == arms["neither"][0].scores.keys()
        for arm in ARMS
        for world in relabeled[arm]
    )
    assert reference(relabeled, 2) == original
    assert qualify(relabeled, 2) == original
    assert qualify({arm: relabeled[arm] for arm in reversed(ARMS)}, 2) == original


def replace_row(arms: Arms, arm: str, index: int, **changes: Any) -> Arms:
    rows = list(arms[arm])
    rows[index] = dataclasses.replace(rows[index], **changes)
    return {**arms, arm: rows}


def replace_all(arms: Arms, arm: str, change: Callable[[World], World]) -> Arms:
    return {**arms, arm: [change(world) for world in arms[arm]]}


def one_payoff(arms):
    world = arms["second"][3]
    assert world.scores["hold"] == 1
    return replace_row(arms, "second", 3, scores={**world.scores, "hold": 0})


def one_mass(arms):
    return replace_row(arms, "first", 2, mass=arms["first"][2].mass + 1)


def extended_menu(arms):
    return replace_all(
        arms,
        "both",
        lambda world: dataclasses.replace(world, scores={**world.scores, "wait": 0}),
    )


def renamed_rule(arms):
    def rename(world):
        scores = {("park" if a == "hold" else a): s for a, s in world.scores.items()}
        return dataclasses.replace(world, scores=scores)

    return replace_all(arms, "neither", rename)


def out_of_scale(arms):
    world = arms["second"][0]
    return replace_row(arms, "second", 0, scores={**world.scores, "straight": 3})


def renamed_world(arms):
    return replace_row(arms, "second", 0, name="early-AX-9")


def neither_reads_registrar(arms):
    return {**arms, "neither": arms["first"]}


def missing_arm(arms):
    return {arm: arms[arm] for arm in ARMS[:3]}


def extra_arm(arms):
    return {**arms, "third": arms["both"]}


def legacy_labels(arms):
    return {
        "neither": arms["neither"],
        "registrar": arms["first"],
        "depot": arms["second"],
        "both": arms["both"],
    }


def tuple_arm(arms):
    return {**arms, "first": tuple(arms["first"])}


@pytest.mark.parametrize(
    "mutate",
    [
        one_payoff,
        one_mass,
        extended_menu,
        renamed_rule,
        out_of_scale,
        renamed_world,
        missing_arm,
        extra_arm,
        legacy_labels,
        tuple_arm,
    ],
)
def test_reject_single_changed_payoff_weight_menu_or_roster(mutate):
    arms = warehouse(shift_score, mass=shift_mass, shifts=("early", "late"))
    check(arms, 2)
    with pytest.raises(BoundaryError):
        qualify(mutate(arms), 2)


def test_reject_public_baseline_that_reads_a_private_source():
    arms = neither_reads_registrar(
        warehouse(shift_score, mass=shift_mass, shifts=("early", "late"))
    )
    assert not reference_valid(arms)
    with pytest.raises(BoundaryError, match="coarsen"):
        qualify(arms, 2)


def mask_late_registrar(world: World) -> World:
    if not world.name.startswith("late-"):
        return world
    masked = world.observation
    for crate in CRATES:
        shown = f"registrar={crate}".encode()
        masked = masked.replace(shown, f"registrar={MASK}".encode())
    return dataclasses.replace(world, observation=masked)


def test_reject_joint_view_that_erases_an_irrelevant_single_distinction():
    arms = warehouse(shift_score, mass=shift_mass, shifts=("early", "late"))
    lossy = replace_all(arms, "both", mask_late_registrar)
    late = {w.observation for w in lossy["both"] if w.name.startswith("late-")}
    filing = {"shift": "late", "registrar": CRATES[0], "depot": BAYS[0]}
    assert render(filing, frozenset({"registrar"})) in late
    assert len(late) == 2
    assert not reference_valid(lossy)
    lossy_both = reference_arm(lossy["both"], 2)
    assert lossy_both.observed == reference_arm(arms["both"], 2).observed
    assert lossy_both.observations == 6
    with pytest.raises(BoundaryError, match="erases"):
        qualify(lossy, 2)


def test_reject_joint_only_nonce_that_splits_a_hidden_lift():
    arms = warehouse(lift_score, lifts=(0, 1))
    valid = check(arms, 1)
    assert valid["arms"]["both"]["gap"] == "1/2"
    assert valid["arms"]["both"]["observations"] == 4
    target = arms["both"][0]
    nonce = replace_row(
        arms, "both", 0, observation=target.observation + b";nonce=7f3a"
    )
    assert not reference_valid(nonce)
    nonce_both = reference_arm(nonce["both"], 1)
    assert nonce_both.observed > Fraction(valid["values"]["both"])
    with pytest.raises(BoundaryError, match="joint-only"):
        qualify(nonce, 1)


def seeded_arms(rng: random.Random) -> tuple[Arms, int, str]:
    scale = rng.randint(1, 4)
    actions = rng.sample(("straight", "cross", "hold", "relay"), rng.randint(1, 3))
    shifts = ("early", "late") if rng.random() < 0.5 else ("early",)
    lifts = (0, 1) if len(shifts) == 1 and rng.random() < 0.5 else (0,)
    masses: dict[Cell, int] = {}
    scores: dict[Cell, dict[str, int]] = {}

    def mass(shift: str, crate: int, bay: int, lift: int) -> int:
        return masses.setdefault((shift, crate, bay, lift), rng.randint(1, 6))

    def score(shift: str, crate: int, bay: int, lift: int) -> dict[str, int]:
        cell = (shift, crate, bay, lift)
        return scores.setdefault(cell, {a: rng.randint(0, scale) for a in actions})

    arms = warehouse(score, mass, shifts, lifts)
    size = len(arms["both"])
    mutation = rng.choice(("none", "nonce", "erase", "leak", "constant"))
    i, j = rng.randrange(size), rng.randrange(size)
    if mutation == "nonce":
        both = arms["both"][i]
        arms = replace_row(arms, "both", i, observation=both.observation + b";n")
    elif mutation == "erase":
        arms = replace_row(arms, "both", i, observation=arms["both"][j].observation)
    elif mutation == "leak":
        arms = replace_row(arms, "neither", i, observation=arms["first"][i].observation)
    elif mutation == "constant":
        arms = replace_all(
            arms, "neither", lambda w: dataclasses.replace(w, observation=b"")
        )
    for arm in ARMS:
        if rng.random() < 0.5:
            arms[arm] = arms[arm][::-1]
        if rng.random() < 0.5:
            arms[arm] = reverse_menu(arms[arm], range(rng.randrange(2), size, 2))
    return arms, scale, mutation


def test_seeded_differential_against_policy_enumeration():
    outcomes: dict[tuple[str, bool], int] = {}
    for seed in range(64):
        arms, scale, mutation = seeded_arms(random.Random(20261125 + seed))
        valid = reference_valid(arms)
        outcomes[(mutation, valid)] = outcomes.get((mutation, valid), 0) + 1
        if not valid:
            with pytest.raises(BoundaryError):
                qualify(arms, scale)
            continue
        result = qualify(arms, scale)
        assert result == reference(arms, scale), seed
        gains = {key: Fraction(value) for key, value in result["gains"].items()}
        conditional = {
            key: Fraction(value) for key, value in result["conditional_gains"].items()
        }
        interaction = Fraction(result["interaction"])
        assert (
            conditional["first_given_second"] - gains["first_over_neither"]
            == interaction
        )
        assert (
            conditional["second_given_first"] - gains["second_over_neither"]
            == interaction
        )
        assert min(*gains.values(), *conditional.values()) >= 0
    assert sum(count for (_, valid), count in outcomes.items() if valid) >= 16
    assert sum(count for (_, valid), count in outcomes.items() if not valid) >= 8
    assert {mutation for mutation, _ in outcomes} == {
        "none",
        "nonce",
        "erase",
        "leak",
        "constant",
    }
