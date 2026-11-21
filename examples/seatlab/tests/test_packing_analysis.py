import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.handoff import dispatch, packet
from examples.seatlab.handoff.episode import Turn
from examples.seatlab.handoff.packet import Block
from examples.seatlab.packing import analysis
from examples.seatlab.packing.episode import episode
from examples.seatlab.packing.log import key
from examples.seatlab.packing.source import Entry

_FROZEN = sha256(b"packing analysis fixture").hexdigest()
_LONG = "Warehouse stock has been checked. " * 4
_SHORT = "Use the check sheet."


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _row(
    block: Block,
    replies: dict[str, object] | None = None,
    *,
    note: str | None = None,
    invalid_source: bool = False,
) -> dict[str, Any]:
    source_reply = json.dumps({"note": note}) if note is not None else "invalid"
    entry = Entry(
        block,
        "usable" if note is not None else "invalid" if invalid_source else "missing",
        _digest(source_reply) if note is not None or invalid_source else None,
        note,
    )
    name = key(entry, _FROZEN)
    turns: list[dict[str, object]] = []
    halted = False

    def ask(turn: Turn) -> str | None:
        nonlocal halted
        if halted or replies is None or turn.stage not in replies:
            halted = True
            return None
        value = replies[turn.stage]
        raw = json.dumps(value) if value is not None else None
        halted = raw is None
        turns.append(
            {
                "run": f"{name}-{turn.stage}",
                "role": turn.role,
                "status": "callback_error" if halted else "received",
                "prompt_sha256": _digest(turn.prompt),
                "response_sha256": _digest(raw) if raw is not None else None,
                "usage": None,
            }
        )
        return raw

    outcome = episode(entry, ask)
    return {
        "block": name,
        "case": block.case.id,
        "cluster": block.case.cluster,
        "rep": block.rep,
        "fingerprint": _FROZEN,
        "state": "claimed" if replies is not None else "unclaimed",
        "turns": turns,
        "outcome": outcome,
    }


def _case(case: str, cluster: str, size: int, revised: bool) -> dict[str, Any]:
    records = []
    for index in range(size):
        changes = revised and index == size - 1
        records.append(
            {
                "id": str(index),
                "old_version": "1",
                "new_version": "2" if changes else "1",
                "before": "Available bay: A.",
                "after": "Available bay: B." if changes else "Available bay: A.",
                "question": "Which bay is available?",
                "old_best": "A",
                "best": "B" if changes else "A",
            }
        )
    return {"id": case, "cluster": cluster, "records": records}


def _packet() -> packet.Packet:
    return packet.parse(
        json.dumps(
            {
                "schema": "seatlab.handoff-input/v1",
                "brief": "Check the latest warehouse stock.",
                "choices": ["A", "B"],
                "cases": [
                    _case("short", "warehouse-a", 2, False),
                    _case("long", "warehouse-a", 4, True),
                    _case("other", "warehouse-b", 2, False),
                ],
            }
        ).encode()
    )


def _choices(*values: str) -> dict[str, dict[str, str]]:
    return {"choices": {str(index): value for index, value in enumerate(values)}}


def test_unchanged_block_world_weights_and_enrolled_bounds() -> None:
    blocks = packet.grid(_packet())
    rows = [
        _row(
            blocks[0],
            {
                "rewrite": {"note": "Available bay: A."},
                "prefix-decide": _choices("B", "B"),
                "rewrite-decide": _choices("A", "A"),
            },
            note=_LONG,
        ),
        _row(
            blocks[1],
            {"rewrite": {"wrong": "schema"}, "prefix-decide": _choices("A", "A")},
            note=_LONG,
        ),
        _row(
            blocks[2],
            {
                "rewrite": {"note": "Available bay: A."},
                "rewrite-decide": _choices("A", "A", "B", "B"),
                "prefix-decide": _choices("A", "A", "A", "A"),
            },
            note=_LONG,
        ),
        _row(blocks[3], invalid_source=True),
        _row(blocks[4], {"both-decide": _choices("A", "A")}, note=_SHORT),
        _row(blocks[5]),
    ]
    summary = analysis.summarize(blocks, rows)
    primary = summary["primary"]
    assert primary["paired_complete_delta"] == pytest.approx(1 / 6)
    assert primary["worst_delta"] == pytest.approx(-5 / 12)
    assert primary["best_delta"] == pytest.approx(11 / 24)
    assert primary["scheduled_pairs"] == 6
    assert primary["complete_pairs"] == 3
    assert primary["by_cluster"]["warehouse-a"]["complete_pairs"] == 2
    assert summary["arms"]["prefix"]["changed_records"]["accuracy"] == 0
    assert summary["arms"]["rewrite"]["changed_records"]["accuracy"] == 1
    assert summary["arms"]["prefix"]["changed_target_records"]["stale_choices"] == 1
    assert summary["arms"]["rewrite"]["statuses"]["invalid_rewrite"] == 1
    assert summary["counts"]["sources"] == {"invalid": 1, "missing": 1, "usable": 4}
    assert summary["counts"]["shared_final_calls"] == 1
    assert summary["counts"]["recorded_physical_calls"] == 9
    assert summary["counts"]["strategy_cost_exposure"] == {
        "prefix": {"packing_calls": 0, "successor_calls": 4},
        "rewrite": {"packing_calls": 3, "successor_calls": 3},
    }
    assert summary["arms"]["prefix"]["packed_bytes"]["max"] == 96

    rows[4] = _row(blocks[4], {"both-decide": None}, note=_SHORT)
    cold = analysis.summarize(blocks, rows)
    assert cold["primary"]["paired_complete_delta"] is None
    assert cold["primary"]["complete_pairs"] == 2
    assert cold["primary"]["clusters_with_pairs"] == 1
    assert cold["primary"]["enrolled_clusters"] == 2
    assert cold["primary"]["worst_delta"] == pytest.approx(-2 / 3)
    assert cold["primary"]["best_delta"] == pytest.approx(17 / 24)
    assert cold["counts"]["shared_final_calls"] == 1
    assert cold["counts"]["shared_final_complete"] == 0


def _native_report(raw: bytes) -> dict[str, Any]:
    subject = packet.parse(raw)
    blocks = packet.grid(subject)
    correct = {record.id: record.best for record in blocks[0].case.records}
    rows = [
        _row(blocks[0], {"both-decide": {"choices": correct}}, note=_SHORT),
        _row(
            blocks[1],
            {
                "rewrite": {"note": "Verified bay list."},
                "rewrite-decide": {"choices": correct},
                "prefix-decide": {"choices": correct},
            },
            note=_LONG,
        ),
        *[_row(block) for block in blocks[2:]],
    ]
    return {
        "schema": "seatlab.packing-report/v1",
        "packet_sha256": subject.sha256,
        "source_sha256": _FROZEN,
        "outcomes": rows,
        **analysis.summarize(blocks, rows),
    }


def test_stock_regrades_observed_distinct_native_finals() -> None:
    raw = dispatch.packet_bytes()
    report = _native_report(raw)
    check = analysis.stock(report)
    assert check["observed_dossiers"] == 3
    assert check["observed_native_choices"] == 12
    assert check["shared_dossiers"] == 1
    assert check["observed_worlds"] == 1
    assert check["registered_worlds"] == 12
    assert check["primary"]["complete_pairs"] == 2
    assert check["primary"]["paired_complete_delta"] is None

    tampered = deepcopy(report)
    changed = tampered["outcomes"][1]["outcome"]["arms"]["rewrite"]["choices"]
    changed["Q"] = next(bay for bay in "ABCD" if bay != changed["Q"])
    with pytest.raises(ValueError, match="published packing projection"):
        analysis.stock(tampered)

    tampered = deepcopy(report)
    tampered["primary"]["complete_pairs"] = 120
    with pytest.raises(ValueError, match="published packing projection"):
        analysis.stock(tampered)

    tampered = deepcopy(report)
    shared = tampered["outcomes"][0]["outcome"]["arms"]["rewrite"]["choices"].copy()
    shared["Q"] = next(bay for bay in "ABCD" if bay != shared["Q"])
    tampered["outcomes"][0]["outcome"]["arms"]["rewrite"]["choices"] = shared
    with pytest.raises(ValueError, match="inconsistent shared packing decision"):
        analysis.stock(tampered)

    tampered = deepcopy(report)
    tampered["outcomes"][0], tampered["outcomes"][1] = (
        tampered["outcomes"][1],
        tampered["outcomes"][0],
    )
    with pytest.raises(ValueError, match="row identity"):
        analysis.stock(tampered)

    tampered = deepcopy(report)
    tampered["source_sha256"] = _FROZEN[:12] + "e" * 52
    with pytest.raises(ValueError, match="row identity"):
        analysis.stock(tampered)

    with pytest.raises(ValueError, match="requires the default native packet"):
        analysis.stock(_native_report(raw + b" "))


def test_stock_rejects_packet_target_that_disagrees_with_native_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contents = json.loads(dispatch.packet_bytes())
    target = contents["cases"][0]["records"][0]
    target["best"] = next(bay for bay in "ABCD" if bay != target["best"])
    raw = json.dumps(contents).encode()
    report = _native_report(raw)
    monkeypatch.setattr(dispatch, "packet_bytes", lambda: raw)
    with pytest.raises(ValueError, match="packing choice does not match native score"):
        analysis.stock(report)
