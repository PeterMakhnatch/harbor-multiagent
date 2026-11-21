"""Split pilot analysis (docs/SPLIT.md section 4, pre-registered): each trial round by round, the validity test,
the behavior prediction and the real cost from tokens.

    python3 flotilla/docs/results/scripts/split_trials.py .flotilla-local/jobs 'sp1-*'

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
MAX_MISSED, MAX_COST = 0.10, 0.50


def main() -> None:
    jobs, pattern = Path(sys.argv[1]), sys.argv[2]
    rows = []
    for row in WAVE.trials(jobs, pattern):  # valid = a verifier score, no error, no Harbor exception
        trial = jobs / row["job"] / row["trial"]
        result = json.loads((trial / "result.json").read_text())
        task = json.loads((Path(result["task_id"]["path"]) / "environment" / "task.json").read_text())
        state = json.loads((trial / "verifier" / "world-state.json").read_text()) if row["valid"] else {}
        log = (state.get("game") or {}).get("log") or []
        score = SPLIT.score_game(task["game"], log) if row["valid"] else {}
        rows.append({**row, "task": task, "split": score, "messages": state.get("messages") or [], "log": log})
    for row in sorted(rows, key=lambda r: (r["instance"] if r["instance"] is not None else -1, r["arm"] or "")):
        game, score = row["task"]["game"], row["split"]
        print(f"instance {row['instance']} {row['arm']}: pot {game['capacity']}, outside {game['outside']}, "
              f"valid {row['valid']}, cost ${row['cost']:.4f}")
        for record in row["log"]:
            print(f"    round {record['round']}: pot {record['stock']}, claims {record['requests']}, "
                  f"deal {record['agreed']}, missed {record['missed']}")
        if score:
            print(f"    deal {score['agreed']} in round {score['deal_round']}, payoff {score['totals']}, efficiency "
                  f"{score['reward']:.3f}, surplus {score['surplus_at_deal']}, shares {score['surplus_shares']}, "
                  f"below outside {score['below_outside']}, left {score['left_on_table']}, missed {score['missed']:.2f}")
        for message in row["messages"]:
            print(f"    message {message.get('from')}->{message.get('to')} at {message.get('t')} s: "
                  f"{str(message.get('text'))[:300]!r}")
    valid = [r for r in rows if r["valid"]]
    seat_rounds = sum(len(r["log"]) * len(r["task"]["game"]["seats"]) for r in valid)
    missed = sum(r["split"]["missed"] * len(r["log"]) * len(r["task"]["game"]["seats"]) for r in valid)
    cost = sum(r["cost"] for r in rows)
    missed_share = missed / seat_rounds if seat_rounds else 1.0
    validity = len(rows) == 6 and len(valid) == 6 and missed_share <= MAX_MISSED and cost <= MAX_COST
    print(f"\nValidity test: {len(valid)} of {len(rows)} trials valid (6 planned), missed {missed_share:.3f} of "
          f"seat-rounds, real cost ${cost:.4f}: {'PASS' if validity else 'FAIL'}")
    fast = [r for r in valid if r["arm"] == "talk"]
    slow = [r for r in valid if r["arm"] == "silent"]
    talk_ok = len(fast) == 3 and all(r["split"]["agreed"] and r["split"]["deal_round"] <= 2 for r in fast)
    silent_ok = any(not r["split"]["agreed"] or r["split"]["deal_round"] > 2 for r in slow)
    print(f"Behavior prediction: every talk trial deals by round 2: {talk_ok}; at least 1 silent trial does not: "
          f"{silent_ok}: {'PASS' if talk_ok and silent_ok else 'FAIL'}")
    for arm in ("silent", "talk"):
        scores = [r["split"] for r in valid if r["arm"] == arm]
        if scores:
            print(f"{arm}: deals {sum(s['agreed'] for s in scores)} of {len(scores)}, mean efficiency "
                  f"{sum(s['reward'] for s in scores) / len(scores):.3f}, deal rounds "
                  f"{[s['deal_round'] for s in scores]}")


if __name__ == "__main__":
    main()
