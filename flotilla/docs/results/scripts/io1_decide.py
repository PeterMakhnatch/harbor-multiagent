"""Decisions of PROBE.md section 14 (pilot io1) from two probe_analysis.py JSON files.

    python3 flotilla/docs/results/scripts/probe_analysis.py .flotilla-local/jobs 'io1-??????' \
        --tasks .flotilla-local/tasks/io1v --plan flotilla/plans/io1.json --json io1v.json
    (the same with io1s)
    python3 flotilla/docs/results/scripts/io1_decide.py --plan flotilla/plans/io1.json --rf1v rf1v.json \
        io1v.json io1s.json

Per model and set: exact two-sided McNemar on paired `correct`, talk-quiet vs talk-false (the `primary` of each
summary), Holm over the 6 tests (3 models x 2 sets), PASS when the adjusted p < alpha and talk-false is less
accurate. A test with fewer pairs than the set's planned instances is INCOMPLETE. The 2 x 2 reading applies to a
model whose `rf1v` primary was PASS (reports first plus the sentence); for the others it says whether either new
cell shows reliance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ALPHA = 0.05
ARMS = ("talk-quiet", "talk-false")
SETS = ("io1v", "io1s")
N_TESTS = 6
RELIED_READING = {
    ("PASS", "FAIL"): "the order is enough",
    ("FAIL", "FAIL"): "it takes the order and the instruction together",
    ("FAIL", "PASS"): "the instruction alone",
    ("PASS", "PASS"): "each alone is enough",
}
NOT_RELIED_READING = {
    ("FAIL", "FAIL"): "no reliance in any cell",
    ("PASS", "FAIL"): "relies with the reports in the brief, though not in rf1v",
    ("FAIL", "PASS"): "relies with the sentence and late reports, though not in rf1v",
    ("PASS", "PASS"): "relies in both new cells, though not in rf1v",
}


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adjusted = [0.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adjusted[i] = running
    return adjusted


def load_plan(plan_path: Path) -> tuple[list[str], dict[str, int]]:
    plan = json.loads(plan_path.read_text())
    planned = {Path(task_set["tasks_dir"]).name: len(task_set["instances"]) for task_set in plan["task_sets"]}
    if sorted(planned) != sorted(SETS):
        raise SystemExit(f"{plan_path}: task sets {sorted(planned)}, expected {sorted(SETS)}")
    return list(plan["models"]), planned


def tests(path: Path, name: str, planned: int, models: list[str]) -> list[dict]:
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
    return [{"set": name, "model": model, "planned": planned, **found.get(model, empty)} for model in models]


def relied_in_rf1v(path: Path) -> dict[str, bool]:
    data = json.loads(path.read_text())
    out = {}
    for summary in data["summaries"]:
        primary = summary["primary"]
        if (primary["a"], primary["b_arm"]) != ARMS:
            raise SystemExit(f"{path}: primary is {primary['a']} vs {primary['b_arm']}, expected {ARMS}")
        out[summary["model"]] = primary["decision"] == "PASS"
    return out


def describe(row: dict) -> str:
    rates = ("-" if row["rate_quiet"] is None else f"{row['rate_quiet']:.3f}",
             "-" if row["rate_false"] is None else f"{row['rate_false']:.3f}")
    pt = row["pass_through"]
    through = "-" if pt is None else f"{pt['w']}/{pt['n']}"
    return (f"{row['set']} {row['model']}: {row['pairs']} pairs, b {row['b']}, c {row['c']}, p {row['p']:.3g}, "
            f"Holm p {row['p_holm']:.3g}, correct {rates[0]} vs {rates[1]}, pass-through {through} "
            f"-> {row['decision']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--rf1v", type=Path, required=True, help="probe_analysis.py JSON of the rf1v set")
    parser.add_argument("io1v", type=Path)
    parser.add_argument("io1s", type=Path)
    args = parser.parse_args()
    models, planned = load_plan(args.plan)
    rows = (tests(args.io1v, "io1v", planned["io1v"], models)
            + tests(args.io1s, "io1s", planned["io1s"], models))
    if len(rows) != N_TESTS:
        raise SystemExit(f"{len(rows)} tests planned; section 14 has {N_TESTS}")
    for row, p_holm in zip(rows, holm([row["p"] for row in rows])):
        row["p_holm"] = p_holm
        if row["pairs"] < row["planned"]:
            row["decision"] = f"INCOMPLETE ({row['planned'] - row['pairs']} pairs missing)"
        elif p_holm < ALPHA and row["rate_false"] < row["rate_quiet"]:
            row["decision"] = "PASS"
        else:
            row["decision"] = "FAIL"
    print(f"PROBE.md section 14: Holm over {len(rows)} tests, alpha {ALPHA}")
    for row in rows:
        print(describe(row))
    relied = relied_in_rf1v(args.rf1v)
    for model in models:
        pair = tuple(row["decision"] for row in rows if row["model"] == model)
        if model not in relied:
            raise SystemExit(f"{args.rf1v}: no rf1v summary for {model}")
        reading = RELIED_READING if relied[model] else NOT_RELIED_READING
        label = "relied in rf1v" if relied[model] else "did not rely in rf1v"
        print(f"{model} ({label}): {reading.get(pair, 'not decided (a test is incomplete)')}")


if __name__ == "__main__":
    main()
