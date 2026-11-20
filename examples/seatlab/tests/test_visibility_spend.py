from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from multiprocessing import get_context
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path

import pytest

from examples.seatlab.visibility import spend

_BALANCE = Decimal("2.00")
_MODEL = "DeepSeek-V4.1-Flash-1017"
_OFFPEAK = datetime(2026, 11, 20, 0, 30, tzinfo=UTC)
_MINUTE = timedelta(minutes=1)


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "private-ledger"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("SEATLAB_SPEND_DIR", str(root))
    return root


def _settle(run: str, *, started_at: datetime = _OFFPEAK) -> None:
    spend.settle(
        run,
        "inventory",
        cache_hit=7,
        cache_miss=11,
        output=13,
        served_model=_MODEL,
        started_at=started_at,
        finished_at=started_at + _MINUTE,
    )


def _race_reserve(gate: Barrier, results: Queue[str]) -> None:
    gate.wait()
    try:
        spend.reserve("same-run", "inventory", balance_usd=_BALANCE)
    except spend.SpendRefused as error:
        results.put(str(error))
    else:
        results.put("reserved")


def test_only_one_process_can_reserve_the_same_run_and_role(ledger: Path) -> None:
    spend.initialize(ledger, _BALANCE, Decimal("1.00"))
    ctx = get_context("spawn")
    gate = ctx.Barrier(2, timeout=15)
    results = ctx.Queue()
    started = []
    try:
        for _ in range(2):
            process = ctx.Process(target=_race_reserve, args=(gate, results))
            process.start()
            started.append(process)
        for process in started:
            process.join(timeout=20)
            assert process.exitcode == 0
        assert sorted(results.get(timeout=1) for _ in started) == [
            "call_already_reserved",
            "reserved",
        ]
    finally:
        for process in started:
            if process.is_alive():
                process.kill()
            process.join()
            process.close()
        results.close()
        results.join_thread()

    receipt = spend.report(ledger)
    assert [(call["run"], call["role"]) for call in receipt["calls"]] == [("same-run", "inventory")]
    assert receipt["unsettled_calls"] == 1
    assert receipt["upper_usd_including_holds"] == "0.010000000"


@pytest.mark.parametrize(("balance", "holds"), [("1.51", 1), ("1.52", 2)])
def test_pending_holds_count_against_the_balance_floor(
    ledger: Path, balance: str, holds: int
) -> None:
    available = Decimal(balance)
    spend.initialize(ledger, available, Decimal("1.00"))
    for index in range(holds):
        spend.reserve(f"pending-{index}", "inventory", balance_usd=available)

    with pytest.raises(spend.SpendRefused, match="^balance_floor$"):
        spend.reserve("would-cross-floor", "dispatch", balance_usd=available)

    assert spend.report(ledger)["unsettled_calls"] == holds


def test_lane_cap_counts_outstanding_holds_and_completed_upper_cost(ledger: Path) -> None:
    spend.initialize(ledger, _BALANCE, Decimal("0.030015000"))
    for run in ("first", "second", "third"):
        spend.reserve(run, "inventory", balance_usd=_BALANCE)

    with pytest.raises(spend.SpendRefused, match="^budget_cap$"):
        spend.reserve("fourth", "inventory", balance_usd=_BALANCE)

    # Three holds plus the completed cost's lower estimate fit; its upper estimate does not.
    _settle("first", started_at=_OFFPEAK.replace(hour=1))
    with pytest.raises(spend.SpendRefused, match="^budget_cap$"):
        spend.reserve("fourth", "inventory", balance_usd=_BALANCE)

    _settle("second")
    spend.reserve("fourth", "inventory", balance_usd=_BALANCE)
    receipt = spend.report(ledger)
    assert receipt["unsettled_calls"] == 2
    assert receipt["upper_usd_including_holds"] == "0.020028413"


def test_shared_cap_counts_account_spend_and_outstanding_holds(ledger: Path) -> None:
    spend.initialize(ledger, Decimal("12.00"), Decimal("1.00"))
    balance = Decimal("2.01")
    spend.reserve("last-within-cap", "inventory", balance_usd=balance)

    with pytest.raises(spend.SpendRefused, match="^budget_cap$"):
        spend.reserve("would-cross-cap", "dispatch", balance_usd=balance)

    assert spend.report(ledger)["unsettled_calls"] == 1


def test_settled_reservation_cannot_be_replaced_or_reopened(ledger: Path) -> None:
    spend.initialize(ledger, _BALANCE, Decimal("1.00"))
    spend.reserve("first-attempt", "inventory", balance_usd=_BALANCE)
    _settle("first-attempt")
    first_receipt = spend.report(ledger)

    with pytest.raises(spend.SpendRefused, match="^call_already_reserved$"):
        spend.reserve("first-attempt", "inventory", balance_usd=_BALANCE)
    with pytest.raises(spend.SpendRefused, match="^no_open_reservation$"):
        _settle("first-attempt", started_at=_OFFPEAK.replace(hour=1))
    assert spend.report(ledger) == first_receipt

    spend.reserve("first-attempt", "dispatch", balance_usd=_BALANCE)
    assert spend.report(ledger)["unsettled_calls"] == 1


@pytest.mark.parametrize(
    ("hit", "miss", "output", "cost"),
    [(7, 0, 0, "0.000000021"), (0, 11, 0, "0.000001650"), (0, 0, 13, "0.000007800")],
)
def test_offpeak_uses_exact_nanodollar_rates(
    ledger: Path, hit: int, miss: int, output: int, cost: str
) -> None:
    spend.initialize(ledger, _BALANCE, Decimal("1.00"))
    spend.reserve("known-usage", "dispatch", balance_usd=_BALANCE)
    spend.settle(
        "known-usage",
        "dispatch",
        cache_hit=hit,
        cache_miss=miss,
        output=output,
        served_model=_MODEL,
        started_at=_OFFPEAK,
        finished_at=_OFFPEAK + _MINUTE,
    )

    receipt = spend.report(ledger)
    assert receipt["calls"] == [
        {
            "run": "known-usage",
            "role": "dispatch",
            "served_model": _MODEL,
            "cache_hit": hit,
            "cache_miss": miss,
            "output": output,
            "estimated_usd_low": cost,
            "estimated_usd_high": cost,
            "unsettled_hold_usd": "0.000000000",
        }
    ]
    assert receipt["known_usd_low"] == receipt["upper_usd_including_holds"] == cost
    assert receipt["unsettled_calls"] == 0


@pytest.mark.parametrize(
    ("day", "hour", "minute", "high"),
    [
        pytest.param(20, 0, 59, "0.000018942", id="enters-first-peak"),
        pytest.param(20, 3, 59, "0.000018942", id="leaves-first-peak"),
        pytest.param(20, 5, 59, "0.000018942", id="enters-second-peak"),
        pytest.param(20, 9, 59, "0.000018942", id="leaves-second-peak"),
        pytest.param(20, 4, 0, "0.000009471", id="between-peaks"),
        pytest.param(21, 0, 59, "0.000009471", id="weekend"),
    ],
)
def test_request_interval_bounds_peak_price_uncertainty(
    ledger: Path, day: int, hour: int, minute: int, high: str
) -> None:
    spend.initialize(ledger, _BALANCE, Decimal("1.00"))
    spend.reserve("priced-call", "inventory", balance_usd=_BALANCE)
    _settle("priced-call", started_at=_OFFPEAK.replace(day=day, hour=hour, minute=minute))

    receipt = spend.report(ledger)
    (call,) = receipt["calls"]
    assert call["estimated_usd_low"] == receipt["known_usd_low"] == "0.000009471"
    assert call["estimated_usd_high"] == receipt["upper_usd_including_holds"] == high


def test_report_keeps_full_holds_and_does_not_invent_usage_or_export_keys(
    ledger: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unrelated_key = "fixture-unrelated-provider-key"
    monkeypatch.setenv("UNRELATED_API_KEY", unrelated_key)
    spend.initialize(ledger, _BALANCE, Decimal("1.00"))
    spend.reserve("missing-usage", "dispatch", balance_usd=_BALANCE)
    spend.reserve("invalid-usage", "dispatch", balance_usd=_BALANCE)
    spend.reserve("measured-usage", "inventory", balance_usd=_BALANCE)
    _settle("measured-usage")

    with pytest.raises(spend.SpendRefused, match="^unknown_usage_kept_full_hold$"):
        spend.settle(
            "invalid-usage",
            "dispatch",
            cache_hit=-1,
            cache_miss=11,
            output=13,
            served_model=_MODEL,
            started_at=_OFFPEAK,
            finished_at=_OFFPEAK + _MINUTE,
        )

    receipt = spend.report(ledger)
    calls = {call["run"]: call for call in receipt["calls"]}
    assert set(calls) == {"missing-usage", "invalid-usage", "measured-usage"}
    for run in ("missing-usage", "invalid-usage"):
        assert calls[run] == {
            "run": run,
            "role": "dispatch",
            "served_model": None,
            "cache_hit": None,
            "cache_miss": None,
            "output": None,
            "estimated_usd_low": None,
            "estimated_usd_high": None,
            "unsettled_hold_usd": "0.010000000",
        }
    assert receipt["unsettled_calls"] == 2
    assert receipt["known_usd_low"] == "0.000009471"
    assert receipt["upper_usd_including_holds"] == "0.020009471"
    exported = json.dumps(receipt)
    assert unrelated_key not in exported
    assert str(ledger) not in exported
