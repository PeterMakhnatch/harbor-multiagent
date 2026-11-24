import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Never, override

from ..native_evidence import Invalid
from . import collect as collector
from .study import CHANNELS, FIXTURES, load_registration
from .support import audit

SCHEMA = "harbor.joint-reopen.effect/v1"
DELIVER, WITHHOLD = CHANNELS
BINDING = ("ticket", "team", "seed", "fixture", "channel")
DESIGN = (
    "task_registered",
    "balanced_paired",
    "teams",
    "teams_certified",
    "strata",
    "unbalanced_strata",
    "blocks",
    "paired_strata",
    "unpaired_strata",
    "repeats",
    "live_timing_removed",
)
KINDS = {
    (1, 0): "deliver_only",
    (0, 1): "withhold_only",
    (0, 0): "both_0",
    (1, 1): "both_1",
}
NOTES = {
    "difference": (
        "delivered minus withheld success rate over comparable tickets;"
        " [low, high]/denominator is the exact missing-binary-outcome"
        " identification bound with every unknown set to 0 or 1,"
        " not a sampling confidence interval"
    ),
    "blocks": (
        "a block is one team, private seed and fixture; it is comparable"
        " only when both arms hold the same positive number of tickets;"
        " every other block is unmatched in both arms"
    ),
    "pairs": (
        "single-replicate comparable blocks only; repeated blocks carry no"
        " pair ids and are never paired"
    ),
    "teams": "team_index is the position of the private label in sorted order",
    "timing": (
        "both arms keep the live phase barrier and elapsed time;"
        " withholding report contents does not remove timing signals"
    ),
    "scope": "no p-values, stopping rule or cost claim follows from this table",
}

assert CHANNELS == ("deliver", "withhold")

Outcome = int | None
Block = dict[str, list[Outcome]]


class Failure(Exception):
    pass


def _check(condition: bool, category: str) -> None:
    if not condition:
        raise Failure(category)


def _stage(category: str, step: Callable[..., Any], *args: Any) -> Any:
    try:
        return step(*args)
    except (OSError, ValueError, Invalid) as error:
        raise Failure(category) from error


def _outcome(row: dict[str, Any]) -> Outcome:
    status = row.get("status")
    observation = row.get("observation")
    _check(status in collector.STATUSES, "collection")
    if status != "usable":
        _check(observation is None, "collection")
        return None
    if not isinstance(observation, dict):
        raise Failure("collection")
    reward = observation.get("reward")
    _check(type(reward) is int and reward in (0, 1), "collection")
    return reward


def _blocks(
    cells: list[dict[str, Any]], index: dict[str, int]
) -> dict[tuple[int, str], list[Block]]:
    blocks: dict[tuple[int, int, str], Block] = {}
    for row in cells:
        key = (index[row["team"]], row["seed"], row["fixture"])
        arms = blocks.setdefault(key, {channel: [] for channel in CHANNELS})
        arms[row["channel"]].append(_outcome(row))
    grouped: dict[tuple[int, str], list[Block]] = {}
    for (team, _, fixture), arms in sorted(blocks.items()):
        grouped.setdefault((team, fixture), []).append(arms)
    assert sum(len(found) for found in grouped.values()) == len(blocks)
    return grouped


def _arm() -> dict[str, Any]:
    return {
        "registered": 0,
        "usable": 0,
        "unknown": 0,
        "unmatched": 0,
        "comparable": {"n": 0, "successes": 0, "failures": 0, "unknown": 0},
    }


def _tally(arm: dict[str, Any], outcomes: list[Outcome], matched: bool) -> None:
    comparable = arm["comparable"]
    for outcome in outcomes:
        arm["registered"] += 1
        arm["usable" if outcome is not None else "unknown"] += 1
        if not matched:
            arm["unmatched"] += 1
            continue
        comparable["n"] += 1
        kind = {1: "successes", 0: "failures", None: "unknown"}[outcome]
        comparable[kind] += 1


def _pair(pairs: dict[str, int], counts: dict[str, int], block: Block) -> None:
    if len(block[DELIVER]) > 1:
        counts["repeated"] += 1
        return
    pairs["single"] += 1
    deliver, withhold = block[DELIVER][0], block[WITHHOLD][0]
    if deliver is None or withhold is None:
        pairs["incomplete"] += 1
        return
    pairs["complete"] += 1
    pairs[KINDS[(deliver, withhold)]] += 1


def _difference(deliver: dict[str, int], withhold: dict[str, int]) -> Any:
    n = deliver["n"]
    assert n == withhold["n"] >= 0
    if n == 0:
        return None
    low = deliver["successes"] - (withhold["successes"] + withhold["unknown"])
    high = deliver["successes"] + deliver["unknown"] - withhold["successes"]
    assert -n <= low <= high <= n
    return {"low": low, "high": high, "denominator": n}


def _row(team: int, fixture: str, blocks: list[Block]) -> dict[str, Any]:
    arms = {channel: _arm() for channel in CHANNELS}
    counts = {"comparable": 0, "unmatched": 0, "repeated": 0}
    pairs = dict.fromkeys(("single", "complete", *KINDS.values(), "incomplete"), 0)
    for block in blocks:
        sizes = {len(block[channel]) for channel in CHANNELS}
        matched = len(sizes) == 1 and 0 not in sizes
        counts["comparable" if matched else "unmatched"] += 1
        for channel in CHANNELS:
            _tally(arms[channel], block[channel], matched)
        if matched:
            _pair(pairs, counts, block)
    assert pairs["single"] + counts["repeated"] == counts["comparable"]
    assert pairs["complete"] + pairs["incomplete"] == pairs["single"]
    return {
        "team_index": team,
        "fixture": fixture,
        "blocks": counts,
        "arms": arms,
        "difference": _difference(
            arms[DELIVER]["comparable"], arms[WITHHOLD]["comparable"]
        ),
        "pairs": pairs,
    }


def _totals(rows: list[dict[str, Any]], statuses: Counter[str]) -> dict[str, Any]:
    arms = {}
    for channel in CHANNELS:
        found = [row["arms"][channel] for row in rows]
        arms[channel] = {
            key: sum(arm[key] for arm in found)
            for key in ("registered", "usable", "unknown", "unmatched")
        }
        arms[channel]["comparable"] = sum(arm["comparable"]["n"] for arm in found)
    registered = sum(statuses.values())
    usable = statuses["usable"]
    assert registered == sum(arm["registered"] for arm in arms.values())
    assert usable == sum(arm["usable"] for arm in arms.values())
    return {
        "registered": registered,
        "usable": usable,
        "unknown": registered - usable,
        "statuses": {status: statuses[status] for status in collector.STATUSES},
        "arms": arms,
    }


def effect(registration_path: Path, roots: list[Path]) -> dict[str, Any]:
    _check(1 <= len(roots) <= collector.MAX_ROOTS, "usage")
    registration = _stage("registration", load_registration, registration_path)
    design = _stage("registration", audit, registration)
    report = _stage("collection", collector.collect_registered, registration, roots)
    cells = report["cells"]
    assert len(cells) == len(registration["cells"])
    index = {label: rank for rank, label in enumerate(sorted(registration["teams"]))}
    grouped = _blocks(cells, index)
    statuses = Counter(row["status"] for row in cells)
    rows = [
        _row(team, fixture, grouped.get((team, fixture), []))
        for team in range(len(index))
        for fixture in FIXTURES
    ]
    return {
        "schema": SCHEMA,
        "teams": len(index),
        "fixtures": list(FIXTURES),
        "channels": list(CHANNELS),
        "totals": _totals(rows, statuses),
        "design": {key: design[key] for key in DESIGN},
        "rows": rows,
        "notes": NOTES,
    }


def _flat(row: dict[str, Any]) -> dict[str, Any]:
    deliver, withhold = (row["arms"][channel] for channel in CHANNELS)
    difference = row["difference"]
    bound = (
        None
        if difference is None
        else "[{low},{high}]/{denominator}".format(**difference)
    )
    return {
        "team_index": row["team_index"],
        "fixture": row["fixture"],
        "n": deliver["comparable"]["n"],
        "deliver_successes": deliver["comparable"]["successes"],
        "deliver_unknown": deliver["comparable"]["unknown"],
        "withhold_successes": withhold["comparable"]["successes"],
        "withhold_unknown": withhold["comparable"]["unknown"],
        "difference": bound,
        "deliver_unmatched": deliver["unmatched"],
        "withhold_unmatched": withhold["unmatched"],
        "repeated_blocks": row["blocks"]["repeated"],
        **{f"pairs_{key}": count for key, count in row["pairs"].items()},
    }


def _tsv(rows: list[dict[str, Any]]) -> str:
    assert rows
    lines = ["\t".join(rows[0])]
    lines.extend(
        "\t".join("-" if value is None else str(value) for value in row.values())
        for row in rows
    )
    return "\n".join(lines)


def render(report: dict[str, Any]) -> str:
    totals = report["totals"]
    design = report["design"]
    arms = totals["arms"]
    summary = {
        "teams": report["teams"],
        **{key: totals[key] for key in ("registered", "usable", "unknown")},
        **totals["statuses"],
        **{f"{channel}_unmatched": arms[channel]["unmatched"] for channel in CHANNELS},
        **{key: design[key] for key in DESIGN if type(design[key]) is bool},
    }
    notes = "\n".join(f"{key}: {text}" for key, text in report["notes"].items())
    rows = [_flat(row) for row in report["rows"]]
    return "\n\n".join((_tsv([summary]), _tsv(rows), notes))


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        raise Failure("usage")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="python3 -BS -m examples.joint_reopen.effect")
    parser.add_argument("registration", type=Path)
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIALS_DIR")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        report = effect(args.registration, args.roots)
        text = json.dumps(report, indent=2) if args.json else render(report)
    except Failure as error:
        category = str(error.args[0])
    except Exception:
        category = "internal"
    else:
        print(text)
        return 0
    print(f"effect: {category}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
