"""Run two seat harnesses on recorded Crucible evidence, or verify their decisions offline."""

from __future__ import annotations

import argparse
import json
import os
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
from .clarify_run import StudyError

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


def _continuation_spend_dir(mode: str) -> Path | None:
    if mode != "direct":
        return None
    path = Path(os.environ.get("SEATLAB_SPEND_DIR", ""))
    if not path.is_absolute():
        raise ValueError("direct continuation requires an absolute SEATLAB_SPEND_DIR")
    return path


def _seats(
    args: argparse.Namespace,
) -> tuple[Callable[[str], str], Callable[[str], str]] | None:
    if args.mode == "demo":

        def reviewer(_: str) -> str:
            return '{"vote":"B","a":[0,0],"b":[0,0]}'

        def decider(prompt: str) -> str:
            if args.workflow == "delegate" and '"ask"' in prompt:
                return '{"ask":"X"}'
            return '{"choice":"A"}'

        return reviewer, decider
    if args.mode == "commands":
        from .seat_command import load_commands

        if args.config is None:
            raise ValueError("ready command blocks require --config")
        return load_commands(args.config)
    if args.mode != "direct":
        raise ValueError("unregistered transport")
    _admit()
    return None


def _run(
    args: argparse.Namespace,
    packet: Path,
    digest: str,
    blocks: tuple[protocol.Block, ...],
) -> int:
    from . import route_memory_run, route_pending

    delegated = args.workflow == "delegate"
    runner = route_memory_run if delegated else route_run
    if not 0 <= args.start < len(blocks):
        raise ValueError("unregistered start index")
    stop = args.start + 1 if args.stop is None else args.stop
    if not args.start < stop <= len(blocks):
        raise ValueError("unregistered stop index")
    indices = range(args.start, stop)
    spend_dir = None
    ready = None
    if args.only_unclaimed:
        spend_dir = _continuation_spend_dir(args.mode)
        ready = set(
            route_pending.snapshot(
                packet,
                digest,
                args.study,
                workflow=args.workflow,
                spend_dir=spend_dir,
            )["ready"]
        )
    seats = _seats(args) if ready is None or not ready.isdisjoint(indices) else None
    for i in indices:
        block = blocks[i]
        if ready is not None and i not in ready:
            status = "skipped_not_ready"
        else:
            try:
                if seats is None:
                    status = runner._play(packet, digest, args.study, block, _direct)
                else:
                    status = runner.play_block(
                        packet,
                        digest,
                        args.study,
                        block,
                        reviewer=seats[0],
                        decider=seats[1],
                    )
            except StudyError as error:
                if (
                    ready is None
                    or type(error) is not StudyError
                    or error.args != ("block_already_claimed",)
                ):
                    raise
                latest = route_pending.snapshot(
                    packet,
                    digest,
                    args.study,
                    workflow=args.workflow,
                    spend_dir=spend_dir,
                )
                if i in latest["ready"]:
                    raise
                ready.intersection_update(latest["ready"])
                status = "skipped_already_claimed"
        key = route_memory.key(block) if delegated else block.id
        _print({"index": i, "block": key, "mode": args.mode, "status": status})
    return 0


def _execute(args: argparse.Namespace) -> int:
    packet, digest = _identity(args)
    if args.mode == "pending":
        from . import route_pending

        _print(
            route_pending.snapshot(
                packet,
                digest,
                args.study,
                workflow=args.workflow,
                spend_dir=args.spend_dir,
            )
        )
        return 0
    delegated = args.workflow == "delegate"
    if delegated:
        from . import route_memory_audit

        collect = route_memory_audit.collect
        blocks = route_memory.blocks()
    else:
        collect = route_audit.collect
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
    return _run(args, packet, digest, blocks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "report", "pending", "demo", "commands", "direct"):
        sub = modes.add_parser(mode, allow_abbrev=False)
        _scope(sub)
        sub.add_argument(
            "--workflow",
            choices=("review", "delegate"),
            default="review",
            required=mode == "pending",
            help="Compare two context views, or let the coordinator choose a reviewer.",
        )
        if mode != "plan":
            sub.add_argument(
                "--study",
                type=Path,
                required=True,
                help="An owner-only directory outside the checkout.",
            )
        if mode in ("report", "pending"):
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
            sub.add_argument(
                "--only-unclaimed",
                action="store_true",
                help=(
                    "Skip blocks that are not ready. For direct mode, set "
                    "SEATLAB_SPEND_DIR to the absolute path of the original ledger directory."
                ),
            )
        if mode == "commands":
            sub.add_argument(
                "--config",
                type=Path,
                help="Trusted reviewer and decider commands. Required for ready blocks.",
            )
    args = parser.parse_args(argv)
    if args.mode == "commands" and args.config is None and not args.only_unclaimed:
        modes.choices["commands"].error("the following arguments are required: --config")
    try:
        return _execute(args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        # External programs, providers, and paths can carry private content.
        print(
            "Cannot verify inputs or finish this first attempt. Check the packet, private study and selected transport. Do not retry an attempted block.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
