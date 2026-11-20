import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from examples.seatlab.visibility import (
    boxes_choice,
    boxes_packet,
    boxes_report,
    boxes_run,
    deepseek,
    intent_packet,
    intent_protocol,
    intent_report,
    intent_run,
    spend,
)

_Choices = intent_protocol.Choices
_PRIVATE = "private fixture response"
_USAGE = {"input": 50, "cached": 3, "output": 7}
_COST = 3 * 3 + 47 * 150 + 7 * 600


def _stamp(seconds: int) -> str:
    # Both cost bounds are equal on Saturday.
    return (
        datetime(2026, 11, 21, 12, tzinfo=UTC) + timedelta(seconds=seconds)
    ).isoformat()


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _world(instance: int) -> intent_packet.World:
    def case(box: int) -> boxes_packet.Case:
        return boxes_packet.Case(
            instance,
            ((box, 1, False),),
            tuple(x for x in (1, 2, 3) if x != box),
            8,
            True,
        )

    return intent_packet.World(instance, (case(1), case(2), case(3)))


@dataclass
class _Study:
    root: Path
    spend_dir: Path
    worlds: dict[int, intent_packet.World]

    def ledger(
        self,
        run: str,
        *,
        role: str = intent_run.ROLE,
        reserved: int = 0,
        finished: int | None = 0,
    ) -> None:
        with (
            closing(sqlite3.connect(self.spend_dir / "direct-flash.sqlite3")) as db,
            db,
        ):
            if finished is None:
                db.execute(
                    "INSERT INTO calls (run, role, reserved_at) VALUES (?, ?, ?)",
                    (run, role, _stamp(reserved)),
                )
            else:
                db.execute(
                    "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run,
                        role,
                        _stamp(reserved),
                        _stamp(reserved),
                        _stamp(finished),
                        deepseek.REVISION,
                        3,
                        47,
                        7,
                        _COST,
                        _COST,
                    ),
                )

    def receipt(
        self,
        block: intent_protocol.Block,
        label: int,
        stage: str,
        box: int | None,
        proposals: _Choices | None = None,
        *,
        reserved: int = 0,
        finished: int = 0,
        recorded_box: int | None = None,
        append_prompt: str = "",
    ) -> None:
        run = intent_protocol.call_id(block, label, stage)
        self.ledger(run, reserved=reserved, finished=finished)
        world = self.worlds[block.instance]
        view = (
            intent_protocol.prompt(world, block, label, stage, proposals)
            + append_prompt
        )
        text = (
            _PRIVATE
            if box is None
            else json.dumps({"box": 61 - box if block.numbering else box})
        )
        claim = self.root / "cells" / run
        claim.mkdir(mode=0o700)
        path = claim / "receipt.json"
        path.touch(mode=0o600)
        path.write_text(
            json.dumps(
                {
                    "schema": intent_run.RECEIPT_SCHEMA,
                    "run": run,
                    "role": intent_run.ROLE,
                    "packet_sha256": intent_packet.PACKET_SHA256,
                    "prompt_sha256": _digest(view),
                    "status": "accepted" if box is not None else "invalid",
                    "reason": None if box is not None else "invalid_choice",
                    "requested_model": deepseek.MODEL,
                    "served_model": deepseek.REVISION,
                    "usage": _USAGE,
                    "response_text": text,
                    "response_sha256": _digest(text),
                    "original_box": box if recorded_box is None else recorded_box,
                }
            ),
            encoding="utf-8",
        )

    def pair(
        self,
        block: intent_protocol.Block,
        proposals: _Choices,
        masked: _Choices,
        shown: _Choices,
    ) -> None:
        for label, choice in enumerate(proposals):
            self.receipt(block, label, "proposal", choice)
        for stage, choices in (("masked", masked), ("shown", shown)):
            for label, choice in enumerate(choices):
                self.receipt(block, label, stage, choice, proposals)

    def analyze(self) -> dict[str, Any]:
        return intent_report.analyze(self.root, self.spend_dir)


@pytest.fixture
def study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Study:
    root = tmp_path.resolve() / "private-study"
    root.mkdir(mode=0o700)
    (root / "cells").mkdir(mode=0o700)
    account = tmp_path.resolve() / "private-account"
    account.mkdir(mode=0o700)
    spend.initialize(account, Decimal("2.50"), Decimal("1.00"))
    worlds = {instance: _world(instance) for instance in (1, 2, 6)}
    monkeypatch.setattr(intent_packet, "load", lambda: worlds)
    return _Study(root, account, worlds)


def _block(instance: int = 1, numbering: int = 0) -> intent_protocol.Block:
    return intent_protocol.Block(instance, numbering, 0, 0)


def _result(report: dict[str, Any], block: intent_protocol.Block) -> dict[str, Any]:
    return next(
        row
        for row in cast("list[dict[str, Any]]", report["blocks"])
        if row["id"] == block.id
    )


def test_incomplete_proposal_barrier_refuses_revisions(study: _Study) -> None:
    block = _block()
    study.receipt(block, 0, "proposal", 7)
    study.receipt(block, 1, "proposal", None)
    study.ledger(intent_protocol.call_id(block, 2, "proposal"))
    study.receipt(block, 0, "shown", 25, (7, 8, 9))

    report = study.analyze()
    result = _result(report, block)
    assert result["proposal_barrier"] == "incomplete"
    assert result["calls"]["proposal"]["B"]["status"] == "invalid"
    assert result["calls"]["proposal"]["C"]["status"] == "receipt_missing"
    assert result["calls"]["shown"]["A"]["status"] == "unexpected"
    assert result["proposals"] is None
    assert result["arms"] == {"masked": None, "shown": None}
    assert report["coverage"]["missing_actual_receipts"] == 1
    assert report["primary"]["complete_pairs"] == 0
    assert _PRIVATE not in json.dumps(report)


def test_reservation_before_last_proposal_finish_invalidates_both_arms(
    study: _Study,
) -> None:
    block = _block()
    proposed = (7, 8, 9)
    for label, choice in enumerate(proposed):
        study.receipt(
            block, label, "proposal", choice, reserved=2 * label, finished=2 * label + 2
        )
    for arm_slot, arm in enumerate(block.arms):
        for label, choice in enumerate(proposed):
            instant = 5 + 3 * arm_slot + label
            study.receipt(
                block, label, arm, choice, proposed, reserved=instant, finished=instant
            )

    result = _result(study.analyze(), block)
    assert result["proposal_barrier"] == "violated"
    assert result["order_violations"] == [
        intent_protocol.call_id(block, 0, block.arms[0])
    ]
    assert result["arms"] == {"masked": None, "shown": None}
    assert result["shown_minus_masked"] is None


def test_swapped_revision_order_is_not_a_complete_block(study: _Study) -> None:
    block = _block(numbering=1)
    proposed = (7, 8, 9)
    for label, choice in enumerate(proposed):
        study.receipt(block, label, "proposal", choice)
        study.receipt(block, label, "masked", choice, proposed, reserved=1, finished=1)
        study.receipt(block, label, "shown", choice, proposed, reserved=2, finished=2)

    report = study.analyze()
    result = _result(report, block)
    assert result["arm_order"] == ["shown", "masked"]
    assert result["proposal_barrier"] == "verified"
    assert all(
        call["status"] == "unexpected" for call in result["calls"]["masked"].values()
    )
    assert result["arms"] == {"masked": None, "shown": None}
    assert report["primary"]["complete_pairs"] == 0


def test_triple_collision_and_equal_world_mean(study: _Study) -> None:
    triple = (7, 7, 7)
    separate = (7, 18, 2)
    block = _block(numbering=1)
    study.pair(block, triple, triple, separate)
    study.pair(_block(), triple, triple, triple)
    study.pair(_block(instance=2), separate, separate, triple)
    study.receipt(_block(instance=6), 0, "proposal", 7)

    report = study.analyze()
    result = _result(report, block)
    assert len(report["blocks"]) == 36
    assert report["coverage"]["scheduled_calls"] == 324
    assert result["proposals"]["choices"] == [7, 7, 7]
    assert result["arms"]["masked"]["metrics"]["excess_new_collisions"] == 2
    assert result["arms"]["shown"]["choices"] == [7, 18, 2]
    assert result["shown_minus_masked"] == {
        "individually_new": -1,
        "distinct_new": 1,
        "excess_new_collisions": -2,
        "historical_repeats": 1,
        "changed_proposals": 2,
    }
    assert report["primary"]["complete_pairs"] == 3
    assert report["primary"]["supported_worlds"] == 2
    assert not report["primary"]["full_world_support"]
    assert report["primary"]["shown_minus_masked"] is None
    assert report["by_numbering"][1]["collision_direction_counts"]["lower"] == 1
    assert report["by_rotation"][0]["collision_direction_counts"] == {
        "lower": 1,
        "equal": 1,
        "higher": 1,
    }
    assert report["by_repetition"][1]["complete_pairs"] == 0
    transitions = report["by_initial_collision"]["colliding"]
    assert transitions["transitions_in_complete_pairs"]["shown"] == {
        "final_colliding": 1,
        "final_noncolliding": 1,
    }

    study.pair(_block(instance=6, numbering=1), triple, triple, separate)
    primary = study.analyze()["primary"]
    assert primary["full_world_support"]
    assert primary["shown_minus_masked"]["excess_new_collisions"] == -1 / 3


def test_never_uses_claimed_box_or_unregistered_prompt(study: _Study) -> None:
    block = _block()
    proposed = (7, 8, 9)
    for label, choice in enumerate(proposed):
        study.receipt(block, label, "proposal", choice)
    study.receipt(block, 0, "masked", 7, proposed, recorded_box=31)
    study.receipt(block, 1, "masked", 8, proposed, append_prompt="Other final box: 32")
    study.ledger(intent_protocol.call_id(block, 2, "masked"), finished=None)
    unknown = "bi-unregistered-private-marker"
    study.ledger(unknown, finished=None)
    (study.root / "cells" / unknown).mkdir(mode=0o700)

    report = study.analyze()
    result = _result(report, block)
    assert result["proposal_barrier"] == "verified"
    assert result["calls"]["masked"]["A"]["status"] == "invalid_evidence"
    assert result["calls"]["masked"]["B"]["status"] == "invalid_evidence"
    assert result["calls"]["masked"]["C"]["status"] == "held"
    assert report["coverage"]["unexpected_claims"] == 1
    assert report["coverage"]["unexpected_ledger_rows"] == 1
    assert result["arms"]["masked"] is None
    assert unknown not in json.dumps(report)


def test_receiver_replay_is_unchanged_on_shared_ledger(study: _Study) -> None:
    cell = boxes_choice.grid()[0]
    case = boxes_packet.load(boxes_choice.PACKET, boxes_choice.PACKET_SHA256)[
        cell.instance
    ]
    assert case is not None
    response = json.dumps({"box": 60 if cell.numbering else 1})
    claim = study.root / "cells" / cell.id
    claim.mkdir(mode=0o700)
    (claim / "receipt.json").write_text(
        json.dumps(
            {
                "schema": boxes_run.RECEIPT_SCHEMA,
                "run": cell.id,
                "role": boxes_run.ROLE,
                "packet_sha256": boxes_choice.PACKET_SHA256,
                "prompt_sha256": _digest(boxes_choice.prompt(case, cell)),
                "status": "accepted",
                "reason": None,
                "requested_model": deepseek.MODEL,
                "served_model": deepseek.REVISION,
                "usage": _USAGE,
                "response_text": response,
                "response_sha256": _digest(response),
                "original_box": 1,
            }
        ),
        encoding="utf-8",
    )
    study.ledger(cell.id, role=boxes_run.ROLE)

    def receiver_report() -> dict[str, Any]:
        return boxes_report.analyze(
            study.root,
            packet_path=boxes_choice.PACKET,
            expected_sha256=boxes_choice.PACKET_SHA256,
            spend_dir=study.spend_dir,
        )

    before = receiver_report()
    assert before["cells"][0]["status"] == "accepted"
    study.ledger(intent_protocol.call_id(_block(), 0, "proposal"))
    assert receiver_report() == before
