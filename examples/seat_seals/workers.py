import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ..native_evidence import Incomplete, Invalid
from .contract import load_registration
from .worker_layout import digest, load_plan, make_plan, write_record
from .worker_packets import capture, load_packet
from .worker_report import join
from .worker_run import run_next, status

Record = dict[str, Any]


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="python -m examples.seat_seals.workers")
    commands = cli.add_subparsers(dest="command", required=True)
    build = commands.add_parser("plan", help="assign whole pairs or cross their arms")
    build.add_argument("registration", type=Path)
    build.add_argument("out", type=Path)
    build.add_argument("--workers", type=int, required=True)
    build.add_argument("--layout", choices=("paired", "crossed"), default="paired")
    for name in ("status", "run-next", "pack"):
        cmd = commands.add_parser(name)
        cmd.add_argument("registration", type=Path)
        cmd.add_argument("plan", type=Path)
        cmd.add_argument("--expected-plan", metavar="DIGEST")
        cmd.add_argument(
            "--worker", type=int, required=True, help="zero-based worker index"
        )
        if name == "run-next":
            cmd.add_argument("task", type=Path)
            cmd.add_argument("--allow-named-seats", action="store_true")
            cmd.add_argument("--interval", type=float)
        if name == "pack":
            cmd.add_argument("out", type=Path)
            cmd.add_argument("roots", nargs="+", type=Path)
        else:
            cmd.add_argument("trials", type=Path)
    show = commands.add_parser(
        "show", help="compute bounds, even with no returned worker"
    )
    show.add_argument("plan", type=Path)
    show.add_argument("--expected-plan", metavar="DIGEST")
    show.add_argument("packets", type=Path, nargs="*")
    show.add_argument(
        "--checked", nargs=2, metavar=("PACKET", "DIGEST"), action="append", default=[]
    )
    show.add_argument("--json", action="store_true")
    return cli


def emit(data: Record) -> None:
    print(json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True))


def show(args: argparse.Namespace, plan: Record) -> int:
    if len(args.packets) + len(args.checked) > plan["workers"]:
        raise ValueError("more packets than planned workers")
    records = [load_packet(path, plan) for path in args.packets]
    records.extend(
        load_packet(Path(path), plan, expected) for path, expected in args.checked
    )
    report = join(plan, records)
    if args.json:
        emit(report)
    else:
        bounds = report["bounds"]
        low, high = bounds["net_sum"]
        n = bounds["denominator"]
        print(f"workers received\t{len(records)}/{plan['workers']}")
        print(f"known team rewards\t{bounds['known_arms']}/{2 * n}")
        print(f"pairs with both final rewards\t{bounds['paired_finals']}/{n}")
        print(
            f"pairs with both return-linked decisions\t{report['paired_interventions']}/{n}"
        )
        print(f"pairs with both schedules supported\t{report['paired_schedules']}/{n}")
        print(f"sum of seal minus continue rewards\t[{low}, {high}] across {n} pairs")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.command == "plan":
        plan = make_plan(
            load_registration(args.registration), args.workers, args.layout
        )
        write_record(args.out, plan)
        emit(
            {
                "plan_digest": digest(plan),
                "workers": args.workers,
                "pairs": len(plan["blocks"]),
            }
        )
        return 0
    plan = load_plan(args.plan, expected=args.expected_plan)
    if args.command == "show":
        return show(args, plan)
    if args.command == "pack":
        packet = capture(args.registration, plan, args.worker, args.roots)
        write_record(args.out, packet)
        emit(
            {
                "worker": args.worker,
                "plan_digest": digest(plan),
                "packet_digest": digest(packet),
            }
        )
        return 0
    if args.command == "status":
        emit(status(args.registration, plan, args.worker, args.trials))
        return 0
    assert args.command == "run-next"
    result = asyncio.run(
        run_next(
            args.registration,
            plan,
            args.worker,
            args.task,
            args.trials,
            allow_named_seats=args.allow_named_seats,
            interval=args.interval,
        )
    )
    if result is None:
        emit({"worker": args.worker, "available": 0})
        return 3
    emit(result)
    return 0 if result["native_runner_ok"] else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (ValueError, Invalid, Incomplete, OSError) as error:
        print(f"workers: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
