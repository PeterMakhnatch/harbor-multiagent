from fractions import Fraction
from typing import Any

from .core import BoundaryError, World, analyze
from .pair import partition

SCHEMA = "seatlab.joint-value/v1"
ARMS = ("neither", "first", "second", "both")
_ARM_FIELDS = (
    "observed",
    "unobserved",
    "informed",
    "gap",
    "observations",
    "ambiguous_classes",
)
_FINER = frozenset({"refinement", "equivalent"})


def _sign(value: Fraction) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def _check_arms(arms: dict[str, list[World]]) -> None:
    if type(arms) is not dict or len(arms) != len(ARMS) or set(arms) != set(ARMS):
        raise BoundaryError("arms must be exactly neither, first, second, both")
    for name in ARMS:
        if type(arms[name]) is not list:
            raise BoundaryError(f"arm {name} is not a world list")


def _relations(arms: dict[str, list[World]]) -> dict[str, str]:
    pairs = (
        ("neither_to_first", "neither", "first"),
        ("neither_to_second", "neither", "second"),
        ("first_to_both", "first", "both"),
        ("second_to_both", "second", "both"),
        ("neither_to_both", "neither", "both"),
        ("first_to_second", "first", "second"),
    )
    relations = {
        key: partition(arms[before], arms[after])[0] for key, before, after in pairs
    }
    for key in ("neither_to_first", "neither_to_second", "neither_to_both"):
        if relations[key] not in _FINER:
            raise BoundaryError(f"neither partition does not coarsen: {key}")
    for key in ("first_to_both", "second_to_both"):
        if relations[key] not in _FINER:
            raise BoundaryError(f"joint partition erases single information: {key}")
    return relations


def _check_meet(arms: dict[str, list[World]]) -> int:
    first = {world.name: world.observation for world in arms["first"]}
    second = {world.name: world.observation for world in arms["second"]}
    joint_to_pair: dict[bytes, tuple[bytes, bytes]] = {}
    pair_to_joint: dict[tuple[bytes, bytes], bytes] = {}
    for world in arms["both"]:
        pair = (first[world.name], second[world.name])
        if joint_to_pair.setdefault(world.observation, pair) != pair:
            raise BoundaryError("joint partition erases single-source information")
        if pair_to_joint.setdefault(pair, world.observation) != world.observation:
            raise BoundaryError("joint partition carries joint-only information")
    if len(joint_to_pair) != len(pair_to_joint):
        raise RuntimeError("meet bijection invariant violated")
    return len(pair_to_joint)


def _analyses(
    arms: dict[str, list[World]], scale: int
) -> dict[str, dict[str, int | str]]:
    analyses = {name: analyze(arms[name], scale) for name in ARMS}
    shared = ("worlds", "actions", "mass", "scale", "unobserved", "informed")
    for name in ARMS:
        for field in shared:
            if analyses[name][field] != analyses["neither"][field]:
                raise BoundaryError(f"arm {name} differs on {field}")
    return analyses


def _values(values: dict[str, Fraction]) -> dict[str, object]:
    neither, first = values["neither"], values["first"]
    second, both = values["second"], values["both"]
    first_given_second = both - second
    second_given_first = both - first
    interaction = both - first - second + neither
    increments = (first - neither, second - neither, first_given_second)
    if min(increments) < 0 or second_given_first < 0:
        raise RuntimeError("refinement value monotonicity violated")
    return {
        "values": {name: str(values[name]) for name in ARMS},
        "gains": {
            "first_over_neither": str(first - neither),
            "second_over_neither": str(second - neither),
            "both_over_neither": str(both - neither),
        },
        "conditional_gains": {
            "first_given_second": str(first_given_second),
            "second_given_first": str(second_given_first),
        },
        "interaction": str(interaction),
        "interaction_sign": _sign(interaction),
        "each_source_adds_value_given_other": first_given_second > 0
        and second_given_first > 0,
    }


def qualify(arms: dict[str, list[World]], scale: int) -> dict[str, Any]:
    _check_arms(arms)
    analyses = _analyses(arms, scale)
    relations = _relations(arms)
    meet_classes = _check_meet(arms)
    if meet_classes != analyses["both"]["observations"]:
        raise RuntimeError("meet class count invariant violated")
    base = analyses["neither"]
    return {
        "schema": SCHEMA,
        "worlds": base["worlds"],
        "actions": base["actions"],
        "mass": base["mass"],
        "scale": base["scale"],
        "arms": {
            name: {field: analyses[name][field] for field in _ARM_FIELDS}
            for name in ARMS
        },
        "relations": relations,
        "joint_partition": "meet",
        **_values({name: Fraction(analyses[name]["observed"]) for name in ARMS}),
    }
