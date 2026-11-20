import json
import os
import stat
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.visibility import (
    boxes_choice,
    boxes_packet,
    boxes_run,
    deepseek,
    spend,
)

_SECRET = "private-response-do-not-export"
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
_USAGE = {
    "prompt_tokens": 40,
    "prompt_cache_hit_tokens": 7,
    "prompt_cache_miss_tokens": 33,
    "completion_tokens": 8,
    "total_tokens": 48,
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("unexpected direct provider request")

    monkeypatch.setattr(deepseek, "_request", unexpected)
    monkeypatch.setattr(deepseek, "_BOUND", False)
    monkeypatch.setattr(spend, "_HOST_LEDGER", None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")


@pytest.fixture
def paths(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    ledger = root / "ledger"
    ledger.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal(3), Decimal(1))
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(ledger))
    return root / "study", ledger


@pytest.fixture
def packet(monkeypatch):
    cases = dict.fromkeys(range(1, 11))
    for instance in (1, 3):
        cases[instance] = boxes_packet.Case(
            instance=instance,
            own=((1, 1, True), (2, 1, True)),
            peers=(2, 3, 4),
            budget_left=7,
            earlier_observation=True,
        )
    loads = []

    def load(path, expected_sha256):
        loads.append((path, expected_sha256))
        return cases

    monkeypatch.setattr(boxes_packet, "load", load)
    cells = [cell for cell in boxes_choice.grid() if cases[cell.instance] is not None]
    return cases, cells, loads


def _receipt_path(study, cell):
    return study / "cells" / cell.id / "receipt.json"


def _receipt(study, cell):
    return json.loads(_receipt_path(study, cell).read_bytes())


def _provider(document, monkeypatch):
    def request(path, body, timeout):
        if path == "/user/balance":
            assert body is None
            return _BALANCE
        assert path == "/chat/completions"
        return document() if callable(document) else document

    monkeypatch.setattr(deepseek, "_request", request)


def _completion(text, *, model=deepseek.REVISION, usage=_USAGE):
    return {
        "model": model,
        "usage": usage,
        "choices": [{"message": {"content": text}}],
    }


def test_records_registered_first_choices_and_keeps_invalid_private(
    paths, packet, monkeypatch, capsys
):
    study, ledger = paths
    cases, cells, loads = packet
    payloads = []

    def request(path, body, timeout):
        if path == "/user/balance":
            return _BALANCE
        assert path == "/chat/completions"
        index = len(payloads)
        cell = cells[index]
        claim = _receipt_path(study, cell).parent
        assert stat.S_IMODE(claim.stat().st_mode) == 0o700
        assert not _receipt_path(study, cell).exists()
        rows = spend.report(ledger, run_prefix="bx-")["calls"]
        assert len(rows) == index + 1
        assert any(row["run"] == cell.id and row["output"] is None for row in rows)
        payloads.append(json.loads(body))
        return _completion(_SECRET if index == 1 else '{"box":11}')

    monkeypatch.setattr(deepseek, "_request", request)
    assert boxes_run.main([str(study), "--spend-dir", str(ledger)]) == 0
    captured = capsys.readouterr()
    assert _SECRET not in captured.out + captured.err
    assert str(study) not in captured.out + captured.err
    assert json.loads(captured.out) == {
        "eligible": 16,
        "accepted": 15,
        "invalid": 1,
        "failed": 0,
        "unattempted": 0,
        "stopped": None,
    }
    assert loads == [(boxes_choice.PACKET, boxes_choice.PACKET_SHA256)]
    assert len(payloads) == len(cells)
    assert stat.S_IMODE(study.stat().st_mode) == 0o700
    assert stat.S_IMODE((study / "cells").stat().st_mode) == 0o700
    assert {path.name for path in (study / "cells").iterdir()} == {
        cell.id for cell in cells
    }
    for i, (cell, body) in enumerate(zip(cells, payloads, strict=True)):
        prompt = boxes_choice.prompt(cases[cell.instance], cell)
        assert body == {
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
        invalid = i == 1
        response = _SECRET if invalid else '{"box":11}'
        assert stat.S_IMODE(_receipt_path(study, cell).stat().st_mode) == 0o600
        assert _receipt(study, cell) == {
            "schema": "seatlab.box-receiver/v1",
            "run": cell.id,
            "role": "box_receiver",
            "packet_sha256": boxes_choice.PACKET_SHA256,
            "prompt_sha256": sha256(prompt.encode()).hexdigest(),
            "status": "invalid" if invalid else "accepted",
            "reason": "invalid_choice" if invalid else None,
            "requested_model": deepseek.MODEL,
            "served_model": deepseek.REVISION,
            "usage": {"input": 40, "cached": 7, "output": 8},
            "response_text": response,
            "response_sha256": sha256(response.encode()).hexdigest(),
            "original_box": None if invalid else (50 if cell.numbering else 11),
        }
    assert spend.report(ledger, run_prefix="bx-")["unsettled_calls"] == 0


@pytest.mark.parametrize(
    ("document", "reason", "settled"),
    [
        (_completion(_SECRET, usage=None), "usage_unverified", False),
        (_completion(_SECRET, model=_SECRET), "served_model_unverified", False),
        (_completion(None), "provider_response_invalid", True),
        (_completion("\ud800"), "provider_response_invalid", True),
        (
            _completion("x" * (boxes_run.MAX_RESPONSE_BYTES + 1)),
            "provider_response_invalid",
            True,
        ),
    ],
)
def test_stops_on_indeterminate_or_unrecordable_provider_result(
    paths, packet, monkeypatch, capsys, document, reason, settled
):
    study, ledger = paths
    _, cells, _ = packet
    _provider(document, monkeypatch)
    assert boxes_run.main([str(study), "--spend-dir", str(ledger)]) == 1
    output = capsys.readouterr()
    assert _SECRET not in output.out + output.err
    summary = json.loads(output.out)
    assert summary["stopped"] == {"run": cells[0].id, "reason": reason}
    assert summary["unattempted"] == len(cells) - 1
    failure = _receipt(study, cells[0])
    assert failure["status"] == "failed"
    assert failure["reason"] == reason
    assert failure["response_text"] is None
    assert failure["response_sha256"] is None
    assert failure["original_box"] is None
    assert failure["usage"] == (
        {"input": 40, "cached": 7, "output": 8} if settled else None
    )
    assert failure["served_model"] == (deepseek.REVISION if settled else None)
    assert not _receipt_path(study, cells[1]).parent.exists()
    rows = spend.report(ledger, run_prefix="bx-")
    assert len(rows["calls"]) == 1
    assert rows["unsettled_calls"] == (0 if settled else 1)


def test_transport_failure_keeps_hold_without_leaking_provider_error(
    paths, packet, monkeypatch, capsys
):
    study, ledger = paths
    first = packet[1][0]

    def transport_failure():
        raise deepseek.SeatUnavailable(_SECRET)

    _provider(transport_failure, monkeypatch)
    assert boxes_run.main([str(study), "--spend-dir", str(ledger)]) == 1
    output = capsys.readouterr()
    assert (
        _SECRET not in output.out + output.err + _receipt_path(study, first).read_text()
    )
    assert _receipt(study, first)["reason"] == "transport_failed"
    assert _receipt(study, first)["usage"] is None
    assert spend.report(ledger, run_prefix="bx-")["unsettled_calls"] == 1


def test_returned_turn_without_exact_settlement_has_no_observed_decision(
    paths, packet, monkeypatch
):
    study, ledger = paths
    first = packet[1][0]
    monkeypatch.setattr(
        deepseek,
        "turn",
        lambda *args, **kwargs: deepseek.SeatTurn(_SECRET, deepseek.MODEL, 40, 7, 8),
    )
    result = boxes_run.run(study, spend_dir=ledger)
    assert result["stopped"] == {"run": first.id, "reason": "settlement_unverified"}
    assert _receipt(study, first)["response_text"] is None
    assert _receipt(study, first)["usage"] is None
    assert not _receipt_path(study, packet[1][1]).parent.exists()


def test_account_floor_stops_before_reserving_or_requesting_generation(
    paths, packet, monkeypatch
):
    study, ledger = paths
    paths_requested = []

    def request(path, body, timeout):
        paths_requested.append(path)
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

    monkeypatch.setattr(deepseek, "_request", request)
    result = boxes_run.run(study, spend_dir=ledger)
    assert result["stopped"] == {"run": packet[1][0].id, "reason": "budget_unresolved"}
    assert paths_requested == ["/user/balance"]
    assert not spend.report(ledger, run_prefix="bx-")["calls"]
    assert _receipt(study, packet[1][0])["usage"] is None


@pytest.mark.parametrize("settle", [False, True])
def test_refuses_prior_box_reservations_even_outside_grid_or_role(paths, settle):
    study, ledger = paths
    spend.reserve("bx-unregistered", "inventory", balance_usd=Decimal(3))
    if settle:
        now = datetime.now(UTC)
        spend.settle(
            "bx-unregistered",
            "inventory",
            cache_hit=0,
            cache_miss=20,
            output=5,
            served_model=deepseek.MODEL,
            started_at=now,
            finished_at=now,
        )
    with pytest.raises(boxes_run.StudyError, match="^prior_box_reservation$"):
        boxes_run.run(study, spend_dir=ledger)
    assert not study.exists()


def test_refuses_preexisting_study_and_cannot_open_another_on_active_ledger(
    paths, packet, monkeypatch
):
    study, ledger = paths
    older = study.parent / "existing"
    older.mkdir(mode=0o700)
    with pytest.raises(FileExistsError):
        boxes_run.run(older, spend_dir=ledger)
    assert not (older / "cells").exists()
    other = study.parent / "other"

    def document():
        with pytest.raises(boxes_run.StudyError, match="^box_study_already_running$"):
            boxes_run.run(other, spend_dir=ledger)
        return _completion(None)

    _provider(document, monkeypatch)
    assert boxes_run.run(study, spend_dir=ledger)["failed"] == 1
    assert not other.exists()


def test_never_overwrites_receipt_and_stops_after_storage_conflict(
    paths, packet, monkeypatch
):
    study, ledger = paths
    first, second = packet[1][:2]
    receipt = _receipt_path(study, first)

    def document():
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("prior receipt")
        return _completion('{"box":11}')

    _provider(document, monkeypatch)
    with pytest.raises(FileExistsError):
        boxes_run.run(study, spend_dir=ledger)
    assert receipt.read_text() == "prior receipt"
    assert not _receipt_path(study, second).parent.exists()
    assert len(spend.report(ledger, run_prefix="bx-")["calls"]) == 1


def test_storage_failure_leaves_claim_and_settlement_but_no_receipt(
    paths, packet, monkeypatch, capsys
):
    study, ledger = paths
    first, second = packet[1][:2]
    _provider(_completion('{"box":11}'), monkeypatch)

    def fail_write(directory, receipt):
        raise OSError(_SECRET)

    monkeypatch.setattr(boxes_run, "_write_receipt", fail_write)
    assert boxes_run.main([str(study), "--spend-dir", str(ledger)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"error": "study_io_failed"}
    assert _receipt_path(study, first).parent.is_dir()
    assert not _receipt_path(study, first).exists()
    assert not _receipt_path(study, second).parent.exists()
    rows = spend.report(ledger, run_prefix="bx-")["calls"]
    assert [(row["run"], row["output"]) for row in rows] == [(first.id, 8)]


def test_checks_physical_paths_git_ancestry_private_modes_and_environment(
    paths, monkeypatch
):
    study, ledger = paths
    with pytest.raises(boxes_run.StudyError, match="^physical_absolute_path_required$"):
        boxes_run.run(Path(study.name), spend_dir=ledger)
    alias = study.parent / "alias"
    alias.symlink_to(study.parent, target_is_directory=True)
    with pytest.raises(OSError):
        boxes_run.run(alias / study.name, spend_dir=ledger)
    checkout = study.parent / "worktree"
    checkout.mkdir()
    (checkout / ".git").touch()
    with pytest.raises(
        boxes_run.StudyError, match="^private_paths_outside_repos_required$"
    ):
        boxes_run.run(checkout / study.name, spend_dir=ledger)
    with monkeypatch.context() as local:
        local.setenv("SEATLAB_SPEND_DIR", str(alias / ledger.name))
        with pytest.raises(boxes_run.StudyError, match="^spend_environment_mismatch$"):
            boxes_run.run(study, spend_dir=ledger)
    for value in (None, ""):
        with monkeypatch.context() as local:
            if value is None:
                local.delenv("SEATLAB_SPEND_DIR")
            else:
                local.setenv("SEATLAB_SPEND_DIR", value)
            with pytest.raises(
                boxes_run.StudyError, match="^spend_environment_mismatch$"
            ):
                boxes_run.run(study, spend_dir=ledger)
    with monkeypatch.context() as local:
        local.delenv("DEEPSEEK_API_KEY")
        with pytest.raises(boxes_run.StudyError, match="^host_key_unavailable$"):
            boxes_run.run(study, spend_dir=ledger)
    ledger.chmod(0o755)
    with pytest.raises(boxes_run.StudyError, match="^private_storage_required$"):
        boxes_run.run(study, spend_dir=ledger)
    assert not study.exists()


def test_fails_closed_without_initialized_ledger_or_registered_packet(
    paths, monkeypatch
):
    study, ledger = paths
    other = ledger.parent / "uninitialized"
    other.mkdir(mode=0o700)
    (other / "direct-flash.sqlite3").touch(mode=0o600)
    with monkeypatch.context() as local:
        local.setenv("SEATLAB_SPEND_DIR", str(other))
        with pytest.raises(boxes_run.StudyError, match="^ledger_not_initialized$"):
            boxes_run.run(study, spend_dir=other)
    changed = study.parent / "mutated-packet.json"
    changed.write_text("{}")
    monkeypatch.setattr(boxes_choice, "PACKET", changed)
    with pytest.raises(boxes_run.StudyError, match="^packet_unverified$"):
        boxes_run.run(study, spend_dir=ledger)
    assert not study.exists()
