import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from examples.seatlab.visibility import calendar_evallab as bridge
from examples.seatlab.visibility import calendar_packet as packet
from examples.seatlab.visibility.calendar_protocol import blocks

_USAGE = ("input_tokens", "cache_tokens", "output_tokens")


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _path(root, block, arm):
    run = f"{block.id}-{arm}"
    return root / "cells" / run / "trials" / f"calendar-{run}"


def _call(run, role, usage):
    hit, miss, output = (None, None, None) if usage is None else usage
    return {
        "run": run,
        "role": role,
        "served_model": None if usage is None else "deepseek-v4-flash",
        "cache_hit": hit,
        "cache_miss": miss,
        "output": output,
        "estimated_usd_low": None if usage is None else "0.000000001",
        "estimated_usd_high": None if usage is None else "0.000000002",
        "unsettled_hold_usd": "0.010000000" if usage is None else "0.000000000",
    }


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    module = ModuleType("evallab.results")
    loaded = []

    def load_trial(path):
        loaded.append(path)
        raw = json.loads((path / "result.json").read_text())
        return SimpleNamespace(
            path=path,
            result=raw,
            id=raw["id"],
            name=raw["trial_name"],
            config=json.loads((path / "config.json").read_text()),
            primary_reward=float(raw["verifier_result"]["rewards"]["reward"]),
        )

    module.__dict__["load_trial"] = load_trial
    package = ModuleType("evallab")
    package.__dict__["results"] = module
    monkeypatch.setitem(sys.modules, "evallab", package)
    monkeypatch.setitem(sys.modules, "evallab.results", module)
    registered = blocks()
    # Direction 1 makes p2 the sender, regardless of row order in seat_results.
    block, failed = registered[1], registered[0]
    reports: dict[str, dict[str, Any]] = {
        b.id: {
            "block": b.id,
            "world": b.world,
            "direction": b.direction,
            "repetition": b.repetition,
            "arm_order": list(b.arms),
            "arms": {arm: {"status": "unattempted"} for arm in b.arms},
        }
        for b in registered
    }
    reports[failed.id]["arms"]["blind"] = {"status": "native_failure"}
    calls = [
        _call(block.id, "cal_initial_p1", (1, 2, 3)),
        _call(block.id, "cal_initial_p2", (0, 2, 4)),
        _call(f"{failed.id}-blind", "cal_recheck", None),
        _call("cf-unregistered", "cal_decide", (2, 3, 4)),
    ]
    fixture = tmp_path / "flotilla" / f"schedule-{block.world:03d}-talk"
    world = packet.load(packet.PACKET, packet.PACKET_SHA256)[block.world]
    for arm, invalid in (("blind", None), ("peer", block.sender)):
        path = _path(tmp_path, block, arm)
        run = f"{block.id}-{arm}"
        config = {"task": {"path": str(fixture)}}
        _write(path / "config.json", config)
        usage = {}
        seats: list[dict[str, Any]] = []
        for receiver, seat in enumerate((block.sender, block.receiver)):
            no_call = receiver == 1 and invalid == block.sender
            role = "cal_decide" if receiver else "cal_recheck"
            counts = (3, 4, 5) if receiver else (2, 4, 8)
            hit, miss, output = counts
            measured = dict(zip(_USAGE, (hit + miss, hit, output), strict=True))
            usage[seat] = None if no_call else measured
            if not no_call:
                calls.append(_call(run, role, counts))
            phase = "model_invalid" if seat == invalid else "complete"
            context: dict[str, Any] = {
                f"n_{k}": None if no_call else n for k, n in measured.items()
            }
            context["metadata"] = {
                "calendar": {
                    "run": run,
                    "seat": seat,
                    "control": False,
                    "stage": "peer_model_invalid" if no_call else phase,
                    "incoming_id": f"m{1 + receiver}",
                    "outgoing_id": f"m{2 - receiver}",
                }
            }
            seats.append(
                {
                    "seat": seat,
                    "under_test": True,
                    "exception_info": None,
                    "agent_result": context,
                }
            )
        reward = int(invalid is None)
        reports[block.id]["arms"][arm] = {
            "status": "complete" if invalid is None else "model_invalid_scored_zero",
            "invalid_seat": invalid,
            "reward": reward,
            "tokens": usage,
        }
        _write(
            path / "result.json",
            {
                "id": str(uuid4()),
                "trial_name": path.name,
                "trial_uri": path.as_uri(),
                "task_checksum": world.task_checksum,
                "task_id": {"path": str(fixture)},
                "task_name": f"flotilla/{fixture.name}",
                "exception_info": None,
                "reward_valid": True,
                "started_at": f"2026-01-01T00:0{block.arms.index(arm)}:00Z",
                "finished_at": f"2026-01-01T00:0{block.arms.index(arm)}:01Z",
                "seat_results": sorted(seats, key=lambda row: row["seat"]),
                "config": config,
                "verifier_result": {"rewards": {"reward": float(reward)}},
            },
        )
    report = {
        "schema": "seatlab.calendar-feedback-report/v1",
        "blocks": list(reports.values()),
    }
    ledger = {
        "schema": "seatlab.direct-flash-cost/v1",
        "currency": "USD",
        "calls": calls,
        "unsettled_calls": 1,
        "known_usd_low": "0.000000006",
        "upper_usd_including_holds": "0.010000012",
    }
    return SimpleNamespace(
        root=tmp_path,
        report=report,
        ledger=ledger,
        block=block,
        failed=failed,
        loaded=loaded,
    )


def test_calendar_evallab_cli_aggregates_cohort_and_matched_seats(evidence, capsys):
    root = evidence.root
    _write(root / "report.json", evidence.report)
    _write(root / "cost.json", evidence.ledger)
    argv = [
        "--study",
        str(root),
        "--report",
        str(root / "report.json"),
        "--cost",
        str(root / "cost.json"),
    ]
    assert bridge.main(argv) == 0
    output = capsys.readouterr().out
    aggregate = json.loads(output)
    assert aggregate["matched_trials"] == 2
    assert aggregate["matched_paid_seats"] == 3
    assert aggregate["skipped_statuses"] == {"native_failure": 1, "unattempted": 93}
    assert aggregate["matched_native"] == {
        "calls": 3,
        "unsettled_calls": 0,
        "input_tokens": 19,
        "cache_tokens": 7,
        "output_tokens": 21,
        "known_usd_low": "0.000000003",
        "upper_usd_including_holds": "0.000000006",
    }
    assert aggregate["cohort"] == {
        "calls": 7,
        "unsettled_calls": 1,
        "input_tokens": 29,
        "cache_tokens": 10,
        "output_tokens": 32,
        "known_usd_low": "0.000000006",
        "upper_usd_including_holds": "0.010000012",
    }
    assert aggregate["seed_calls"]["calls"] == 2
    assert aggregate["unscored_calls"]["unsettled_calls"] == 1
    assert aggregate["unmatched_ledger"]["calls"] == 1
    assert set(evidence.loaded) == {
        _path(root, evidence.block, arm) for arm in evidence.block.arms
    }
    assert str(root) not in output
    assert "cf-" not in output


@pytest.mark.parametrize(
    "defect",
    ["lost_seat", "seat_role", "native_usage", "raw_reward", "zero_uuid", "dup_uuid"],
)
def test_calendar_evallab_rejects_native_mismatches(evidence, defect):
    path = _path(evidence.root, evidence.block, "blind") / "result.json"
    raw = json.loads(path.read_text())
    if defect == "lost_seat":
        raw["seat_results"].pop()
    elif defect == "seat_role":
        raw["seat_results"][0]["under_test"] = False
    elif defect == "native_usage":
        raw["seat_results"][0]["agent_result"]["n_cache_tokens"] += 1
    elif defect == "raw_reward":
        # The reader coerces this to 1.0, but the raw score is not numeric.
        raw["verifier_result"]["rewards"]["reward"] = True
    elif defect == "zero_uuid":
        raw["id"] = str(UUID(int=0))
    else:
        # An unscored native record still participates in the UUID census.
        path = _path(evidence.root, evidence.failed, "blind") / "result.json"
        raw = {"id": raw["id"]}
    _write(path, raw)
    with pytest.raises(ValueError):
        bridge.reconcile(evidence.root, evidence.report, evidence.ledger)


@pytest.mark.parametrize(
    "defect", ["lost_call", "wrong_cache_split", "paid_no_call_peer", "duplicate_call"]
)
def test_calendar_evallab_rejects_ledger_mismatches(evidence, defect):
    calls = evidence.ledger["calls"]
    target = next(
        row
        for row in calls
        if row["run"] == f"{evidence.block.id}-blind" and row["role"] == "cal_decide"
    )
    if defect == "lost_call":
        calls.remove(target)
    elif defect == "wrong_cache_split":
        target["cache_hit"] += 1
        target["cache_miss"] -= 1
    elif defect == "paid_no_call_peer":
        calls.append({**target, "run": f"{evidence.block.id}-peer"})
    else:
        calls.append(target.copy())
    with pytest.raises(
        ValueError, match="(ledger|settled seat usage|duplicate spend row)"
    ):
        bridge.reconcile(evidence.root, evidence.report, evidence.ledger)


def test_calendar_evallab_does_not_promote_raw_score_or_trust_report_score(evidence):
    branch = evidence.report["blocks"][1]["arms"]["blind"]
    branch["reward"] = 0
    with pytest.raises(ValueError, match="native reward differs"):
        bridge.reconcile(evidence.root, evidence.report, evidence.ledger)
    branch.clear()
    branch["status"] = "invalid_evidence"
    aggregate = bridge.reconcile(evidence.root, evidence.report, evidence.ledger)
    assert aggregate["matched_trials"] == 1
    assert aggregate["unscored_calls"]["calls"] == 3
