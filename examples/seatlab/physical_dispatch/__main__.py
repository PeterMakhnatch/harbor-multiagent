import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify the physical consequence of a peer filing, without starting a trial."
    )
    sub = parser.add_subparsers(required=True, dest="command")
    c = sub.add_parser(
        "certify",
        help="enumerate both hidden maps and every legal final dispatch at a natural focal draw",
    )
    c.add_argument(
        "--start",
        type=int,
        default=7,
        help="first public development seed to inspect; search at most 128",
    )
    m = sub.add_parser(
        "materialize",
        help="create a distinct native task outside the checkout; do not start it",
    )
    m.add_argument("out", type=Path)
    inspect = sub.add_parser(
        "check-task",
        help="check an existing materialized task against the pinned stock and overlay",
    )
    inspect.add_argument("task", type=Path)
    p = sub.add_parser(
        "prepare",
        help="write matched reference configs and native locks; do not run them",
    )
    p.add_argument("task", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--seed", required=True, type=int)
    receivers = sub.add_parser(
        "prepare-receivers",
        help="prepare four prospective receiver configs without sending requests",
    )
    receivers.add_argument("task", type=Path)
    receivers.add_argument("out", type=Path)
    receivers.add_argument("--seed", required=True, type=int)
    cells = ("original-other", "original-relevant", "swapped-other", "swapped-relevant")
    for cell in cells:
        receivers.add_argument(
            "--" + cell,
            dest=cell,
            required=True,
            metavar="SOCKET",
            help="an existing private one-turn receiver socket",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "certify":
            from .certificate import certify

            result = certify(args.start)
        elif args.command == "prepare-receivers":
            from .receiver_plan import prepare_receivers

            result = prepare_receivers(
                args.task,
                args.out,
                args.seed,
                {cell: getattr(args, cell) for cell in cells},
            )
        elif args.command == "prepare":
            from .preflight import prepare

            result = prepare(args.task, args.out, args.seed)
        elif args.command == "check-task":
            from .stage import check_stage

            result = {
                "schema": "seatlab.physical-dispatch.task-check/v1",
                "digest": check_stage(args.task),
                "executions": 0,
            }
        else:
            from .stage import materialize

            result = materialize(args.out)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"physical-dispatch: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
