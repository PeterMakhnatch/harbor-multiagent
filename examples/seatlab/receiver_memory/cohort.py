import argparse
import json
import sys
from fractions import Fraction
from itertools import permutations, product
from pathlib import Path
from runpy import run_path
from typing import Any

from ...native_study import require
from ..decision_boundary.core import World
from ..decision_boundary.read_budget import evaluate
from ..decision_boundary.revised_temporal import MAX_SEED, SLOTS, TASK
from . import wire_policy
from .model import Episode
from .provenance import AGES, ARMS, COINS, offer, reread
from .revised import SENDERS, cases

PRICE = Fraction(1, 4)
MAX_CANDIDATES = 256
PAIRS = frozenset(permutations(SLOTS, 2))


def balanced_seeds(start: int) -> dict[tuple[str, str], int]:
    require(type(start) is int and 0 <= start <= MAX_SEED, "invalid starting seed")
    fixture = run_path(str(Path(TASK) / "environment" / "world.py"))["fixture"]
    found: dict[tuple[str, str], int] = {}
    for seed in range(start, min(start + MAX_CANDIDATES, MAX_SEED + 1)):
        pair = fixture("replace", seed)
        require(pair in PAIRS, "invalid native slot rotation")
        found.setdefault(pair, seed)
        if found.keys() == PAIRS:
            return found
    raise ValueError("insufficient seeds for all six slot rotations")


def _winner(case: Episode) -> str:
    require(
        set(case.scores) == set(SLOTS) and sorted(case.scores.values()) == [0, 0, 1],
        "invalid native slot payoffs",
    )
    return next(slot for slot in SLOTS if case.scores[slot] == 1)


def _matched(wires: dict[str, bytes], age: str, coin: str) -> None:
    marked, erased, sham = (wires[key] for key in ("version", "erased", "sham"))
    require(len(marked) == len(erased) == len(sham), "version tags change offer size")
    changes = [
        (a, b, c)
        for a, b, c in zip(marked, erased, sham, strict=True)
        if a != b or b != c
    ]
    tag = b"1" if age == "oldest" else b"2"
    require(
        changes == [(tag[0], ord("?"), ord(coin))],
        "offers differ outside the assigned tag",
    )


def _sample(
    cases_by_arm: dict[str, list[World]],
    replies: list[World],
    episode: Episode,
    seed: int,
) -> None:
    response = reread(episode)
    for age, coin in product(AGES, COINS):
        row = f"{seed}/{episode.name}/{age}/{coin}"
        wires = {arm: offer(episode, age, arm, coin, PRICE) for arm in ARMS}
        _matched(wires, age, coin)
        for arm, wire in wires.items():
            cases_by_arm[arm].append(World(row, episode.mass, wire, episode.scores))
        replies.append(World(row, episode.mass, response, episode.scores))


def _play(rows: list[World], replies: list[World], rule: str) -> dict[str, str]:
    earned = reads = mass = 0
    for case, reply in zip(rows, replies, strict=True):
        require(case.name == reply.name, "wire replies not matched to decisions")
        action = wire_policy.first(case.observation, rule)
        if action["op"] == "read":
            reads += case.mass
            action = wire_policy.second(reply.observation)
        require(
            action["op"] == "commit" and action["slot"] in SLOTS,
            "wire policy did not commit a legal slot",
        )
        earned += case.mass * case.scores[action["slot"]]
        mass += case.mass
    physical, rate = Fraction(earned, mass), Fraction(reads, mass)
    return {
        "reward": str(physical),
        "reads": str(rate),
        "utility": str(physical - PRICE * rate),
    }


def cohort(start: int, sender: str) -> dict[str, Any]:
    require(sender in SENDERS, "unknown sender rule")
    seeds = balanced_seeds(start)
    arms: dict[str, list[World]] = {arm: [] for arm in ARMS}
    replies: list[World] = []
    for pair, seed in seeds.items():
        episodes = {case.name: case for case in cases(seed, sender)}
        require(
            episodes.keys() == {"confirm", "replace"}, "native world roster changed"
        )
        require(
            tuple(_winner(episodes[name]) for name in ("confirm", "replace")) == pair,
            "native grades disagree with slot rotation",
        )
        for episode in episodes.values():
            require(episode.mass == 1, "unbalanced native worlds")
            _sample(arms, replies, episode, seed)
    require(len(replies) == 48, "incomplete tag assignment")
    widths = {len(row.observation) for rows in arms.values() for row in rows}
    require(len(widths) == 1, "world or arm changes offer length")
    result = {arm: evaluate(rows, 1, replies, 1, PRICE) for arm, rows in arms.items()}
    fixed = {
        arm: {rule: _play(rows, replies, rule) for rule in wire_policy.POLICIES}
        for arm, rows in arms.items()
    }
    require(
        all(
            fixed[arm][rule] == result[arm][rule]
            for arm in ARMS
            for rule in ("never", "always")
        ),
        "public fixed controls do not attain the computed endpoints",
    )
    valid, sham = (fixed[arm]["by-tag"] for arm in ("version", "sham"))
    require(
        valid["reads"] == sham["reads"],
        "independent tag changes the read rate of the public policy",
    )
    public_advantage = Fraction(valid["utility"]) - Fraction(sham["utility"])
    for arm in ("erased", "sham"):
        require(
            all(
                result[arm][policy] == result["version"][policy]
                for policy in ("never", "always")
            ),
            "tag changes the fixed-policy result",
        )
    require(
        result["erased"]["adaptive"] == result["sham"]["adaptive"],
        "random tag is informative",
    )
    advantage = Fraction(result["version"]["adaptive"]["utility"]) - Fraction(
        result["sham"]["adaptive"]["utility"]
    )
    return {
        "schema": "seatlab.receiver-memory.optional-read/v1",
        "sender": sender,
        "seeds": sorted(seeds.values()),
        "rows_per_arm": len(replies),
        "native_grades": len(seeds) * len(SLOTS) * 2 * 2,
        "model_calls": 0,
        "trials": 0,
        "offer_bytes": widths.pop(),
        "price": str(PRICE),
        "arms": result,
        "public_wire_policies": fixed,
        "by_tag_version_vs_sham_utility": str(public_advantage),
        "version_vs_sham_utility": str(advantage),
        "version_benefit": result["version"]["selective_reading_pattern"]
        and advantage > 0
        and public_advantage > 0
        and not any(
            result[arm]["selective_reading_pattern"] for arm in ("erased", "sham")
        ),
        "scope": "Optimal closed-context receiver decisions given the declared distribution and sender rule. One legal inbox reread has a hypothetical utility price, not a native fee. Not agent behavior, provider compaction, or a free-team trial.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify selective inbox rereads on native handoffs."
    )
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--sender", required=True, choices=SENDERS)
    parser.add_argument("--expect-version-benefit", choices=("yes", "no"))
    args = parser.parse_args(argv)
    try:
        result = cohort(args.start, args.sender)
    except (ValueError, RuntimeError, OSError, AssertionError) as error:
        print(f"receiver-cohort: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")))
    return (
        0
        if args.expect_version_benefit is None
        or result["version_benefit"] == (args.expect_version_benefit == "yes")
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
