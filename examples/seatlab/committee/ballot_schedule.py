"""Run each vote interface once per source seat in each of two blocks."""

from dataclasses import dataclass

from .schedule import ROLES

ARMS = ("private", "choices", "cited")


@dataclass(frozen=True, slots=True)
class Cell:
    block: int
    map_index: int
    seat_index: int
    arm: str

    @property
    def cell_id(self) -> str:
        return f"pb-b{self.block}-m{self.map_index}-s{self.seat_index + 1}-{self.arm}"

    @property
    def role(self) -> str:
        return ROLES[self.seat_index]


def grid() -> tuple[Cell, ...]:
    cells = []
    for block in range(2):
        for map_index in range(6):
            for seat_index in range(len(ROLES)):
                offset = (map_index + seat_index + block) % len(ARMS)
                order = ARMS[offset:] + ARMS[:offset]
                for arm in reversed(order) if block else order:
                    cells.append(Cell(block, map_index, seat_index, arm))
    return tuple(cells)
