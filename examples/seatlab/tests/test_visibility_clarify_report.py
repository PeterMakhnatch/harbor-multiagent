import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from examples.seatlab.capsule import view_hash
from examples.seatlab.visibility import clarify_report, clarify_views
from examples.seatlab.visibility.clarify_schedule import (
    ARMS,
    OFFSETS_MINUTES,
    PACKETS,
    SEEDS,
    Cell,
    grid,
    packet_path,
)


def _source(cell: Cell) -> tuple[bytes, dict[str, Any]]:
    raw = packet_path(PACKETS, cell.seed).read_bytes()
    return raw, json.loads(raw)


def _turn(prompt: str) -> dict[str, str]:
    return {
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "served_model": "DeepSeek-V4.1-Flash-1017",
    }


def _receipt(cell: Cell, selected: int, *, best: bool = True) -> dict[str, Any]:
    raw, packet = _source(cell)
    actions = {"rows": []}
    for case in packet["world"]:
        bay = (
            max(case["after"], key=case["utilities"].__getitem__)
            if best
            else min(case["utilities"].keys() - set(case["after"]))
        )
        actions["rows"].append({"id": case["id"], "bay": bay})
    actions = clarify_views.validate_actions(packet, cell, json.dumps(actions))
    return {
        "schema": "seatlab-clarification-turns-v1",
        "cell_id": cell.cell_id,
        "window": cell.window,
        "seed": cell.seed,
        "mask": cell.mask,
        "arm": cell.arm,
        "packet_sha256": sha256(raw).hexdigest(),
        "status": "complete",
        "selected_case": selected,
        "delivered_case": cell.delivery(selected),
        "actions": actions,
        "grade": clarify_views.grade_actions(packet, cell, actions),
        "turns": {
            "select": _turn(clarify_views.build_initial(packet, cell)),
            "act": _turn(clarify_views.build_final(packet, cell, selected)),
        },
    }


def _write(study: Path, cell: Cell, receipt: dict[str, Any]) -> None:
    cells = study / "cells"
    cells.mkdir(exist_ok=True)
    path = cells / f"{cell.cell_id}.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    path.chmod(0o600)


def _cell(report: dict[str, Any], cell: Cell) -> dict[str, Any]:
    return next(row for row in report["cells"] if row["cell_id"] == cell.cell_id)


def test_preserves_missing_failed_and_unpaired_outcomes(tmp_path: Path) -> None:
    active = Cell(0, SEEDS[0], 0, "active")
    queue = Cell(0, SEEDS[0], 0, "queue")
    unpaired = Cell(0, SEEDS[0], 1, "active")
    failed = Cell(0, SEEDS[0], 1, "queue")
    active_visible = next(case for case in range(4) if case not in active.hidden)
    queue_hidden = next(case for case in queue.hidden if case != queue.queued_case)
    _write(tmp_path, active, _receipt(active, active_visible))
    queue_receipt = _receipt(queue, queue_hidden, best=False)
    queue_receipt["turns"]["select"]["served_model"] = "deepseek-flash"
    queue_receipt["turns"]["act"]["served_model"] = "deepseek-v4.1-flash-1017"
    _write(tmp_path, queue, queue_receipt)
    _write(tmp_path, unpaired, _receipt(unpaired, unpaired.hidden[0], best=False))
    partial = _receipt(failed, failed.hidden[0])
    partial.update(status="transport_failed", actions=None, grade=None)
    del partial["turns"]["act"]
    _write(tmp_path, failed, partial)
    (tmp_path / "cells/.interrupted.tmp").write_text("not a receipt", encoding="utf-8")

    report = clarify_report.collect(PACKETS, tmp_path)

    assert report["independent_worlds"] == 4
    assert report["counts"] == {
        "scheduled": len(grid()),
        "attempted": 4,
        "complete": 3,
        "incomplete": 1,
        "invalid": 0,
        "missing": len(grid()) - 4,
    }
    assert _cell(report, queue)["selected_case"] == queue_hidden
    assert _cell(report, queue)["delivered_case"] == queue.queued_case
    failure = _cell(report, failed)
    assert (failure["status"], failure["reason"]) == ("incomplete", "transport_failed")
    assert failure["selected_case"] == failed.hidden[0]
    assert failure["delivered_case"] is None
    assert _cell(report, Cell(1, SEEDS[0], 0, "queue"))["status"] == "missing"
    assert report["pairs"][0]["active_minus_queue"] == {"feasible": 1.0, "optimal": 1.0}
    assert report["pairs"][0]["different_delivered_case"] is True
    assert report["pairs"][1]["active_status"] == "complete"
    assert report["pairs"][1]["queue_status"] == "incomplete"
    assert report["pairs"][1]["active_minus_queue"] is None
    overall = report["overall"]
    assert overall["worlds_with_complete_pairs"] == 1
    assert overall["equal_world_mean_active_minus_queue"] is None
    assert overall["complete_pairs"] == 1
    assert overall["incomplete_pairs"] == len(grid()) // 2 - 1
    assert overall["selections"] == {
        "selected_hidden": 3,
        "selected_already_visible": 1,
    }
    assert overall["arms"]["active"]["complete_cells"] == 2
    assert overall["paired_arms"]["active"]["complete_cells"] == 1
    assert overall["paired_arms"]["active"]["optimal"] == 4
    assert overall["arms"]["active"]["selected_already_visible"] == 1
    assert overall["arms"]["active"]["delivered_already_visible"] == 1
    assert overall["arms"]["active"]["delivery_differs_from_selection"] == 0
    assert overall["arms"]["queue"]["selected_hidden"] == 2
    assert overall["arms"]["queue"]["delivered_hidden"] == 1
    assert overall["arms"]["queue"]["delivery_differs_from_selection"] == 1


def test_weights_worlds_equally_and_preserves_window_coverage(tmp_path: Path) -> None:
    for window, seed, mask, best_by_arm in (
        (0, SEEDS[0], 0, (True, False)),
        (0, SEEDS[0], 1, (True, False)),
        (0, SEEDS[1], 2, (False, True)),
        (1, SEEDS[2], 3, (False, False)),
        (2, SEEDS[3], 4, (True, True)),
    ):
        for arm, best in zip(ARMS, best_by_arm, strict=True):
            cell = Cell(window, seed, mask, arm)
            _write(tmp_path, cell, _receipt(cell, cell.queued_case, best=best))

    report = clarify_report.collect(PACKETS, tmp_path)

    overall = report["overall"]
    assert [world["complete_pairs"] for world in overall["worlds"]] == [2, 1, 1, 1]
    assert [world["active_minus_queue"]["optimal"] for world in overall["worlds"]] == [
        1,
        -1,
        0,
        0,
    ]
    assert overall["equal_world_mean_active_minus_queue"] == {
        "optimal": 0,
        "feasible": 0,
    }
    assert overall["paired_arms"]["active"]["optimal"] == 12
    assert overall["paired_arms"]["queue"]["optimal"] == 8
    assert overall["pairs_with_different_deliveries"] == 0
    windows = report["windows"]
    assert tuple(window["offset_minutes"] for window in windows) == OFFSETS_MINUTES
    assert [window["complete_pairs"] for window in windows] == [3, 1, 1]
    assert [window["worlds_with_complete_pairs"] for window in windows] == [2, 1, 1]
    assert all(
        window["equal_world_mean_active_minus_queue"] is None for window in windows
    )


def test_rejects_hash_of_requested_instead_of_delivered_report(tmp_path: Path) -> None:
    active = Cell(0, SEEDS[0], 0, "active")
    queue = Cell(0, SEEDS[0], 0, "queue")
    requested = next(case for case in queue.hidden if case != queue.queued_case)
    receipt = _receipt(queue, requested)
    _, packet = _source(queue)
    receipt["turns"]["act"] = _turn(
        clarify_views.build_final(packet, active, requested)
    )
    _write(tmp_path, queue, receipt)

    report = clarify_report.collect(PACKETS, tmp_path)

    row = _cell(report, queue)
    assert (row["status"], row["reason"]) == ("invalid", "act_turn_mismatch")
    assert row["delivered_case"] is None
    assert row["grade"] is None


@pytest.mark.parametrize(
    ("keys", "value", "reason"),
    [
        (("cell_id",), "ask-not-scheduled", "cell_identity_mismatch"),
        (("window",), False, "cell_identity_mismatch"),
        (("arm",), "active", "cell_identity_mismatch"),
        (("packet_sha256",), "0" * 64, "packet_sha256_mismatch"),
        (("selected_case",), True, "selected_case_invalid"),
        (("delivered_case",), 3, "delivered_case_mismatch"),
        (("delivered_case",), None, "delivered_case_mismatch"),
        (("status",), "started", "checkpoint_mismatch"),
        (("status",), "transport_failed", "outcome_on_incomplete_receipt"),
        (
            ("turns", "select", "served_model"),
            "different-model",
            "select_turn_mismatch",
        ),
        (("turns", "select", "served_model"), "DeepSeek-Flash", "select_turn_mismatch"),
        (("turns", "select", "prompt_sha256"), "a" * 64, "select_turn_mismatch"),
        (("actions", "rows", 0, "id"), 7, "actions_invalid"),
        (("grade", "optimal"), 0, "grade_mismatch"),
        (("grade", "by_case", 0, "feasible"), 1, "grade_mismatch"),
    ],
)
def test_rejects_mismatched_receipt_without_counting_a_grade(
    tmp_path: Path, keys: tuple[str | int, ...], value: object, reason: str
) -> None:
    cell = Cell(0, SEEDS[0], 0, "queue")
    receipt = _receipt(cell, cell.queued_case)
    target: Any = receipt
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    _write(tmp_path, cell, receipt)

    report = clarify_report.collect(PACKETS, tmp_path)

    row = _cell(report, cell)
    assert (row["status"], row["reason"]) == ("invalid", reason)
    assert row["grade"] is None
    assert report["overall"]["arms"]["queue"]["decisions"] == 0


def test_packet_world_must_match_native_truth_not_just_supplied_hash(
    tmp_path: Path,
) -> None:
    packets = tmp_path / "packets"
    packets.mkdir()
    for seed in SEEDS:
        raw, packet = _source(Cell(0, seed, 0, "active"))
        if seed == SEEDS[0]:
            packet["world"][0]["after"] = ["B", "C"]
            packet["world_sha256"] = view_hash(packet["world"])
            raw = json.dumps(packet).encode("utf-8")
        packet_path(packets, seed).write_bytes(raw)

    with pytest.raises(ValueError, match="world does not match"):
        clarify_report.collect(packets, tmp_path)
