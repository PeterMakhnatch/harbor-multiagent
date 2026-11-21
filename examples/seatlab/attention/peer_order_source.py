import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import evidence, team_driver
from ..visibility import clarify_run
from . import audit, peer_order, protocol, ranked_collect, ranking
from .__main__ import _checked_study, _config, _observed

_MAX_REPLY_BYTES = 32 * 1024


@dataclass(frozen=True, slots=True)
class _Source:
    seed: int
    repetition: int
    shown: str
    world: dict[str, Any]


def _load_source(root: Path, seed: int) -> _Source:
    root = _checked_study(root)
    plan, ledger = ranked_collect._registered(root)
    cells = plan["cells"]
    cell = next(c for c in cells if c["seed"] == seed and c["arm"] == "shown")
    cell_id = cell["cell_id"]
    bills = ranked_collect._index_bills(ledger, cells)[cell_id]
    trial = team_driver._trial_dir(root, cell)
    config = _config(root, cell, "direct")
    if ranked_collect._native_paths(trial, cell, config) != {
        "config.json",
        "result.json",
        "handoff.json",
    }:
        raise ValueError("missing native source evidence")
    result_path = trial / "result.json"
    world_path = trial / "artifacts/logs/world/handoff.json"
    result_body = result_path.read_bytes()
    world_body = world_path.read_bytes()
    native, trial_id, success = ranked_collect._read_result(result_body, trial, config)
    observed = _observed(root, cell, "direct", ledger)
    outcome = observed["outcome"]
    if (
        not success
        or not native["reward_valid"]
        or observed["state"] != "complete"
        or outcome is None
        or outcome["trial_id"] != trial_id
        or not ranked_collect._usage_matches(outcome["turns"], bills, cell_id)
    ):
        raise ValueError("native source is not complete with 3 settled matching calls")
    if result_path.read_bytes() != result_body or world_path.read_bytes() != world_body:
        raise ValueError("native source changed during verification")
    world = clarify_run._object(world_body)
    cases = audit.reconstructed_capsules(world)["dispatch"]["view"]["cases"]
    repetition = cell["repetition"]
    opened = protocol._open_pair(outcome["opened"])
    shown = protocol.decide_prompt(cases, seed, repetition, opened)
    decide_turn = next(turn for turn in outcome["turns"] if turn["stage"] == "decide")
    audit._check_prompt(decide_turn, shown)
    return _Source(seed, repetition, shown, world)


def _score(source: _Source, reply: str) -> dict[str, int]:
    cases = source.world["cases"]
    decision = protocol.decide(reply, cases, source.seed, source.repetition)
    choices = {row["id"]: row["bay"] for row in decision["rows"]}
    graded_cases = [case | {"chosen": choices[case["id"]]} for case in cases]
    grade = evidence._score(source.world | {"cases": graded_cases})
    visible = protocol._reply(source.shown.partition("\n")[2], "cases")
    aliases = protocol.aliases(cases, source.seed, source.repetition)
    follows_best_visible = sum(
        choices[aliases[row["alias"]]]
        == max(
            row.get("revision_2", row["revision_1"]), key=row["utilities"].__getitem__
        )
        for row in visible
    )
    return {
        "shipments": grade["episodes"],
        "follows_best_visible": follows_best_visible,
        "optimal": grade["optimal"],
        "feasible": grade["feasible"],
    }


def _read_reply() -> str:
    try:
        raw = sys.stdin.buffer.read(_MAX_REPLY_BYTES + 1)
    except OSError:
        raise ValueError("cannot read the original JSON reply from stdin") from None
    if len(raw) > _MAX_REPLY_BYTES:
        raise ValueError(f"reply exceeds {_MAX_REPLY_BYTES} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("reply must be UTF-8") from None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export or score one verified native shown receiver prompt.",
        allow_abbrev=False,
    )
    parser.add_argument("--study", type=Path, required=True, help="physical study root")
    parser.add_argument(
        "--seed", type=int, required=True, help="registered ranked seed"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prompt = commands.add_parser("prompt", allow_abbrev=False)
    prompt.add_argument("arm", choices=("shown", "report", "utility"))
    commands.add_parser(
        "score", allow_abbrev=False, help="read the original JSON reply from stdin"
    )
    args = parser.parse_args(argv)
    if args.seed not in ranking._SEEDS:
        parser.error("--seed: expected a registered ranked seed")
    try:
        source = _load_source(args.study, args.seed)
    except (OSError, ValueError, TypeError, LookupError, RuntimeError, sqlite3.Error):
        parser.error("--study/--seed: expected a verified ranked direct shown source")
    if args.command == "prompt":
        try:
            text = (
                source.shown
                if args.arm == "shown"
                else peer_order.variant(source.shown, args.arm)
            )
        except ValueError:
            parser.error("prompt: cannot render the verified shown source")
        sys.stdout.buffer.write(text.encode("utf-8"))
    else:
        try:
            counts = _score(source, _read_reply())
        except ValueError as error:
            parser.error(f"score: {error}")
        print(json.dumps(counts, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
