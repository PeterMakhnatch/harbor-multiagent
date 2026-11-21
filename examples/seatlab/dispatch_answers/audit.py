from math import fsum
from pathlib import Path
from typing import Any

from .. import evidence, seat_agent, team_report
from ..attention import audit as attention
from ..attention import protocol as aliases
from ..capsule import view_hash
from ..export_capsules import reconstructed_capsules
from ..native_report import _digest
from ..visibility import native_pairs
from . import protocol

type Record = dict[str, Any]
type Turn = tuple[str, Record]

_TOKENS = ("input_tokens", "cache_tokens", "output_tokens")
_TURN_FIELDS = {
    "stage",
    "run",
    "role",
    "status",
    "usage",
    "text",
    "prompt_sha256",
    "response_sha256",
}


def _seat(
    records: dict[str, Record],
    capsules: dict[str, Any],
    world: Record,
    cell_id: str,
    role: str,
) -> tuple[Record, dict[str, Turn]]:
    inventory = role == "inventory"
    column, key = ("reports", "reports") if inventory else ("bay", "chosen")
    rows = [{"id": row["id"], column: row[key]} for row in world["cases"]]
    seat = records[role]
    metadata = attention._seat_metadata(
        seat,
        capsules[role],
        role,
        cell_id,
        rows,
        "direct",
        agent_name=protocol.AGENT_NAME,
        seat_schema=protocol.SEAT_SCHEMA,
    )
    stages = [stage for stage, owner in protocol.STAGES if owner == role]
    raw = metadata["turns"]
    if (
        not isinstance(raw, list)
        or len(raw) != len(stages)
        or any(
            not isinstance(turn, dict) or turn.keys() != _TURN_FIELDS for turn in raw
        )
    ):
        raise ValueError("Expected two complete model turns from each native seat")
    turns = {
        stage: attention._turn(turn, stage, role, cell_id, "direct")
        for stage, turn in zip(stages, raw, strict=True)
    }
    for key in _TOKENS:
        count = seat["agent_result"][f"n_{key}"]
        expected = sum(turn["usage"][key] for _, turn in turns.values())
        if type(count) is not int or count != expected:
            raise ValueError("Native seat totals differ from the verified model turns")
    return metadata, turns


def _receipt(nonce: str, stage: str, payload: object) -> str:
    return view_hash(("seatlab.dispatch-answers-receipt/v1", nonce, stage, payload))


def _reconcile(records: dict[str, Record], world: Record, cell: Record) -> Record:
    capsules = reconstructed_capsules(world)
    inventory, inv_turns = _seat(records, capsules, world, cell["cell_id"], "inventory")
    dispatch, dis_turns = _seat(records, capsules, world, cell["cell_id"], "dispatch")
    inv_cases = attention._capsule_cases(capsules["inventory"])
    dis_cases = attention._capsule_cases(capsules["dispatch"])
    seed = cell["seed"]
    nonce = dispatch["public_receipt_nonce"]
    if not _digest(nonce):
        raise ValueError("Invalid private receipt nonce")

    text, turn = inv_turns["inventory"]
    attention._check_prompt(turn, native_pairs._prompt("inventory", inv_cases))
    submission = seat_agent._json_object(text, "inventory model")
    seat_agent._checked_submission(submission, "inventory", protocol.EPISODES)
    if (
        native_pairs._decision_hash("inventory", submission["rows"])
        != inventory["decision_sha256"]
    ):
        raise ValueError(
            "The inventory model response differs from stock world reports"
        )

    text, turn = dis_turns["question"]
    attention._check_prompt(turn, protocol.question_prompt(dis_cases, seed))
    question = protocol.parse_question(text)
    question_hash = view_hash(question)
    if (
        inventory["question_sha256"] != question_hash
        or dispatch["question_sha256"] != question_hash
    ):
        raise ValueError("The two seats disagree on the actual question")

    text, turn = inv_turns["answer"]
    answer_prompt = protocol.answer_prompt(submission["rows"], seed, question)
    attention._check_prompt(turn, answer_prompt)
    answers = protocol.parse_answers(text)
    answer_hash = view_hash((question, answers))
    visible = cell["arm"] == "visible"
    delivered = answers if visible else None
    if inventory["answer_sha256"] != answer_hash or dispatch[
        "delivered_answer_sha256"
    ] != (answer_hash if visible else None):
        raise ValueError("Inventory answers or dispatch delivery disagree")

    text, turn = dis_turns["decide"]
    decide_prompt = protocol.decide_prompt(dis_cases, seed, question, delivered)
    attention._check_prompt(turn, decide_prompt)
    choices = aliases.decide(text, dis_cases, seed, 0)
    if (
        native_pairs._decision_hash("dispatch", choices["rows"])
        != dispatch["decision_sha256"]
    ):
        raise ValueError("The final model decisions differ from stock world decisions")

    answer_receipt = _receipt(nonce, "answer", (question, answers))
    turns = inv_turns | dis_turns
    return {
        "question_sha256": _receipt(nonce, "question", question),
        "answer_sha256": answer_receipt,
        "delivered_answer_sha256": answer_receipt if visible else None,
        "final_sha256": _receipt(nonce, "final", choices["rows"]),
        "turns": [
            {
                key: turns[stage][1][key]
                for key in ("stage", "run", "role", "status", "usage")
            }
            for stage, _ in protocol.STAGES
        ],
    }


def inspect_trial(trial: Path, config: Record, cell: Record, transport: str) -> Record:
    if transport != "direct" or cell not in protocol.grid():
        raise ValueError("Expected a registered native Dispatch answers trial")
    try:
        trial = trial.resolve(strict=True)
        native = attention._read_native(
            trial,
            config,
            cell["cell_id"],
            "direct",
            agent_import=protocol.AGENT_IMPORT,
            channel_id=trial.name,
        )
        receipt, world = evidence.read_trial_with_world(trial)
        score = receipt["score"]
        if (
            receipt["trial_id"] != native["id"]
            or receipt["seed"] != cell["seed"]
            or receipt["condition"] != protocol.CONDITION
            or score["episodes"] != protocol.EPISODES
            or receipt["fingerprint"]
            != team_report._registered_fingerprint(cell["seed"], protocol.EPISODES)
        ):
            raise ValueError("The native trial does not match the registered world")
        rewards = native["verifier_result"]["rewards"]
        reward_path = trial / "verifier" / "reward.json"
        if (
            not team_report._within_trial(reward_path, trial)
            or team_report._read_object(reward_path) != rewards
        ):
            raise ValueError("Harbor's recorded reward differs from the verifier file")
        records = evidence._by_role(native["seat_results"], "seat_results")
        return {
            "trial_id": receipt["trial_id"],
            "fingerprint": receipt["fingerprint"],
            "native_score": rewards["reward"],
            **_reconcile(records, world, cell),
        }
    except (
        OSError,
        TypeError,
        KeyError,
        IndexError,
        RecursionError,
        seat_agent._Unmeasured,
    ):
        raise ValueError("Native Dispatch answers evidence is incomplete") from None


def summarize(cells: list[Record], rows: list[Record]) -> Record:
    scores: dict[tuple[int, str], float] = {}
    trials: set[str] = set()
    worlds: dict[int, str] = {}
    for cell, row in zip(cells, rows, strict=True):
        result = row["outcome"]
        if result is None:
            continue
        seed, arm = cell["seed"], cell["arm"]
        trial, fingerprint = result["trial_id"], result["fingerprint"]
        if trial in trials or worlds.setdefault(seed, fingerprint) != fingerprint:
            raise ValueError(
                "Expected fresh native trials and a matched world per seed"
            )
        trials.add(trial)
        scores[seed, arm] = result["native_score"]
    if len(set(worlds.values())) != len(worlds):
        raise ValueError("Distinct seeds yielded the same native world")

    pairs = [
        (scores.get((seed, "visible")), scores.get((seed, "withheld")))
        for seed in protocol.SEEDS
    ]
    deltas = [v - w for v, w in pairs if v is not None and w is not None]
    lower = fsum(
        (v if v is not None else 0) - (w if w is not None else 1) for v, w in pairs
    )
    upper = fsum(
        (v if v is not None else 1) - (w if w is not None else 0) for v, w in pairs
    )
    return {
        "registered_cells": len(cells),
        "observed_cells": len(trials),
        "registered_pairs": len(pairs),
        "matched_pairs": len(deltas),
        "matched_mean_visible_minus_withheld": fsum(deltas) / len(deltas)
        if deltas
        else None,
        "registered_mean_bounds": {
            "lower": lower / len(pairs),
            "upper": upper / len(pairs),
        },
    }
