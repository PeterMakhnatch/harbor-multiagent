import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from examples.seatlab.committee.analysis import analyze, main
from examples.seatlab.visibility import spend

_ROLES = tuple(f"analyst_{i}" for i in range(1, 5))
_SHARED = [f"F_SHARED_{i}" for i in range(1, 6)]
_PRIVATE = [f"F_PRIV_{i}" for i in range(1, 5)]
_WORLD = "artifacts/logs/world/events.jsonl"
_SERVED = "DeepSeek-V4.1-Flash-1017"
_TREE = "62e6d77d171b2c022f6857781ad3ab9e4e0d4c7d"
_CHECKSUM = "adb41137272dabea3eb946c8733da5424d77520200f373b14f7ef08913da4e1d"
_PRIVATE_PATH = "/private/archive/native-path/hidden_profile_unshared"
_FINISHED = "2026-11-20T00:00:00+00:00"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _receipt_path(trial: Path, seat: int) -> Path:
    return trial / "seats" / _ROLES[seat] / "agent" / "committee-turns.json"


def _fixture(
    root: Path, index: int, repeat: int, arm: str, *, n_private: int = 0,
    choices: tuple[str, ...] = ("Option_A", "Option_B", "Option_B", "Option_A"),
    order: tuple[int, ...] = (2, 0, 3, 1), skip_ballot: int | None = None,
) -> Path:
    cell_id = f"hp-m{index}-r{repeat}-{arm}"
    cell = root / "cells" / cell_id
    trial_name = f"committee-{cell_id}"
    trial = cell / "trials" / trial_name
    _write(cell / "attempt.json", {
        "schema": "seatlab.committee-attempt/v1", "cell_id": cell_id,
        "map_index": index, "repetition": repeat, "arm": arm, "trial_name": trial_name,
        "task_git_tree": _TREE,
    })
    config = {
        "schema_version": 1, "task": {"path": _PRIVATE_PATH}, "trial_name": trial_name,
        "trials_dir": str(trial.parent), "verifier": {"disable": False},
        "participants": [{
            "seat": role, "import_path": "examples.seatlab.committee.seat:CommitteeSeat",
            "model_name": "deepseek/deepseek-flash",
            "kwargs": {"role": role, "map_index": index, "arm": arm, "run_id": cell_id},
        } for role in _ROLES],
    }
    _write(cell / "config.json", config)
    _write(cell / "outcome.json", {"schema": "seatlab.committee-launch/v1", "returncode": 0, "error": None})
    _write(trial / "artifacts/manifest.json", [{
        "source": "/logs/world/events.jsonl", "destination": _WORLD, "status": "ok", "type": "file", "service": None,
    }])

    events = []

    def event(kind: str, actor: str, scope: str, recipients: list[str], payload: dict) -> int:
        seq = len(events) + 1
        events.append({
            "seq": seq, "ts": _FINISHED, "task_family": "epistemic-crucible",
            "condition": "hidden_profile_unshared", "seed": 42, "event_type": kind,
            "actor": actor, "authenticated": actor in _ROLES, "visibility": scope,
            "recipients": recipients, "payload": payload,
        })
        return seq

    public = list(_ROLES)
    event("world_started", "world", "world_private", [], {"mode": "hidden_profile", "seats": public})
    share, votes = {}, {}
    post_seqs = []
    for seat in order:
        role = _ROLES[seat]
        dossier = event("dossier_read", role, "world_audit", [], {})
        event("dossier_truth", "world", "world_private", [role], {
            "read_seq": dossier, "shared_ids": _SHARED, "private_ids": [_PRIVATE[seat]],
        })
        if seat < n_private:
            fact = _PRIVATE[seat]
            option, points = ("Option_B", -9) if seat in (0, 2) else ("Option_A", 9)
        else:
            fact, option, points = _SHARED[0], "Option_B", 10  # Repeated shared fact is still four messages.
        share[seat] = {"text": json.dumps({"id": fact, "option": option, "points": points}, separators=(",", ":"))}
        post_seqs.append(event("chat_message", role, "public", public, {"seat": role, **share[seat]}))
        event("message_read", role, "world_audit", [], {"channel": "public", "msg_seqs": post_seqs.copy()})

    for seat in reversed(order):
        role = _ROLES[seat]
        event("message_read", role, "world_audit", [], {"channel": "public", "msg_seqs": post_seqs})
        if seat == skip_ballot:
            continue
        votes[seat] = {"choice": choices[seat], "cited_facts": [_PRIVATE[seat], _SHARED[0]]}
        if seat == 3 and n_private > 0:
            votes[seat]["cited_facts"].append(_PRIVATE[0])
        event("vote", role, "world_audit", [], {"seat": role, **votes[seat]})
        event("seat_done", role, "world_audit", [], {"seat": role})

    (trial / _WORLD).parent.mkdir(parents=True)
    _write_events(trial, events)
    seat_results = []
    for i, role in enumerate(_ROLES):
        posts = {"share": share[i], "vote": votes.get(i)}
        metadata = {
            "schema": "seatlab.committee-seat/v1", "role": role, "run_id": cell_id,
            "map_index": index, "arm": arm, "phase": "done", "status": "complete", "turns": {},
        }
        for phase, post in posts.items():
            metadata["turns"][phase] = {
                "status": "submitted", "requested_model": "deepseek-flash", "served_model": _SERVED,
                "prompt_sha256": _digest(f"fixture private prompt {role}:{phase}"),
                "response_sha256": _digest(f"fixture private response {role}:{phase}"),
                "action_sha256": _digest(json.dumps(post, sort_keys=True, separators=(",", ":"))),
            }
        _write(_receipt_path(trial, i), metadata | {"turns": {
            phase: record | {"action": posts[phase]} for phase, record in metadata["turns"].items()
        }})
        seat_results.append({
            "seat": role, "under_test": True, "agent_execution": {"finished_at": _FINISHED},
            "exception_info": None, "agent_result": {"metadata": metadata},
            "agent_info": {"name": "seatlab-committee", "model_info": {"provider": "deepseek", "name": "deepseek-flash"}},
        })
    correct = sum(ballot["choice"] == "Option_A" for ballot in votes.values())
    native_group = float(correct >= 3)
    _write(trial / "result.json", {
        "id": str(uuid4()), "task_id": {"path": _PRIVATE_PATH}, "task_checksum": _CHECKSUM,
        "task_name": "crucible/epistemic-crucible-hidden_profile_unshared", "trial_name": trial_name,
        "config": config | {"agent": {"name": "oracle"}}, "finished_at": _FINISHED,
        "reward_valid": True, "seat_results": seat_results, "exception_info": None,
        "verifier_result": {"rewards": {
            "reward": native_group, "group_correct": native_group, "task_utility": native_group,
            "individual_accuracy": correct / 4,
        }},
        "trial_uri": f"file:///private/raw-native-reference/{trial_name}",
    })
    return trial


def _write_events(trial: Path, events: list[dict]) -> None:
    (trial / _WORLD).write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")


def _excluded(report: dict, cell_id: str) -> dict:
    return next(row for row in report["excluded_cells"] if row["cell_id"] == cell_id)


def _label(index: int, fact: str) -> str:
    order = sorted(_SHARED + _PRIVATE, key=lambda native: _digest(f"seatlab-committee-v1:{index}:{native}"))
    return f"K{order.index(fact) + 1}"


def test_native_choices_and_pair_deltas_ignore_seat_arrival_order(tmp_path):
    open_trial = _fixture(tmp_path, 0, 0, "open", n_private=1)
    _fixture(tmp_path, 0, 0, "novelty", n_private=3, order=(3, 1, 2, 0),
             choices=("Option_A", "Option_A", "Option_A", "Option_C"))
    _fixture(tmp_path, 4, 1, "novelty", n_private=4,
             choices=("Option_A", "Option_A", "Option_B", "Option_C"))

    report = analyze(tmp_path)
    open_cell, novelty, permuted = report["cells"]
    assert report["summary"]["scored_cells"] == 3
    assert report["summary"]["scheduled_cells"] == 24
    assert report["summary"]["complete_pairs"] == 1
    assert report["pairs"][0]["delta"] == {
        "unique_private_facts": 2, "native_vote_reward": 1, "correct_individual_votes": 1,
        "strict_group_majority": 1, "at_least_three_private_facts": 1,
    }
    assert report["pairs"][1]["delta"] is None
    assert report["summary"]["effects"]["unique_private_facts"] is None
    assert report["summary"]["complete_pair_effects"]["unique_private_facts"] == 2
    assert open_cell["native_vote_reward"] == 0
    assert open_cell["correct_individual_votes"] == 2
    assert open_cell["strict_group_majority"] is False
    assert novelty["native_vote_reward"] == 1
    assert novelty["strict_group_majority"] is True
    assert [row["seat"] for row in novelty["seats"]] == ["S1", "S2", "S3", "S4"]
    assert novelty["seats"][3]["vote"] == {
        "choice": "Z", "cited": [_label(0, fid) for fid in ("F_PRIV_4", "F_SHARED_1", "F_PRIV_1")],
    }
    assert permuted["seats"][0]["vote"]["choice"] == "Z"
    assert permuted["seats"][2]["vote"]["choice"] == "X"
    assert all(seat["turns"][phase] == {"receipt": "attested", "paid_call": "unknown", "served_model": _SERVED}
               for seat in novelty["seats"] for phase in ("share", "vote"))
    public = json.dumps(report)
    for token in (_PRIVATE_PATH, open_trial.name, _read(open_trial / "result.json")["id"],
                  "raw-native-reference", "analyst_", "F_PRIV_", "F_SHARED_", "Option_", "prompt_sha256"):
        assert token not in public


def test_map_means_weight_each_label_map_equally(tmp_path):
    # Map 0 has two complete contrasts (4 and 0). The other five maps have one contrast (0).
    for index in range(6):
        for arm in ("open", "novelty"):
            _fixture(tmp_path, index, 0, arm, n_private=4 if index == 0 and arm == "novelty" else 0)
    for arm in ("open", "novelty"):
        _fixture(tmp_path, 0, 1, arm)
    report = analyze(tmp_path)
    assert [row["complete_pairs"] for row in report["maps"]] == [2, 1, 1, 1, 1, 1]
    assert report["maps"][0]["effects"]["unique_private_facts"] == 2
    assert report["summary"]["effects"]["unique_private_facts"] == pytest.approx(2 / 6)
    assert report["summary"]["complete_pair_effects"]["unique_private_facts"] == pytest.approx(4 / 7)


def test_wrong_native_reward_and_partial_ballots_cannot_count_as_wins(tmp_path):
    correct = ("Option_A", "Option_A", "Option_A", "Option_B")
    partial = _fixture(tmp_path, 0, 0, "open", choices=correct, skip_ballot=3)
    tied = _fixture(tmp_path, 0, 0, "novelty")
    result = _read(tied / "result.json")
    result["verifier_result"]["rewards"]["reward"] = 1.0
    _write(tied / "result.json", result)
    assert _read(partial / "result.json")["verifier_result"]["rewards"]["reward"] == 1.0

    report = analyze(tmp_path)
    assert report["cells"] == []
    assert report["pairs"][0]["delta"] is None
    for cell_id, reason in (("hp-m0-r0-open", "world_actions_incomplete"),
                            ("hp-m0-r0-novelty", "native_reward_mismatch")):
        entry = _excluded(report, cell_id)
        assert entry["status"] == "failed"
        assert entry["reason"] == reason
        assert "native_vote_reward" not in entry


@pytest.mark.parametrize("changed_part", ["receipt_body", "context_and_receipt_hash", "world_attribution", "dossier_truth", "barrier", "task_checksum"])
def test_independent_evidence_rejects_mismatches(tmp_path, changed_part):
    trial = _fixture(tmp_path, 0, 0, "open", n_private=2)
    receipt = _receipt_path(trial, 0)
    native = trial / "result.json"
    if changed_part == "receipt_body":
        row = _read(receipt)
        row["turns"]["vote"]["action"]["choice"] = "Option_C"
        _write(receipt, row)
    elif changed_part == "context_and_receipt_hash":
        result = _read(native)
        result["seat_results"][0]["agent_result"]["metadata"]["turns"]["share"]["action_sha256"] = "1" * 64
        _write(native, result)
        row = _read(receipt)
        row["turns"]["share"]["action_sha256"] = "1" * 64
        _write(receipt, row)
    elif changed_part == "task_checksum":
        result = _read(native)
        result["task_checksum"] = "2" * 64
        _write(native, result)
    else:
        events = [json.loads(line) for line in (trial / _WORLD).read_text().splitlines()]
        if changed_part == "world_attribution":
            row = next(e for e in events if e["event_type"] == "chat_message" and e["actor"] == _ROLES[0])
            row["payload"]["text"] = '{"id":"F_PRIV_2","option":"Option_A","points":9}'
        elif changed_part == "dossier_truth":
            row = next(e for e in events if e["event_type"] == "dossier_truth" and e["recipients"] == [_ROLES[0]])
            row["payload"]["private_ids"] = [_PRIVATE[1]]
        else:
            for row in events:
                if row["event_type"] == "message_read" and row["actor"] == _ROLES[0]:
                    row["payload"]["msg_seqs"] = []
        _write_events(trial, events)

    report = analyze(tmp_path)
    assert report["cells"] == []
    assert _excluded(report, "hp-m0-r0-open")["reason"] == {
        "receipt_body": "seat_receipt_mismatch", "context_and_receipt_hash": "seat_receipt_mismatch",
        "world_attribution": "world_share_outside_dossier", "dossier_truth": "world_role_visibility_mismatch",
        "barrier": "world_role_visibility_mismatch", "task_checksum": "native_result_invalid",
    }[changed_part]


def _ledger(tmp_path: Path, rows: list[tuple]) -> Path:
    accounting = tmp_path / "private-spend"
    accounting.mkdir(mode=0o700)
    spend.initialize(accounting, Decimal("3"), Decimal("0.50"))
    with closing(sqlite3.connect(accounting / "direct-flash.sqlite3")) as db, db:
        db.executemany(
            "INSERT INTO calls (run,role,reserved_at,served_model,hit,miss,output,cost_low,cost_high) VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return accounting


def test_receipt_and_paid_call_availability_are_separate(tmp_path):
    trial = _fixture(tmp_path, 0, 0, "open")
    _receipt_path(trial, 0).unlink()
    accounting = _ledger(tmp_path, [
        ("hp-m0-r0-open-share", _ROLES[0], _FINISHED, _SERVED, 3, 20, 5, 103, 133),
        ("hp-m0-r0-open-vote", _ROLES[0], _FINISHED, None, None, None, None, None, None),
        ("unregistered-private-run", "private-role", _FINISHED, None, None, None, None, None, None),
    ])

    report = analyze(tmp_path, spend_dir=accounting)
    assert report["spend_evidence"] == "available"
    row = _excluded(report, "hp-m0-r0-open")
    assert row["reason"] == "seat_receipt_missing"
    turns = row["seats"][0]["turns"]
    assert turns["share"] == {"receipt": "missing", "paid_call": "settled"}
    assert turns["vote"] == {"receipt": "missing", "paid_call": "held"}
    assert row["seats"][1]["turns"]["share"] == {"receipt": "present", "paid_call": "no_paid_call_recorded"}
    assert "unregistered-private-run" not in json.dumps(report)
    unknown = analyze(tmp_path, spend_dir=tmp_path / "not-a-ledger")
    assert unknown["spend_evidence"] == "unavailable"
    assert _excluded(unknown, "hp-m0-r0-open")["seats"][0]["turns"]["share"]["paid_call"] == "unknown"


def test_all_eight_paid_turns_join_the_native_receipts(tmp_path):
    _fixture(tmp_path, 0, 0, "open")
    accounting = _ledger(tmp_path, [
        (f"hp-m0-r0-open-{phase}", role, _FINISHED, _SERVED, 3, 20, 5, 103, 133)
        for role in _ROLES for phase in ("share", "vote")
    ])
    report = analyze(tmp_path, spend_dir=accounting)
    for seat in report["cells"][0]["seats"]:
        for turn in seat["turns"].values():
            assert turn == {"receipt": "attested", "paid_call": "settled", "served_model": _SERVED}

    with closing(sqlite3.connect(accounting / "direct-flash.sqlite3")) as db, db:
        db.execute("UPDATE calls SET served_model = ? WHERE run = ? AND role = ?",
                   ("deepseek-flash", "hp-m0-r0-open-vote", _ROLES[2]))
    mismatched = analyze(tmp_path, spend_dir=accounting)
    assert mismatched["cells"] == []
    assert _excluded(mismatched, "hp-m0-r0-open")["reason"] == "paid_call_model_mismatch"


def test_extra_native_trial_and_failed_launcher_are_excluded(tmp_path):
    duplicate = _fixture(tmp_path, 0, 0, "open")
    _write(duplicate.parent / "other-native-trial" / "result.json", _read(duplicate / "result.json"))
    failed = _fixture(tmp_path, 0, 0, "novelty")
    _write(failed.parent.parent / "outcome.json",
           {"schema": "seatlab.committee-launch/v1", "returncode": 1, "error": "private-error-detail"})
    report = analyze(tmp_path)
    assert report["cells"] == []
    assert _excluded(report, "hp-m0-r0-open")["reason"] == "multiple_native_trials"
    assert _excluded(report, "hp-m0-r0-novelty")["reason"] == "native_launch_failed"
    assert "private-error-detail" not in json.dumps(report)


def test_cli_emits_exact_public_report_without_reading_linked_evidence(tmp_path, capsys, monkeypatch):
    study = tmp_path / "private-study"
    trial = _fixture(study, 0, 0, "open")
    path = trial / _WORLD
    outside = tmp_path / "do-not-leak.jsonl"
    path.rename(outside)
    path.symlink_to(outside)

    monkeypatch.setattr(sys, "argv", ["analysis", str(study)])
    main()
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert output.err == ""
    assert report == analyze(study)
    assert report["cells"] == []
    assert len(report["excluded_cells"]) == 24
    assert len(report["pairs"]) == 12
    assert _excluded(report, "hp-m0-r0-open")["reason"] == "unsafe_evidence_path"
    assert "do-not-leak" not in output.out
    assert str(study) not in output.out
