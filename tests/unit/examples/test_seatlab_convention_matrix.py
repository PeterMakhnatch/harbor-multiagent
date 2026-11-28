from dataclasses import replace

import pytest

from examples.seatlab.convention_crossplay.matrix import evaluate
from examples.seatlab.convention_crossplay.records import Game, Team
from examples.seatlab.coupled_retention.records import Cell
from examples.seatlab.coupled_retention.strategy import digest

E0, E1, W0 = digest(b"\x00\xff"), digest(b""), digest(b"\x00\xff")
GAME: Game = (
    ("accept", "reject"),
    ("first", "second", "third"),
    [
        Cell(2, b"\x00\xff", b"\x00\xff", ((1, 0, 1), (1, 1, 0))),
        Cell(3, b"", b"\x00\xff", ((0, 1, 1), (1, 0, 0))),
    ],
)
A = Team("A", {E0: "accept", E1: "reject"}, {W0: "first"})
B = Team("B", {E0: "reject", E1: "accept"}, {W0: "second"})
C = Team("C", dict(A.east), {W0: "third"})


def test_weighted_asymmetric_rectangular_crossplay() -> None:
    result = evaluate(GAME, [A, B, C])
    fingerprint = result.pop("decision_game_sha256")
    assert fingerprint == evaluate(GAME, [C, A])["decision_game_sha256"]
    altered = GAME[0], GAME[1], [replace(GAME[2][0], weight=3), GAME[2][1]]
    assert fingerprint != evaluate(altered, [A, B, C])["decision_game_sha256"]
    assert result == {
        "unit": "convention",
        "distinct_behaviors": 3,
        "order": ["A", "B", "C"],
        "matrix": [["1", "0", "2/5"], ["2/5", "1", "3/5"], ["1", "0", "2/5"]],
        "home_mean": "4/5",
        "cross_mean": "2/5",
        "home_advantage": "2/5",
        "home_values_equal": False,
        "min_cross": "0",
        "max_cross": "1",
        "home_pairs": 3,
        "cross_pairs": 6,
        "rows": 2,
        "total_weight": 5,
    }
    reversed_result = evaluate(GAME, [C, B, A])
    assert reversed_result["order"] == ["C", "B", "A"]
    assert reversed_result["matrix"] == [
        ["2/5", "0", "1"],
        ["3/5", "1", "2/5"],
        ["2/5", "0", "1"],
    ]
    for field in (
        "home_mean",
        "cross_mean",
        "home_advantage",
        "min_cross",
        "max_cross",
    ):
        assert reversed_result[field] == result[field]


def test_negative_advantage_and_same_west_allowed() -> None:
    teams = [
        Team("a", {E0: "accept"}, {W0: "first"}),
        Team("b", {E0: "reject"}, {W0: "second"}),
    ]
    game: Game = (
        GAME[0],
        GAME[1],
        [Cell(7, b"\x00\xff", b"\x00\xff", ((0, 1, 0), (1, 0, 0)))],
    )
    result = evaluate(game, teams)
    assert result["matrix"] == [["0", "1"], ["1", "0"]]
    assert result["home_mean"] == "0"
    assert result["cross_mean"] == "1"
    assert result["home_advantage"] == "-1"
    assert result["home_values_equal"] is True
    assert result["min_cross"] == result["max_cross"] == "1"
    same_west = replace(teams[1], west=teams[0].west)
    assert evaluate(game, [teams[0], same_west])["matrix"] == [["0", "0"], ["1", "1"]]


def test_distinct_behaviors_can_have_identical_zero_scores() -> None:
    game: Game = (
        GAME[0],
        GAME[1],
        [replace(cell, scores=((0, 0, 0), (0, 0, 0))) for cell in GAME[2]],
    )
    result = evaluate(game, [A, B, C])
    assert result["matrix"] == [["0"] * 3 for _ in range(3)]
    assert (
        result["home_mean"] == result["cross_mean"] == result["home_advantage"] == "0"
    )
    assert result["min_cross"] == result["max_cross"] == "0"
    assert result["home_values_equal"] is True


@pytest.mark.parametrize("name", ["", "_a", "a b", "a/b", "a\n", "é", "a" * 49, "A"])
def test_reject_bad_or_duplicate_team_name(name: str) -> None:
    with pytest.raises(ValueError, match="team name"):
        evaluate(GAME, [A, replace(B, name=name)])


def test_team_name_boundaries() -> None:
    names = ["0", "a" + "-_" * 23 + "z"]
    result = evaluate(GAME, [replace(A, name=names[0]), replace(B, name=names[1])])
    assert result["order"] == names


def test_reject_duplicate_behavior_with_different_names_and_rule_order() -> None:
    clone = Team("clone", {E1: "reject", E0: "accept"}, dict(A.west))
    with pytest.raises(ValueError, match="duplicate team behavior"):
        evaluate(GAME, [A, B, clone])


def test_roster_size_bounds() -> None:
    for teams in ([], [A], [A] * 13):
        with pytest.raises(ValueError, match="2 to 12"):
            evaluate(GAME, teams)
    teams = [
        Team(f"t{i}{j}{k}", {E0: a, E1: b}, {W0: c})
        for i, a in enumerate(GAME[0])
        for j, b in enumerate(GAME[0])
        for k, c in enumerate(GAME[1])
    ]
    result = evaluate(GAME, teams)
    assert result["order"] == [team.name for team in teams]
    assert result["home_pairs"] == 12
    assert result["cross_pairs"] == 132
    assert [len(row) for row in result["matrix"]] == [12] * 12


@pytest.mark.parametrize(
    "team",
    [
        replace(B, east={E0: "reject"}),
        replace(B, east={**B.east, digest(b"unseen"): "accept"}),
        replace(B, east={E0: "reject", E1: "third"}),
        replace(B, west={}),
        replace(B, west={**B.west, E1: "second"}),
        replace(B, west={W0: "accept"}),
        replace(B, west={W0.upper(): "second"}),
    ],
)
def test_reject_incomplete_extra_or_invalid_role_rules(team: Team) -> None:
    with pytest.raises(ValueError):
        evaluate(GAME, [A, team])


@pytest.mark.parametrize(
    "game",
    [
        (GAME[0], GAME[1], []),
        (("accept", "accept"), GAME[1], GAME[2]),
        (GAME[0], GAME[1], [replace(GAME[2][0], weight=0), GAME[2][1]]),
        (GAME[0], GAME[1], [replace(GAME[2][0], scores=((0, 1), (1, 0))), GAME[2][1]]),
        (
            GAME[0],
            GAME[1],
            [replace(GAME[2][0], scores=((0, 2, 1), (1, 0, 0))), GAME[2][1]],
        ),
    ],
)
def test_reject_invalid_game(game: Game) -> None:
    with pytest.raises(ValueError):
        evaluate(game, [A, B])
