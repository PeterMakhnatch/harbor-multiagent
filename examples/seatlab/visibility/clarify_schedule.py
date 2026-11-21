"""A paired, equal-turn test of one clarification request on recorded Harbor worlds."""

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

SEEDS = (3101, 3103, 3109, 3119)
MASKS = tuple(combinations(range(4), 2))
ARMS = ("active", "queue")
OFFSETS_MINUTES = (0, 180, 330)
WINDOW_MINUTES = 30
PACKETS = Path(__file__).resolve().parents[1] / "results"


@dataclass(frozen=True, slots=True)
class Cell:
    window: int
    seed: int
    mask: int
    arm: str

    @property
    def cell_id(self) -> str:
        return f"ask-w{self.window}-s{self.seed}-m{self.mask}-{self.arm}"

    @property
    def hidden(self) -> tuple[int, int]:
        return MASKS[self.mask]

    @property
    def queued_case(self) -> int:
        return self.hidden[(SEEDS.index(self.seed) + self.mask + self.window) % 2]

    def delivery(self, selected_case: int) -> int:
        if type(selected_case) is not int or selected_case not in range(4):
            raise ValueError("a selector must name one of the four cases")
        return selected_case if self.arm == "active" else self.queued_case


def grid() -> tuple[Cell, ...]:
    return tuple(
        Cell(window, seed, mask, arm)
        for window in range(len(OFFSETS_MINUTES))
        for index, seed in enumerate(SEEDS)
        for mask in range(len(MASKS))
        for arm in (ARMS if (window + index + mask) % 2 == 0 else ARMS[::-1])
    )


def packet_path(directory: Path, seed: int) -> Path:
    if seed not in SEEDS:
        raise ValueError("unregistered source world")
    return directory / f"dispatch-s{seed}-r1.json"
