"""Fixed native Dispatch Handoff roster comparison. No provider or Harbor imports."""

from ..team_driver import Cell as TeamCell

SEEDS = (4001, 4003, 4007, 4013)
REPETITIONS = range(2)
EPISODES = 6
ROLES = ("inventory", "dispatch")
CLAUDE = "anthropic/claude-sonnet-5-5"
DIRECT = "deepseek/deepseek-flash"
_TREATMENTS = (
    ("fresh", "cd"),
    ("fresh", "dc"),
    ("reordered", "cd"),
    ("reordered", "dc"),
)
_ORDER = ((0, 1, 3, 2), (1, 2, 0, 3), (2, 3, 1, 0), (3, 0, 2, 1))


class Cell(TeamCell):
    repetition: int


def grid() -> list[Cell]:
    schedule: list[Cell] = []
    for index, seed in enumerate(SEEDS):
        for repetition in REPETITIONS:
            for arm in _ORDER[(index + 2 * repetition) % 4]:
                condition, pairing = _TREATMENTS[arm]
                schedule.append(
                    {
                        "seed": seed,
                        "repetition": repetition,
                        "condition": condition,
                        "pairing": pairing,
                        "cell_id": f"mx-s{seed}-r{repetition}-{condition}-{pairing}",
                    }
                )
    return schedule


def model_for(cell: Cell, role: str) -> str:
    if cell["pairing"] not in ("cd", "dc") or role not in ROLES:
        raise ValueError("unknown registered roster or role")
    return CLAUDE if cell["pairing"][ROLES.index(role)] == "c" else DIRECT
