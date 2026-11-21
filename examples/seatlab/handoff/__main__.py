"""Run a portable two-agent checkpoint handoff, or score an existing first attempt."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..visibility import clarify_run as files
from ..visibility.route_run import Answer
from . import packet


def _print(value: object) -> None:
    print(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False),
        flush=True,
    )


def _load(args: argparse.Namespace) -> packet.Packet:
    if args.packet is None:
        from .dispatch import packet_bytes

        return packet.parse(packet_bytes(), args.sha256)
    if args.sha256 is None:
        raise ValueError("custom packets need an expected SHA-256")
    with args.packet.open("rb") as source:
        raw = source.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("oversized packet")
    return packet.parse(raw, args.sha256)


def _ledger(directory: Path, subject: packet.Packet) -> dict[str, Any]:
    from ..visibility import spend

    return spend.report(directory, run_prefix="ho-" + subject.sha256[:12] + "-")


def _occupied(
    study: Path, blocks: tuple[packet.Block, ...], ledger: dict[str, Any] | None
) -> set[str]:
    keys = {b.key for b in blocks}
    with files._directory(files._study_path(study)) as fd:
        files._private(fd, mode=0o700)
        with os.scandir(fd) as entries:
            occupied = set()
            for entry in entries:
                if entry.name not in keys or not entry.is_dir(follow_symlinks=False):
                    raise ValueError("unregistered study entry")
                occupied.add(entry.name)
    if ledger is not None:
        stages = (
            "note",
            "bound-select",
            "anonymous-select",
            "bound-decide",
            "anonymous-decide",
            "both-decide",
        )
        attempts = {f"{key}-{stage}": key for key in keys for stage in stages}
        for row in ledger["calls"]:
            occupied.add(attempts[row["run"]])
    return occupied


def _meter(rows: list[dict[str, Any]], ledger: dict[str, Any]) -> dict[str, Any]:
    from ..visibility import route_audit

    bills = {(r["run"], r["role"]): r for r in ledger["calls"]}
    matches: Counter[str] = Counter()
    for row in rows:
        for raw in row["turns"]:
            evidence = route_audit._Turn(raw["status"], usage=raw["usage"])
            key = raw["run"], raw["role"]
            matches[route_audit._meter(evidence, bills.pop(key, None), True)] += 1
    return {
        "by_match": dict(matches),
        "ledger_without_turn": len(bills),
        **{
            k: ledger[k]
            for k in ("unsettled_calls", "known_usd_low", "upper_usd_including_holds")
        },
    }


def _transport(args: argparse.Namespace) -> Callable[[str, str, str], Answer]:
    if args.mode == "direct":
        from ..visibility.route_cli import _admit, _direct

        _admit()
        return _direct
    if args.mode == "commands":
        from ..visibility.seat_command import load_commands

        author, successor = load_commands(args.config)
        return lambda prompt, run, role: Answer(
            (author if role == "route_review" else successor)(prompt)
        )

    def demo(prompt: str, run: str, _role: str) -> Answer:
        # Explicit non-model fixture. It does not solve the task.
        data = json.loads(prompt.split("\n\nData:\n", 1)[1])
        if run.endswith("-note"):
            return Answer('{"note":"Read the current record before choosing."}')
        ids = [q["id"] for q in data["questions"]]
        if run.endswith("-select"):
            return Answer(json.dumps({"read": ids[0]}))
        return Answer(json.dumps({"choices": dict.fromkeys(ids, data["options"][0])}))

    return demo


def _run(args: argparse.Namespace) -> None:
    if args.mode == "packet":
        from .dispatch import packet_bytes

        sys.stdout.buffer.write(packet_bytes())
        return
    if args.mode == "stock":
        from .dispatch import stock

        with args.report.open("rb") as source:
            raw = source.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("oversized report")
        report = files._object(raw)
        if (
            report.get("schema") != "seatlab.handoff-report/v1"
            or not isinstance(report.get("primary"), dict)
            or not isinstance(report.get("packet_sha256"), str)
        ):
            raise ValueError("unbound stock comparison")
        check = stock(report)
        _print(check)
        if check.get("mismatches") != 0:
            raise ValueError("stock mismatch")
        return
    subject = _load(args)
    blocks = packet.grid(subject)
    if args.mode == "plan":
        _print(
            {
                "schema": "seatlab.handoff-plan/v1",
                "packet_sha256": subject.sha256,
                "max_calls_per_block": 5,
                "blocks": [
                    {
                        "index": i,
                        "block": b.key,
                        "cluster": b.case.cluster,
                        "case": b.case.id,
                        "rep": b.rep,
                    }
                    for i, b in enumerate(blocks)
                ],
            }
        )
        return
    directory = getattr(args, "spend_dir", None)
    if args.mode == "direct":
        directory = Path(os.environ.get("SEATLAB_SPEND_DIR", ""))
        if not directory.is_absolute():
            raise ValueError(
                "direct mode requires the original absolute spend directory"
            )
    costs = _ledger(directory, subject) if directory is not None else None
    if args.mode == "report":
        from . import analysis, journal

        rows = journal.collect(args.study, blocks)
        result = {
            "schema": "seatlab.handoff-report/v1",
            "packet_sha256": subject.sha256,
            "outcomes": rows,
            **analysis.summarize(blocks, rows),
        }
        if costs is not None:
            result["meter"] = _meter(rows, costs)
        _print(result)
        return
    occupied = _occupied(args.study, blocks, costs)
    if args.mode == "pending":
        _print(
            {
                "packet_sha256": subject.sha256,
                "occupied": len(occupied),
                "unclaimed": [i for i, b in enumerate(blocks) if b.key not in occupied],
            }
        )
        return
    stop = args.start + 1 if args.stop is None else args.stop
    if not 0 <= args.start < stop <= len(blocks) or any(
        b.key in occupied for b in blocks[args.start : stop]
    ):
        raise ValueError("invalid or occupied range")
    from . import journal

    respond = _transport(args)
    for i in range(args.start, stop):
        block = blocks[i]
        outcome = journal.play(args.study, block, respond)
        _print(
            {
                "index": i,
                "block": block.key,
                "note_status": outcome["note_status"],
                "arms": {k: v["status"] for k, v in outcome["arms"].items()},
            }
        )
        if outcome["note_status"] == "missing" or any(
            arm["status"] in {"missing_select", "missing_decide"}
            for arm in outcome["arms"].values()
        ):
            raise ValueError("transport stopped; first attempts are preserved")


def main(argv: Sequence[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = cli.add_subparsers(dest="mode", required=True)
    for mode in (
        "packet",
        "plan",
        "pending",
        "demo",
        "commands",
        "direct",
        "report",
        "stock",
    ):
        sub = modes.add_parser(mode, allow_abbrev=False)
        if mode == "packet":
            continue
        if mode == "stock":
            sub.add_argument(
                "report",
                type=Path,
                help="Public report for the registered Dispatch worlds.",
            )
            continue
        if mode == "direct":
            sub.set_defaults(packet=None, sha256=None)
        else:
            sub.add_argument(
                "--packet",
                type=Path,
                help="Your committed handoff JSON. Default: included Dispatch worlds.",
            )
            sub.add_argument(
                "--sha256",
                help="Expected digest of the exact packet bytes; required for a custom packet.",
            )
        if mode != "plan":
            sub.add_argument(
                "--study",
                type=Path,
                required=True,
                help="Private physical directory outside the checkout.",
            )
        if mode in ("report", "pending"):
            sub.add_argument(
                "--spend-dir",
                type=Path,
                help="Original ledger; only for the included direct provider.",
            )
        if mode in ("demo", "commands", "direct"):
            sub.add_argument(
                "--start", type=int, default=0, help="First included block index."
            )
            sub.add_argument(
                "--stop",
                type=int,
                help="First excluded block index. Default: start + 1.",
            )
        if mode == "commands":
            sub.add_argument(
                "--config",
                required=True,
                type=Path,
                help="Trusted reviewer (writer) and decider (successor) commands.",
            )
    args = cli.parse_args(argv)
    try:
        _run(args)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, sqlite3.Error):
        print(
            "Cannot verify the packet, study, original receipt, or requested first attempt. Do not rerun an attempted block.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
