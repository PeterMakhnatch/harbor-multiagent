import json
from collections import Counter
from statistics import fmean
from typing import Any

from ..attention.protocol import EPISODES, aliases
from ..turn_fork.packet import REPETITIONS, SEEDS, _digest, _object
from . import protocol

_ARMS = ("solo-r1", "draft-r1", "solo-r2", "draft-r2")
_STATES = ("unavailable", "unclaimed", "incomplete", "complete")
_SCOPES = {"target": 1, "all_shipments": EPISODES, "other_shipments": EPISODES - 1}
_EFFECTS = {
    "revision_solo": {"solo-r1": -1, "solo-r2": 1},
    "revision_draft": {"draft-r1": -1, "draft-r2": 1},
    "draft_r1": {"solo-r1": -1, "draft-r1": 1},
    "draft_r2": {"solo-r2": -1, "draft-r2": 1},
    "interaction": {"solo-r1": 1, "draft-r1": -1, "solo-r2": -1, "draft-r2": 1},
}

type _Document = dict[str, Any]
type _Outcomes = dict[tuple[str, str], _Document]
type _Weights = dict[str, int]


def _action(raw: object) -> _Document:
    action = _object(raw, "id", "choice", "optimal", "feasible")
    if (
        type(action["id"]) is not int
        or type(action["choice"]) is not str
        or type(action["optimal"]) is not bool
        or type(action["feasible"]) is not bool
    ):
        raise ValueError("invalid peer-update action types")
    return action


def _check_grade(
    packet: _Document,
    board: _Document,
    row: _Document,
    mapping: dict[str, int],
    raw: object,
) -> _Document:
    outcome = _object(raw, "actions", "target")
    actions = outcome["actions"]
    if not isinstance(actions, list) or len(actions) != EPISODES:
        raise ValueError("expected six stock actions")
    choices = {}
    for raw_action in actions:
        action = _action(raw_action)
        choices[action["id"]] = action["choice"]
    if choices.keys() != set(mapping.values()):
        raise ValueError("expected one action per shipment")
    _action(outcome["target"])
    answer = json.dumps(
        {"choices": {alias: choices[case_id] for alias, case_id in mapping.items()}}
    )
    if outcome != protocol.grade(packet, board, row, answer):
        raise ValueError("peer-update outcome differs from stock grade or target")
    return outcome


def _report_rows(
    report: _Document, panel: _Document, panel_sha256: str
) -> list[_Document]:
    checked = _object(report, "schema", "panel_sha256", "rows")
    rows = checked["rows"]
    if (
        checked["schema"] != "seatlab.peer-update-report/v1"
        or checked["panel_sha256"] != panel_sha256
        or not isinstance(rows, list)
        or len(rows) != len(panel["rows"])
    ):
        raise ValueError("peer-update report does not match the panel")
    for raw, planned in zip(rows, panel["rows"], strict=True):
        row = _object(raw, *planned.keys(), "state", "turn", "outcome")
        if type(row["index"]) is not int or any(
            row[k] != v for k, v in planned.items()
        ):
            raise ValueError("peer-update row differs from the panel")
        prompt = planned["prompt_sha256"]
        state, turn, outcome = row["state"], row["turn"], row["outcome"]
        if prompt is None:
            if state != "unavailable" or turn is not None or outcome is not None:
                raise ValueError("peer-update evidence for an unavailable source")
        elif state == "unclaimed":
            if turn is not None or outcome is not None:
                raise ValueError("peer-update evidence for an unclaimed row")
        elif state in ("complete", "incomplete"):
            if turn is not None:
                checked_turn = _object(turn, "prompt_sha256", "response_sha256")
                _digest(checked_turn["response_sha256"])
                if checked_turn["prompt_sha256"] != prompt:
                    raise ValueError(
                        "peer-update turn does not match the committed prompt"
                    )
            if state == "complete" and (turn is None or outcome is None):
                raise ValueError("missing evidence for a complete peer-update row")
            if state == "incomplete" and outcome is not None:
                raise ValueError("invalid grade for an incomplete peer-update row")
        else:
            raise ValueError("invalid peer-update state")
    return rows


def _counts(outcome: _Document) -> tuple[int, int, int]:
    target = int(outcome["target"]["optimal"])
    total = sum(action["optimal"] for action in outcome["actions"])
    return target, total, total - target


def _scopes(
    optimal: dict[str, list[int]], matched: int, weights: _Weights
) -> _Document:
    scopes = {}
    for index, (scope, shipments) in enumerate(_SCOPES.items()):
        by_arm = {arm: counts[index] for arm, counts in optimal.items()}
        numerator = sum(by_arm[arm] * weight for arm, weight in weights.items())
        denominator = matched * shipments
        scopes[scope] = {
            "optimal_by_arm": by_arm,
            "numerator": numerator,
            "denominator": denominator,
            "value": numerator / denominator if denominator else None,
        }
    return scopes


def _estimate(outcomes: _Outcomes, weights: _Weights) -> _Document:
    worlds: list[_Document] = []
    registered = len(SEEDS) * len(REPETITIONS)
    totals = {arm: [0] * len(_SCOPES) for arm in weights}
    for seed in SEEDS:
        matched = 0
        optimal = {arm: [0] * len(_SCOPES) for arm in weights}
        for rep in REPETITIONS:
            source = f"s{seed}-r{rep}"
            if not all((source, arm) in outcomes for arm in weights):
                continue
            matched += 1
            for arm in weights:
                for index, count in enumerate(_counts(outcomes[source, arm])):
                    optimal[arm][index] += count
                    totals[arm][index] += count
        worlds.append(
            {
                "seed": seed,
                "matched_sources": matched,
                "missing_sources": len(REPETITIONS) - matched,
                "scopes": _scopes(optimal, matched, weights),
            }
        )
    represented = [world for world in worlds if world["matched_sources"]]
    matched = sum(world["matched_sources"] for world in represented)
    scopes = _scopes(totals, matched, weights)
    for scope, scores in scopes.items():
        mean = (
            fmean(world["scopes"][scope]["value"] for world in represented)
            if represented
            else None
        )
        scores["pooled_value"] = scores.pop("value")
        scores["observed_equal_world_mean"] = mean
        scores["full_world_mean"] = mean if matched == registered else None
    return {
        "weights": weights.copy(),
        "matched_sources": matched,
        "missing_sources": registered - matched,
        "represented_worlds": len(represented),
        "registered_worlds": len(SEEDS),
        "scopes": scopes,
        "worlds": worlds,
    }


def _transitions(
    pairs: list[tuple[_Document, _Document]], registered: int
) -> _Document:
    labels = ("both_wrong", "wrong_to_right", "right_to_wrong", "unchanged_right")
    counts = dict.fromkeys(labels, 0)
    same_choice = 0
    for before, after in pairs:
        counts[labels[2 * before["optimal"] + after["optimal"]]] += 1
        same_choice += before["choice"] == after["choice"]
    return {
        "matched_sources": len(pairs),
        "missing_sources": registered - len(pairs),
        **counts,
        "same_choice": same_choice,
    }


def _draft_targets(
    panel: _Document,
    direct_report: _Document,
    outcomes: _Outcomes,
    mappings: dict[str, dict[str, int]],
) -> _Document:
    peer_rows = {row["run"]: row for row in direct_report["rows"]}
    comparisons = {"peer_to_draft_r1": [], "peer_to_draft_r2": [], "draft_r1_to_r2": []}
    for slot in panel["sources"]:
        if not slot["available"]:
            continue
        source = slot["source_id"]
        target_id = mappings[source][slot["target"]]
        actions = peer_rows[slot["peer_run"]]["outcome"]["actions"]
        peer = next(action for action in actions if action["id"] == target_id)
        first = outcomes.get((source, "draft-r1"))
        second = outcomes.get((source, "draft-r2"))
        if first is not None:
            comparisons["peer_to_draft_r1"].append((peer, first["target"]))
        if second is not None:
            comparisons["peer_to_draft_r2"].append((peer, second["target"]))
        if first is not None and second is not None:
            comparisons["draft_r1_to_r2"].append((first["target"], second["target"]))
    return {
        key: _transitions(pairs, len(panel["sources"]))
        for key, pairs in comparisons.items()
    }


def _target_changes(
    panel: _Document, sources: dict[str, _Document], mappings: dict[str, dict[str, int]]
) -> _Document:
    changed = unchanged = 0
    for slot in panel["sources"]:
        source = slot["source_id"]
        target_id = mappings[source][slot["target"]]
        cases = sources[source]["cases"]
        case = next(case for case in cases if case["id"] == target_id)
        previous, current = case["reports"]
        rank = case["utilities"].__getitem__
        if max(previous["available"], key=rank) == max(current["available"], key=rank):
            unchanged += 1
        else:
            changed += 1
    return {
        "changed": changed,
        "unchanged": unchanged,
        "compared_sources": changed + unchanged,
    }


def _coverage(panel: _Document, rows: list[_Document]) -> _Document:
    states = Counter(row["state"] for row in rows)
    by_arm = Counter((row["arm"], row["state"]) for row in rows)
    return {
        "registered_sources": len(panel["sources"]),
        "available_sources": sum(slot["available"] for slot in panel["sources"]),
        "registered_worlds": len(SEEDS),
        "scheduled_rows": len(rows),
        "claimed_first_attempts": states["complete"] + states["incomplete"],
        "recorded_replies": sum(row["turn"] is not None for row in rows),
        "missing_rows": len(rows) - states["complete"],
        "states": {state: states[state] for state in _STATES},
        "states_by_arm": {
            arm: {state: by_arm[arm, state] for state in _STATES} for arm in _ARMS
        },
    }


def summarize(
    packet: _Document,
    board: _Document,
    panel: _Document,
    direct_report: _Document,
    report: _Document,
) -> _Document:
    expected = protocol.build(
        packet, board, protocol.panel_digest(board), direct_report
    )
    panel_sha256 = protocol.panel_digest(expected)
    if protocol.panel_digest(panel) != panel_sha256:
        raise ValueError("peer-update panel differs from reconstructed provenance")
    rows = _report_rows(report, expected, panel_sha256)
    sources = {source["source_id"]: source for source in packet["sources"]}
    mappings = {
        key: aliases(source["cases"], source["seed"], source["repetition"])
        for key, source in sources.items()
    }
    outcomes = {
        (row["source_id"], row["arm"]): _check_grade(
            packet, board, planned, mappings[row["source_id"]], row["outcome"]
        )
        for row, planned in zip(rows, expected["rows"], strict=True)
        if row["state"] == "complete"
    }
    return {
        "schema": "seatlab.peer-update-summary/v1",
        "measurement": "conditional_stock_optimality_not_new_native_rewards",
        "panel_sha256": panel_sha256,
        "coverage": _coverage(expected, rows),
        "arms": {arm: _estimate(outcomes, {arm: 1}) for arm in _ARMS},
        "effects": {
            key: _estimate(outcomes, weights) for key, weights in _EFFECTS.items()
        },
        "target_best_reported_bay": _target_changes(expected, sources, mappings),
        "draft_target_transitions": _draft_targets(
            expected, direct_report, outcomes, mappings
        ),
    }
