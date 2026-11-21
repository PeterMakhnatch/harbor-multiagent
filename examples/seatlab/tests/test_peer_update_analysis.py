import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import inputs
from examples.seatlab.peer_update import analysis, protocol
from examples.seatlab.turn_fork import protocol as stock

type _Document = dict[str, Any]
type _Checkpoint = tuple[_Document, _Document, str]


@pytest.fixture(scope="module")
def checkpoint() -> _Checkpoint:
    results = Path(__file__).resolve().parents[1] / "results"
    return inputs.build(
        results / "turn-fork-packet.json",
        (results / "turn-fork.sha256").read_text(encoding="ascii").strip(),
        results / "turn-fork-direct-live.json",
        results / "turn-fork-subscription-live.json",
    )


def _first_observations(board: _Document) -> dict[str, _Document]:
    selected = {}
    for obs in sorted(board["observations"], key=lambda row: row["prompt_sha256"]):
        selected.setdefault(obs["source_id"], obs)
    return selected


def _answer(source: _Document, target: str, right: bool, other_right: bool) -> str:
    mapping = aliases(source["cases"], source["seed"], source["repetition"])
    cases = {case["id"]: case for case in source["cases"]}
    choices = {}
    for alias, case_id in mapping.items():
        case = cases[case_id]
        worse, best = sorted(case["after"], key=case["utilities"].__getitem__)
        choices[alias] = best if (right if alias == target else other_right) else worse
    return json.dumps({"choices": choices})


def _direct_report(
    checkpoint: _Checkpoint, completed: dict[tuple[str, int], bool]
) -> _Document:
    packet, board, digest = checkpoint
    sources = inputs.source_index(packet)
    observations = {obs["obs_id"]: obs for obs in board["observations"]}
    rows = []
    for draw in inputs.draws(board):
        obs_id = draw["obs_id"]
        key = obs_id, draw["draw"]
        row = dict(
            draw,
            run=inputs.run_id(digest, *key),
            state="unclaimed",
            turn=None,
            outcome=None,
        )
        if key in completed:
            source = sources[draw["source_id"]]
            opened = tuple(observations[obs_id]["opened"])
            mapping = aliases(source["cases"], source["seed"], source["repetition"])
            target = min(mapping.keys() - set(opened))
            answer = _answer(source, target, completed[key], other_right=False)
            row.update(
                state="complete",
                turn={
                    "status": "received",
                    "prompt_sha256": draw["prompt_sha256"],
                    "response_sha256": sha256(answer.encode()).hexdigest(),
                    "usage": {
                        "served_model": "deepseek-v4.1-flash-1017",
                        "input_tokens": 13,
                        "cache_tokens": 4,
                        "output_tokens": 2,
                    },
                },
                outcome=stock.grade(source, opened, answer),
            )
        rows.append(row)
    return {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": "direct",
        "rows": rows,
    }


def _report(panel: _Document) -> _Document:
    return {
        "schema": "seatlab.peer-update-report/v1",
        "panel_sha256": protocol.panel_digest(panel),
        "rows": [
            dict(
                row,
                state="unavailable" if row["prompt_sha256"] is None else "unclaimed",
                turn=None,
                outcome=None,
            )
            for row in panel["rows"]
        ],
    }


def _complete(packet: _Document, board: _Document, row: _Document, answer: str) -> None:
    planned = {
        key: value
        for key, value in row.items()
        if key not in ("state", "turn", "outcome")
    }
    row.update(
        state="complete",
        turn={
            "prompt_sha256": row["prompt_sha256"],
            "response_sha256": sha256(answer.encode()).hexdigest(),
        },
        outcome=protocol.grade(packet, board, planned, answer),
    )


def test_pairs_use_complete_repetitions_and_weight_worlds_equally(
    checkpoint: _Checkpoint,
) -> None:
    packet, board, board_digest = checkpoint
    first = _first_observations(board)
    direct = _direct_report(
        checkpoint,
        {
            (first[source]["obs_id"], 0): source == "s6501-r1"
            for source in ("s6501-r0", "s6501-r1", "s6502-r0", "s6502-r1")
        },
    )
    panel = protocol.build(packet, board, board_digest, direct)
    report = _report(panel)
    sources = inputs.source_index(packet)
    scores = {
        "s6501-r0": {
            "solo-r1": False,
            "draft-r1": False,
            "solo-r2": True,
            "draft-r2": True,
        },
        "s6501-r1": {
            "solo-r1": False,
            "draft-r1": True,
            "solo-r2": True,
            "draft-r2": False,
        },
        "s6502-r0": {"solo-r1": True, "draft-r1": False, "solo-r2": False},
    }
    for row in report["rows"]:
        source_id, arm = row["source_id"], row["arm"]
        if arm in scores.get(source_id, {}):
            answer = _answer(
                sources[source_id],
                row["target"],
                scores[source_id][arm],
                other_right=(source_id, arm) == ("s6501-r0", "solo-r2"),
            )
            _complete(packet, board, row, answer)
        elif (source_id, arm) == ("s6502-r0", "draft-r2"):
            row["state"] = "incomplete"

    summary = analysis.summarize(packet, board, panel, direct, report)
    coverage = summary["coverage"]
    assert coverage["states"] == {
        "complete": 11,
        "incomplete": 1,
        "unavailable": 32,
        "unclaimed": 4,
    }
    assert coverage["claimed_first_attempts"] == 12
    assert coverage["recorded_replies"] == 11
    assert coverage["missing_rows"] == 37
    solo = summary["effects"]["revision_solo"]
    assert (solo["matched_sources"], solo["represented_worlds"]) == (3, 2)
    target = solo["scopes"]["target"]
    assert target["optimal_by_arm"] == {"solo-r1": 1, "solo-r2": 2}
    assert (target["numerator"], target["denominator"]) == (1, 3)
    assert target["pooled_value"] == 1 / 3
    assert target["observed_equal_world_mean"] == 0
    assert target["full_world_mean"] is None
    other = solo["scopes"]["other_shipments"]
    all_shipments = solo["scopes"]["all_shipments"]
    assert (other["numerator"], other["denominator"]) == (5, 15)
    assert other["observed_equal_world_mean"] == 0.25
    assert (all_shipments["numerator"], all_shipments["denominator"]) == (6, 18)
    assert summary["effects"]["revision_draft"]["matched_sources"] == 2
    draft_r1_target = summary["effects"]["draft_r1"]["scopes"]["target"]
    assert draft_r1_target["observed_equal_world_mean"] == -0.25
    interaction = summary["effects"]["interaction"]
    assert interaction["matched_sources"] == 2
    assert interaction["scopes"]["target"]["observed_equal_world_mean"] == -1
    transitions = summary["draft_target_transitions"]
    assert transitions["peer_to_draft_r2"]["wrong_to_right"] == 1
    assert transitions["peer_to_draft_r2"]["right_to_wrong"] == 1
    assert transitions["peer_to_draft_r2"]["same_choice"] == 0
    assert transitions["peer_to_draft_r1"]["both_wrong"] == 2


def test_smaller_prompt_and_first_unopened_target_stay_fixed_when_draw_zero_is_missing(
    checkpoint: _Checkpoint,
) -> None:
    packet, board, board_digest = checkpoint
    enrolled = "s6501-r1"
    first = _first_observations(board)[enrolled]
    other = next(
        obs
        for obs in board["observations"]
        if obs["source_id"] == enrolled and obs["obs_id"] != first["obs_id"]
    )
    direct = _direct_report(
        checkpoint, {(first["obs_id"], 1): True, (other["obs_id"], 0): True}
    )
    panel = protocol.build(packet, board, board_digest, direct)
    slot = next(row for row in panel["sources"] if row["source_id"] == enrolled)
    unopened = sorted({f"Q{number}" for number in range(1, 7)} - set(first["opened"]))
    assert slot["obs_id"] == first["obs_id"]
    assert slot["target"] == unopened[0]
    assert slot["available"] is False
    report = _report(panel)
    summary = analysis.summarize(packet, board, panel, direct, report)
    assert summary["coverage"]["states"]["unavailable"] == 48
    effect = summary["effects"]["revision_solo"]["scopes"]["target"]
    assert effect["denominator"] == 0
    assert effect["observed_equal_world_mean"] is None
    assert summary["target_best_reported_bay"]["compared_sources"] == 12

    slot["target"] = unopened[1]
    for row in panel["rows"]:
        if row["source_id"] == enrolled:
            row["target"] = unopened[1]
    with pytest.raises(ValueError, match="panel differs from reconstructed provenance"):
        analysis.summarize(packet, board, panel, direct, _report(panel))


def test_rejects_wrong_target_even_when_the_other_action_has_a_valid_stock_grade(
    checkpoint: _Checkpoint,
) -> None:
    packet, board, board_digest = checkpoint
    enrolled = "s6501-r0"
    obs_id = _first_observations(board)[enrolled]["obs_id"]
    direct = _direct_report(checkpoint, {(obs_id, 0): False})
    panel = protocol.build(packet, board, board_digest, direct)
    report = _report(panel)
    row = next(row for row in report["rows"] if row["source_id"] == enrolled)
    source = inputs.source_index(packet)[enrolled]
    answer = _answer(source, row["target"], right=True, other_right=True)
    _complete(packet, board, row, answer)
    outcome = row["outcome"]
    target_id = outcome["target"]["id"]
    outcome["target"] = next(
        action for action in outcome["actions"] if action["id"] != target_id
    )
    with pytest.raises(ValueError, match="outcome differs from stock grade or target"):
        analysis.summarize(packet, board, panel, direct, report)
