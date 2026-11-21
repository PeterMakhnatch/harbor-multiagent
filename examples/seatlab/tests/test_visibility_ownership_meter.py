import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from examples.seatlab.visibility import (
    deepseek,
    intent_packet,
    intent_protocol,
    ownership_driver,
    ownership_meter,
    ownership_protocol,
    spend,
)

_BASE = datetime(2026, 11, 21, 12, tzinfo=UTC)
_MODEL = deepseek.MODEL
_PRIVATE = "unpublishable-raw-reply-or-foreign-id"


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("the meter called the provider")

    monkeypatch.setattr(deepseek, "turn", forbidden)
    monkeypatch.setattr(deepseek, "_request", forbidden)
    study, ledger = (tmp_path.resolve() / name for name in ("study", "ledger"))
    for directory in study, ledger:
        directory.mkdir(mode=0o700)
    spend.initialize(ledger, Decimal(12), Decimal(1))
    return study, ledger


def _registered(*, numbering=1, rotation=2, instance=1):
    return next(
        block
        for block in intent_protocol.blocks()
        if (block.instance, block.numbering, block.rotation)
        == (instance, numbering, rotation)
    )


def _document(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def _arm(evidence, block, policy):
    study, _ = evidence
    directory = study / ownership_protocol.run_id(block, policy)
    directory.mkdir(mode=0o700)
    (directory / "meter").mkdir(mode=0o700)
    return directory


def _receipts(directory, block, policy, *texts):
    proposals = ownership_protocol.seeds()[block.id]
    world = intent_packet.load()[block.instance]

    def callback(seat, prompt):
        text = texts[ownership_driver.SEATS.index(seat)]
        _document(
            directory / "meter" / f"{seat}.json",
            {
                "schema": ownership_driver.METER,
                "run": f"{directory.name}-{seat}",
                "role": ownership_driver.ROLE,
                "seat": seat,
                "prompt_sha256": sha256(prompt.encode()).hexdigest(),
                "response_sha256": sha256(text.encode()).hexdigest(),
                "served_model": _MODEL,
                "input": 18,
                "cached": 4,
                "output": 7,
            },
        )
        return text

    reply = ownership_driver._respond(directory, block, policy, proposals, callback)
    ordered = [ownership_driver.SEATS[block.seat_index(label)] for label in range(3)]
    for label, seat in enumerate(ordered):
        if texts[ownership_driver.SEATS.index(seat)] is None:
            break
        reply(seat, ownership_protocol.prompt(world, block, label, policy, proposals))
    return ordered


def _ledger_row(
    evidence,
    call,
    *,
    role=ownership_driver.ROLE,
    usage=(4, 14, 7),
    model=_MODEL,
    reserved=20,
    started=21,
    finished=22,
):
    def iso(offset):
        return (_BASE + timedelta(seconds=offset)).isoformat()

    with closing(sqlite3.connect(spend._location(evidence[1]))) as db, db:
        if usage is None:
            values = (None,) * 8
        else:
            hit, miss, output = usage
            cost = hit * 3 + miss * 150 + output * 600
            values = iso(started), iso(finished), model, hit, miss, output, cost, cost
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (call, role, iso(reserved), *values),
        )


def _report_arm(result, directory):
    return next(arm for arm in result["arms"] if arm["run"] == directory.name)


def _native_events(directory, block, choices, *, prefix=18, first_open=38):
    trial = directory / "trials" / directory.name
    for subdir in trial.parent, trial, trial / "verifier":
        subdir.mkdir(mode=0o700)
    events = []

    def add(kind, seat, offset, request):
        events.append(
            {
                "seq": len(events) + 1,
                "kind": kind,
                "seat": seat,
                "t": _BASE.timestamp() + offset,
                "method": "POST",
                "status": 200,
                "ok": True,
                "request": request,
                "response": {},
            }
        )

    world = intent_packet.load()[block.instance]
    for seat, case in zip(ownership_driver.SEATS, world.seats, strict=True):
        for box, _, _ in case.own:
            add("open", seat, 3, {"box": box})
    for _ in range({1: 9, 2: 7, 6: 9}[block.instance]):
        add("msg", "p1", prefix, {"message": _PRIVATE})
    for index, seat in enumerate(ownership_driver.SEATS):
        add("open", seat, first_open + index, {"box": choices[seat]})
    add("admin.close", None, first_open + 4, {})
    path = trial / "verifier" / "events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in events))
    path.chmod(0o600)
    _document(
        trial / "result.json",
        {
            "trial_name": directory.name,
            "started_at": _BASE.isoformat(),
            "finished_at": (_BASE + timedelta(seconds=50)).isoformat(),
        },
    )
    return path


def test_exact_match_includes_grammar_invalid_paid_response(evidence):
    block = _registered()
    directory = _arm(evidence, block, "owner")
    ordered = _receipts(directory, block, "owner", '{"box":4}', _PRIVATE, '{"box":23}')
    for seat in ordered:
        _ledger_row(evidence, f"{directory.name}-{seat}")
    result = ownership_meter.analyze(*evidence)
    arm = _report_arm(result, directory)
    counts = arm["counts"]
    assert ordered == ["p2", "p3", "p1"]
    assert [turn["label"] for turn in arm["turns"]] == ["A", "B", "C"]
    assert [turn["direct"]["original_box"] for turn in arm["turns"]] == [None, 38, 57]
    assert [
        counts[key]
        for key in (
            "expected_calls",
            "received_calls",
            "matched_calls",
            "grammar_invalid_calls",
        )
    ] == [3, 3, 3, 1]
    assert arm["native_generation_window"]["status"] == "unknown"
    assert arm["ledger_cost"]["known_usd_low"] == "0.000018936"
    assert arm["direct_usage"]["output"] == 21
    assert result["counts"]["expected_calls"] == 216
    assert len(result["arms"]) == 72
    assert result["provider_attested"] is False
    assert _PRIVATE not in json.dumps(result)


def test_mismatches_and_holds_keep_real_ledger_amounts(evidence):
    block = _registered(numbering=0, rotation=0)
    directory = _arm(evidence, block, "neutral")
    _receipts(directory, block, "neutral", '{"box":4}', '{"box":5}', '{"box":6}')
    run = directory.name
    _ledger_row(evidence, f"{run}-p1", usage=(5, 13, 7))
    _ledger_row(evidence, f"{run}-p2", model=deepseek.REVISION)
    _ledger_row(evidence, f"{run}-p3", usage=None)
    _ledger_row(evidence, f"{run}-p3", role="dispatch", usage=None)
    _ledger_row(evidence, "bo-" + _PRIVATE)
    _ledger_row(evidence, _PRIVATE)  # Non-study calls are not in either cost total.

    result = ownership_meter.analyze(*evidence)
    arm = _report_arm(result, directory)
    assert [turn["match"] for turn in arm["turns"]] == [
        "usage_or_model_mismatch",
        "usage_or_model_mismatch",
        "unsettled",
    ]
    assert arm["counts"]["unmatched_rows"] == 3
    assert arm["counts"]["unexpected_rows"] == 1
    assert arm["counts"]["unmatched_receipts"] == 3
    assert result["counts"]["unexpected_rows"] == 2
    assert result["counts"]["settled_calls"] == 3
    assert result["counts"]["unsettled_calls"] == 2
    assert result["ledger_cost"]["upper_usd_including_holds"] == "0.020018789"
    assert result["direct_usage"]["estimated_usd_low"] == "0.000018936"
    assert _PRIVATE not in json.dumps(result)


def test_wrong_prompt_and_stale_response_digests_cannot_bind_usage(evidence):
    block = _registered(numbering=0, rotation=0)
    directory = _arm(evidence, block, "neutral")
    _receipts(directory, block, "neutral", '{"box":1}', '{"box":2}', '{"box":3}')
    run = directory.name
    p1 = directory / "meter" / "p1.json"
    p2 = directory / "turns" / "p2.json"
    for file, field, value in (
        (p1, "prompt_sha256", "0" * 64),
        (p2, "response_text", _PRIVATE),
    ):
        doc = json.loads(file.read_text())
        doc[field] = value
        _document(file, doc)
    (directory / "meter" / "p3.json").unlink()  # Native-only receipt.
    _ledger_row(evidence, f"{run}-p1")
    _ledger_row(evidence, f"{run}-p2")

    result = ownership_meter.analyze(*evidence)
    arm = _report_arm(result, directory)
    assert arm["counts"]["native_receipts"] == 2
    assert arm["counts"]["received_calls"] == 0
    assert arm["counts"]["unmatched_rows"] == 2
    assert arm["direct_usage"] is None
    assert arm["ledger_cost"]["known_usd_low"] == "0.000012624"
    assert arm["turns"][2]["match"] == "missing_ledger_row"
    assert _PRIVATE not in json.dumps(result)


def test_missing_ledger_does_not_create_a_database(evidence, capsys):
    study, ledger = evidence
    spend._location(ledger).unlink()
    block = _registered()
    directory = _arm(evidence, block, "owner")
    _receipts(directory, block, "owner", '{"box":1}', '{"box":2}', '{"box":3}')
    assert (
        ownership_meter.main(["--study", str(study), "--spend-dir", str(ledger)]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ledger_status"] == "missing"
    assert result["ledger_cost"] is None
    assert result["counts"]["received_calls"] == 3
    assert result["counts"]["unmatched_receipts"] == 3
    assert list(ledger.iterdir()) == []
    assert str(ledger) not in json.dumps(result)


def test_unsafe_paths_and_file_modes_are_not_read(evidence):
    study, ledger = evidence
    db = spend._location(ledger)
    real_db = ledger / "copy"
    db.rename(real_db)
    db.symlink_to(real_db)
    block = _registered(numbering=0, rotation=0)
    directory = _arm(evidence, block, "neutral")
    _receipts(directory, block, "neutral", '{"box":1}', '{"box":2}', '{"box":3}')
    p1 = directory / "meter" / "p1.json"
    p1.rename(p1.with_suffix(".private"))
    p1.symlink_to(p1.with_suffix(".private"))
    (directory / "turns" / "p2.json").chmod(0o644)
    result = ownership_meter.analyze(study, ledger)
    arm = _report_arm(result, directory)
    assert result["ledger_status"] == "invalid"
    assert result["ledger_cost"] is None
    assert arm["counts"]["received_calls"] == 1
    assert arm["turns"][0]["meter_receipt"] == "invalid"
    assert arm["turns"][1]["native_receipt"] == "invalid"


@pytest.mark.parametrize(
    ("prefix", "first_open", "expected"),
    [
        (18, 38, "confirmed"),
        (22, 38, "violated"),
        (18, 27, "violated"),
        (21, 38, "unknown"),
    ],
)
def test_native_window_uses_both_service_and_local_ledger_clocks(
    evidence, prefix, first_open, expected
):
    block = _registered(instance=6)
    directory = _arm(evidence, block, "owner")
    ordered = _receipts(
        directory, block, "owner", '{"box":1}', '{"box":2}', '{"box":3}'
    )
    _native_events(
        directory,
        block,
        {"p1": 60, "p2": 59, "p3": 58},
        prefix=prefix,
        first_open=first_open,
    )
    for index, seat in enumerate(ordered):
        t = 20 + index * 4
        _ledger_row(
            evidence,
            f"{directory.name}-{seat}",
            reserved=t,
            started=t + 1,
            finished=t + 2,
        )

    result = ownership_meter.analyze(*evidence)
    arm = _report_arm(result, directory)
    assert arm["native_generation_window"]["status"] == expected
    assert sum(result["native_generation_window"].values()) == 72
    assert result["native_generation_window"][expected] == (
        72 if expected == "unknown" else 1
    )
    assert arm["counts"]["matched_calls"] == 3
    assert arm["ledger_cost"]["known_usd_low"] == "0.000018936"
    assert _PRIVATE not in json.dumps(result)


def test_invalid_chronology_stays_unknown_without_erasing_charges(evidence):
    block = _registered(numbering=0, rotation=0)
    directory = _arm(evidence, block, "neutral")
    ordered = _receipts(
        directory, block, "neutral", '{"box":1}', '{"box":2}', '{"box":3}'
    )
    path = _native_events(directory, block, {"p1": 1, "p2": 2, "p3": 3})
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    lines[-2]["t"] = lines[0]["t"]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    for index, seat in enumerate(ordered):
        t = 20 + index * 4
        _ledger_row(
            evidence,
            f"{directory.name}-{seat}",
            reserved=t,
            started=t + 1,
            finished=t + 2,
        )

    result = ownership_meter.analyze(*evidence)
    arm = _report_arm(result, directory)
    assert arm["native_generation_window"] == {
        "status": "unknown",
        "reason": "native_chronology_invalid",
    }
    assert arm["counts"]["matched_calls"] == 3
    assert arm["ledger_cost"]["known_usd_low"] == "0.000018936"


def test_interrupted_arm_keeps_a_hold_and_a_settlement_without_receipts(evidence):
    block = _registered(numbering=0, rotation=0)
    directory = _arm(evidence, block, "neutral")
    _receipts(directory, block, "neutral", '{"box":1}', None, None)
    _ledger_row(evidence, f"{directory.name}-p1")
    _ledger_row(evidence, f"{directory.name}-p2", usage=None)
    _ledger_row(evidence, f"{directory.name}-p3")

    arm = _report_arm(ownership_meter.analyze(*evidence), directory)
    assert [turn["match"] for turn in arm["turns"]] == [
        "matched",
        "unsettled",
        "missing_verified_receipt",
    ]
    assert arm["counts"]["native_receipts"] == 1
    assert arm["counts"]["received_calls"] == 1
    assert arm["counts"]["matched_calls"] == 1
    assert arm["counts"]["unmatched_rows"] == 2
    assert arm["ledger_cost"]["upper_usd_including_holds"] == "0.010012624"


def test_native_windows_do_not_impose_an_order_between_arms(evidence):
    block = _registered(numbering=0, rotation=0)
    for policy in ownership_protocol.arms(block):
        directory = _arm(evidence, block, policy)
        ordered = _receipts(
            directory, block, policy, '{"box":1}', '{"box":2}', '{"box":3}'
        )
        _native_events(directory, block, {"p1": 1, "p2": 2, "p3": 3})
        for index, seat in enumerate(ordered):
            t = 20 + index * 4
            _ledger_row(
                evidence,
                f"{directory.name}-{seat}",
                reserved=t,
                started=t + 1,
                finished=t + 2,
            )

    result = ownership_meter.analyze(*evidence)
    assert result["native_generation_window"] == {
        "confirmed": 2,
        "violated": 0,
        "unknown": 70,
    }
