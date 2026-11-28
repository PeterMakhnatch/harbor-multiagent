import json
from collections import Counter
from itertools import permutations
from typing import Any

from ..coupled_retention import bounds, native, panel, policy, strategy
from ..coupled_retention.records import NativeCase
from .records import Game, Team


def _active_labels(cases: list[NativeCase]) -> tuple[str, ...]:
    active: set[str] = set()
    incumbents: set[str] = set()
    for case in cases:
        east = json.loads(case.east[0])
        west = json.loads(case.west[0])
        active.update(east["own"])
        active.update(west["own"])
        incumbents.update((east["incumbent"], west["incumbent"]))
    if len(active) != 3 or len(incumbents) != 1 or active & incumbents:
        raise ValueError("expected three active alternatives and a fixed incumbent")
    return tuple(sorted(active))


def _relabel_packet(packet: bytes, labels: dict[str, str]) -> bytes:
    observed: tuple[str, ...] | None
    data = json.loads(packet)
    peer = tuple(data["peer"])
    if peer == policy.OMITTED:
        observed = None
    elif peer == policy.PASSED:
        observed = ()
    else:
        observed = tuple(labels[label] for label in peer)
    return policy.render(
        data["role"],
        labels[data["incumbent"]],
        tuple(data["options"]),
        tuple(labels[label] for label in data["own"]),
        observed,
    )


def _require_automorphism(
    game: Game, labels: dict[str, str]
) -> tuple[dict[bytes, bytes], dict[bytes, bytes]]:
    east_actions, west_actions, rows = game
    if any(
        set(labels) != set(actions) or set(labels.values()) != set(actions)
        for actions in (east_actions, west_actions)
    ):
        raise ValueError("automorphism requires a bijection of both action rosters")
    inverse = {target: source for source, target in labels.items()}
    east_indices = tuple(east_actions.index(inverse[label]) for label in east_actions)
    west_indices = tuple(west_actions.index(inverse[label]) for label in west_actions)
    east = {p: _relabel_packet(p, labels) for p in {row.east for row in rows}}
    west = {p: _relabel_packet(p, labels) for p in {row.west for row in rows}}
    original: Counter[tuple[bytes, bytes, tuple[tuple[int, ...], ...]]] = Counter()
    transformed: Counter[tuple[bytes, bytes, tuple[tuple[int, ...], ...]]] = Counter()
    for row in rows:
        scores = tuple(
            tuple(row.scores[i][j] for j in west_indices) for i in east_indices
        )
        original[row.east, row.west, row.scores] += row.weight
        transformed[east[row.east], west[row.west], scores] += row.weight
    if transformed != original:
        raise ValueError("label permutation is not a weighted game automorphism")
    return east, west


def _push_forward(
    rule: dict[bytes, int],
    packets: dict[bytes, bytes],
    actions: tuple[str, ...],
    labels: dict[str, str],
) -> dict[str, str]:
    if rule.keys() != packets.keys():
        raise ValueError("optimal rule does not cover the role observation support")
    result = {
        strategy.digest(packets[packet]): labels[actions[action]]
        for packet, action in rule.items()
    }
    if len(result) != len(rule):
        raise ValueError("duplicate transformed observation digest")
    return result


def native_roster(
    start: int, sender: str, arm: str
) -> tuple[Game, list[Team], dict[str, Any]]:
    if arm not in panel.ARMS:
        raise ValueError("unknown schedule arm")
    cases = native.native_cases(start, sender)
    rows = panel.schedules(cases)[arm]
    actions = cases[0].actions
    game: Game = (actions, actions, rows)
    active = _active_labels(cases)
    values, east_rule, west_rule = bounds.optimize(rows)
    teams: list[Team] = []
    multiplicities: dict[str, int] = {}
    for index, order in enumerate(permutations(active)):
        labels = dict(zip(active, order, strict=True))
        labels.update((label, label) for label in actions if label not in active)
        east_packets, west_packets = _require_automorphism(game, labels)
        east = _push_forward(east_rule, east_packets, actions, labels)
        west = _push_forward(west_rule, west_packets, actions, labels)
        match = next(
            (team for team in teams if team.east == east and team.west == west),
            None,
        )
        if match is None:
            match = Team(f"p{index}", east, west)
            teams.append(match)
        multiplicities[match.name] = multiplicities.get(match.name, 0) + 1
    return (
        game,
        teams,
        {
            "development_seeds": [case.seed for case in cases],
            "active_labels": list(active),
            "permutations": 6,
            "distinct_conventions": len(teams),
            "multiplicities": multiplicities,
            "decentralized": values["decentralized"],
        },
    )
