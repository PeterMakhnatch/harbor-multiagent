"""Run two seat harnesses on recorded Crucible evidence, or verify their decisions offline."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import (
    route_audit,
    route_memory,
    route_packet,
    route_protocol as protocol,
    route_run,
)

_RESULTS = Path(__file__).resolve().parent.parent / "results"
_PUBLIC = _RESULTS / "crucible-routed-packet.json"
_COMMITMENT = _RESULTS / "crucible-routed-packet.sha256"


def _direct(prompt: str, run: str, role: str) -> route_run.Answer:
    from . import deepseek

    received = deepseek.turn(prompt, run_id=run, role=role)
    return route_run.Answer(
        received.text,
        received.served_model,
        received.input_tokens,
        received.cache_tokens,
        received.output_tokens,
    )


def _admit() -> None:
    from . import deepseek, spend

    deepseek.bind_host_key()
    spend.bind_host_spend_dir()
    deepseek.preflight()


def _print(value: object) -> None:
    print(
        json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False),
        flush=True,
    )


def _scope(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--packet",
        type=Path,
        default=_PUBLIC,
        help="Published packet, or a private copy with --sha256.",
    )
    parser.add_argument(
        "--sha256",
        help="Expected packet digest. The default is the published commitment.",
    )


def _identity(args: argparse.Namespace) -> tuple[Path, str]:
    if isinstance(args.sha256, str) and isinstance(args.packet, Path):
        return args.packet, args.sha256
    if args.packet != _PUBLIC:
        raise ValueError("supply the expected digest for another packet")
    return _PUBLIC, _COMMITMENT.read_text(encoding="ascii").strip()


def _execute(args: argparse.Namespace) -> int:
    packet, digest = _identity(args)
    delegated = args.workflow == "delegate"
    if delegated:
        from . import route_memory_audit, route_memory_run

        collect = route_memory_audit.collect
        play_external = route_memory_run.play_block
        play_direct = route_memory_run._play
        blocks = route_memory.blocks()
    else:
        collect = route_audit.collect
        play_external = route_run.play_block
        play_direct = route_run._play
        blocks = protocol.blocks()
    if args.mode == "report":
        _print(collect(packet, digest, args.study, spend_dir=args.spend_dir))
        return 0
    # The run path validates the packet inside each first-attempt claim.
    if args.mode == "plan":
        cases = route_packet.load(packet, digest)
        if delegated:
            _print(
                {
                    "packet_sha256": digest,
                    "source_rounds": len(cases),
                    "target_rounds": len({(b.policy, b.round) for b in blocks}),
                    "eligible_blocks": sum(
                        route_memory.menu(cases, b) is not None for b in blocks
                    ),
                    "blocks": [
                        {
                            "index": i,
                            "block": route_memory.key(b),
                            "selectors": route_memory.arm_order(b),
                        }
                        for i, b in enumerate(blocks)
                    ],
                    "max_calls": 6 * len(blocks),
                }
            )
            return 0
        _print(
            {
                "packet_sha256": digest,
                "source_rounds": len(cases),
                "eligible_rounds": sum(c.reports is not None for c in cases),
                "blocks": [
                    {"index": i, "block": b.id, "lenses": protocol.lenses(b)}
                    for i, b in enumerate(blocks)
                ],
                "max_calls": 6 * len(blocks),
            }
        )
        return 0
    if not 0 <= args.start < len(blocks):
        raise ValueError("unregistered start index")
    stop = args.start + 1 if args.stop is None else args.stop
    if not args.start < stop <= len(blocks):
        raise ValueError("unregistered stop index")
    seats: tuple[Callable[[str], str], Callable[[str], str]] | None = None
    if args.mode == "demo":

        def reviewer(_: str) -> str:
            return '{"vote":"B","a":[0,0],"b":[0,0]}'

        def decider(prompt: str) -> str:
            if delegated and '"ask"' in prompt:
                return '{"ask":"X"}'
            return '{"choice":"A"}'

        seats = (reviewer, decider)
    elif args.mode == "commands":
        from .seat_command import load_commands

        seats = load_commands(args.config)
    else:
        if args.mode != "direct":
            raise ValueError("unregistered transport")
        _admit()
    for i in range(args.start, stop):
        block = blocks[i]
        if seats is not None:
            status = play_external(
                packet, digest, args.study, block, reviewer=seats[0], decider=seats[1]
            )
        else:
            status = play_direct(packet, digest, args.study, block, _direct)
        key = route_memory.key(block) if delegated else block.id
        _print({"index": i, "block": key, "mode": args.mode, "status": status})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "report", "demo", "commands", "direct"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        _scope(sub)
        sub.add_argument(
            "--workflow",
            choices=("review", "delegate"),
            default="review",
            help="Compare two context views, or let the coordinator choose a reviewer.",
        )
        if mode != "plan":
            sub.add_argument(
                "--study",
                type=Path,
                required=True,
                help="An owner-only directory outside the checkout.",
            )
        if mode == "report":
            sub.add_argument(
                "--spend-dir",
                type=Path,
                help="Optional original direct ledger. Not used for external seat costs.",
            )
        if mode in ("demo", "commands", "direct"):
            sub.add_argument(
                "--start",
                type=int,
                default=0,
                help="First registered block (included).",
            )
            sub.add_argument(
                "--stop",
                type=int,
                help="Exclusive block index. The default runs one block.",
            )
        if mode == "commands":
            sub.add_argument(
                "--config",
                type=Path,
                required=True,
                help="Trusted reviewer and decider commands.",
            )
    try:
        return _execute(parser.parse_args(argv))
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        # External programs, providers, and paths can carry private content.
        print(
            "Cannot verify inputs or finish this first attempt. Check the packet, private study and selected transport. Do not retry an attempted block.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
