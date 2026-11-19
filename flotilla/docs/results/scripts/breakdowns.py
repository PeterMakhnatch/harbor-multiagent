"""Breakdowns behind RESULTS.md that `flotilla-analyze` does not print (R3, R4, c5, the v1 liar pilot).

    python3 flotilla/docs/results/scripts/breakdowns.py .flotilla-local/jobs

Reads only trial folders: result.json, verifier/score.json, verifier/world-state.json and
artifacts/logs/world/{events,messages}.jsonl. Standard library only. Prints plain text.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

DATA = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri)[a-z]*[ :]*\d{1,2}", re.IGNORECASE)  # a day followed by an hour
MODELS = {"ds": "DeepSeek V4 Flash", "qwen36": "Qwen 3.6 35B-A3B", "gptoss": "GPT-OSS-120B"}


def trials(jobs: Path, job: str, condition: str | None = None):
    for trial in sorted((jobs / job).glob("*/")):
        try:
            result = json.loads((trial / "result.json").read_text())
            score = json.loads((trial / "verifier" / "score.json").read_text())
        except (OSError, ValueError):
            continue
        match = re.search(r"-(\d{3})-([a-z-]+)$", result.get("task_name", ""))
        if match is None or (condition and match.group(2) != condition):
            continue
        state = json.loads((trial / "verifier" / "world-state.json").read_text())
        key = score.get("answer") if score.get("score") == 1.0 else None
        task_json = Path(result["task_id"]["path"]) / "environment" / "task.json"
        if task_json.exists():
            key = json.loads(task_json.read_text())["answer_key"]
        world = trial / "artifacts" / "logs" / "world"
        events = [json.loads(line) for line in (world / "events.jsonl").read_text().splitlines()] \
            if (world / "events.jsonl").exists() else []
        yield {
            "name": trial.name, "instance": int(match.group(1)), "score": score["score"], "key": key,
            "deceived": score.get("deceived"), "submissions": state.get("submissions") or [],
            "messages": state.get("messages") or [], "events": events,
        }


def r3(jobs: Path) -> None:
    print("R3: talk trials of wave c1 (found = the right answer was submitted at some point)")
    for slug, model in MODELS.items():
        rows = list(trials(jobs, f"c1-{slug}-2seat", "talk"))
        found = lost = overwrote = had_data = announced_won = announced_lost = won = 0
        for t in rows:
            right = [s for s in t["submissions"] if s["answer"] == t["key"]]
            if not right:
                continue
            found += 1
            first = right[0]
            solver = first["seat"]
            teammate_data = any(m["from"] != solver and DATA.search(m["text"]) and m.get("t", 0) <= first.get("t", 0)
                                for m in t["messages"])
            had_data += teammate_data
            announced = any(m["from"] == solver and t["key"]["day"] in m["text"] and t["key"]["start"][:2] in m["text"]
                            and m.get("t", 0) >= first.get("t", 0) - 5 for m in t["messages"])
            if t["score"] < 1.0:
                lost += 1
                announced_lost += announced
                overwrote += any(s["seat"] != solver and s["answer"] != t["key"] and s.get("t", 0) > first.get("t", 0)
                                 for s in t["submissions"])
            else:
                won += 1
                announced_won += announced
        print(f"  {model}: {len(rows)} trials, found {found}, lost {lost}, lost with a later wrong write by the "
              f"teammate {overwrote}, first right submitter had teammate data {had_data} of {found}, "
              f"solver announced the answer: won {announced_won}/{won}, lost {announced_lost}/{lost}")


def c5(jobs: Path) -> None:
    print("c5: submitter role (only p1 submits) against c1 talk, same instances")
    for slug, model in MODELS.items():
        for job, label in ((f"c1-{slug}-2seat", "c1 talk"), (f"c5-{slug}-2seat", "c5 role")):
            rows = list(trials(jobs, job, "talk"))
            if not rows:
                continue
            p2 = sum(1 for t in rows if any(s["seat"] == "p2" for s in t["submissions"]))
            mean = sum(t["score"] for t in rows) / len(rows)
            print(f"  {model} {label}: {len(rows)} trials, mean {mean:.3f}, p2 submitted in {p2}")


def r4(jobs: Path) -> None:
    print("R4: narrow channel (wave c4); accepted messages, messages with no day-and-hour data, 413 on /messages")
    for slug, model in MODELS.items():
        rows = list(trials(jobs, f"c4-{slug}-2seat"))
        messages = [m for t in rows for m in t["messages"]]
        no_data = sum(1 for m in messages if not DATA.search(m["text"]))
        too_long = sum(1 for t in rows for e in t["events"] if e["kind"] == "invalid"
                       and (e.get("data") or {}).get("path") == "/messages" and (e.get("data") or {}).get("status") == 413)
        mean = sum(t["score"] for t in rows) / len(rows) if rows else float("nan")
        print(f"  {model}: {len(rows)} trials, mean {mean:.3f}, accepted messages {len(messages)}, "
              f"with no calendar data {no_data}, rejected as too long {too_long}")


def pilot(jobs: Path) -> None:
    print("v1 liar pilot (wave wL, world v1, confounded): talk-liar trials")
    for job in sorted(p.name for p in jobs.glob("wL-*")):
        rows = list(trials(jobs, job, "talk-liar"))
        correct = sum(1 for t in rows if t["score"] == 1.0)
        deceived = sum(1 for t in rows if t["deceived"] is True)
        print(f"  {job}: {len(rows)} scored, correct {correct}, deceived {deceived}")


def main() -> None:
    jobs = Path(sys.argv[1] if len(sys.argv) > 1 else ".flotilla-local/jobs")
    for part in (r3, c5, r4, pilot):
        part(jobs)
        print()


if __name__ == "__main__":
    main()
