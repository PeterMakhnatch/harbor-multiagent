from fractions import Fraction
from itertools import product
from typing import Any

type Quartet = tuple[int | None, int | None, int | None, int | None]

_METRICS = (
    "cross_disagreement",
    "within_disagreement",
    "excess_disagreement",
    "replacement_minus_original_reward",
)


def _fourths(a0: int, a1: int, b0: int, b1: int) -> tuple[int, int, int, int]:
    cross = (a0 != b0) + (a0 != b1) + (a1 != b0) + (a1 != b1)
    within = 2 * ((a0 != a1) + (b0 != b1))
    return cross, within, cross - within, 2 * (b0 + b1 - a0 - a1)


def summarize(cases: list[Quartet]) -> dict[str, Any]:
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1024:
        raise ValueError("expected 1..1024 registered cases")
    lower = [0] * len(_METRICS)
    upper = [0] * len(_METRICS)
    complete_cases = 0
    stable_original_only = 0
    stable_replacement_only = 0
    for case in cases:
        if (
            type(case) is not tuple
            or len(case) != 4
            or any(
                grade is not None and (type(grade) is not int or grade not in (0, 1))
                for grade in case
            )
        ):
            raise ValueError("expected a four-tuple of 0, 1, or null")
        if None not in case:
            complete_cases += 1
            stable_original_only += case == (1, 1, 0, 0)
            stable_replacement_only += case == (0, 0, 1, 1)
        choices = [(0, 1) if grade is None else (grade,) for grade in case]
        completions = [_fourths(*grades) for grades in product(*choices)]
        for index, values in enumerate(zip(*completions, strict=True)):
            lower[index] += min(values)
            upper[index] += max(values)

    count = len(cases)
    complete = complete_cases == count
    report: dict[str, Any] = {
        "registered_cases": count,
        "complete_cases": complete_cases,
        "unknown_cases": count - complete_cases,
        "complete": complete,
        "stable_original_only": stable_original_only,
        "stable_replacement_only": stable_replacement_only,
    }
    for metric, lo, hi in zip(_METRICS, lower, upper, strict=True):
        low = str(Fraction(lo, 4 * count))
        high = str(Fraction(hi, 4 * count))
        report[metric] = {"value": low if complete else None, "bounds": [low, high]}
    return report
