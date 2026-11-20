import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from examples.seatlab.visibility import (
    boxes_choice,
    boxes_packet,
    intent_packet,
    intent_protocol,
    ownership_evidence,
    ownership_protocol,
    ownership_source,
)
from harbor.models.job.lock import build_trial_lock
from harbor.models.trial.config import TrialConfig

_SEATS = ("p1", "p2", "p3")
_ARM = "owner"
_BLOCK = intent_protocol.Block(1, 1, 1, 0)
_PROPOSALS = (50, 50, 2)
_CHOICES = (12, 3, 3)  # A is p3, B is p1, C is p2.
_TRIAL_ID = "b1448151-e501-464a-9676-3f47ecac657a"
_TRIAL_HASH = sha256(_TRIAL_ID.encode()).hexdigest()
_AGENT = "examples.seatlab.visibility.ownership_seat:OwnershipSeat"
_INFO = {"name": "seatlab-box-ownership", "version": "1.0.0", "model_info": None}
_PRIVATE = "private test text, never returned from read_arm"
_INVALID = {
    "status": "invalid",
    "reason": "native_evidence_invalid",
    "trial_id_sha256": _TRIAL_HASH,
}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def _load(path):
    return json.loads(path.read_text())


def _open(seat, box, flag, budget):
    return ownership_source.Action(
        seat,
        "open",
        {"box": box},
        {"box": box, "room": (box - 1) // 10 + 1, "flag": flag, "budget_left": budget},
    )


def _event(seat, kind, request, response, *, method="POST", status=200):
    return {
        "seat": seat,
        "kind": kind,
        "method": method,
        "status": status,
        "ok": status == 200,
        "request": request,
        "response": response,
    }


def _mutation(action):
    return _event(action.seat, action.kind, action.request, action.response)


@dataclass
class Evidence:
    directory: Path
    cut: ownership_source.Cut
    trial: Path
    events: list[dict[str, Any]]

    def read(self, proposals=_PROPOSALS):
        return ownership_evidence.read_arm(
            self.directory, self.cut, _BLOCK, _ARM, proposals
        )

    def record_events(self):
        for seq, event in enumerate(self.events, 1):
            event.update(seq=seq, t=1_700_000_000 + seq)
            if event["kind"] == "admin.close" and event["ok"]:
                event["response"]["events"] = seq
        (self.trial / "verifier" / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in self.events)
        )


@pytest.fixture
def make_evidence(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    task = tmp_path / "boxes-i01"
    _write(
        task / "environment" / "world" / "instance.json",
        {
            "rooms": 6,
            "room_size": 10,
            "treasure_rooms": [1, 2],
            "seats": list(_SEATS),
            "budget": 9,
            "channel": True,
        },
    )
    (task / "task.toml").write_text(
        'schema_version = "1.4"\n[task]\nname = "muster/boxes-i01"\n'
        "[agents.p1]\n[agents.p2]\n[agents.p3]\n"
    )
    (task / "instructions").mkdir()
    (task / "tests").mkdir()
    (task / "tests" / "test.sh").write_text("#!/bin/sh\n")
    for seat in _SEATS:
        (task / "instructions" / f"{seat}.md").write_text("Choose a box.\n")
    checksum, digest = ownership_source._task_identity(task)
    msg = {"id": 1, "from": "p2", "text": _PRIVATE}
    prefix = (
        _open("p1", 1, True, 8),
        ownership_source.Action("p2", "msg", {"text": _PRIVATE}, msg),
        _open("p2", 22, False, 8),
        _open("p3", 21, False, 8),
    )
    cut = ownership_source.Cut(
        1, task, prefix, checksum, digest, ownership_source.prefix_sha256(prefix), 1
    )
    cases = (
        boxes_packet.Case(1, ((1, 1, True),), (21, 22), 8, True),
        boxes_packet.Case(1, ((22, 3, False),), (1, 21), 8, True),
        boxes_packet.Case(1, ((21, 3, False),), (1, 22), 8, True),
    )
    world = intent_packet.World(1, cases)
    monkeypatch.setattr(intent_packet, "load", lambda: {1: world})

    def make(*, invalid=False, zero=False):
        run = ownership_protocol.run_id(_BLOCK, _ARM)
        directory = tmp_path / run
        trial = directory / "trials" / run
        config = TrialConfig.model_validate(
            {
                "trial_name": run,
                "trials_dir": str(trial.parent),
                "task": {"path": str(task)},
                "environment": {"type": "docker", "force_build": True},
                "participants": [
                    {"seat": seat, "import_path": _AGENT, "kwargs": {"seat": seat}}
                    for seat in _SEATS
                ],
            }
        )
        lock = build_trial_lock(
            trial_config=config,
            task_download_result=SimpleNamespace(
                path=task, content_hash=None, resolved_git_commit_id=None
            ),
        )
        _write(trial / "lock.json", lock.model_dump(mode="json", exclude_none=True))
        _write(trial / "config.json", config.model_dump(mode="json", exclude={"agent"}))
        seats = []
        choices = (21, 1, 22) if zero else _CHOICES
        for label, box in enumerate(choices):
            seat = _SEATS[_BLOCK.seat_index(label)]
            own_invalid = invalid and seat == "p2"
            text = (
                _PRIVATE
                if own_invalid
                else json.dumps({"box": boxes_choice.map_box(box, _BLOCK.numbering)})
            )
            prompt = ownership_protocol.prompt(world, _BLOCK, label, _ARM, _PROPOSALS)
            hashes = {
                "prompt_sha256": sha256(prompt.encode()).hexdigest(),
                "response_sha256": sha256(text.encode()).hexdigest(),
            }
            _write(
                directory / "turns" / f"{seat}.json",
                {
                    "schema": "seatlab.box-ownership-turn/v1",
                    "run": run,
                    "seat": seat,
                    "response_text": text,
                    **hashes,
                },
            )
            seats.append(
                {
                    "seat": seat,
                    "under_test": True,
                    "exception_info": None,
                    "agent_info": _INFO,
                    "agent_result": {
                        "metadata": {
                            "box_ownership": {
                                "schema": "seatlab.box-ownership-seat/v1",
                                "run": run,
                                "seat": seat,
                                "status": "model_invalid"
                                if own_invalid
                                else ("peer_invalid" if invalid else "opened"),
                                "prefix_sha256": cut.prefix_sha256,
                                "original_box": None if own_invalid else box,
                                **hashes,
                            }
                        }
                    },
                }
            )
        rewards = {
            "reward": 0.05 if invalid or zero else 0.15,
            "seat.p1": 0.05 if invalid or zero else 0.10,
            "seat.p2": 0.0,
            "seat.p3": 0.0 if invalid or zero else 0.05,
            "opens": 3 if invalid else 6,
            "messages": 1,
            "distinct_boxes": 3 if invalid or zero else 5,
            "duplicate_opens": 0 if invalid else 3 if zero else 1,
            "empty_room_repeats": 2 if zero else 0,
        }
        _write(trial / "verifier" / "reward.json", rewards)
        _write(
            trial / "result.json",
            {
                "schema_version": 1,
                "id": _TRIAL_ID,
                "trial_name": run,
                "trial_uri": trial.as_uri(),
                "task_name": "muster/boxes-i01",
                "task_checksum": checksum,
                "task_id": {"path": str(task)},
                "config": config.model_dump(mode="json"),
                "agent_info": _INFO,
                "exception_info": None,
                "reward_valid": True,
                "verifier_environment_mode": "shared",
                "verifier_result": {"rewards": rewards},
                "finished_at": "2026-11-20T13:00:00Z",
                "seat_results": seats,
            },
        )
        events = [_mutation(action) for action in cut.actions]
        for seat, case in zip(_SEATS, world.seats, strict=True):
            box, room, flag = case.own[0]
            views = {
                "mine": {"opens": [{"box": box, "room": room, "flag": flag}]},
                "rules": {
                    "you": seat,
                    "seats": list(_SEATS),
                    "channel": True,
                    "budget_left": {"open": 8},
                },
                "msgs": {"messages": [msg]},
            }
            events.extend(
                _event(seat, kind, None, view, method="GET")
                for kind, view in views.items()
            )
        if not invalid:
            for seat, box in zip(
                _SEATS, (choices[1], choices[2], choices[0]), strict=True
            ):
                events.append(_mutation(_open(seat, box, box <= 20, 7)))
        events.append(_event(None, "admin.close", {}, {"closed": True}))
        evidence = Evidence(directory, cut, trial, events)
        evidence.record_events()
        return evidence

    return make


def test_native_open_order_decoding_and_safe_rewards(make_evidence):
    evidence = make_evidence()
    # Real rejected late traffic is not scored or mistaken for a new open.
    evidence.events.append(
        {
            **_event("p1", "open", {"box": 4}, {"error": "play is over"}, status=409),
            "late": True,
        }
    )
    evidence.record_events()
    row = evidence.read()
    assert row["status"] == "complete"
    assert row["trial_id_sha256"] == _TRIAL_HASH
    assert row["baseline_flags"] == 1
    assert row["new_flags"] == 2
    assert row["team_reward"] == 0.15
    assert row["choices"] == _CHOICES
    assert row["metrics"] == {
        "individually_new": 3,
        "distinct_new": 2,
        "excess_new_collisions": 1,
        "historical_repeats": 0,
    }
    assert row["native_rewards"]["duplicate_opens"] == 1
    assert row["model_invalid_seats"] == []
    serialized = json.dumps(row)
    assert all(
        s not in serialized for s in (_PRIVATE, _TRIAL_ID, str(evidence.cut.task))
    )


def test_model_invalid_has_native_baseline_but_no_primary_outcome(make_evidence):
    row = make_evidence(invalid=True).read()
    assert row["status"] == "model_invalid"
    assert row["model_invalid_seats"] == ["p2"]
    assert row["team_reward"] == 0.05
    assert row["native_rewards"]["opens"] == 3
    assert row["new_flags"] is row["choices"] is row["metrics"] is None


def test_valid_zero_is_measured(make_evidence):
    row = make_evidence(zero=True).read()
    assert row["status"] == "complete"
    assert row["new_flags"] == 0
    assert row["team_reward"] == 0.05
    assert row["native_rewards"]["empty_room_repeats"] == 2


def test_unstarted_and_partial_claims_are_distinct(make_evidence):
    evidence = make_evidence()
    other_arm = "neutral"
    directory = evidence.directory.parent / ownership_protocol.run_id(_BLOCK, other_arm)
    args = (directory, evidence.cut, _BLOCK, other_arm, _PROPOSALS)
    assert ownership_evidence.read_arm(*args) == {"status": "not_started"}
    directory.mkdir()
    assert ownership_evidence.read_arm(*args) == {
        "status": "invalid",
        "reason": "native_evidence_invalid",
    }


@pytest.mark.parametrize(
    "field",
    [
        "raw_response",
        "prompt",
        "seat_role",
        "decoded_selection",
        "agent_import",
        "cloud",
        "disabled_verifier",
        "source_path",
        "lock_digest",
    ],
)
def test_rejects_receipt_role_and_source_mismatches(make_evidence, field):
    evidence = make_evidence()
    result_path = evidence.trial / "result.json"
    result = _load(result_path)
    if field == "raw_response":
        turn = evidence.directory / "turns" / "p1.json"
        _write(turn, {**_load(turn), "response_text": '{"box":12}'})
    elif field == "prompt":
        assert evidence.read((50, 50, 3)) == _INVALID
        return
    elif field == "seat_role":
        result["seat_results"][0]["under_test"] = False
        _write(result_path, result)
    elif field == "decoded_selection":
        ownership = result["seat_results"][0]["agent_result"]["metadata"][
            "box_ownership"
        ]
        ownership["original_box"] = 13
        _write(result_path, result)
    elif field in {"agent_import", "cloud", "disabled_verifier"}:
        config_path = evidence.trial / "config.json"
        lock_path = evidence.trial / "lock.json"
        config, lock = _load(config_path), _load(lock_path)
        for record in (result["config"], config, lock):
            if field == "agent_import":
                record["participants"][0]["import_path"] = "other:Agent"
            elif field == "cloud":
                record["environment"]["type"] = "daytona"
            else:
                record["verifier"]["disable"] = True
        _write(config_path, config)
        _write(lock_path, lock)
        _write(result_path, result)
    elif field == "source_path":
        result["task_id"]["path"] = str(evidence.cut.task.parent / "foreign")
        _write(result_path, result)
    else:
        lock_path = evidence.trial / "lock.json"
        lock = _load(lock_path)
        lock["task"]["digest"] = "sha256:" + "f" * 64
        _write(lock_path, lock)
    assert evidence.read() == _INVALID


@pytest.mark.parametrize(
    "fault",
    [
        "early_close",
        "unauth_open",
        "extra_open",
        "prefix_order",
        "budget",
        "missing_view",
        "wrong_final_order",
    ],
)
def test_rejects_events_outside_the_exact_native_play(make_evidence, fault):
    evidence = make_evidence()
    events = evidence.events
    if fault == "early_close":
        events.insert(-2, events.pop())
    elif fault == "unauth_open":
        events[-2]["seat"] = None
    elif fault == "extra_open":
        events.insert(-1, _mutation(_open("p1", 4, True, 6)))
    elif fault == "prefix_order":
        events[1], events[2] = events[2], events[1]
    elif fault == "budget":
        events[-2]["response"]["budget_left"] = 8
    elif fault == "missing_view":
        events.pop(len(evidence.cut.actions))
    else:
        events[-4], events[-3] = events[-3], events[-4]
    evidence.record_events()
    assert evidence.read() == _INVALID


def test_rejects_clean_open_when_any_response_is_invalid(make_evidence):
    evidence = make_evidence(invalid=True)
    evidence.events.insert(-1, _mutation(_open("p3", 12, True, 7)))
    evidence.record_events()
    assert evidence.read() == _INVALID


@pytest.mark.parametrize("rewrite_event", [False, True])
def test_false_reward_cannot_mask_a_flag(make_evidence, rewrite_event):
    evidence = make_evidence()
    result_path = evidence.trial / "result.json"
    reward_path = evidence.trial / "verifier" / "reward.json"
    result = _load(result_path)
    rewards = {**_load(reward_path), "reward": 0.10, "seat.p3": 0.0}
    result["verifier_result"]["rewards"] = rewards
    _write(result_path, result)
    _write(reward_path, rewards)
    if rewrite_event:
        evidence.events[-2]["response"]["flag"] = False
        evidence.record_events()
    assert evidence.read() == _INVALID


def test_symlink_turn_receipt_is_not_read(make_evidence):
    evidence = make_evidence()
    turn = evidence.directory / "turns" / "p1.json"
    original = turn.rename(evidence.directory / "foreign-receipt.json")
    turn.symlink_to(original)
    assert evidence.read() == _INVALID
