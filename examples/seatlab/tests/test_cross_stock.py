import json
from hashlib import sha256
from itertools import product
from typing import Any

import pytest

from examples.seatlab.attention.protocol import aliases
from examples.seatlab.decision_cross import inputs, stock
from examples.seatlab.tests import test_turn_fork_packet as fixtures
from examples.seatlab.turn_fork import packet, protocol
from examples.seatlab.visibility.clarify_run import _encode

study = fixtures.study
stored = fixtures.stored


def _selections(
    source_id: str, obs_id: str, opened: tuple[str, str]
) -> list[dict[str, Any]]:
    sender_hash = sha256(json.dumps({"open": opened}).encode()).hexdigest()
    slots = product(
        packet.SEEDS, packet.REPETITIONS, ("direct", "commands"), ("aligned", "repeat")
    )
    selections: list[dict[str, Any]] = []
    for seed, rep, sender, arm in slots:
        slot = f"s{seed}-r{rep}"
        selections.append(
            {
                "source_id": slot,
                "sender": sender,
                "arm": arm,
                "obs_id": obs_id if slot == source_id else None,
                "sender_response_sha256": sender_hash if slot == source_id else None,
            }
        )
    return selections


def _cross(
    source_packet: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = source_packet["sources"][0]
    source_id = source["source_id"]
    opened = ("Q1", "Q2")
    prompt_sha256 = sha256(protocol.decide_prompt(source, opened).encode()).hexdigest()
    obs_id = f"{source_id}-{prompt_sha256[:12]}"
    board: dict[str, Any] = {
        "schema": "seatlab.decision-cross-board/v1",
        "packet_sha256": sha256(_encode(source_packet)).hexdigest(),
        "direct_report_sha256": "d" * 64,
        "commands_report_sha256": "c" * 64,
        "observations": [
            {
                "source_id": source_id,
                "obs_id": obs_id,
                "prompt_sha256": prompt_sha256,
                "opened": list(opened),
            }
        ],
        "selections": _selections(source_id, obs_id, opened),
    }
    choices = {
        alias: max(case["after"], key=case["utilities"].__getitem__)
        for alias, case_id in aliases(
            source["cases"], source["seed"], source["repetition"]
        ).items()
        for case in (source["cases"][case_id],)
    }
    response = json.dumps({"choices": choices})
    digest = sha256(_encode(board)).hexdigest()
    rows = []
    for draw in inputs.draws(board):
        complete = draw["draw"] == 0
        rows.append(
            dict(
                draw,
                run=inputs.run_id(digest, obs_id, draw["draw"]),
                state="complete" if complete else "incomplete",
                turn={
                    "status": "received" if complete else "callback_error",
                    "prompt_sha256": prompt_sha256,
                    "response_sha256": (
                        sha256(response.encode()).hexdigest() if complete else None
                    ),
                    "usage": None,
                },
                outcome=protocol.grade(source, opened, response) if complete else None,
            )
        )
    return board, {
        "schema": "seatlab.decision-cross-report/v1",
        "board_sha256": digest,
        "transport": "commands",
        "rows": rows,
    }


def test_stock_counts_shared_draw_once_and_keeps_logical_uses(
    study: fixtures.Study, stored: dict[str, Any]
) -> None:
    board, report = _cross(stored)
    audit = stock.compare(stored, board, report, study.root)
    assert audit["original_prompts_bound"] == 2
    assert audit["original_actions_bound"] == 6
    assert audit["observation_weights"] == {board["observations"][0]["obs_id"]: 4}
    assert audit["unique_draws"] == {
        "scheduled": 2,
        "complete": 1,
        "incomplete": 1,
        "unclaimed": 0,
        "actions": 6,
        "optimal": 6,
        "feasible": 6,
    }
    assert audit["logical_uses"] == {
        "registered": 96,
        "assigned": 8,
        "complete": 4,
        "incomplete": 4,
        "unclaimed": 0,
        "excluded": 88,
    }


def test_stock_rejects_drift_in_the_private_world(
    study: fixtures.Study, stored: dict[str, Any]
) -> None:
    board, report = _cross(stored)
    case = study.world["cases"][1]
    replacement = next(
        bay for bay in "ABCD" if bay not in case["before"] + case["after"]
    )
    retained = set(case["before"]) & set(case["after"])
    case["before"] = sorted(retained | {replacement})
    fixtures._write(study.trial / "artifacts/logs/world/handoff.json", study.world)

    with pytest.raises(ValueError, match="native source trial or world differs"):
        stock.compare(stored, board, report, study.root)
