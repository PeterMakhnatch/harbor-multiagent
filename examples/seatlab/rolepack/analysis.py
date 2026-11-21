import json
from collections import Counter
from statistics import fmean
from typing import Any

from . import evidence, protocol

_ARMS = ("original", "peer")
_DRAWS = (0, 1)
_STATES = ("unavailable", "unclaimed", "incomplete", "complete")
_RELATIONS = (
    "same_correct",
    "same_wrong",
    "corrected",
    "harmed",
    "wrong_different",
    "right_different",
)

type _Document = dict[str, Any]
type _Rows = dict[tuple[str, str, int], _Document]


def _relation(same: bool, prior_correct: bool, correct: bool) -> str:
    if same:
        return "same_correct" if prior_correct else "same_wrong"
    if correct:
        return "right_different" if prior_correct else "corrected"
    return "harmed" if prior_correct else "wrong_different"


def _state_counts(rows: _Rows, records: list[_Document], arm: str) -> dict[str, int]:
    counts = Counter(rows[record["id"], arm, draw]["state"] for record in records for draw in _DRAWS)
    return {state: counts[state] for state in _STATES}


def _arm(
    rows: _Rows, records: list[_Document], arm: str, priors: dict[str, _Document]
) -> _Document:
    optimal = actions = 0
    comparable_sources = agreeing_proposals = comparable_actions = agreeing_actions = 0
    compared_proposals = copied_proposals = 0
    relations: Counter[str] = Counter()
    for record in records:
        source = record["id"]
        first, second = (rows[source, arm, draw]["outcome"] for draw in _DRAWS)
        if first is not None and second is not None:
            comparable_sources += 1
            agreeing_proposals += first["values"] == second["values"]
            comparable_actions += len(first["values"])
            agreeing_actions += sum(
                a == b for a, b in zip(first["values"], second["values"], strict=True)
            )
        for outcome in (first, second):
            if outcome is None:
                continue
            prior = priors[source]
            actions += len(outcome["values"])
            optimal += sum(outcome["correct"])
            compared_proposals += 1
            copied_proposals += outcome["values"] == prior["values"]
            for old, new, was_correct, is_correct in zip(
                prior["values"], outcome["values"], prior["correct"], outcome["correct"], strict=True
            ):
                relations[_relation(old == new, was_correct, is_correct)] += 1
    copied = relations["same_correct"] + relations["same_wrong"]
    return {
        "states": _state_counts(rows, records, arm),
        "scored_actions": actions,
        "optimal_actions": optimal,
        "observed_pooled_action_rate": optimal / actions if actions else None,
        "repeat": {
            "registered_sources": len(records),
            "comparable_sources": comparable_sources,
            "agreeing_proposals": agreeing_proposals,
            "comparable_actions": comparable_actions,
            "agreeing_actions": agreeing_actions,
        },
        "vs_prior": {
            "compared_proposals": compared_proposals,
            "copied_proposals": copied_proposals,
            "compared_actions": actions,
            "prior_wrong_actions": relations["same_wrong"] + relations["wrong_different"] + relations["corrected"],
            "copied": copied,
            "changed": actions - copied,
            **{relation: relations[relation] for relation in _RELATIONS},
        },
    }


def _world(rows: _Rows, records: list[_Document]) -> _Document:
    matched_draws = []
    matched_sources = set()
    matched_actions = 0
    optimal_actions = dict.fromkeys(_ARMS, 0)
    rates: dict[str, list[float]] = {arm: [] for arm in _ARMS}
    for record in records:
        source = record["id"]
        n = len(record["rubric"]["items"])
        for draw in _DRAWS:
            original = rows[source, "original", draw]["outcome"]
            peer = rows[source, "peer", draw]["outcome"]
            if original is None or peer is None:
                continue
            matched_draws.append({"record_id": source, "draw": draw})
            matched_sources.add(source)
            matched_actions += n
            for arm, outcome in (("original", original), ("peer", peer)):
                correct = sum(outcome["correct"])
                optimal_actions[arm] += correct
                rates[arm].append(correct / n)
    means = {arm: fmean(values) if values else None for arm, values in rates.items()}
    original_rate, peer_rate = means["original"], means["peer"]
    return {
        "registered_sources": len(records),
        "available_sources": sum(record["peer"] is not None for record in records),
        "registered_source_draws": len(records) * len(_DRAWS),
        "matched_sources": len(matched_sources),
        "matched_source_draws": len(matched_draws),
        "matched_draws": matched_draws,
        "matched_actions_per_arm": matched_actions,
        "matched_optimal_actions_by_arm": optimal_actions,
        "mean_matched_draw_optimal_rate_by_arm": means,
        "peer_minus_original": (
            peer_rate - original_rate
            if original_rate is not None and peer_rate is not None
            else None
        ),
        "states_by_arm": {arm: _state_counts(rows, records, arm) for arm in _ARMS},
    }


def _task(rows: _Rows, records: list[_Document]) -> _Document:
    by_world: dict[str, list[_Document]] = {}
    priors = {}
    for record in records:
        by_world.setdefault(record["world"], []).append(record)
        prior = record["peer"]
        if prior is not None:
            priors[record["id"]] = protocol.grade(record, json.dumps(prior["answer"]))
    worlds = {world: _world(rows, group) for world, group in sorted(by_world.items())}
    represented = [stats for stats in worlds.values() if stats["matched_source_draws"]]
    effect = fmean(stats["peer_minus_original"] for stats in represented) if represented else None
    coverage = (
        "registered_sources",
        "available_sources",
        "registered_source_draws",
        "matched_sources",
        "matched_source_draws",
        "matched_actions_per_arm",
    )
    return {
        **{field: sum(stats[field] for stats in worlds.values()) for field in coverage},
        "registered_worlds": len(worlds),
        "represented_worlds": len(represented),
        "observed_equal_world_peer_minus_original": effect,
        "equal_world_peer_minus_original": effect if len(represented) == len(worlds) else None,
        "observed_equal_world_matched_optimal_rate_by_arm": {
            arm: fmean(stats["mean_matched_draw_optimal_rate_by_arm"][arm] for stats in represented)
            if represented else None
            for arm in _ARMS
        },
        "by_arm": {arm: _arm(rows, records, arm, priors) for arm in _ARMS},
        "by_world": worlds,
    }


def summarize(pack: _Document, report: _Document) -> _Document:
    evidence.verify(pack, report)
    rows = {(row["record_id"], row["arm"], row["draw"]): row for row in report["rows"]}
    tasks: dict[str, list[_Document]] = {}
    for record in sorted(pack["records"], key=lambda record: record["id"]):
        tasks.setdefault(record["task"], []).append(record)
    return {
        "schema": "seatlab.rolepack-summary/v1",
        "pack_sha256": report["pack_sha256"],
        "measurement": "matched_draw_optimal_action_fraction_equal_weight_per_world",
        "tasks": {task: _task(rows, records) for task, records in sorted(tasks.items())},
    }
