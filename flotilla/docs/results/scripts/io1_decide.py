"""Decisions of PROBE.md section 14 (pilot io1) from two probe_analysis.py JSON files.

    python3 flotilla/docs/results/scripts/probe_analysis.py .flotilla-local/jobs 'io1-??????' \
        --tasks .flotilla-local/tasks/io1v --plan flotilla/plans/io1.json --json io1v.json
    (the same with io1s)
    python3 flotilla/docs/results/scripts/io1_decide.py --plan flotilla/plans/io1.json --reliance rr1-decisions.json \
        io1v.json io1s.json

Per model and set: exact two-sided McNemar on paired `correct`, talk-quiet vs talk-false (the `primary` of each
summary), Holm over its 2 tests (one per set), PASS when the adjusted p < alpha and talk-false is less accurate.
A test with fewer than 32 pairs is INCOMPLETE and remains in its model's family. The 2 x 2 reading uses the
model's rr1 decision for the cell with both reports first and the instruction.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ALPHA = 0.05
ARMS = ("talk-quiet", "talk-false")
SETS = ("io1v", "io1s")
PLANNED_PAIRS = 32
RELIED_READING = {
    ("PASS", "FAIL"): "the order is enough",
    ("FAIL", "FAIL"): "it takes the order and the instruction together",
    ("FAIL", "PASS"): "the instruction alone",
    ("PASS", "PASS"): "each alone is enough",
}
NOT_RELIED_READING = {
    ("FAIL", "FAIL"): "no reliance in any cell",
    ("PASS", "FAIL"): "relies with the reports in the brief, though not in rr1",
    ("FAIL", "PASS"): "relies with the sentence and late reports, though not in rr1",
    ("PASS", "PASS"): "relies in both new cells, though not in rr1",
}


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adjusted = [0.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adjusted[i] = running
    return adjusted


def tests(path: Path, name: str, models: list[str]) -> dict[str, dict]:
    data = json.loads(path.read_text())
    found = {}
    for summary in data["summaries"]:
        primary = summary["primary"]
        if (primary["a"], primary["b_arm"]) != ARMS:
            raise SystemExit(f"{path}: primary is {primary['a']} vs {primary['b_arm']}, expected {ARMS}")
        if summary["model"] not in models:
            raise SystemExit(f"{path}: summary for {summary['model']}, which is not in the plan")
        found[summary["model"]] = {"pairs": primary["pairs"], "b": primary["b"], "c": primary["c"],
                                   "p": primary["p"], "rate_quiet": primary["rate_a"],
                                   "rate_false": primary["rate_b"], "pass_through": summary["pass_through"]}
    empty = {"pairs": 0, "b": 0, "c": 0, "p": 1.0, "rate_quiet": None, "rate_false": None, "pass_through": None}
    return {model: {"set": name, "model": model, **found.get(model, empty)} for model in models}


def read_reliance(path: Path, models: list[str]) -> dict[str, bool]:
    data = json.loads(path.read_text())
    decisions = {row["model"]: row["decision"] for row in data["models"]}
    for model in models:
        if decisions.get(model) not in ("PASS", "FAIL"):
            raise SystemExit(f"{path}: no settled rr1 decision for {model}")
    return {model: decisions[model] == "PASS" for model in models}


def describe(row: dict) -> str:
    rates = ("-" if row["rate_quiet"] is None else f"{row['rate_quiet']:.3f}",
             "-" if row["rate_false"] is None else f"{row['rate_false']:.3f}")
    pt = row["pass_through"]
    through = "-" if pt is None else f"{pt['w']}/{pt['n']}"
    return (f"{row['set']} {row['model']}: {row['pairs']} pairs, b {row['b']}, c {row['c']}, p {row['p']:.3g}, "
            f"Holm p {row['p_holm']:.3g}, correct {rates[0]} vs {rates[1]}, pass-through {through} "
            f"-> {row['decision']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--reliance", type=Path, required=True, help="rr1_decide.py --json output")
    parser.add_argument("io1v", type=Path)
    parser.add_argument("io1s", type=Path)
    args = parser.parse_args(argv)
    models = json.loads(args.plan.read_text())["models"]
    relied = read_reliance(args.reliance, models)
    sets = [tests(path, name, models) for name, path in zip(SETS, (args.io1v, args.io1s))]
    print(f"PROBE.md section 14 amendment: Holm over 2 tests per model, alpha {ALPHA}")
    for model in models:
        rows = [group[model] for group in sets]
        for row, p_holm in zip(rows, holm([row["p"] for row in rows])):
            row["p_holm"] = p_holm
            if row["pairs"] < PLANNED_PAIRS:
                row["decision"] = f"INCOMPLETE ({PLANNED_PAIRS - row['pairs']} pairs missing)"
            elif p_holm < ALPHA and row["rate_false"] < row["rate_quiet"]:
                row["decision"] = "PASS"
            else:
                row["decision"] = "FAIL"
            print(describe(row))
        pair = tuple(row["decision"] for row in rows)
        reading = RELIED_READING if relied[model] else NOT_RELIED_READING
        label = "relied in rr1" if relied[model] else "did not rely in rr1"
        print(f"{model} ({label}): {reading.get(pair, 'not decided (a test is incomplete)')}")


if __name__ == "__main__":
    main()
