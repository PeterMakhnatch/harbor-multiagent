import itertools
import json
import random
from fractions import Fraction
from typing import Any

import pytest

from examples.seatlab.physical_dispatch.contrast import summarize

CELLS = ("original-other", "original-relevant", "swapped-other", "swapped-relevant")
FAILURES = ("incomplete", "missing_artifact", "inconsistent_or_unreadable")
STATUSES = ("complete", *FAILURES)
KEYS = {
    "planned_cells",
    "complete_cases",
    "complete_cells",
    "status_counts",
    "mean_contrast_bounds",
    "mean_contrast",
    "complete_case_mean_contrast",
}
Cells = dict[str, dict[str, dict[str, Any]]]
Rewards = dict[str, int | None]


def tickets_for(n: int) -> list[str]:
    return [f"case-{index:04d}" for index in range(1, n + 1)]


def done(reward: int) -> dict[str, Any]:
    return {"status": "complete", "native_reward": reward}


def failed(status: str = "missing_artifact") -> dict[str, Any]:
    return {"status": status, "native_reward": None}


def cohort(
    rows: list[tuple[int | None, int | None, int | None, int | None]],
) -> tuple[list[str], Cells]:
    tickets = tickets_for(len(rows))
    cells = {
        ticket: {
            condition: done(value) if value is not None else failed()
            for condition, value in zip(CELLS, row, strict=True)
        }
        for ticket, row in zip(tickets, rows, strict=True)
    }
    return tickets, cells


def rewards(cells: Cells, tickets: list[str]) -> list[Rewards]:
    return [
        {condition: cells[ticket][condition]["native_reward"] for condition in CELLS}
        for ticket in tickets
    ]


def case_contrast(row: dict[str, int]) -> Fraction:
    original = row["original-relevant"] - row["original-other"]
    swapped = row["swapped-relevant"] - row["swapped-other"]
    return Fraction(original + swapped, 2)


def population_mean(rows: list[dict[str, int]]) -> Fraction:
    return sum((case_contrast(row) for row in rows), Fraction(0)) / len(rows)


def exhaustive_bounds(rows: list[Rewards]) -> tuple[Fraction, Fraction]:
    unknown = [(i, c) for i, row in enumerate(rows) for c in CELLS if row[c] is None]
    assert len(unknown) <= 5
    means = []
    for values in itertools.product((0, 1), repeat=len(unknown)):
        filled = [dict(row) for row in rows]
        for (i, condition), value in zip(unknown, values, strict=True):
            filled[i][condition] = value
        means.append(population_mean(filled))
    assert len(means) == 2 ** len(unknown)
    return min(means), max(means)


def complete_case_mean(rows: list[Rewards]) -> Fraction | None:
    full = [row for row in rows if all(row[c] is not None for c in CELLS)]
    return population_mean(full) if full else None


def frac(value: object) -> Fraction:
    assert type(value) is str
    assert "." not in value and "e" not in value.lower()
    return Fraction(value)


def bounds(result: dict[str, Any]) -> tuple[Fraction, Fraction]:
    pair = result["mean_contrast_bounds"]
    assert type(pair) in (list, tuple) and len(pair) == 2
    low, high = frac(pair[0]), frac(pair[1])
    assert -1 <= low <= high <= 1
    return low, high


def point(value: object) -> Fraction | None:
    return None if value is None else frac(value)


def counts(result: dict[str, Any]) -> dict[str, int]:
    raw = result["status_counts"]
    assert type(raw) is dict and set(raw) <= set(STATUSES)
    assert all(type(v) is int and v >= 0 for v in raw.values())
    return {status: count for status, count in raw.items() if count}


def check(result: dict[str, Any], tickets: list[str], cells: Cells) -> None:
    rows = rewards(cells, tickets)
    statuses = [cells[t][c]["status"] for t in tickets for c in CELLS]
    assert KEYS <= set(result)
    assert result["planned_cells"] == 4 * len(tickets)
    assert result["complete_cells"] == statuses.count("complete")
    assert result["complete_cases"] == sum(
        all(row[c] is not None for c in CELLS) for row in rows
    )
    assert counts(result) == {s: statuses.count(s) for s in STATUSES if s in statuses}
    assert sum(counts(result).values()) == result["planned_cells"]
    low, high = bounds(result)
    assert (low, high) == exhaustive_bounds(rows)
    if all(s == "complete" for s in statuses):
        assert point(result["mean_contrast"]) == low == high
    else:
        assert result["mean_contrast"] is None
    assert point(result["complete_case_mean_contrast"]) == complete_case_mean(rows)
    text = json.dumps(result, sort_keys=True)
    assert not any(ticket in text for ticket in tickets)


def random_cohort(rng: random.Random, n: int, missing: int) -> tuple[list[str], Cells]:
    tickets = tickets_for(n)
    cells = {t: {c: done(rng.randint(0, 1)) for c in CELLS} for t in tickets}
    slots = rng.sample([(t, c) for t in tickets for c in CELLS], missing)
    for ticket, condition in slots:
        cells[ticket][condition] = failed(rng.choice(FAILURES))
    return tickets, cells


@pytest.mark.parametrize("n", [4, 8, 12, 64])
@pytest.mark.parametrize("missing", [0, 1, 2, 3, 4, 5])
def test_bounds_equal_every_binary_completion_of_the_unknown_cells(
    n: int, missing: int
) -> None:
    rng = random.Random(f"contrast-{n}-{missing}")
    for _ in range(6):
        tickets, cells = random_cohort(rng, n, missing)
        check(summarize(tickets, cells), tickets, cells)


def test_failed_relevant_cells_never_collide_with_reward_zero() -> None:
    genuine_tickets, genuine = cohort([(0, 0, 0, 0)] + [(0, 1, 0, 1)] * 3)
    failed_tickets, selective = cohort([(0, None, 0, None)] + [(0, 1, 0, 1)] * 3)
    zero = summarize(genuine_tickets, genuine)
    unknown = summarize(failed_tickets, selective)
    check(zero, genuine_tickets, genuine)
    check(unknown, failed_tickets, selective)
    assert point(zero["mean_contrast"]) == Fraction(3, 4)
    assert unknown["mean_contrast"] is None
    assert bounds(unknown) == (Fraction(3, 4), Fraction(1))
    assert point(unknown["complete_case_mean_contrast"]) == 1
    assert point(zero["complete_case_mean_contrast"]) == Fraction(3, 4)
    assert counts(unknown) == {"complete": 14, "missing_artifact": 2}


def test_failed_other_cells_widen_downward_not_to_zero() -> None:
    tickets, cells = cohort([(None, 1, None, 1)] + [(1, 1, 1, 1)] * 3)
    result = summarize(tickets, cells)
    check(result, tickets, cells)
    assert bounds(result) == (Fraction(0), Fraction(1, 4))
    assert point(result["complete_case_mean_contrast"]) == 0


def test_all_unknown_cohort_spans_the_full_interval_with_no_point() -> None:
    tickets = tickets_for(4)
    cells = {
        t: {c: failed(FAILURES[i % 3]) for i, c in enumerate(CELLS)} for t in tickets
    }
    result = summarize(tickets, cells)
    assert bounds(result) == (Fraction(-1), Fraction(1))
    assert result["mean_contrast"] is None
    assert result["complete_case_mean_contrast"] is None
    assert result["complete_cases"] == 0 and result["complete_cells"] == 0
    assert counts(result) == {
        "incomplete": 8,
        "missing_artifact": 4,
        "inconsistent_or_unreadable": 4,
    }
