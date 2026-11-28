import argparse
import json
import sys
from fractions import Fraction
from typing import Any

from . import author, bounds


def arguments(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Check whether identical seat-level memory can yield different team decisions."
    )
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("qualify", "packet", "grade"):
        sub = commands.add_parser(name)
        sub.add_argument("--start", type=int, required=True)
        sub.add_argument("--sender", choices=("truthful", "pass"), required=True)
        if name == "qualify":
            sub.add_argument("--expect-team-gain", choices=("yes", "no"))
        else:
            sub.add_argument("--case", type=int, choices=range(6), required=True)
            if name == "packet":
                sub.add_argument("--role", choices=("east", "west"), required=True)
                sub.add_argument(
                    "--note", choices=("retained", "omitted"), required=True
                )
            else:
                sub.add_argument("--east-vote", required=True)
                sub.add_argument("--west-vote", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("file")
    inspect.add_argument("--expect-pooling-gap", choices=("yes", "no"))
    return p.parse_args(argv)


def _met(expected: str | None, value: bool) -> int:
    return int(expected is not None and (expected == "yes") != value)


def command(args: argparse.Namespace) -> tuple[int, bytes]:
    report: dict[str, Any]
    status = 0
    if args.command == "inspect":
        report = bounds.evaluate(author.census(args.file))
        status = _met(
            args.expect_pooling_gap,
            Fraction(report["pooled"]) > Fraction(report["decentralized"]),
        )
        report = {
            "schema": "seatlab.two-seat-game.values/v1",
            "values": report,
            "scope": "Exact values on supplied inputs. No authenticated role observations or native scores.",
        }
    else:
        from . import native

        if args.command == "qualify":
            from . import panel

            cases = native.native_cases(args.start, args.sender)
            report = panel.qualify(cases)
            gap = Fraction(report["team_gap"])
            if args.expect_team_gain is not None:
                matched = (
                    report["equal_role_marginals"] and report["equal_role_accuracy"]
                )
                status = int(
                    not matched
                    or not (gap > 0 if args.expect_team_gain == "yes" else gap == 0)
                )
            report.update(
                {
                    "schema": "seatlab.coupled-retention.report/v1",
                    "sender": args.sender,
                    "start": args.start,
                    "development_seeds": [case.seed for case in cases],
                    "native_pair_grades": sum(len(case.actions) ** 2 for case in cases),
                    "model_calls": 0,
                    "trials": 0,
                    "scope": "Finite native grades for closed simultaneous votes, not model performance or live Harbor trials.",
                }
            )
        else:
            world, east_packets, west_packets, actions = native.prepare_case(
                args.start, args.sender, args.case
            )
            if args.command == "packet":
                pair = east_packets if args.role == "east" else west_packets
                return 0, pair[int(args.note == "retained")]
            if args.east_vote == args.west_vote == "-":
                raise ValueError("use separate vote inputs")
            east, west = (
                actions[author.vote(path, actions)]
                for path in (args.east_vote, args.west_vote)
            )
            report = {
                "schema": "seatlab.coupled-retention.grade/v1",
                "agree": east == west,
                "reward": native.grade_pair(world, east, west),
                "source": "submitted_votes_unverified",
            }
    return status, json.dumps(
        report, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        code, payload = command(args)
    except (ValueError, OSError, OverflowError, RecursionError):
        print(
            "coupled-retention: invalid input or unsupported finite game",
            file=sys.stderr,
        )
        return 2
    sys.stdout.buffer.write(payload + b"\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
