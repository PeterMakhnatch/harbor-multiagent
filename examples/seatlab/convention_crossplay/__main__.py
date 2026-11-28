import argparse
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..coupled_retention import strategies
from . import loader, matrix, transfer


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare original and mixed partners on fixed closed decisions."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    enroll = commands.add_parser("enroll")
    enroll.add_argument(
        "--team",
        action="append",
        nargs=3,
        metavar=("NAME", "EAST", "WEST"),
        required=True,
    )
    enroll.add_argument("--out", type=Path, required=True)
    for command in ("native", "score"):
        p = commands.add_parser(command)
        if command == "score":
            p.add_argument("--roster", required=True)
            p.add_argument("--roster-sha256", required=True)
            p.add_argument("--require-equal-home", action="store_true")
            source = p.add_mutually_exclusive_group(required=True)
            source.add_argument("--start", type=int)
            source.add_argument("--game")
        else:
            p.add_argument("--start", type=int, required=True)
            p.add_argument("--export", type=Path)
        p.add_argument(
            "--sender", choices=("truthful", "pass"), required=command == "native"
        )
        p.add_argument(
            "--arm",
            choices=("together", "apart", "independent"),
            required=command == "native",
        )
        p.add_argument("--expect-gap", choices=("positive", "zero", "negative"))
    return parser.parse_args(argv)


def command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "enroll":
        return 0, {
            "schema": "seatlab.crossplay.enrollment/v1",
            **transfer.export(args.out, transfer.inputs(args.team)),
        }
    meta: dict[str, Any] = {}
    if args.command == "native":
        from . import orbit

        game, teams, meta = orbit.native_roster(args.start, args.sender, args.arm)
        meta.update(
            {
                "source": "scripted_native_symmetries",
                "start": args.start,
                "sender": args.sender,
                "arm": args.arm,
            }
        )
    else:
        game = strategies.game(args)
        teams = loader.load(args.roster, args.roster_sha256)
        meta = {"source": "submitted_frozen_rules", "roster_sha256": args.roster_sha256}
    result = matrix.evaluate(game, teams)
    if args.command == "native":
        if (
            not result["home_values_equal"]
            or result["home_mean"] != meta["decentralized"]
        ):
            raise ValueError("label permutations did not preserve optimal home reward")
        if args.export is not None:
            meta["export"] = transfer.export(
                args.export, transfer.native_inputs(game, teams)
            )
    gap = Fraction(result["home_advantage"])
    sign = "positive" if gap > 0 else "negative" if gap < 0 else "zero"
    status = int(args.expect_gap is not None and args.expect_gap != sign)
    if (
        args.command == "score"
        and args.require_equal_home
        and not result["home_values_equal"]
    ):
        status = 1
    return status, {
        "schema": "seatlab.convention-crossplay/v1",
        "closed_role_replay": result,
        "design": meta,
        "model_calls": 0,
        "trials": 0,
        "scope": "Fixed local rules on the same exogenous inputs. Not a live partnership, training run or stochastic-model estimate.",
    }


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        status, result = command(args)
    except (ValueError, OSError, OverflowError, RecursionError):
        print("crossplay: invalid game, roster, convention or output", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
