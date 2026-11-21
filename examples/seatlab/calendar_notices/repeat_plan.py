import argparse

from ..receiver_panel import _print_json, _shard
from .team_views import JOIN_ARMS, JOIN_WORLDS, RepeatJoinCell


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assign the two ordered native teams of each calendar to one worker."
    )
    parser.add_argument("--repeat", type=int, required=True, choices=range(1, 33))
    parser.add_argument("--shard", type=_shard, default=(0, 1), metavar="INDEX/COUNT")
    args = parser.parse_args()
    index, count = args.shard
    if count > len(JOIN_WORLDS):
        parser.error("use at most one worker per registered world")
    for world in JOIN_WORLDS[index::count]:
        cells = [
            RepeatJoinCell(world, arm, repetition=args.repeat) for arm in JOIN_ARMS
        ]
        cells.sort(key=lambda cell: cell.arm != cell.first)
        _print_json(
            {
                "world": world,
                "repetition": args.repeat,
                "arms": [cell.arm for cell in cells],
                "runs": [cell.run for cell in cells],
            }
        )


if __name__ == "__main__":
    main()
