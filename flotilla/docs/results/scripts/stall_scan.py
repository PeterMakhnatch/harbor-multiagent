"""Stall scan: seats whose agent went quiet for a long time between two turns, grouped into events.

    python3 flotilla/docs/results/scripts/stall_scan.py .flotilla-local/jobs 'xm1-??????' ['pv10-??????' ...] \
        [--threshold 500] [--window 120] [--json out.json]

Every seat of every trial of the matching jobs (JOBS_DIR/<job>/<trial>/seats/<seat>/agent/trajectory.json, ATIF)
gives its model (agent.model_name) and its step timestamps (steps[].timestamp, sorted). A seat stalls when two
consecutive timestamps are more than --threshold seconds apart; only its first such gap counts. Gaps are sorted by
start, and a gap that starts within --window seconds of the previous one joins its event. For each event, per model:
seats stalled in the event / seats in flight at the event's median gap start (first step <= t <= last step).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path


def stamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def utc(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M:%S")


def first_gap(times: list[float], threshold: float) -> tuple[float, float] | None:
    for before, after in zip(times, times[1:]):
        if after - before > threshold:
            return before, after - before
    return None


def seats_of(jobs_dir: Path, patterns: list[str], threshold: float) -> list[dict]:
    seen, seats = set(), []
    for pattern in patterns:
        for job in sorted(jobs_dir.glob(pattern)):
            if not job.is_dir() or job in seen:
                continue
            seen.add(job)
            for path in sorted(job.glob("*/seats/*/agent/trajectory.json")):
                data = json.loads(path.read_text())
                times = sorted(stamp(step["timestamp"]) for step in data.get("steps", []) if step.get("timestamp"))
                if not times:
                    continue
                gap = first_gap(times, threshold)
                seats.append({
                    "job": job.name, "trial": path.parents[3].name, "seat": path.parents[1].name,
                    "model": (data.get("agent") or {}).get("model_name") or "unknown",
                    "first": times[0], "last": times[-1],
                    "gap_start": gap[0] if gap else None, "gap_s": gap[1] if gap else None,
                })
    return seats


def events_of(seats: list[dict], window: float) -> list[dict]:
    stalled = sorted((seat for seat in seats if seat["gap_start"] is not None), key=lambda seat: seat["gap_start"])
    groups: list[list[dict]] = []
    for seat in stalled:
        if groups and seat["gap_start"] - groups[-1][-1]["gap_start"] <= window:
            groups[-1].append(seat)
        else:
            groups.append([seat])
    events = []
    for group in groups:
        middle = statistics.median(seat["gap_start"] for seat in group)
        models: dict[str, dict[str, int]] = {}
        for seat in seats:
            row = models.setdefault(seat["model"], {"stalled": 0, "in_flight": 0})
            row["in_flight"] += seat["first"] <= middle <= seat["last"]
        for seat in group:
            models[seat["model"]]["stalled"] += 1
        events.append({
            "start": group[0]["gap_start"], "end": group[-1]["gap_start"], "median": middle,
            "seats": len(group), "trials": len({(seat["job"], seat["trial"]) for seat in group}),
            "gap_min_s": min(seat["gap_s"] for seat in group), "gap_max_s": max(seat["gap_s"] for seat in group),
            "models": {model: models[model] for model in sorted(models)},
        })
    return events


def report(seats: list[dict], events: list[dict], threshold: float) -> str:
    stalled = sorted((seat for seat in seats if seat["gap_start"] is not None), key=lambda seat: seat["gap_start"])
    trials = {(seat["job"], seat["trial"]) for seat in seats}
    lines = [f"{len(seats)} seats in {len(trials)} trials scanned, threshold {threshold:g} s"]
    if not stalled:
        lines.append("no stalls")
        return "\n".join(lines)
    stalled_trials = {(seat["job"], seat["trial"]) for seat in stalled}
    lines.append(f"{len(stalled)} seats in {len(stalled_trials)} trials stalled, gaps "
                 f"{min(seat['gap_s'] for seat in stalled):.0f}-{max(seat['gap_s'] for seat in stalled):.0f} s")
    lines.append("")
    lines.append(f"{'job':<14} {'seat':<5} {'model':<44} {'gap start UTC':<20} {'gap s':>6}")
    for seat in stalled:
        lines.append(f"{seat['job']:<14} {seat['seat']:<5} {seat['model']:<44} {utc(seat['gap_start']):<20} "
                     f"{seat['gap_s']:>6.0f}")
    for number, event in enumerate(events, 1):
        lines.append("")
        lines.append(f"event {number}: {event['seats']} seats in {event['trials']} trials, gap starts "
                     f"{utc(event['start'])}-{utc(event['end'])[11:]} UTC, median {utc(event['median'])[11:]}, "
                     f"gaps {event['gap_min_s']:.0f}-{event['gap_max_s']:.0f} s")
        for model, row in event["models"].items():
            if row["stalled"] or row["in_flight"]:
                lines.append(f"  {model:<44} stalled {row['stalled']} of {row['in_flight']} in flight")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find seats that stalled between two agent turns.")
    parser.add_argument("jobs_dir", type=Path)
    parser.add_argument("patterns", nargs="+", help="job name globs, e.g. 'xm1-??????'")
    parser.add_argument("--threshold", type=float, default=500.0, help="gap in seconds that counts as a stall")
    parser.add_argument("--window", type=float, default=120.0, help="gap starts this close join one event")
    parser.add_argument("--json", type=Path, help="write the seats, stalls and events here")
    args = parser.parse_args(argv)
    seats = seats_of(args.jobs_dir, args.patterns, args.threshold)
    events = events_of(seats, args.window)
    print(report(seats, events, args.threshold))
    if args.json:
        stalls = [
            {key: seat[key] for key in ("job", "trial", "seat", "model", "gap_s")} | {"gap_start_utc": utc(seat["gap_start"])}
            for seat in sorted((seat for seat in seats if seat["gap_start"] is not None),
                               key=lambda seat: seat["gap_start"])
        ]
        payload = {
            "patterns": args.patterns, "threshold_s": args.threshold, "window_s": args.window,
            "seats_scanned": len(seats), "stalls": stalls,
            "events": [event | {key: utc(event[key]) for key in ("start", "end", "median")} for event in events],
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
