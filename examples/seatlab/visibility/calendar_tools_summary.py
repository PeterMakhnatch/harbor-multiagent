"""Compare registered Calendar Feedback tool policies on stock native outcomes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from itertools import product
from statistics import mean
from typing import NamedTuple, TypedDict

_WORLDS = tuple(range(12))
_PRIOR_WORLDS = (0, 4, 8)
_DIRECTIONS = _REPETITIONS = (0, 1)
_ROOMS = ("blind", "peer")
_POLICIES = ("off", "on")
_CELLS = tuple(product(_WORLDS, _DIRECTIONS, _REPETITIONS, _ROOMS))
_INVALID_ZERO = "model_invalid_scored_zero"
_UNKNOWN = {
    "not_started",
    "missing",
    "missing_evidence",
    "invalid_evidence",
    "native_failure",
    "invalid_seed",
    "partial",
}


class _Outcome(NamedTuple):
    status: str
    bound: bool
    reward: int | None
    slot: int | None


class _Pair(NamedTuple):
    world: int
    direction: int
    repetition: int
    room: str
    off: _Outcome
    on: _Outcome


class _NativeCounts(TypedDict):
    planned: int
    protocol_bound: int
    status_counts: dict[str, int]
    scored: int
    wins: int
    valid_choice_losses: int
    model_invalid_scored_zero: int
    unknown: int
    native_only: int


class _Contrast(TypedDict):
    pairs: int
    worlds: int
    world_counts: dict[str, int]
    world_means: dict[str, float | None]
    equal_world_mean: float | None
    wins: int
    ties: int
    losses: int


class _Slice(TypedDict):
    registered_pairs: int
    native_counts: dict[str, _NativeCounts]
    native_only: int
    primary: _Contrast
    accepted_pairs_only: _Contrast
    all_registered_pair_bounds: list[float]
    all_registered_pair_bounds_by_world: dict[str, list[float]]


class _Summary(_Slice):
    by_room: dict[str, _Slice]
    by_cohort: dict[str, _Slice]
    by_direction: dict[str, _Slice]
    by_repetition: dict[str, _Slice]


def _outcome(row: Mapping[str, object]) -> _Outcome:
    status = row.get("native_status")
    bound = row.get("protocol_bound")
    if type(status) is not str or type(bound) is not bool:
        raise ValueError("expected native status and boolean protocol binding")
    if "reward" not in row or "slot" not in row:
        raise ValueError("include explicit reward and slot fields")
    reward, slot = row["reward"], row["slot"]
    if reward is not None and (type(reward) is not int or reward not in (0, 1)):
        raise ValueError("native reward must be binary or null")
    if slot is not None and (type(slot) is not int or not 0 <= slot < 35):
        raise ValueError("native slot must be an integer from 0 to 34 or null")
    if status == "complete":
        consistent = reward is not None and slot is not None
    elif status == _INVALID_ZERO:
        consistent = reward == 0 and slot is None
    else:
        consistent = status in _UNKNOWN and reward is None and slot is None
    if not consistent:
        raise ValueError("inconsistent native status, reward, or slot")
    return _Outcome(status, bound, reward, slot)


def _pairs(rows: Iterable[Mapping[str, object]]) -> list[_Pair]:
    outcomes: dict[tuple[int, int, int, str, str], _Outcome] = {}
    for row in rows:
        world = row.get("world")
        direction = row.get("direction")
        repetition = row.get("repetition")
        room = row.get("room")
        policy = row.get("policy")
        if (
            type(world) is not int
            or world not in _WORLDS
            or type(direction) is not int
            or direction not in _DIRECTIONS
            or type(repetition) is not int
            or repetition not in _REPETITIONS
            or type(room) is not str
            or room not in _ROOMS
            or type(policy) is not str
            or policy not in _POLICIES
        ):
            raise ValueError("unregistered native team coordinates")
        key = (world, direction, repetition, room, policy)
        if key in outcomes:
            raise ValueError("duplicate registered native team")
        outcomes[key] = _outcome(row)
    if len(outcomes) != len(_CELLS) * len(_POLICIES):
        raise ValueError(
            "missing registered native teams; include explicit unknown rows"
        )
    return [
        _Pair(*cell, outcomes[*cell, "off"], outcomes[*cell, "on"]) for cell in _CELLS
    ]


def _native_counts(outcomes: list[_Outcome]) -> _NativeCounts:
    statuses = Counter(outcome.status for outcome in outcomes)
    wins = sum(outcome.reward == 1 for outcome in outcomes)
    valid_losses = statuses["complete"] - wins
    zeroes = statuses[_INVALID_ZERO]
    scored = wins + valid_losses + zeroes
    return {
        "planned": len(outcomes),
        "protocol_bound": sum(outcome.bound for outcome in outcomes),
        "status_counts": dict(statuses),
        "scored": scored,
        "wins": wins,
        "valid_choice_losses": valid_losses,
        "model_invalid_scored_zero": zeroes,
        "unknown": len(outcomes) - scored,
        "native_only": sum(o.reward is not None and not o.bound for o in outcomes),
    }


def _contrast(by_world: dict[int, list[int]]) -> _Contrast:
    counts = Counter(delta for values in by_world.values() for delta in values)
    means = {str(w): mean(values) if values else None for w, values in by_world.items()}
    supported = [value for value in means.values() if value is not None]
    return {
        "pairs": counts.total(),
        "worlds": len(supported),
        "world_counts": {str(w): len(values) for w, values in by_world.items()},
        "world_means": means,
        "equal_world_mean": mean(supported)
        if len(supported) == len(by_world)
        else None,
        "wins": counts[1],
        "ties": counts[0],
        "losses": counts[-1],
    }


def _slice(pairs: list[_Pair]) -> _Slice:
    worlds = sorted({pair.world for pair in pairs})
    primary: dict[int, list[int]] = {world: [] for world in worlds}
    accepted: dict[int, list[int]] = {world: [] for world in worlds}
    bounds: dict[int, list[tuple[int, int]]] = {world: [] for world in worlds}
    for pair in pairs:
        off = pair.off.reward if pair.off.bound else None
        on = pair.on.reward if pair.on.bound else None
        bounds[pair.world].append(
            (
                (on if on is not None else 0) - (off if off is not None else 1),
                (on if on is not None else 1) - (off if off is not None else 0),
            )
        )
        if off is not None and on is not None:
            delta = on - off
            primary[pair.world].append(delta)
            if pair.off.slot is not None and pair.on.slot is not None:
                accepted[pair.world].append(delta)

    world_bounds = {
        str(world): [mean(lo for lo, _ in values), mean(hi for _, hi in values)]
        for world, values in bounds.items()
    }
    native = {
        "off": _native_counts([pair.off for pair in pairs]),
        "on": _native_counts([pair.on for pair in pairs]),
    }
    return {
        "registered_pairs": len(pairs),
        "native_counts": native,
        "native_only": sum(counts["native_only"] for counts in native.values()),
        "primary": _contrast(primary),
        "accepted_pairs_only": _contrast(accepted),
        "all_registered_pair_bounds": [
            mean(values[end] for values in world_bounds.values()) for end in (0, 1)
        ],
        "all_registered_pair_bounds_by_world": world_bounds,
    }


def summarize(rows: Iterable[Mapping[str, object]]) -> _Summary:
    """Match on/off within world, direction, repetition, and native room.

    Require all registered teams, including unknowns. A reward is known for
    policy comparisons and bounds only if the stock score and tools are bound.
    The accepted-only contrast further requires two valid slots. First average
    within each world, then weight worlds equally. Report no point estimate if
    any world in the slice lacks a comparison. Rooms share opening lists, so
    matched pair counts describe coverage, not independent samples.
    """
    pairs = _pairs(rows)
    return {
        **_slice(pairs),
        "by_room": {
            room: _slice([p for p in pairs if p.room == room]) for room in _ROOMS
        },
        "by_cohort": {
            "prior": _slice([p for p in pairs if p.world in _PRIOR_WORLDS]),
            "new": _slice([p for p in pairs if p.world not in _PRIOR_WORLDS]),
        },
        "by_direction": {
            str(d): _slice([p for p in pairs if p.direction == d]) for d in _DIRECTIONS
        },
        "by_repetition": {
            str(r): _slice([p for p in pairs if p.repetition == r])
            for r in _REPETITIONS
        },
    }
