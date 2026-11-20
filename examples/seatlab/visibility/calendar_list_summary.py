"""Summarize registered Calendar Feedback final-decision replays."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from itertools import product
from statistics import mean
from typing import Any, NamedTuple

_WORLDS = (0, 4, 8)
_DRAWS = (0, 1)
_CONDITIONS = ("rr", "er", "re", "ee")
_CASES = {
    f"rx-w{world:02d}-d{direction}-r{repetition}-{arm}": (
        world,
        direction,
        repetition,
        arm,
    )
    for world, direction, repetition, arm in product(
        _WORLDS, (0, 1), (0, 1), ("blind", "peer")
    )
}
_COORDINATES = (
    "case",
    "world",
    "direction",
    "repetition",
    "source_arm",
    "draw",
    "condition",
)
_Delta = tuple[int, int, int]  # world, direction, difference


class _Outcome(NamedTuple):
    slot: int | None
    reward: int | None


def _run_id(case: str, draw: int, condition: str) -> str:
    return f"cl-{case.removeprefix('rx-')}-k{draw}-{condition}"


_REGISTRATION = {
    _run_id(case, draw, condition): (case, *coordinates, draw, condition)
    for case, coordinates in _CASES.items()
    for draw, condition in product(_DRAWS, _CONDITIONS)
}


def _outcomes(rows: Iterable[Mapping[str, object]]) -> dict[str, _Outcome]:
    outcomes: dict[str, _Outcome] = {}
    for row in rows:
        run = row.get("run")
        if type(run) is not str or run not in _REGISTRATION:
            raise ValueError("unregistered replay run")
        if run in outcomes:
            raise ValueError("duplicate replay run")
        for field, expected in zip(_COORDINATES, _REGISTRATION[run], strict=True):
            value = row.get(field)
            if type(value) is not type(expected) or value != expected:
                raise ValueError("replay coordinates do not match the registered run")
        if not {"status", "slot", "reward"} <= row.keys():
            raise ValueError("incomplete replay outcome fields")
        status, slot, reward = row["status"], row["slot"], row["reward"]
        if reward is not None and (type(reward) is not int or reward not in (0, 1)):
            raise ValueError("replay reward must be binary or null")
        if status == "complete" and type(slot) is int and type(reward) is int:
            outcomes[run] = _Outcome(slot, reward)
        elif status == "model_invalid" and slot is None and reward in (0, None):
            outcomes[run] = _Outcome(None, 0)
        elif status == "missing" and slot is None and reward is None:
            outcomes[run] = _Outcome(None, None)
        else:
            raise ValueError("inconsistent replay outcome")
    if len(outcomes) != len(_REGISTRATION):
        raise ValueError(
            "missing registered replay runs; include explicit missing rows"
        )
    return outcomes


def _counts(outcomes: Iterable[_Outcome]) -> dict[str, int]:
    valid = model_invalid = missing = wins = 0
    for slot, reward in outcomes:
        valid += slot is not None
        model_invalid += slot is None and reward == 0
        missing += reward is None
        wins += reward == 1
    return {
        "registered": valid + model_invalid + missing,
        "valid": valid,
        "model_invalid": model_invalid,
        "missing": missing,
        "scored": valid + model_invalid,
        "wins": wins,
        "valid_losses": valid - wins,
    }


def _contrast(deltas: list[_Delta], unit: str) -> dict[str, object]:
    groups: dict[int, list[int]] = {world: [] for world in _WORLDS}
    contexts = set()
    for world, direction, delta in deltas:
        groups[world].append(delta)
        contexts.add((world, direction))
    world_means = {
        str(w): mean(values) if values else None for w, values in groups.items()
    }
    supported = [value for value in world_means.values() if value is not None]
    return {
        unit: len(deltas),
        "worlds": len(supported),
        "historical_contexts": len(contexts),
        "world_counts": {str(w): len(values) for w, values in groups.items()},
        "world_means": world_means,
        "equal_world_mean": mean(supported) if len(supported) == len(_WORLDS) else None,
    }


def summarize(rows: Iterable[Mapping[str, object]]) -> dict[str, Any]:
    """Compare matched case/draw rewards, with equal weight for each world.

    Keep observed invalid replies as zero and unknown rewards as missing. An
    accepted pair requires two valid slots, and a complete quartet requires four
    observed rewards. The draw comparison uses only pairs of valid slots.
    """
    outcomes = _outcomes(rows)
    primary: list[_Delta] = []
    accepted: list[_Delta] = []
    secondary: dict[str, list[_Delta]] = {
        "er_minus_rr": [],
        "re_minus_rr": [],
        "interaction": [],
    }
    bounds: dict[int, list[tuple[int, int]]] = defaultdict(list)
    cells: dict[tuple[int, str], list[_Outcome]] = defaultdict(list)
    disagreement: dict[str, dict[str, bool | None]] = {}
    for case, (world, direction, _, _) in _CASES.items():
        draws = {
            (draw, condition): outcomes[_run_id(case, draw, condition)]
            for draw, condition in product(_DRAWS, _CONDITIONS)
        }
        for (_, condition), outcome in draws.items():
            cells[world, condition].append(outcome)
        disagreement[case] = {}
        for condition in _CONDITIONS:
            first, second = (draws[draw, condition].slot for draw in _DRAWS)
            disagreement[case][condition] = (
                first != second if first is not None and second is not None else None
            )
        for draw in _DRAWS:
            rr, er, re, ee = (draws[draw, condition] for condition in _CONDITIONS)
            baseline, exact = rr.reward, ee.reward
            bounds[world].append(
                (
                    (exact if exact is not None else 0)
                    - (baseline if baseline is not None else 1),
                    (exact if exact is not None else 1)
                    - (baseline if baseline is not None else 0),
                )
            )
            if baseline is None or exact is None:
                continue
            delta = (world, direction, exact - baseline)
            primary.append(delta)
            if rr.slot is not None and ee.slot is not None:
                accepted.append(delta)
            if er.reward is not None and re.reward is not None:
                secondary["er_minus_rr"].append(
                    (world, direction, er.reward - baseline)
                )
                secondary["re_minus_rr"].append(
                    (world, direction, re.reward - baseline)
                )
                secondary["interaction"].append(
                    (world, direction, exact - er.reward - re.reward + baseline)
                )

    by_world_bounds = {
        str(w): [mean(pair[end] for pair in bounds[w]) for end in (0, 1)]
        for w in _WORLDS
    }
    draw_counts = {}
    for condition in _CONDITIONS:
        changes = [values[condition] for values in disagreement.values()]
        draw_counts[condition] = {
            "registered_pairs": len(changes),
            "comparable_pairs": sum(change is not None for change in changes),
            "different_slots": sum(change is True for change in changes),
        }
    return {
        "counts": _counts(outcomes.values()),
        "by_world": {
            str(w): {
                "counts": _counts(o for c in _CONDITIONS for o in cells[w, c]),
                "by_condition": {c: _counts(cells[w, c]) for c in _CONDITIONS},
            }
            for w in _WORLDS
        },
        "by_condition": {
            c: _counts(o for w in _WORLDS for o in cells[w, c]) for c in _CONDITIONS
        },
        "registered_pairs": len(_CASES) * len(_DRAWS),
        "historical_contexts": len({(w, d) for w, d, _, _ in _CASES.values()}),
        "primary": _contrast(primary, "pairs"),
        "accepted_pairs_only": _contrast(accepted, "pairs"),
        "all_registered_pair_bounds": [
            mean(endpoints[end] for endpoints in by_world_bounds.values())
            for end in (0, 1)
        ],
        "all_registered_pair_bounds_by_world": by_world_bounds,
        "secondary": {
            name: _contrast(deltas, "quartets") for name, deltas in secondary.items()
        },
        "draw_disagreement": {"by_case": disagreement, "by_condition": draw_counts},
    }
