import argparse
import json
import sys
from pathlib import Path

from .world import CHANNELS, MAPS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or qualify the three-role warehouse. No command starts a trial.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    certify = commands.add_parser(
        "certify",
        help="grade every native dispatch on four private maps under four delivery conditions",
    )
    certify.add_argument("--start", type=int, default=7)
    export = commands.add_parser(
        "export-worlds",
        help="write private native inputs and score tables for independent author checks",
    )
    export.add_argument("out", type=Path)
    export.add_argument("--start", type=int, default=7)
    stage = commands.add_parser(
        "materialize",
        help="write a pinned native task outside the checkout without replacing files",
    )
    stage.add_argument("out", type=Path)
    config = commands.add_parser(
        "prepare-team", help="prepare three free roles and four prospective turns"
    )
    receiver = commands.add_parser(
        "prepare-receiver",
        help="freeze the nomination and both filings; prepare one final owner turn",
    )
    for command in (config, receiver):
        command.add_argument("task", type=Path)
        command.add_argument("out", type=Path)
        command.add_argument("--seed", type=int, required=True)
        command.add_argument("--registry-map", choices=MAPS, required=True)
        command.add_argument("--bay-map", choices=MAPS, required=True)
        command.add_argument("--channels", choices=CHANNELS, required=True)
    receiver.add_argument(
        "--loader-dispatch", type=Path, required=True, metavar="ABSENT_SOCKET"
    )
    turns = ("loader_request", "registrar", "depot", "loader_dispatch")
    for turn in turns:
        config.add_argument(
            "--" + turn.replace("_", "-"),
            type=Path,
            required=True,
            metavar="ABSENT_SOCKET",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "certify":
            from .certificate import certify as certificate

            outcome = certificate(args.start)
        elif args.command == "export-worlds":
            from .export import export as world_files

            outcome = world_files(args.out, args.start)
        elif args.command == "materialize":
            from .stage import materialize

            outcome = materialize(args.out)
        else:
            from ...native_study import record_bytes
            from ..exhibit_enroll import digest
            from .prepare import prepare, prepare_receiver

            cell = (
                args.task,
                args.out,
                args.seed,
                args.registry_map,
                args.bay_map,
                args.channels,
            )
            if args.command == "prepare-receiver":
                receipt = prepare_receiver(*cell, args.loader_dispatch)
            else:
                receipt = prepare(*cell, {turn: getattr(args, turn) for turn in turns})
            outcome = {
                "receipt_digest": digest(record_bytes(receipt)),
                "executions": receipt["executions"],
                "task": receipt["task_name"],
                "design": receipt["design"],
                "status": receipt["status"],
            }
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        IndexError,
        AttributeError,
        RecursionError,
    ) as error:
        print(f"joint-handoffs: {error}", file=sys.stderr)
        return 2
    print(json.dumps(outcome, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
