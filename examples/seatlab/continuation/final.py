from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import __main__ as exchange
from . import final_board, final_score

HERE = Path(__file__).resolve().parent
PORTFOLIO = HERE.parent / "results" / "continuation-portfolio.json"
CODE = (
    "final.py",
    "final_board.py",
    "final_claims.py",
    "final_score.py",
    "source.py",
    "panel.py",
    "__main__.py",
)


READER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two completed Bespoke teams without revealing their grades.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    emit = commands.add_parser(
        "emit", help="write only blinded worker requests to stdout", allow_abbrev=False
    )
    emit.add_argument("cache", type=Path)
    emit.add_argument(
        "--shard", type=exchange._shard, default=(0, 1), metavar="INDEX/COUNT"
    )
    score = commands.add_parser(
        "score",
        help="score the public-claim rule and optional worker returns",
        allow_abbrev=False,
    )
    score.add_argument("cache", type=Path)
    score.add_argument(
        "--reader", help="one owner-supplied method label for all these replies"
    )
    score.add_argument("replies", nargs="*", type=Path)
    return parser.parse_args(argv)


def _bindings(
    owner: dict[str, Any], requests: list[dict[str, str]]
) -> tuple[list[list[tuple[str, str]]], dict[str, bytes]]:
    groups = [
        [(order["id"], order["prompt_sha256"]) for order in pair["orders"]]
        for pair in owner["pairs"]
    ]
    if (
        len(groups) != 18
        or any(len(group) != 2 for group in groups)
        or len(exchange._expected(groups)) != 36
    ):
        raise ValueError("need 18 pairs and 36 distinct order commitments")
    return groups, exchange._request_lines(requests, groups)


def _projection(owner: dict[str, Any], lines: dict[str, bytes]) -> dict[str, Any]:
    events: dict[str, int] = {}
    for pair in owner["pairs"]:
        for run, count in zip(pair["run_ids"], pair["events"], strict=True):
            if events.setdefault(run, count) != count:
                raise ValueError(f"inconsistent final-hour event count for {run}")
    return {
        "window_minutes": [180, 240],
        "end_exclusive": True,
        "unique_boards": len(events),
        "unique_public_events": sum(events.values()),
        "silent_boards": sum(n == 0 for n in events.values()),
        "owner_sha256": hashlib.sha256(exchange._encode(owner)).hexdigest(),
        "requests_sha256": hashlib.sha256(
            b"".join(lines[k] for k in sorted(lines))
        ).hexdigest(),
        "code_sha256": {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in CODE
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        owner, requests = final_board.prepare(
            exchange._outside_checkout(args.cache), PORTFOLIO
        )
        groups, lines = _bindings(owner, requests)
        if args.command == "emit":
            index, count = args.shard
            chosen = sorted(
                key
                for i, group in enumerate(groups)
                if i % count == index
                for key, _ in group
            )
            for key in chosen:
                sys.stdout.buffer.write(lines[key])
            return 0
        if args.replies and not args.reader:
            raise ValueError("provide --reader to label this method's replies")
        if args.reader and (
            len(args.reader) > 64 or READER.fullmatch(args.reader) is None
        ):
            raise ValueError("reader must be a lowercase slug of at most 64 characters")
        if len(args.replies) > exchange._MAX_REPLY_FILES:
            raise ValueError("too many reply files")
        replies = exchange._replies(args.replies, exchange._expected(groups))
        report = final_score.report(owner, replies)
        report["reader_label"] = args.reader
        report["projection"] = _projection(owner, lines)
        print(
            json.dumps(report, sort_keys=True, allow_nan=False, separators=(",", ":"))
        )
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        OverflowError,
        RecursionError,
    ) as exc:
        print(f"final board not evaluable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
