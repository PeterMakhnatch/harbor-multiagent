import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from examples.seatlab.physical_dispatch.order_evidence import reconcile

CELLS = ("original-other", "original-relevant", "swapped-other", "swapped-relevant")
UNKNOWN = "inconsistent_or_unreadable"
BASE = datetime(2026, 1, 1, 12, tzinfo=UTC)
GONE = {"status": "missing_artifact", "native_reward": None}


def uid(n: int) -> str:
    return str(uuid.UUID(int=n, version=4))


def stamp(minute: int, offset: int) -> str:
    return (
        (BASE + timedelta(minutes=minute))
        .astimezone(timezone(timedelta(hours=offset)))
        .isoformat()
    )


def rec(
    n: int, start: int, finish: int, reward: int | None = 1, offset: int = 0
) -> dict[str, Any]:
    status = "incomplete" if reward is None else "complete"
    return {
        "status": status,
        "native_reward": reward,
        "native_trial_id": uid(n),
        "native_started_at": stamp(start, offset),
        "native_finished_at": stamp(finish, offset),
    }


def cohort(
    rows: dict[int, dict[str, dict[str, Any]]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, dict]]]:
    orders = {f"case-{i}": list(CELLS) for i in range(1, 5)}
    reports = {t: {c: deepcopy(GONE) for c in CELLS} for t in orders}
    for i, cells in rows.items():
        reports[f"case-{i}"].update(deepcopy(cells))
    return orders, reports


def grade(cell: dict[str, Any]) -> tuple[str, int | None]:
    return cell["status"], cell["native_reward"]


def run(orders: dict, reports: dict) -> tuple[dict, dict, dict[str, int]]:
    before = deepcopy((orders, reports))
    out, agg = reconcile(orders, reports)
    assert (orders, reports) == before
    text = json.dumps(agg)
    assert "2026" not in text and "case-" not in text and "-4000-" not in text
    counts = agg["recorded_order_counts"]
    assert set(counts) == {
        "not_comparable",
        "partial_consistent",
        "complete_consistent",
        "conflict",
    }
    assert sum(counts.values()) == len(orders)
    return out, agg, counts


def test_id_reused_across_cases_marks_both_cells_unknown_without_picking_a_winner() -> (
    None
):
    orders, reports = cohort(
        {
            1: {"original-other": rec(7, 0, 5), "original-relevant": rec(1, 10, 15)},
            2: {"swapped-relevant": rec(7, 0, 5, 0), "swapped-other": rec(2, 10, 15)},
        }
    )
    out, agg, counts = run(orders, reports)
    assert (
        grade(out["case-1"]["original-other"])
        == grade(out["case-2"]["swapped-relevant"])
        == (UNKNOWN, None)
    )
    assert (
        grade(out["case-1"]["original-relevant"])
        == grade(out["case-2"]["swapped-other"])
        == ("complete", 1)
    )
    assert grade(out["case-3"]["original-other"]) == ("missing_artifact", None)
    assert agg["duplicated_trial_ids"] == 1 and agg["cells_with_reused_trial_ids"] == 2
    assert counts == {
        "not_comparable": 4,
        "partial_consistent": 0,
        "complete_consistent": 0,
        "conflict": 0,
    }


def test_id_reused_inside_a_case_drops_both_cells_from_its_chronology() -> None:
    cells = {c: rec(10 + k, 10 * k, 10 * k + 5) for k, c in enumerate(CELLS)}
    cells["swapped-relevant"]["native_trial_id"] = uid(10)
    out, agg, counts = run(*cohort({1: cells}))
    assert [grade(out["case-1"][c])[0] for c in CELLS] == [
        UNKNOWN,
        "complete",
        "complete",
        UNKNOWN,
    ]
    assert agg["duplicated_trial_ids"] == 1 and agg["cells_with_reused_trial_ids"] == 2
    assert counts == {
        "not_comparable": 3,
        "partial_consistent": 1,
        "complete_consistent": 0,
        "conflict": 0,
    }


def test_complete_and_partial_order_compare_instants_across_offsets() -> None:
    plan = ["swapped-relevant", "original-other", "swapped-other", "original-relevant"]
    full = {
        c: rec(20 + k, 20 * k, 20 * k + 10, k % 2, (9, -8, 3, -11)[k])
        for k, c in enumerate(plan)
    }
    orders, reports = cohort(
        {
            1: full,
            2: {
                "original-other": rec(30, 0, 10, None, 9),
                "swapped-relevant": rec(31, 15, 25, 1, -8),
            },
            3: {"original-relevant": rec(40, 0, 10)},
        }
    )
    orders["case-1"] = plan
    out, agg, counts = run(orders, reports)
    assert {c: grade(out["case-1"][c]) for c in CELLS} == {
        c: grade(full[c]) for c in CELLS
    }
    assert grade(out["case-2"]["original-other"]) == ("incomplete", None)
    assert agg["duplicated_trial_ids"] == 0 and agg["cells_with_reused_trial_ids"] == 0
    assert counts == {
        "not_comparable": 2,
        "partial_consistent": 1,
        "complete_consistent": 1,
        "conflict": 0,
    }


def test_overlap_reversal_and_equal_start_are_conflicts_that_keep_physical_grades() -> (
    None
):
    rows = {
        1: {
            "original-other": rec(50, 0, 12, 1, 5),
            "original-relevant": rec(51, 10, 20, 0, -5),
        },
        2: {
            "original-other": rec(52, 30, 40, 0),
            "swapped-other": rec(53, 0, 10, 1),
            "original-relevant": rec(56, 50, 60, 1),
            "swapped-relevant": rec(57, 70, 80, 0),
        },
        3: {
            "swapped-other": rec(54, 0, 0, 1, 2),
            "swapped-relevant": rec(55, 0, 9, 1, -2),
        },
        4: {"original-relevant": rec(58, 0, 10), "swapped-other": rec(59, 10, 20, 0)},
    }
    orders, reports = cohort(rows)
    out, agg, counts = run(orders, reports)
    for t, cells in reports.items():
        assert {c: grade(out[t][c]) for c in CELLS} == {
            c: grade(cells[c]) for c in CELLS
        }
    assert agg["duplicated_trial_ids"] == 0 and agg["cells_with_reused_trial_ids"] == 0
    assert counts == {
        "not_comparable": 0,
        "partial_consistent": 1,
        "complete_consistent": 0,
        "conflict": 3,
    }
