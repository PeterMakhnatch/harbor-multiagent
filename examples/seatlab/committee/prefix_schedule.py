"""Branch the first masked native recommendation history on each option map."""

from pathlib import Path

from . import schedule

PACKET = (
    Path(__file__).resolve().parents[1] / "results" / "committee-prefix-packet.json"
)
PACKET_SHA256 = "481bf1356bf1b3653c098708b8194081bdf5b962ac970d752a354afb1fb5ba08"


def grid() -> tuple[schedule.Cell, ...]:
    return tuple(
        {
            "cell_id": f"pf-m{cell['map_index']}-r{cell['repetition']}-{cell['arm']}",
            "map_index": cell["map_index"],
            "repetition": cell["repetition"],
            "arm": cell["arm"],
        }
        for cell in schedule.recommendation_grid()
    )
