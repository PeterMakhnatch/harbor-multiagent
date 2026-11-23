"""Listening: did seats read the other seats' messages before they acted? Measured from the world event logs.

    python3 flotilla/docs/results/scripts/listening.py .flotilla-local/jobs '<pattern>' \
        --tasks .flotilla-local/tasks/<split build> [--tasks .flotilla-local/tasks/<commons build>] [--json out.json]

Each trial folder of the matching jobs gets its family, instance and arm from a --tasks manifest and the model of each
seat from the trial config (xp_analysis.seat_models; same-model and cross-play trials alike). Validity comes from
xm_analysis.harness_problems plus a readable world log (artifacts/logs/world/events.jsonl); in each (family, instance,
arm, seat models) cell the first valid trial by start time counts (xm_analysis.choose) and only counted trials are
summed. Events are taken in `seq` order (the world records them under one lock), so a send, a deliver and an action at
the same time `t` count in the order the world handled them.
- Message to seat s: a `send` event from another seat with `to` s or "all".
- Action: a `request` (a split claim), `pass`, `propose`, `accept` or `decline` event; `gated` events (world v11 read
  gate) are counted apart and are not actions.
- unread(s, action): messages to s sent before the action whose id is in no `deliver` event of s before it. When the
  action event carries the world's own `unread` count (v11), that count is used and every difference from the log
  count is listed.
- Exposed action: at least one message to s was sent before it.
- Per seat: actions, exposed actions, exposed actions with unread >= 1, messages to s and how many a deliver showed s
  by the end (seen x/y), GET /messages calls (`deliver` events without "via": "gate"), gate deliveries, and the first
  GET /messages call in seconds after the first round opened (negative: before it).
- Per model x family x arm: the sums, the share of exposed actions with unread >= 1, the seats with no GET /messages
  call, the seats that saw none of the messages sent to them, and the median first call. Arms without messaging (not
  talk*, not gated) report n/a.
Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


XM = _load("listening_xm", HERE.parent / "xm_analysis.py")
XP = _load("listening_xp", HERE.parent / "xp_analysis.py")
ACTIONS = ("request", "pass", "propose", "accept", "decline")
GATED = "gated"
COUNTS = ("actions", "exposed", "exposed_unread", "addressed", "seen", "get_calls", "gate_deliveries", "gated",
          "world_unread")
NA = "n/a"


def read_events(path: Path) -> list[dict] | None:
    """The world event log, None when it is missing; a malformed line raises json.JSONDecodeError."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return None
    return [json.loads(line) for line in lines if line.strip()]


def ordered(events: list[dict]) -> list[dict]:
    found = sorted(events, key=lambda event: event["seq"])
    seqs = [event["seq"] for event in found]
    assert len(set(seqs)) == len(seqs), "the world log repeats a seq number"
    return found


def messaging(start: dict | None, arm: str) -> bool:
    """Whether seats could message: a talk* or gated condition, or a world start event with a read gate."""
    data = (start or {}).get("data") or {}
    condition = data.get("condition") or arm
    return condition.startswith("talk") or condition == "gated" or bool(data.get("read_gate"))


def recipients(event: dict, seats: list[str]) -> list[str]:
    to, sender = event["data"]["to"], event["seat"]
    if to == "all":
        return [seat for seat in seats if seat != sender]
    return [to] if to in seats and to != sender else []


def empty_seat() -> dict[str, Any]:
    return dict.fromkeys(COUNTS, 0) | {"by_kind": {}, "first_read": None, "mismatches": []}


def note_action(found: dict, event: dict, addressed: int, unread: int) -> None:
    """Count one action or gated event; the world's own `unread` count (v11) wins over the log count."""
    kind, data = event["kind"], event.get("data") or {}
    world = data.get("unread")
    if world is not None:
        assert isinstance(world, int) and not isinstance(world, bool) and world >= 0, world
        found["world_unread"] += 1
        if world != unread:
            found["mismatches"].append({"seq": event["seq"], "kind": kind, "world": world, "log": unread})
        unread = world
    if kind == GATED:
        found["gated"] += 1
        return
    by_kind = found["by_kind"].setdefault(kind, {"actions": 0, "exposed": 0, "exposed_unread": 0})
    for counts in (found, by_kind):
        counts["actions"] += 1
        counts["exposed"] += addressed > 0
        counts["exposed_unread"] += addressed > 0 and unread > 0


def note_deliver(found: dict, event: dict) -> None:
    if (event.get("data") or {}).get("via") == "gate":
        found["gate_deliveries"] += 1
        return
    found["get_calls"] += 1
    if found["first_read"] is None:
        found["first_read"] = event["t"]


def listen(events: list[dict], seats: list[str]) -> dict[str, dict]:
    """Per seat: the listening counts of one trial's world log."""
    events = ordered(events)
    opened = next((event["t"] for event in events if event["kind"] == "open"), None)
    found = {seat: empty_seat() for seat in seats}
    addressed: dict[str, set[str]] = {seat: set() for seat in seats}
    delivered: dict[str, set[str]] = {seat: set() for seat in seats}
    for event in events:
        seat, kind = event.get("seat"), event["kind"]
        if kind == "send":
            for to in recipients(event, seats):
                addressed[to].add(event["data"]["id"])
        elif seat not in found:
            continue
        elif kind == "deliver":
            note_deliver(found[seat], event)
            delivered[seat] |= set(event["data"]["ids"])
        elif kind in ACTIONS or kind == GATED:
            note_action(found[seat], event, len(addressed[seat]), len(addressed[seat] - delivered[seat]))
    for seat in seats:
        first = found[seat]["first_read"]
        found[seat] |= {"addressed": len(addressed[seat]), "seen": len(addressed[seat] & delivered[seat]),
                        "first_read": None if first is None or opened is None else round(first - opened, 3)}
    return found


def world_log(trial: Path) -> tuple[list[dict] | None, list[str]]:
    try:
        events = read_events(trial / "artifacts" / "logs" / "world" / "events.jsonl")
    except json.JSONDecodeError as exc:
        return None, [f"unreadable world event log: {exc}"]
    return events, [] if events else ["no world event log"]


def trial_row(trial: Path, tasks: dict[str, dict]) -> dict | None:
    """One trial's listening counts per seat; None when its task is in no manifest."""
    config, result, task = XM.locate(trial, tasks)
    if task is None:
        return None
    verdict = XM.read_json(trial / "verifier" / "score.json")
    state = XM.read_json(trial / "verifier" / "world-state.json")
    models = XP.seat_models(config, result or {})
    events, reasons = world_log(trial)
    reasons = XM.harness_problems(result, verdict, state, task) + reasons
    start = next((event for event in events or [] if event["kind"] == "start"), None)
    seats = list(((start or {}).get("data") or {}).get("seats") or sorted(models))
    if any(seat not in models for seat in seats):
        reasons.append(f"seat models {models} for seats {seats}")
    pair = [models.get(seat, "unknown") for seat in seats]
    row = {"job": trial.parent.name, "trial": trial.name, "started": (result or {}).get("started_at") or "",
           "family": task["family"], "instance": task["instance"], "arm": task["arm"], "model": " vs ".join(pair),
           "messaging": messaging(start, task["arm"]), "models": dict(zip(seats, pair, strict=True)),
           "valid": not reasons, "reasons": reasons, "seats": {}}
    return row if reasons else row | {"seats": listen(events or [], seats)}


def empty_group(model: str, family: str, arm: str, talk: bool) -> dict[str, Any]:
    return {"model": model, "family": family, "arm": arm, "messaging": talk, "trials": set(), "seats": 0,
            "never_read": 0, "blind": 0, "first_reads": [], "claims": {"exposed": 0, "exposed_unread": 0},
            "mismatches": 0, **dict.fromkeys(COUNTS, 0)}


def finish(group: dict) -> dict[str, Any]:
    talk, reads = group.pop("messaging"), group.pop("first_reads")
    found = group | {"messaging": talk, "trials": len(group["trials"]),
                     "share": group["exposed_unread"] / group["exposed"] if talk and group["exposed"] else None,
                     "median_first_read": statistics.median(reads) if talk and reads else None}
    if not talk:
        found |= dict.fromkeys(("never_read", "blind", "addressed", "seen", "get_calls", "gate_deliveries", "gated",
                                "exposed", "exposed_unread"), None) | {"claims": None}
    return found


def summarise(counted: list[dict]) -> list[dict[str, Any]]:
    """Per (model, family, arm) over the counted trials: the summed seat counts, the unread share and first reads."""
    groups: dict[tuple[str, str, str], dict] = {}
    for row in counted:
        assert row["valid"], f"{row['job']}/{row['trial']} is not valid"
        for seat, found in row["seats"].items():
            key = (row["models"][seat], row["family"], row["arm"])
            group = groups.setdefault(key, empty_group(*key, row["messaging"]))
            assert group["messaging"] == row["messaging"], f"{key}: trials with and without messaging"
            group["trials"].add((row["job"], row["trial"]))
            group["seats"] += 1
            for name in COUNTS:
                group[name] += found[name]
            claims = found["by_kind"].get("request", {})
            group["claims"]["exposed"] += claims.get("exposed", 0)
            group["claims"]["exposed_unread"] += claims.get("exposed_unread", 0)
            group["mismatches"] += len(found["mismatches"])
            group["never_read"] += found["get_calls"] == 0
            group["blind"] += found["addressed"] > 0 and found["seen"] == 0
            if found["first_read"] is not None:
                group["first_reads"].append(found["first_read"])
    return [finish(groups[key]) for key in sorted(groups)]


def analyse(rows: list[dict]) -> dict[str, Any]:
    """The counted trial of each (family, instance, arm, seat models) cell is the first valid one by start time
    (xm_analysis.choose); other trials of a cell are listed as duplicates and left out."""
    counted, duplicates = XM.choose(rows)
    chosen = {(row["job"], row["trial"]) for row in counted.values()}
    return {"groups": summarise(list(counted.values())), "rows": [r | {"counted": (r["job"], r["trial"]) in chosen}
                                                                   for r in rows],
            "duplicates": [f"{r['job']}/{r['trial']}" for r in duplicates],
            "invalid": [f"{r['job']}/{r['trial']}: {'; '.join(r['reasons'])}" for r in rows if not r["valid"]],
            "mismatches": [{"trial": f"{r['job']}/{r['trial']}", "seat": seat, **m} for r in rows
                           for seat, found in r["seats"].items() for m in found["mismatches"]]}


def fmt(value: Any, digits: int = 3) -> str:
    return NA if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def of(numerator: int | None, denominator: int | None) -> str:
    return NA if numerator is None or denominator is None else f"{numerator}/{denominator}"


def print_groups(groups: list[dict]) -> None:
    print("| model | family | arm | trials | seats | actions | exposed with unread / exposed | share | claims: "
          "exposed with unread / exposed | seen / messages to the seat | GET /messages calls | seats with no GET | "
          "seats that saw none of their messages | median first GET (s after round 1 opened) | gated | "
          "gate deliveries | world unread checked (mismatches) |")
    print("|" + "---|" * 17)
    for g in groups:
        claims, seats = g["claims"] or {}, g["seats"] if g["messaging"] else None
        print(f"| {g['model']} | {g['family']} | {g['arm']} | {g['trials']} | {g['seats']} | {g['actions']} | "
              f"{of(g['exposed_unread'], g['exposed'])} | {fmt(g['share'])} | "
              f"{of(claims.get('exposed_unread'), claims.get('exposed'))} | {of(g['seen'], g['addressed'])} | "
              f"{fmt(g['get_calls'])} | {of(g['never_read'], seats)} | {of(g['blind'], seats)} | "
              f"{fmt(g['median_first_read'], 1)} | {fmt(g['gated'])} | {fmt(g['gate_deliveries'])} | "
              f"{g['world_unread']} ({g['mismatches']}) |")


def print_seats(rows: list[dict]) -> None:
    print("\n## Seats of the counted trials in arms with messaging\n\n| trial | family | arm | seat | model | actions | "
          "exposed with unread / exposed | seen | GET calls | first GET (s) | gated |")
    print("|" + "---|" * 11)
    for row in rows:
        if not (row["counted"] and row["messaging"]):
            continue
        for seat, f in row["seats"].items():
            print(f"| {row['job']}/{row['trial']} | {row['family']} | {row['arm']} | {seat} | {row['models'][seat]} | "
                  f"{f['actions']} | {of(f['exposed_unread'], f['exposed'])} | {of(f['seen'], f['addressed'])} | "
                  f"{f['get_calls']} | {fmt(f['first_read'], 1)} | {f['gated']} |")


def report(result: dict, unmapped: list[str]) -> None:
    print("# Listening: unread messages at the moment of each action\n")
    rows = result["rows"]
    print(f"Trials: {len(rows)}, {sum(r['valid'] for r in rows)} valid, {sum(r['counted'] for r in rows)} counted "
          "(summed below). Arms without messaging: n/a.\n")
    print_groups(result["groups"])
    print_seats(rows)
    for found in result["mismatches"]:
        print(f"world unread differs: {found['trial']} {found['seat']} seq {found['seq']} {found['kind']}: "
              f"world {found['world']}, log {found['log']}")
    for name in result["invalid"]:
        print(f"invalid: {name}")
    for name in result["duplicates"]:
        print(f"duplicate, not counted: {name}")
    for name in unmapped:
        print(f"not in any manifest: {name}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", type=Path)
    parser.add_argument("pattern")
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    rows, unmapped = XM.read_trials(args.jobs, args.pattern, XM.load_manifests(args.tasks), reader=trial_row)
    result = analyse(rows)
    report(result, unmapped)
    if args.json:
        args.json.write_text(json.dumps(result | {"unmapped": unmapped}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
