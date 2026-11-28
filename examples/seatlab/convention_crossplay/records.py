import re
from dataclasses import dataclass

from ..coupled_retention.records import Cell

Game = tuple[tuple[str, ...], tuple[str, ...], list[Cell]]


def team_name(value: object) -> str:
    if (
        type(value) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", value) is None
    ):
        raise ValueError("invalid team name")
    return value


@dataclass(frozen=True, slots=True)
class Team:
    name: str
    east: dict[str, str]
    west: dict[str, str]
