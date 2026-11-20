import json
import os
import stat
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.visibility import (
    boxes_choice,
    boxes_packet,
    boxes_run,
    deepseek,
    intent_packet,
    intent_protocol,
    intent_run,
    spend,
)

_BALANCE = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "USD",
            "total_balance": "3.00",
            "granted_balance": "0.00",
            "topped_up_balance": "3.00",
        }
    ],
}
_PRIVATE_TEXT = "test-only private invalid response"
_REVISIONS = {"masked": (57, 58, 59), "shown": (38, 39, 39)}


def _proposals(block: intent_protocol.Block) -> intent_protocol.Choices:
    a = 20 + block.instance + 2 * block.numbering
    return a, a + block.rotation, 41 + block.repetition


def _response(box: int, numbering: int) -> str:
    return json.dumps({"box": boxes_choice.map_box(box, numbering)})


def _completion(text: str | None) -> dict[str, object]:
    return {
        "model": deepseek.REVISION,
        "usage": {
            "prompt_tokens": 40,
            "prompt_cache_hit_tokens": 7,
            "prompt_cache_miss_tokens": 33,
            "completion_tokens": 8,
            "total_tokens": 48,
        },
        "choices": [{"message": {"content": text}}],
    }


def _receipt(study: Path, run_id: str) -> Path:
    return study / "cells" / run_id / "receipt.json"


def _claims(study: Path) -> set[str]:
    return {path.name for path in (study / "cells").iterdir()}


@pytest.fixture
def host(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unexpected direct provider request")

    ledger = tmp_path.resolve() / "ledger"
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal(3), Decimal(1))
    monkeypatch.setattr(deepseek, "_request", unexpected)
    monkeypatch.setattr(deepseek, "_BOUND", False)
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    return ledger.parent / "study", ledger


@pytest.fixture
def packet(monkeypatch):
    def seat(instance: int, index: int) -> boxes_packet.Case:
        box = index + 1
        return boxes_packet.Case(
            instance=instance,
            own=((box, 1, True),),
            peers=tuple(other for other in (1, 2, 3) if other != box),
            budget_left=8,
            earlier_observation=True,
        )

    worlds = {
        i: intent_packet.World(i, (seat(i, 0), seat(i, 1), seat(i, 2)))
        for i in (1, 2, 6)
    }

    def load(path, expected_sha256):
        assert path == intent_packet.PACKET
        assert expected_sha256 == intent_packet.PACKET_SHA256
        return {
            block.instance: worlds[block.instance] for block in intent_protocol.blocks()
        }

    monkeypatch.setattr(intent_packet, "load", load)
    return worlds


def test_registered_calls_seal_and_reuse_one_numbered_proposal_triple(
    host, packet, monkeypatch, capsys
):
    study, ledger = host
    plan = [
        (block, stage, label)
        for block in intent_protocol.blocks()
        for stage in ("proposal", *block.arms)
        for label in range(3)
    ]
    expected_claims = set()
    index = 0

    def request(path, body, timeout):
        nonlocal index
        if path == "/user/balance":
            assert body is None
            return _BALANCE
        assert path == "/chat/completions"
        block, stage, label = plan[index]
        index += 1
        run_id = intent_protocol.call_id(block, label, stage)
        proposals = _proposals(block)
        receipt = _receipt(study, run_id)
        expected_claims.add(run_id)
        assert not receipt.exists()
        assert stat.S_IMODE(receipt.parent.stat().st_mode) == 0o700
        if label == 0:
            assert _claims(study) == expected_claims
            rows = spend.report(ledger, run_prefix="bi-")["calls"]
            assert [row["run"] for row in rows if row["output"] is None] == [run_id]
            if stage != "proposal":
                for seat, expected in enumerate(proposals):
                    proposal_id = intent_protocol.call_id(block, seat, "proposal")
                    stored = json.loads(_receipt(study, proposal_id).read_bytes())
                    assert stored["original_box"] == expected
        prompt = intent_protocol.prompt(
            packet[block.instance],
            block,
            label,
            stage,
            None if stage == "proposal" else proposals,
        )
        assert json.loads(body) == {
            "model": deepseek.MODEL,
            "messages": [
                {"role": "system", "content": "Answer only with JSON."},
                {"role": "user", "content": prompt},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.7,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        choices = proposals if stage == "proposal" else _REVISIONS[stage]
        return _completion(_response(choices[label], block.numbering))

    monkeypatch.setattr(deepseek, "_request", request)
    assert intent_run.main([str(study), "--spend-dir", str(ledger)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert str(study) not in output.out
    assert json.loads(output.out) == {
        "blocks": 36,
        "scheduled": 324,
        "accepted": 324,
        "invalid": 0,
        "failed": 0,
        "unattempted": 0,
        "skipped_revisions": 0,
        "stopped": None,
    }
    assert index == len(plan) == len(_claims(study)) == 324
    assert stat.S_IMODE(study.stat().st_mode) == 0o700
    assert stat.S_IMODE((study / "cells").stat().st_mode) == 0o700
    for block, stage, label in plan:
        run_id = intent_protocol.call_id(block, label, stage)
        assert run_id.startswith("bi-")
        path = _receipt(study, run_id)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        proposals = _proposals(block)
        prompt = intent_protocol.prompt(
            packet[block.instance],
            block,
            label,
            stage,
            None if stage == "proposal" else proposals,
        )
        choices = proposals if stage == "proposal" else _REVISIONS[stage]
        original_box = choices[label]
        text = _response(original_box, block.numbering)
        assert json.loads(path.read_bytes()) == {
            "schema": "seatlab.box-receiver/v1",
            "run": run_id,
            "role": "box_intent",
            "packet_sha256": intent_packet.PACKET_SHA256,
            "prompt_sha256": sha256(prompt.encode()).hexdigest(),
            "status": "accepted",
            "reason": None,
            "requested_model": deepseek.MODEL,
            "served_model": deepseek.REVISION,
            "usage": {"input": 40, "cached": 7, "output": 8},
            "response_text": text,
            "response_sha256": sha256(text.encode()).hexdigest(),
            "original_box": original_box,
        }
    cost = spend.report(ledger, run_prefix="bi-")
    assert cost["unsettled_calls"] == 0
    assert {row["run"] for row in cost["calls"]} == expected_claims
    assert all(row["role"] == "box_intent" for row in cost["calls"])
    assert len(cost["calls"]) == 324


def test_grammar_invalid_proposal_skips_arms_but_invalid_revision_does_not(
    host, packet, monkeypatch, capsys
):
    study, ledger = host
    first, second = intent_protocol.blocks()[:2]
    monkeypatch.setattr(intent_protocol, "blocks", lambda: (first, second))
    attempted = [
        intent_protocol.call_id(block, label, stage)
        for block, stages in (
            (first, ("proposal",)),
            (second, ("proposal", *second.arms)),
        )
        for stage in stages
        for label in range(3)
    ]
    generated = 0

    def request(path, body, timeout):
        nonlocal generated
        if path == "/user/balance":
            return _BALANCE
        assert path == "/chat/completions"
        run_id = attempted[generated]
        assert _receipt(study, run_id).parent.is_dir()
        invalid = generated in (0, 6)
        generated += 1
        return _completion(_PRIVATE_TEXT if invalid else '{"box":25}')

    monkeypatch.setattr(deepseek, "_request", request)
    assert intent_run.main([str(study), "--spend-dir", str(ledger)]) == 0
    output = capsys.readouterr()
    assert _PRIVATE_TEXT not in output.out + output.err
    assert json.loads(output.out) == {
        "blocks": 2,
        "scheduled": 18,
        "accepted": 10,
        "invalid": 2,
        "failed": 0,
        "unattempted": 6,
        "skipped_revisions": 6,
        "stopped": None,
    }
    assert generated == len(attempted) == 12
    assert _claims(study) == set(attempted)
    for i in (0, 6):
        receipt = json.loads(_receipt(study, attempted[i]).read_bytes())
        assert (receipt["status"], receipt["reason"]) == ("invalid", "invalid_choice")
        assert receipt["response_text"] == _PRIVATE_TEXT
        assert receipt["original_box"] is None
    assert spend.report(ledger, run_prefix="bi-")["unsettled_calls"] == 0


@pytest.mark.parametrize(
    ("failure", "reason", "held"),
    [
        ("provider", "transport_failed", 1),
        ("budget", "budget_unresolved", 0),
        ("evidence", "settlement_unverified", 1),
    ],
)
def test_fatal_failure_stops_in_place_and_retains_prior_receipts(
    host, packet, monkeypatch, capsys, failure, reason, held
):
    study, ledger = host
    block = intent_protocol.blocks()[0]
    prior = [intent_protocol.call_id(block, label, "proposal") for label in range(3)]
    prior.append(intent_protocol.call_id(block, 0, block.arms[0]))
    stopped = intent_protocol.call_id(block, 1, block.arms[0])
    generation_count = 0

    def request(path, body, timeout):
        nonlocal generation_count
        at_failure = generation_count == 4
        if path == "/user/balance":
            if failure == "budget" and at_failure:
                return {
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "USD",
                            "total_balance": "1.50",
                            "granted_balance": "0.00",
                            "topped_up_balance": "1.50",
                        }
                    ],
                }
            return _BALANCE
        assert path == "/chat/completions"
        assert generation_count <= 4
        generation_count += 1
        if at_failure and failure == "provider":
            raise deepseek.SeatUnavailable(_PRIVATE_TEXT)
        return _completion('{"box":25}')

    monkeypatch.setattr(deepseek, "_request", request)
    if failure == "evidence":
        actual_settle = spend.settle

        def omit_settlement(run_id, role, **usage):
            if run_id != stopped:
                actual_settle(run_id, role, **usage)

        monkeypatch.setattr(spend, "settle", omit_settlement)
    assert intent_run.main([str(study), "--spend-dir", str(ledger)]) == 1
    output = capsys.readouterr()
    assert _PRIVATE_TEXT not in output.out + output.err
    assert json.loads(output.out)["stopped"] == {"run": stopped, "reason": reason}
    assert generation_count == 4 + held
    assert _claims(study) == {*prior, stopped}
    for run_id in prior:
        assert json.loads(_receipt(study, run_id).read_bytes())["status"] == "accepted"
    evidence = _receipt(study, stopped).read_text()
    assert _PRIVATE_TEXT not in evidence
    receipt = json.loads(evidence)
    assert (receipt["status"], receipt["reason"]) == ("failed", reason)
    assert receipt["response_text"] is None
    assert receipt["usage"] is None
    cost = spend.report(ledger, run_prefix="bi-")
    assert len(cost["calls"]) == 4 + held
    assert cost["unsettled_calls"] == held


def test_receipt_conflict_preserves_claim_and_prevents_retry(host, packet, monkeypatch):
    study, ledger = host
    block = intent_protocol.blocks()[0]
    first = intent_protocol.call_id(block, 0, "proposal")
    path = _receipt(study, first)

    def request(route, body, timeout):
        if route == "/user/balance":
            return _BALANCE
        assert route == "/chat/completions"
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write("prior receipt")
        return _completion('{"box":25}')

    monkeypatch.setattr(deepseek, "_request", request)
    with pytest.raises(FileExistsError):
        intent_run.run(study, spend_dir=ledger)
    assert path.read_text() == "prior receipt"
    assert _claims(study) == {first}
    with pytest.raises(boxes_run.StudyError, match="^prior_box_reservation$"):
        intent_run.run(study.parent / "another-study", spend_dir=ledger)
    cost = spend.report(ledger, run_prefix="bi-")
    assert [(row["run"], row["output"]) for row in cost["calls"]] == [(first, 8)]


def test_namespace_lock_does_not_serialize_other_paid_studies(
    host, packet, monkeypatch
):
    study, ledger = host
    other = study.parent / "concurrent-intentions"
    spend.reserve("bx-prior", "box_receiver", balance_usd=Decimal(3))

    def request(path, body, timeout):
        if path == "/user/balance":
            return _BALANCE
        assert path == "/chat/completions"
        with pytest.raises(boxes_run.StudyError, match="^box_study_already_running$"):
            intent_run.run(other, spend_dir=ledger)
        spend.reserve("ask-offline-concurrent", "dispatch", balance_usd=Decimal(3))
        return _completion(None)

    monkeypatch.setattr(deepseek, "_request", request)
    with boxes_run._ledger_lock(ledger):
        result = intent_run.run(study, spend_dir=ledger)
    assert result["failed"] == 1
    assert not other.exists()
    assert {row["role"] for row in spend.report(ledger)["calls"]} == {
        "box_receiver",
        "box_intent",
        "dispatch",
    }


@pytest.mark.parametrize(
    "run_id,role", [("bi-unregistered", "inventory"), ("other", "box_intent")]
)
def test_prior_prefix_or_role_reservation_blocks_another_study(host, run_id, role):
    study, ledger = host
    spend.reserve(run_id, role, balance_usd=Decimal(3))
    with pytest.raises(boxes_run.StudyError, match="^prior_box_reservation$"):
        intent_run.run(study, spend_dir=ledger)
    assert not study.exists()


def test_rejects_aliases_existing_claims_insecure_and_uninitialized_ledger(
    host, packet, monkeypatch
):
    study, ledger = host
    alias = study.parent / "alias"
    alias.symlink_to(study.parent, target_is_directory=True)
    with pytest.raises(OSError):
        intent_run.run(alias / study.name, spend_dir=ledger)
    existing = study.parent / "existing"
    existing.mkdir(mode=0o700)
    with pytest.raises(FileExistsError):
        intent_run.run(existing, spend_dir=ledger)
    assert not (existing / "cells").exists()
    uninitialized = study.parent / "uninitialized"
    uninitialized.mkdir(mode=0o700)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(uninitialized))
    with pytest.raises(FileNotFoundError):
        intent_run.run(study, spend_dir=uninitialized)
    assert not (uninitialized / "direct-flash.sqlite3").exists()
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    ledger.chmod(0o755)
    with pytest.raises(boxes_run.StudyError, match="^private_storage_required$"):
        intent_run.run(study, spend_dir=ledger)
    assert not study.exists()


def test_unverified_packet_stops_before_claim_or_reservation(host, monkeypatch):
    study, ledger = host
    changed = study.parent / "changed-packet.json"
    changed.write_text("{}")
    monkeypatch.setattr(intent_packet, "PACKET", changed)
    with pytest.raises(boxes_run.StudyError, match="^packet_unverified$"):
        intent_run.run(study, spend_dir=ledger)
    assert not study.exists()
    assert spend.report(ledger, run_prefix="bi-")["calls"] == []
