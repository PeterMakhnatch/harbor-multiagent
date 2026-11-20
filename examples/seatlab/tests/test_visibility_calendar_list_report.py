import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.visibility import (
    calendar_list_packet,
    calendar_list_protocol,
    calendar_list_report,
    spend,
)

_ROLE = "cal_decide"
_MODEL = "deepseek-flash"
_CASE = "rx-w00-d0-r0-blind"


@pytest.fixture
def cell() -> calendar_list_protocol.Cell:
    return next(
        cell
        for cell in calendar_list_protocol.cells(calendar_list_packet.load())
        if cell.case.id == _CASE and cell.draw == 0 and cell.condition == "rr"
    )


@pytest.fixture
def study(tmp_path: Path) -> Path:
    directory = tmp_path.resolve() / "private-list-study"
    directory.mkdir(mode=0o700)
    return directory


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path.resolve() / "private-budget"
    directory.mkdir(mode=0o700)
    spend.initialize(directory, Decimal(12), Decimal(1))
    monkeypatch.setattr(spend, "_HOST_LEDGER", directory)
    return directory


def _write(path: Path, row: dict[str, Any]) -> None:
    path.write_text(json.dumps(row), encoding="utf-8")
    path.chmod(0o600)


def _seal(
    study: Path,
    cell: calendar_list_protocol.Cell,
    text: str,
    **changes: object,
) -> dict[str, Any]:
    path = study / cell.id / "response.json"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    prompt = calendar_list_protocol.prompt(cell.case, cell.condition)
    response = {
        "schema": "seatlab.calendar-list-response/v1",
        "run": cell.id,
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "response_sha256": sha256(text.encode()).hexdigest(),
        "text": text,
    }
    response.update(changes)
    _write(path, response)
    return response


def _meter_file(study: Path, response: dict[str, Any], **changes: object) -> None:
    run = response["run"]
    receipt = {
        "schema": "seatlab.calendar-direct-meter/v1",
        "attempt": 1,
        "status": "success",
        "run": run,
        "role": _ROLE,
        "prompt_sha256": response["prompt_sha256"],
        "response_sha256": response["response_sha256"],
        "served_model": _MODEL,
        "input_tokens": 30,
        "cache_tokens": 8,
        "output_tokens": 13,
    }
    receipt.update(changes)
    _write(study / run / f"{run}_{_ROLE}.json", receipt)


def _reserve(run: str) -> None:
    spend.reserve(run, _ROLE, balance_usd=Decimal(12))


def _settle(run: str) -> None:
    _reserve(run)
    instant = datetime(2026, 11, 20, 12, tzinfo=UTC)
    spend.settle(
        run,
        _ROLE,
        cache_hit=8,
        cache_miss=22,
        output=13,
        served_model=_MODEL,
        started_at=instant,
        finished_at=instant,
    )


def _row(result: dict[str, Any], cell: calendar_list_protocol.Cell) -> dict[str, Any]:
    return next(row for row in result["rows"] if row["run"] == cell.id)


def test_replay_keeps_invalid_missing_and_valid_zero_distinct(
    study: Path, cell: calendar_list_protocol.Cell
) -> None:
    loss = replace(cell, condition="er")
    invalid = replace(cell, condition="re")
    missing = replace(cell, condition="ee")
    (study / missing.id).mkdir(mode=0o700)  # Claimed but never sealed.
    (study / "cl-unregistered-private-run").mkdir(mode=0o700)
    _seal(study, cell, '{ "slot": 26 }')
    _seal(study, loss, '{"slot":0}')
    _seal(study, invalid, '{"slot":26,"slot":26,"private-raw-answer":true}')

    result = calendar_list_report.analyze(study)
    assert set(result) == {"schema", "rows", "summary", "diagnostics"}
    assert result["schema"] == "seatlab.calendar-list-report/v1"
    assert len({row["run"] for row in result["rows"]}) == 192
    assert _row(result, cell) == {
        "run": cell.id,
        "case": _CASE,
        "world": 0,
        "direction": 0,
        "repetition": 0,
        "source_arm": "blind",
        "draw": 0,
        "condition": "rr",
        "status": "complete",
        "slot": 26,
        "reward": 1,
    }
    assert _row(result, loss)["slot"] == 0
    assert _row(result, loss)["reward"] == 0
    assert _row(result, loss)["status"] == "complete"
    assert _row(result, invalid)["slot"] is None
    assert _row(result, invalid)["reward"] == 0
    assert _row(result, invalid)["status"] == "model_invalid"
    assert _row(result, missing)["slot"] is None
    assert _row(result, missing)["reward"] is None
    assert _row(result, missing)["status"] == "missing"
    assert result["summary"]["counts"] == {
        "registered": 192,
        "valid": 2,
        "model_invalid": 1,
        "missing": 189,
        "scored": 3,
        "wins": 1,
        "valid_losses": 1,
    }
    diagnostics = result["diagnostics"]
    assert diagnostics["missing_receipts"] == 189
    assert diagnostics["invalid_receipts"] == 0
    assert diagnostics["meter"]["ledger_status"] == "not_requested"
    assert diagnostics["meter"]["spend"] is None
    assert diagnostics["meter"]["charged_only_calls"] is None
    rendered = json.dumps(result)
    assert str(study) not in rendered
    assert "private-raw-answer" not in rendered
    assert "cl-unregistered-private-run" not in rendered


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("run", "cl-w00-d0-r0-blind-k1-rr", "receipt_invalid"),
        ("prompt_sha256", "0" * 64, "prompt_mismatch"),
        ("response_sha256", "0" * 64, "response_mismatch"),
        ("score", 1, "receipt_invalid"),
    ],
)
def test_unbound_response_stays_unknown_even_if_hashable_and_malformed(
    study: Path,
    cell: calendar_list_protocol.Cell,
    field: str,
    value: object,
    issue: str,
) -> None:
    _seal(study, cell, "not a choice", **{field: value})
    result = calendar_list_report.analyze(study)
    row = _row(result, cell)
    assert row["status"] == "missing"
    assert row["slot"] is row["reward"] is None
    assert result["diagnostics"]["missing_receipts"] == 191
    assert result["diagnostics"]["invalid_receipts"] == 1
    assert result["diagnostics"]["receipt_issues"][issue] == 1
    assert result["summary"]["counts"]["model_invalid"] == 0


def test_wrong_mode_is_not_a_sealed_receipt(
    study: Path, cell: calendar_list_protocol.Cell
) -> None:
    _seal(study, cell, '{"slot":26}')
    (study / cell.id / "response.json").chmod(0o640)
    result = calendar_list_report.analyze(study)
    assert _row(result, cell)["status"] == "missing"
    assert result["diagnostics"]["invalid_receipts"] == 1


def test_cost_uses_original_prefix_totals_and_matches_an_invalid_model_reply(
    study: Path, cell: calendar_list_protocol.Cell, ledger: Path
) -> None:
    _meter_file(study, _seal(study, cell, '{"slot":26,"slot":26}'))
    _settle(cell.id)
    _settle("cl-private-meter-origin")  # Charged, but no registered decision.
    _reserve(replace(cell, condition="ee").id)
    _settle("rx-other-study")

    result = calendar_list_report.analyze(study, ledger)
    meter = result["diagnostics"]["meter"]
    assert _row(result, cell)["status"] == "model_invalid"
    assert meter["ledger_status"] == "available"
    assert meter["matched_calls"] == 1
    assert meter["matched_runs"] == [cell.id]
    assert meter["charged_only_calls"] == 1
    assert meter["held_calls"] == 1
    assert meter["spend"]["known_usd_low"] == "0.000022248"
    assert meter["spend"]["upper_usd_including_holds"] == "0.010022248"
    assert "cl-private-meter-origin" not in json.dumps(result)


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"run": "cl-w00-d0-r0-blind-k1-rr"}, "meter_invalid"),
        ({"role": "cal_recheck"}, "meter_invalid"),
        ({"attempt": True}, "meter_invalid"),
        ({"input_tokens": True}, "meter_invalid"),
        ({"input_tokens": 31}, "ledger_mismatch"),
        ({"served_model": "DeepSeek-V4.1-Flash-1017"}, "ledger_mismatch"),
        ({"prompt_sha256": "0" * 64}, "prompt_mismatch"),
        ({"response_sha256": "0" * 64}, "response_mismatch"),
    ],
)
def test_meter_mismatch_keeps_verified_choice_and_charge(
    study: Path,
    cell: calendar_list_protocol.Cell,
    ledger: Path,
    changes: dict[str, Any],
    issue: str,
) -> None:
    _meter_file(study, _seal(study, cell, '{"slot":26}'), **changes)
    _settle(cell.id)
    result = calendar_list_report.analyze(study, ledger)
    assert _row(result, cell)["status"] == "complete"
    assert _row(result, cell)["reward"] == 1
    meter = result["diagnostics"]["meter"]
    assert meter["matched_runs"] == []
    assert meter["matched_calls"] == 0
    assert meter["charged_only_calls"] == 1
    assert meter["issues"][issue] == 1


def test_meter_cannot_replace_missing_response(
    study: Path, cell: calendar_list_protocol.Cell, ledger: Path
) -> None:
    _meter_file(study, _seal(study, cell, '{"slot":26}'))
    _settle(cell.id)
    (study / cell.id / "response.json").unlink()

    result = calendar_list_report.analyze(study, ledger)
    assert _row(result, cell)["status"] == "missing"
    meter = result["diagnostics"]["meter"]
    assert meter["matched_calls"] == 0
    assert meter["charged_only_calls"] == 1
    assert meter["issues"]["response_unverified"] == 1


def test_missing_ledger_does_not_lose_a_verified_reply(
    study: Path, cell: calendar_list_protocol.Cell
) -> None:
    _seal(study, cell, '{"slot":26}')
    result = calendar_list_report.analyze(study, study / "absent-budget")
    assert _row(result, cell)["reward"] == 1
    meter = result["diagnostics"]["meter"]
    assert meter["ledger_status"] == "unavailable"
    assert meter["spend"] is None
    assert meter["held_calls"] is None
    assert meter["matched_calls"] is None


def test_cli_reports_charges_even_without_a_study_root(
    study: Path,
    ledger: Path,
    cell: calendar_list_protocol.Cell,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _settle(cell.id)
    absent = study / "absent-study"
    assert calendar_list_report.main([str(absent), "--spend-dir", str(ledger)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["diagnostics"]["study_status"] == "missing"
    assert result["diagnostics"]["missing_receipts"] == 192
    assert result["diagnostics"]["meter"]["charged_only_calls"] == 1
    assert result["summary"]["counts"]["scored"] == 0
    assert not absent.exists()
