from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest

from examples.seatlab.coverage import analysis
from examples.seatlab.handoff import dispatch, packet
from examples.seatlab.visibility.clarify_run import _encode

type _Row = dict[str, Any]
type _Inputs = tuple[tuple[packet.Block, ...], str, list[_Row], list[_Row]]


def _register(
    blocks: tuple[packet.Block, ...], ready: set[int]
) -> tuple[str, list[_Row]]:
    slots = [
        {
            "block": block.key,
            "case": block.case.id,
            "cluster": block.case.cluster,
            "rep": block.rep,
            "status": "ready" if index in ready else "no_request",
            "prompt_sha256": sha256(block.key.encode()).hexdigest()
            if index in ready
            else None,
        }
        for index, block in enumerate(blocks)
    ]
    document = {
        "schema": "seatlab.coverage-source/v1",
        "packet_sha256": blocks[0].fingerprint,
        "slots": slots,
    }
    return sha256(_encode(document)).hexdigest(), slots


def _rows(
    blocks: tuple[packet.Block, ...], source: str, slots: list[_Row]
) -> list[_Row]:
    rows: list[_Row] = []
    for slot, block in enumerate(blocks):
        if slots[slot]["status"] != "ready":
            continue
        identities = [("batch", "batch", None), ("checklist", "checklist", None)]
        identities.extend(
            (f"owner{index}", "owners", record.id)
            for index, record in enumerate(block.case.records)
        )
        for suffix, policy, target in identities:
            run = f"cv-{source[:12]}-{slot:03d}-{suffix}"
            rows.append(
                {
                    "run": run,
                    "role": "route_decide",
                    "slot": slot,
                    "policy": policy,
                    "target": target,
                    "state": "unclaimed",
                    "status": "missing",
                    "choices": None,
                    "prompt_sha256": (
                        slots[slot]["prompt_sha256"]
                        if policy == "batch"
                        else sha256(run.encode()).hexdigest()
                    ),
                    "response_sha256": None,
                    "usage": None,
                }
            )
    return rows


def _reply(row: _Row, choices: dict[str, str] | None) -> None:
    row.update(
        state="claimed",
        status="received",
        choices=choices,
        response_sha256=sha256(_encode(choices)).hexdigest(),
    )


def _fixture() -> _Inputs:
    truth = {"P": "A", "Q": "B", "R": "B"}
    records = tuple(
        packet.Record(key, "1", "1", "Available.", "Available.", "Bay?", bay, bay)
        for key, bay in truth.items()
    )
    blocks = packet.grid(
        packet.Packet(
            "Choose a bay.",
            ("A", "B"),
            (
                packet.Case("first", "west", records),
                packet.Case("second", "west", records),
                packet.Case("third", "east", records),
            ),
            sha256(b"coverage fixture").hexdigest(),
        )
    )
    source, slots = _register(blocks, {0, 1, 2, 4})
    rows = _rows(blocks, source, slots)
    for row in rows:
        index, policy, target = row["slot"], row["policy"], row["target"]
        if policy == "batch" and index != 2:
            _reply(row, truth if index != 0 else None)
        if policy == "checklist":
            _reply(row, truth if index < 2 else None)
        if policy == "owners":
            if index == 4 and target == "R":
                row.update(state="claimed", status="callback_error")
            elif index == 0 and target == "Q":
                _reply(row, None)
            elif (
                index == 1
                or (index == 4 and target != "R")
                or (index == 0 and target == "P")
            ):
                _reply(
                    row,
                    {target: "A" if index == 4 and target == "Q" else truth[target]},
                )
    return blocks, source, slots, rows


def test_missingness_bounds_use_equal_world_weights() -> None:
    report = analysis.summarize(*_fixture())
    primary, secondary = report["primary"], report["secondary"]
    assert primary["paired_complete_delta"] == -0.25
    assert primary["worst_delta"] == -0.5
    assert primary["best_delta"] == pytest.approx(-1 / 3)
    assert [primary[key] for key in ("gain", "loss", "tie")] == [1, 1, 1]
    assert (primary["complete_pairs"], primary["scheduled_pairs"]) == (3, 4)
    assert secondary["paired_complete_delta"] is None
    assert secondary["worst_delta"] == pytest.approx(-1 / 6)
    assert secondary["best_delta"] == 0.5
    assert secondary["by_cluster"]["west"]["loss"] == 1
    assert secondary["by_cluster"]["east"]["complete_pairs"] == 0


def test_partial_owner_credit_is_separate_from_complete_team_delivery() -> None:
    report = analysis.summarize(*_fixture())
    owners = report["arms"]["owners"]
    batch = report["arms"]["batch"]
    assert report["counts"]["ineligible_slots"] == 2
    assert owners["eligible_requests"] == 4
    assert owners["scheduled_calls"] == 12
    assert owners["callback_errors"] == 1
    assert owners["missing_calls"] == 4
    assert owners["invalid_responses"] == 1
    assert owners["valid_responses"] == owners["valid_choices"] == 6
    assert owners["correct_delivered"] == 5
    assert owners["correct_on_complete_deliveries"] == 3
    assert owners["delivery_rate_bounds"] == {"lower": 1 / 4, "upper": 3 / 4}
    assert owners["correct_per_eligible_record_bounds"] == {
        "lower": 5 / 12,
        "upper": 10 / 12,
    }
    assert batch["valid_choices"] == 6
    assert batch["correct_per_eligible_record_bounds"] == {
        "lower": 6 / 12,
        "upper": 9 / 12,
    }


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate",
        "excess",
        "missing",
        "ineligible",
        "fields",
        "choices",
        "target",
        "claim",
        "batch_prompt",
        "response",
        "usage",
        "order",
        "seal",
    ],
)
def test_rejects_inconsistent_public_evidence(corruption: str) -> None:
    blocks, source, slots, rows = _fixture()
    match corruption:
        case "duplicate":
            rows[1] = rows[0]
        case "excess":
            rows.append(rows[0])
        case "missing":
            rows.pop()
        case "ineligible":
            rows[0].update(slot=3, run=f"cv-{source[:12]}-003-batch")
        case "fields":
            rows[0]["text"] = "private"
        case "choices":
            rows[0]["choices"] = {"P": "A"}
        case "target":
            rows[2]["target"] = "Q"
        case "claim":
            rows[1]["state"] = "unbound"
        case "batch_prompt":
            rows[0]["prompt_sha256"] = rows[1]["prompt_sha256"]
        case "response":
            rows[-1]["response_sha256"] = rows[0]["response_sha256"]
        case "usage":
            rows[0]["usage"] = {
                "served_model": "model",
                "input_tokens": 1,
                "cache_tokens": 2,
                "output_tokens": 1,
            }
        case "order":
            slots[0], slots[1] = slots[1], slots[0]
        case "seal":
            slots[-1]["status"] = "no_note"
    with pytest.raises(ValueError):
        analysis.summarize(blocks, source, slots, rows)


def _native_report() -> _Row:
    blocks = packet.grid(packet.parse(dispatch.packet_bytes()))
    source, slots = _register(blocks, {0})
    rows = _rows(blocks, source, slots)
    expected = {record.id: record.best for record in blocks[0].case.records}
    incorrect = next(bay for bay in "ABCD" if bay != expected["P"])
    _reply(rows[0], expected | {"P": incorrect})
    _reply(rows[1], None)
    _reply(rows[2], {"P": expected["P"]})
    _reply(rows[3], None)
    return {
        "schema": "seatlab.coverage-report/v1",
        "packet_sha256": blocks[0].fingerprint,
        "source_sha256": source,
        "slots": slots,
        "rows": rows,
        **analysis.summarize(blocks, source, slots, rows),
    }


def test_stock_regrades_native_singleton_without_counting_filler() -> None:
    report = _native_report()
    checked = analysis.stock(report)
    assert checked["registered_worlds"] == 12
    assert checked["observed_worlds"] == 1
    assert checked["observed_dossiers"] == 2
    assert checked["observed_native_choices"] == 5
    assert checked["by_policy"]["owners"] == {"scorer_calls": 1, "observed_choices": 1}
    assert checked["arms"]["owners"]["correct_delivered"] == 1
    assert checked["arms"]["owners"]["correct_on_complete_deliveries"] == 0
    assert checked["arms"]["batch"]["accuracy_on_complete_deliveries"] == 0.75
    assert checked["primary"]["paired_complete_delta"] is None
    for key in ("primary", "secondary", "arms", "counts"):
        tampered = deepcopy(report)
        tampered[key]["invented"] = True
        with pytest.raises(ValueError, match="published coverage summary"):
            analysis.stock(tampered)
    tampered = deepcopy(report)
    tampered["rows"][2]["choices"]["P"] = report["rows"][0]["choices"]["P"]
    with pytest.raises(ValueError, match="published coverage summary"):
        analysis.stock(tampered)
