from fractions import Fraction
from typing import Any

from .core import BoundaryError, World, analyze
from .pair import partition

SCHEMA = "seatlab.temporal-boundary/v1"
ARMS = ("early_commit", "early_close", "late_commit", "late_close")
PATTERN_SCOPE = (
    "Observational pattern over declared histories only; not proof of a treatment "
    "cause, task difficulty or model performance."
)
_ARM_FIELDS = ("observed", "observations", "ambiguous_classes", "gap")
_SHARED = ("worlds", "actions", "mass", "scale", "unobserved", "informed")
_FINER = frozenset({"refinement", "equivalent"})
_COARSER = frozenset({"coarsening", "equivalent"})


def _check_arms(arms: dict[str, list[World]]) -> None:
    if type(arms) is not dict or set(arms) != set(ARMS):
        raise BoundaryError(f"arms must be exactly {', '.join(ARMS)}")
    for name in ARMS:
        if type(arms[name]) is not list:
            raise BoundaryError(f"arm {name} is not a world list")


def _check_prefix(arms: dict[str, list[World]], timing: str) -> None:
    decided = {world.name: world.observation for world in arms[f"{timing}_commit"]}
    for world in arms[f"{timing}_close"]:
        if not world.observation.startswith(decided[world.name]):
            raise BoundaryError(
                f"{timing} full history does not start with its decision prefix"
            )


def _hindsight(arms: dict[str, list[World]], timing: str) -> str:
    relation, _ = partition(arms[f"{timing}_commit"], arms[f"{timing}_close"])
    if relation not in _FINER:
        raise BoundaryError(f"{timing} full history erases decision information")
    return relation


def _cross(
    arms: dict[str, list[World]], values: dict[str, Fraction], boundary: str
) -> tuple[str, Fraction]:
    before, after = f"late_{boundary}", f"early_{boundary}"
    relation, _ = partition(arms[before], arms[after])
    gain = values[after] - values[before]
    if (relation in _FINER and gain < 0) or (relation in _COARSER and gain > 0):
        raise RuntimeError("partition value monotonicity violated")
    return relation, gain


def _analyses(arms: dict[str, list[World]], scale: int) -> dict[str, dict[str, Any]]:
    analyses = {name: analyze(arms[name], scale) for name in ARMS}
    for name in ARMS:
        for field in _SHARED:
            if analyses[name][field] != analyses["early_commit"][field]:
                raise BoundaryError(f"arm {name} differs on {field}")
    return analyses


def qualify(arms: dict[str, list[World]], scale: int) -> dict[str, Any]:
    _check_arms(arms)
    analyses = _analyses(arms, scale)
    for name in ARMS[1:]:
        partition(arms["early_commit"], arms[name])
    for timing in ("early", "late"):
        _check_prefix(arms, timing)
    hindsight = {timing: _hindsight(arms, timing) for timing in ("early", "late")}
    values = {name: Fraction(analyses[name]["observed"]) for name in ARMS}
    commit_relation, commit_gain = _cross(arms, values, "commit")
    close_relation, close_gain = _cross(arms, values, "close")
    gains = {
        "early_after_commit": values["early_close"] - values["early_commit"],
        "late_after_commit": values["late_close"] - values["late_commit"],
    }
    if min(gains.values()) < 0:
        raise RuntimeError("prefix refinement value monotonicity violated")
    pattern = (
        commit_relation == "refinement"
        and commit_gain > 0
        and hindsight["early"] == "equivalent"
        and close_relation == "equivalent"
        and close_gain == 0
        and gains["late_after_commit"] > 0
    )
    base = analyses["early_commit"]
    return {
        "schema": SCHEMA,
        "worlds": base["worlds"],
        "actions": base["actions"],
        "mass": base["mass"],
        "scale": base["scale"],
        "unobserved": base["unobserved"],
        "informed": base["informed"],
        "arms": {
            name: {field: analyses[name][field] for field in _ARM_FIELDS}
            for name in ARMS
        },
        "commit_relation": commit_relation,
        "commit_gain": str(commit_gain),
        "close_relation": close_relation,
        "close_gain": str(close_gain),
        "early_hindsight_relation": hindsight["early"],
        "late_hindsight_relation": hindsight["late"],
        "early_hindsight_gain": str(gains["early_after_commit"]),
        "late_hindsight_gain": str(gains["late_after_commit"]),
        "timing_only_pattern": pattern,
        "pattern_scope": PATTERN_SCOPE,
    }
