from fractions import Fraction
from typing import Any

from ...joint_reopen.observation import MAX_SEED
from ...native_pair_bounds import Pair, summarize
from ..convention_crossplay.records import team_name
from .plan import CASES, MAX_NAMES


def _cohort(
    names: list[str], panels: dict[tuple[int, int], dict[int, int | None]]
) -> list[int]:
    if not isinstance(names, list) or not 2 <= len(names) <= MAX_NAMES:
        raise ValueError("expected 2..5 enrolled team names")
    if len({team_name(name) for name in names}) != len(names):
        raise ValueError("duplicate team name")
    expected = {(i, j) for i in range(len(names)) for j in range(len(names))}
    if (
        not isinstance(panels, dict)
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(index) is not int for index in pair)
            for pair in panels
        )
        or set(panels) != expected
    ):
        raise ValueError("expected the complete ordered team grid")
    seeds: set[int] | None = None
    for panel in panels.values():
        if not isinstance(panel, dict) or len(panel) != CASES:
            raise ValueError("expected six registered cases per team pair")
        if any(type(seed) is not int or not 0 <= seed <= MAX_SEED for seed in panel):
            raise ValueError("invalid registered seed")
        if any(
            grade is not None and (type(grade) is not int or grade not in (0, 1))
            for grade in panel.values()
        ):
            raise ValueError("grade is not 0, 1, or null")
        if seeds is None:
            seeds = set(panel)
        elif seeds != set(panel):
            raise ValueError("team pairs must share the same registered seeds")
    assert seeds is not None
    return sorted(seeds)


def _contrast(anchor: str, role: str, partner: str, arms: list[Pair]) -> dict[str, Any]:
    bounds = summarize(arms)
    complete = bounds["paired_finals"]
    return {
        "anchor": anchor,
        "role": role,
        "new_partner": partner,
        "registered_cases": CASES,
        "complete_pairs": complete,
        "both_win": arms.count((1, 1)),
        "both_lose": arms.count((0, 0)),
        "original_only": arms.count((0, 1)),
        "replacement_only": arms.count((1, 0)),
        "unknown_pairs": CASES - complete,
        "original_only_bounds": bounds["positive"],
        "replacement_only_bounds": bounds["negative"],
        "original_minus_replacement_bounds": [
            str(Fraction(value, CASES)) for value in bounds["net_sum"]
        ],
    }


def contrasts(
    names: list[str], panels: dict[tuple[int, int], dict[int, int | None]]
) -> list[dict[str, Any]]:
    seeds = _cohort(names, panels)
    results: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        home = panels[i, i]
        for role in ("east", "west"):
            for j, partner in enumerate(names):
                if i == j:
                    continue
                mixed = panels[i, j] if role == "east" else panels[j, i]
                arms = [(mixed[seed], home[seed]) for seed in seeds]
                results.append(_contrast(name, role, partner, arms))
    return results
