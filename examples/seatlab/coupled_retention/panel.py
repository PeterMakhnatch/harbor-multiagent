import json
from collections import Counter
from fractions import Fraction
from typing import Any

from . import bounds, policy
from .records import Cell, NativeCase

ARMS = {
    "together": ((0, 0), (0, 0), (1, 1), (1, 1)),
    "apart": ((0, 1), (0, 1), (1, 0), (1, 0)),
    "independent": ((0, 0), (0, 1), (1, 0), (1, 1)),
}


def _target(scores: tuple[tuple[int, ...], ...], size: int) -> int:
    if len(scores) != size or any(len(line) != size for line in scores):
        raise ValueError("payoff matrix does not match the action roster")
    hits = []
    for i, line in enumerate(scores):
        for j, value in enumerate(line):
            if type(value) is not int or value not in (0, 1):
                raise ValueError("native rewards must be binary integers")
            if value:
                hits.append((i, j))
    if len(hits) != 1 or hits[0][0] != hits[0][1]:
        raise ValueError("native rewards require one successful diagonal vote pair")
    return hits[0][0]


def _omitted(
    role: str, packets: tuple[bytes, bytes], actions: tuple[str, ...]
) -> tuple[str, tuple[str, ...]]:
    if len(packets) != 2 or any(type(packet) is not bytes for packet in packets):
        raise ValueError("each role requires two byte packets")
    missing, retained = packets
    policy.act(missing)
    policy.act(retained)
    data = json.loads(missing)
    incumbent, own = data["incumbent"], tuple(data["own"])
    if missing != policy.render(role, incumbent, actions, own, None):
        raise ValueError("omitted packet does not match its role and action roster")
    return incumbent, own


def _validate_packets(case: NativeCase) -> None:
    incumbent, east = _omitted("east", case.east, case.actions)
    other_incumbent, west = _omitted("west", case.west, case.actions)
    if incumbent != other_incumbent:
        raise ValueError("role packets do not share an incumbent")
    for east_peer, west_peer in ((west, east), ((), ())):
        if case.east[1] == policy.render(
            "east", incumbent, case.actions, east, east_peer
        ) and case.west[1] == policy.render(
            "west", incumbent, case.actions, west, west_peer
        ):
            return
    raise ValueError(
        "retained packets must contain the reciprocal reports or both passes"
    )


def _validate_cases(cases: list[NativeCase]) -> None:
    if len(cases) != 6:
        raise ValueError("a panel requires six worlds")
    seeds = [case.seed for case in cases]
    if any(type(seed) is not int for seed in seeds) or len(set(seeds)) != 6:
        raise ValueError("world seeds must be distinct integers")
    actions = cases[0].actions
    if len(actions) != 6 or any(
        type(label) is not str or not label for label in actions
    ):
        raise ValueError("a panel requires six action labels")
    if len(set(actions)) != 6:
        raise ValueError("action labels must be unique")
    for case in cases:
        if case.actions != actions:
            raise ValueError("world action rosters must have the same order")
        _target(case.scores, len(actions))
        _validate_packets(case)


def _marginals(rows: list[Cell]) -> tuple[Counter[bytes], Counter[bytes]]:
    east: Counter[bytes] = Counter()
    west: Counter[bytes] = Counter()
    for row in rows:
        if type(row.weight) is not int or row.weight <= 0:
            raise ValueError("schedule weights must be positive integers")
        east[row.east] += row.weight
        west[row.west] += row.weight
    return east, west


def schedules(cases: list[NativeCase]) -> dict[str, list[Cell]]:
    _validate_cases(cases)
    result: dict[str, list[Cell]] = {arm: [] for arm in ARMS}
    for case in cases:
        expected = (Counter(case.east * 2), Counter(case.west * 2))
        for arm, masks in ARMS.items():
            rows = [Cell(1, case.east[e], case.west[w], case.scores) for e, w in masks]
            if len(rows) != 4 or _marginals(rows) != expected:
                raise ValueError("schedule changes a within-world role marginal")
            result[arm].extend(rows)
    expected = _marginals(result["together"])
    if any(len(rows) != 24 or _marginals(rows) != expected for rows in result.values()):
        raise ValueError("schedule changes a panel role marginal")
    return result


def _reference(rows: list[Cell], actions: tuple[str, ...]) -> dict[str, str]:
    counts = dict.fromkeys(("team", "east", "west", "split", "agree_wrong"), 0)
    total = sum(row.weight for row in rows)
    for row in rows:
        east = actions.index(policy.act(row.east)["slot"])
        west = actions.index(policy.act(row.west)["slot"])
        target = _target(row.scores, len(actions))
        reward = row.scores[east][west]
        for name, value in (
            ("team", reward),
            ("east", east == target),
            ("west", west == target),
            ("split", east != west),
            ("agree_wrong", east == west and reward == 0),
        ):
            counts[name] += row.weight * int(value)
    return {name: str(Fraction(value, total)) for name, value in counts.items()}


def qualify(cases: list[NativeCase]) -> dict[str, Any]:
    rows = schedules(cases)
    reference = {
        arm: _reference(cells, cases[0].actions) for arm, cells in rows.items()
    }
    equal_accuracy = all(
        all(stats[role] == reference["together"][role] for stats in reference.values())
        for role in ("east", "west")
    )
    if not equal_accuracy:
        raise ValueError("fixed policy accuracy differs across equal role marginals")
    return {
        "worlds": len(cases),
        "rows_per_arm": len(rows["together"]),
        "equal_role_marginals": True,
        "equal_role_accuracy": equal_accuracy,
        "team_gap": str(
            Fraction(reference["together"]["team"])
            - Fraction(reference["apart"]["team"])
        ),
        "arms": {
            arm: {"reference": reference[arm], "ceilings": bounds.evaluate(cells)}
            for arm, cells in rows.items()
        },
    }
