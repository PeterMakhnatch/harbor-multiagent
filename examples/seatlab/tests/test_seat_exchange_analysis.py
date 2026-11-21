from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import pytest

from examples.seatlab.exchange.analysis import stock, summarize
from examples.seatlab.handoff.native_input import Corpus
from examples.seatlab.handoff.packet import grid, parse
from examples.seatlab.visibility.clarify_run import _encode

if TYPE_CHECKING:
    from examples.seatlab.handoff.packet import Block

type _Document = dict[str, Any]
type _Run = tuple[Corpus, tuple[Block, ...], _Document, list[_Document]]


def _world(seed: int) -> _Document:
    cases = []
    for ship in range(3):
        before = ["A", "B"]
        after = ["B", "C"] if ship == 1 else before
        reports = [
            {"revision": 1, "available": before},
            {"revision": 2, "available": after},
        ]
        cases.append(
            {
                "id": ship,
                "before": before,
                "after": after,
                "utilities": {"A": 4, "B": 1, "C": 3, "D": 2},
                "reports": reports,
                "visible": reports[::-1],
                "chosen": "D",
            }
        )
    return {
        "schema": "seatlab.world/v1",
        "complete": True,
        "settings": {
            "schema": "seatlab.scenario/v1",
            "condition": "reordered",
            "seed": seed,
            "episodes": len(cases),
        },
        "cases": cases,
    }


def _records(*, revised: bool) -> list[_Document]:
    records = []
    for ship in range(3):
        changed = revised and ship == 1
        records.append(
            {
                "id": f"S{ship}",
                "old_version": "1" if changed else "2",
                "new_version": "2",
                "before": "A, B" if changed or ship != 1 else "B, C",
                "after": "B, C" if ship == 1 else "A, B",
                "question": "Choose the highest utility among available bays.",
                "old_best": "A" if changed or ship != 1 else "C",
                "best": "C" if ship == 1 else "A",
            }
        )
    return records


def _seal(registry: _Document) -> None:
    registry.pop("source_sha256", None)
    registry["source_sha256"] = sha256(_encode(registry)).hexdigest()


def _row(source: str, slot: int, status: str, text: str | None) -> _Document:
    return {
        "run": f"cv-{source[:12]}-{slot:03d}-checklist",
        "role": "route_decide",
        "slot": slot,
        "policy": "checklist",
        "target": None,
        "state": "unclaimed" if status == "missing" else "claimed",
        "status": status,
        "choices": dict(zip(("S0", "S1", "S2"), text, strict=True))
        if text is not None
        else None,
        "prompt_sha256": sha256(f"prompt {slot // 2}".encode()).hexdigest(),
        "response_sha256": sha256(f"response {slot}".encode()).hexdigest()
        if status == "received"
        else None,
        "usage": {
            "served_model": "provider/model",
            "input_tokens": 30,
            "cache_tokens": 10,
            "output_tokens": 5,
        }
        if status == "received"
        else None,
    }


@pytest.fixture
def run() -> _Run:
    raw = _encode(
        {
            "schema": "seatlab.handoff-input/v1",
            "brief": "Dispatch the shipments.",
            "choices": list("ABCD"),
            "cases": [
                {"id": "a", "cluster": "north", "records": _records(revised=True)},
                {"id": "b", "cluster": "south", "records": _records(revised=False)},
                {"id": "c", "cluster": "west", "records": _records(revised=True)},
            ],
        }
    )
    blocks = grid(parse(raw))
    corpus = Corpus(
        raw, {}, {case: _world(i) for i, case in enumerate("abc")}, (0, 1, 2)
    )
    registry = {
        "schema": "seatlab.exchange-source/v1",
        "packet_sha256": blocks[0].fingerprint,
        "sources": ["W0", "W1"],
        "readers": ["R0", "R1"],
        "slots": [
            {
                "index": index,
                "block": blocks[index].key,
                "case": blocks[index].case.id,
                "cluster": blocks[index].case.cluster,
                "rep": blocks[index].rep,
                "opened": "S0" if index == 3 else "S1",
                "notes": [
                    {
                        "status": "valid" if index < 4 else unavailable,
                        "note_sha256": (
                            sha256(f"note {index} {writer}".encode()).hexdigest()
                            if index < 4
                            else None
                        ),
                    }
                    for writer, unavailable in enumerate(("missing", "invalid"))
                ],
            }
            for index in (0, 1, 3, 4)
        ],
    }
    _seal(registry)
    responses = (
        ("received", "ACA"),
        ("callback_error", None),
        ("received", "BCB"),
        ("received", "ACB"),
        ("received", "ACA"),
        ("received", "AAA"),
        ("received", "BCB"),
        ("missing", None),
        ("received", "ABB"),
        ("received", None),
        ("received", "ACA"),
        ("received", "ACA"),
    )
    rows = [
        _row(registry["source_sha256"], slot, status, text)
        for slot, (status, text) in enumerate(responses)
    ]
    return corpus, blocks, registry, rows


def test_reader_failures_are_not_delivered(run: _Run) -> None:
    _, blocks, registry, rows = run
    results = summarize(blocks, registry, rows[::-1])
    assert set(results["cells"]) == {"W0:R0", "W0:R1", "W1:R0", "W1:R1"}
    w0r1 = results["cells"]["W0:R1"]
    assert w0r1 == {
        "enrolled_blocks": 4,
        "valid_writer_notes": 3,
        "received": 2,
        "invalid": 1,
        "missing": 0,
        "callback_errors": 1,
        "delivered": 1,
        "opened": {"correct": 0, "total": 1},
        "unopened": {"correct": 2, "total": 2},
        "delivered_correct_unopened_per_enrolled": {
            "correct": 2,
            "total": 8,
            "fraction": 0.25,
        },
    }
    assert results["cells"]["W1:R1"]["missing"] == 1


@pytest.mark.parametrize("status", ["missing", "invalid"])
def test_one_writer_does_not_require_the_other(run: _Run, status: str) -> None:
    _, blocks, registry, _ = run
    slot = registry["slots"][-1]
    slot["notes"] = [
        {"status": status, "note_sha256": None},
        {"status": "valid", "note_sha256": "b" * 64},
    ]
    registry["slots"] = [slot]
    _seal(registry)
    digest = registry["source_sha256"]
    rows = [_row(digest, 2, "received", "ACA"), _row(digest, 3, "missing", None)]
    result = summarize(blocks, registry, rows)
    cells = result["cells"]
    for reader in ("R0", "R1"):
        assert cells[f"W0:{reader}"]["valid_writer_notes"] == 0
    assert cells["W1:R0"]["delivered"] == cells["W1:R1"]["missing"] == 1
    contrast = result["comparisons"]["W1_minus_W0"]["R0"]
    assert contrast["paired_unopened_accuracy_delta"] is None
    assert contrast["finite_missing_data_bounds"] == {"lower": 0, "upper": 1}
    with pytest.raises(ValueError, match="row count"):
        summarize(blocks, registry, rows[:1])


def test_bounds_use_all_worlds_and_pairs_use_only_delivered_maps(run: _Run) -> None:
    _, blocks, registry, rows = run
    result = summarize(blocks, registry, rows)
    writer = result["comparisons"]["W1_minus_W0"]["R0"]
    assert writer == {
        "enrolled_pairs": 4,
        "paired_worlds": 2,
        "contributing_worlds": ["north", "south"],
        "complete_pairs": 3,
        "gains": 1,
        "losses": 2,
        "ties": 0,
        "paired_unopened_accuracy_delta": 0,
        "finite_missing_data_bounds": pytest.approx({"lower": -1 / 3, "upper": 1 / 3}),
    }
    reader = result["comparisons"]["R1_minus_R0"]["W1"]
    assert reader["paired_unopened_accuracy_delta"] == 0.25
    assert reader["ties"] == reader["gains"] == 1
    assert reader["finite_missing_data_bounds"] == pytest.approx(
        {"lower": -0.25, "upper": 7 / 12}
    )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("choices", {"S0": "A", "S1": "C"}, "choices"),
        ("choices", {"S0": "A", "S1": "E", "S2": "A"}, "choices"),
        ("state", "unclaimed", "unclaimed"),
        ("usage", {"input_tokens": 30}, "usage"),
        ("slot", True, "duplicate exchange row"),
        ("prompt_sha256", "c" * 64, "same prompt"),
        ("response_sha256", "short", "response fingerprint"),
        ("unexpected", "not allowed", "row fields"),
    ],
)
def test_rejects_bad_public_rows(
    run: _Run, field: str, value: object, error: str
) -> None:
    _, blocks, registry, rows = run
    rows[0][field] = value
    with pytest.raises(ValueError, match=error):
        summarize(blocks, registry, rows)


def test_rejects_resealed_slot_drift_and_unsealed_source_change(run: _Run) -> None:
    _, blocks, registry, rows = run
    registry["slots"][0]["notes"][0]["note_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="source fingerprint"):
        summarize(blocks, registry, rows)
    registry["slots"][0]["opened"] = "S0"
    _seal(registry)
    with pytest.raises(ValueError, match="opened record"):
        summarize(blocks, registry, rows)


def test_stock_regrades_every_delivery_and_rejects_conflicts(run: _Run) -> None:
    corpus, blocks, registry, rows = run
    report = {
        "schema": "seatlab.exchange-report/v1",
        "source": registry,
        "rows": rows,
        "summary": summarize(blocks, registry, rows),
    }
    result = stock(corpus, report)
    assert result["summary"] == report["summary"]
    assert result["observed_worlds"] == 2
    assert result["observed_dossiers"] == 9
    assert result["observed_native_choices"] == 27
    assert result["correct_native_choices"] == 19

    worlds = deepcopy(corpus.worlds)
    worlds["a"]["cases"][0]["utilities"].update(A=1, B=4)
    with pytest.raises(ValueError, match="packet answer does not match"):
        stock(Corpus(corpus.packet, {}, worlds, corpus.ids), report)
    report["summary"]["cells"]["W0:R0"]["opened"]["correct"] = 0
    with pytest.raises(ValueError, match="summary does not match"):
        stock(corpus, report)
