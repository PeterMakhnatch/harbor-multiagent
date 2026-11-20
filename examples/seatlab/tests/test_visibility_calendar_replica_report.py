from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.visibility import calendar_protocol as protocol
from examples.seatlab.visibility import calendar_replica_report as report
from examples.seatlab.visibility import calendar_replica_source as source_identity
from harbor.models.trial.config import TrialConfig

_Row = dict[str, Any]
_SEATS = ("p1", "p2")
_ZERO = (0, 0, 0, 0, 0)
_TABLES = (
    ((240, 255, 255, 255, 255), _ZERO, _ZERO),
    ((249, 255, 255, 255, 255), _ZERO, _ZERO),
)
_SEEDS = ((0, 1, 2), (1,))
_CHECKSUM = "a" * 64
_SEMANTICS = "b" * 64
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_IDENTITY = {
    "name": "seatlab-calendar-external",
    "version": "1.0.0",
    "model_info": None,
}
_WORLD = {
    "world_version": 2,
    "condition": "talk",
    "seats": list(_SEATS),
    "limits": {"max_messages": 200, "max_text": 4000},
}


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _read(path: Path) -> _Row:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


class _Block:
    def __init__(self, tmp_path: Path) -> None:
        self.out = tmp_path / "output"
        self.source = tmp_path / "tasks" / "schedule-000-talk"
        self.block = protocol.Block(0, 1, 0)
        self.run = "rx-w00-d1-r0"
        self.source.mkdir(parents=True)
        _write(
            self.out / "block.json",
            {
                "schema": "seatlab.calendar-replica/v1",
                "world": 0,
                "direction": 1,
                "repetition": 0,
                "source": str(self.source),
                "native_sha256": _CHECKSUM,
                "semantics_sha256": _SEMANTICS,
            },
        )
        (self.out / "host" / "home").mkdir(parents=True)
        (self.out / "host" / "home" / "private.log").write_text("not public")

    def turn(self, run: str, role: str, seat: str, prompt: str, text: str) -> Path:
        path = self.out / "turns" / f"{run}_{role}.json"
        _write(
            path,
            {
                "schema": "seatlab.relay-turn/v1",
                "run": run,
                "role": role,
                "seat": seat,
                "prompt_sha256": _digest(prompt),
                "response_sha256": _digest(text),
                "response_text": text,
            },
        )
        return path

    def seeds(self, *, first: str | None = None) -> None:
        for i, seat in enumerate(_SEATS):
            text = (
                first
                if i == 0 and first is not None
                else json.dumps({"starts": _SEEDS[i]})
            )
            self.turn(
                self.run,
                f"cal_initial_{seat}",
                seat,
                protocol.initial_prompt(_TABLES[i]),
                text,
            )

    def branch(
        self, arm: str, slot: int | None, *, invalid_recheck: bool = False
    ) -> Path:
        run = f"{self.run}-{arm}"
        trial = self.out / "native" / run
        config = TrialConfig.model_validate(
            {
                "task": {"path": str(self.source)},
                "trial_name": run,
                "trials_dir": str(trial.parent),
                "environment": {"type": "docker", "force_build": True},
                "participants": [
                    {
                        "seat": seat,
                        "import_path": "examples.seatlab.visibility.calendar_replica_seat:RelayCalendarSeat",
                        "kwargs": {
                            "world": 0,
                            "direction": 1,
                            "repetition": 0,
                            "arm": arm,
                            "seat": seat,
                            "control": False,
                            "own_starts": list(_SEEDS[i]),
                        },
                    }
                    for i, seat in enumerate(_SEATS)
                ],
            }
        ).model_dump(mode="json")
        _write(trial / "config.json", {k: v for k, v in config.items() if k != "agent"})
        sender, receiver = self.block.sender, self.block.receiver
        revised = (1,)
        feedback = _SEEDS[0] if arm == "peer" else None
        recheck = protocol.recheck_prompt(_TABLES[1], _SEEDS[1], feedback)
        self.turn(
            run,
            "cal_recheck",
            sender,
            recheck,
            "private invalid-recheck response" if invalid_recheck else '{"starts":[1]}',
        )
        decide = None
        if not invalid_recheck:
            decide = protocol.decision_prompt(_TABLES[0], _SEEDS[0], revised)
            self.turn(
                run,
                "cal_decide",
                receiver,
                decide,
                "private invalid-decision response"
                if slot is None
                else json.dumps({"slot": slot}),
            )
        seat_results = []
        for seat, prompt, stage, incoming, outgoing in (
            (
                sender,
                recheck,
                "model_invalid" if invalid_recheck else "complete",
                "m1",
                "m2",
            ),
            (
                receiver,
                decide,
                "peer_model_invalid"
                if invalid_recheck
                else "model_invalid"
                if slot is None
                else "complete",
                "m2",
                "m1",
            ),
        ):
            (trial / "seats" / seat / "agent").mkdir(parents=True)
            seat_results.append(
                {
                    "seat": seat,
                    "under_test": True,
                    "agent_info": _IDENTITY,
                    "exception_info": None,
                    "agent_result": {
                        "metadata": {
                            "calendar": {
                                "schema": "seatlab.calendar-seat/v1",
                                "run": run,
                                "seat": seat,
                                "control": False,
                                "stage": stage,
                                "own_table_sha256": _digest(
                                    json.dumps(
                                        _TABLES[_SEATS.index(seat)],
                                        separators=(",", ":"),
                                    )
                                ),
                                "own_prompt_sha256": _digest(prompt)
                                if prompt is not None
                                else None,
                                "incoming_id": incoming,
                                "outgoing_id": outgoing,
                            }
                        }
                    },
                }
            )
        reward = int(slot == 1)
        start = _NOW + timedelta(minutes=self.block.arms.index(arm))
        _write(
            trial / "result.json",
            {
                "schema_version": 1,
                "id": str(uuid4()),
                "task_checksum": _CHECKSUM,
                "trial_name": run,
                "trial_uri": trial.as_uri(),
                "task_id": {"path": str(self.source)},
                "task_name": "flotilla/schedule-000-talk",
                "source": None,
                "verifier_environment_mode": "shared",
                "config": config,
                "agent_info": _IDENTITY,
                "seat_results": seat_results,
                "reward_valid": True,
                "exception_info": None,
                "started_at": start.isoformat(),
                "finished_at": (start + timedelta(seconds=15)).isoformat(),
                "verifier_result": {"rewards": {"reward": reward}},
            },
        )
        msgs = [
            {
                "id": "m1",
                "from": receiver,
                "to": sender,
                "t": 4,
                "text": protocol.feedback_wire(feedback),
            },
            {
                "id": "m2",
                "from": sender,
                "to": receiver,
                "t": 6,
                "text": '{"model_invalid":true}'
                if invalid_recheck
                else protocol.feedback_wire(revised),
            },
        ]
        submit = (
            {"n": 1, "seat": receiver, "answer": protocol.answer(slot), "t": 8}
            if slot is not None
            else None
        )
        world = trial / "artifacts" / "logs" / "world"
        world.mkdir(parents=True)
        events = [
            ("start", None, _WORLD),
            ("whoami", sender, {}),
            ("whoami", receiver, {}),
            (
                "send",
                receiver,
                {"id": "m1", "to": sender, "chars": len(msgs[0]["text"])},
            ),
            ("deliver", sender, {"ids": ["m1"]}),
            (
                "send",
                sender,
                {"id": "m2", "to": receiver, "chars": len(msgs[1]["text"])},
            ),
            ("deliver", receiver, {"ids": ["m2"]}),
        ]
        if submit is not None:
            events.append(("submit", receiver, {"n": 1, "answer": submit["answer"]}))
        (world / "events.jsonl").write_text(
            "".join(
                json.dumps({"seq": i, "t": i, "kind": kind, "seat": seat, "data": data})
                + "\n"
                for i, (kind, seat, data) in enumerate(events, 1)
            ),
            encoding="utf-8",
        )
        (world / "messages.jsonl").write_text(
            "".join(json.dumps(msg) + "\n" for msg in msgs), encoding="utf-8"
        )
        _write(
            trial / "verifier" / "world-state.json",
            {
                **_WORLD,
                "frozen": True,
                "sent": {"p1": 1, "p2": 1},
                "messages": msgs,
                "submissions": [submit] if submit is not None else [],
            },
        )
        _write(trial / "verifier" / "reward.json", {"reward": reward})
        _write(
            trial / "verifier" / "score.json",
            {
                "deceived": None,
                "error": None,
                "family": "schedule",
                "instance": 0,
                "condition": "talk",
                "seats": list(_SEATS),
                "world_version": 2,
                "fields_correct": reward,
                "fields_total": 1,
                "score": reward,
                "answer": submit["answer"] if submit is not None else None,
                "submitted_by": receiver if submit is not None else None,
                "n_submissions": int(submit is not None),
                "n_messages": 2,
            },
        )
        return trial

    def analyze(self) -> _Row:
        return report.analyze(self.out)


@pytest.fixture
def block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Block:
    fixture = _Block(tmp_path.resolve())

    def verify(task: Path, world: int) -> source_identity.Identity:
        assert (task, world) == (fixture.source, 0)
        return source_identity.Identity(_CHECKSUM, _SEMANTICS)

    monkeypatch.setattr(source_identity, "verify", verify)
    monkeypatch.setattr(
        packet, "load", lambda *_: (packet.World(0, _CHECKSUM, _TABLES),)
    )
    return fixture


def test_complete_replays_one_seed_pair_and_both_deliveries(block: _Block) -> None:
    block.seeds()
    peer_trial = block.branch("peer", 1)
    block.branch("blind", 0)
    result = block.analyze()
    assert result["schema"] == "seatlab.calendar-replica-report/v1"
    assert result["status"] == "complete"
    assert result["native_sha256"] == _CHECKSUM
    assert result["semantics_sha256"] == _SEMANTICS
    assert result["counted_turns"] == 6
    assert result["equal_final_prompt"] is True
    assert result["equal_choice"] is False
    blind, peer = (result["arms"][arm] for arm in ("blind", "peer"))
    assert (blind["slot"], blind["reward"], peer["slot"], peer["reward"]) == (
        0,
        0,
        1,
        1,
    )
    assert blind["status"] == peer["status"] == "complete"
    assert blind["native_timeline_verified"] is True
    assert peer["native_timeline_verified"] is True
    peer_id = _read(peer_trial / "result.json")["id"]
    assert peer["trial_id_sha256"] == _digest(peer_id)
    assert blind["sender_prompt_sha256"] != peer["sender_prompt_sha256"]
    assert blind["usage"] == peer["usage"] == dict.fromkeys(_SEATS)
    public = json.dumps(result)
    for private in (
        str(block.source),
        "not public",
        _NOW.isoformat(),
        "response_text",
        peer_id,
    ):
        assert private not in public


def test_consistent_forged_prompt_from_other_seat_is_rejected(block: _Block) -> None:
    block.seeds()
    trial = block.branch("peer", 1)
    forged = _digest(protocol.recheck_prompt(_TABLES[0], _SEEDS[0], _SEEDS[1]))
    receipt = block.out / "turns" / f"{trial.name}_cal_recheck.json"
    _write(receipt, {**_read(receipt), "prompt_sha256": forged})
    native = _read(trial / "result.json")
    native["seat_results"][0]["agent_result"]["metadata"]["calendar"][
        "own_prompt_sha256"
    ] = forged
    _write(trial / "result.json", native)
    result = block.analyze()
    assert result["status"] == "invalid_evidence"
    assert result["arms"]["peer"]["reason"] == "relay_turn_binding"
    assert result["arms"]["peer"]["reward"] is None
    assert result["counted_turns"] == 2


@pytest.mark.parametrize("contradiction", ["reward", "delivery"])
def test_native_contradiction_withholds_reward(
    block: _Block, contradiction: str
) -> None:
    block.seeds()
    trial = block.branch("peer", 1)
    if contradiction == "reward":
        _write(trial / "verifier" / "reward.json", {"reward": 0})
        reason = "stock_reward_mismatch"
    else:
        path = trial / "artifacts" / "logs" / "world" / "events.jsonl"
        events = path.read_text().splitlines()
        events[6] = json.dumps({**json.loads(events[6]), "data": {"ids": []}})
        path.write_text("\n".join(events) + "\n")
        reason = "delivery_event"
    result = block.analyze()
    assert result["status"] == "invalid_evidence"
    assert result["arms"]["peer"]["reward"] is None
    assert result["arms"]["peer"]["reason"] == reason
    assert result["counted_turns"] == 4


def test_invalid_decision_and_invalid_recheck_are_scored_but_no_native_action_is_not(
    block: _Block,
) -> None:
    assert block.analyze()["status"] == "not_started"
    block.seeds()
    pending = block.analyze()
    assert pending["status"] == "partial"
    assert pending["arms"]["peer"]["status"] == "not_started"
    assert pending["arms"]["peer"]["reward"] is None
    block.branch("peer", None)
    block.branch("blind", None, invalid_recheck=True)
    result = block.analyze()
    assert result["status"] == "complete"
    assert result["counted_turns"] == 5
    assert result["equal_final_prompt"] is None
    for arm in result["arms"].values():
        assert arm["status"] == "model_invalid_scored_zero"
        assert arm["reward"] == 0
        assert arm["slot"] is None
    assert result["arms"]["blind"]["receiver_prompt_sha256"] is None
    assert result["arms"]["peer"]["receiver_prompt_sha256"] is not None
    assert "private invalid" not in json.dumps(result)


def test_missing_receiver_receipt_cannot_be_inferred_from_stock_zero(
    block: _Block,
) -> None:
    block.seeds()
    trial = block.branch("peer", None)
    (block.out / "turns" / f"{trial.name}_cal_decide.json").unlink()
    result = block.analyze()
    assert result["status"] == "partial"
    assert result["arms"]["peer"]["status"] == "missing_evidence"
    assert result["arms"]["peer"]["reward"] is None
    assert result["arms"]["peer"]["native_timeline_verified"] is False
    assert result["arms"]["peer"]["trial_id_sha256"] == _digest(
        _read(trial / "result.json")["id"]
    )
    assert result["counted_turns"] == 3


def test_seed_invalid_and_trial_exception_do_not_become_a_loss(block: _Block) -> None:
    block.seeds(first="private invalid seed")
    result = block.analyze()
    assert result["status"] == "invalid_seed"
    assert result["seeds"]["p1"]["status"] == "model_invalid"
    assert result["seeds"]["p2"]["status"] == "accepted"
    assert result["counted_turns"] == 2
    block.seeds()
    trial = block.branch("peer", None)
    path = trial / "result.json"
    _write(path, {**_read(path), "exception_info": {"reason": "private failure"}})
    result = block.analyze()
    assert result["status"] == "partial"
    assert result["arms"]["peer"]["status"] == "native_failure"
    assert result["arms"]["peer"]["reward"] is None
    assert result["counted_turns"] == 4
    assert "private failure" not in json.dumps(result)


@pytest.mark.parametrize(
    "tamper", ["duplicate", "extra_arm", "extra_turn", "source_checksum"]
)
def test_block_binding_rejects_duplicate_trials_and_unregistered_entries(
    block: _Block, tamper: str
) -> None:
    block.seeds()
    peer = block.branch("peer", 1)
    blind = block.branch("blind", 0)
    if tamper == "duplicate":
        _write(
            blind / "result.json",
            {**_read(blind / "result.json"), "id": _read(peer / "result.json")["id"]},
        )
    elif tamper == "extra_arm":
        (peer.parent / f"{block.run}-third").mkdir()
    elif tamper == "extra_turn":
        _write(block.out / "turns" / "unregistered.json", {})
    else:
        path = block.out / "block.json"
        _write(path, {**_read(path), "native_sha256": "c" * 64})
    result = block.analyze()
    assert result["status"] == "invalid_evidence"
    assert all(arm["reward"] is None for arm in result["arms"].values())
