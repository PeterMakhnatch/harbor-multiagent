import argparse
import json
import sys

from .control import choose


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Scripted check: reply to exactly one role observation on standard input."
    )
    p.add_argument("--convention", choices=("forward", "reverse"), required=True)
    p.add_argument("--role", choices=("east", "west"), required=True)
    args = p.parse_args(argv)
    try:
        packet = sys.stdin.buffer.read(4097)
        if len(packet) > 4096:
            raise ValueError("oversized role observation")
        slot = choose(1 if args.convention == "forward" else -1, args.role, packet)
    except (OSError, ValueError, RecursionError):
        print("partner-switch: invalid isolated role observation", file=sys.stderr)
        return 2
    print(json.dumps({"slot": slot}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
