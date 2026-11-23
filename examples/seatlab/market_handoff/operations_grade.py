from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import decisions

MANIFEST = "examples/seatlab/market-operations.md"
ROW_SCHEMA = "seatlab.market-operations/v1"
FREEZE_SCHEMA = "seatlab.market-operations-freeze/v1"
SCORE_SCHEMA = "seatlab.market-operations-score/v1"
SEED_NAMESPACE = "seatlab/market-roles/v2"
SPLITS = ("calibrate", "holdout")
CALIBRATE, HOLDOUT = SPLITS
NUMPY_VERSION = "2.1.3"
ADS = ("usual", "off", "push")
STOCK = ("usual", "lean", "deep")
OPTIONS = tuple((ads, stock) for ads in ADS for stock in STOCK)
LABELS = tuple(f"{ads}/{stock}" for ads, stock in OPTIONS)
ROW_KEYS = frozenset({"schema", "split", "world", "shape", "source", "numpy_version",
                      "completed_days", "terminal", "prefix_fingerprint", "options"})
OPTION_KEYS = frozenset({"ads", "stock", "world_score", "multiple"})
MIN_GAIN_OVER_FIXED = Fraction(3, 100)
MIN_GAIN_OVER_ONE_AXIS = Fraction(1, 100)
MIN_BOTH_NEEDED_WORLDS = 3
WORLDS = decisions.WORLDS
OUTCOMES = len(SPLITS) * WORLDS * len(OPTIONS)
Record = dict[str, Any]
Menu = list[Fraction]

assert WORLDS == 12 and len(decisions.SHAPES) == WORLDS
assert len(OPTIONS) == 9 and len(set(LABELS)) == len(OPTIONS) and OUTCOMES == 216

FIELDS = {
    "label": "option label ads/stock; ads in usual,off,push; stock in usual,lean,deep",
    "rank": "0-based Cartesian rank: ads outer usual,off,push, stock inner usual,lean,deep",
    "exact": "grade as an exact fraction string parsed from the decimal in the row file",
    "float": "the same grade rounded to a binary float for reading only",
    "freeze": "calibration-only seal; score re-derives it and requires identical canonical text",
    "freeze_sha256": "sha256 of the canonical freeze text without trailing newline",
    "fixed": "(a) holdout world_score at the frozen calibration combination",
    "one_axis": "(b) best world_score changing at most one control from the frozen combination",
    "best": "(c) best world_score across all 9 combinations",
    "labels": "every combination that attains the grade; exact ties are kept",
    "axes": "controls changed by the one-axis labels; none means the frozen combination ties",
    "gain_over_fixed": "c - a",
    "gain_over_one_axis": "c - b",
    "both_needed": "c > b: the world needs both controls changed",
    "all_equal": "all 9 combinations have the same world_score",
    "positive": "holdout world counts with a strictly positive difference",
    "gate": "12 complete worlds, 216 outcomes, mean(c-a) >= 3/100, mean(c-b) >= 1/100, "
            "both_needed on >= 3 worlds; exact fraction comparisons",
}


def _unique(pairs: list[tuple[str, Any]]) -> Record:
    if len({key for key, _ in pairs}) != len(pairs):
        decisions.fail("duplicate key")
    return dict(pairs)


def exact(value: Fraction) -> Record:
    return {"exact": str(value), "float": float(value)}


def mean(values: Menu) -> Fraction:
    assert values
    return sum(values, Fraction(0)) / len(values)


def _number(value: Any) -> bool:
    return type(value) in (int, Fraction)


def read_row(root: Path, split: str, world: int) -> tuple[Record, Menu, str]:
    assert split in SPLITS and 0 <= world < WORLDS
    path = root / f"{split}-{world:02d}.operations.json"
    if path.is_symlink() or not path.is_file():
        decisions.fail(f"{path}: missing or not a regular file")
    data = path.read_bytes()
    if len(data) > decisions.MAX_FILE_BYTES:
        decisions.fail(f"{path}: larger than {decisions.MAX_FILE_BYTES} bytes")
    record = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                        parse_constant=decisions.fail, parse_float=Fraction)
    if not isinstance(record, dict) or record.keys() != ROW_KEYS:
        decisions.fail(f"{path}: expected exactly {sorted(ROW_KEYS)}")
    options = record["options"] if isinstance(record["options"], list) else []
    days, state = record["completed_days"], record["prefix_fingerprint"]
    menu = len(options) == len(OPTIONS) and all(
        isinstance(item, dict) and item.keys() == OPTION_KEYS
        and (item["ads"], item["stock"]) == option for item, option in zip(options, OPTIONS))
    decisions.require(path, {
        "schema": record["schema"] == ROW_SCHEMA,
        "split": record["split"] == split,
        "world": type(record["world"]) is int and record["world"] == world,
        "shape": record["shape"] == decisions.SHAPES[world],
        "source": record["source"] == decisions.PINNED_SOURCE,
        "numpy_version": record["numpy_version"] == NUMPY_VERSION,
        "completed_days": type(days) is int and 0 <= days <= decisions.PREFIX_DAYS,
        "terminal": type(record["terminal"]) is bool
        and (days == decisions.PREFIX_DAYS or record["terminal"]),
        "prefix_fingerprint": isinstance(state, str)
        and decisions.HEX64.fullmatch(state) is not None,
        "options": menu,
        "world_score": menu and all(_number(item["world_score"]) and 0 <= item["world_score"] <= 1
                                    for item in options),
        "multiple": menu and all(_number(item["multiple"]) for item in options),
    })
    scores = [Fraction(item["world_score"]) for item in options]
    assert len(scores) == len(OPTIONS)
    return record, scores, hashlib.sha256(data).hexdigest()


def read_split(root: Path, split: str) -> tuple[list[Record], list[Menu], dict[str, str]]:
    assert split in SPLITS
    if not root.is_dir():
        decisions.fail(f"{root}: not a directory")
    expected = [f"{split}-{world:02d}.operations.json" for world in range(WORLDS)]
    found = sorted(path.name for path in root.glob("*.operations.json"))
    if found != expected:
        decisions.fail(f"{root}: expected exactly {expected[0]}..{expected[-1]}, found {found}")
    rows: list[Record] = []
    menus: list[Menu] = []
    digests: dict[str, str] = {}
    for world, name in enumerate(expected):
        row, menu, digests[name] = read_row(root, split, world)
        rows.append(row)
        menus.append(menu)
    if len({row["prefix_fingerprint"] for row in rows}) != WORLDS:
        decisions.fail(f"{root}: {split} prefix fingerprints repeat")
    assert len(rows) == len(menus) == len(digests) == WORLDS
    return rows, menus, digests


def option(rank: int) -> Record:
    assert 0 <= rank < len(OPTIONS)
    ads, stock = OPTIONS[rank]
    return {"rank": rank, "label": LABELS[rank], "ads": ads, "stock": stock}


def freeze(root: Path) -> Record:
    rows, menus, digests = read_split(root, CALIBRATE)
    means = [mean([menu[rank] for menu in menus]) for rank in range(len(OPTIONS))]
    fixed = means.index(max(means))
    assert all(value < means[fixed] for value in means[:fixed])
    return {
        "schema": FREEZE_SCHEMA, "manifest": MANIFEST, "seed_namespace": SEED_NAMESPACE,
        "source": decisions.PINNED_SOURCE, "numpy_version": NUMPY_VERSION,
        "options": list(LABELS),
        "rule": "highest_calibration_mean_world_score_earliest_cartesian_rank_on_tie",
        "calibration": {
            "split": CALIBRATE, "worlds": WORLDS, "operations_sha256": digests,
            "operations_set_sha256": decisions.sha(decisions.canonical(digests)),
            "prefix_fingerprints": [row["prefix_fingerprint"] for row in rows],
            "means": [option(rank) | exact(value) for rank, value in enumerate(means)]},
        "fixed": option(fixed),
    }


def separate(calibration: Path, holdout: Path) -> None:
    left, right = calibration.resolve(), holdout.resolve()
    if left == right or left in right.parents or right in left.parents:
        decisions.fail("calibration and holdout directories must be separate roots")


def sealed(calibration: Path, freeze_path: Path) -> tuple[Record, str]:
    derived = freeze(calibration)
    text = decisions.canonical(derived)
    if decisions.read_text(freeze_path).removesuffix("\n") != text:
        decisions.fail(f"{freeze_path}: does not match the freeze re-derived from {calibration}")
    return derived, decisions.sha(text)


def one_axis_ranks(fixed: int) -> list[int]:
    ads, stock = OPTIONS[fixed]
    ranks = [rank for rank, (a, s) in enumerate(OPTIONS) if a == ads or s == stock]
    assert len(ranks) == len(ADS) + len(STOCK) - 1 and fixed in ranks
    return ranks


def changed_axis(fixed: int, rank: int) -> str:
    if rank == fixed:
        return "none"
    assert rank in one_axis_ranks(fixed)
    return "ads" if OPTIONS[rank][0] != OPTIONS[fixed][0] else "stock"


def attribute(row: Record, menu: Menu, fixed: int, digest: str) -> Record:
    near = one_axis_ranks(fixed)
    a, b, c = menu[fixed], max(menu[rank] for rank in near), max(menu)
    assert a <= b <= c
    near_best = [rank for rank in near if menu[rank] == b]
    return {
        "world": row["world"], "shape": row["shape"], "terminal": row["terminal"],
        "completed_days": row["completed_days"], "prefix_fingerprint": row["prefix_fingerprint"],
        "operations_sha256": digest,
        "world_score": {LABELS[rank]: exact(value) for rank, value in enumerate(menu)},
        "multiple": {LABELS[rank]: float(item["multiple"])
                     for rank, item in enumerate(row["options"])},
        "fixed": {"labels": [LABELS[fixed]]} | exact(a),
        "one_axis": {"labels": [LABELS[rank] for rank in near_best],
                     "axes": sorted({changed_axis(fixed, rank) for rank in near_best})} | exact(b),
        "best": {"labels": [LABELS[rank] for rank, value in enumerate(menu) if value == c]}
        | exact(c),
        "gain_over_fixed": exact(c - a), "gain_over_one_axis": exact(c - b),
        "both_needed": c > b, "all_equal": len(set(menu)) == 1,
    }


def gate(menus: list[Menu], fixed: int) -> Record:
    near = one_axis_ranks(fixed)
    a = [menu[fixed] for menu in menus]
    b = [max(menu[rank] for rank in near) for menu in menus]
    c = [max(menu) for menu in menus]
    over_fixed = mean([best - base for best, base in zip(c, a)])
    over_one_axis = mean([best - base for best, base in zip(c, b)])
    both_needed = sum(best > base for best, base in zip(c, b))
    checks = {
        "complete": len(menus) == WORLDS and all(len(menu) == len(OPTIONS) for menu in menus),
        "gain_over_fixed": over_fixed >= MIN_GAIN_OVER_FIXED,
        "gain_over_one_axis": over_one_axis >= MIN_GAIN_OVER_ONE_AXIS,
        "both_needed_worlds": both_needed >= MIN_BOTH_NEEDED_WORLDS,
    }
    passed = all(checks.values())
    return {
        "means": {"fixed": exact(mean(a)), "one_axis": exact(mean(b)), "best": exact(mean(c)),
                  "gain_over_fixed": exact(over_fixed),
                  "gain_over_one_axis": exact(over_one_axis)},
        "positive": {"best_over_fixed": sum(x > y for x, y in zip(c, a)),
                     "best_over_one_axis": both_needed,
                     "one_axis_over_fixed": sum(x > y for x, y in zip(b, a)),
                     "all_equal": sum(len(set(menu)) == 1 for menu in menus),
                     "zero_every_option": sum(max(menu) == 0 for menu in menus)},
        "gate": {"worlds": len(menus), "required_worlds": WORLDS, "outcomes": OUTCOMES,
                 "min_gain_over_fixed": str(MIN_GAIN_OVER_FIXED),
                 "min_gain_over_one_axis": str(MIN_GAIN_OVER_ONE_AXIS),
                 "min_both_needed_worlds": MIN_BOTH_NEEDED_WORLDS,
                 "both_needed_worlds": both_needed,
                 "checks": checks, "passed": passed,
                 "reading": "both_controls_have_room_design_a_model_study_only" if passed
                 else "this_operating_target_ruled_out_on_these_worlds"},
    }


def score(calibration: Path, holdout: Path, freeze_path: Path) -> Record:
    separate(calibration, holdout)
    frozen, frozen_sha = sealed(calibration, freeze_path)
    rows, menus, digests = read_split(holdout, HOLDOUT)
    reused = set(frozen["calibration"]["prefix_fingerprints"]) & {
        row["prefix_fingerprint"] for row in rows}
    if reused:
        decisions.fail(f"holdout reuses calibration states: {sorted(reused)}")
    fixed = frozen["fixed"]["rank"]
    assert len(digests) == WORLDS and len(frozen["calibration"]["operations_sha256"]) == WORLDS
    attribution = [attribute(row, menu, fixed, digests[f"{HOLDOUT}-{world:02d}.operations.json"])
                   for world, (row, menu) in enumerate(zip(rows, menus))]
    return {
        "schema": SCORE_SCHEMA, "fields": FIELDS, "manifest": MANIFEST,
        "source": decisions.PINNED_SOURCE, "numpy_version": NUMPY_VERSION,
        "freeze": frozen, "freeze_sha256": frozen_sha,
        "holdout": {"split": HOLDOUT, "worlds": WORLDS, "operations_sha256": digests,
                    "operations_set_sha256": decisions.sha(decisions.canonical(digests))},
        "fixed": frozen["fixed"],
        **gate(menus, fixed),
        "attribution": attribution,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.market_handoff.operations_grade")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("freeze").add_argument("calibration_dir", type=Path)
    scoring = commands.add_parser("score")
    for name in ("calibration_dir", "holdout_dir", "freeze_file"):
        scoring.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            report = freeze(args.calibration_dir)
        else:
            report = score(args.calibration_dir, args.holdout_dir, args.freeze_file)
    except (OSError, UnicodeDecodeError, ValueError, OverflowError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    print(decisions.canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
