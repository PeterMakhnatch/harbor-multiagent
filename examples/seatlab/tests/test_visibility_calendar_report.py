from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from examples.seatlab.visibility import calendar_report as report
from examples.seatlab.visibility import spend
from examples.seatlab.visibility.calendar_packet import World
from examples.seatlab.visibility.calendar_protocol import (
    Block,
    answer,
    decision_prompt,
    feedback_wire,
    initial_prompt,
    recheck_prompt,
)
from harbor.models.trial.config import TrialConfig

_Row = dict[str, Any]

_SEATS = ("p1", "p2")
_ZERO = (0, 0, 0, 0, 0)
_TABLES = (
    ((240, 255, 255, 255, 255), _ZERO, _ZERO),
    ((249, 255, 255, 255, 255), _ZERO, _ZERO),
)
_INITIAL = ((0, 1, 2), (1,))
_MODEL = "DeepSeek-V4.1-Flash-1017"
_BALANCE = Decimal("3.00")
_NOW = datetime(2026, 11, 20, tzinfo=UTC)
_USAGE = {"input_tokens": 12, "cache_tokens": 4, "output_tokens": 8}
_ROSTER = {
    "world_version": 2,
    "condition": "talk",
    "seats": list(_SEATS),
    "limits": {"max_messages": 200, "max_text": 4000},
}


def _hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _read(path: Path) -> _Row:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _lines(path: Path, rows: Iterable[_Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class _Study:
    def __init__(self, root: Path):
        self.root = root
        self.ledger = root / "ledger"
        self.worlds = tuple(World(i, str(i) * 64, _TABLES) for i in range(2))
        self.blocks = tuple(Block(w, d, 0) for w in range(2) for d in range(2))

    def measure(
        self, path: Path, run: str, role: str, prompt: str, value: object
    ) -> None:
        key = "slot" if role == "cal_decide" else "starts"
        text = json.dumps({key: value}) if value is not None else "invalid model text"
        spend.reserve(run, role, balance_usd=_BALANCE)
        spend.settle(
            run,
            role,
            cache_hit=4,
            cache_miss=8,
            output=8,
            served_model=_MODEL,
            started_at=_NOW,
            finished_at=_NOW + timedelta(seconds=1),
        )
        _json(
            path,
            {
                "schema": "seatlab.calendar-turn/v1",
                "run": run,
                "role": role,
                "prompt_sha256": _hash(prompt.encode()),
                "served_model": _MODEL,
                "status": "invalid" if value is None else "accepted",
                "reason": "action_invalid" if value is None else None,
                **_USAGE,
                "response_sha256": _hash(text.encode()),
                "response_text": text,
                "value": value,
            },
        )
        path.chmod(0o600)

    def seed(self, block: Block, *, invalid: str | None = None) -> Path:
        (self.root / "seeds").mkdir(mode=0o700, exist_ok=True)
        directory = self.root / "seeds" / block.id
        directory.mkdir(mode=0o700)
        for i, seat in enumerate(_SEATS):
            self.measure(
                directory / f"{seat}.json",
                block.id,
                f"cal_initial_{seat}",
                initial_prompt(_TABLES[i]),
                list(_INITIAL[i]) if seat != invalid else None,
            )
        return directory

    def arm(
        self,
        block: Block,
        arm: str,
        slot: int | None,
        *,
        revised: tuple[int, ...] | None = (1,),
    ) -> Path:
        run = f"{block.id}-{arm}"
        cell = self.root / "cells" / run
        trial = cell / "trials" / f"calendar-{run}"
        world = self.worlds[block.world]
        source = self.root / f"schedule-{block.world:03d}-talk"
        config = {
            "trial_name": trial.name,
            "trials_dir": str(trial.parent),
            "task": {"path": str(source)},
            "environment": {"type": "docker", "force_build": True},
            "participants": [
                {
                    "seat": seat,
                    "import_path": "examples.seatlab.visibility.calendar_seat:CalendarSeat",
                    "model_name": "deepseek/deepseek-flash",
                    "kwargs": {
                        "world": block.world,
                        "direction": block.direction,
                        "repetition": 0,
                        "arm": arm,
                        "seat": seat,
                        "control": False,
                        "own_starts": list(_INITIAL[i]),
                    },
                }
                for i, seat in enumerate(_SEATS)
            ],
        }
        _json(cell / "config.json", config)
        _json(
            cell / "attempt.json",
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
                "packet_sha256": report.packet.PACKET_SHA256,
                "task_checksum": world.task_checksum,
                "config_sha256": _hash((cell / "config.json").read_bytes()),
                "seed_sha256": {
                    seat: _hash(
                        (self.root / "seeds" / block.id / f"{seat}.json").read_bytes()
                    )
                    for seat in _SEATS
                },
            },
        )
        config_dump = TrialConfig.model_validate(config).model_dump(mode="json")
        _json(
            trial / "config.json",
            {k: v for k, v in config_dump.items() if k != "agent"},
        )
        identity = {
            "name": "seatlab-calendar-feedback",
            "version": "1.0.0",
            "model_info": {"provider": "deepseek", "name": "deepseek-flash"},
        }
        sender, receiver = block.sender, block.receiver
        si, ri = _SEATS.index(sender), _SEATS.index(receiver)
        feedback = _INITIAL[ri] if arm == "peer" else None
        recheck = recheck_prompt(_TABLES[si], _INITIAL[si], feedback)
        decide = (
            decision_prompt(_TABLES[ri], _INITIAL[ri], revised)
            if revised is not None
            else None
        )
        seat_results = []
        for seat, i, role, prompt, value, incoming, outgoing in (
            (
                sender,
                si,
                "cal_recheck",
                recheck,
                list(revised) if revised is not None else None,
                "m1",
                "m2",
            ),
            (receiver, ri, "cal_decide", decide, slot, "m2", "m1"),
        ):
            if prompt is not None:
                self.measure(
                    trial / "seats" / seat / "agent/calendar-turn.json",
                    run,
                    role,
                    prompt,
                    value,
                )
            seat_results.append(
                {
                    "seat": seat,
                    "under_test": True,
                    "agent_info": identity,
                    "exception_info": None,
                    "agent_result": {
                        **{
                            f"n_{key}": n if prompt is not None else None
                            for key, n in _USAGE.items()
                        },
                        "metadata": {
                            "calendar": {
                                "schema": "seatlab.calendar-seat/v1",
                                "run": run,
                                "seat": seat,
                                "control": False,
                                "stage": "peer_model_invalid"
                                if prompt is None
                                else "model_invalid"
                                if value is None
                                else "complete",
                                "own_table_sha256": _hash(
                                    json.dumps(
                                        _TABLES[i], separators=(",", ":")
                                    ).encode()
                                ),
                                "own_prompt_sha256": _hash(prompt.encode())
                                if prompt is not None
                                else None,
                                "incoming_id": incoming,
                                "outgoing_id": outgoing,
                            }
                        },
                    },
                }
            )
        reward = float(slot == 1)
        submission = (
            {"n": 1, "seat": receiver, "answer": answer(slot), "t": 8.0}
            if slot is not None
            else None
        )
        starts_at = _NOW + timedelta(minutes=block.arms.index(arm))
        _json(
            trial / "result.json",
            {
                "schema_version": 1,
                "id": str(uuid4()),
                "task_checksum": world.task_checksum,
                "trial_name": trial.name,
                "trial_uri": trial.as_uri(),
                "task_id": {"path": str(source)},
                "task_name": f"flotilla/schedule-{block.world:03d}-talk",
                "source": None,
                "verifier_environment_mode": "shared",
                "config": config_dump,
                "agent_info": identity,
                "seat_results": seat_results,
                "reward_valid": True,
                "exception_info": None,
                "started_at": starts_at.isoformat(),
                "finished_at": (starts_at + timedelta(seconds=15)).isoformat(),
                "verifier_result": {"rewards": {"reward": reward}},
            },
        )
        messages = [
            {
                "id": "m1",
                "from": receiver,
                "to": sender,
                "text": feedback_wire(feedback),
                "t": 4.0,
            },
            {
                "id": "m2",
                "from": sender,
                "to": receiver,
                "t": 6.0,
                "text": feedback_wire(revised)
                if revised is not None
                else '{"model_invalid":true}',
            },
        ]
        events = []
        for i, (kind, seat, data) in enumerate(
            [
                ("start", None, _ROSTER),
                ("whoami", sender, {}),
                ("whoami", receiver, {}),
                (
                    "send",
                    receiver,
                    {"id": "m1", "to": sender, "chars": len(messages[0]["text"])},
                ),
                ("deliver", sender, {"ids": ["m1"]}),
                (
                    "send",
                    sender,
                    {"id": "m2", "to": receiver, "chars": len(messages[1]["text"])},
                ),
                ("deliver", receiver, {"ids": ["m2"]}),
            ]
            + (
                [("submit", receiver, {"n": 1, "answer": submission["answer"]})]
                if submission is not None
                else []
            ),
            1,
        ):
            events.append(
                {"seq": i, "t": float(i), "kind": kind, "seat": seat, "data": data}
            )
        _lines(trial / "artifacts/logs/world/events.jsonl", events)
        _lines(trial / "artifacts/logs/world/messages.jsonl", messages)
        _json(
            trial / "verifier/world-state.json",
            {
                **_ROSTER,
                "frozen": True,
                "sent": {"p1": 1, "p2": 1},
                "messages": messages,
                "submissions": [submission] if submission is not None else [],
            },
        )
        _json(trial / "verifier/reward.json", {"reward": reward})
        _json(
            trial / "verifier/score.json",
            {
                "deceived": None,
                "error": None,
                "family": "schedule",
                "instance": block.world,
                "condition": "talk",
                "seats": list(_SEATS),
                "world_version": 2,
                "fields_correct": int(reward),
                "fields_total": 1,
                "score": reward,
                "answer": submission["answer"] if submission is not None else None,
                "submitted_by": receiver if submission is not None else None,
                "n_submissions": int(submission is not None),
                "n_messages": 2,
            },
        )
        _json(
            cell / "outcome.json",
            {
                "schema": "seatlab.calendar-launch/v1",
                "run": run,
                "returncode": 0,
                "error": None,
                "status": "complete" if submission is not None else "invalid_model",
            },
        )
        return trial

    def collect(self) -> _Row:
        return report.analyze(self.root, self.ledger)


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Study:
    root = tmp_path.resolve() / "study"
    root.mkdir(mode=0o700)
    fixture = _Study(root)
    fixture.ledger.mkdir(mode=0o700)
    spend.initialize(fixture.ledger, _BALANCE, Decimal("1.00"))
    monkeypatch.setattr(spend, "_HOST_LEDGER", fixture.ledger)
    # Use synthetic tables and pinned task IDs, not the private archive. Replay
    # the real stock-shaped artifacts, registered prompts, and SQLite ledger.
    monkeypatch.setattr(report.packet, "load", lambda *_: fixture.worlds)
    monkeypatch.setattr(report, "_stock_source", lambda *_: None)
    monkeypatch.setattr(report, "blocks", lambda: fixture.blocks)
    return fixture


def test_equal_world_and_same_verified_final_prompt(study: _Study) -> None:
    for block, blind, peer in zip(study.blocks[:3], (0, 0, 1), (1, 1, 0), strict=True):
        study.seed(block)
        study.arm(block, "blind", blind)
        study.arm(block, "peer", peer)
    result = study.collect()
    summary = result["summary"]
    assert summary["primary"] == {
        "pairs": 3,
        "worlds": 2,
        "world_pair_counts": [2, 1],
        "world_means": [1, -1],
        "equal_world_mean": 0,
    }
    assert summary["all_registered_pair_bounds"] == [0, 0.5]
    assert summary["secondary"]["sender_includes_target"]["equal_world_mean"] == 0
    assert summary["identical_delivery"]["same_final_prompt_pairs"] == 3
    assert summary["identical_delivery"]["different_decisions"] == 3
    assert result["blocks"][-1]["status"] == "unattempted"
    public = json.dumps(result)
    assert "response_text" not in public
    assert str(study.root) not in public


def test_different_delivery_and_sender_arithmetic(study: _Study) -> None:
    block = study.blocks[0]
    study.seed(block)
    study.arm(block, "blind", 0, revised=(0, 3))
    study.arm(block, "peer", 1, revised=(0, 1, 2))
    pair = study.collect()["blocks"][0]
    assert pair["same_delivered_sender"] is False
    assert pair["same_final_prompt"] is False
    assert pair["same_prompt_decision_changed"] is None
    assert pair["secondary_peer_minus_blind"] == {
        "sender_includes_target": 1,
        "sender_exact_list": 1,
        "sender_changed_list": -1,
        "sender_false_positives": -1,
        "sender_false_negatives": -2,
        "receiver_own_feasible": 1,
    }


def test_partial_and_independently_scored_invalid_are_distinct(study: _Study) -> None:
    complete, partial, bad_seed = study.blocks[:3]
    study.seed(complete)
    study.arm(complete, "blind", None, revised=None)
    study.arm(complete, "peer", 1)
    study.seed(partial)
    trial = study.arm(partial, "peer", None)
    (trial / "verifier/reward.json").unlink()
    study.seed(bad_seed, invalid="p2")
    result = study.collect()
    scored, incomplete, blocked = result["blocks"][:3]
    assert scored["arms"]["blind"]["status"] == "model_invalid_scored_zero"
    assert scored["arms"]["blind"]["invalid_seat"] == complete.sender
    assert scored["peer_minus_blind"] == 1
    assert scored["accepted_peer_minus_blind"] is None
    assert incomplete["arms"]["peer"]["status"] == "missing_evidence"
    assert "reward" not in incomplete["arms"]["peer"]
    assert blocked["seeds"]["p2"]["status"] == "model_invalid"
    assert blocked["status"] == "partial"
    assert result["summary"]["primary"]["equal_world_mean"] is None
    assert result["summary"]["accepted_pairs_only"]["pairs"] == 0


@pytest.mark.parametrize(
    "forgery",
    [
        "delivery",
        "wire",
        "reward",
        "usage",
        "ledger",
        "uuid",
        "identity",
        "prompt",
        "order",
    ],
)
def test_forged_evidence_has_no_outcome(study: _Study, forgery: str) -> None:
    block = study.blocks[0]
    study.seed(block)
    blind = study.arm(block, "blind", 1)
    peer = study.arm(block, "peer", 0)
    native_path = peer / "result.json"
    native = _read(native_path)
    if forgery == "delivery":
        path = peer / "artifacts/logs/world/events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        events[4]["seat"] = (
            block.receiver
        )  # A send is not proof the sender received the first wire.
        _lines(path, events)
    elif forgery == "wire":
        path = peer / "verifier/world-state.json"
        frozen = _read(path)
        # The unchanged byte count and two matching stock copies are not
        # enough: they must contain the actual decoded sender response.
        frozen["messages"][1]["text"] = '{"starts":[2]}'
        _json(path, frozen)
        _lines(peer / "artifacts/logs/world/messages.jsonl", frozen["messages"])
    elif forgery == "reward":
        native["verifier_result"]["rewards"]["reward"] = 1
        _json(peer / "verifier/reward.json", {"reward": 1})
        score = _read(peer / "verifier/score.json")
        _json(peer / "verifier/score.json", {**score, "score": 1, "fields_correct": 1})
    elif forgery in {"usage", "ledger"}:
        native["seat_results"][0]["agent_result"]["n_input_tokens"] = 13
        if (
            forgery == "ledger"
        ):  # The receipt and native counter now agree, but not the ledger.
            path = peer / "seats" / block.sender / "agent/calendar-turn.json"
            _json(path, {**_read(path), "input_tokens": 13})
    elif forgery == "uuid":
        native["id"] = _read(blind / "result.json")["id"].upper()
    elif forgery == "identity":
        native["seat_results"][0]["agent_info"]["version"] = "9.9.9"
    elif forgery == "order":
        native["started_at"] = _read(blind / "result.json")["started_at"]
    else:
        context = native["seat_results"][1]["agent_result"]["metadata"]["calendar"]
        context["own_prompt_sha256"] = "0" * 64
        path = peer / "seats" / block.receiver / "agent/calendar-turn.json"
        _json(path, {**_read(path), "prompt_sha256": "0" * 64})
    _json(native_path, native)
    result = study.collect()["blocks"][0]
    expected = {
        "delivery": "delivery_event",
        "reward": "stock_reward_mismatch",
        "usage": "native_seat_usage",
        "ledger": "ledger_mismatch",
        "uuid": "duplicate_trial_uuid",
        "identity": "native_agent_identity",
        "prompt": "seat_prompt",
        "wire": "stock_wire",
        "order": "registered_arm_order",
    }[forgery]
    assert result["arms"]["peer"] == {"status": "invalid_evidence", "reason": expected}
    assert result["peer_minus_blind"] is None
    assert result["same_final_prompt"] is None
    if forgery == "uuid":
        assert result["arms"]["blind"]["reason"] == "duplicate_trial_uuid"
