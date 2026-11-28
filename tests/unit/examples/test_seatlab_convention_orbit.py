import json
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from itertools import permutations

import pytest

from examples.seatlab.convention_crossplay.orbit import (
    _require_automorphism,
    native_roster,
)
from examples.seatlab.convention_crossplay.records import Game, Team
from examples.seatlab.coupled_retention import bounds, native, policy, strategy
from examples.seatlab.coupled_retention.records import Cell


def _reference_orbit(game: Game, base: Team) -> tuple[list[Team], dict[str, int]]:
    east_actions, _, rows = game
    active = sorted(
        {
            label
            for row in rows
            for packet in (row.east, row.west)
            for label in json.loads(packet)["own"]
        }
    )
    result: list[Team] = []
    counts: Counter[str] = Counter()
    for index, order in enumerate(permutations(active)):
        labels = dict(zip(active, order, strict=True))
        labels.update((label, label) for label in east_actions if label not in active)
        pair: list[dict[str, str]] = []
        for role in ("east", "west"):
            rules = base.east if role == "east" else base.west
            packets = {row.east if role == "east" else row.west for row in rows}
            transformed = {}
            for packet in packets:
                data = json.loads(packet)
                data["own"] = sorted(labels[label] for label in data["own"])
                if data["peer"][0] not in ("?", "-"):
                    data["peer"] = sorted(labels[label] for label in data["peer"])
                key = strategy.digest(json.dumps(data, separators=(",", ":")).encode())
                transformed[key] = labels[rules[strategy.digest(packet)]]
            pair.append(transformed)
        existing = next(
            (team for team in result if (team.east, team.west) == tuple(pair)),
            None,
        )
        if existing is None:
            existing = Team(f"p{index}", pair[0], pair[1])
            result.append(existing)
        counts[existing.name] += 1
    return result, dict(counts)


@pytest.mark.parametrize(
    ("start", "sender", "arm"),
    [(321, "truthful", "independent"), (0, "pass", "apart")],
)
def test_native_roster_is_the_complete_distinct_orbit_of_one_optimum(
    start: int, sender: str, arm: str
) -> None:
    game, teams, metadata = native_roster(start, sender, arm)
    east_actions, west_actions, rows = game
    ceilings, east_rule, west_rule = bounds.optimize(rows)
    assert teams[0] == Team(
        "p0",
        {strategy.digest(p): east_actions[a] for p, a in east_rule.items()},
        {strategy.digest(p): west_actions[a] for p, a in west_rule.items()},
    )
    expected, multiplicities = _reference_orbit(game, teams[0])
    assert teams == expected
    assert metadata == {
        "development_seeds": list(native.balanced_seeds(start)),
        "active_labels": sorted(
            {label for row in rows for label in json.loads(row.east)["own"]}
        ),
        "permutations": 6,
        "distinct_conventions": len(expected),
        "multiplicities": multiplicities,
        "decentralized": ceilings["decentralized"],
    }
    assert sum(multiplicities.values()) == 6
    for team in teams:
        assert set(team.east) == {strategy.digest(row.east) for row in rows}
        assert set(team.west) == {strategy.digest(row.west) for row in rows}
        earned = sum(
            row.weight
            * row.scores[east_actions.index(team.east[strategy.digest(row.east)])][
                west_actions.index(team.west[strategy.digest(row.west)])
            ]
            for row in rows
        )
        assert Fraction(earned, sum(row.weight for row in rows)) == Fraction(
            ceilings["decentralized"]
        )


def test_automorphism_requires_full_matrix_even_with_identical_packet_marginals():
    actions = tuple("ABCDEF")
    labels = dict(zip(actions, "ABDCEF", strict=True))
    east_c = policy.render("east", "A", actions, ("B", "C"), None)
    east_d = policy.render("east", "A", actions, ("B", "D"), None)
    west_c = policy.render("west", "A", actions, ("B", "C"), ())
    west_d = policy.render("west", "A", actions, ("B", "D"), ())
    scores = tuple(tuple(int(e == w == "B") for w in actions) for e in actions)
    first = Cell(2, east_c, west_d, scores)
    second = Cell(1, east_d, west_c, scores)
    rows = [first, second, second]
    east_map, west_map = _require_automorphism((actions, actions, rows), labels)
    assert east_map == {east_c: east_d, east_d: east_c}
    assert west_map == {west_c: west_d, west_d: west_c}
    changed = tuple(
        tuple(int(e == w == "B" or (e == "C" and w == "B")) for w in actions)
        for e in actions
    )
    broken = [replace(first, scores=changed), second, second]
    with pytest.raises(ValueError, match="weighted game automorphism"):
        _require_automorphism((actions, actions, broken), labels)
