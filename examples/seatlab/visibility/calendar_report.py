"""Replay registered Calendar Feedback cells against stock artifacts and the direct ledger."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import stat
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean
from typing import Any, Never
from uuid import UUID

from harbor.models.task.task import Task
from harbor.models.trial.config import TrialConfig

from . import calendar_packet as packet
from . import calendar_seat as actor
from . import calendar_turns as turns
from . import spend
from .calendar_protocol import (
    Block,
    answer,
    blocks,
    decision_prompt,
    feedback_wire,
    recheck_prompt,
)

_Row = dict[str, Any]

_ARMS = ("blind", "peer")
_SEATS = ("p1", "p2")
_ROLES = {"p1": "cal_initial_p1", "p2": "cal_initial_p2"}
_USAGE = ("input_tokens", "cache_tokens", "output_tokens")
_ARTIFACTS = ["/logs/world/events.jsonl", "/logs/world/messages.jsonl"]
_WORLD = {
    "world_version": 2,
    "condition": "talk",
    "seats": list(_SEATS),
    "limits": {"max_messages": 200, "max_text": 4000},
}
_SECONDARY = (
    "sender_includes_target",
    "sender_exact_list",
    "sender_changed_list",
    "sender_false_positives",
    "sender_false_negatives",
    "receiver_own_feasible",
)
_IMPORT = "examples.seatlab.visibility.calendar_seat:CalendarSeat"


class _EvidenceError(ValueError):
    def __init__(self, reason: str, status: str = "invalid_evidence"):
        super().__init__(reason)
        self.status = status


def _check(ok: bool, reason: str) -> None:
    if not ok:
        raise _EvidenceError(reason)


def _bytes(path: Path, limit: int = 1_048_576) -> bytes:
    _check(path.resolve() == path, "nonphysical_evidence")
    info = path.lstat()
    _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "nonregular_evidence")
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    _check(len(raw) <= limit, "oversized_evidence")
    return raw


def _unique(pairs: list[tuple[str, Any]]) -> _Row:
    obj = dict(pairs)
    _check(len(obj) == len(pairs), "duplicate_json_key")
    return obj


def _not_json(_: str) -> Never:
    raise _EvidenceError("nonstandard_json")


def _object(raw: bytes) -> _Row:
    row = json.loads(raw, object_pairs_hook=_unique, parse_constant=_not_json)
    if not isinstance(row, dict):
        raise _EvidenceError("expected_object")
    return row


def _read(path: Path) -> _Row:
    return _object(_bytes(path))


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _json(value: Any) -> str:
    # A strict comparison must distinguish bool and int in raw evidence.
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _same(actual: Any, expected: Any, reason: str) -> None:
    _check(_json(actual) == _json(expected), reason)


def _timestamp(value: object) -> bool:
    return (type(value) is int or type(value) is float) and 0 <= value < math.inf


def _by_seat(rows: object) -> dict[str, _Row]:
    if not isinstance(rows, list) or len(rows) != 2:
        raise _EvidenceError("native_roster")
    seats: dict[str, _Row] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise _EvidenceError("native_roster")
        seat = row.get("seat")
        if not isinstance(seat, str) or seat not in _SEATS or seat in seats:
            raise _EvidenceError("native_roster")
        seats[seat] = row
    return seats


def _stock_source(path: Path, world: packet.World) -> None:
    packet.require_native_task_checksum(path, world.task_checksum)
    task = Task(path)
    _check(task.name == f"flotilla/schedule-{world.index:03d}-talk", "stock_task_name")
    _check(
        set(task.config.agents) == set(_SEATS)
        and all(seat.under_test for seat in task.config.agents.values()),
        "stock_task_roster",
    )
    _same(
        task.config.model_dump(mode="json")["artifacts"],
        _ARTIFACTS,
        "stock_artifact_policy",
    )
    _check(packet.source_tables(path, world.index) == world.tables, "stock_task_tables")


def _sources(
    study: Path,
    trial: Path,
    native: _Row,
    block: Block,
    arm: str,
    world: packet.World,
    seeds: dict[str, tuple[int, ...]],
) -> tuple[dict[str, _Row], tuple[datetime, datetime]]:
    cell = trial.parent.parent
    run = f"{block.id}-{arm}"
    sealed_raw = _bytes(cell / "config.json")
    seal = _read(cell / "attempt.json")
    _same(
        seal,
        {
            "schema": "seatlab.calendar-attempt/v1",
            "mode": "run",
            "run": run,
            "block": block.id,
            "world": block.world,
            "arm": arm,
            "sender": block.sender,
            "receiver": block.receiver,
            "trial_name": trial.name,
            "packet_sha256": packet.PACKET_SHA256,
            "task_checksum": world.task_checksum,
            "config_sha256": _sha(sealed_raw),
            "seed_sha256": {
                seat: _sha(_bytes(study / "seeds" / block.id / f"{seat}.json"))
                for seat in _SEATS
            },
        },
        "attempt_binding",
    )
    sealed = _object(sealed_raw)
    source = Path(sealed["task"]["path"])
    _check(
        source.is_absolute()
        and source.resolve() == source
        and source.name == f"schedule-{world.index:03d}-talk",
        "stock_task_path",
    )
    expected = {
        "trial_name": trial.name,
        "trials_dir": str(trial.parent),
        "task": {"path": str(source)},
        "environment": {"type": "docker", "force_build": True},
        "verifier": {"disable": False},
        "participants": [
            {
                "seat": seat,
                "import_path": _IMPORT,
                "model_name": actor.DECLARED_MODEL,
                "kwargs": {
                    "world": block.world,
                    "direction": block.direction,
                    "repetition": block.repetition,
                    "arm": arm,
                    "seat": seat,
                    "own_starts": list(seeds[seat]),
                    "control": False,
                },
            }
            for seat in _SEATS
        ],
    }
    # Compare against native defaults as well as the registered fields, so
    # neither copy can add a verifier, environment, context, or actor override.
    expected = TrialConfig.model_validate(expected).model_dump(mode="json")
    _same(
        TrialConfig.model_validate(sealed).model_dump(mode="json"),
        expected,
        "compiled_trial_policy",
    )
    config = native["config"]
    _same(config, expected, "native_trial_policy")
    _same(
        _read(trial / "config.json"),
        {key: value for key, value in config.items() if key != "agent"},
        "native_config_copy",
    )
    for key, value in {
        "schema_version": 1,
        "task_checksum": world.task_checksum,
        "trial_name": trial.name,
        "trial_uri": trial.as_uri(),
        "task_id": {"path": str(source)},
        "task_name": f"flotilla/schedule-{world.index:03d}-talk",
        "source": None,
        "verifier_environment_mode": "shared",
    }.items():
        _same(native[key], value, "native_identity")
    _stock_source(source, world)
    if native["exception_info"] is not None:
        raise _EvidenceError("native_trial_exception", "native_failure")
    finished_at = native.get("finished_at")
    if finished_at is None:
        raise _EvidenceError("native_unfinished", "missing_evidence")
    start = datetime.fromisoformat(native["started_at"])
    finish = datetime.fromisoformat(finished_at)
    _check(
        start.utcoffset() is not None
        and finish.utcoffset() is not None
        and start <= finish,
        "native_time",
    )
    provider, name = actor.DECLARED_MODEL.split("/", 1)
    identity = {
        "name": actor.AGENT_NAME,
        "version": actor.AGENT_VERSION,
        "model_info": {"name": name, "provider": provider},
    }
    _same(native["agent_info"], identity, "native_agent_identity")
    contexts: dict[str, _Row] = {}
    for seat, row in _by_seat(native["seat_results"]).items():
        _check(row["under_test"] is True, "native_roster")
        _same(row["agent_info"], identity, "native_agent_identity")
        if row["exception_info"] is not None:
            raise _EvidenceError("native_seat_exception", "native_failure")
        context = row.get("agent_result")
        if context is None:
            raise _EvidenceError("native_seat_context_absent", "missing_evidence")
        if not isinstance(context, dict):
            raise _EvidenceError("native_seat_context_shape")
        contexts[seat] = context
    return contexts, (start, finish)


def _receipt_path(trial: Path, seat: str) -> Path:
    return trial / "seats" / seat / "agent" / actor.TURN_FILE


def _turn(
    trial: Path,
    seat: str,
    context: _Row,
    prompt: str,
    run: str,
    role: str,
    ledger: _Row,
) -> tuple[tuple[int, ...] | int | None, _Row, str]:
    row = _read(_receipt_path(trial, seat))
    prompt_hash = _sha(prompt.encode())
    _same(
        context["metadata"]["calendar"]["own_prompt_sha256"], prompt_hash, "seat_prompt"
    )
    try:
        value = turns.verify_turn(row, prompt, run, role, ledger)
    except turns.TurnError as exc:
        if exc.category == "model_invalid":
            value = None
        elif exc.category in {"ledger_missing", "ledger_unsettled"}:
            raise _EvidenceError(exc.category, "missing_evidence") from None
        elif exc.category == "turn_failed":
            raise _EvidenceError(exc.category, "native_failure") from None
        else:
            raise _EvidenceError(exc.category) from None
    usage = {key: row[key] for key in _USAGE}
    _check(
        all(type(count) is int and count >= 0 for count in usage.values()),
        "receipt_usage",
    )
    _same(
        {f"n_{key}": usage[key] for key in _USAGE},
        {f"n_{key}": context[f"n_{key}"] for key in _USAGE},
        "native_seat_usage",
    )
    return value, usage, prompt_hash


def _timeline(
    trial: Path,
    block: Block,
    wires: tuple[str, str],
    slot: int | None,
) -> None:
    verifier = trial / "verifier"
    state = _read(verifier / "world-state.json")
    messages = state["messages"]
    submissions = state["submissions"]
    _same(
        state,
        {
            **_WORLD,
            "frozen": True,
            "sent": dict.fromkeys(_SEATS, 1),
            "messages": messages,
            "submissions": submissions,
        },
        "frozen_world",
    )
    _check(isinstance(messages, list) and len(messages) == 2, "frozen_messages")
    for n, (msg, wire, sender, recipient) in enumerate(
        zip(
            messages,
            wires,
            (block.receiver, block.sender),
            (block.sender, block.receiver),
            strict=True,
        ),
        1,
    ):
        _check(_timestamp(msg["t"]), "message_time")
        _same(
            msg,
            {
                "id": f"m{n}",
                "from": sender,
                "to": recipient,
                "text": wire,
                "t": msg["t"],
            },
            "stock_wire",
        )
    _check(
        isinstance(submissions, list) and len(submissions) == int(slot is not None),
        "frozen_submission",
    )
    if slot is not None:
        submission = submissions[0]
        _check(_timestamp(submission["t"]), "submission_time")
        _same(
            submission,
            {
                "n": 1,
                "seat": block.receiver,
                "answer": answer(slot),
                "t": submission["t"],
            },
            "frozen_submission",
        )

    world = trial / "artifacts/logs/world"
    logged_messages = [
        _object(line) for line in _bytes(world / "messages.jsonl").splitlines()
    ]
    _same(logged_messages, messages, "message_log_frozen_mismatch")
    sent = 0
    delivered: set[str] = set()
    identified: set[str] = set()
    submitted = False
    previous_t = 0
    # Stock Harbor snapshots main artifacts before running the verifier. The
    # verifier's world-state.json, not the event log, must prove the freeze.
    events = _bytes(world / "events.jsonl").splitlines()
    for seq, line in enumerate(events, 1):
        event = _object(line)
        _check(event.keys() == {"seq", "t", "seat", "kind", "data"}, "event_shape")
        kind, seat, t, data = (event[k] for k in ("kind", "seat", "t", "data"))
        _same(event["seq"], seq, "event_sequence")
        _check(_timestamp(t) and t >= previous_t, "event_time")
        if seq == 1:
            _same((kind, seat, data), ("start", None, _WORLD), "event_start")
        elif kind == "health":
            _same((seat, data), (None, {}), "event_health")
        elif kind == "whoami":
            _check(
                seat in _SEATS and seat not in identified and data == {},
                "event_identity",
            )
            identified.add(seat)
        else:
            _check(seat in identified and not submitted, "event_actor_or_order")
            if kind == "send":
                _check(sent < 2 and (sent == 0 or "m1" in delivered), "send_chronology")
                msg = messages[sent]
                _check(previous_t <= msg["t"] <= t, "message_time")
                _same(
                    (seat, data),
                    (
                        msg["from"],
                        {
                            "id": msg["id"],
                            "to": msg["to"],
                            "chars": len(msg["text"]),
                        },
                    ),
                    "send_event",
                )
                sent += 1
            elif kind == "deliver":
                # Stock GET /messages logs incoming IDs only, not the actor's own wire.
                ids = [msg["id"] for msg in messages[:sent] if msg["to"] == seat]
                _same(data, {"ids": ids}, "delivery_event")
                delivered.update(ids)
            elif kind == "submit":
                if slot is None or submitted or "m2" not in delivered:
                    raise _EvidenceError("submit_chronology")
                submission = submissions[0]
                _check(previous_t <= submission["t"] <= t, "submission_time")
                _same(
                    (seat, data),
                    (block.receiver, {"n": 1, "answer": answer(slot)}),
                    "submit_event",
                )
                submitted = True
            else:
                raise _EvidenceError("unexpected_stock_event")
        previous_t = t
    _check(
        sent == 2 and delivered == {"m1", "m2"} and submitted == (slot is not None),
        "incomplete_timeline",
    )


def _verify_reward(
    trial: Path, native: _Row, block: Block, slot: int | None, target: int
) -> int:
    score = _read(trial / "verifier/score.json")
    reward = _read(trial / "verifier/reward.json")
    verifier = native.get("verifier_result")
    if verifier is None:
        raise _EvidenceError("native_verifier_absent", "missing_evidence")
    native_reward = verifier["rewards"]
    if native_reward is None:
        raise _EvidenceError("native_reward_absent", "missing_evidence")
    _check(native["reward_valid"] is True, "native_reward_unusable")
    # Validate the stock reward; never substitute this independent calendar check for it.
    won = int(slot == target)
    for value in (reward["reward"], native_reward["reward"], score["score"]):
        _check(type(value) in (int, float) and value == won, "stock_reward_mismatch")
    _check(reward.keys() == native_reward.keys() == {"reward"}, "stock_reward_shape")
    _same(
        {key: value for key, value in score.items() if key != "score"},
        {
            "deceived": None,
            "error": None,
            "family": "schedule",
            "instance": block.world,
            "condition": "talk",
            "seats": list(_SEATS),
            "world_version": 2,
            "fields_correct": won,
            "fields_total": 1,
            "answer": answer(slot) if slot is not None else None,
            "submitted_by": block.receiver if slot is not None else None,
            "n_submissions": int(slot is not None),
            "n_messages": 2,
        },
        "stock_score_mismatch",
    )
    return int(reward["reward"])


def _branch(
    study: Path,
    trial: Path,
    native: _Row,
    block: Block,
    arm: str,
    world: packet.World,
    seeds: dict[str, tuple[int, ...]],
    ledger: _Row,
    call_roles: list[str],
) -> _Row:
    contexts, interval = _sources(study, trial, native, block, arm, world, seeds)
    run = f"{block.id}-{arm}"
    sender, receiver = block.sender, block.receiver
    sctx, rctx = contexts[sender], contexts[receiver]
    st, rt = (world.tables[_SEATS.index(seat)] for seat in (sender, receiver))
    sender_starts, receiver_starts = set(packet.starts(st)), set(packet.starts(rt))
    (target,) = sender_starts & receiver_starts
    feedback = seeds[receiver] if arm == "peer" else None
    svalue, susage, sprompt = _turn(
        trial,
        sender,
        sctx,
        recheck_prompt(st, seeds[sender], feedback),
        run,
        "cal_recheck",
        ledger,
    )
    if svalue is None:
        _check(call_roles == ["cal_recheck"], "unexpected_branch_calls")
        for key in _USAGE:
            count = rctx.get(f"n_{key}")
            _check(
                count is None or (type(count) is int and count == 0),
                "unexpected_seat_usage",
            )
        absent = _receipt_path(trial, receiver)
        _check(
            absent.resolve() == absent and not absent.exists(),
            "unexpected_turn_receipt",
        )
        slot, rprompt, rusage = None, None, None
        sender_wire = actor.INVALID_WIRE
    else:
        if not isinstance(svalue, tuple):
            raise _EvidenceError("sender_value")
        sender_wire = feedback_wire(svalue)
        rvalue, rusage, rprompt = _turn(
            trial,
            receiver,
            rctx,
            decision_prompt(rt, seeds[receiver], svalue),
            run,
            "cal_decide",
            ledger,
        )
        _check(
            sorted(call_roles) == ["cal_decide", "cal_recheck"],
            "unexpected_branch_calls",
        )
        if rvalue is not None and type(rvalue) is not int:
            raise _EvidenceError("receiver_value")
        slot = rvalue
    stages = (
        "model_invalid" if svalue is None else "complete",
        "peer_model_invalid"
        if svalue is None
        else "model_invalid"
        if slot is None
        else "complete",
    )
    for seat, table, stage, prompt_hash, incoming, outgoing in (
        (sender, st, stages[0], sprompt, "m1", "m2"),
        (receiver, rt, stages[1], rprompt, "m2", "m1"),
    ):
        _same(
            contexts[seat]["metadata"]["calendar"],
            {
                "schema": "seatlab.calendar-seat/v1",
                "run": run,
                "seat": seat,
                "control": False,
                "stage": stage,
                "own_table_sha256": _sha(_json(table).encode()),
                "own_prompt_sha256": prompt_hash,
                "incoming_id": incoming,
                "outgoing_id": outgoing,
            },
            "seat_transcript_binding",
        )
    _timeline(trial, block, (feedback_wire(feedback), sender_wire), slot)
    reward = _verify_reward(trial, native, block, slot, target)
    _same(
        _read(trial.parent.parent / "outcome.json"),
        {
            "schema": "seatlab.calendar-launch/v1",
            "run": run,
            "returncode": 0,
            "error": None,
            "status": "complete" if slot is not None else "invalid_model",
        },
        "launch_outcome_binding",
    )
    revised = set(svalue) if svalue is not None else None
    return {
        "status": "complete" if slot is not None else "model_invalid_scored_zero",
        "invalid_seat": sender
        if svalue is None
        else receiver
        if slot is None
        else None,
        "reward": reward,
        "slot": slot,
        "tokens": {sender: susage, receiver: rusage},
        "sender_includes_target": target in revised if revised is not None else None,
        "sender_exact_list": revised == sender_starts if revised is not None else None,
        "sender_changed_list": revised != set(seeds[sender])
        if revised is not None
        else None,
        "sender_false_positives": len(revised - sender_starts)
        if revised is not None
        else None,
        "sender_false_negatives": len(sender_starts - revised)
        if revised is not None
        else None,
        "receiver_own_feasible": slot in receiver_starts if slot is not None else None,
        "_interval": interval,
        "_sender_wire": sender_wire,
        "_final_prompt": rprompt,
    }


def _failure(exc: Exception) -> _Row:
    if isinstance(exc, _EvidenceError):
        return {"status": exc.status, "reason": str(exc)}
    if isinstance(exc, FileNotFoundError):
        return {"status": "missing_evidence", "reason": "required_file_absent"}
    if isinstance(exc, OSError):
        return {"status": "missing_evidence", "reason": "required_file_unreadable"}
    return {"status": "invalid_evidence", "reason": "invalid_native_evidence"}


_BAD_EVIDENCE = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    RecursionError,
)


def _pair(arms: dict[str, _Row]) -> _Row:
    blind, peer = (arms[arm] for arm in _ARMS)
    lo, hi = (blind.get("reward"), peer.get("reward"))
    same_delivery = (
        blind["_sender_wire"] == peer["_sender_wire"]
        if "_sender_wire" in blind
        and "_sender_wire" in peer
        and blind["_sender_wire"] != actor.INVALID_WIRE
        and peer["_sender_wire"] != actor.INVALID_WIRE
        else None
    )
    blind_prompt, peer_prompt = (blind.get("_final_prompt"), peer.get("_final_prompt"))
    same_prompt = (
        blind_prompt == peer_prompt
        if blind_prompt is not None and peer_prompt is not None
        else None
    )
    if same_delivery is not None:
        _check(same_delivery == same_prompt, "paired_final_prompt_binding")
    accepted = blind.get("slot") is not None and peer.get("slot") is not None
    delta = hi - lo if lo is not None and hi is not None else None
    metrics = {}
    for key in _SECONDARY:
        left, right = blind.get(key), peer.get(key)
        metrics[key] = right - left if left is not None and right is not None else None
    return {
        "status": "complete"
        if lo is not None and hi is not None
        else "partial"
        if any(arm["status"] != "unattempted" for arm in arms.values())
        else "unattempted",
        "peer_minus_blind": delta,
        "accepted_peer_minus_blind": delta if accepted else None,
        "bounds": [
            (hi if hi is not None else 0) - (lo if lo is not None else 1),
            (hi if hi is not None else 1) - (lo if lo is not None else 0),
        ],
        "secondary_peer_minus_blind": metrics,
        "same_delivered_sender": same_delivery,
        "same_final_prompt": same_prompt,
        "same_prompt_decision_changed": (
            blind["slot"] != peer["slot"]
            if same_delivery is True and same_prompt is True and accepted
            else None
        ),
    }


def _equal_world(groups: list[list[float]]) -> _Row:
    means = [mean(group) if group else None for group in groups]
    supported = [value for value in means if value is not None]
    return {
        "pairs": sum(map(len, groups)),
        "worlds": len(supported),
        "world_pair_counts": list(map(len, groups)),
        "world_means": means,
        "equal_world_mean": mean(supported) if len(supported) == len(groups) else None,
    }


def _summary(rows: list[_Row], world_count: int) -> _Row:
    per_world = [
        [row for row in rows if row["world"] == index] for index in range(world_count)
    ]

    def paired(field: str) -> _Row:
        return _equal_world(
            [
                [int(row[field]) for row in group if row[field] is not None]
                for group in per_world
            ]
        )

    return {
        "registered_pairs": len(rows),
        "primary": paired("peer_minus_blind"),
        "accepted_pairs_only": paired("accepted_peer_minus_blind"),
        "all_registered_pair_bounds": [
            mean(mean(row["bounds"][end] for row in group) for group in per_world)
            for end in (0, 1)
        ],
        "secondary": {
            key: _equal_world(
                [
                    [
                        row["secondary_peer_minus_blind"][key]
                        for row in group
                        if row["secondary_peer_minus_blind"][key] is not None
                    ]
                    for group in per_world
                ]
            )
            for key in _SECONDARY
        },
        "identical_delivery": {
            "comparable_pairs": sum(
                row["same_delivered_sender"] is not None for row in rows
            ),
            "same_sender_wire_pairs": sum(
                row["same_delivered_sender"] is True for row in rows
            ),
            "different_sender_wire_pairs": sum(
                row["same_delivered_sender"] is False for row in rows
            ),
            "same_final_prompt_pairs": sum(
                row["same_final_prompt"] is True for row in rows
            ),
            "decision_variation": paired("same_prompt_decision_changed"),
            "different_decisions": sum(
                row["same_prompt_decision_changed"] is True for row in rows
            ),
        },
        "arms": {
            arm: {
                "registered": len(rows),
                "status_counts": dict(
                    Counter(row["arms"][arm]["status"] for row in rows)
                ),
                "verified_rewards": sum("reward" in row["arms"][arm] for row in rows),
                "primary_paired_reward": _equal_world(
                    [
                        [
                            row["arms"][arm]["reward"]
                            for row in group
                            if row["peer_minus_blind"] is not None
                        ]
                        for group in per_world
                    ]
                ),
                "wins": sum(row["arms"][arm].get("reward", 0) for row in rows),
                "tokens": {
                    token: sum(
                        usage[token]
                        for row in rows
                        for usage in row["arms"][arm].get("tokens", {}).values()
                        if usage is not None
                    )
                    for token in _USAGE
                },
            }
            for arm in _ARMS
        },
    }


def _seed_usage(call: _Row | None) -> _Row | None:
    if call is None or call.get("estimated_usd_high") is None:
        return None
    return {
        "input_tokens": call["cache_hit"] + call["cache_miss"],
        "cache_tokens": call["cache_hit"],
        "output_tokens": call["output"],
    }


def analyze(study: Path, spend_dir: Path) -> _Row:
    """Produce an offline report; only independently verified outcomes contribute."""
    study = study.resolve(strict=True)
    worlds = packet.load(packet.PACKET, packet.PACKET_SHA256)
    registered = blocks()
    ledger = spend.report(spend_dir, run_prefix="cf-")
    roles: dict[str, list[str]] = {}
    calls: dict[tuple[str, str], _Row] = {}
    for call in ledger["calls"]:
        roles.setdefault(call["run"], []).append(call["role"])
        calls[(call["run"], call["role"])] = call

    # Census before replay, so a UUID reused by even an incomplete cell invalidates both.
    native_trials: dict[str, tuple[Path, _Row | None, UUID | None, _Row | None]] = {}
    ids: Counter[UUID] = Counter()
    for block in registered:
        for arm in _ARMS:
            run = f"{block.id}-{arm}"
            cell = study / "cells" / run
            trial = cell / "trials" / f"calendar-{run}"
            try:
                _check(trial.resolve() == trial, "nonphysical_trial")
                native = _read(trial / "result.json")
                uid = UUID(native["id"])
                ids[uid] += 1
                native_trials[run] = trial, native, uid, None
            except _BAD_EVIDENCE as exc:
                failure = _failure(exc)
                if (
                    isinstance(exc, FileNotFoundError)
                    and not cell.exists()
                    and run not in roles
                ):
                    failure = {"status": "unattempted"}
                native_trials[run] = trial, None, None, failure

    rows: list[_Row] = []
    for block in registered:
        world = worlds[block.world]
        try:
            seed_values = dict(
                zip(
                    _SEATS,
                    turns.load_seed_pair(study, block, world, spend_dir),
                    strict=True,
                )
            )
            seed_status = dict.fromkeys(_SEATS, "accepted")
            seed_ok = sorted(roles.get(block.id, [])) == sorted(_ROLES.values())
            if not seed_ok:
                seed_status = dict.fromkeys(_SEATS, "unexpected_seed_calls")
        except turns.SeedPairError as exc:
            seed_values = exc.values
            seed_status = {seat: exc.issues.get(seat, "accepted") for seat in _SEATS}
            seed_ok = False
        except turns.TurnError as exc:
            seed_values = {}
            seed_status = dict.fromkeys(_SEATS, exc.category)
            seed_ok = False
        arms: dict[str, _Row] = {}
        for arm in _ARMS:
            run = f"{block.id}-{arm}"
            trial, native, uid, failure = native_trials[run]
            if uid is not None and ids[uid] != 1:
                arms[arm] = {
                    "status": "invalid_evidence",
                    "reason": "duplicate_trial_uuid",
                }
            elif failure is not None:
                arms[arm] = failure
            elif not seed_ok:
                arms[arm] = {
                    "status": "missing_evidence",
                    "reason": "seed_pair_unavailable",
                }
            else:
                try:
                    if native is None:
                        raise _EvidenceError("native_result_absent", "missing_evidence")
                    _check(
                        {p.name for p in trial.parent.iterdir()} == {trial.name},
                        "extra_native_trial",
                    )
                    arms[arm] = _branch(
                        study,
                        trial,
                        native,
                        block,
                        arm,
                        world,
                        seed_values,
                        ledger,
                        roles.get(run, []),
                    )
                except _BAD_EVIDENCE as exc:
                    arms[arm] = _failure(exc)
        first, second = (arms[arm].get("_interval") for arm in block.arms)
        if first is not None and second is not None and first[1] > second[0]:
            arms = {
                arm: {"status": "invalid_evidence", "reason": "registered_arm_order"}
                for arm in _ARMS
            }
        pair = _pair(arms)
        if pair["status"] == "unattempted" and (
            (study / "seeds" / block.id).exists() or block.id in roles
        ):
            pair["status"] = "partial"
        for arm in arms.values():
            arm.pop("_sender_wire", None)
            arm.pop("_final_prompt", None)
            arm.pop("_interval", None)
        rows.append(
            {
                "block": block.id,
                "world": block.world,
                "direction": block.direction,
                "repetition": block.repetition,
                "arm_order": list(block.arms),
                "seeds": {
                    seat: {
                        "status": seed_status[seat],
                        "tokens": _seed_usage(calls.get((block.id, _ROLES[seat])))
                        if seed_status[seat] in {"accepted", "model_invalid"}
                        else None,
                        "exact_list": seed_values[seat]
                        == packet.starts(world.tables[i])
                        if seat in seed_values
                        else None,
                    }
                    for i, seat in enumerate(_SEATS)
                },
                "arms": arms,
                **pair,
            }
        )
    return {
        "schema": "seatlab.calendar-feedback-report/v1",
        "blocks": rows,
        "summary": _summary(rows, len(worlds)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--spend-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = analyze(args.study, args.spend_dir)
    except _BAD_EVIDENCE + (sqlite3.Error,):
        parser.exit(
            1, "Calendar report could not verify its study, packet, or ledger.\n"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
