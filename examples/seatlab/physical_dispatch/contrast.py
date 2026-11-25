import re
from fractions import Fraction
from typing import Any, cast

from .cohort_draw import CELLS as CELL_ORDER

STATUSES = ("complete", "missing_artifact", "incomplete", "inconsistent_or_unreadable")
CELL_KEYS = frozenset({"status", "native_reward"})
CASE_ID = re.compile(r"case-[0-9]{4}")
CASES_MIN = 4
CASES_MAX = 64
CASES_STEP = 4
MAP_WEIGHT = Fraction(1, 2)
RELEVANT_SIGN = {
    "original-other": -1,
    "original-relevant": 1,
    "swapped-other": -1,
    "swapped-relevant": 1,
}


def exact(value: Fraction) -> str:
    assert isinstance(value, Fraction)
    return str(value)


def check_tickets(tickets: list[str]) -> None:
    if not isinstance(tickets, list):
        raise TypeError("tickets must be a list")
    count = len(tickets)
    if count < CASES_MIN or count > CASES_MAX or count % CASES_STEP != 0:
        raise ValueError("case count must be 4..64 and divisible by 4")
    for ticket in tickets:
        if not isinstance(ticket, str) or CASE_ID.fullmatch(ticket) is None:
            raise ValueError("case id must match case-NNNN")
    if len(set(tickets)) != count:
        raise ValueError("duplicate case id")


def check_cell(cell: object) -> tuple[str, int | None]:
    if not isinstance(cell, dict) or set(cell) != CELL_KEYS:
        raise ValueError("cell must have exactly status and native_reward")
    fields = cast(dict[str, object], cell)
    status = fields["status"]
    reward = fields["native_reward"]
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError("unknown cell status")
    if status == "complete":
        if type(reward) is not int or reward not in (0, 1):
            raise ValueError("complete cell needs int reward 0 or 1")
        return status, reward
    if reward is not None:
        raise ValueError("non-complete cell needs null reward")
    return status, None


def case_rewards(case_cells: object) -> list[tuple[str, int | None]]:
    if not isinstance(case_cells, dict) or set(case_cells) != set(CELL_ORDER):
        raise ValueError("case must have exactly the four planned cells")
    fields = cast(dict[str, object], case_cells)
    return [check_cell(fields[name]) for name in CELL_ORDER]


def case_contrast(
    rewards: list[int | None], unknown_relevant: int, unknown_other: int
) -> Fraction:
    assert len(rewards) == len(CELL_ORDER)
    assert unknown_relevant in (0, 1) and unknown_other in (0, 1)
    total = Fraction(0)
    for name, reward in zip(CELL_ORDER, rewards, strict=True):
        sign = RELEVANT_SIGN[name]
        if reward is None:
            reward = unknown_relevant if sign > 0 else unknown_other
        total += MAP_WEIGHT * sign * reward
    assert -1 <= total <= 1
    return total


def summarize(
    tickets: list[str], cells: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    check_tickets(tickets)
    if not isinstance(cells, dict) or set(cells) != set(tickets):
        raise ValueError("cells must cover exactly the enrolled cases")
    case_count = len(tickets)
    status_counts = {status: 0 for status in STATUSES}
    lower = Fraction(0)
    upper = Fraction(0)
    paired = Fraction(0)
    complete_cases = 0
    for ticket in tickets:
        checked = case_rewards(cells[ticket])
        for status, _ in checked:
            status_counts[status] += 1
        rewards = [reward for _, reward in checked]
        lower += case_contrast(rewards, unknown_relevant=0, unknown_other=1)
        upper += case_contrast(rewards, unknown_relevant=1, unknown_other=0)
        if all(reward is not None for reward in rewards):
            complete_cases += 1
            paired += case_contrast(rewards, unknown_relevant=0, unknown_other=0)
    lower /= case_count
    upper /= case_count
    planned_cells = case_count * len(CELL_ORDER)
    complete_cells = status_counts["complete"]
    assert sum(status_counts.values()) == planned_cells
    assert -1 <= lower <= upper <= 1
    assert (lower == upper) == (complete_cells == planned_cells)
    return {
        "case_count": case_count,
        "complete_cases": complete_cases,
        "planned_cells": planned_cells,
        "complete_cells": complete_cells,
        "status_counts": status_counts,
        "contrast": "mean over enrolled cases of 1/2 per map of relevant minus other native reward",
        "mean_contrast_bounds": [exact(lower), exact(upper)],
        "mean_contrast": exact(lower) if lower == upper else None,
        "complete_case_mean_contrast": exact(paired / complete_cases)
        if complete_cases
        else None,
        "complete_case_denominator": complete_cases,
        "unknown_cell_handling": "each unknown binary reward ranges over 0 and 1; no imputation",
        "p_value": None,
        "scope": "enrolled cases only; descriptive; no generalization beyond these cases",
    }
