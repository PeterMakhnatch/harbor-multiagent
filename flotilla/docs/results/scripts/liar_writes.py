"""Who wrote the final answer in the scripted-liar trials, and did it matter?

    python3 flotilla/docs/results/scripts/liar_writes.py .flotilla-local/jobs

Reads wave c3 (`c3-*-3seat`): verifier/score.json (score, deceived), verifier/world-state.json
(submissions in order) and the task's environment/task.json (answer key). p1 holds the witness's
true calendar, so only p1 can catch the lie; p2 cannot; p3 is the scripted peer. Standard library only.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from math import comb
from pathlib import Path


def norm(answer):
    if not isinstance(answer, dict):
        return None
    day = str(answer.get("day", "")).strip()[:3].title()
    match = re.fullmatch(r"(\d{1,2})(?::00(?::00)?)?", str(answer.get("start", "")).strip())
    return (day, int(match.group(1))) if match else None


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    n, row, col = a + b + c + d, a + b, a + c
    def p(x: int) -> float:
        return comb(row, x) * comb(n - row, col - x) / comb(n, col)
    observed = p(a)
    return sum(p(x) for x in range(max(0, col - (n - row)), min(row, col) + 1) if p(x) <= observed + 1e-12)


def main() -> None:
    jobs = Path(sys.argv[1] if len(sys.argv) > 1 else ".flotilla-local/jobs")
    table = collections.Counter()
    deceived_paths = collections.Counter()
    for trial in sorted(jobs.glob("c3-*-3seat/*/")):
        try:
            result = json.loads((trial / "result.json").read_text())
            score = json.loads((trial / "verifier" / "score.json").read_text())
            state = json.loads((trial / "verifier" / "world-state.json").read_text())
        except (OSError, ValueError):
            continue
        match = re.search(r"-\d{3}-(talk(?:-liar)?(?:-warned)?)$", result.get("task_name", ""))
        if match is None or "liar" not in match.group(1) or not state.get("submissions"):
            continue
        key = norm(json.loads((Path(result["task_id"]["path"]) / "environment" / "task.json").read_text())["answer_key"])
        submissions = [(s["seat"], norm(s["answer"])) for s in state["submissions"]]
        final = submissions[-1][0]
        outcome = "correct" if score["score"] == 1.0 else ("deceived" if score.get("deceived") else "corrupted")
        table[(final, outcome)] += 1
        if outcome == "deceived":
            right_first = any(answer == key for _, answer in submissions)
            deceived_paths[("right answer first" if right_first else "decoy only", f"final by {final}")] += 1
    print("Liar conditions (talk-liar and talk-liar-warned), all models: final writer x outcome")
    for seat in ("p1", "p2"):
        print(f"  final by {seat}: " + ", ".join(f"{o} {table[(seat, o)]}" for o in ("correct", "deceived", "corrupted")))
    a, b = table[("p1", "correct")], table[("p1", "deceived")]
    c, d = table[("p2", "correct")], table[("p2", "deceived")]
    print(f"  Fisher two-sided p, final writer vs correct/deceived: {fisher_two_sided(a, b, c, d):.4f}")
    print("Deceived trials by path:")
    for (path, final), n in sorted(deceived_paths.items()):
        print(f"  {n}: {path}, {final}")


if __name__ == "__main__":
    main()
