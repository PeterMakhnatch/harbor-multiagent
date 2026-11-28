import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from ...seat_checkpoints.materialize import sync_directory
from ...ship_inflight.materialize import publish
from ..continuation.__main__ import _read_bounded
from ..coupled_retention import author, strategies
from ..exhibit_enroll import target, write_private
from . import ballot_deck, ballot_freeze, loader, matrix
from .records import team_name

LIMIT = 16 * 1024 * 1024


def _write(out: Path, files: dict[str, bytes]) -> None:
    out = target(out)
    with tempfile.TemporaryDirectory(
        prefix=".seatlab-ballot-", dir=out.parent
    ) as stage:
        root = Path(stage) / "output"
        root.mkdir(mode=0o700)
        for name, content in files.items():
            write_private(root / name, content)
        sync_directory(root)
        publish(root, out)
        sync_directory(out.parent)


def _encode(obj: object) -> bytes:
    return (
        json.dumps(
            obj,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def _native_support(
    start: int, sender: str, role: str
) -> tuple[tuple[str, ...], set[bytes]]:
    from ..coupled_retention import native

    support: set[bytes] = set()
    labels: tuple[str, ...] | None = None
    for index in range(6):
        _, east, west, current = native.prepare_case(start, sender, index)
        if labels is not None and labels != current:
            raise ValueError("inconsistent native actions")
        labels = current
        support.update(east if role == "east" else west)
    if labels is None:
        raise ValueError("empty native support")
    return labels, support


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    if args.game is not None:
        if args.sender is not None:
            raise ValueError("cannot combine an authored game and native sender")
        east, west, rows = author.load_game(args.game)
        actions = east if args.role == "east" else west
        packets = {r.east if args.role == "east" else r.west for r in rows}
        field = "action"
    else:
        if args.sender is None:
            raise ValueError("missing native sender")
        actions, packets = _native_support(args.start, args.sender, args.role)
        field = "slot"
    manifest, requests = ballot_deck.make(args.role, actions, packets, field)
    pin = loader.digest(manifest)
    _write(args.out, {"deck.json": manifest, "requests.jsonl": requests})
    return {
        "schema": "seatlab.role-ballot-prepared/v1",
        "role": args.role,
        "requests": len(packets),
        "deck_sha256": pin,
    }


def freeze(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    source = team_name(args.source)
    manifest = _read_bounded(args.deck / "deck.json", 32 * 1024)
    requests = _read_bounded(args.deck / "requests.jsonl", LIMIT)
    role, actions, field, mapping = ballot_deck.parse(
        manifest, requests, args.deck_sha256
    )
    if role != args.role or not 1 <= len(args.replies) <= 32:
        raise ValueError("wrong role or invalid reply set")
    shards: list[bytes] = []
    remaining = LIMIT
    for path in args.replies:
        if str(path) == "-":
            raise ValueError("a saved reply file is required")
        data = _read_bounded(path, remaining)
        remaining -= len(data)
        shards.append(data)
    counts, table = ballot_freeze.freeze(role, actions, field, mapping, shards)
    receipt: dict[str, Any] = {
        "schema": "seatlab.role-ballot-result/v1",
        "role": role,
        "declared_source": source,
        "deck_sha256": args.deck_sha256,
        "reply_shard_sha256": sorted(loader.digest(s) for s in shards),
        "counts": counts,
        "complete": table is not None,
        "table_sha256": None,
        "scope": "One recorded choice per exact role observation, not verified first attempts, provenance, or live team rewards.",
    }
    if table is None:
        return 1, receipt
    receipt["table_sha256"] = loader.digest(table)
    _write(args.out, {"strategy.json": table, "evidence.json": _encode(receipt)})
    return 0, receipt


def compare(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from . import ballot_cohort

    teams, info = ballot_cohort.load(args.team)
    if args.cohort_sha256 is not None and args.cohort_sha256 != info["cohort_sha256"]:
        raise ValueError("changed cohort")
    result = matrix.evaluate(strategies.game(args), teams, unit="recorded_team")
    return matrix.gate(result, args.expect_gap, args.require_equal_home), {
        "schema": "seatlab.recorded-team-crossplay/v1",
        "cohort": info,
        "closed_role_replay": result,
        "model_calls": 0,
        "trials": 0,
        "scope": "One captured decision per observation. Pairings share those decisions. Not independent trials, verified model identities, or live interactions.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Prepare role-only requests or freeze their saved replies. No provider calls."
    )
    commands = p.add_subparsers(dest="command", required=True)
    deck = commands.add_parser("prepare")
    group = deck.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", type=int)
    group.add_argument("--game")
    deck.add_argument("--sender", choices=("truthful", "pass"))
    submitted = commands.add_parser("freeze")
    submitted.add_argument("--deck", type=Path, required=True)
    submitted.add_argument("--deck-sha256", required=True)
    submitted.add_argument("--source", required=True)
    submitted.add_argument("--replies", type=Path, nargs="+", required=True)
    crossed = commands.add_parser("compare")
    source = crossed.add_mutually_exclusive_group(required=True)
    source.add_argument("--start", type=int)
    source.add_argument("--game")
    crossed.add_argument("--sender", choices=("truthful", "pass"))
    crossed.add_argument("--arm", choices=("together", "apart", "independent"))
    crossed.add_argument(
        "--team",
        action="append",
        nargs=3,
        metavar=("NAME", "EAST_DIR", "WEST_DIR"),
        required=True,
    )
    crossed.add_argument("--cohort-sha256")
    crossed.add_argument("--require-equal-home", action="store_true")
    crossed.add_argument("--expect-gap", choices=("positive", "zero", "negative"))
    for cmd in (deck, submitted):
        cmd.add_argument("--role", choices=("east", "west"), required=True)
        cmd.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "prepare":
            code, result = 0, prepare(args)
        elif args.command == "freeze":
            code, result = freeze(args)
        else:
            code, result = compare(args)
    except (OSError, ValueError, OverflowError, RecursionError, TypeError):
        print(
            "ballots: invalid source, role, enrollment, response file or output",
            file=sys.stderr,
        )
        return 2
    print(_encode(result).decode("ascii"), end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
