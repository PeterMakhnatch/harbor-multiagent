import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from examples.seatlab.visibility import (
    boxes_choice,
    boxes_packet,
    boxes_report,
    deepseek,
    spend,
)

_ROLE = "box_receiver"
_STAMP = "2026-11-21T12:00:00+00:00"  # Saturday, so both cost bounds are equal.
_USAGE = {"input": 50, "cached": 3, "output": 7}
_COST = 3 * 3 + 47 * 150 + 7 * 600
_PRIVATE = "unpublished response marker"


def _hash(text):
    return sha256(text.encode()).hexdigest()


@dataclass
class _Study:
    root: Path
    spend_dir: Path
    cases: dict[int, boxes_packet.Case | None]

    def sql(self, statement, bindings):
        with (
            closing(sqlite3.connect(self.spend_dir / "direct-flash.sqlite3")) as db,
            db,
        ):
            db.execute(statement, bindings)

    def reserve(self, cell, *, settled=False, role=_ROLE):
        if settled:
            self.sql(
                "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cell.id,
                    role,
                    _STAMP,
                    _STAMP,
                    _STAMP,
                    deepseek.REVISION,
                    3,
                    47,
                    7,
                    _COST,
                    _COST,
                ),
            )
        else:
            self.sql(
                "INSERT INTO calls(run,role,reserved_at) VALUES (?,?,?)",
                (cell.id, role, _STAMP),
            )

    def claim(self, cell):
        claim = self.root / "cells" / cell.id
        claim.mkdir(mode=0o700, exist_ok=True)
        return claim / "receipt.json"

    def receipt(self, cell, *, box=None, invalid=None, failure=None, metered=True):
        case = self.cases[cell.instance]
        assert case is not None
        text = (
            json.dumps({"box": 61 - box if cell.numbering else box})
            if box is not None
            else invalid
        )
        invalid_choice = invalid is not None
        row = {
            "schema": "seatlab.box-receiver/v1",
            "run": cell.id,
            "role": _ROLE,
            "packet_sha256": boxes_choice.PACKET_SHA256,
            "prompt_sha256": _hash(boxes_choice.prompt(case, cell)),
            "status": "invalid"
            if invalid_choice
            else "failed"
            if failure
            else "accepted",
            "reason": "invalid_choice" if invalid_choice else failure,
            "requested_model": deepseek.MODEL,
            "served_model": deepseek.REVISION if metered else None,
            "usage": dict(_USAGE) if metered else None,
            "response_text": text,
            "response_sha256": _hash(text) if text is not None else None,
            "original_box": box,
        }
        self.claim(cell).write_text(json.dumps(row), encoding="utf-8")
        return row

    def accepted(self, cell, box):
        self.reserve(cell, settled=True)
        return self.receipt(cell, box=box)

    def analyze(self, *, with_spend=True):
        return boxes_report.analyze(
            self.root,
            packet_path=boxes_choice.PACKET,
            expected_sha256=boxes_choice.PACKET_SHA256,
            spend_dir=self.spend_dir if with_spend else None,
        )


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "private-study"
    (root / "cells").mkdir(parents=True)
    spend_dir = tmp_path / "private-account"
    spend_dir.mkdir(mode=0o700)
    spend.initialize(spend_dir, Decimal("2.50"), Decimal("1.00"))
    cases = boxes_packet.load(boxes_choice.PACKET, boxes_choice.PACKET_SHA256)
    return _Study(root, spend_dir, cases)


def _cell(arm="own", *, instance=1, numbering=0, repetition=0):
    return boxes_choice.Cell(instance, numbering, repetition, arm)


def _new(case):
    seen = {box for box, _, _ in case.own} | set(case.peers)
    return min(set(range(1, 61)) - seen)


def _row(report, cell):
    return next(
        row
        for row in report["cells"]
        if (row["instance"], row["numbering"], row["repetition"], row["arm"])
        == (cell.instance, cell.numbering, cell.repetition, cell.arm)
    )


def test_reverse_numbering_and_equal_instance_weighting(study):
    target = study.cases[1]
    assert target is not None
    own = target.own[0][0]
    peer_only = min(set(target.peers) - {box for box, _, _ in target.own})
    for cell in boxes_choice.grid():
        case = study.cases[cell.instance]
        assert case is not None
        if cell.instance == 1 and (cell.numbering, cell.repetition) != (1, 1):
            study.accepted(cell, peer_only if cell.arm == "own" else own)
        elif cell.instance == 1 or (cell.numbering, cell.repetition) == (1, 0):
            study.accepted(cell, _new(case))

    report = study.analyze()
    assert len(report["cells"]) == 80
    assert len(report["pairs"]) == 40
    assert report["primary"]["possible_pairs"] == 40
    assert report["primary"]["complete_pairs"] == 13
    assert report["primary"]["supported_instances"] == 10
    assert report["primary"]["shared_minus_own"] == {
        "peer_only_repeat": -3 / 4 / 10,
        "own_repeat": 3 / 4 / 10,
        "globally_new": 0,
    }
    first = report["primary"]["per_instance"][0]
    assert first["complete_pairs"] == 4
    assert first["shared_minus_own_numerator"] == {
        "peer_only_repeat": -3,
        "own_repeat": 3,
        "globally_new": 0,
    }
    assert report["arms"]["own"]["outcome_denominator"] == 13
    assert report["arms"]["own"]["outcomes"] == {
        "peer_only_repeat": 3,
        "own_repeat": 0,
        "globally_new": 10,
    }
    reverse = _row(report, _cell("shared", numbering=1))
    assert reverse["original_box"] == own
    assert reverse["outcome"] == "own_repeat"
    assert report["by_numbering"][0]["paired"]["shared_minus_own"] is None
    numbered = report["by_numbering"][1]["paired"]["shared_minus_own"]
    repeated = report["by_repetition"][0]["paired"]["shared_minus_own"]
    assert numbered["peer_only_repeat"] == -0.05
    assert repeated["peer_only_repeat"] == -0.1
    assert report["by_repetition"][1]["paired"]["shared_minus_own"] is None
    assert report["coverage_challenge"]["lower_peer_only_without_more_new"] is True
    assert report["coverage_challenge"]["peer_only_to_own_repeat_pairs"] == 3
    assert report["coverage_challenge"]["peer_only_to_globally_new_pairs"] == 0
    earlier = [
        case.instance
        for case in study.cases.values()
        if case is not None and case.earlier_observation
    ]
    sensitivity = report["earlier_observation_sensitivity"]
    assert [row["instance"] for row in sensitivity["per_instance"]] == earlier
    assert sensitivity["possible_pairs"] == 4 * len(earlier)
    assert sensitivity["complete_pairs"] == len(earlier) + (3 if 1 in earlier else 0)
    if earlier:
        expected = -3 / 4 / len(earlier) if 1 in earlier else 0
        assert sensitivity["shared_minus_own"]["peer_only_repeat"] == expected


def test_primary_requires_all_ten_instances_but_sensitivity_can_survive(study):
    unsupported = next(
        instance
        for instance, case in study.cases.items()
        if case is not None and not case.earlier_observation
    )
    for cell in boxes_choice.grid():
        if (cell.numbering, cell.repetition) != (0, 0):
            continue
        case = study.cases[cell.instance]
        assert case is not None
        if cell.instance == unsupported and cell.arm == "shared":
            study.reserve(cell, settled=True)
            study.receipt(cell, invalid='{"box": 61}')
        else:
            study.accepted(cell, _new(case))

    report = study.analyze()
    assert report["primary"]["complete_pairs"] == 9
    assert report["primary"]["supported_instances"] == 9
    assert report["primary"]["shared_minus_own"] is None
    assert report["earlier_observation_sensitivity"]["shared_minus_own"] == {
        "peer_only_repeat": 0,
        "own_repeat": 0,
        "globally_new": 0,
    }


def test_separates_invalid_held_failed_unattempted_and_missing_evidence(study):
    accepted = _cell()
    invalid = _cell("shared")
    held = _cell(numbering=1)
    settled_no_receipt = _cell("shared", numbering=1)
    failed = _cell(repetition=1)
    held_failure = _cell("shared", repetition=1)
    metered_failure = _cell(numbering=1, repetition=1)
    unattempted = _cell("shared", numbering=1, repetition=1)
    case = study.cases[1]
    assert case is not None
    study.accepted(accepted, _new(case))
    study.reserve(invalid, settled=True)
    study.receipt(invalid, invalid=_PRIVATE)
    study.claim(held)
    study.reserve(held)
    study.claim(settled_no_receipt)
    study.reserve(settled_no_receipt, settled=True)
    study.receipt(failed, failure="transport_failed", metered=False)
    study.reserve(held_failure)
    study.receipt(held_failure, failure="usage_unverified", metered=False)
    study.reserve(metered_failure, settled=True)
    study.receipt(metered_failure, failure="provider_response_invalid")
    study.sql(
        "INSERT INTO calls(run,role,reserved_at) VALUES (?,?,?)",
        ("mirror-01", "decision_maker", _STAMP),
    )

    report = study.analyze()
    expected = {
        accepted: "accepted",
        invalid: "invalid",
        held: "held",
        settled_no_receipt: "receipt_unavailable",
        failed: "failed",
        held_failure: "held",
        metered_failure: "failed",
        unattempted: "unattempted",
    }
    assert {cell: _row(report, cell)["status"] for cell in expected} == expected
    assert _row(report, held_failure)["receipt_status"] == "failed"
    assert _row(report, metered_failure)["spend"] == "settled"
    assert report["arms"]["shared"]["status_counts"]["invalid"] == 1
    assert report["arms"]["shared"]["outcome_denominator"] == 0
    assert report["arms"]["shared"]["missing_choices"] == 39
    assert report["primary"]["complete_pairs"] == 0
    assert report["primary"]["shared_minus_own"] is None
    public = json.dumps(report)
    for private in (
        _PRIVATE,
        accepted.id,
        str(study.root),
        str(study.spend_dir),
        _hash(_PRIVATE),
    ):
        assert private not in public

    unavailable = study.analyze(with_spend=False)
    assert unavailable["spend_ledger_available"] is False
    for cell, receipt_status in ((invalid, "invalid"), (accepted, "accepted")):
        row = _row(unavailable, cell)
        assert row["receipt_status"] == receipt_status
        assert row["status"] == "spend_unavailable"
        assert row["original_box"] is None
    assert _row(unavailable, unattempted)["status"] == "unattempted"
    assert unavailable["arms"]["own"]["outcome_denominator"] == 0


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        ({"run": "bx-i02-m1-r0-own"}, "identity mismatch"),
        ({"role": "inventory"}, "identity mismatch"),
        ({"requested_model": "wrong-model"}, "identity mismatch"),
        ({"packet_sha256": "0" * 64}, "packet commitment mismatch"),
        ({"prompt_sha256": "0" * 64}, "prompt commitment mismatch"),
        ({"response_text": '{"box": 4}'}, "response commitment mismatch"),
        ({"served_model": deepseek.MODEL}, "differ from ledger"),
        ({"usage": {"input": 50, "cached": 4, "output": 7}}, "differ from ledger"),
        ({"original_box": 57}, "accepted box receipt does not match replay"),
        (
            {"status": "invalid", "reason": "invalid_choice", "original_box": None},
            "invalid box receipt does not match replay",
        ),
        ({"original_box": True}, "accepted box receipt does not match replay"),
    ],
)
def test_rejects_receipts_that_disagree_with_packet_replay_or_ledger(
    study, patch, error
):
    cell = _cell(numbering=1)
    receipt = study.accepted(cell, 2)
    receipt.update(patch)
    study.claim(cell).write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        study.analyze()


def test_rejects_a_valid_choice_without_a_matching_ledger_row(study):
    study.receipt(_cell(), box=2)
    with pytest.raises(ValueError, match="no matching settlement"):
        study.analyze()


def test_rejects_an_unexplained_hold_and_a_second_role(study):
    cell = _cell()
    study.reserve(cell)
    with pytest.raises(ValueError, match="lacks a registered claim"):
        study.analyze()
    study.claim(cell)
    assert _row(study.analyze(), cell)["status"] == "held"
    study.reserve(cell, role="inventory")
    with pytest.raises(ValueError, match="unexpected or duplicate box ledger call"):
        study.analyze()


def test_rejects_a_duplicate_receipt(study):
    cell = _cell()
    study.accepted(cell, 2)
    receipt = study.claim(cell)
    receipt.with_name("receipt-2.json").write_bytes(receipt.read_bytes())
    with pytest.raises(ValueError, match="unexpected or duplicate box receipt"):
        study.analyze()
