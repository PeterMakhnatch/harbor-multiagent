from collections import Counter
from hashlib import sha256
from statistics import fmean
from typing import Any

from ..attention.protocol import EPISODES
from ..turn_fork.packet import REPETITIONS, SEEDS, _digest, _object
from ..visibility.clarify_run import _encode
from . import inputs

_TRANSPORTS = ("direct", "commands")
_ARMS = ("aligned", "repeat")
_DRAWS = (0, 1)
_REGISTER = tuple(
    (f"s{seed}-r{rep}", sender, arm)
    for seed in SEEDS
    for rep in REPETITIONS
    for sender in _TRANSPORTS
    for arm in _ARMS
)
_DRAW_FIELDS = ("index", "source_id", "obs_id", "prompt_sha256", "draw")

type _Document = dict[str, Any]
type _Selections = dict[tuple[str, str, str], str | None]
type _Draw = tuple[str, str, int]
type _Outcomes = dict[_Draw, _Document | None]
type _WorldSlots = dict[int, list[_Draw | None]]


def _observations(board: _Document) -> dict[str, str]:
    rows = board["observations"]
    if not isinstance(rows, list) or len(rows) > len(_REGISTER):
        raise ValueError("invalid cross observation coverage")
    owners: dict[str, str] = {}
    previous = ("", "")
    for raw in rows:
        row = _object(raw, "source_id", "obs_id", "prompt_sha256", "opened")
        source_id, prompt = row["source_id"], _digest(row["prompt_sha256"])
        if not isinstance(source_id, str):
            raise ValueError("invalid cross observation source")
        key = (source_id, prompt)
        obs_id = f"{source_id}-{prompt[:12]}"
        if row["obs_id"] != obs_id or obs_id in owners or key <= previous:
            raise ValueError("invalid cross observation identity or order")
        owners[obs_id] = source_id
        previous = key
    return owners


def _selections(board: _Document, owners: dict[str, str]) -> _Selections:
    rows = board["selections"]
    if not isinstance(rows, list) or len(rows) != len(_REGISTER):
        raise ValueError("expected all registered cross selections")
    selections: _Selections = {}
    referenced: set[str] = set()
    for raw, key in zip(rows, _REGISTER, strict=True):
        row = _object(
            raw, "source_id", "sender", "arm", "obs_id", "sender_response_sha256"
        )
        if (row["source_id"], row["sender"], row["arm"]) != key:
            raise ValueError("cross selection identity or order differs from register")
        obs_id = row["obs_id"]
        if obs_id is None:
            if row["sender_response_sha256"] is not None:
                raise ValueError("missing cross selection has a response digest")
        else:
            if not isinstance(obs_id, str) or owners.get(obs_id) != key[0]:
                raise ValueError("cross selection refers to an unknown observation")
            _digest(row["sender_response_sha256"])
            referenced.add(obs_id)
        selections[key] = obs_id
    if referenced != owners.keys():
        raise ValueError("cross board contains unreferenced observations")
    return selections


def _report_outcomes(
    report: _Document, digest: str, receiver: str, schedule: list[_Document]
) -> _Outcomes:
    _object(report, "schema", "board_sha256", "transport", "rows")
    if (
        report["schema"] != "seatlab.decision-cross-report/v1"
        or report["board_sha256"] != digest
        or report["transport"] != receiver
    ):
        raise ValueError("cross report schema, board digest, or transport mismatch")
    rows = report["rows"]
    if not isinstance(rows, list) or len(rows) != len(schedule):
        raise ValueError("cross report does not cover the scheduled draws")
    outcomes: _Outcomes = {}
    for raw, draw in zip(rows, schedule, strict=True):
        row = _object(raw, *_DRAW_FIELDS, "run", "state", "turn", "outcome")
        obs_id, index = draw["obs_id"], draw["draw"]
        key = (receiver, obs_id, index)
        if (
            key in outcomes
            or any(row[field] != draw[field] for field in _DRAW_FIELDS)
            or type(row["index"]) is not int
            or type(row["draw"]) is not int
            or row["run"] != inputs.run_id(digest, obs_id, index)
        ):
            raise ValueError("cross report draw identity or order differs from board")
        outcome = row["outcome"]
        if row["state"] == "complete":
            outcomes[key] = _object(
                outcome, "opened", "actions", "optimal", "feasible", "opened_sensitive"
            )
        elif row["state"] in ("unclaimed", "incomplete") and outcome is None:
            outcomes[key] = None
        else:
            raise ValueError("invalid cross outcome coverage")
    return outcomes


def _world_slots(selections: _Selections, sender: str, receiver: str) -> _WorldSlots:
    worlds: _WorldSlots = {}
    for seed in SEEDS:
        slots: list[_Draw | None] = []
        for rep in REPETITIONS:
            for arm in _ARMS:
                obs_id = selections[f"s{seed}-r{rep}", sender, arm]
                for draw in _DRAWS:
                    slots.append(None if obs_id is None else (receiver, obs_id, draw))
        worlds[seed] = slots
    return worlds


def _estimate(worlds: list[_Document], field: str) -> float | None:
    values = [world[field] for world in worlds if world[field] is not None]
    return fmean(values) if len(values) == len(worlds) else None


def _cell_world(seed: int, slots: list[_Draw | None], outcomes: _Outcomes) -> _Document:
    selected = completed = optimal = 0
    for key in slots:
        if key is not None:
            selected += 1
            outcome = outcomes[key]
            if outcome is not None:
                completed += 1
                optimal += outcome["optimal"]
    actions = completed * EPISODES
    return {
        "seed": seed,
        "eligible_slots": len(slots),
        "selected_slots": selected,
        "completed_slots": completed,
        "weighted_optimal_actions": optimal,
        "weighted_scored_actions": actions,
        "optimal_fraction": optimal / actions if actions else None,
    }


def _cell(world_slots: _WorldSlots, outcomes: _Outcomes) -> _Document:
    worlds = [_cell_world(seed, slots, outcomes) for seed, slots in world_slots.items()]
    return {
        **{
            field: sum(world[field] for world in worlds)
            for field in (
                "eligible_slots",
                "selected_slots",
                "completed_slots",
                "weighted_optimal_actions",
                "weighted_scored_actions",
            )
        },
        "equal_world_optimal_fraction": _estimate(worlds, "optimal_fraction"),
        "worlds": worlds,
    }


def _contrast_world(
    seed: int, left: list[_Draw | None], right: list[_Draw | None], outcomes: _Outcomes
) -> _Document:
    matched = observed_sum = same_draw = same_draw_unobserved = 0
    fixed_sum = unknown_left = unknown_right = 0
    missing_weights: Counter[_Draw] = Counter()
    for a, b in zip(left, right, strict=True):
        x = outcomes[a] if a is not None else None
        y = outcomes[b] if b is not None else None
        if x is not None and y is not None:
            matched += 1
            observed_sum += x["optimal"] - y["optimal"]
        if a is not None and a == b:
            same_draw += 1
            same_draw_unobserved += x is None
            continue
        for key, outcome, weight in ((a, x, 1), (b, y, -1)):
            if outcome is not None:
                fixed_sum += weight * outcome["optimal"]
            elif key is not None:
                missing_weights[key] += weight
            elif weight > 0:
                unknown_left += 1
            else:
                unknown_right += 1
    lower = fixed_sum + EPISODES * (
        sum(min(0, weight) for weight in missing_weights.values()) - unknown_right
    )
    upper = fixed_sum + EPISODES * (
        sum(max(0, weight) for weight in missing_weights.values()) + unknown_left
    )
    actions = len(left) * EPISODES
    return {
        "seed": seed,
        "eligible_pairs": len(left),
        "observed_matched_pairs": matched,
        "same_draw_pairs": same_draw,
        "structurally_known_unobserved_pairs": same_draw_unobserved,
        "observed_optimal_fraction_difference": observed_sum / (matched * EPISODES)
        if matched
        else None,
        "registered_bounds": {"lower": lower / actions, "upper": upper / actions},
    }


def _contrast(left: _WorldSlots, right: _WorldSlots, outcomes: _Outcomes) -> _Document:
    worlds = [
        _contrast_world(seed, left[seed], right[seed], outcomes) for seed in SEEDS
    ]
    return {
        "direction": "direct_minus_commands",
        **{
            field: sum(world[field] for world in worlds)
            for field in (
                "eligible_pairs",
                "observed_matched_pairs",
                "same_draw_pairs",
                "structurally_known_unobserved_pairs",
            )
        },
        "equal_world_observed_optimal_fraction_difference": _estimate(
            worlds, "observed_optimal_fraction_difference"
        ),
        "registered_bounds": {
            side: fmean(world["registered_bounds"][side] for world in worlds)
            for side in ("lower", "upper")
        },
        "worlds": worlds,
    }


def _variation(
    receiver: str, observations: dict[str, str], outcomes: _Outcomes
) -> _Document:
    matched = identical = 0
    agreements = [0] * EPISODES
    differences: Counter[int] = Counter()
    for obs_id in observations:
        first, second = outcomes[receiver, obs_id, 0], outcomes[receiver, obs_id, 1]
        if first is None or second is None:
            continue
        matched += 1
        pair_actions = zip(first["actions"], second["actions"], strict=True)
        pair_agreements = 0
        for slot, (a, b) in enumerate(pair_actions):
            same = a["choice"] == b["choice"]
            agreements[slot] += same
            pair_agreements += same
        identical += pair_agreements == EPISODES
        differences[second["optimal"] - first["optimal"]] += 1
    return {
        "unique_observations": len(observations),
        "matched_unique_observations": matched,
        "identical_action_vectors": identical,
        "action_slots": matched * EPISODES,
        "action_agreements": sum(agreements),
        "action_agreements_by_slot": agreements,
        "optimal_actions_draw_1_minus_draw_0_counts": {
            str(delta): count for delta, count in sorted(differences.items())
        },
    }


def summarize(
    board: _Document, direct_report: _Document, command_report: _Document
) -> _Document:
    _object(
        board,
        "schema",
        "packet_sha256",
        "direct_report_sha256",
        "commands_report_sha256",
        "observations",
        "selections",
    )
    if board["schema"] != "seatlab.decision-cross-board/v1":
        raise ValueError("cross board schema mismatch")
    observations = _observations(board)
    selections = _selections(board, observations)
    digest = sha256(_encode(board)).hexdigest()
    schedule = inputs.draws(board)
    outcomes = _report_outcomes(direct_report, digest, "direct", schedule)
    outcomes.update(_report_outcomes(command_report, digest, "commands", schedule))
    slots = {
        (sender, receiver): _world_slots(selections, sender, receiver)
        for sender in _TRANSPORTS
        for receiver in _TRANSPORTS
    }
    selected = sum(obs_id is not None for obs_id in selections.values())
    unshared = selected * len(_DRAWS)
    saved = unshared - len(schedule)
    return {
        "schema": "seatlab.decision-cross-summary/v1",
        "board_sha256": digest,
        "measurement": "conditional_crossed_decisions_not_native_rewards",
        "cells": {
            sender: {
                receiver: _cell(slots[sender, receiver], outcomes)
                for receiver in _TRANSPORTS
            }
            for sender in _TRANSPORTS
        },
        "receiver_effects": {
            sender: _contrast(
                slots[sender, "direct"], slots[sender, "commands"], outcomes
            )
            for sender in _TRANSPORTS
        },
        "sender_effects": {
            receiver: _contrast(
                slots["direct", receiver], slots["commands", receiver], outcomes
            )
            for receiver in _TRANSPORTS
        },
        "receiver_draw_agreement": {
            receiver: _variation(receiver, observations, outcomes)
            for receiver in _TRANSPORTS
        },
        "compression": {
            "registered_selection_occurrences": len(selections),
            "nonmissing_selection_occurrences": selected,
            "unique_observations": len(observations),
            "unshared_call_slots_per_receiver": unshared,
            "shared_call_slots_per_receiver": len(schedule),
            "saved_call_slots_per_receiver": saved,
            "saved_call_slots_across_receivers": saved * len(_TRANSPORTS),
        },
    }
