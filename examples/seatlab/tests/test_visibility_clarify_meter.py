import json
import sqlite3
from contextlib import closing
from decimal import Decimal

import pytest

from examples.seatlab.visibility import clarify_meter as meter
from examples.seatlab.visibility import clarify_run as runner
from examples.seatlab.visibility import clarify_schedule as schedule
from examples.seatlab.visibility import deepseek, spend

_START = "2026-11-22T11:00:00Z"
_CELLS = [cell for cell in schedule.grid() if cell.window == 0]
_PRIVATE = "unpublishable-receipt-or-ledger-value"


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("the offline meter tried to access the provider")

    monkeypatch.setattr(deepseek, "turn", forbidden)
    monkeypatch.setattr(deepseek, "_request", forbidden)
    root = tmp_path.resolve()
    study, spend_dir = root / "study", root / "private ledger #1"
    plan = runner.seal(schedule.PACKETS, study, start_utc=_START)
    spend_dir.mkdir(mode=0o700)
    spend.initialize(spend_dir, Decimal("12"), Decimal("1"))
    return study, spend_dir, plan


def _receipt(evidence, cell, *, status, stages):
    study, _, plan = evidence
    digest = plan["packet_sha256"][
        schedule.packet_path(schedule.PACKETS, cell.seed).name
    ]
    row = runner._receipt(cell, digest)
    row.update(
        status=status,
        turns={
            stage: {"served_model": deepseek.MODEL, "prompt_sha256": "a" * 64}
            for stage in stages
        },
        selected_case=0,
        delivered_case=cell.delivery(0),
    )
    if status == "complete":
        row.update(actions={"private": _PRIVATE}, grade={})
    path = study / "cells" / f"{cell.cell_id}.json"
    path.write_text(json.dumps(row))
    path.chmod(0o600)
    return path


def _call(
    evidence,
    run_id,
    *,
    start=None,
    finish=None,
    cost=None,
    model=deepseek.MODEL,
    role="dispatch",
):
    with closing(sqlite3.connect(spend._location(evidence[1]))) as db, db:
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                role,
                _START,
                start,
                finish,
                model if cost is not None else None,
                10 if cost is not None else None,
                20 if cost is not None else None,
                30 if cost is not None else None,
                *(cost if cost is not None else (None, None)),
            ),
        )


def _anomalies(window):
    return {call["run_id"]: call["reasons"] for call in window["anomalous_calls"]}


def test_exact_bounds_separate_late_starts_and_finishes(evidence):
    first, late, early = _CELLS[:3]
    for cell in (first, late):
        _receipt(evidence, cell, status="complete", stages=("select", "act"))
    for cell, stage, start, finish in (
        (first, "select", _START, "2026-11-22T11:00:01Z"),
        (first, "act", "2026-11-22T11:30:00Z", "2026-11-22T11:30:01Z"),
        (late, "select", "2026-11-22T11:30:00.000001Z", "2026-11-22T11:30:02Z"),
        (late, "act", "2026-11-22T11:30:03Z", "2026-11-22T11:30:04Z"),
        (early, "select", "2026-11-22T10:59:59Z", _START),
    ):
        _call(
            evidence, f"{cell.cell_id}-{stage}", start=start, finish=finish, cost=(3, 6)
        )

    result = meter.collect(*evidence[:2])
    assert result["overall"]["counts"]["scheduled_max_calls"] == 288
    window, *empty = result["windows"]
    for bounds, found in zip(evidence[2]["windows"], result["windows"], strict=True):
        assert {key: found[key] for key in bounds} == bounds
        assert found["counts"]["scheduled_max_calls"] == 96
    assert all(w["counts"]["no_ledger_row_calls"] == 96 for w in empty)
    assert all(w["first_actual_start_utc"] is None for w in empty)
    assert window["first_actual_start_utc"] == "2026-11-22T10:59:59Z"
    assert window["last_actual_finish_utc"] == "2026-11-22T11:30:04Z"
    counts = window["counts"]
    assert counts["starts_inside_window"] == 2
    assert counts["starts_outside_window"] == 3
    assert counts["starts_before_open"] == 1
    assert counts["starts_after_close"] == 2
    assert counts["finishes_after_close"] == 3
    assert counts["inside_starts_finishing_after_close"] == 1
    assert counts["complete_receipts_matched_to_ledger"] == 2
    assert counts["complete_receipts_with_in_window_starts"] == 1
    assert _anomalies(window)[f"{first.cell_id}-act"] == ["finishes_after_close"]
    assert _anomalies(window)[f"{late.cell_id}-select"] == [
        "starts_after_close",
        "finishes_after_close",
    ]


def test_run_id_assigns_window_even_when_start_falls_in_another_window(evidence):
    other_windows = [
        next(cell for cell in schedule.grid() if cell.window == window)
        for window in (1, 2)
    ]
    for cell, start in zip(
        other_windows, (_START, evidence[2]["windows"][2]["start_utc"]), strict=True
    ):
        _call(
            evidence,
            f"{cell.cell_id}-select",
            start=start,
            finish=start,
            cost=(3, 6),
        )

    result = meter.collect(*evidence[:2])
    first, second, third = result["windows"]
    assert first["counts"]["settled_calls"] == 0
    assert second["counts"]["starts_before_open"] == 1
    assert third["counts"]["starts_inside_window"] == 1
    assert result["overall"]["counts"]["settled_calls"] == 2
    assert (
        result["overall"]["estimated_cost_usd"]["known_settled_high"] == "0.000000012"
    )


def test_paid_select_partial_and_unsettled_act_are_distinct(evidence):
    partial, reserved_without_receipt, unattempted_act = _CELLS[:3]
    for cell in (partial, unattempted_act):
        _receipt(evidence, cell, status="budget_unresolved", stages=("select",))
        _call(
            evidence,
            f"{cell.cell_id}-select",
            start=_START,
            finish="2026-11-22T11:00:01Z",
            cost=(2370, 4740),
        )
    _call(evidence, f"{partial.cell_id}-act")
    _call(evidence, f"{reserved_without_receipt.cell_id}-select")

    result = meter.collect(*evidence[:2])
    window = result["windows"][0]
    counts = window["counts"]
    assert counts["settled_calls"] == counts["attested_named_model_calls"] == 2
    assert counts["no_ledger_row_calls"] == 92
    assert counts["unknown_start_calls"] == counts["unknown_finish_calls"] == 2
    assert counts["unsettled_calls"] == counts["incomplete_receipts"] == 2
    assert counts["receipt_matched_calls"] == 2
    assert counts["complete_receipts"] == 0
    assert counts["complete_receipts_matched_to_ledger"] == 0
    assert window["settled_tokens"] == {"cache_hit": 20, "cache_miss": 40, "output": 60}
    assert (
        window["estimated_cost_usd"]
        == result["overall"]["estimated_cost_usd"]
        == {
            "known_settled_low": "0.000004740",
            "known_settled_high": "0.000009480",
            "unresolved_holds": "0.020000000",
            "upper_including_holds": "0.020009480",
        }
    )
    anomalies = _anomalies(window)
    assert f"{partial.cell_id}-select" not in anomalies
    assert f"{unattempted_act.cell_id}-act" not in anomalies
    assert "unsettled_reservation" in anomalies[f"{partial.cell_id}-act"]
    assert (
        "ledger_without_receipt"
        in anomalies[f"{reserved_without_receipt.cell_id}-select"]
    )


def test_offset_aware_time_unknown_invalid_and_reverse_order(evidence):
    offset, bad, backwards, excessive = _CELLS[:4]
    _receipt(evidence, offset, status="complete", stages=("select", "act"))
    _receipt(evidence, bad, status="complete", stages=("select", "act"))
    for cell, stage, start, finish in (
        (offset, "select", "2026-11-22T16:32:00+05:30", "2026-11-22T08:03:00-03:00"),
        (offset, "act", "2026-11-22T12:02:30+01:00", "2026-11-22T11:04:00Z"),
        (bad, "select", "2026-11-22T11:01:00", "2026-11-22T11:05:00Z"),
        (bad, "act", None, _PRIVATE),
        (backwards, "select", "2026-11-22T09:06:00-02:00", "2026-11-22T19:05:00+08:00"),
        (excessive, "select", "2026-11-22T11:30:00.0000001Z", None),
    ):
        _call(
            evidence, f"{cell.cell_id}-{stage}", start=start, finish=finish, cost=(3, 6)
        )

    result = meter.collect(*evidence[:2])
    window = result["windows"][0]
    assert window["first_actual_start_utc"] == "2026-11-22T11:02:00Z"
    assert window["last_actual_finish_utc"] == "2026-11-22T11:05:00Z"
    counts = window["counts"]
    assert counts["starts_inside_window"] == 2
    assert counts["starts_outside_window"] == 0
    assert counts["invalid_start_calls"] == 3
    assert counts["unknown_start_calls"] == 1
    assert counts["invalid_finish_calls"] == 2
    assert counts["unknown_finish_calls"] == 1
    assert counts["complete_receipts_matched_to_ledger"] == 2
    assert counts["complete_receipts_with_in_window_starts"] == 0
    anomalies = _anomalies(window)
    assert "act_starts_before_select_ends" in anomalies[f"{offset.cell_id}-act"]
    assert "finish_before_start" in anomalies[f"{backwards.cell_id}-select"]
    assert _PRIVATE not in json.dumps(result)


def test_complete_receipt_requires_both_exact_ledger_stages_and_models(evidence):
    missing_act, mismatch, unverified, invalid_receipt = _CELLS[:4]
    for cell in (missing_act, mismatch, unverified, invalid_receipt):
        path = _receipt(evidence, cell, status="complete", stages=("select", "act"))
        if cell == invalid_receipt:
            path.write_text(json.dumps({"unexpected": _PRIVATE}))
        _call(
            evidence, f"{cell.cell_id}-select", start=_START, finish=_START, cost=(3, 6)
        )
    _call(
        evidence,
        f"{mismatch.cell_id}-act",
        start=_START,
        finish=_START,
        cost=(3, 6),
        model=deepseek.REVISION,
    )
    _call(
        evidence,
        f"{unverified.cell_id}-act",
        start=_START,
        finish=_START,
        cost=(3, 6),
        model=_PRIVATE,
    )

    result = meter.collect(*evidence[:2])
    window = result["windows"][0]
    counts = window["counts"]
    assert counts["attested_named_model_calls"] == 5
    assert counts["complete_receipts"] == 3
    assert counts["invalid_receipts"] == 1
    assert counts["receipt_matched_calls"] == 3
    assert counts["complete_receipts_matched_to_ledger"] == 0
    anomalies = _anomalies(window)
    assert anomalies[f"{missing_act.cell_id}-act"] == ["receipt_without_ledger_row"]
    assert anomalies[f"{mismatch.cell_id}-act"] == ["receipt_ledger_model_mismatch"]
    assert anomalies[f"{unverified.cell_id}-act"] == [
        "ledger_model_unattested",
        "receipt_ledger_model_mismatch",
    ]
    assert "receipt_invalid" in anomalies[f"{invalid_receipt.cell_id}-select"]
    assert _PRIVATE not in json.dumps(result)


def test_cli_reads_exact_dispatch_ids_only_and_leaves_database_unchanged(
    evidence, monkeypatch, capsys
):
    study, spend_dir, _ = evidence
    run_id = f"{_CELLS[0].cell_id}-select"
    _call(evidence, run_id)
    for other_run, role in (
        (run_id, "inventory"),
        (f"{run_id}-replay", "dispatch"),
        (_PRIVATE, "dispatch"),
        ("ask-w0-s9999-m0-active-select", "dispatch"),
    ):
        _call(evidence, other_run, role=role, cost=(30, 60))
    ledger_path = spend._location(spend_dir)
    before = ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns
    names = sorted(spend_dir.iterdir())
    connect = sqlite3.connect
    connections = []

    def read_only(database, **kwargs):
        assert kwargs["uri"] is True and database.endswith("?mode=ro")
        connections.append(database)
        return connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", read_only)
    assert meter.main([str(study), "--spend-dir", str(spend_dir)]) == 0
    output = capsys.readouterr()
    assert not output.err
    result = json.loads(output.out)
    assert len(connections) == 1
    assert result["overall"]["counts"]["unsettled_calls"] == 1
    assert result["overall"]["counts"]["settled_calls"] == 0
    assert result["overall"]["counts"]["no_ledger_row_calls"] == 287
    assert set(_anomalies(result["windows"][0])) == {run_id}
    assert _PRIVATE not in output.out and str(spend_dir) not in output.out
    assert (ledger_path.read_bytes(), ledger_path.stat().st_mtime_ns) == before
    assert sorted(spend_dir.iterdir()) == names


def test_missing_ledger_fails_without_creating_database_or_leaking_path(
    evidence, capsys
):
    study, spend_dir, _ = evidence
    absent = spend_dir / _PRIVATE
    absent.mkdir(mode=0o700)
    assert meter.main([str(study), "--spend-dir", str(absent)]) == 1
    output = capsys.readouterr()
    assert not output.out and _PRIVATE not in output.err
    assert json.loads(output.err) == {
        "error": "cannot_read_clarification_plan_receipts_or_ledger"
    }
    assert not list(absent.iterdir())


def test_anomaly_list_is_bounded_by_distinct_public_call_ids(evidence):
    run_ids = [f"{cell.cell_id}-select" for cell in _CELLS]
    for run_id in run_ids:
        _call(evidence, run_id)
    window = meter.collect(*evidence[:2])["windows"][0]
    assert window["counts"]["anomalous_calls"] == len(run_ids)
    assert list(_anomalies(window)) == run_ids[:16]
    assert window["anomalous_calls_omitted"] == len(run_ids) - 16
