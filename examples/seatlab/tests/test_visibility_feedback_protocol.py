from __future__ import annotations

import json
import re
from pathlib import Path
from runpy import run_path
from typing import Any

import pytest

from examples.seatlab.visibility import feedback_protocol as protocol

_WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "tasks/dispatch-handoff/environment/seatworld.py"
)


def _data(turn: protocol.Turn) -> dict[str, Any]:
    return json.loads(turn.prompt.split("\n\nData:\n", 1)[1])


def _copy(turn: protocol.Turn) -> str:
    if turn.stage == "review":
        return json.dumps(_data(turn))
    if turn.stage == "select":
        return '{"ask":"X"}'
    return '{"bay":"A"}'


def test_register_order_and_block_commitments() -> None:
    assert [b.key for b in protocol.grid((5201, 5202))] == [
        "hz-s5201-switch-r0",
        "hz-s5201-stable-r0",
        "hz-s5201-stable-r1",
        "hz-s5201-switch-r1",
        "hz-s5202-stable-r0",
        "hz-s5202-switch-r0",
        "hz-s5202-switch-r1",
        "hz-s5202-stable-r1",
    ]
    blocks = protocol.grid()
    assert len(blocks) == 48
    assert len({protocol.fingerprint(block) for block in blocks}) == 48
    same = protocol.Block(5201, "switch", 0)
    assert protocol.fingerprint(same) == protocol.fingerprint(blocks[0])


@pytest.mark.parametrize(
    "seeds", [(1, 1), (True,), (0,), (2_147_483_648,), tuple(range(1, 66))]
)
def test_register_rejects_invalid_seeds(seeds: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        protocol.grid(seeds)


@pytest.mark.parametrize("seed", [5201, 5202, 2_147_483_647])
def test_shipments_use_all_odd_ids_from_the_native_world(seed: int) -> None:
    world = run_path(str(_WORLD_PATH))["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": 20,
            "condition": "pooled",
        }
    )
    cases = world.evidence()["cases"]
    shipments = protocol.shipments(seed)
    assert [shipment.native_id for shipment in shipments] == list(range(1, 20, 2))
    for shipment in shipments:
        case = cases[shipment.native_id]
        assert shipment.before == tuple(case["before"])
        assert shipment.after == tuple(case["after"])
        assert shipment.utilities == tuple(case["utilities"][bay] for bay in "ABCD")


@pytest.mark.parametrize(
    ("seed", "rep", "expected"),
    [(5201, 0, "XY"), (5201, 1, "YX"), (5202, 0, "XY"), (5203, 0, "YX")],
)
def test_warmups_are_shared_calls_without_a_selector(
    seed: int, rep: int, expected: str
) -> None:
    calls: list[protocol.Turn] = []

    def ask(turn: protocol.Turn) -> str | None:
        calls.append(turn)
        return _copy(turn) if turn.arm == "warm" else None

    rows = protocol.episode(protocol.Block(seed, "stable", rep), ask)
    assert [(turn.arm, turn.step, turn.stage) for turn in calls[:4]] == [
        ("warm", 0, "review"),
        ("warm", 0, "decide"),
        ("warm", 1, "review"),
        ("warm", 1, "decide"),
    ]
    assert [row["desk"] for row in rows[:2]] == list(expected)
    assert len(calls) == 6
    assert calls[4].prompt == calls[5].prompt


def test_live_audit_changes_the_recent_policy_after_the_switch() -> None:
    block = protocol.Block(5202, "switch", 0)
    source = protocol.shipments(block.seed)
    calls: list[protocol.Turn] = []
    desks: dict[tuple[str, int], str] = {("warm", 0): "X", ("warm", 1): "Y"}
    reports: dict[tuple[str, int], list[str]] = {}
    audit: dict[tuple[str, int], list[dict[str, Any]]] = {}

    def ask(turn: protocol.Turn) -> str:
        calls.append(turn)
        data = _data(turn)
        key = turn.arm, turn.step
        assert not re.search(
            r"\b(seed|step|arm|stable|switch|recent|lifetime|revision|previous|current|native_id)\b",
            turn.prompt,
            flags=re.IGNORECASE,
        )
        assert block.key not in turn.prompt
        assert turn.role == (
            "route_review" if turn.stage == "review" else "route_decide"
        )
        shipment = source[turn.step]
        if turn.stage == "select":
            assert list(data) == ["audit"]
            x, y = data["audit"]
            assert (x["desk"], y["desk"]) == ("X", "Y")
            assert x.keys() == y.keys() == {"desk", "exact", "checked"}
            audit[key] = [x, y]
            desks[key] = "X" if x["exact"] > y["exact"] else "Y"
            return json.dumps({"ask": desks[key]})
        if turn.stage == "review":
            current = "X" if turn.step < 6 else "Y"
            assigned = shipment.after if desks[key] == current else shipment.before
            assert data == {"available": list(assigned)}
            reports[key] = list(reversed(data["available"]))
            return json.dumps({"available": reports[key]})
        utilities = dict(zip("ABCD", shipment.utilities, strict=True))
        assert data == {"available": reports[key], "utilities": utilities}
        assert list(data["utilities"]) == list("ABCD")
        return json.dumps({"bay": max(reports[key], key=utilities.__getitem__)})

    rows = protocol.episode(block, ask)
    assert len({protocol.run_id(block, turn) for turn in calls}) == len(calls) == 52
    assert [(row["arm"], row["step"]) for row in rows] == [
        ("warm", 0),
        ("warm", 1),
        *[
            (arm, step)
            for step in range(2, 10)
            for arm in (protocol.ARMS if step % 2 == 0 else protocol.ARMS[::-1])
        ],
    ]
    assert (
        audit[("lifetime", 2)]
        == audit[("recent", 2)]
        == [
            {"desk": "X", "exact": 1, "checked": 1},
            {"desk": "Y", "exact": 0, "checked": 1},
        ]
    )
    assert audit[("recent", 4)] == [
        {"desk": "X", "exact": 2, "checked": 2},
        {"desk": "Y", "exact": 0, "checked": 0},
    ]
    assert audit[("lifetime", 8)] == [
        {"desk": "X", "exact": 5, "checked": 7},
        {"desk": "Y", "exact": 0, "checked": 1},
    ]
    assert audit[("recent", 8)] == [
        {"desk": "X", "exact": 0, "checked": 2},
        {"desk": "Y", "exact": 0, "checked": 0},
    ]
    assert [desks[("lifetime", step)] for step in range(6, 10)] == list("XXXX")
    assert [desks[("recent", step)] for step in range(6, 10)] == list("XXYY")
    for row in rows:
        shipment = source[row["step"]]
        utilities = dict(zip("ABCD", shipment.utilities, strict=True))
        before_best = max(shipment.before, key=utilities.__getitem__)
        after_best = max(shipment.after, key=utilities.__getitem__)
        assert row["native_id"] == shipment.native_id
        assert row["status"] == "complete"
        assert row["copied"] is True
        assert row["exact"] == (row["desk"] == row["current_desk"])
        assert row["source_sensitive"] == (before_best != after_best)
        assert row["optimal"] == (row["bay"] == after_best)
        assert row["feasible"] == (row["bay"] in shipment.after)


def test_history_counts_the_actual_report_not_the_desk_or_final_choice() -> None:
    block = protocol.Block(5202, "stable", 0)
    shipment = protocol.shipments(block.seed)[0]
    other_bays = [bay for bay in "ABCD" if bay not in shipment.after]
    utilities = dict(zip("ABCD", shipment.utilities, strict=True))
    best = max(shipment.after, key=utilities.__getitem__)

    def ask(turn: protocol.Turn) -> str:
        if turn.stage == "select":
            assert _data(turn) == {
                "audit": [
                    {"desk": "X", "exact": 0, "checked": 1},
                    {"desk": "Y", "exact": 0, "checked": 1},
                ]
            }
            return '{"ask":"X","ask":"Y"}'
        if turn.step == 0:
            if turn.stage == "review":
                return json.dumps({"available": other_bays})
            assert _data(turn)["available"] == other_bays
            return json.dumps({"bay": best})
        return _copy(turn)

    rows = protocol.episode(block, ask)
    warm = rows[0]
    assert warm["bay"] == best
    assert warm["optimal"] is warm["feasible"] is True
    assert warm["exact"] is warm["copied"] is False
    assert [row["status"] for row in rows[2:4]] == ["invalid_select"] * 2
    assert all(row["desk"] is None for row in rows[2:])
    assert all(row["status"] == "blocked_history" for row in rows[4:])


def test_missing_final_retains_actual_report_and_blocks_one_policy() -> None:
    block = protocol.Block(5202, "stable", 0)
    current = protocol.shipments(block.seed)[2].after
    recent_calls: list[str] = []

    def ask(turn: protocol.Turn) -> str | None:
        if turn.arm != "recent":
            return _copy(turn)
        recent_calls.append(turn.suffix)
        if turn.stage == "select":
            return '{"ask":"Y"}'
        if turn.stage == "review":
            return json.dumps({"available": current[::-1]})
        assert _data(turn)["available"] == list(current[::-1])
        return None

    rows = protocol.episode(block, ask)
    assert len(rows) == 18
    assert recent_calls == ["recent-2-select", "recent-2-review", "recent-2-decide"]
    recent = [row for row in rows if row["arm"] == "recent"]
    first = recent[0]
    assert (first["status"], first["desk"], first["exact"], first["copied"]) == (
        "missing_decide",
        "Y",
        True,
        False,
    )
    assert all(first[field] is None for field in ("bay", "optimal", "feasible"))
    assert all(row["status"] == "blocked_history" for row in recent[1:])
    assert all(row["status"] == "complete" for row in rows if row["arm"] != "recent")


@pytest.mark.parametrize(
    ("stage", "reply", "status"),
    [
        ("review", None, "missing_review"),
        ("review", "", "invalid_review"),
        ("review", '{"available":["A","B"],"available":["C","D"]}', "invalid_review"),
        ("review", '{"available":["A","A"]}', "invalid_review"),
        ("review", '{"available":["A","B"],"revision":1}', "invalid_review"),
        ("decide", '{"bay":true}', "invalid_decide"),
    ],
)
def test_failed_warmup_blocks_both_policies(
    stage: str, reply: str | None, status: str
) -> None:
    calls: list[str] = []

    def ask(turn: protocol.Turn) -> str | None:
        calls.append(turn.suffix)
        return reply if turn.stage == stage else _copy(turn)

    rows = protocol.episode(protocol.Block(5202, "stable", 0), ask)
    expected_calls = (
        ["warm-0-review"] if stage == "review" else ["warm-0-review", "warm-0-decide"]
    )
    assert calls == expected_calls
    assert len(rows) == 18
    assert rows[0]["status"] == status
    assert rows[0]["desk"] == "X"
    assert rows[0]["bay"] is rows[0]["optimal"] is rows[0]["feasible"] is None
    expected_check = True if stage == "decide" else None
    assert rows[0]["exact"] is rows[0]["copied"] is expected_check
    assert all(
        row["status"] == "blocked_history" and row["desk"] is None for row in rows[1:]
    )
