"""Commons wave analysis (docs/COMMONS.md section 14, pre-registered): the primary test, the secondary measures and
the real cost from tokens.

    python3 flotilla/docs/results/scripts/commons_wave.py .flotilla-local/jobs 'w1-*'

Reads every trial folder of the jobs whose names match the pattern: verifier/score.json (instance, condition, error),
verifier/world-state.json (the round log; every measure is recomputed with games/commons.py score_game) and
result.json (the task folder, seat token use, start time, exceptions). For each instance
and arm it uses the first valid trial (a verifier score, no Harbor exception, no verifier error). Host-side output.
Standard library only.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import random
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

# deepseek-flash (DeepSeek-V4.1-Flash), USD per million tokens, off-peak, checked 2026-11-20 on
# api-docs.deepseek.com/quick_start/pricing. Peak rates are double: 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday.
OFF_PEAK = {"hit": 0.003, "miss": 0.15, "out": 0.60}
PEAK_HOURS_UTC = ((1, 4), (6, 10))
ARMS = ("solo", "silent", "talk")
MIN_PAIRS = 10
_SPEC = importlib.util.spec_from_file_location("commons", Path(__file__).resolve().parents[3] / "games" / "commons.py")
COMMONS = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
_SPEC.loader.exec_module(COMMONS)  # type: ignore[union-attr]


def rate(started: datetime) -> float:
    """1 off-peak, 2 at peak (Chinese public holidays, which are off-peak, are not modelled: an upper bound)."""
    hour = started.hour + started.minute / 60
    return 2.0 if started.weekday() < 5 and any(a <= hour < b for a, b in PEAK_HOURS_UTC) else 1.0


def tokens_and_cost(result: dict) -> tuple[dict[str, int], float]:
    started = result.get("started_at")
    when = datetime.fromisoformat(started).astimezone(UTC) if started else datetime.now(UTC)
    tokens = {"hit": 0, "miss": 0, "out": 0}
    for seat in result.get("seat_results") or []:
        usage = seat.get("agent_result") or {}
        cached, total = usage.get("n_cache_tokens") or 0, usage.get("n_input_tokens") or 0
        tokens["hit"] += cached
        tokens["miss"] += max(0, total - cached)  # Harbor's input count includes the cached tokens
        tokens["out"] += usage.get("n_output_tokens") or 0
    return tokens, rate(when) * sum(tokens[k] * OFF_PEAK[k] for k in tokens) / 1e6


def trials(jobs: Path, pattern: str, scorer=None) -> list[dict]:
    """Every trial of the matching jobs; `scorer` recomputes the measures (default: commons score_game)."""
    scorer = scorer or COMMONS.score_game
    found = []
    for trial in sorted(jobs.glob(f"{pattern}/*/")):
        if not (trial / "result.json").is_file():
            continue
        result = json.loads((trial / "result.json").read_text())
        tokens, cost = tokens_and_cost(result)
        score_path, state_path = trial / "verifier" / "score.json", trial / "verifier" / "world-state.json"
        verdict = json.loads(score_path.read_text()) if score_path.is_file() else {}
        failed = [seat.get("seat") for seat in result.get("seat_results") or [] if seat.get("exception_info")]
        valid = (bool(verdict) and state_path.is_file() and verdict.get("error") is None
                 and not result.get("exception_info") and not failed)
        score = dict(verdict)
        if valid:  # every measure from the frozen round log, whatever verifier version wrote score.json
            task = json.loads((Path(result["task_id"]["path"]) / "environment" / "task.json").read_text())
            log = (json.loads(state_path.read_text()).get("game") or {}).get("log") or []
            score |= scorer(task["game"], log)
        found.append({"job": trial.parent.name, "trial": trial.name, "started": result.get("started_at") or "",
                      "score": score, "valid": valid, "tokens": tokens, "cost": cost,
                      "instance": score.get("instance"), "arm": score.get("condition")})
    return found


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact McNemar: the binomial(b + c, 1/2) probability of a split at least as uneven as b : c."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n)


def sign_flip(differences: list[float]) -> float:
    """Two-sided exact sign-flip p for the mean of paired differences."""
    observed = abs(statistics.fmean(differences))
    flips = list(itertools.product((1, -1), repeat=len(differences)))
    extreme = sum(abs(statistics.fmean([s * d for s, d in zip(signs, differences, strict=True)])) >= observed - 1e-12
                  for signs in flips)
    return extreme / len(flips)


def bootstrap(differences: list[float], resamples: int = 10_000, seed: int = 1) -> tuple[float, float]:
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(differences, k=len(differences))) for _ in range(resamples))
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


def main() -> None:
    jobs, pattern = Path(sys.argv[1]), sys.argv[2]
    rows = trials(jobs, pattern)
    chosen: dict[tuple[int, str], dict] = {}
    for row in sorted(rows, key=lambda r: r["started"]):
        if row["valid"] and (row["instance"], row["arm"]) not in chosen:
            chosen[(row["instance"], row["arm"])] = row
    instances = sorted({instance for instance, _arm in chosen})
    print(f"trials: {len(rows)} run, {sum(r['valid'] for r in rows)} valid; instances with a valid trial: {len(instances)}")
    print("\n| arm | trials | survived | mean sustained share | mean collapse round | over-use | missed | response |")
    print("|---|---|---|---|---|---|---|---|")
    for arm in ARMS:
        scores = [row["score"] for (_i, a), row in chosen.items() if a == arm]
        if not scores:
            continue
        collapses = [s["collapsed_in"] for s in scores if s.get("collapsed_in")]
        responses = [s["response"] for s in scores if s.get("response") is not None]
        print(f"| {arm} | {len(scores)} | {sum(bool(s['survived']) for s in scores)} | "
              f"{statistics.fmean(s['reward'] for s in scores):.3f} | "
              f"{statistics.fmean(collapses) if collapses else float('nan'):.1f} | "
              f"{statistics.fmean(s['over_use'] for s in scores):.3f} | {statistics.fmean(s['missed'] for s in scores):.3f} | "
              f"{statistics.fmean(responses) if responses else float('nan'):.2f} ({len(responses)}) |")
    pairs = [i for i in instances if (i, "talk") in chosen and (i, "silent") in chosen]
    b = sum(chosen[(i, "talk")]["score"]["survived"] and not chosen[(i, "silent")]["score"]["survived"] for i in pairs)
    c = sum(chosen[(i, "silent")]["score"]["survived"] and not chosen[(i, "talk")]["score"]["survived"] for i in pairs)
    p = exact_mcnemar(b, c)
    if len(pairs) < MIN_PAIRS:
        verdict = f"INCONCLUSIVE ({len(pairs)} complete pairs, fewer than {MIN_PAIRS})"
    elif b > c and p <= 0.05:
        verdict = "PASS: talk kept the lake alive more often than silent"
    elif c > b and p <= 0.05:
        verdict = "FAIL, reversed: silent kept the lake alive more often than talk"
    else:
        verdict = "FAIL"
    print(f"\nPrimary test (exact McNemar, survived, talk vs silent): {len(pairs)} pairs; only talk survived b = {b}; "
          f"only silent survived c = {c}; two-sided p = {p:.4f}. {verdict}")
    for left, right in (("talk", "silent"), ("solo", "silent"), ("solo", "talk")):
        both = [i for i in instances if (i, left) in chosen and (i, right) in chosen]
        if len(both) < 2:
            continue
        diffs = [chosen[(i, left)]["score"]["reward"] - chosen[(i, right)]["score"]["reward"] for i in both]
        low, high = bootstrap(diffs)
        print(f"Sustained share, {left} - {right}: {len(both)} pairs, mean {statistics.fmean(diffs):+.3f}, "
              f"95% bootstrap [{low:+.3f}, {high:+.3f}], exact sign-flip p = {sign_flip(diffs):.4f}")
    means = {arm: statistics.fmean(r["score"]["reward"] for (_i, a), r in chosen.items() if a == arm)
             for arm in ARMS if any(a == arm for _i, a in chosen)}
    if len(means) == 3 and means["solo"] != means["silent"]:
        print(f"Conflict cost (solo - silent) {means['solo'] - means['silent']:+.3f}; talk gain (talk - silent) "
              f"{means['talk'] - means['silent']:+.3f}; recovery {(means['talk'] - means['silent']) / (means['solo'] - means['silent']):.2f}")
    total = sum(r["cost"] for r in rows)
    by_arm = {arm: sum(r["cost"] for r in rows if r["arm"] == arm) for arm in ARMS}
    tokens = {k: sum(r["tokens"][k] for r in rows) for k in OFF_PEAK}
    print(f"\nReal cost from tokens (all {len(rows)} runs, invalid ones included): ${total:.4f} "
          f"({', '.join(f'{arm} ${cost:.4f}' for arm, cost in by_arm.items())}); tokens: cached input {tokens['hit']:,}, "
          f"other input {tokens['miss']:,}, output {tokens['out']:,}")


if __name__ == "__main__":
    main()
