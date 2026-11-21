from collections.abc import Iterable, Mapping
from math import fsum
from typing import NamedTuple, TypedDict, TypeGuard

from ..team_report import _registered_fingerprint
from .dispatch_mix_plan import (
    EPISODES,
    REPETITIONS,
    ROLES,
    SEEDS,
    Cell,
    grid,
    model_for,
)

_CONDITIONS = ("fresh", "reordered")
_PAIRINGS = ("cd", "dc")
_STATUSES = ("valid", "missing", "incomplete", "invalid_evidence")
_ACTION_FIELDS = {"id", "choice", "changed", "optimal", "feasible"}


class _Action(NamedTuple):
    choice: str
    changed: bool
    optimal: bool


class _Evidence(NamedTuple):
    score: float
    inventory_exact: int
    actions: dict[int, _Action]


class _RepeatGroup(TypedDict):
    seed: int
    condition: str
    pairing: str
    disagreeing_shipment_ids: list[int] | None


type _Coordinate = tuple[int, int, str, str]
type _Valid = dict[_Coordinate, _Evidence]


class _Contrast(NamedTuple):
    difference: float | None
    low: float
    high: float


class _Aggregate(NamedTuple):
    completed: int
    observed_mean: float | None
    low: float
    high: float
    signs: dict[str, int]


def _models(cell: Cell) -> dict[str, str]:
    return {role: model_for(cell, role) for role in ROLES}


def _is_plain_dict(value: object) -> TypeGuard[Mapping[object, object]]:
    return type(value) is dict


def _actions(value: object) -> dict[int, _Action] | None:
    if type(value) is not list or len(value) != EPISODES:
        return None
    actions: dict[int, _Action] = {}
    for action in value:
        if not _is_plain_dict(action) or action.keys() != _ACTION_FIELDS:
            return None
        case, choice = action["id"], action["choice"]
        changed, optimal, feasible = (
            action["changed"],
            action["optimal"],
            action["feasible"],
        )
        if (
            type(case) is not int
            or not 0 <= case < EPISODES
            or case in actions
            or type(choice) is not str
            or choice not in ("A", "B", "C", "D")
            or type(changed) is not bool
            or type(optimal) is not bool
            or type(feasible) is not bool
            or changed != bool(case % 2)
            or (optimal and not feasible)
        ):
            return None
        actions[case] = _Action(choice, changed, optimal)
    return actions


def _evidence(row: Mapping[object, object], cell: Cell) -> _Evidence | None:
    score, exact = row.get("native_score"), row.get("inventory_exact")
    if type(score) is not float and type(score) is not int:
        return None
    if (
        not 0 <= score <= 1
        or type(exact) is not int
        or not 0 <= exact <= EPISODES
        or type(row.get("fingerprint")) is not str
        or row["fingerprint"] != _registered_fingerprint(cell["seed"], EPISODES)
    ):
        return None
    if "requested_models" in row and row["requested_models"] != _models(cell):
        return None
    actions = _actions(row.get("actions"))
    return _Evidence(score, exact, actions) if actions is not None else None


def _registered_rows(
    rows: Iterable[object], plan: list[Cell]
) -> tuple[_Valid, list[dict[str, object]], int]:
    known = {cell["cell_id"] for cell in plan}
    seen: dict[str, Mapping[object, object] | None] = {}
    unregistered = 0
    for row in rows:
        if not _is_plain_dict(row):
            unregistered += 1
            continue
        cell_id = row.get("cell_id")
        if type(cell_id) is not str or cell_id not in known:
            unregistered += 1
        elif cell_id in seen:
            seen[cell_id] = None
        else:
            seen[cell_id] = row
    valid: _Valid = {}
    public: list[dict[str, object]] = []
    for cell in plan:
        cell_id = cell["cell_id"]
        row = seen.get(cell_id)
        evidence = None
        status = "invalid_evidence" if cell_id in seen else "missing"
        if row is not None and all(
            type(row.get(key)) is type(expected) and row[key] == expected
            for key, expected in cell.items()
        ):
            reported = row.get("status")
            if type(reported) is str and reported in _STATUSES:
                status = reported
                if status == "valid":
                    evidence = _evidence(row, cell)
                    if evidence is None:
                        status = "invalid_evidence"
        if evidence is not None:
            coord = (
                cell["seed"],
                cell["repetition"],
                cell["condition"],
                cell["pairing"],
            )
            valid[coord] = evidence
        public.append(
            {
                **cell,
                "status": status,
                "native_score": None if evidence is None else evidence.score,
            }
        )
    return valid, public, unregistered


def _contrast(scores: list[tuple[float | None, int]]) -> _Contrast:
    known = fsum(score * sign for score, sign in scores if score is not None)
    missing = [sign for score, sign in scores if score is None]
    low = fsum([known, *(sign for sign in missing if sign < 0)])
    high = fsum([known, *(sign for sign in missing if sign > 0)])
    return _Contrast(None if missing else known, low, high)


def _aggregate(values: list[_Contrast]) -> _Aggregate:
    seen = [value.difference for value in values if value.difference is not None]
    return _Aggregate(
        completed=len(seen),
        observed_mean=fsum(seen) / len(seen) if seen else None,
        low=fsum(value.low for value in values) / len(values),
        high=fsum(value.high for value in values) / len(values),
        signs={
            "positive": sum(delta > 0 for delta in seen),
            "negative": sum(delta < 0 for delta in seen),
            "ties": sum(delta == 0 for delta in seen),
        },
    )


def _effect(
    valid: _Valid, signs: dict[tuple[str, str], int], unit: str
) -> dict[str, object]:
    registered_key = f"registered_{unit}"
    completed_key = f"completed_{unit}"
    seed_blocks: list[dict[str, object]] = []
    seed_values = []
    completed = 0
    sign_counts = {"positive": 0, "negative": 0, "ties": 0}
    for seed in SEEDS:
        repetitions: list[dict[str, object]] = []
        values = []
        for repetition in REPETITIONS:
            scores: dict[str, dict[str, float | None]] = {}
            terms = []
            for (condition, pairing), sign in signs.items():
                evidence = valid.get((seed, repetition, condition, pairing))
                score = None if evidence is None else evidence.score
                scores.setdefault(condition, {})[pairing] = score
                terms.append((score, sign))
            delta = _contrast(terms)
            values.append(delta)
            repetitions.append(
                {
                    "repetition": repetition,
                    "scores": scores,
                    "difference": delta.difference,
                    "bounds": [delta.low, delta.high],
                }
            )
        block = _aggregate(values)
        seed_blocks.append(
            {
                "seed": seed,
                registered_key: len(values),
                completed_key: block.completed,
                "observed_mean": block.observed_mean,
                "bounds": [block.low, block.high],
                "signs": block.signs,
                "repetitions": repetitions,
            }
        )
        seed_values.append(_Contrast(block.observed_mean, block.low, block.high))
        completed += block.completed
        for name in sign_counts:
            sign_counts[name] += block.signs[name]
    seeds = _aggregate(seed_values)
    return {
        registered_key: len(SEEDS) * len(REPETITIONS),
        completed_key: completed,
        "signs": sign_counts,
        "seed_blocks": seed_blocks,
        "seed_signs": seeds.signs,
        "observed_seeds": seeds.completed,
        "observed_seed_mean": seeds.observed_mean,
        "equal_seed_weight_mean": (
            seeds.observed_mean if seeds.completed == len(SEEDS) else None
        ),
        "bounds": [seeds.low, seeds.high],
    }


def _rate(count: int, observed: int, registered: int) -> dict[str, int | float | None]:
    return {
        "count": count,
        "observed_denominator": observed,
        "registered_denominator": registered,
        "observed_rate": count / observed if observed else None,
    }


def _arm(
    valid: _Valid, plan: list[Cell], condition: str, pairing: str
) -> dict[str, object]:
    cells = [
        cell
        for cell in plan
        if (cell["condition"], cell["pairing"]) == (condition, pairing)
    ]
    rows = [
        row
        for (_, _, current_condition, current_pairing), row in valid.items()
        if (current_condition, current_pairing) == (condition, pairing)
    ]
    registered = len(cells)
    return {
        "requested_models": _models(cells[0]),
        "registered_trials": registered,
        "valid_trials": len(rows),
        "inventory_exact": _rate(
            sum(row.inventory_exact for row in rows),
            len(rows) * EPISODES,
            registered * EPISODES,
        ),
        "optimal": {
            label: _rate(
                sum(
                    action.optimal
                    for row in rows
                    for action in row.actions.values()
                    if action.changed == changed
                ),
                len(rows) * (EPISODES // 2),
                registered * (EPISODES // 2),
            )
            for label, changed in (("changed", True), ("stable", False))
        },
    }


def _repeats(valid: _Valid) -> dict[str, object]:
    groups: list[_RepeatGroup] = []
    first, second = REPETITIONS
    for seed in SEEDS:
        for condition in _CONDITIONS:
            for pairing in _PAIRINGS:
                left = valid.get((seed, first, condition, pairing))
                right = valid.get((seed, second, condition, pairing))
                different = None
                if left is not None and right is not None:
                    different = [
                        case
                        for case in range(EPISODES)
                        if left.actions[case].choice != right.actions[case].choice
                    ]
                groups.append(
                    {
                        "seed": seed,
                        "condition": condition,
                        "pairing": pairing,
                        "disagreeing_shipment_ids": different,
                    }
                )
    observed = [
        disagreements
        for group in groups
        if (disagreements := group["disagreeing_shipment_ids"]) is not None
    ]
    return {
        "registered_comparisons": len(groups),
        "completed_comparisons": len(observed),
        "choice_disagreement": _rate(
            sum(map(len, observed)), len(observed) * EPISODES, len(groups) * EPISODES
        ),
        "groups": groups,
    }


def summarize(rows: Iterable[object]) -> dict[str, object]:
    plan = grid()
    valid, cells, unregistered = _registered_rows(rows, plan)
    observed_worlds = len({key[0] for key in valid})
    reordered = {("reordered", "cd"): -1, ("reordered", "dc"): 1}
    fresh = {("fresh", "cd"): -1, ("fresh", "dc"): 1}
    interaction = reordered | {arm: -sign for arm, sign in fresh.items()}
    return {
        "schema": "seatlab.visibility-dispatch-mix-summary/v1",
        "counts": {
            "registered_trials": len(plan),
            "valid_trials": len(valid),
            "registered_worlds": len(SEEDS),
            "observed_worlds": observed_worlds,
            "registered_distinct_shipments": len(SEEDS) * EPISODES,
            "observed_distinct_shipments": observed_worlds * EPISODES,
            "registered_shipment_observations": len(plan) * EPISODES,
            "valid_shipment_observations": len(valid) * EPISODES,
            "unregistered_rows": unregistered,
            "status": {
                status: sum(cell["status"] == status for cell in cells)
                for status in _STATUSES
            },
        },
        "primary": {
            "contrast": "reordered_dc_minus_cd",
            **_effect(valid, reordered, "pairs"),
        },
        "fresh": {"contrast": "fresh_dc_minus_cd", **_effect(valid, fresh, "pairs")},
        "interaction": {
            "contrast": "reordered_effect_minus_fresh_effect",
            **_effect(valid, interaction, "blocks"),
        },
        "arms": {
            condition: {
                pairing: _arm(valid, plan, condition, pairing) for pairing in _PAIRINGS
            }
            for condition in _CONDITIONS
        },
        "repeats": _repeats(valid),
        "cells": cells,
    }
