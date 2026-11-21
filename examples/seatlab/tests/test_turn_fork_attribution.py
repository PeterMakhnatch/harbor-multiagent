import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.briefing.protocol import REPETITIONS, SEEDS
from examples.seatlab.turn_fork import attribution, protocol

_DIGEST = "a" * 64
_PROMPT = "b" * 64
_REPLY = "c" * 64
_SUBSCRIPTION = "openai-codex/gpt-5.4"
_DIRECT = "deepseek-v4.1-flash-1017"
_DIRECT_USAGE = {
    "served_model": _DIRECT,
    "input_tokens": 13,
    "cache_tokens": 4,
    "output_tokens": 2,
}
type _Document = dict[str, Any]


def _report(transport: str) -> _Document:
    return {
        "schema": "seatlab.turn-fork-report/v1",
        "transport": transport,
        "packet_sha256": _DIGEST,
        "rows": [
            {
                "source_id": f"s{seed}-r{rep}",
                "seed": seed,
                "repetition": rep,
                "arm": arm,
                "state": "unclaimed" if (seed, rep) == (6501, 0) else "no_source",
                "outcome": None,
                "turns": [],
            }
            for seed in SEEDS
            for rep in REPETITIONS
            for arm in protocol.arms(seed, rep)
        ],
        "summary": {},
    }


def _append(
    report: _Document,
    arm: str,
    stage: str,
    *,
    prompt: str = _PROMPT,
    reply: str | None = _REPLY,
) -> _Document:
    row = next(
        r for r in report["rows"] if r["source_id"] == "s6501-r0" and r["arm"] == arm
    )
    row["state"] = "incomplete"
    turn = {
        "stage": stage,
        "role": "inventory" if stage == "push" else "dispatch",
        "run": protocol.run_id(_DIGEST, "s6501-r0", arm, stage),
        "prompt_sha256": prompt,
        "response_sha256": reply,
        "usage": dict(_DIRECT_USAGE)
        if report["transport"] == "direct" and reply is not None
        else None,
        "status": "received" if reply is not None else "callback_error",
    }
    row["turns"].append(turn)
    return turn


@pytest.fixture
def synthetic_service_dirs(tmp_path: Path) -> tuple[Path, Path]:
    inventory = tmp_path.resolve() / "synthetic-inventory"
    dispatch = tmp_path.resolve() / "synthetic-dispatch"
    inventory.mkdir(mode=0o700)
    dispatch.mkdir(mode=0o700)
    return inventory, dispatch


def _synthetic_receipt(
    path: Path,
    turn: _Document,
    *,
    model: str = _SUBSCRIPTION,
    **usage: Any,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "seatlab.omp-seat-receipt/v1",
                "prompt_sha256": turn["prompt_sha256"],
                "reply_sha256": turn["response_sha256"],
                "served_model": model,
                "usage": {
                    "prompt_tokens": 11,
                    "cached_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                }
                | usage,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_service_counts_repeat_receipts_without_assigning_tokens_to_cells(
    synthetic_service_dirs: tuple[Path, Path],
) -> None:
    inventory, dispatch = synthetic_service_dirs
    report = _report("commands")
    aligned = _append(report, "aligned", "push")
    repeated = _append(report, "repeat", "push")
    decision = _append(report, "aligned", "decide", prompt="d" * 64)
    repeat_decision = _append(report, "repeat", "decide", prompt="d" * 64)
    _append(report, "shifted", "push", prompt="e" * 64, reply=None)
    _synthetic_receipt(inventory / "synthetic-a.json", aligned)
    _synthetic_receipt(inventory / "synthetic-b.json", repeated, cached_tokens=0)
    _synthetic_receipt(dispatch / "synthetic-c.json", decision)
    _synthetic_receipt(dispatch / "synthetic-d.json", repeat_decision)

    result = attribution.subscriptions(report, inventory, dispatch, _SUBSCRIPTION)

    assert result["schema"] == "seatlab.turn-fork-service/v1"
    assert result["verified_served_model"] == _SUBSCRIPTION
    assert result["roles"] == {
        "inventory": {
            "matched": 2,
            "callback_errors": 1,
            "input_tokens": 22,
            "cache_tokens": 3,
            "output_tokens": 8,
        },
        "dispatch": {
            "matched": 2,
            "callback_errors": 0,
            "input_tokens": 22,
            "cache_tokens": 6,
            "output_tokens": 8,
        },
    }


def test_service_rejects_missing_identity_even_when_counts_match(
    synthetic_service_dirs: tuple[Path, Path],
) -> None:
    inventory, dispatch = synthetic_service_dirs
    report = _report("commands")
    aligned = _append(report, "aligned", "push")
    repeated = _append(report, "repeat", "push", reply="d" * 64)
    _synthetic_receipt(inventory / "synthetic-a.json", aligned)
    _synthetic_receipt(inventory / "synthetic-b.json", aligned)
    with pytest.raises(ValueError, match="^invalid_turn_fork_service_evidence$"):
        attribution.subscriptions(report, inventory, dispatch, _SUBSCRIPTION)

    _synthetic_receipt(inventory / "synthetic-b.json", repeated)
    _synthetic_receipt(inventory / "synthetic-extra.json", aligned)
    with pytest.raises(ValueError, match="^invalid_turn_fork_service_evidence$"):
        attribution.subscriptions(report, inventory, dispatch, _SUBSCRIPTION)


@pytest.mark.parametrize(
    "change",
    [
        {"cached_tokens": 12},
        {"completion_tokens": -1},
        {"cached_tokens": True},
        {"total_tokens": 14},
        {"model": "openai-codex/gpt-5.5"},
    ],
)
def test_service_rejects_unverified_usage_or_model(
    synthetic_service_dirs: tuple[Path, Path], change: _Document
) -> None:
    inventory, dispatch = synthetic_service_dirs
    report = _report("commands")
    visible = _append(report, "aligned", "push")
    _synthetic_receipt(inventory / "synthetic-malformed.json", visible, **change)
    with pytest.raises(ValueError, match="^invalid_turn_fork_service_evidence$"):
        attribution.subscriptions(report, inventory, dispatch, _SUBSCRIPTION)


def test_service_rejects_receipt_overlap_with_unreceived_call(
    synthetic_service_dirs: tuple[Path, Path],
) -> None:
    inventory, dispatch = synthetic_service_dirs
    report = _report("commands")
    visible = _append(report, "aligned", "push")
    _append(report, "repeat", "push", reply=None)
    _synthetic_receipt(inventory / "synthetic-unattributable.json", visible)
    with pytest.raises(ValueError, match="^invalid_turn_fork_service_evidence$"):
        attribution.subscriptions(report, inventory, dispatch, _SUBSCRIPTION)


def test_service_rejects_an_overlapping_dispatch_with_no_journal_turn(
    synthetic_service_dirs: tuple[Path, Path],
) -> None:
    inventory, dispatch = synthetic_service_dirs
    report = _report("commands")
    aligned = _append(report, "aligned", "push")
    repeated = _append(report, "repeat", "push")
    decision = _append(report, "aligned", "decide", prompt="d" * 64)
    _synthetic_receipt(inventory / "synthetic-a.json", aligned)
    _synthetic_receipt(inventory / "synthetic-b.json", repeated)
    _synthetic_receipt(dispatch / "synthetic-c.json", decision)
    with pytest.raises(ValueError, match="^invalid_turn_fork_service_evidence$"):
        attribution.subscriptions(report, inventory, dispatch, _SUBSCRIPTION)


def _settled(run: str, role: str) -> _Document:
    return {
        "run": run,
        "role": role,
        "served_model": _DIRECT,
        "cache_hit": 4,
        "cache_miss": 9,
        "output": 2,
        "estimated_usd_low": "0.000002562",
        "estimated_usd_high": "0.000005124",
        "unsettled_hold_usd": "0.000000000",
    }


def _hold(run: str, role: str) -> _Document:
    return _settled(run, role) | {
        "served_model": None,
        "cache_hit": None,
        "cache_miss": None,
        "output": None,
        "estimated_usd_low": None,
        "estimated_usd_high": None,
        "unsettled_hold_usd": "0.010000000",
    }


def _ledger(*calls: _Document) -> _Document:
    low = sum(Decimal(c["estimated_usd_low"] or "0") for c in calls)
    high = sum(
        Decimal(c["estimated_usd_high"] or c["unsettled_hold_usd"]) for c in calls
    )
    return {
        "schema": "seatlab.direct-flash-cost/v1",
        "currency": "USD",
        "calls": list(calls),
        "unsettled_calls": sum(c["estimated_usd_high"] is None for c in calls),
        "known_usd_low": f"{low:.9f}",
        "upper_usd_including_holds": f"{high:.9f}",
        "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
    }


def test_direct_preserves_a_lost_response_and_hold_without_visible_decision() -> None:
    report = _report("direct")
    visible = _append(report, "aligned", "push")
    _append(report, "shifted", "push", prompt="d" * 64, reply=None)
    ledger = _ledger(
        _settled(visible["run"], "inventory"),
        _hold(protocol.run_id(_DIGEST, "s6501-r0", "aligned", "decide"), "dispatch"),
        _settled(protocol.run_id(_DIGEST, "s6501-r0", "shifted", "push"), "inventory"),
    )

    result = attribution.direct(report, ledger)

    assert result["schema"] == "seatlab.turn-fork-meter/v1"
    assert result["run_prefix"] == "tf-aaaaaaaaaaaa-"
    assert result["matched_calls"] == 1
    assert result["ledger_only_calls"] == 1
    assert result["unsettled_calls"] == 1
    assert result["known_usd_low"] == "0.000005124"
    assert result["known_usd_high"] == "0.000010248"
    assert result["unsettled_hold_usd"] == "0.010000000"
    assert result["upper_usd_including_holds"] == "0.010010248"
    assert [call["status"] for call in result["calls"]] == [
        "matched",
        "unsettled",
        "ledger_only",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"served_model": "deepseek-flash"},
        {"cache_hit": 3, "cache_miss": 10},
        {"cache_miss": 8},
        {"output": 1},
        {"output": True},
        {"role": "dispatch"},
        {"run": "tf-aaaaaaaaaaaa-s6507-r0-aligned-push"},
        {"run": "tf-bbbbbbbbbbbb-s6501-r0-aligned-push"},
        {"run": "tf-aaaaaaaaaaaa-s6501-r0-repeat-push"},
    ],
)
def test_direct_rejects_wrong_usage_or_off_register_identity(change: _Document) -> None:
    report = _report("direct")
    visible = _append(report, "aligned", "push")
    ledger = _ledger(_settled(visible["run"], "inventory") | change)
    with pytest.raises(ValueError, match="^invalid_turn_fork_ledger$"):
        attribution.direct(report, ledger)


def test_direct_rejects_unsettled_and_missing_received_call() -> None:
    report = _report("direct")
    visible = _append(report, "aligned", "push")
    for ledger in (_ledger(), _ledger(_hold(visible["run"], "inventory"))):
        with pytest.raises(ValueError, match="^invalid_turn_fork_ledger$"):
            attribution.direct(report, ledger)


def test_direct_rejects_second_stage_after_callback_error() -> None:
    report = _report("direct")
    _append(report, "aligned", "push", reply=None)
    _append(report, "aligned", "decide")
    with pytest.raises(ValueError, match="^invalid_turn_fork_report$"):
        attribution.direct(report, _ledger())


def test_direct_rejects_reordered_register() -> None:
    report = _report("direct")
    rows = report["rows"]
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ValueError, match="^invalid_turn_fork_report$"):
        attribution.direct(report, _ledger())
