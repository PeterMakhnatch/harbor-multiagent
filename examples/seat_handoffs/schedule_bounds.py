from __future__ import annotations

from collections import Counter
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schedule_native import Team

Window = tuple[str, int]
WINDOWS: frozenset[Window] = frozenset(
    (day, hour) for day in ("Mon", "Tue", "Wed", "Thu", "Fri") for hour in range(9, 16)
)
METRICS = ("cross_seat_improved", "cross_seat_worsened", "final_cross_seat_worsened")


def possible(teams: list[Team]) -> frozenset[Window]:
    candidates: set[Window] = set(WINDOWS)
    for team in teams:
        final = team.writes[-1].choice if team.writes else None
        if team.reward == 1:
            if final is None or final not in candidates:
                return frozenset()
            candidates.clear()
            candidates.add(final)
        elif final is not None:
            candidates.discard(final)
    return frozenset(candidates)


def bounds(teams: list[Team], candidates: frozenset[Window]) -> dict[str, list[int]]:
    if not candidates:
        raise ValueError("inconsistent schedule witnesses")
    improved: Counter[Window] = Counter()
    worsened: Counter[Window] = Counter()
    last_worsened: Counter[Window] = Counter()
    for team in teams:
        for before, after in pairwise(team.writes):
            if before.seat == after.seat or before.choice == after.choice:
                continue
            if after.choice is not None and after.choice in candidates:
                improved[after.choice] += 1
            if before.choice is not None and before.choice in candidates:
                worsened[before.choice] += 1
                if after.n == len(team.writes):
                    last_worsened[before.choice] += 1
    result = {}
    for metric, counts in zip(
        METRICS, (improved, worsened, last_worsened), strict=True
    ):
        values = [counts[window] for window in candidates]
        result[metric] = [min(values), max(values)]
    return result
