import argparse
import json
import sys
from pathlib import Path

from .control import run_controls
from .manifest import load, seal
from .report import assess
from .session import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pin a receiver study and serve one opaque ticket per process. No models or trials are launched.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser(
        "plan", help="prepare the private assignment before the first decision"
    )
    create.add_argument("root", type=Path)
    create.add_argument("--start", type=int, required=True)
    create.add_argument("--sender", choices=("truthful", "repeat"), required=True)
    create.add_argument(
        "--source", choices=("scripted_control", "external_unverified"), required=True
    )
    for name in ("tickets", "serve", "assess", "control"):
        command = sub.add_parser(name)
        command.add_argument("root", type=Path)
        command.add_argument("--expect", required=True, metavar="PLAN_SHA256")
        if name == "serve":
            command.add_argument("ticket")
        elif name == "control":
            group = command.add_mutually_exclusive_group(required=True)
            group.add_argument(
                "--ticket",
                action="append",
                help="select one unused ticket; repeat to select several",
            )
            group.add_argument(
                "--all", action="store_true", help="require every ticket to be unused"
            )
            command.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args(argv)
    try:
        if args.action == "serve":
            serve(args.root, args.expect, args.ticket)
            return 0
        if args.action == "plan":
            value = seal(args.root, args.start, args.sender, args.source)
        elif args.action == "tickets":
            plan = load(args.root, args.expect)
            value = {"tickets": [row["ticket"] for row in plan["assignments"]]}
        elif args.action == "control":
            value = run_controls(args.root, args.expect, args.ticket, args.workers)
        else:
            value = assess(args.root, args.expect)
    except (BrokenPipeError, KeyboardInterrupt):
        print(
            "receiver-study: connection interrupted; inspect the attempt state",
            file=sys.stderr,
        )
        return 2
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        IndexError,
        AssertionError,
        RecursionError,
    ):
        print(
            "receiver-study: invalid input, unavailable ticket, or inconsistent private evidence",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
