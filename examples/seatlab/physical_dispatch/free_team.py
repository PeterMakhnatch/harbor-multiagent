import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and inspect freely chosen handoffs in the native 3-role physical dispatch task. No command starts a team."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare",
        help="write one native config, its lock and independently pinnable receipt",
    )
    prepare.add_argument("task", type=Path)
    prepare.add_argument("out", type=Path)
    prepare.add_argument("--seed", required=True, type=int)
    prepare.add_argument("--map", choices=("original", "swapped"), required=True)
    turns = ("loader_request", "registrar", "depot", "loader_dispatch")
    for turn in turns:
        prepare.add_argument(
            "--" + turn.replace("_", "-"),
            type=Path,
            required=True,
            metavar="ABSENT_SOCKET",
        )
    audit = sub.add_parser(
        "inspect",
        help="read one original native trial using a separately retained receipt digest",
    )
    audit.add_argument("plan", type=Path)
    audit.add_argument("trial", type=Path)
    audit.add_argument("--expect", required=True, metavar="RECEIPT_DIGEST")
    witness = sub.add_parser(
        "certify-freshness",
        help="grade every dispatch in three matched filing controls, without model seats",
    )
    witness.add_argument("--start", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        if args.command == "certify-freshness":
            from .freshness import certify_freshness

            output = certify_freshness(args.start)
        elif args.command == "prepare":
            from ...native_study import record_bytes
            from ..exhibit_enroll import digest
            from .team_plan import prepare_team

            receipt = prepare_team(
                args.task,
                args.out,
                args.seed,
                args.map,
                {turn: getattr(args, turn) for turn in turns},
            )
            output = {
                "status": "prepared_not_run",
                "receipt_digest": digest(record_bytes(receipt)),
                "executions": receipt["executions"],
                "task": receipt["task_name"],
            }
        else:
            from .team_audit import inspect_team

            output = inspect_team(args.plan, args.trial, expect=args.expect)
    except (
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        OSError,
        RuntimeError,
        RecursionError,
    ) as error:
        if args.command == "inspect":
            from .cohort_scores import unknown

            output = {**unknown(error), "provider_authenticated": False}
            print(json.dumps(output, sort_keys=True, allow_nan=False))
        else:
            print(f"free-team: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
