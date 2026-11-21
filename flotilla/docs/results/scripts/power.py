"""How many instances each comparison in RESULTS.md needs, from the observed paired outcomes.

    python3 flotilla/docs/results/scripts/power.py .flotilla-local/jobs

For each comparison, per instance d = outcome(A) - outcome(B) is +1, -1 or 0 (one trial per instance
and arm). The observed shares of +1, -1 and 0 are resampled for N instances; the exact two-sided
sign-flip test (for binary data: the exact binomial test on the discordant instances) is applied;
power = share of draws with p < 0.05. The result is the smallest N in the grid with power >= 0.8.
Costs use the observed mean model cost per trial of the 2 arms. Standard library only; seed fixed.
"""

from __future__ import annotations

import json
import random
import sys
from math import comb
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import breakdowns as b  # noqa: E402

ALPHA, POWER, DRAWS = 0.05, 0.8, 4000
GRID = [12, 16, 20, 24, 32, 40, 48, 64, 80, 96, 128, 160, 200, 256]


def exact_p(plus: int, minus: int) -> float:
    k = plus + minus
    if k == 0:
        return 1.0
    low = min(plus, minus)
    tail = sum(comb(k, i) for i in range(low + 1)) / 2**k
    return min(1.0, 2 * tail)


def power(p_plus: float, p_minus: float, n: int, rng: random.Random) -> float:
    hits = 0
    for _ in range(DRAWS):
        plus = minus = 0
        for _ in range(n):
            u = rng.random()
            if u < p_plus:
                plus += 1
            elif u < p_plus + p_minus:
                minus += 1
        hits += exact_p(plus, minus) < ALPHA
    return hits / DRAWS


def arm(jobs: Path, job: str, condition: str | None) -> dict[int, dict]:
    out = {}
    for t in b.trials(jobs, job, condition):
        result = json.loads((jobs / job / t["name"] / "result.json").read_text())
        cost = sum(((s.get("agent_result") or {}).get("cost_usd") or 0) for s in result.get("seat_results") or [])
        out[t["instance"]] = {"y": 1 if t["score"] >= 1.0 else 0, "deceived": t["deceived"] is True, "cost": cost}
    return out


def compare(name: str, a: dict[int, dict], c: dict[int, dict], key: str, rng: random.Random) -> None:
    pairs = sorted(set(a) & set(c))
    if not pairs:
        return
    diffs = [int(a[i][key]) - int(c[i][key]) for i in pairs]
    plus, minus = diffs.count(1), diffs.count(-1)
    n = len(pairs)
    p_plus, p_minus = plus / n, minus / n
    needed = next((g for g in GRID if power(p_plus, p_minus, g, rng) >= POWER), None)
    cost = sum(a[i]["cost"] + c[i]["cost"] for i in pairs) / n  # both arms, per instance
    effect = (plus - minus) / n
    print(f"| {name} | {n} | {effect:+.3f} | {plus}/{minus} | {exact_p(plus, minus):.3f} | "
          f"{needed if needed else '> ' + str(GRID[-1])} | "
          f"{'$%.2f' % (cost * needed) if needed else 'n/a'} |")


def main() -> None:
    jobs = Path(sys.argv[1] if len(sys.argv) > 1 else ".flotilla-local/jobs")
    rng = random.Random(1)
    print("| Comparison | instances now | effect | +/− instances | exact p now | instances for 80% power | model cost at that N |")
    print("|---|---|---|---|---|---|---|")
    for slug, model in b.MODELS.items():
        talk, silent = arm(jobs, f"c1-{slug}-2seat", "talk"), arm(jobs, f"c1-{slug}-2seat", "silent")
        compare(f"R1 gain, {model}: talk − silent", talk, silent, "y", rng)
    for slug, model in b.MODELS.items():
        talk = arm(jobs, f"c1-{slug}-2seat", "talk")
        compare(f"R4, {model}: talk − narrow channel", talk, arm(jobs, f"c4-{slug}-2seat", None), "y", rng)
        compare(f"R3, {model}: submitter role − talk", arm(jobs, f"c5-{slug}-2seat", None), talk, "y", rng)
    for slug, model in (("ds", "DeepSeek V4 Flash"), ("qwen36", "Qwen 3.6 35B-A3B")):
        job = f"c3-{slug}-3seat"
        honest, liar = arm(jobs, job, "talk"), arm(jobs, job, "talk-liar")
        warned, liar_warned = arm(jobs, job, "talk-warned"), arm(jobs, job, "talk-liar-warned")
        compare(f"R5 lie effect, {model}: honest − liar", honest, liar, "y", rng)
        compare(f"R5 warning benefit, {model}: liar warned − liar", liar_warned, liar, "y", rng)
        compare(f"R5 deception cut, {model}: deceived(liar) − deceived(liar warned)", liar, liar_warned, "deceived", rng)


if __name__ == "__main__":
    main()
