from collections import Counter, defaultdict
from collections.abc import Iterable
from statistics import fmean
from typing import Any

from . import evidence

type _Document = dict[str, Any]
type _Pair = tuple[_Document, _Document]
type _Sources = dict[str, list[_Pair]]

_STATES = ("unavailable", "unclaimed", "incomplete", "complete")
_SIDES = ("baseline", "candidate")
_DRAW_STATES = (
    "unavailable",
    "matched",
    "baseline_only",
    "candidate_only",
    "neither_complete",
)
_COVERAGE = (
    "registered_sources",
    "eligible_sources",
    "matched_sources",
    "registered_source_draws",
    "eligible_source_draws",
    *(f"{state}_source_draws" for state in _DRAW_STATES),
)
_TRANSITIONS = {
    (True, False): "right_to_wrong",
    (False, True): "wrong_to_right",
    (True, True): "both_right",
    (False, False): "both_wrong",
}


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _interval(outcome: _Document | None) -> tuple[float, float]:
    if outcome is None:
        return 0.0, 1.0
    rate = sum(outcome["correct"]) / len(outcome["correct"])
    return rate, rate


def _world(sources: _Sources) -> _Document:
    observed: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    coverage: Counter[str] = Counter()
    for pairs in sources.values():
        source_observed: list[float] = []
        source_lower: list[float] = []
        source_upper: list[float] = []
        for baseline, candidate in pairs:
            if baseline["state"] == "unavailable":
                coverage["unavailable"] += 1
                continue
            old, new = baseline["outcome"], candidate["outcome"]
            old_low, old_high = _interval(old)
            new_low, new_high = _interval(new)
            source_lower.append(new_low - old_high)
            source_upper.append(new_high - old_low)
            if old is not None and new is not None:
                coverage["matched"] += 1
                source_observed.append(new_low - old_low)
            elif old is not None:
                coverage["baseline_only"] += 1
            elif new is not None:
                coverage["candidate_only"] += 1
            else:
                coverage["neither_complete"] += 1
        if source_observed:
            observed.append(fmean(source_observed))
        if source_lower:
            lower.append(fmean(source_lower))
            upper.append(fmean(source_upper))
    return {
        "registered_sources": len(sources),
        "eligible_sources": len(lower),
        "matched_sources": len(observed),
        "registered_source_draws": coverage.total(),
        "eligible_source_draws": coverage.total() - coverage["unavailable"],
        **{f"{key}_source_draws": coverage[key] for key in _DRAW_STATES},
        "observed_candidate_minus_baseline": _mean(observed),
        "candidate_minus_baseline_bounds": (
            [fmean(lower), fmean(upper)] if lower else None
        ),
    }


def _observations(pairs: Iterable[_Pair]) -> _Document:
    states: tuple[Counter[str], Counter[str]] = Counter(), Counter()
    scored, optimal, matched_optimal = [0, 0], [0, 0], [0, 0]
    matched_draws = []
    matched_actions = disagreements = agreeing_responses = agreeing_vectors = 0
    transitions: Counter[str] = Counter()
    for pair in pairs:
        baseline, candidate = pair
        for side, row in enumerate(pair):
            states[side][row["state"]] += 1
            outcome = row["outcome"]
            if outcome is not None:
                scored[side] += len(outcome["correct"])
                optimal[side] += sum(outcome["correct"])
        old, new = baseline["outcome"], candidate["outcome"]
        if old is None or new is None:
            continue
        matched_draws.append(
            {"record_id": baseline["record_id"], "draw": baseline["draw"]}
        )
        agreeing_responses += (
            baseline["turn"]["response_sha256"] == candidate["turn"]["response_sha256"]
        )
        agreeing_vectors += old["values"] == new["values"]
        matched_actions += len(old["values"])
        for a, b, a_right, b_right in zip(
            old["values"], new["values"], old["correct"], new["correct"], strict=True
        ):
            disagreements += a != b
            transitions[_TRANSITIONS[a_right, b_right]] += 1
            matched_optimal[0] += a_right
            matched_optimal[1] += b_right
    return {
        **{
            name: {
                "states": {state: states[side][state] for state in _STATES},
                "scored_actions": scored[side],
                "optimal_actions": optimal[side],
                "matched_optimal_actions": matched_optimal[side],
            }
            for side, name in enumerate(_SIDES)
        },
        "matched_draws": sorted(
            matched_draws, key=lambda draw: (draw["record_id"], draw["draw"])
        ),
        "matched_actions_per_side": matched_actions,
        "disagreements": disagreements,
        **{name: transitions[name] for name in _TRANSITIONS.values()},
        "whole_response_agreement": {
            "compared_responses": len(matched_draws),
            "exact_matches": agreeing_responses,
            "action_vector_matches": agreeing_vectors,
        },
    }


def _arm(world_sources: dict[str, _Sources]) -> _Document:
    worlds = {
        world: _world(sources) for world, sources in sorted(world_sources.items())
    }
    pairs = (
        pair
        for sources in world_sources.values()
        for draws in sources.values()
        for pair in draws
    )
    observed = [
        w["observed_candidate_minus_baseline"]
        for w in worlds.values()
        if w["matched_sources"]
    ]
    bounds = [
        w["candidate_minus_baseline_bounds"]
        for w in worlds.values()
        if w["eligible_sources"]
    ]
    return {
        **_observations(pairs),
        **{key: sum(w[key] for w in worlds.values()) for key in _COVERAGE},
        "registered_worlds": len(worlds),
        "eligible_worlds": len(bounds),
        "matched_worlds": len(observed),
        "observed_equal_world_candidate_minus_baseline": _mean(observed),
        "equal_world_candidate_minus_baseline_bounds": (
            [
                fmean(interval[0] for interval in bounds),
                fmean(interval[1] for interval in bounds),
            ]
            if bounds
            else None
        ),
        "by_world": worlds,
    }


def compare(pack: _Document, baseline: _Document, candidate: _Document) -> _Document:
    evidence.verify(pack, baseline)
    evidence.verify(pack, candidate)
    tasks: dict[str, dict[str, dict[str, _Sources]]] = defaultdict(dict)
    for old, new in zip(baseline["rows"], candidate["rows"], strict=True):
        worlds = tasks[old["task"]].setdefault(old["arm"], {})
        sources = worlds.setdefault(old["world"], {})
        sources.setdefault(old["record_id"], []).append((old, new))
    return {
        "schema": "seatlab.rolepack-comparison/v1",
        "pack_sha256": baseline["pack_sha256"],
        "measurement": (
            "descriptive observed first-attempt rubric contrast; "
            "not a causal model effect or team reward"
        ),
        "weighting": "equal draws within source, equal sources within world, equal worlds within task and arm",
        "tasks": {
            task: {
                "by_arm": {arm: _arm(worlds) for arm, worlds in sorted(arms.items())}
            }
            for task, arms in sorted(tasks.items())
        },
    }
