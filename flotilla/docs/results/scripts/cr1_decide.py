"""Decisions of PROBE.md section 15 (pilot cr1) from the counted rows of one probe_analysis.py JSON file.

    python3 flotilla/docs/results/scripts/probe_analysis.py .flotilla-local/jobs 'cr1-??????' \
        --tasks .flotilla-local/tasks/cr1 --plan flotilla/plans/cr1.json --json cr1.json
    python3 flotilla/docs/results/scripts/cr1_decide.py --plan flotilla/plans/cr1.json cr1.json

Per model: exact two-sided McNemar on `correct`, paired by instance. Primary: talk-false vs talk-false-admit, Holm
over the plan's models, PASS when the adjusted p < alpha and talk-false-admit is more accurate. Secondary (reported,
not gated): talk-false vs talk-false-insist, Holm over the models, with the direction of any difference. A test with
fewer pairs than planned instances is INCOMPLETE and stays in its Holm family.
"""

from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path

ALPHA = 0.05
BASE = "talk-false"
PRIMARY = "talk-false-admit"
SECONDARY = "talk-false-insist"


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adjusted = [0.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adjusted[i] = running
    return adjusted


def load_plan(path: Path) -> tuple[list[str], int, list[str]]:
    plan = json.loads(path.read_text())
    if len(plan["task_sets"]) != 1:
        raise SystemExit(f"{path}: expected one task set, found {len(plan['task_sets'])}")
    task_set = plan["task_sets"][0]
    arms = list(task_set["arms"])
    for arm in (BASE, PRIMARY, SECONDARY):
        if arm not in arms:
            raise SystemExit(f"{path}: arm {arm} is not planned")
    return list(plan["models"]), len(task_set["instances"]), arms


def correct_by_cell(path: Path, models: list[str]) -> dict[tuple[str, str, int], bool]:
    data = json.loads(path.read_text())
    cells: dict[tuple[str, str, int], bool] = {}
    for row in data["rows"]:
        if not row.get("counted"):
            continue
        if row["model"] not in models:
            raise SystemExit(f"{path}: counted row for {row['model']}, which is not in the plan")
        key = (row["model"], row["arm"], int(row["instance"]))
        if key in cells:
            raise SystemExit(f"{path}: two counted rows for {key}")
        cells[key] = bool(row["correct"])
    return cells


def test(cells: dict, model: str, arm: str, planned: int) -> dict:
    instances = sorted({i for (m, a, i) in cells if m == model and a == BASE}
                       & {i for (m, a, i) in cells if m == model and a == arm})
    b = sum(1 for i in instances if cells[(model, BASE, i)] and not cells[(model, arm, i)])
    c = sum(1 for i in instances if not cells[(model, BASE, i)] and cells[(model, arm, i)])
    base_correct = sum(cells[(model, BASE, i)] for i in instances)
    arm_correct = sum(cells[(model, arm, i)] for i in instances)
    return {"model": model, "arm": arm, "pairs": len(instances), "planned": planned, "b": b, "c": c,
            "p": mcnemar_exact(b, c), "base_correct": base_correct, "arm_correct": arm_correct}


def decide(tests: list[dict], primary: bool) -> None:
    for row, p_holm in zip(tests, holm([row["p"] for row in tests])):
        row["p_holm"] = p_holm
        higher = row["arm_correct"] > row["base_correct"]
        if row["pairs"] < row["planned"]:
            row["decision"] = f"INCOMPLETE ({row['planned'] - row['pairs']} pairs missing)"
        elif primary:
            row["decision"] = "PASS" if p_holm < ALPHA and higher else "FAIL"
        elif p_holm < ALPHA:
            row["decision"] = f"differs: {row['arm']} {'more' if higher else 'less'} accurate"
        else:
            row["decision"] = "no difference"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("analysis", type=Path)
    args = parser.parse_args()
    models, planned, _ = load_plan(args.plan)
    cells = correct_by_cell(args.analysis, models)
    for label, arm in (("Primary", PRIMARY), ("Secondary", SECONDARY)):
        tests = [test(cells, model, arm, planned) for model in models]
        decide(tests, primary=label == "Primary")
        print(f"{label}: {BASE} vs {arm}, Holm over {len(tests)} models, alpha {ALPHA} (PROBE.md section 15)")
        for row in tests:
            print(f"  {row['model']}: {row['pairs']} pairs, correct {row['base_correct']} vs {row['arm_correct']}, "
                  f"b {row['b']}, c {row['c']}, p {row['p']:.3g}, Holm p {row['p_holm']:.3g} -> {row['decision']}")


if __name__ == "__main__":
    main()
