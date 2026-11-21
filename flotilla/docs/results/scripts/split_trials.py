"""Split pilot analysis (docs/SPLIT.md sections 4 and 6, pre-registered): each trial round by round, the validity
test, the pilot's pass or fail tests and the real cost from tokens.

    python3 flotilla/docs/results/scripts/split_trials.py .flotilla-local/jobs 'sp1-??????' sp1
    python3 flotilla/docs/results/scripts/split_trials.py .flotilla-local/jobs 'sp2-??????' sp2

Recomputes every measure from the frozen round log with games/split.py score_game. Host-side output (it prints the
private outside options). Standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


SPLIT = _load("split", HERE.parents[3] / "games" / "split.py")
WAVE = _load("wave", HERE.parent / "commons_wave.py")  # trial reading and the token price
MAX_MISSED = 0.10
PLANS = {"sp1": (6, 0.50), "sp2": (8, 0.40), "sp3": (12, 0.70), "sp4": (6, 0.40)}  # plan -> (trials, cost cap $)


def equal_first_round_deal(row: dict) -> bool:
    """A deal in round 1 whose claims differ by at most 1 point: the focal equal split of pilot sp1."""
    score = row["split"]
    claims = list(row["log"][0]["requests"].values()) if row["log"] else []
    return bool(score["agreed"] and score["deal_round"] == 1 and claims and max(claims) - min(claims) <= 1)


def sp1_tests(valid: list[dict]) -> None:
    fast = [r for r in valid if r["arm"] == "talk"]
    slow = [r for r in valid if r["arm"] == "silent"]
    talk_ok = len(fast) == 3 and all(r["split"]["agreed"] and r["split"]["deal_round"] <= 2 for r in fast)
    silent_ok = any(not r["split"]["agreed"] or r["split"]["deal_round"] > 2 for r in slow)
    print(f"Behavior prediction: every talk trial deals by round 2: {talk_ok}; at least 1 silent trial does not: "
          f"{silent_ok}: {'PASS' if talk_ok and silent_ok else 'FAIL'}")


def sp2_tests(valid: list[dict]) -> None:
    unequal = [r for r in valid if not equal_first_round_deal(r)]
    print(f"Ceiling test: {len(unequal)} of {len(valid)} valid trials do not end in an equal split in round 1 "
          f"(PASS needs at least 3 of 8): {'PASS' if len(valid) == 8 and len(unequal) >= 3 else 'FAIL'}")
    by_instance: dict[int, dict[str, float]] = {}
    for r in valid:
        by_instance.setdefault(r["instance"], {})[r["arm"]] = r["split"]["reward"]
    pairs = {i: arms for i, arms in by_instance.items() if set(arms) == {"silent", "talk"}}
    better = sum(arms["talk"] >= arms["silent"] for arms in pairs.values())
    print(f"Talk observation: talk efficiency >= silent in {better} of {len(pairs)} instances (predicted: at least "
          f"3 of 4): {'HOLDS' if len(pairs) == 4 and better >= 3 else 'DOES NOT HOLD'}")
    for r in valid:
        game = r["task"]["game"]
        strong = max(game["outside"], key=game["outside"].get)
        print(f"instance {r['instance']} {r['arm']}: strong seat {strong} (outside {game['outside'][strong]} of "
              f"{game['capacity']}) got {r['split']['totals'][strong]}; below outside: {r['split']['below_outside']}")


def sp3_tests(valid: list[dict]) -> None:
    deals: dict[int, dict[str, bool]] = {}
    for r in valid:
        deals.setdefault(r["instance"], {})[r["arm"]] = bool(r["split"]["agreed"])
    pairs = [arms for arms in deals.values() if set(arms) == {"silent", "talk"}]
    b = sum(arms["talk"] and not arms["silent"] for arms in pairs)
    c = sum(arms["silent"] and not arms["talk"] for arms in pairs)
    p = WAVE.exact_mcnemar(b, c)
    verdict = ("INCONCLUSIVE" if len(pairs) < 6 else "PASS" if b > c and p <= 0.05 else "FAIL")
    print(f"Primary test: {len(pairs)} complete pairs, only talk dealt b = {b}, only silent dealt c = {c}, exact "
          f"McNemar p = {p:.4f}: {verdict}")
    shares = [r["split"]["surplus_shares"] for r in valid if r["arm"] == "talk" and r["split"]["surplus_shares"]]
    strong = []
    for r in valid:
        game = r["task"]["game"]
        if r["arm"] == "talk" and r["split"]["surplus_shares"]:
            strong.append(r["split"]["surplus_shares"][max(game["outside"], key=game["outside"].get)])
    print(f"Strong seat's surplus share in talk deals: {[round(s, 3) for s in strong]}"
          + (f", mean {sum(strong) / len(strong):.3f}" if strong else "") + f" ({len(shares)} deals)")
    for arm in ("silent", "talk"):
        rows = [r for r in valid if r["arm"] == arm]
        if rows:
            print(f"{arm}: passed {sum(r['split']['passed'] for r in rows) / len(rows):.3f}, missed "
                  f"{sum(r['split']['missed'] for r in rows) / len(rows):.3f} (mean share of seat-rounds per trial)")


def load_rows(jobs: Path, pattern: str) -> list[dict]:
    rows = []
    for row in WAVE.trials(jobs, pattern, scorer=SPLIT.score_game):  # valid: a score, no error, no exception
        trial = jobs / row["job"] / row["trial"]
        result = json.loads((trial / "result.json").read_text())
        task = json.loads((Path(result["task_id"]["path"]) / "environment" / "task.json").read_text())
        state = json.loads((trial / "verifier" / "world-state.json").read_text()) if row["valid"] else {}
        log = (state.get("game") or {}).get("log") or []
        score = SPLIT.score_game(task["game"], log) if row["valid"] else {}
        rows.append({**row, "task": task, "split": score, "messages": state.get("messages") or [], "log": log})
    return rows


def sp4_tests(valid: list[dict], jobs: Path) -> None:
    """Open arm (sp4) against the sp3 silent and talk trials of the same instances (same seed)."""
    earlier = [r for r in load_rows(jobs, "sp3-??????") if r["valid"]]
    by: dict[int, dict[str, dict]] = {}
    for r in [*earlier, *valid]:
        by.setdefault(r["instance"], {})[r["arm"]] = r["split"]
    for game in sorted({r["instance"] for r in valid}):
        arms = by[game]
        print(f"instance {game}: " + ", ".join(
            f"{arm} deal {arms[arm]['agreed']} round {arms[arm]['deal_round']} efficiency {arms[arm]['reward']:.3f}"
            for arm in ("silent", "open", "talk") if arm in arms))
    pairs = [arms for arms in by.values() if {"open", "silent"} <= set(arms)]
    b = sum(arms["open"]["agreed"] and not arms["silent"]["agreed"] for arms in pairs)
    c = sum(arms["silent"]["agreed"] and not arms["open"]["agreed"] for arms in pairs)
    p = WAVE.exact_mcnemar(b, c)
    verdict = "INCONCLUSIVE" if len(pairs) < 6 else "PASS" if b > c and p <= 0.05 else "FAIL"
    print(f"Primary test (open vs sp3 silent, deal): {len(pairs)} pairs, b = {b}, c = {c}, exact McNemar p = "
          f"{p:.4f}: {verdict}")
    talk = [by[game] for game in sorted(by) if {"open", "talk"} <= set(by[game])]
    gaps = [arms["talk"]["reward"] - arms["open"]["reward"] for arms in talk]
    print(f"Talk - open efficiency per instance: {[round(g, 3) for g in gaps]}"
          + (f", mean {sum(gaps) / len(gaps):+.3f}" if gaps else ""))
    strong = []
    for r in valid:
        game = r["task"]["game"]
        if r["split"]["surplus_shares"]:
            strong.append(r["split"]["surplus_shares"][max(game["outside"], key=game["outside"].get)])
    print(f"Strong seat's surplus share in open deals: {[round(s, 3) for s in strong]}")


def main() -> None:
    jobs, pattern = Path(sys.argv[1]), sys.argv[2]
    plan = sys.argv[3] if len(sys.argv) > 3 else "sp1"
    planned, max_cost = PLANS[plan]
    tests = {"sp1": sp1_tests, "sp2": sp2_tests, "sp3": sp3_tests, "sp4": lambda v: sp4_tests(v, jobs)}[plan]
    rows = load_rows(jobs, pattern)
    for row in sorted(rows, key=lambda r: (r["instance"] if r["instance"] is not None else -1, r["arm"] or "")):
        game, score = row["task"]["game"], row["split"]
        print(f"instance {row['instance']} {row['arm']}: pot {game['capacity']}, outside {game['outside']}, "
              f"valid {row['valid']}, cost ${row['cost']:.4f}")
        for record in row["log"]:
            passed = f", passed {record['passed']}" if "passed" in record else ""
            print(f"    round {record['round']}: pot {record['stock']}, claims {record['requests']}, "
                  f"deal {record['agreed']}, missed {record['missed']}{passed}")
        if score:
            passed = f", passed {score['passed']:.2f}" if any("passed" in record for record in row["log"]) else ""
            print(f"    deal {score['agreed']} in round {score['deal_round']}, payoff {score['totals']}, efficiency "
                  f"{score['reward']:.3f}, surplus {score['surplus_at_deal']}, shares {score['surplus_shares']}, "
                  f"below outside {score['below_outside']}, left {score['left_on_table']}, "
                  f"missed {score['missed']:.2f}{passed}")
        for message in row["messages"]:
            print(f"    message {message.get('from')}->{message.get('to')} at {message.get('t')} s: "
                  f"{str(message.get('text'))[:300]!r}")
    valid = [r for r in rows if r["valid"]]
    seat_rounds = sum(len(r["log"]) * len(r["task"]["game"]["seats"]) for r in valid)
    missed = sum(r["split"]["missed"] * len(r["log"]) * len(r["task"]["game"]["seats"]) for r in valid)
    cost = sum(r["cost"] for r in rows)
    missed_share = missed / seat_rounds if seat_rounds else 1.0
    validity = len(rows) == planned and len(valid) == planned and missed_share <= MAX_MISSED and cost <= max_cost
    print(f"\nValidity test: {len(valid)} of {len(rows)} trials valid ({planned} planned), missed {missed_share:.3f} "
          f"of seat-rounds, real cost ${cost:.4f} (cap ${max_cost:.2f}): {'PASS' if validity else 'FAIL'}")
    if any("passed" in record for row in valid for record in row["log"]):
        passed = sum(r["split"]["passed"] * len(r["log"]) * len(r["task"]["game"]["seats"]) for r in valid)
        print(f"Passed {passed / seat_rounds:.3f} of seat-rounds.")
    tests(valid)
    for arm in ("silent", "talk", "open"):
        scores = [r["split"] for r in valid if r["arm"] == arm]
        if scores:
            print(f"{arm}: deals {sum(s['agreed'] for s in scores)} of {len(scores)}, mean efficiency "
                  f"{sum(s['reward'] for s in scores) / len(scores):.3f}, deal rounds "
                  f"{[s['deal_round'] for s in scores]}")


if __name__ == "__main__":
    main()
