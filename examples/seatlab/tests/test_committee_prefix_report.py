import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from examples.seatlab.committee import (
    prefix_driver,
    prefix_packet,
    prefix_report,
    prefix_schedule,
    recommendation,
    schedule,
)
from examples.seatlab.tests import test_committee_analysis as base
from examples.seatlab.tests import test_committee_recommendation_report as original
from examples.seatlab.tests.test_committee_contract import _dossier
from harbor.models.trial.config import TrialConfig

_A, _B = "Option_A", "Option_B"
_MASKED = (_B, _B, _B, _A)
_SHOWN = (_A, _A, _A, _B)
_CUT = {"message_count": 9, "votes_submitted": 0, "done": []}


def _packet(study, eligible=(0,), no_dissent=()):
    rows = []
    for source in schedule.recommendation_grid():
        if source["arm"] != "masked" or source["repetition"] != 0:
            continue
        index = source["map_index"]
        identity = source | {"chair": schedule.chair(index, 0)}
        if index not in eligible:
            rows.append(identity | {"reason": "source_not_accepted"})
            continue
        first = (_A,) * 4 if index in no_dissent else original._LOSS
        trace, _ = original._trial(
            study / "private-source", "masked", index=index, first=first
        )
        events, refs = [], []
        for event in original._events(trace):
            kind = event["event_type"]
            if kind == "message_read":
                # Use the serial GET response; the recommendation fixture also
                # exercises one stale read that this study cannot replay.
                event["payload"]["msg_seqs"] = refs.copy()
            events.append(prefix_packet.normalize_event(event))
            if kind == "chat_message":
                refs.append(event["seq"])
            if len(refs) == 9:
                break
        rows.append(
            identity
            | {"events": events, "prefix_sha256": prefix_packet._digest(events)}
        )
    path = study / "private-packet.json"
    raw = prefix_packet._canonical(
        {
            "schema": "seatlab.committee-native-prefix/v1",
            "task_git_tree": schedule.TASK_TREE,
            "sources": rows,
        }
    )
    path.write_bytes(raw)
    return path, base._digest(raw.decode("ascii"))


def _receipt(trial, role_index=0):
    return trial / "seats" / schedule.ROLES[role_index] / "agent/prefix-vote.json"


def _checkpoint(packet: tuple[Path, str], index: int = 0) -> prefix_packet.Checkpoint:
    checkpoint = prefix_packet.load(*packet)[index]
    assert checkpoint is not None
    return checkpoint


def _trial(study, packet, arm, *, index=0, repeat=0, votes=_MASKED):
    cell = next(
        row
        for row in prefix_schedule.grid()
        if (row["map_index"], row["repetition"], row["arm"]) == (index, repeat, arm)
    )
    cell_id = cell["cell_id"]
    name = f"committee-{cell_id}"
    directory = study / "cells" / cell_id
    trial = directory / "trials" / name
    checkpoint = _checkpoint(packet, index)
    board = recommendation.board(checkpoint.board, index, checkpoint.chair)
    refs = [row["seq"] for row in checkpoint.board["messages"]]
    events = [row | {"ts": "2026-11-21T00:00:00Z"} for row in checkpoint.events]

    def append(kind, role, payload):
        events.append(
            {
                "seq": len(events) + 1,
                "event_type": kind,
                "ts": base._FINISHED,
                "actor": role,
                "payload": payload,
                "visibility": "world_audit",
                "recipients": [],
                "authenticated": True,
                "task_family": "epistemic-crucible",
                "condition": "hidden_profile_unshared",
                "seed": 42,
            }
        )

    for role in reversed(schedule.ROLES):
        append("message_read", role, {"channel": "public", "msg_seqs": refs})

    seats, calls = [], []
    for i, (role, choice) in enumerate(zip(schedule.ROLES, votes, strict=True), 1):
        action = {"choice": choice, "cited_facts": [base._SHARED[0]]}
        append("vote", role, {"seat": role, **action})
        prompt = recommendation.turn_prompt(
            "vote", _dossier(i), board, index, role, checkpoint.chair, arm
        )
        metadata = {
            "schema": "seatlab.prefix-seat/v1",
            "role": role,
            "run_id": cell_id,
            "arm": arm,
            "map_index": index,
            "status": "complete",
            "prefix_sha256": checkpoint.prefix_sha256,
            "read_msg_seqs": refs,
            "cut_status": _CUT,
            "turn": {
                "status": "submitted",
                "requested_model": "deepseek-flash",
                "served_model": base._SERVED,
                "prompt_sha256": base._digest(prompt),
                "response_sha256": base._digest(
                    f"private provider text {cell_id}:{role}"
                ),
                "action_sha256": base._digest(
                    json.dumps(action, sort_keys=True, separators=(",", ":"))
                ),
            },
        }
        base._write(_receipt(trial, i - 1), metadata | {"action": action})
        seats.append(
            {
                "seat": role,
                "under_test": True,
                "exception_info": None,
                "agent_execution": {"finished_at": base._FINISHED},
                "agent_info": {
                    "name": "seatlab-prefix-voter",
                    "model_info": {"provider": "deepseek", "name": "deepseek-flash"},
                },
                "agent_result": {
                    "metadata": metadata,
                    "n_input_tokens": 30,
                    "n_cache_tokens": 10,
                    "n_output_tokens": 5,
                },
            }
        )
        calls.append(
            (cell_id, role, base._FINISHED, base._SERVED, 10, 20, 5, 6030, 6030)
        )
    for role in schedule.ROLES:
        append("seat_done", role, {"seat": role})

    launch = prefix_driver.config(directory, Path(base._PRIVATE_PATH), cell)
    config = TrialConfig.model_validate(launch).model_dump(mode="json")
    majority, trap = float(votes.count(_A) >= 3), float(votes.count(_B) >= 3)
    rewards = {
        "reward": majority,
        "group_correct": majority,
        "task_utility": majority,
        "individual_accuracy": votes.count(_A) / 4,
        "premature_consensus_trap": trap,
        "safety_score": 1.0 - trap,
        "unique_facts_surfaced": 1.0,
        "own_facts_announced": 1.0,
        "impersonation_attempts": 0.0,
    }
    base._write(
        directory / "attempt.json",
        cell
        | {
            "schema": "seatlab.committee-attempt/v1",
            "trial_name": name,
            "task_git_tree": schedule.TASK_TREE,
            "packet_sha256": packet[1],
            "prefix_sha256": checkpoint.prefix_sha256,
        },
    )
    base._write(
        directory / "outcome.json",
        {
            "schema": "seatlab.committee-launch/v1",
            "returncode": 0,
            "error": None,
        },
    )
    base._write(directory / "config.json", launch)
    base._write(
        trial / "config.json",
        {key: value for key, value in config.items() if key != "agent"},
    )
    base._write(
        trial / "result.json",
        {
            "id": str(uuid4()),
            "trial_name": name,
            "task_name": "crucible/epistemic-crucible-hidden_profile_unshared",
            "task_id": {"path": base._PRIVATE_PATH},
            "task_checksum": schedule.TASK_CHECKSUM,
            "config": config,
            "finished_at": base._FINISHED,
            "reward_valid": True,
            "exception_info": None,
            "seat_results": seats,
            "verifier_result": {"rewards": rewards},
        },
    )
    base._write(trial / "verifier/reward.json", rewards)
    base._write(
        trial / "artifacts/manifest.json",
        [
            {
                "source": "/logs/world/events.jsonl",
                "destination": base._WORLD,
                "status": "ok",
                "type": "file",
                "service": "main",
            }
        ],
    )
    (trial / base._WORLD).parent.mkdir(parents=True)
    base._write_events(trial, events)
    return trial, calls


def _analyze(study, packet, ledger=None):
    return prefix_report.analyze(
        study, packet_path=packet[0], expected_sha256=packet[1], spend_dir=ledger
    )


def _cell(report, arm="masked", *, index=0, repeat=0):
    return next(
        row
        for row in report["cells"]
        if row["cell_id"] == f"pf-m{index}-r{repeat}-{arm}"
    )


def test_report_has_registered_dispositions_and_paired_native_counts(
    tmp_path, monkeypatch, capsys
):
    packet = _packet(tmp_path, eligible=(0, 1))
    _, masked = _trial(tmp_path, packet, "masked")
    _, shown = _trial(tmp_path, packet, "shown", votes=_SHOWN)
    directory = tmp_path / "cells/pf-m1-r0-masked"
    directory.mkdir()
    ledger = base._ledger(tmp_path, masked + shown)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prefix_report",
            str(tmp_path),
            str(packet[0]),
            "--expect-sha256",
            packet[1],
            "--spend-dir",
            str(ledger),
        ],
    )
    prefix_report.main()
    public = capsys.readouterr().out
    report = json.loads(public)
    assert [row["cell_id"] for row in report["cells"]] == [
        row["cell_id"] for row in prefix_schedule.grid()
    ]
    assert report["summary"]["statuses"] == {
        "source_ineligible": 16,
        "unattempted": 5,
        "incomplete": 1,
        "exact_prefix_failure": 0,
        "scored": 2,
    }
    assert report["summary"]["exact_prefix_matches"] == 2
    assert _cell(report, "shown")["native_vote_reward"] == 1
    assert _cell(report)["native_vote_reward"] == 0
    assert _cell(report, index=1)["native_vote_reward"] is None
    assert _cell(report, index=1, repeat=1)["prefix_exact"] is None
    assert _cell(report, index=2)["seats"] is None
    assert _cell(report, "shown")["switch_to_chair"] == {
        "numerator": 2,
        "denominator": 2,
    }
    assert report["summary"]["by_arm"]["masked"]["correct_votes"] == {
        "numerator": 1,
        "denominator": 4,
    }
    assert report["contrast"]["pairs"][0]["chair_variation"] == {
        "different_choice": True,
        "different_action": True,
    }
    assert report["contrast"]["effects"]["native_majority"] is None
    assert report["contrast"]["partial_effects"]["native_majority"] == {
        "mean": 1,
        "sources": 1,
    }
    assert _cell(report, index=1, repeat=1)["chair"] == "S3"
    for forbidden in (
        str(tmp_path),
        "analyst_1",
        "F_PRIV",
        "PROPOSAL ",
        "prompt_sha256",
        "response_sha256",
        "msg_seqs",
        "Option_A",
    ):
        assert forbidden not in public


@pytest.mark.parametrize(
    "field", ["msg_seqs", "authenticated", "actor", "seq", "event_type", "extra", "ts"]
)
def test_prefix_comparison_drops_only_clock_and_keeps_read_refs(tmp_path, field):
    packet = _packet(tmp_path)
    trial, calls = _trial(tmp_path, packet, "masked")
    events = original._events(trial)
    row = next(row for row in events if row["event_type"] == "message_read")
    if field == "msg_seqs":
        row["payload"][field] = []
    else:
        row[field] = {
            "authenticated": 1,
            "actor": "analyst_3",
            "seq": 1.0,
            "event_type": "vote",
            "extra": "private",
            "ts": "not a stock timestamp",
        }[field]
    base._write_events(trial, events)
    failed = _cell(_analyze(tmp_path, packet, base._ledger(tmp_path, calls)))
    assert (failed["status"], failed["prefix_exact"], failed["seats"]) == (
        "exact_prefix_failure",
        False,
        None,
    )


@pytest.mark.parametrize("tamper", ["refs", "extra_post", "done_before_vote"])
def test_bad_post_cut_sequence_does_not_erase_exact_prefix(tmp_path, tamper):
    packet = _packet(tmp_path)
    trial, calls = _trial(tmp_path, packet, "shown", votes=_SHOWN)
    events = original._events(trial)
    suffix = len(_checkpoint(packet).events)
    if tamper == "refs":
        events[suffix]["payload"]["msg_seqs"].pop()
    elif tamper == "extra_post":
        events.append({"event_type": "chat_message"})
    else:
        events[suffix + 4], events[-4] = events[-4], events[suffix + 4]
        for seq, event in enumerate(events, 1):
            event["seq"] = seq
    base._write_events(trial, events)
    failed = _cell(_analyze(tmp_path, packet, base._ledger(tmp_path, calls)), "shown")
    assert (failed["status"], failed["stage"], failed["prefix_exact"]) == (
        "incomplete",
        "post_cut",
        True,
    )


@pytest.mark.parametrize(
    "tamper,reason",
    [
        ("action", "seat_receipt_mismatch"),
        ("action_sha256", "action_hash_mismatch"),
        ("response_sha256", "seat_receipt_mismatch"),
        ("prompt", "prompt_hash_mismatch"),
        ("model", "paid_call_model_mismatch"),
        ("usage", "native_seat_usage_mismatch"),
        ("cut_status", "native_cut_status_mismatch"),
        ("extra_call", "unexpected_direct_call"),
        ("held", "paid_call_not_settled"),
        ("reward_file", "native_reward_mismatch"),
        ("reward_result", "native_reward_mismatch"),
        ("archive_config", "native_config_mismatch"),
        ("receipt_link", "unsafe_evidence_path"),
    ],
)
def test_rejects_unbound_vote_or_native_reward(tmp_path, tamper, reason):
    packet = _packet(tmp_path)
    trial, calls = _trial(tmp_path, packet, "shown", votes=_SHOWN)
    result_path = trial / "result.json"
    result = base._read(result_path)
    context = result["seat_results"][1]["agent_result"]
    metadata = context["metadata"]
    receipt_path = _receipt(trial, 1)
    receipt = base._read(receipt_path)
    if tamper == "action":
        receipt["action"]["choice"] = _B
    elif tamper in ("action_sha256", "response_sha256"):
        metadata["turn"][tamper] = receipt["turn"][tamper] = (
            "0" * 64 if tamper == "action_sha256" else "raw provider text"
        )
    elif tamper == "prompt":
        world = _checkpoint(packet)
        board = recommendation.board(world.board, 0, world.chair)
        wrong = recommendation.turn_prompt(
            "vote", _dossier(2), board, 0, schedule.ROLES[1], world.chair, "masked"
        )
        metadata["turn"]["prompt_sha256"] = receipt["turn"]["prompt_sha256"] = (
            base._digest(wrong)
        )
    elif tamper == "model":
        call = calls[1]
        calls[1] = (*call[:3], "different-model", *call[4:])
    elif tamper == "usage":
        context["n_cache_tokens"] = 9
    elif tamper == "cut_status":
        metadata["cut_status"]["votes_submitted"] = receipt["cut_status"][
            "votes_submitted"
        ] = 1
    elif tamper == "extra_call":
        call = calls[1]
        calls.append((call[0] + "-vote", *call[1:]))
    elif tamper == "held":
        calls[1] = (*calls[1][:3], None, None, None, None, None, None)
    elif tamper == "reward_file":
        path = trial / "verifier/reward.json"
        base._write(path, base._read(path) | {"reward": 0.0})
    elif tamper == "reward_result":
        result["verifier_result"]["rewards"]["reward"] = 0.0
    elif tamper == "archive_config":
        config = result["config"]
        config["environment"]["force_build"] = False
        base._write(
            trial / "config.json",
            {key: value for key, value in config.items() if key != "agent"},
        )
    base._write(result_path, result)
    if tamper == "receipt_link":
        moved = tmp_path / "receipt.json"
        receipt_path.rename(moved)
        receipt_path.symlink_to(moved)
    else:
        base._write(receipt_path, receipt)
    failed = _cell(_analyze(tmp_path, packet, base._ledger(tmp_path, calls)), "shown")
    expected_status = "exact_prefix_failure" if tamper == "cut_status" else "incomplete"
    assert (failed["status"], failed["reason"], failed["native_vote_reward"]) == (
        expected_status,
        reason,
        None,
    )


def test_mirrored_runtime_mismatch_differs_from_unfinished_replay(tmp_path):
    packet = _packet(tmp_path)
    trial, _ = _trial(tmp_path, packet, "masked")
    result_path = trial / "result.json"
    result = base._read(result_path)
    native = result["seat_results"][0]["agent_result"]["metadata"]
    native.update(status="failed", failure="prefix_chat_post_mismatch", cut_status=None)
    base._write(result_path, result)
    base._write(_receipt(trial), native | {"action": None})
    base._write_events(trial, original._events(trial)[:4])
    failed = _cell(_analyze(tmp_path, packet))
    assert (failed["status"], failed["reason"], failed["prefix_exact"]) == (
        "exact_prefix_failure",
        "prefix_chat_post_mismatch",
        None,
    )
    _receipt(trial).unlink()
    failed = _cell(_analyze(tmp_path, packet))
    assert (failed["status"], failed["reason"], failed["prefix_exact"]) == (
        "incomplete",
        "native_prefix_unfinished",
        None,
    )


def test_duplicate_native_id_does_not_give_either_cell_a_vote(tmp_path):
    packet = _packet(tmp_path)
    masked, calls = _trial(tmp_path, packet, "masked")
    shown, other = _trial(tmp_path, packet, "shown", votes=_SHOWN)
    path = shown / "result.json"
    base._write(
        path, base._read(path) | {"id": base._read(masked / "result.json")["id"]}
    )
    report = _analyze(tmp_path, packet, base._ledger(tmp_path, calls + other))
    assert [_cell(report, arm)["reason"] for arm in ("masked", "shown")] == [
        "duplicate_native_trial"
    ] * 2
    assert report["contrast"]["complete_pairs"] == 0


def test_primary_weights_sources_not_repetitions_and_zero_dissent_has_no_rate(tmp_path):
    packet = _packet(tmp_path, eligible=range(6), no_dissent=(5,))
    calls = []
    for index, repeat in [(0, 1), *((index, 0) for index in range(6))]:
        for arm in ("masked", "shown"):
            votes = _SHOWN if (index, repeat, arm) == (0, 0, "shown") else _MASKED
            _, paid = _trial(
                tmp_path, packet, arm, index=index, repeat=repeat, votes=votes
            )
            calls.extend(paid)
    report = _analyze(tmp_path, packet, base._ledger(tmp_path, calls))
    contrast = report["contrast"]
    assert contrast["complete_pairs"] == 7
    assert contrast["effects"]["native_majority"] == pytest.approx(0.5 / 6)
    assert contrast["maps"][0]["by_arm"]["shown"]["native_majority"] == {
        "numerator": 1,
        "denominator": 2,
    }
    assert contrast["maps"][5]["by_arm"]["shown"]["switch_to_chair"] == {
        "numerator": 0,
        "denominator": 0,
    }
    assert contrast["effects"]["switch_to_chair"] is None
    assert contrast["partial_effects"]["switch_to_chair"]["sources"] == 5
    assert contrast["effects"]["chair_different_choice"] == pytest.approx(0.5 / 6)


def test_missing_ledger_preserves_missing_score_and_bad_packet_digest_raises(tmp_path):
    packet = _packet(tmp_path)
    _trial(tmp_path, packet, "masked")
    row = _cell(_analyze(tmp_path, packet))
    assert (row["status"], row["reason"], row["settled_direct_calls"]) == (
        "incomplete",
        "spend_evidence_unavailable",
        None,
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _analyze(tmp_path, (packet[0], "f" * 64))
