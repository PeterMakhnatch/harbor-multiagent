import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import analysis, inputs
from examples.seatlab.turn_fork import packet, protocol
from examples.seatlab.visibility.clarify_run import _encode

_OPENED = {"a": ("Q1", "Q2"), "b": ("Q3", "Q4")}
_SENDERS = ("direct", "commands")
_ARMS = ("aligned", "repeat")

type _Document = dict[str, Any]
type _Checkpoint = tuple[str, dict[str, _Document]]
type _Pick = tuple[int, int, str, str]
type _Score = tuple[int, int, str, int]


@pytest.fixture(scope="module")
def checkpoint() -> _Checkpoint:
    results = Path(__file__).resolve().parents[1] / "results"
    digest = (results / "turn-fork.sha256").read_text(encoding="ascii").strip()
    data = packet.load(results / "turn-fork-packet.json", digest)
    return digest, {source["source_id"]: source for source in data["sources"]}


def _board(checkpoint: _Checkpoint, picks: dict[_Pick, str]) -> _Document:
    packet_digest, sources = checkpoint
    observations: dict[tuple[str, str], _Document] = {}
    selections: list[_Document] = []
    for seed in packet.SEEDS:
        for rep in packet.REPETITIONS:
            source_id = f"s{seed}-r{rep}"
            for sender in _SENDERS:
                for arm in _ARMS:
                    name = picks.get((seed, rep, sender, arm))
                    obs_id = response_sha256 = None
                    if name is not None:
                        key = (source_id, name)
                        if key not in observations:
                            opened = _OPENED[name]
                            prompt = protocol.decide_prompt(sources[source_id], opened)
                            digest = sha256(prompt.encode()).hexdigest()
                            observations[key] = {
                                "source_id": source_id,
                                "prompt_sha256": digest,
                                "obs_id": f"{source_id}-{digest[:12]}",
                                "opened": list(opened),
                            }
                        obs_id = observations[key]["obs_id"]
                        response_sha256 = sha256(
                            f"{source_id}/{sender}/{arm}/{name}".encode()
                        ).hexdigest()
                    selections.append(
                        {
                            "source_id": source_id,
                            "sender": sender,
                            "arm": arm,
                            "obs_id": obs_id,
                            "sender_response_sha256": response_sha256,
                        }
                    )
    return {
        "schema": "seatlab.decision-cross-board/v1",
        "packet_sha256": packet_digest,
        "direct_report_sha256": "d" * 64,
        "commands_report_sha256": "c" * 64,
        "observations": sorted(
            observations.values(),
            key=lambda row: (row["source_id"], row["prompt_sha256"]),
        ),
        "selections": selections,
    }


def _reply(source: _Document, score: int) -> str:
    choices = {}
    by_alias = aliases(source["cases"], source["seed"], source["repetition"])
    for alias, case_id in by_alias.items():
        case = source["cases"][case_id]
        worse, better = sorted(case["after"], key=case["utilities"].__getitem__)
        choices[alias] = better if case_id < score else worse
    return json.dumps({"choices": choices})


def _report(
    board: _Document, checkpoint: _Checkpoint, receiver: str, scores: dict[_Score, int]
) -> _Document:
    digest = sha256(_encode(board)).hexdigest()
    targets = {
        (f"s{seed}-r{rep}", _OPENED[name], draw): score
        for (seed, rep, name, draw), score in scores.items()
    }
    observations = {obs["obs_id"]: obs for obs in board["observations"]}
    rows: list[_Document] = []
    for draw in inputs.draws(board):
        obs = observations[draw["obs_id"]]
        source_id = obs["source_id"]
        first, second = obs["opened"]
        opened = (first, second)
        score = targets.get((source_id, opened, draw["draw"]))
        row: _Document = {
            **draw,
            "run": inputs.run_id(digest, obs["obs_id"], draw["draw"]),
            "state": "unclaimed",
            "turn": None,
            "outcome": None,
        }
        if score is not None:
            source = checkpoint[1][source_id]
            text = _reply(source, score)
            row["state"] = "complete"
            row["turn"] = {
                "status": "received",
                "prompt_sha256": obs["prompt_sha256"],
                "response_sha256": sha256(text.encode()).hexdigest(),
                "usage": None,
            }
            row["outcome"] = protocol.grade(source, opened, text)
        rows.append(row)
    return {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": receiver,
        "rows": rows,
    }


def test_partial_worlds_keep_bounds_on_all_registered_slots(
    checkpoint: _Checkpoint,
) -> None:
    board = _board(checkpoint, {(6501, 0, "direct", "aligned"): "a"})
    direct = _report(
        board, checkpoint, "direct", {(6501, 0, "a", 0): 6, (6501, 0, "a", 1): 3}
    )
    commands = _report(board, checkpoint, "commands", {(6501, 0, "a", 0): 0})
    summary = analysis.summarize(board, direct, commands)
    cell = summary["cells"]["direct"]["direct"]
    assert (
        cell["eligible_slots"],
        cell["selected_slots"],
        cell["completed_slots"],
    ) == (48, 2, 2)
    assert (cell["weighted_optimal_actions"], cell["weighted_scored_actions"]) == (
        9,
        12,
    )
    assert cell["equal_world_optimal_fraction"] is None
    effect = summary["receiver_effects"]["direct"]
    assert effect["eligible_pairs"] == 48
    assert effect["observed_matched_pairs"] == 1
    assert effect["equal_world_observed_optimal_fraction_difference"] is None
    assert effect["worlds"][0]["observed_optimal_fraction_difference"] == 1
    assert effect["worlds"][1]["registered_bounds"] == {"lower": -1, "upper": 1}
    assert effect["registered_bounds"] == pytest.approx(
        {"lower": -45.5 / 48, "upper": 47.5 / 48}
    )
    assert summary["compression"]["saved_call_slots_per_receiver"] == 0


def test_equal_world_estimates_do_not_pool_matched_slots(
    checkpoint: _Checkpoint,
) -> None:
    board = _board(
        checkpoint,
        {
            (seed, 0, sender, "aligned"): name
            for seed in packet.SEEDS
            for sender, name in (("direct", "a"), ("commands", "b"))
        },
    )
    commands_scores: dict[_Score, int] = {
        (seed, 0, name, 0): score
        for seed in packet.SEEDS
        for name, score in (("a", 2), ("b", 5))
    }
    commands_scores[6501, 0, "a", 1] = 2
    direct_scores: dict[_Score, int] = {
        (seed, 0, name, 0): score
        for seed in packet.SEEDS
        for name, score in (("a", 0), ("b", 3))
    }
    direct_scores[6501, 0, "a", 0] = direct_scores[6501, 0, "a", 1] = 6
    direct = _report(board, checkpoint, "direct", direct_scores)
    commands = _report(board, checkpoint, "commands", commands_scores)
    summary = analysis.summarize(board, direct, commands)
    cell = summary["cells"]["direct"]["direct"]
    assert (cell["weighted_optimal_actions"], cell["weighted_scored_actions"]) == (
        12,
        42,
    )
    assert cell["equal_world_optimal_fraction"] == pytest.approx(1 / 6)
    receiver = summary["receiver_effects"]
    sender = summary["sender_effects"]
    assert receiver["direct"]["observed_matched_pairs"] == 7
    estimate = "equal_world_observed_optimal_fraction_difference"
    assert receiver["direct"][estimate] == pytest.approx(-1 / 6)
    assert receiver["commands"][estimate] == pytest.approx(-1 / 3)
    assert sender["direct"][estimate] == pytest.approx(-1 / 3)
    assert sender["commands"][estimate] == pytest.approx(-1 / 2)


def test_reuse_weights_cells_but_not_repeat_agreement(checkpoint: _Checkpoint) -> None:
    board = _board(
        checkpoint,
        {
            (seed, rep, sender, arm): "a"
            for seed in packet.SEEDS
            for rep in packet.REPETITIONS
            for sender in _SENDERS
            for arm in _ARMS
        },
    )
    direct = _report(
        board,
        checkpoint,
        "direct",
        {
            (6501, 0, "a", 0): 6,
            (6501, 0, "a", 1): 6,
            (6501, 1, "a", 0): 1,
            (6501, 1, "a", 1): 3,
            (6502, 0, "a", 0): 4,
        },
    )
    commands = _report(board, checkpoint, "commands", {})
    summary = analysis.summarize(board, direct, commands)
    for sender in _SENDERS:
        cell = summary["cells"][sender]["direct"]
        assert cell["completed_slots"] == 10
        assert (cell["weighted_optimal_actions"], cell["weighted_scored_actions"]) == (
            40,
            60,
        )
    assert summary["compression"] == {
        "registered_selection_occurrences": 48,
        "nonmissing_selection_occurrences": 48,
        "unique_observations": 12,
        "unshared_call_slots_per_receiver": 96,
        "shared_call_slots_per_receiver": 24,
        "saved_call_slots_per_receiver": 72,
        "saved_call_slots_across_receivers": 144,
    }
    assert summary["receiver_draw_agreement"]["direct"] == {
        "unique_observations": 12,
        "matched_unique_observations": 2,
        "identical_action_vectors": 1,
        "action_slots": 12,
        "action_agreements": 10,
        "action_agreements_by_slot": [2, 1, 1, 2, 2, 2],
        "optimal_actions_draw_1_minus_draw_0_counts": {"0": 1, "2": 1},
    }
    for receiver, matched in (("direct", 10), ("commands", 0)):
        effect = summary["sender_effects"][receiver]
        assert effect["observed_matched_pairs"] == matched
        assert effect["same_draw_pairs"] == 48
        assert effect["structurally_known_unobserved_pairs"] == 48 - matched
        assert effect["registered_bounds"] == {"lower": 0, "upper": 0}
    assert summary["receiver_effects"]["direct"]["same_draw_pairs"] == 0


def test_swapped_shared_draws_cancel_only_in_the_aggregate(
    checkpoint: _Checkpoint,
) -> None:
    board = _board(
        checkpoint,
        {
            (6501, 0, "direct", "aligned"): "a",
            (6501, 0, "direct", "repeat"): "b",
            (6501, 0, "commands", "aligned"): "b",
            (6501, 0, "commands", "repeat"): "a",
        },
    )
    direct = _report(board, checkpoint, "direct", {})
    commands = _report(board, checkpoint, "commands", {})
    effect = analysis.summarize(board, direct, commands)["sender_effects"]["direct"]
    assert effect["observed_matched_pairs"] == 0
    assert effect["same_draw_pairs"] == 0
    assert effect["structurally_known_unobserved_pairs"] == 0
    assert effect["worlds"][0]["registered_bounds"] == {"lower": -0.5, "upper": 0.5}
    assert effect["registered_bounds"] == pytest.approx(
        {"lower": -44 / 48, "upper": 44 / 48}
    )


@pytest.mark.parametrize(
    "change", ("schema", "board_sha256", "transport", "order", "duplicate")
)
def test_rejects_unmatched_or_duplicate_report_draws(
    checkpoint: _Checkpoint, change: str
) -> None:
    board = _board(checkpoint, {(6501, 0, "direct", "aligned"): "a"})
    direct = _report(board, checkpoint, "direct", {})
    commands = _report(board, checkpoint, "commands", {})
    if change in ("schema", "board_sha256", "transport"):
        commands[change] = direct[change] if change == "transport" else "incorrect"
    elif change == "order":
        commands["rows"].reverse()
    else:
        commands["rows"][1] = commands["rows"][0]
    with pytest.raises(ValueError, match="cross report"):
        analysis.summarize(board, direct, commands)
