import re
from collections.abc import Sequence
from hashlib import sha256
from statistics import fmean
from typing import Any, cast

from ..attention.protocol import EPISODES, _open_pair, aliases
from ..briefing.protocol import REPETITIONS, SEEDS
from ..visibility.route_run import Answer, _validate_answer
from . import packet as packet_format
from . import protocol

_ROW_FIELDS = {"source_id", "seed", "repetition", "arm", "state", "outcome", "turns"}
_TURN_FIELDS = {
    "stage",
    "role",
    "run",
    "prompt_sha256",
    "response_sha256",
    "usage",
    "status",
}
_USAGE_FIELDS = {"served_model", "input_tokens", "cache_tokens", "output_tokens"}
_OUTCOME_FIELDS = {"opened", "actions", "optimal", "feasible", "opened_sensitive"}
_ACTION_FIELDS = {"id", "choice", "optimal", "feasible"}
_STAGES = (("push", "inventory"), ("decide", "dispatch"))
_ARMS = ("aligned", "shifted", "repeat")
_STATES = ("no_source", "unclaimed", "incomplete", "complete")
_BAYS = ("A", "B", "C", "D")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SLOTS = tuple((seed, rep) for seed in SEEDS for rep in REPETITIONS)
_REGISTER = tuple(
    (seed, rep, arm) for seed, rep in _SLOTS for arm in protocol.arms(seed, rep)
)


type _Document = dict[str, Any]
type _Cell = tuple[int, int, str]
type _Outcomes = dict[_Cell, _Document | None]


def _is_digest(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _prompt_hash(prompt: str) -> str:
    return sha256(prompt.encode("utf-8")).hexdigest()


def _source_index(packet: _Document) -> dict[str, _Document]:
    sources: dict[str, _Document] = {}
    worlds: dict[int, str] = {}
    trials: dict[str, tuple[int, str]] = {}
    for source in packet["sources"]:
        seed, rep = source["seed"], source["repetition"]
        source_id, world = source["source_id"], source["world_sha256"]
        if (
            type(seed) is not int
            or type(rep) is not int
            or (seed, rep) not in _SLOTS
            or source_id != f"s{seed}-r{rep}"
            or source_id in sources
        ):
            raise ValueError("invalid registered source identity")
        if worlds.setdefault(seed, world) != world or trials.setdefault(
            source["trial_id"], (seed, world)
        ) != (seed, world):
            raise ValueError("source trial or world differs from its registered world")
        sources[source_id] = source
    return sources


def _validate_usage(usage: object) -> None:
    if usage is None:
        return
    if not isinstance(usage, dict) or usage.keys() != _USAGE_FIELDS:
        raise ValueError("invalid turn usage")
    try:
        _, validated = _validate_answer(Answer(text="", **cast(_Document, usage)))
    except ValueError:
        raise ValueError("invalid turn usage") from None
    if validated is None:
        raise ValueError("invalid turn usage")


def _validate_turns(digest: str, row: _Document, source: _Document | None) -> None:
    state, turns = row["state"], row["turns"]
    if not isinstance(turns, list) or len(turns) > len(_STAGES):
        raise ValueError("invalid turn coverage")
    if state in ("no_source", "unclaimed") and turns:
        raise ValueError("unexpected turns in an unplayed cell")
    if state == "complete" and len(turns) != len(_STAGES):
        raise ValueError("missing turns in a complete cell")
    if not turns:
        return
    if source is None:
        raise ValueError("turns have no registered source")
    for index, raw in enumerate(turns):
        turn = packet_format._object(raw, *_TURN_FIELDS)
        stage, role = _STAGES[index]
        if (
            turn["stage"] != stage
            or turn["role"] != role
            or turn["run"]
            != protocol.run_id(digest, row["source_id"], row["arm"], stage)
            or not _is_digest(turn["prompt_sha256"])
        ):
            raise ValueError("invalid turn identity, usage field, or prompt digest")
        if turn["status"] == "received":
            if not _is_digest(turn["response_sha256"]):
                raise ValueError("invalid turn response digest")
            _validate_usage(turn["usage"])
        elif (
            turn["status"] != "callback_error"
            or state == "complete"
            or index + 1 != len(turns)
            or turn["response_sha256"] is not None
            or turn["usage"] is not None
        ):
            raise ValueError("invalid callback error receipt")
    push = protocol.push_prompt(source, row["arm"])
    if turns[0]["prompt_sha256"] != _prompt_hash(push):
        raise ValueError("push prompt digest does not match the source and arm")


def _sensitive_aliases(source: _Document, native_ids: dict[str, int]) -> frozenset[str]:
    sensitive = set()
    for alias, native_id in native_ids.items():
        case = source["cases"][native_id]
        previous, recent = case["reports"]
        ranks = case["utilities"].__getitem__
        if max(map(ranks, previous["available"])) != max(
            map(ranks, recent["available"])
        ):
            sensitive.add(alias)
    return frozenset(sensitive)


def _regrade(
    raw: object, source: _Document, sensitive: frozenset[str]
) -> tuple[str, str]:
    outcome = packet_format._object(raw, *_OUTCOME_FIELDS)
    opened, actions = outcome["opened"], outcome["actions"]
    if not isinstance(opened, list):
        raise ValueError("invalid opened aliases")
    first, second = _open_pair(opened)
    if not isinstance(actions, list) or len(actions) != EPISODES:
        raise ValueError("invalid action coverage")
    optimal = feasible = 0
    for case, record in zip(source["cases"], actions, strict=True):
        action = packet_format._object(record, *_ACTION_FIELDS)
        if (
            type(action["id"]) is not int
            or action["id"] != case["id"]
            or type(action["choice"]) is not str
            or action["choice"] not in _BAYS
        ):
            raise ValueError("invalid action identity or choice")
        available = case["after"]
        choice = action["choice"]
        is_feasible = choice in available
        is_optimal = choice == max(available, key=case["utilities"].__getitem__)
        if action["optimal"] is not is_optimal or action["feasible"] is not is_feasible:
            raise ValueError("action grades do not match the chosen bay")
        optimal += is_optimal
        feasible += is_feasible
    for key, expected in (
        ("optimal", optimal),
        ("feasible", feasible),
        ("opened_sensitive", len(sensitive.intersection(opened))),
    ):
        if type(outcome[key]) is not int or outcome[key] != expected:
            raise ValueError("outcome totals do not match independently graded choices")
    return first, second


def _outcomes(
    sources: dict[str, _Document],
    sensitive: dict[str, frozenset[str]],
    digest: str,
    rows: list[_Document],
) -> _Outcomes:
    if len(rows) != len(_REGISTER):
        raise ValueError("expected all 36 registered cells")
    outcomes: _Outcomes = {}
    for row, (seed, rep, arm) in zip(rows, _REGISTER, strict=True):
        source_id = f"s{seed}-r{rep}"
        if (
            not isinstance(row, dict)
            or row.keys() != _ROW_FIELDS
            or row["source_id"] != source_id
            or type(row["seed"]) is not int
            or row["seed"] != seed
            or type(row["repetition"]) is not int
            or row["repetition"] != rep
            or row["arm"] != arm
        ):
            raise ValueError("cell identity or registered order mismatch")
        state = row["state"]
        source = sources.get(source_id)
        if state not in _STATES or (state == "no_source") != (source is None):
            raise ValueError("cell state does not match source coverage")
        _validate_turns(digest, row, source)
        outcome = row["outcome"]
        if state == "complete" and source is not None:
            opened = _regrade(outcome, source, sensitive[source_id])
            decide = protocol.decide_prompt(source, opened)
            if row["turns"][1]["prompt_sha256"] != _prompt_hash(decide):
                raise ValueError(
                    "decide prompt digest does not match the opened aliases"
                )
        elif outcome is not None:
            raise ValueError("unexpected outcome in a cell that is not complete")
        outcomes[seed, rep, arm] = outcome
    return outcomes


def _score_range(outcome: _Document | None) -> tuple[int, int]:
    if outcome is None:
        return 0, EPISODES
    return outcome["optimal"], outcome["optimal"]


def _world_contrast(outcomes: _Outcomes, seed: int, control: str) -> _Document:
    deltas: list[int] = []
    lower = upper = 0
    for rep in REPETITIONS:
        aligned, other = outcomes[seed, rep, "aligned"], outcomes[seed, rep, control]
        if aligned is not None and other is not None:
            deltas.append(aligned["optimal"] - other["optimal"])
        a_low, a_high = _score_range(aligned)
        b_low, b_high = _score_range(other)
        lower += a_low - b_high
        upper += a_high - b_low
    return {
        "seed": seed,
        "matched_pairs": len(deltas),
        "mean": fmean(deltas) / EPISODES if deltas else None,
        "registered_bounds": {
            "lower": lower / (len(REPETITIONS) * EPISODES),
            "upper": upper / (len(REPETITIONS) * EPISODES),
        },
    }


def _contrast(outcomes: _Outcomes, control: str) -> _Document:
    worlds = [_world_contrast(outcomes, seed, control) for seed in SEEDS]
    paired = [world for world in worlds if world["matched_pairs"]]
    return {
        "contrast": f"aligned_minus_{control}_optimal_fraction",
        "estimate": fmean(world["mean"] for world in paired)
        if len(paired) == len(worlds)
        else None,
        "matched_pairs": sum(world["matched_pairs"] for world in paired),
        "registered_pairs": len(_SLOTS),
        "registered_worlds": len(worlds),
        "paired_worlds": len(paired),
        "worlds": worlds,
        "registered_bounds": {
            side: fmean(world["registered_bounds"][side] for world in worlds)
            for side in ("lower", "upper")
        },
    }


def _agreement(outcomes: _Outcomes, control: str) -> _Document:
    pairs = opened_agree = actions_agree = 0
    for seed, rep in _SLOTS:
        aligned, other = outcomes[seed, rep, "aligned"], outcomes[seed, rep, control]
        if aligned is None or other is None:
            continue
        pairs += 1
        opened_agree += set(aligned["opened"]) == set(other["opened"])
        actions_agree += sum(
            left["choice"] == right["choice"]
            for left, right in zip(aligned["actions"], other["actions"], strict=True)
        )
    return {
        "matched_pairs": pairs,
        "opened_set_agreements": opened_agree,
        "action_slots": pairs * EPISODES,
        "action_agreements": actions_agree,
    }


def _targeting(
    outcomes: _Outcomes, sensitive: dict[str, frozenset[str]], arm: str
) -> _Document:
    complete = opened_sensitive = sensitive_cases = max_openable = 0
    for seed, rep in _SLOTS:
        outcome = outcomes[seed, rep, arm]
        if outcome is not None:
            complete += 1
            opened_sensitive += outcome["opened_sensitive"]
            count = len(sensitive[f"s{seed}-r{rep}"])
            sensitive_cases += count
            max_openable += min(2, count)
    return {
        "complete_cells": complete,
        "case_slots": complete * EPISODES,
        "sensitive_cases": sensitive_cases,
        "opened_slots": 2 * complete,
        "opened_sensitive": opened_sensitive,
        "max_sensitive_openable": max_openable,
    }


def _card_rotation(sources: dict[str, _Document]) -> _Document:
    identical = unchanged = 0
    for source in sources.values():
        aligned = protocol.card_for(source, "aligned")
        shifted = protocol.card_for(source, "shifted")
        unchanged += aligned == shifted
        identical += sum(a == b for a, b in zip(aligned, shifted, strict=True))
    return {
        "source_cards": len(sources),
        "card_slots": len(sources) * EPISODES,
        "identical_slots": identical,
        "unchanged_cards": unchanged,
    }


def _meter(rows: Sequence[_Document], index: int) -> _Document:
    absent = callback_errors = unmetered = metered = 0
    tokens = dict.fromkeys(("input_tokens", "cache_tokens", "output_tokens"), 0)
    for row in rows:
        turns = row["turns"]
        if index >= len(turns):
            absent += 1
            continue
        turn = turns[index]
        if turn["status"] == "callback_error":
            callback_errors += 1
        elif turn["usage"] is None:
            unmetered += 1
        else:
            metered += 1
            for key in tokens:
                tokens[key] += turn["usage"][key]
    return {
        "absent_registered_turns": absent,
        "callback_errors": callback_errors,
        "received_unmetered": unmetered,
        "received_metered": metered,
        "reported_tokens": tokens if metered else None,
    }


def summarize(packet: _Document, digest: str, rows: list[_Document]) -> _Document:
    if not _is_digest(digest):
        raise ValueError("invalid packet digest")
    sources = _source_index(packet)
    sensitive = {
        source_id: _sensitive_aliases(
            source, aliases(source["cases"], source["seed"], source["repetition"])
        )
        for source_id, source in sources.items()
    }
    outcomes = _outcomes(sources, sensitive, digest, rows)
    counts = dict.fromkeys(_STATES, 0)
    for row in rows:
        counts[row["state"]] += 1
    return {
        "measurement": "conditional_continuations_not_native_rewards",
        "registered_cells": len(_REGISTER),
        "registered_sources": len(_SLOTS),
        "available_sources": len(sources),
        "cell_states": counts,
        "primary": _contrast(outcomes, "shifted"),
        "negative_control": _contrast(outcomes, "repeat"),
        "secondary": {
            "card_rotation": _card_rotation(sources),
            "targeting": {arm: _targeting(outcomes, sensitive, arm) for arm in _ARMS},
            "aligned_vs_shifted": _agreement(outcomes, "shifted"),
            "aligned_vs_repeat": _agreement(outcomes, "repeat"),
        },
        "meter": {
            stage: _meter(rows, index) for index, (stage, _) in enumerate(_STAGES)
        },
    }
