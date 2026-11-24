import argparse
import json
import sys
from contextlib import ExitStack

from ..native_tickets import survey, take
from .plan import SCHEMA

EXIT_FAILURE = 2
EXIT_NONE_LEFT = 3


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python3 -BS -m examples.shift_board.worklist")
    root.add_argument("manifest")
    root.add_argument("--shard", required=True)
    root.add_argument("--out", required=True)
    root.add_argument("--take", action="store_true")
    return root


def run(args: argparse.Namespace) -> int:
    with ExitStack() as stack:
        found = survey(
            stack, args.manifest, args.shard, args.out, expected_schema=SCHEMA
        )
        if not args.take:
            for ticket in found.unclaimed():
                print(found.workspace / "configs" / f"{ticket}.json")
            print(json.dumps(found.counts(), sort_keys=True), file=sys.stderr)
            return 0
        config = take(stack, found)
        if config is None:
            print(f"no unclaimed ticket in shard {found.shard}", file=sys.stderr)
            print(json.dumps(found.counts(), sort_keys=True), file=sys.stderr)
            return EXIT_NONE_LEFT
        print(config)
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
    except OSError as error:
        print(f"error: filesystem: {error.strerror or 'failed'}", file=sys.stderr)
    return EXIT_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
