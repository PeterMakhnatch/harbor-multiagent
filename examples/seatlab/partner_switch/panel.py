from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from ..convention_crossplay.records import team_name
from . import engine

Inputs = dict[tuple[str, str], set[bytes]]
Decisions = dict[tuple[str, str, bytes], str]
Choice = Callable[[str, str, bytes], str]
Worlds = tuple[tuple[engine.Branch, ...], ...]


@dataclass(frozen=True, slots=True)
class Schedule:
    names: tuple[str, ...]
    worlds: tuple[Worlds, ...]
    report_decisions: int


def report_inputs(cases: tuple[engine.Case, ...], names: tuple[str, ...]) -> Inputs:
    if len(cases) != 6 or any(not isinstance(case, engine.Case) for case in cases):
        raise ValueError("expected six native cases")
    if not isinstance(names, tuple) or not 2 <= len(names) <= 6:
        raise ValueError("expected 2 to 6 teams")
    if len({team_name(name) for name in names}) != len(names):
        raise ValueError("duplicate team name")
    return {
        (name, role): {case.reports[r] for case in cases}
        for name in names
        for r, role in enumerate(engine.ROLES)
    }


def _decisions(inputs: Inputs, choose: Choice) -> Decisions:
    decisions: Decisions = {}
    for (name, role), packets in inputs.items():
        for packet in sorted(packets):
            slot = choose(name, role, packet)
            if type(slot) is not str or slot not in engine.OPTIONS:
                raise ValueError("invalid slot decision")
            decisions[name, role, packet] = slot
    return decisions


def deliver(
    cases: tuple[engine.Case, ...], names: tuple[str, ...], report: Choice
) -> Schedule:
    decisions = _decisions(report_inputs(cases, names), report)
    worlds = tuple(
        tuple(
            tuple(
                engine.exchange(
                    case,
                    decisions[east, "east", case.reports[0]],
                    decisions[west, "west", case.reports[1]],
                )
                for west in names
            )
            for east in names
        )
        for case in cases
    )
    return Schedule(names, worlds, len(decisions))


def vote_inputs(schedule: Schedule) -> Inputs:
    names = schedule.names
    inputs: Inputs = {(name, role): set() for name in names for role in engine.ROLES}
    for world in schedule.worlds:
        for east, row in zip(names, world, strict=True):
            for west, branch in zip(names, row, strict=True):
                inputs[east, "east"].add(branch.votes[0])
                inputs[west, "west"].add(branch.votes[1])
    return inputs


def _votes(
    decisions: Decisions, east: str, west: str, branch: engine.Branch
) -> tuple[str, str]:
    return decisions[east, "east", branch.votes[0]], decisions[
        west, "west", branch.votes[1]
    ]


def _summary(totals: list[list[int]], cases: int) -> dict[str, Any]:
    n = len(totals)
    home = sum(totals[i][i] for i in range(n))
    mixed = sum(map(sum, totals)) - home
    return {
        "matrix": [[str(Fraction(value, cases)) for value in row] for row in totals],
        "home_mean": str(Fraction(home, cases * n)),
        "mixed_mean": str(Fraction(mixed, cases * n * (n - 1))),
    }


def _histograms(schedule: Schedule) -> dict[str, dict[str, dict[str, int]]]:
    return {
        name: {
            role: dict(Counter(world[i][i].reports[r] for world in schedule.worlds))
            for r, role in enumerate(engine.ROLES)
        }
        for i, name in enumerate(schedule.names)
    }


def score(schedule: Schedule, vote: Choice) -> dict[str, Any]:
    names = schedule.names
    n = len(names)
    count = len(schedule.worlds)
    decisions = _decisions(vote_inputs(schedule), vote)
    native = [[0] * n for _ in names]
    frozen = [[0] * n for _ in names]
    changed_peer_views = 0
    changed_votes = 0
    for world in schedule.worlds:
        home = [
            _votes(decisions, name, name, world[i][i]) for i, name in enumerate(names)
        ]
        for i, east in enumerate(names):
            for j, west in enumerate(names):
                branch = world[i][j]
                actual = _votes(decisions, east, west, branch)
                original = home[i][0], home[j][1]
                actual_score = engine.grade(branch, *actual)
                native[i][j] += actual_score
                # Grade the original actions on this branch, not an old partnership's world.
                frozen[i][j] += (
                    actual_score
                    if original == actual
                    else engine.grade(branch, *original)
                )
                if i != j:
                    changed_peer_views += int(
                        branch.reports[1] != world[i][i].reports[1]
                    )
                    changed_peer_views += int(
                        branch.reports[0] != world[j][j].reports[0]
                    )
                    changed_votes += int(actual[0] != original[0]) + int(
                        actual[1] != original[1]
                    )
    actual_summary = _summary(native, count)
    frozen_summary = _summary(frozen, count)
    histograms = _histograms(schedule)
    return {
        "order": list(names),
        "cases": count,
        "native": actual_summary,
        "frozen_home_vote": frozen_summary,
        "mixed_overestimate": str(
            Fraction(frozen_summary["mixed_mean"])
            - Fraction(actual_summary["mixed_mean"])
        ),
        "changed_peer_views": changed_peer_views,
        "changed_votes": changed_votes,
        "observed_role_opportunities": count * n * (n - 1) * 2,
        "report_histograms": histograms,
        "matched_report_histograms": all(
            histograms[name] == histograms[names[0]] for name in names
        ),
        "report_decisions": schedule.report_decisions,
        "vote_decisions": len(decisions),
    }
