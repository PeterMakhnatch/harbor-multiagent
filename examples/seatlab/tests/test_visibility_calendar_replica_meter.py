from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.tests import test_visibility_calendar_replica_report as stock
from examples.seatlab.visibility import calendar_replica_meter as meter
from examples.seatlab.visibility import deepseek, spend

_BASE = "rx-w00-d1-r0"
_ROLE = "cal_initial_p1"
_PRIVATE = "private-unregistered-evidence"


@dataclass(frozen=True)
class _Evidence:
    study: Path
    spend_dir: Path
    source: Path
    turns: dict[str, dict[str, str]]

    @property
    def ledger(self) -> Path:
        return self.spend_dir / "direct-flash.sqlite3"

    @property
    def receipt(self) -> Path:
        return self.study / _BASE / "meter" / f"{_BASE}_{_ROLE}.json"

    def settle(self, run: str, role: str, *, model: str = deepseek.MODEL) -> None:
        spend.reserve(run, role, balance_usd=Decimal(12))
        now = datetime.now(UTC)
        spend.settle(
            run,
            role,
            cache_hit=8,
            cache_miss=22,
            output=13,
            served_model=model,
            started_at=now,
            finished_at=now,
        )

    def analyze(self) -> dict[str, Any]:
        return meter.analyze(self.study, self.spend_dir)


def _write(path: Path, row: object) -> None:
    path.write_text(json.dumps(row), encoding="utf-8")
    path.chmod(0o600)


def _block(report: dict[str, Any], run: str = _BASE) -> dict[str, Any]:
    return next(block for block in report["blocks"] if block["id"] == run)


def _call(
    report: dict[str, Any], run: str = _BASE, role: str = _ROLE
) -> dict[str, Any]:
    return next(
        call
        for call in _block(report, run)["calls"]
        if (call["run"], call["role"]) == (run, role)
    )


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Evidence:
    root = tmp_path.resolve()
    study, spend_dir = root / "study", root / "budget #1"
    study.mkdir(mode=0o700)
    spend_dir.mkdir(mode=0o700)
    (study / _BASE).mkdir(mode=0o700)
    (study / _BASE / "meter").mkdir(mode=0o700)

    block = stock._Block(root / "fixture")
    block.out = block.out.rename(study / _BASE / "block")
    block.out.chmod(0o700)
    block.seeds()
    block.branch("peer", 1)
    block.branch("blind", 0)

    def verify(task: Path, world: int) -> stock.source_identity.Identity:
        assert (task, world) == (block.source, 0)
        return stock.source_identity.Identity(stock._CHECKSUM, stock._SEMANTICS)

    monkeypatch.setattr(stock.source_identity, "verify", verify)
    monkeypatch.setattr(
        stock.packet,
        "load",
        lambda *_: (stock.packet.World(0, stock._CHECKSUM, stock._TABLES),),
    )
    native = block.analyze()
    assert native["status"] == "complete"
    assert native["counted_turns"] == 6

    spend.initialize(spend_dir, Decimal(12), Decimal(1))
    monkeypatch.setattr(spend, "_HOST_LEDGER", spend_dir)
    found = _Evidence(study, spend_dir, block.source, native["turns"])
    found.settle(_BASE, _ROLE)
    _write(
        found.receipt,
        {
            "schema": "seatlab.calendar-direct-meter/v1",
            "attempt": 1,
            "status": "success",
            "run": _BASE,
            "role": _ROLE,
            **found.turns[f"{_BASE}:{_ROLE}"],
            "served_model": deepseek.MODEL,
            "input_tokens": 30,
            "cache_tokens": 8,
            "output_tokens": 13,
        },
    )
    return found


def test_replays_native_scores_and_reads_a_real_settlement(evidence: _Evidence) -> None:
    result = evidence.analyze()
    assert result["schema"] == "seatlab.calendar-replica-meter/v1"
    assert result["provider_attested"] is False
    assert result["study_status"] == result["ledger_status"] == "available"
    assert [block["id"] for block in result["blocks"]] == [
        f"rx-w{world:02d}-d{direction}-r{repetition}"
        for repetition in range(2)
        for world in (0, 4, 8)
        for direction in range(2)
    ]
    counts = result["coverage"]
    assert (counts["expected_blocks"], counts["expected_calls"]) == (12, 72)
    identities = {
        (call["run"], call["role"])
        for block in result["blocks"]
        for call in block["calls"]
    }
    assert len(identities) == 72
    assert counts["received_turns"] == 6
    assert counts["meter_files"] == counts["valid_meter_records"] == 1
    assert counts["settled_calls"] == counts["matched_calls"] == 1
    assert counts["unmatched_settled_calls"] == 0
    assert _call(result) == {
        "run": _BASE,
        "role": _ROLE,
        "received_turn": True,
        "meter_status": "success",
        "ledger_status": "settled",
        "matched": True,
        "issues": [],
    }
    assert _block(result)["arms"] == {
        "blind": {"status": "complete", "reward": 0, "slot": 0},
        "peer": {"status": "complete", "reward": 1, "slot": 1},
    }
    assert _block(result, "rx-w04-d0-r0")["native_status"] == "not_started"
    assert result["spend"] == spend.report(evidence.spend_dir, run_prefix="rx-")
    public = json.dumps(result)
    assert str(evidence.source) not in public
    assert "not public" not in public
    assert "sha256" not in public
    assert {item.name for item in evidence.spend_dir.iterdir()} == {
        evidence.ledger.name
    }


def test_cloned_response_hash_does_not_bind(evidence: _Evidence) -> None:
    before = evidence.analyze()
    source = evidence.turns[f"{_BASE}:cal_initial_p2"]["response_sha256"]
    row = json.loads(evidence.receipt.read_text())
    assert source != row["response_sha256"]
    _write(evidence.receipt, {**row, "response_sha256": source})
    after = evidence.analyze()
    call = _call(after)
    assert call["received_turn"] and not call["matched"]
    assert call["issues"] == ["response_mismatch"]
    assert after["coverage"]["unmatched_settled_calls"] == 1
    assert after["spend"] == before["spend"]
    assert _block(after)["arms"] == _block(before)["arms"]


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("prompt_sha256", "a" * 64, "prompt_mismatch"),
        ("served_model", "DeepSeek-V4.1-Flash-1017", "model_mismatch"),
        ("input_tokens", 31, "usage_mismatch"),
        ("cache_tokens", 9, "usage_mismatch"),
        ("output_tokens", 14, "usage_mismatch"),
    ],
)
def test_each_binding_component_must_agree(
    evidence: _Evidence, field: str, value: object, issue: str
) -> None:
    row = json.loads(evidence.receipt.read_text())
    _write(evidence.receipt, {**row, field: value})
    found = _call(evidence.analyze())
    assert not found["matched"]
    assert found["issues"] == [issue]


def test_failed_attempt_can_still_have_a_settled_ledger_row(
    evidence: _Evidence,
) -> None:
    run = "rx-w08-d0-r0"
    parent = evidence.study / run
    parent.mkdir(mode=0o700)
    (parent / "meter").mkdir(mode=0o700)
    row = json.loads(evidence.receipt.read_text())
    row.update(
        status="failed",
        run=run,
        response_sha256=None,
        served_model=None,
        input_tokens=None,
        cache_tokens=None,
        output_tokens=None,
    )
    _write(parent / "meter" / f"{run}_{_ROLE}.json", row)
    evidence.settle(run, _ROLE)
    result = evidence.analyze()
    counts = result["coverage"]
    assert counts["received_turns"] == 6
    assert counts["failed_meter_records"] == 1
    assert counts["settled_calls"] == 2
    assert counts["matched_calls"] == counts["unmatched_settled_calls"] == 1
    failed = _call(result, run)
    assert not failed["received_turn"] and not failed["matched"]
    assert failed["meter_status"] == "failed" and failed["ledger_status"] == "settled"
    assert result["spend"] == spend.report(evidence.spend_dir, run_prefix="rx-")


@pytest.mark.parametrize("root_exists", [False, True])
def test_missing_ledger_is_unknown_and_does_not_create_inputs(
    evidence: _Evidence, root_exists: bool
) -> None:
    absent = evidence.spend_dir.with_name("absent")
    if root_exists:
        absent.mkdir(mode=0o700)
    result = meter.analyze(evidence.study, absent)
    assert result["ledger_status"] == "missing"
    assert result["spend"] is None
    assert result["coverage"]["received_turns"] == 6
    assert result["coverage"]["valid_meter_records"] == 1
    assert result["coverage"]["settled_calls"] is None
    assert result["coverage"]["matched_calls"] is None
    assert _call(result)["matched"] is None
    assert _block(result)["arms"]["peer"]["reward"] == 1
    assert absent.exists() is root_exists
    assert not (absent / evidence.ledger.name).exists()


def test_missing_study_does_not_hide_unbound_spend(evidence: _Evidence) -> None:
    absent = evidence.study.with_name("absent-study")
    result = meter.analyze(absent, evidence.spend_dir)
    assert result["study_status"] == "missing"
    assert result["coverage"]["received_turns"] == 0
    assert result["coverage"]["unmatched_settled_calls"] == 1
    assert result["spend"] == spend.report(evidence.spend_dir, run_prefix="rx-")
    assert not absent.exists()


def test_foreign_activity_is_counted_without_exporting_its_identity(
    evidence: _Evidence,
) -> None:
    evidence.settle(f"rx-{_PRIVATE}", "dispatch", model=_PRIVATE)
    evidence.settle(_BASE, "dispatch")  # Registered base but unregistered role.
    evidence.settle("unrelated-study", "dispatch")
    (evidence.study / _PRIVATE).mkdir(mode=0o700)
    _write(evidence.receipt.parent / f"{_PRIVATE}.json", {_PRIVATE: _PRIVATE})
    result = evidence.analyze()
    counts = result["coverage"]
    assert counts["ledger_rows"] == counts["settled_calls"] == 3
    assert counts["foreign_ledger_rows"] == counts["foreign_settled_calls"] == 2
    assert counts["unmatched_settled_calls"] == 2
    assert counts["foreign_study_entries"] == counts["foreign_meter_entries"] == 1
    assert len(result["spend"]["calls"]) == 1
    known = spend.report(evidence.spend_dir, run_prefix="rx-")
    assert result["spend"]["known_usd_low"] == known["known_usd_low"]
    assert (
        result["spend"]["upper_usd_including_holds"]
        == known["upper_usd_including_holds"]
    )
    assert _PRIVATE not in json.dumps(result)
    assert "unrelated-study" not in json.dumps(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt": True},
        {"attempt": 2},
        {"status": []},
        {"served_model": {_PRIVATE: []}},
        {"output_tokens": False},
        {"cache_tokens": 31},
        {"run": "rx-w00-d0-r0"},
        {"role": "cal_initial_p2"},
        {"response_text": _PRIVATE},
    ],
)
def test_malformed_shapes_never_bind_or_export_values(
    evidence: _Evidence, changes: dict[str, object]
) -> None:
    row = json.loads(evidence.receipt.read_text())
    _write(evidence.receipt, {**row, **changes})
    result = evidence.analyze()
    assert _call(result)["meter_status"] == "invalid"
    assert result["coverage"]["settled_calls"] == 1
    assert result["coverage"]["valid_meter_records"] == 0
    assert _PRIVATE not in json.dumps(result)


def test_duplicate_json_key_is_invalid_even_with_equal_values(
    evidence: _Evidence,
) -> None:
    raw = evidence.receipt.read_text()
    evidence.receipt.write_text(
        raw.replace('"attempt": 1', '"attempt": 1, "attempt": 1')
    )
    assert _call(evidence.analyze())["meter_status"] == "invalid"


def test_duplicate_ledger_ids_cannot_match(evidence: _Evidence) -> None:
    with closing(sqlite3.connect(evidence.ledger)) as db, db:
        db.executescript("""
            ALTER TABLE calls RENAME TO original;
            CREATE TABLE calls AS SELECT * FROM original;
            INSERT INTO calls SELECT * FROM original;
            DROP TABLE original;
        """)
    result = evidence.analyze()
    assert _call(result)["ledger_status"] == "duplicate"
    assert not _call(result)["matched"]
    assert result["coverage"]["duplicate_ledger_rows"] == 2
    assert result["spend"]["known_usd_low"] == "0.000022248"


def test_unsettled_reservation_retains_a_hold(evidence: _Evidence) -> None:
    run = "rx-w08-d0-r0"
    spend.reserve(run, _ROLE, balance_usd=Decimal(12))
    result = evidence.analyze()
    assert _call(result, run)["ledger_status"] == "unsettled"
    assert result["coverage"]["unsettled_calls"] == 1
    assert result["spend"] == spend.report(evidence.spend_dir, run_prefix="rx-")


def test_malformed_ledger_usage_cannot_bind_or_supply_public_usage(
    evidence: _Evidence,
) -> None:
    with closing(sqlite3.connect(evidence.ledger)) as db, db:
        db.execute("UPDATE calls SET hit = ? WHERE run = ?", (_PRIVATE, _BASE))
    result = evidence.analyze()
    assert _call(result)["ledger_status"] == "invalid"
    assert result["coverage"]["invalid_ledger_rows"] == 1
    assert result["spend"]["calls"][0]["cache_hit"] is None
    assert result["spend"]["known_usd_low"] == "0.000011124"
    assert _PRIVATE not in json.dumps(result)


def test_private_modes_are_enforced_independently(evidence: _Evidence) -> None:
    evidence.receipt.chmod(0o640)
    result = evidence.analyze()
    assert _call(result)["meter_status"] == "invalid"
    assert _block(result)["arms"]["peer"]["reward"] == 1
    evidence.receipt.chmod(0o600)
    evidence.receipt.parent.chmod(0o750)
    result = evidence.analyze()
    assert _call(result)["meter_status"] == "unavailable"
    assert _block(result)["arms"]["peer"]["reward"] == 1
    evidence.ledger.chmod(0o640)
    result = evidence.analyze()
    assert result["ledger_status"] == "unsafe"
    assert result["spend"] is None
    assert _block(result)["arms"]["peer"]["reward"] == 1


def test_symlinks_are_not_evidence(evidence: _Evidence) -> None:
    actual_receipt = evidence.receipt.rename(evidence.study.parent / "existing-receipt")
    evidence.receipt.symlink_to(actual_receipt)
    assert _call(evidence.analyze())["meter_status"] == "invalid"
    actual_ledger = evidence.ledger.rename(evidence.study.parent / "existing-ledger")
    evidence.ledger.symlink_to(actual_ledger)
    result = evidence.analyze()
    assert result["ledger_status"] == "unsafe"
    assert result["spend"] is None
    assert _block(result)["arms"]["peer"]["reward"] == 1
