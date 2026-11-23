from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from examples.seatlab.market_handoff import decisions

CALIBRATION, HELDOUT = decisions.ARMS
THRESHOLD = Fraction(3, 100)
GRADES = ("world_score", "multiple")
Menu = list[Fraction]
Record = dict[str, Any]

assert THRESHOLD == Fraction(str(decisions.MIN_DELTA))
assert decisions.PICKS == (1, 2, 3)


def _pairs(pairs: list[tuple[str, Any]]) -> Record:
    if len({key for key, _ in pairs}) != len(pairs):
        decisions.fail("duplicate key")
    return dict(pairs)


def read_menu(root: Path, arm: str, world: int, cell: Record) -> tuple[Menu, str]:
    assert arm in decisions.ARMS and 0 <= world < decisions.WORLDS
    path = root / f"{arm}-{world:02d}.payoffs.json"
    if path.is_symlink() or not path.is_file():
        decisions.fail(f"{path}: missing or not a regular file")
    data = path.read_bytes()
    if len(data) > decisions.MAX_FILE_BYTES:
        decisions.fail(f"{path}: larger than {decisions.MAX_FILE_BYTES} bytes")
    record = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                        parse_constant=decisions.fail, parse_float=Fraction)
    if not isinstance(record, dict) or record.keys() != decisions.PAYOFF_KEYS:
        decisions.fail(f"{path}: expected exactly {sorted(decisions.PAYOFF_KEYS)}")
    options = record["options"] if isinstance(record["options"], list) else []
    menu = len(options) == len(decisions.PICKS) and all(
        isinstance(item, dict) and item.keys() == decisions.OPTION_KEYS
        and type(item["pick"]) is int and item["pick"] == pick
        for item, pick in zip(options, decisions.PICKS))
    decisions.require(path, {
        "schema": record["schema"] == "seatlab.market-payoffs/v1",
        "source": record["source"] == decisions.PINNED_SOURCE,
        "world": type(record["world"]) is int and record["world"] == world,
        "prefix_fingerprint": record["prefix_fingerprint"] == cell["prefix_fingerprint"],
        "menu": menu,
        "grades": menu and all(type(item[field]) in (int, Fraction) for item in options
                               for field in GRADES)
        and all(0 <= item["world_score"] <= 1 for item in options),
    })
    scores = [Fraction(item["world_score"]) for item in options]
    return scores, hashlib.sha256(data).hexdigest()


def enrolled(root: Path) -> decisions.Cells:
    cells = decisions.load_cells(root)
    states = {arm: {cells[(arm, world)]["prefix_fingerprint"] for world in range(decisions.WORLDS)}
              for arm in decisions.ARMS}
    if states[CALIBRATION] & states[HELDOUT]:
        decisions.fail("state fingerprints repeat across target and donor arms")
    return cells


def fixed_means(menus: list[Menu]) -> tuple[int, Menu]:
    assert len(menus) == decisions.WORLDS
    means = [sum((menu[index] for menu in menus), Fraction(0)) / len(menus)
             for index in range(len(decisions.PICKS))]
    return decisions.PICKS[means.index(max(means))], means


def by_pick(values: Menu) -> dict[str, float]:
    return {str(pick): float(value) for pick, value in zip(decisions.PICKS, values)}


def attribution(world: int, cell: Record, menu: Menu, deployed: int, digest: str) -> Record:
    best, fixed = max(menu), menu[deployed - 1]
    assert best >= fixed
    return {"world": world, "shape": cell["shape"], "terminal": cell["terminal"],
            "prefix_fingerprint": cell["prefix_fingerprint"], "payoffs_sha256": digest,
            "world_score": by_pick(menu), "fixed": float(fixed), "oracle": float(best),
            "oracle_picks": [pick for pick, value in zip(decisions.PICKS, menu) if value == best],
            "regret": float(best - fixed)}


def arm_menus(root: Path, arm: str, cells: decisions.Cells,
              digests: dict[str, str]) -> list[Menu]:
    menus: list[Menu] = []
    for world in range(decisions.WORLDS):
        menu, digests[f"{arm}-{world:02d}.payoffs.json"] = read_menu(
            root, arm, world, cells[(arm, world)])
        menus.append(menu)
    return menus


def ceiling(root: Path) -> Record:
    cells = enrolled(root)
    digests: dict[str, str] = {}
    targets = arm_menus(root, CALIBRATION, cells, digests)
    deployed, calibration = fixed_means(targets)
    held = arm_menus(root, HELDOUT, cells, digests)
    rows = [attribution(world, cells[(HELDOUT, world)], held[world], deployed,
                        digests[f"{HELDOUT}-{world:02d}.payoffs.json"])
            for world in range(decisions.WORLDS)]
    fixed = sum((menu[deployed - 1] for menu in held), Fraction(0)) / len(held)
    oracle = sum((max(menu) for menu in held), Fraction(0)) / len(held)
    gap = oracle - fixed
    assert gap >= 0 and len(digests) == 2 * decisions.WORLDS
    hindsight, heldout_means = fixed_means(held)
    sufficient = gap >= THRESHOLD
    return {
        "schema": "seatlab.market-ceiling/v1", "source": decisions.PINNED_SOURCE,
        "numpy_version": cells[(CALIBRATION, 0)]["numpy_version"],
        "payoffs_sha256": digests,
        "payoffs_set_sha256": decisions.sha(decisions.canonical(digests)),
        "calibration": {"arm": CALIBRATION, "rule": "highest_mean_fixed_pick_lowest_on_tie",
                        "means": by_pick(calibration), "deployed_pick": deployed},
        "heldout": {"arm": HELDOUT, "deployed_pick": deployed, "fixed_mean": float(fixed),
                    "oracle_mean": float(oracle), "gap": float(gap), "gap_exact": str(gap),
                    "threshold": str(THRESHOLD), "sufficient": sufficient,
                    "reading": "menu_has_room_not_agent_evidence" if sufficient
                    else "no_agent_on_this_menu_can_reach_threshold_on_this_panel"},
        "denominators": {
            "enrolled": len(rows), "calibration_worlds": len(targets),
            "heldout_worlds": len(held),
            "regret_positive": sum(row["regret"] > 0 for row in rows),
            "oracle_ties": sum(len(row["oracle_picks"]) > 1 for row in rows),
            "equal_every_option": sum(len(set(menu)) == 1 for menu in held),
            "zero_every_option": sum(max(menu) == 0 for menu in held)},
        "attribution": rows,
        "inspection_only": {"hindsight_best_fixed_pick": hindsight,
                            "heldout_means": by_pick(heldout_means), "changes_deployed": False},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m examples.seatlab.market_handoff.ceiling")
    parser.add_argument("dir", type=Path)
    args = parser.parse_args(argv)
    try:
        report = ceiling(args.dir)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    print(decisions.canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
