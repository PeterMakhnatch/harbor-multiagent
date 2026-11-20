"""Registered four-analyst trial order on one unmodified Crucible world."""

from typing import TypedDict

TASK_TREE = "62e6d77d171b2c022f6857781ad3ab9e4e0d4c7d"
TASK_CHECKSUM = "adb41137272dabea3eb946c8733da5424d77520200f373b14f7ef08913da4e1d"
ROLES = ("analyst_1", "analyst_2", "analyst_3", "analyst_4")
ARMS = ("open", "novelty")
TALLY_ARMS = ("plain", "first", "last")


class Cell(TypedDict):
    cell_id: str
    map_index: int
    repetition: int
    arm: str


def grid() -> list[Cell]:
    return [
        {
            "cell_id": f"hp-m{index}-r{repeat}-{arm}",
            "map_index": index,
            "repetition": repeat,
            "arm": arm,
        }
        for index in range(6)
        for repeat in range(2)
        for arm in (ARMS if (index + repeat) % 2 == 0 else ARMS[::-1])
    ]


def tally_grid() -> list[Cell]:
    """Return fresh teams for the prospective vote-format experiment."""
    cells: list[Cell] = []
    for map_index in range(6):
        for repetition in range(2):
            offset = (map_index + repetition) % len(TALLY_ARMS)
            arms = TALLY_ARMS[offset:] + TALLY_ARMS[:offset]
            for arm in arms if repetition == 0 else arms[::-1]:
                cells.append(
                    {
                        "cell_id": f"tv-m{map_index}-r{repetition}-{arm}",
                        "map_index": map_index,
                        "repetition": repetition,
                        "arm": arm,
                    }
                )
    return cells
