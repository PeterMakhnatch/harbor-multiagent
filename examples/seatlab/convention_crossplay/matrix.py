import hashlib
import json
from fractions import Fraction
from typing import Any

from ..coupled_retention import audit
from ..coupled_retention.strategy import digest
from .records import Game, Team, team_name


def _fingerprint(game: Game, keys: list[tuple[str, str]]) -> str:
    h = hashlib.sha256(b"seatlab.crossplay.weighted-game/v1\n")

    def add(value: object) -> None:
        h.update(
            json.dumps(value, separators=(",", ":"), allow_nan=False).encode("ascii")
            + b"\n"
        )

    add(game[:2])
    for (east, west), cell in zip(keys, game[2], strict=True):
        add((cell.weight, east, west, cell.scores))
    return "sha256:" + h.hexdigest()


def gate(result: dict[str, Any], sign: str | None, equal_home: bool) -> int:
    gap = Fraction(result["home_advantage"])
    actual = "positive" if gap > 0 else "negative" if gap < 0 else "zero"
    return int(
        (sign is not None and sign != actual)
        or (equal_home and not result["home_values_equal"])
    )


def evaluate(
    game: Game, teams: list[Team], *, unit: str = "convention"
) -> dict[str, Any]:
    if unit not in ("convention", "recorded_team"):
        raise ValueError("invalid population unit")
    if type(teams) is not list or not 2 <= len(teams) <= 12:
        raise ValueError("expected 2 to 12 teams")
    if type(game) is not tuple or len(game) != 3:
        raise ValueError("invalid game")
    east_labels, west_labels, rows = game
    east_actions = audit._indices(east_labels)
    west_actions = audit._indices(west_labels)
    if type(rows) is not list or not 1 <= len(rows) <= 256:
        raise ValueError("invalid cell count")

    keys: list[tuple[str, str]] = []
    for cell in rows:
        audit._validate_cell(cell, len(east_actions), len(west_actions))
        keys.append((digest(cell.east), digest(cell.west)))
    east_support = {east for east, _ in keys}
    west_support = {west for _, west in keys}
    names: list[str] = []
    behaviors: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    east_votes: list[tuple[int, ...]] = []
    west_votes: list[tuple[int, ...]] = []
    for team in teams:
        if type(team) is not Team:
            raise ValueError("invalid team")
        team_name(team.name)
        if team.name in names:
            raise ValueError("duplicate team name")
        east = audit._policy_indices(team.east, east_actions)
        west = audit._policy_indices(team.west, west_actions)
        if east.keys() != east_support or west.keys() != west_support:
            raise ValueError("observation rule coverage must match each role")
        east_vector = tuple(east[key] for key, _ in keys)
        west_vector = tuple(west[key] for _, key in keys)
        behavior = (east_vector, west_vector)
        if unit == "convention" and behavior in behaviors:
            raise ValueError("duplicate team behavior")
        behaviors.add(behavior)
        names.append(team.name)
        east_votes.append(east_vector)
        west_votes.append(west_vector)

    total = sum(cell.weight for cell in rows)
    scores = [
        [
            Fraction(
                sum(
                    cell.weight * cell.scores[east][west]
                    for cell, east, west in zip(
                        rows, east_vector, west_vector, strict=True
                    )
                ),
                total,
            )
            for west_vector in west_votes
        ]
        for east_vector in east_votes
    ]
    home = [scores[i][i] for i in range(len(names))]
    cross = [
        score for i, row in enumerate(scores) for j, score in enumerate(row) if i != j
    ]
    home_mean = sum(home, Fraction()) / len(home)
    cross_mean = sum(cross, Fraction()) / len(cross)
    return {
        "unit": unit,
        "distinct_behaviors": len(behaviors),
        "order": names,
        "matrix": [[str(score) for score in row] for row in scores],
        "home_mean": str(home_mean),
        "cross_mean": str(cross_mean),
        "home_advantage": str(home_mean - cross_mean),
        "home_values_equal": len(set(home)) == 1,
        "decision_game_sha256": _fingerprint(game, keys),
        "min_cross": str(min(cross)),
        "max_cross": str(max(cross)),
        "home_pairs": len(home),
        "cross_pairs": len(cross),
        "rows": len(rows),
        "total_weight": total,
    }
