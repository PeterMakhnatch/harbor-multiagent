"""Verify the registered native prefix continuations offline without publishing transcripts."""

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Never
from uuid import UUID

from harbor.models.trial.config import TrialConfig

from ..visibility import spend
from . import (
    accounting,
    analysis,
    contract,
    prefix_driver,
    prefix_packet,
    prefix_schedule,
    recommendation,
    schedule,
)
from . import (
    recommendation_report as native,
)

_ROLES = schedule.ROLES
_ARMS = schedule.RECOMMENDATION_ARMS
_CUT = {"message_count": 9, "votes_submitted": 0, "done": []}
_STATUSES = (
    "source_ineligible",
    "unattempted",
    "incomplete",
    "exact_prefix_failure",
    "scored",
)
_RUNTIME_MISMATCHES = (
    "prefix_dossier_mismatch",
    "prefix_chat_read_mismatch",
    "prefix_chat_post_mismatch",
    "prefix_cut_status_mismatch",
    "prefix_audit_mismatch",
    "prefix_final_board_mismatch",
)
_COUNTS = ("native_majority", "correct_votes", "switch_to_chair")
_require = analysis._require
_canonical = analysis._canonical

type _JsonRow = dict[str, Any]
type _Ledger = dict[tuple[str, str], _JsonRow]


@dataclass
class _Evidence:
    trial_id: UUID | None = None
    prefix_exact: bool | None = None
    cut_status_verified: bool | None = None
    chair_prompt: str | None = None
    chair_action: str | None = None


def _ledger(directory: Path | None) -> tuple[str, _Ledger]:
    if directory is None:
        return "not_supplied", {}
    try:
        directory = directory.absolute()
        analysis._path(directory.parent, f"{directory.name}/direct-flash.sqlite3")
        report = spend.report(directory, run_prefix="pf-")
        _require(report["schema"] == "seatlab.direct-flash-cost/v1", "ledger_schema")
        rows = report["calls"]
        ledger = {(row["run"], row["role"]): row for row in rows}
        _require(len(ledger) == len(rows), "duplicate_direct_receipt")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ArithmeticError,
        sqlite3.Error,
        analysis._Excluded,
    ):
        return "unavailable", {}
    return "available", ledger


def _normalized(event: object) -> _JsonRow | None:
    try:
        return prefix_packet.normalize_event(event)
    except (ValueError, TypeError):
        return None


def _prefix(
    events: list[_JsonRow], checkpoint: prefix_packet.Checkpoint, proof: _Evidence
) -> list[_JsonRow]:
    for actual, expected in zip(events, checkpoint.events):
        normalized = _normalized(actual)
        if normalized is None or _canonical(normalized) != _canonical(expected):
            proof.prefix_exact = False
            raise native._Failure(
                "prefix", "native_prefix_events_differ", "exact_prefix_failure"
            )
    length = len(checkpoint.events)
    _require(len(events) >= length, "native_prefix_unfinished")
    proof.prefix_exact = True
    return events[length:]


def _suffix(after: list[_JsonRow], start: int, refs: list[int]) -> dict[str, _JsonRow]:
    reason = "native_post_cut_actions_mismatch"
    _require(len(after) == 3 * len(_ROLES), reason)
    by_role: dict[str, dict[str, _JsonRow]] = {role: {} for role in _ROLES}
    for seq, event in enumerate(after, start + 1):
        role, kind = event.get("actor"), event.get("event_type")
        if not isinstance(role, str) or not isinstance(kind, str):
            raise analysis._Excluded(reason)
        _require(role in by_role and kind not in by_role[role], reason)
        payload = analysis._object(event.get("payload"), reason)
        expected: _JsonRow
        if seq <= start + len(_ROLES):
            _require(kind == "message_read", reason)
            expected = {"channel": "public", "msg_seqs": refs}
        elif kind == "vote":
            _require("message_read" in by_role[role], reason)
            _require(payload.keys() == {"seat", "choice", "cited_facts"}, reason)
            expected = payload | {"seat": role}
        else:
            _require(kind == "seat_done" and "vote" in by_role[role], reason)
            expected = {"seat": role}
        normalized = _normalized(event)
        _require(
            normalized is not None
            and _canonical(normalized)
            == _canonical(
                {
                    "seq": seq,
                    "task_family": analysis._FAMILY,
                    "condition": analysis._CONDITION,
                    "seed": 42,
                    "event_type": kind,
                    "actor": role,
                    "authenticated": True,
                    "visibility": "world_audit",
                    "recipients": [],
                    "payload": expected,
                }
            ),
            reason,
        )
        by_role[role][kind] = payload
    _require(
        all(
            events.keys() == {"message_read", "vote", "seat_done"}
            for events in by_role.values()
        ),
        reason,
    )
    return {role: events["vote"] for role, events in by_role.items()}


def _config(directory: Path, trial: Path, cell: schedule.Cell) -> tuple[str, _JsonRow]:
    reason = "native_config_mismatch"
    launch = analysis._json(directory, "config.json", reason)
    task = launch["task"]["path"]
    if not isinstance(task, str):
        raise analysis._Excluded(reason)
    task_path = Path(task)
    declared = Path(launch["trials_dir"])
    _require(
        task_path.is_absolute()
        and ".." not in task_path.parts
        and task_path.name == analysis._CONDITION
        and declared.is_absolute()
        and ".." not in declared.parts
        and declared.parts[-3:] == ("cells", cell["cell_id"], "trials"),
        reason,
    )
    # The study can move. Regenerate from the recorded absolute paths, but never
    # follow them. The stock checksum binds the task independently below.
    expected = prefix_driver.config(declared.parent, task_path, cell)
    _require(_canonical(launch) == _canonical(expected), reason)
    normalized = TrialConfig.model_validate(expected).model_dump(mode="json")
    archived = analysis._json(trial, "config.json", reason)
    _require(
        _canonical(archived)
        == _canonical(
            {key: value for key, value in normalized.items() if key != "agent"}
        ),
        reason,
    )
    return task, normalized


def _trial_id(result: object) -> UUID | None:
    if not isinstance(result, dict) or not isinstance(result.get("id"), str):
        return None
    try:
        trial_id = UUID(result["id"])
    except ValueError:
        return None
    return trial_id if trial_id.int else None


def _contexts(
    result: _JsonRow, trial: Path, task: str, config: _JsonRow, proof: _Evidence
) -> dict[str, _JsonRow]:
    reason = "native_result_invalid"
    embedded = result.get("config")
    _require(
        proof.trial_id is not None
        and type(result.get("schema_version", 1)) is int
        and result.get("schema_version", 1) == 1
        and result.get("trial_name") == trial.name
        and result.get("task_name") == analysis._TASK
        and result.get("task_checksum") == schedule.TASK_CHECKSUM
        and result.get("task_id") == {"path": task}
        and isinstance(embedded, dict)
        and _canonical(embedded) == _canonical(config),
        reason,
    )
    reason = "native_trial_failed_or_incomplete"
    _require(
        result.get("exception_info") is None
        and result.get("reward_valid") is True
        and isinstance(result.get("finished_at"), str)
        and result["finished_at"],
        reason,
    )
    contexts: dict[str, _JsonRow] = {}
    for role, seat in analysis._roles(result.get("seat_results"), reason).items():
        finished = analysis._object(seat.get("agent_execution"), reason).get(
            "finished_at"
        )
        _require(
            seat.get("under_test") is True
            and seat.get("exception_info") is None
            and isinstance(finished, str)
            and finished,
            reason,
        )
        info = analysis._object(seat.get("agent_info"), "native_seat_model_mismatch")
        _require(
            info.get("name") == "seatlab-prefix-voter"
            and info.get("model_info")
            == {"provider": "deepseek", "name": "deepseek-flash"},
            "native_seat_model_mismatch",
        )
        contexts[role] = analysis._object(
            seat.get("agent_result"), "seat_context_missing"
        )
    return contexts


def _receipt(trial: Path, role: str) -> _JsonRow:
    reason = "seat_receipt_mismatch"
    return analysis._object(
        analysis._json(trial, f"seats/{role}/agent/prefix-vote.json", reason), reason
    )


def _seat_identity(
    cell: schedule.Cell, role: str, checkpoint: prefix_packet.Checkpoint
) -> _JsonRow:
    return {
        "schema": "seatlab.prefix-seat/v1",
        "role": role,
        "run_id": cell["cell_id"],
        "map_index": cell["map_index"],
        "arm": cell["arm"],
        "prefix_sha256": checkpoint.prefix_sha256,
    }


def _runtime_mismatch(
    trial: Path,
    result: object,
    cell: schedule.Cell,
    checkpoint: prefix_packet.Checkpoint,
) -> str | None:
    # A matched failure receipt can distinguish a declined replay reply from a
    # stopped attempt even when no native result can be scored.
    if not isinstance(result, dict) or result.get("trial_name") != trial.name:
        return None
    try:
        seats = analysis._roles(result.get("seat_results"), "seat_receipt_mismatch")
    except (TypeError, analysis._Excluded):
        return None
    for role, seat in seats.items():
        context = seat.get("agent_result")
        metadata = context.get("metadata") if isinstance(context, dict) else None
        if not isinstance(metadata, dict):
            continue
        failure = metadata.get("failure")
        if (
            not isinstance(failure, str)
            or failure not in _RUNTIME_MISMATCHES
            or metadata.get("status") != "failed"
        ):
            continue
        identity = _seat_identity(cell, role, checkpoint)
        if _canonical({key: metadata.get(key) for key in identity}) != _canonical(
            identity
        ):
            continue
        try:
            receipt = _receipt(trial, role)
        except analysis._Excluded:
            continue
        if _canonical(receipt) == _canonical(metadata | {"action": None}):
            return failure
    return None


def _reject_runtime_mismatch(reason: str, proof: _Evidence) -> Never:
    if reason == "prefix_cut_status_mismatch":
        proof.cut_status_verified = False
    raise native._Failure("prefix", reason, "exact_prefix_failure")


def _seat(
    trial: Path,
    role: str,
    cell: schedule.Cell,
    checkpoint: prefix_packet.Checkpoint,
    refs: list[int],
    context: _JsonRow,
    payload: _JsonRow,
    board: recommendation.Board,
    proof: _Evidence,
) -> tuple[_JsonRow, str, _JsonRow]:
    reason = "seat_receipt_mismatch"
    metadata = analysis._object(context.get("metadata"), reason)
    receipt = _receipt(trial, role)
    identity = _seat_identity(cell, role, checkpoint) | {
        "status": "complete",
        "cut_status": _CUT,
        "read_msg_seqs": refs,
    }
    _require(metadata.keys() == identity.keys() | {"turn"}, reason)
    cut_statuses = (metadata["cut_status"], receipt.get("cut_status"))
    _require(
        all(status is not None for status in cut_statuses), "native_cut_status_missing"
    )
    if any(
        not isinstance(status, dict) or _canonical(status) != _canonical(_CUT)
        for status in cut_statuses
    ):
        proof.cut_status_verified = False
        raise native._Failure(
            "prefix", "native_cut_status_mismatch", "exact_prefix_failure"
        )
    _require(
        _canonical({key: metadata[key] for key in identity}) == _canonical(identity),
        reason,
    )
    index = cell["map_index"]
    action = {key: payload[key] for key in ("choice", "cited_facts")}
    ballot = dict(recommendation.blind_vote(action, index))
    dossier = native._dossier(role)
    decoded = native._vote_action(ballot, dossier, board, index)
    _require(_canonical(decoded) == _canonical(action), "native_action_mismatch")
    _require(_canonical(receipt) == _canonical(metadata | {"action": decoded}), reason)
    prompt = recommendation.turn_prompt(
        "vote", dossier, board, index, role, checkpoint.chair, cell["arm"]
    )
    turn = metadata["turn"]
    served = native._turn_commitment(
        turn, receipt["turn"] | {"action": receipt["action"]}, prompt, decoded
    )
    if role == checkpoint.chair:
        proof.chair_prompt, proof.chair_action = (
            turn["prompt_sha256"],
            turn["action_sha256"],
        )
    return ballot, served, {key: context.get(key) for key in accounting._TOKENS}


def _count(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _inspect(
    root: Path,
    cell: schedule.Cell,
    checkpoint: prefix_packet.Checkpoint,
    packet_sha256: str,
    ledger_status: str,
    ledger: _Ledger,
    proof: _Evidence,
) -> _JsonRow:
    cell_id = cell["cell_id"]
    name = f"committee-{cell_id}"
    with native._stage("attempt"):
        try:
            directory = analysis._path(root, f"cells/{cell_id}", directory=True)
        except FileNotFoundError:
            if ledger:
                raise native._Failure(
                    "attempt", "direct_call_without_attempt"
                ) from None
            raise native._Failure("attempt", "not_attempted", "unattempted") from None
        attempt = analysis._json(
            directory, "attempt.json", "attempt_missing_or_invalid"
        )
        _require(
            _canonical(attempt)
            == _canonical(
                cell
                | {
                    "schema": "seatlab.committee-attempt/v1",
                    "trial_name": name,
                    "task_git_tree": schedule.TASK_TREE,
                    "packet_sha256": packet_sha256,
                    "prefix_sha256": checkpoint.prefix_sha256,
                }
            ),
            "attempt_mismatch",
        )
    with native._stage("trial"):
        trial = analysis._path(directory, f"trials/{name}", directory=True)
        _require(list(trial.parent.iterdir()) == [trial], "multiple_native_trials")
        result = analysis._json(
            trial, "result.json", "native_result_invalid", optional=True
        )
        proof.trial_id = _trial_id(result)
    with native._stage("prefix"):
        runtime_mismatch = _runtime_mismatch(trial, result, cell, checkpoint)
    try:
        with native._stage("prefix"):
            after = _prefix(analysis._events(trial), checkpoint, proof)
    except native._Failure as exc:
        if runtime_mismatch is not None and exc.status != "exact_prefix_failure":
            _reject_runtime_mismatch(runtime_mismatch, proof)
        raise
    if runtime_mismatch is not None:
        _reject_runtime_mismatch(runtime_mismatch, proof)
    with native._stage("launch"):
        launch = analysis._object(
            analysis._json(
                directory, "outcome.json", "launch_outcome_missing_or_invalid"
            ),
            "launch_outcome_missing_or_invalid",
        )
        _require(
            launch.keys() == {"schema", "returncode", "error"}
            and launch["schema"] == "seatlab.committee-launch/v1"
            and type(launch["returncode"]) is int
            and launch["returncode"] == 0
            and launch["error"] is None,
            "native_launch_failed_or_incomplete",
        )
    with native._stage("config"):
        task, config = _config(directory, trial, cell)
    with native._stage("result"):
        result = analysis._object(result, "native_result_invalid")
        contexts = _contexts(result, trial, task, config, proof)
    refs = [message["seq"] for message in checkpoint.board["messages"]]
    with native._stage("post_cut"):
        by_role = _suffix(after, len(checkpoint.events), refs)
    index = cell["map_index"]
    board = recommendation.board(checkpoint.board, index, checkpoint.chair)
    labels, _ = contract._mapping(index)
    recommendation_ballot = board.recommendation
    if recommendation_ballot is None:
        raise native._Failure("prefix", "native_prefix_recommendation_missing")
    recommended = recommendation_ballot["choice"]
    seats, expected_calls, stocks, usage = [], {}, [], {}
    for role in _ROLES:
        with native._stage("vote_receipt"):
            ballot, served, counts = _seat(
                trial,
                role,
                cell,
                checkpoint,
                refs,
                contexts[role],
                by_role[role],
                board,
                proof,
            )
        expected_calls[(cell_id, role)] = served
        usage[role] = counts
        share = analysis._parse(
            next(
                item["text"] for item in board.facts["messages"] if item["seat"] == role
            )
        )
        correct = ballot["choice"] == labels["Option_A"]
        stocks.append(
            {
                "vote": ballot | {"correct": correct},
                "share": {"private": share["id"] in analysis._PRIVATE},
            }
        )
        dissented = (
            role != checkpoint.chair and board.proposals[role]["choice"] != recommended
        )
        seats.append(
            {
                "seat": native._SEATS[role],
                "recommendation_visible": cell["arm"] == "shown"
                or role == checkpoint.chair,
                "choice": ballot["choice"],
                "correct": correct,
                "eligible_dissenter": dissented,
                "switched_to_chair": ballot["choice"] == recommended
                if dissented
                else None,
            }
        )
    proof.cut_status_verified = True
    with native._stage("spend"):
        native._verify_spend(cell_id, ledger_status, ledger, expected_calls)
        for role, counts in usage.items():
            _require(
                _canonical(counts)
                == _canonical(accounting._direct_usage(ledger[cell_id, role])),
                "native_seat_usage_mismatch",
            )
    with native._stage("reward"):
        rewards = analysis._object(
            result["verifier_result"]["rewards"], "native_reward_missing"
        )
        verified = native._verified_reward(trial, rewards, stocks, index)
    return {
        "native_vote_reward": rewards["reward"],
        "seats": seats,
        "native_majority": _count(int(rewards["reward"]), 1),
        "correct_votes": _count(verified["correct_individual_votes"], len(_ROLES)),
        "switch_to_chair": _count(
            sum(seat["switched_to_chair"] is True for seat in seats),
            sum(seat["eligible_dissenter"] for seat in seats),
        ),
    }


def _sum_counts(rows: list[_JsonRow], key: str) -> dict[str, int]:
    return _count(
        sum(row[key]["numerator"] for row in rows),
        sum(row[key]["denominator"] for row in rows),
    )


def _delta(masked: Mapping[str, int], shown: Mapping[str, int]) -> float | None:
    if not masked["denominator"] or not shown["denominator"]:
        return None
    return (
        shown["numerator"] / shown["denominator"]
        - masked["numerator"] / masked["denominator"]
    )


def _contrast(cells: list[_JsonRow], proofs: dict[str, _Evidence]) -> _JsonRow:
    by_key = {(row["map_index"], row["repetition"], row["arm"]): row for row in cells}
    maps, pairs = [], []
    for index in range(6):
        paired = {arm: [] for arm in _ARMS}
        chair_choice, chair_action = [], []
        for repetition in range(2):
            branches = {arm: by_key[index, repetition, arm] for arm in _ARMS}
            complete = all(row["status"] == "scored" for row in branches.values())
            effects = change = None
            if complete:
                for arm, row in branches.items():
                    paired[arm].append(row)
                effects = {
                    key: _delta(branches["masked"][key], branches["shown"][key])
                    for key in _COUNTS
                }
                masked, shown = (proofs[branches[arm]["cell_id"]] for arm in _ARMS)
                if (
                    masked.chair_prompt is not None
                    and masked.chair_prompt == shown.chair_prompt
                ):
                    choices = [
                        next(
                            seat["choice"]
                            for seat in row["seats"]
                            if seat["seat"] == row["chair"]
                        )
                        for row in branches.values()
                    ]
                    change = {
                        "different_choice": choices[0] != choices[1],
                        "different_action": masked.chair_action != shown.chair_action,
                    }
                    chair_choice.append(change["different_choice"])
                    chair_action.append(change["different_action"])
            pairs.append(
                {
                    "map_index": index,
                    "repetition": repetition,
                    "by_arm": {arm: row["status"] for arm, row in branches.items()},
                    "effects": effects,
                    "chair_variation": change,
                }
            )
        totals = {
            arm: {key: _sum_counts(rows, key) for key in _COUNTS}
            for arm, rows in paired.items()
        }
        maps.append(
            {
                "map_index": index,
                "complete_pairs": len(paired["masked"]),
                "by_arm": totals,
                "effects": {
                    key: _delta(totals["masked"][key], totals["shown"][key])
                    for key in _COUNTS
                },
                "chair_variation": {
                    "different_choice": _count(sum(chair_choice), len(chair_choice)),
                    "different_action": _count(sum(chair_action), len(chair_action)),
                },
            }
        )
    measures = {
        key: [row["effects"][key] for row in maps if row["effects"][key] is not None]
        for key in _COUNTS
    }
    for key in ("different_choice", "different_action"):
        counts = [row["chair_variation"][key] for row in maps]
        measures[f"chair_{key}"] = [
            row["numerator"] / row["denominator"]
            for row in counts
            if row["denominator"]
        ]
    return {
        "estimand": "shown_minus_masked",
        "primary_measure": "native_majority",
        "weighting": "merge_complete_pairs_within_source_then_weight_sources_equally",
        "complete_pairs": sum(row["complete_pairs"] for row in maps),
        "pairs": pairs,
        "maps": maps,
        "effects": {
            key: mean(values) if len(values) == len(maps) else None
            for key, values in measures.items()
        },
        "partial_effects": {
            key: {"mean": mean(values) if values else None, "sources": len(values)}
            for key, values in measures.items()
        },
    }


def analyze(
    study: Path,
    *,
    packet_path: Path,
    expected_sha256: str,
    spend_dir: Path | None = None,
) -> _JsonRow:
    """Join the frozen packet, native trials, and original direct ledger."""
    study = study.absolute()
    analysis._path(study.parent, study.name, directory=True)
    sources = prefix_packet.load(packet_path, expected_sha256)
    ledger_status, ledger = _ledger(spend_dir)
    proofs: dict[str, _Evidence] = {}
    cells: list[_JsonRow] = []
    for cell in prefix_schedule.grid():
        index, cell_id = cell["map_index"], cell["cell_id"]
        checkpoint = sources[index]
        proof = proofs[cell_id] = _Evidence()
        calls = {
            key: row
            for key, row in ledger.items()
            if key[0] == cell_id or key[0].startswith(f"{cell_id}-")
        }
        settled = sum(row["estimated_usd_high"] is not None for row in calls.values())
        row: _JsonRow = cell | {
            "source_cell_id": f"rc-m{index}-r0-masked",
            "chair": native._SEATS[schedule.chair(index, 0)],
            "status": "source_ineligible",
            "stage": None,
            "reason": None,
            "prefix_exact": None,
            "cut_status_verified": None,
            "settled_direct_calls": settled if ledger_status == "available" else None,
            "unsettled_direct_calls": len(calls) - settled
            if ledger_status == "available"
            else None,
            "native_vote_reward": None,
            "seats": None,
            **dict.fromkeys(_COUNTS),
        }
        if checkpoint is not None:
            try:
                row |= _inspect(
                    study,
                    cell,
                    checkpoint,
                    expected_sha256,
                    ledger_status,
                    calls,
                    proof,
                )
                row["status"] = "scored"
            except native._Failure as exc:
                row["status"] = exc.status if exc.status in _STATUSES else "incomplete"
                row["stage"], row["reason"] = exc.stage, exc.reason
            row["prefix_exact"] = proof.prefix_exact
            row["cut_status_verified"] = proof.cut_status_verified
        cells.append(row)
    repeats = Counter(
        proof.trial_id for proof in proofs.values() if proof.trial_id is not None
    )
    for row in cells:
        trial_id = proofs[row["cell_id"]].trial_id
        if row["status"] == "scored" and trial_id is not None and repeats[trial_id] > 1:
            row.update(
                status="incomplete",
                stage="result",
                reason="duplicate_native_trial",
                native_vote_reward=None,
                seats=None,
            )
            row.update(dict.fromkeys(_COUNTS))
    by_arm = {}
    for arm in _ARMS:
        rows = [row for row in cells if row["arm"] == arm]
        scored = [row for row in rows if row["status"] == "scored"]
        by_arm[arm] = {
            "planned_cells": len(rows),
            "statuses": {
                status: sum(row["status"] == status for row in rows)
                for status in _STATUSES
            },
            "exact_prefix_matches": sum(row["prefix_exact"] is True for row in rows),
            "verified_cut_statuses": sum(
                row["cut_status_verified"] is True for row in rows
            ),
            "settled_direct_calls": (
                sum(row["settled_direct_calls"] for row in rows)
                if ledger_status == "available"
                else None
            ),
            "unsettled_direct_calls": (
                sum(row["unsettled_direct_calls"] for row in rows)
                if ledger_status == "available"
                else None
            ),
            **{key: _sum_counts(scored, key) for key in _COUNTS},
        }
    return {
        "schema": "seatlab.committee-prefix-report/v1",
        "task": analysis._TASK,
        "task_git_tree": schedule.TASK_TREE,
        "packet_sha256": expected_sha256,
        "spend_evidence": ledger_status,
        "response_evidence": "hash_commitment_only",
        "cells": cells,
        "contrast": _contrast(cells, proofs),
        "summary": {
            "planned_cells": len(cells),
            "eligible_sources": sum(
                checkpoint is not None for checkpoint in sources.values()
            ),
            "exact_prefix_matches": sum(row["prefix_exact"] is True for row in cells),
            "verified_cut_statuses": sum(
                row["cut_status_verified"] is True for row in cells
            ),
            "statuses": {
                status: sum(row["status"] == status for row in cells)
                for status in _STATUSES
            },
            "by_arm": by_arm,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--expect-sha256", required=True)
    parser.add_argument("--spend-dir", type=Path)
    args = parser.parse_args()
    try:
        report = analyze(
            args.study,
            packet_path=args.packet,
            expected_sha256=args.expect_sha256,
            spend_dir=args.spend_dir,
        )
    except (OSError, ValueError, RecursionError, analysis._Excluded):
        parser.error("the study root or bound packet is unavailable or invalid")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
