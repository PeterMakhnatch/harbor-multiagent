import argparse
import hashlib
import json
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..decision_boundary.inputs import read_input
from . import audit, author, bounds, strategy
from .records import Cell

Game = tuple[tuple[str, ...], tuple[str, ...], list[Cell]]
ROLES = ("east", "west")


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synthesize or score a pair of independent scripted controls."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("synthesize", "score"):
        sub = commands.add_parser(name)
        source = sub.add_mutually_exclusive_group(required=True)
        source.add_argument("--game")
        source.add_argument("--start", type=int)
        sub.add_argument("--sender", choices=("truthful", "pass"))
        sub.add_argument("--arm", choices=("together", "apart", "independent"))
        if name == "synthesize":
            sub.add_argument("--out", required=True, type=Path)
        else:
            sub.add_argument("--east", required=True)
            sub.add_argument("--west", required=True)
            sub.add_argument("--expect-value")
    return parser.parse_args(argv)


def game(args: argparse.Namespace) -> Game:
    if args.game is not None:
        if args.sender is not None or args.arm is not None:
            raise ValueError("native conditions cannot specify an authored game")
        return author.load_game(args.game)
    if args.sender is None or args.arm is None:
        raise ValueError("both native conditions are required")
    from . import native, panel

    cases = native.native_cases(args.start, args.sender)
    actions = cases[0].actions
    return actions, actions, panel.schedules(cases)[args.arm]


def tables(out: Path, east: bytes, west: bytes) -> dict[str, str]:
    from ...seat_checkpoints.materialize import sync_directory
    from ...ship_inflight.materialize import publish
    from ..exhibit_enroll import target, write_private

    out = target(out)
    with tempfile.TemporaryDirectory(prefix=".seatlab-rule-", dir=out.parent) as stage:
        root = Path(stage) / "rules"
        root.mkdir(mode=0o700)
        digests = {}
        for role, wire in zip(ROLES, (east, west), strict=True):
            write_private(root / f"{role}.json", wire)
            digests[role] = "sha256:" + hashlib.sha256(wire).hexdigest()
        sync_directory(root)
        publish(root, out)
        sync_directory(out.parent)
    return digests


def synthesize(source: Game, out: Path) -> dict[str, Any]:
    east, west, rows = source
    values, left, right = bounds.optimize(rows)
    e = strategy.table("east", {packet: east[index] for packet, index in left.items()})
    w = strategy.table("west", {packet: west[index] for packet, index in right.items()})
    score = audit.score(
        rows, east, west, strategy.load(e, "east"), strategy.load(w, "west")
    )
    if score["reward"] != values["decentralized"]:
        raise ValueError("separate strategies do not attain the claimed value")
    return {
        "schema": "seatlab.two-seat-strategies.synthesized/v1",
        "values": values,
        "score": score,
        "table_digests": tables(out, e, w),
        "scope": "Two fixed, role-only scripted rules on these finite inputs. Not model behavior.",
    }


def command(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "score" and (args.game, args.east, args.west).count("-") > 1:
        raise ValueError("supply distinct input streams")
    source = game(args)
    if args.command == "synthesize":
        return 0, synthesize(source, args.out)
    east, west, rows = source
    submitted = [
        strategy.load(read_input(path), role)
        for role, path in zip(ROLES, (args.east, args.west), strict=True)
    ]
    result = audit.score(rows, east, west, submitted[0], submitted[1])
    status = 0
    if args.expect_value is not None:
        if not 0 < len(args.expect_value) <= 64:
            raise ValueError("invalid expected reward")
        expect = Fraction(args.expect_value)
        if not 0 <= expect <= 1:
            raise ValueError("expected reward outside unit interval")
        status = int(Fraction(str(result["reward"])) != expect)
    return status, {
        "schema": "seatlab.two-seat-strategies.score/v1",
        "score": result,
        "scope": "Score of submitted role-only rules. No search, optimality claim, or participant authentication.",
    }


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    try:
        code, result = command(args)
    except (ValueError, OSError, OverflowError, ZeroDivisionError, RecursionError):
        print(
            "strategies: invalid source, strategy, expectation or output",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
