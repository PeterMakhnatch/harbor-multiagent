"""Split cross-play analysis: the 2 seats of a trial may use different models (each seat's model from the trial config
participants, by seat name).

    python3 flotilla/docs/results/scripts/xp_analysis.py .flotilla-local/jobs '<pattern>' \
        --tasks .flotilla-local/tasks/<split build> [--json out.json]

Mapping, validity, truncation, missed seat-rounds and the counted trial per cell come from xm_analysis.py; the cell is
(instance, arm, ordered seat-model pair (p1, p2)). Trials of other families are counted and left out. Per arm:
1. the deal matrix (rows p1 model, columns p2 model: deals / counted valid trials);
2. self against cross play: per instance, self_i = mean deal over self-play cells (A, A), cross_i = mean deal over
   cross-play cells (A, B), A != B, d_i = self_i - cross_i, with an exact two-sided sign test on the non-zero d_i;
   an instance without a self or a cross cell is dropped and counted;
3. the role-balanced bargaining advantage per unordered pair {A, B} on instances where (A, B) and (B, A) both dealt:
   adv_i = [A's share in (A, B) + A's share in (B, A)] - [B's share in (B, A) + B's share in (A, B)], shares are
   payoff / starting pot, with an exact sign test; and each model's mean payoff share as strong and as weak seat
   (strong: the larger outside option) in self and cross play.
Over all arms: per model and arm, the missed share over every seat the model played (at most 10%, as in
xm_analysis.py); a failing model-arm is listed and every cell with that model is left out of 2 and 3. And the
outside-option statements of the messages (speech_acts.py) per speaker and listener model: true, inflated or deflated.
Descriptive only (added after the pre-registration; no test): per instance the feasibility window (xm_analysis.py),
and per arm, per ordered cell and for self against cross play, the counted trials and deals by window, the
below-outside deals and the rational deals. Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import statistics
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


XM = _load("xp_xm", HERE.parent / "xm_analysis.py")
SPEECH = _load("xp_speech", HERE.parent / "speech_acts.py")
LABELS = ("true", "inflated", "deflated")


def seat_models(config: dict, result: dict) -> dict[str, str]:
    found = {p["seat"]: p["model_name"] for p in config.get("participants") or []
             if p.get("seat") and p.get("model_name")}
    for seat in result.get("seat_results") or []:
        info = (seat.get("agent_info") or {}).get("model_info")
        if seat.get("seat") and seat["seat"] not in found and info:
            found[seat["seat"]] = f"{info['provider']}/{info['name']}"
    return found


def speech(messages: list[dict], outside: dict[str, int], seats: list[str]) -> dict[str, dict[str, int]]:
    """Outside-option statements per speaking seat: true, inflated, deflated."""
    found = {seat: dict.fromkeys(LABELS, 0) for seat in seats}
    for message in messages[:SPEECH.MAX_MESSAGES]:
        if message.get("from") in found and isinstance(message.get("text"), str):
            for statement in SPEECH.outside_statements(message, outside):
                found[message["from"]][statement["label"]] += 1
    return found


def trial_row(trial: Path, tasks: dict[str, dict]) -> dict | None:
    """One trial with its ordered seat-model pair; None when its task is in no manifest."""
    config, result, task = XM.locate(trial, tasks)
    if task is None:
        return None
    verdict = XM.read_json(trial / "verifier" / "score.json")
    state = XM.read_json(trial / "verifier" / "world-state.json")
    params = XM.read_json(Path(task["task_dir"]) / "environment" / "task.json")["game"]
    seats = list(params.get("players") or params["seats"])
    models = seat_models(config, result or {})
    pair = [models.get(seat, "unknown") for seat in seats]
    reasons = XM.harness_problems(result, verdict, state, task)
    if result is not None and "unknown" in pair:
        reasons.append(f"seat models {models}")
    if task["family"] != "split" or len(seats) != 2:
        reasons.append(f"not a 2-seat split trial ({task['family']}, {len(seats)} seats)")
    cost, unpriced, tokens = XM.usage(result or {})
    row = {"job": trial.parent.name, "trial": trial.name, "started": (result or {}).get("started_at") or "",
           "family": task["family"], "instance": task["instance"], "arm": task["arm"], "pair": pair,
           "model": " vs ".join(pair), "seats": seats, "valid": not reasons, "reasons": reasons, "cost": cost,
           "unpriced_seats": unpriced, "tokens": tokens, "capacity": params["capacity"],
           "outside": params.get("outside"), "seat_rounds": 0, "missed": 0, "seat_missed": {}, "truncated": False,
           "success": None, "totals": None, "speech": {}, "window": XM.window_of(task["family"], params),
           "below_outside": []}
    if reasons:
        return row
    log = (state.get("game") or {}).get("log") or []
    score = XM.SCORERS["split"](params, log)
    found = XM.played("split", params, log, score)
    unplayed = params["rounds"] - len(log) if found["truncated"] else 0
    seat_missed = {seat: sum(seat in record.get("missed", ()) for record in log) + unplayed for seat in seats}
    assert sum(seat_missed.values()) == found["missed"], (seat_missed, found["missed"])
    return row | {"seat_rounds": found["seat_rounds"], "missed": found["missed"], "seat_missed": seat_missed,
                  "truncated": found["truncated"], "success": found["success"], "totals": score["totals"],
                  "speech": speech(state.get("messages") or [], params["outside"], seats),
                  "below_outside": found["below_outside"]}


def model_arms(counted: list[dict]) -> dict[tuple[str, str], dict]:
    """Per (model, arm): seats played, seat-rounds and missed seat-rounds over the counted trials, and validity."""
    table: dict[tuple[str, str], dict] = {}
    for row in counted:
        each = row["seat_rounds"] // len(row["seats"])
        for seat, model in zip(row["seats"], row["pair"], strict=True):
            entry = table.setdefault((model, row["arm"]), {"model": model, "arm": row["arm"], "seats_played": 0,
                                                            "seat_rounds": 0, "missed": 0, "truncated_seats": 0})
            entry["seats_played"] += 1
            entry["seat_rounds"] += each
            entry["missed"] += row["seat_missed"][seat]
            entry["truncated_seats"] += row["truncated"]
    for entry in table.values():
        entry["missed_share"] = entry["missed"] / entry["seat_rounds"] if entry["seat_rounds"] else None
        entry["valid"] = entry["missed_share"] is not None and entry["missed_share"] <= XM.MAX_MISSED
    return table


def matrix(counted: list[dict], arm: str) -> dict[str, Any]:
    rows = [r for r in counted if r["arm"] == arm]
    models = sorted({m for r in rows for m in r["pair"]})
    cells = {}
    for first, second in itertools.product(models, models):
        mine = [r for r in rows if r["pair"] == [first, second]]
        if mine:
            cells[f"{first} vs {second}"] = {"p1": first, "p2": second, "deals": sum(r["success"] for r in mine),
                                             "trials": len(mine)}
    return {"arm": arm, "models": models, "cells": cells}


def sign_test(values: list) -> dict[str, Any]:
    plus, minus = sum(v > 0 for v in values), sum(v < 0 for v in values)
    return {"n": len(values), "n_plus": plus, "n_minus": minus, "ties": len(values) - plus - minus,
            "p": XM.WAVE.exact_mcnemar(plus, minus)}


def self_vs_cross(included: list[dict], instances: list[int], arm: str) -> dict[str, Any]:
    """Per instance the self-play and cross-play deal rates, their difference and the sign test."""
    groups: dict[int, dict[str, list[bool]]] = {i: {"self": [], "cross": []} for i in instances}
    for row in (r for r in included if r["arm"] == arm):
        groups[row["instance"]]["self" if row["pair"][0] == row["pair"][1] else "cross"].append(row["success"])
    kept, differences, dropped = [], [], []
    for instance, group in sorted(groups.items()):
        if not group["self"] or not group["cross"]:
            dropped.append(instance)
            continue
        own = Fraction(sum(group["self"]), len(group["self"]))
        cross = Fraction(sum(group["cross"]), len(group["cross"]))
        differences.append(own - cross)
        kept.append({"instance": instance, "self": float(own), "cross": float(cross), "d": float(own - cross),
                     "self_cells": len(group["self"]), "cross_cells": len(group["cross"])})
    return {"arm": arm, "instances": kept, "dropped": dropped, **sign_test(differences),
            "mean_self": statistics.fmean(k["self"] for k in kept) if kept else None,
            "mean_cross": statistics.fmean(k["cross"] for k in kept) if kept else None}


def share(row: dict, seat: str) -> Fraction:
    return Fraction(row["totals"][seat], row["capacity"])


def advantage(included: list[dict], arm: str) -> list[dict[str, Any]]:
    """Role-balanced advantage of A over B per unordered pair, on instances where both seat orders dealt."""
    cells = {(r["instance"], tuple(r["pair"])): r for r in included if r["arm"] == arm}
    models = sorted({m for _i, pair in cells for m in pair})
    found = []
    for a, b in itertools.combinations(models, 2):
        if not any(pair in ((a, b), (b, a)) for _i, pair in cells):
            continue
        values = []
        for instance in sorted({i for i, _pair in cells}):
            ab, ba = cells.get((instance, (a, b))), cells.get((instance, (b, a)))
            if not (ab and ba and ab["success"] and ba["success"]):
                continue
            a_total = share(ab, ab["seats"][0]) + share(ba, ba["seats"][1])
            b_total = share(ba, ba["seats"][0]) + share(ab, ab["seats"][1])
            values.append((instance, a_total - b_total))
        found.append({"arm": arm, "a": a, "b": b, **sign_test([v for _i, v in values]),
                      "mean": float(sum(v for _i, v in values) / len(values)) if values else None,
                      "instances": [{"instance": i, "adv": float(v)} for i, v in values]})
    return found


def role_shares(included: list[dict], arm: str) -> list[dict[str, Any]]:
    """Each model's mean payoff share (payoff / starting pot) as strong and as weak seat, in self and cross play."""
    shares: dict[tuple[str, str, str], list[float]] = {}
    for row in (r for r in included if r["arm"] == arm):
        outside = row["outside"]
        top = max(outside.values())
        if sum(value == top for value in outside.values()) != 1:
            continue
        kind = "self" if row["pair"][0] == row["pair"][1] else "cross"
        for seat, model in zip(row["seats"], row["pair"], strict=True):
            role = "strong" if outside[seat] == top else "weak"
            shares.setdefault((model, kind, role), []).append(float(share(row, seat)))
    return [{"arm": arm, "model": model, "play": kind, "role": role, "n": len(values),
             "mean_share": statistics.fmean(values)} for (model, kind, role), values in sorted(shares.items())]


def speech_table(counted: list[dict]) -> list[dict[str, Any]]:
    table: dict[tuple[str, str], dict[str, int]] = {}
    for row in counted:
        for seat, model in zip(row["seats"], row["pair"], strict=True):
            listener = next(m for s, m in zip(row["seats"], row["pair"], strict=True) if s != seat)
            entry = table.setdefault((model, listener), dict.fromkeys(LABELS, 0))
            for label in LABELS:
                entry[label] += row["speech"][seat][label]
    return [{"speaker": speaker, "listener": listener, **counts}
            for (speaker, listener), counts in sorted(table.items()) if sum(counts.values())]


def deal_quality(counted: list[dict], arm: str) -> dict[str, Any]:
    """Descriptive, over counted trials: xm_analysis.py's deal quality per ordered cell and for self and cross play."""
    rows = [r for r in counted if r["arm"] == arm]
    cells = [{"p1": first, "p2": second, **XM.deal_quality([r for r in rows if r["pair"] == [first, second]])}
             for first, second in sorted({tuple(r["pair"]) for r in rows})]
    play = [{"play": kind, **XM.deal_quality([r for r in rows if (r["pair"][0] == r["pair"][1]) == (kind == "self")])}
            for kind in ("self", "cross")]
    return {"cells": cells, "play": play}


def analyse(rows: list[dict]) -> dict[str, Any]:
    assert all(r["family"] == "split" for r in rows), "pass split trials only"
    counted_map, duplicates = XM.choose(rows)
    counted = list(counted_map.values())
    table = model_arms(counted)
    excluded = sorted(key for key, entry in table.items() if not entry["valid"])
    included = [r for r in counted if all((m, r["arm"]) not in excluded for m in r["pair"])]
    found = []
    for arm in sorted({r["arm"] for r in rows}):
        instances = sorted({r["instance"] for r in rows if r["arm"] == arm})
        found.append({"arm": arm, "matrix": matrix(counted, arm),
                      "self_vs_cross": self_vs_cross(included, instances, arm),
                      "advantage": advantage(included, arm), "role_shares": role_shares(included, arm),
                      "deal_quality": deal_quality(counted, arm)})
    return {"rows": rows, "counted": [f"{r['job']}/{r['trial']}" for r in counted],
            "invalid": [r for r in rows if not r["valid"]], "duplicates": duplicates,
            "truncated": [r for r in rows if r["truncated"]],
            "model_arms": sorted(table.values(), key=lambda entry: (entry["arm"], entry["model"])),
            "excluded": [list(key) for key in excluded], "arms": found, "speech": speech_table(counted),
            "total_cost": sum(r["cost"] for r in rows), "split_windows": XM.instance_windows(rows),
            "below_outside_all": XM.below_outside_all(rows, counted)}


def fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def print_arm(found: dict) -> None:
    grid = found["matrix"]
    print(f"\n## arm {found['arm']}\n\nDeals / counted valid trials (rows p1 model, columns p2 model)\n\n| p1 \\ p2 | "
          + " | ".join(grid["models"]) + " |\n|---|" + "---|" * len(grid["models"]))
    for first in grid["models"]:
        cells = [grid["cells"].get(f"{first} vs {second}") for second in grid["models"]]
        print(f"| {first} | " + " | ".join(f"{c['deals']}/{c['trials']}" if c else "-" for c in cells) + " |")
    test = found["self_vs_cross"]
    print(f"\nSelf against cross play: {len(test['instances'])} instances (dropped without a valid self or cross cell: "
          f"{len(test['dropped'])} {test['dropped']}); mean self {fmt(test['mean_self'])}, mean cross "
          f"{fmt(test['mean_cross'])}; d > 0 in {test['n_plus']}, d < 0 in {test['n_minus']}, ties {test['ties']}; "
          f"exact sign test p = {test['p']:.5g}")
    print("\nRole-balanced advantage (instances where both seat orders dealt)\n\n| model A | model B | n | "
          "mean adv (A - B) | n+ | n- | ties | p |\n|---|---|---|---|---|---|---|---|")
    for pair in found["advantage"]:
        print(f"| {pair['a']} | {pair['b']} | {pair['n']} | {fmt(pair['mean'])} | {pair['n_plus']} | "
              f"{pair['n_minus']} | {pair['ties']} | {pair['p']:.5g} |")
    print("\n| model | play | seat | n | mean payoff share |\n|---|---|---|---|---|")
    for entry in found["role_shares"]:
        print(f"| {entry['model']} | {entry['play']} | {entry['role']} | {entry['n']} | {entry['mean_share']:.3f} |")
    quality = found["deal_quality"]
    print(f"\nDeal quality over counted trials (descriptive)\n\n| p1 | p2 | trials | deals | {XM.QUALITY_COLUMNS} |\n"
          + "|---" * 10 + "|")
    for entry in quality["cells"]:
        print(f"| {entry['p1']} | {entry['p2']} | {entry['trials']} | {entry['deals']} | {XM.quality_cells(entry)}")
    for entry in quality["play"]:
        print(f"| {entry['play']} play | - | {entry['trials']} | {entry['deals']} | {XM.quality_cells(entry)}")


def report(result: dict, unmapped: list[str], other: int) -> None:
    rows = result["rows"]
    print(f"split trials: {len(rows)}, valid {len(rows) - len(result['invalid'])}, invalid {len(result['invalid'])}, "
          f"counted {len(result['counted'])}, duplicates not counted {len(result['duplicates'])}, truncated "
          f"{len(result['truncated'])}; other families left out {other}; not in any manifest {len(unmapped)}")
    for row in result["invalid"]:
        print(f"invalid: {row['job']}/{row['trial']} {row['instance']} {row['arm']} {row['model']}: "
              f"{'; '.join(row['reasons'])}")
    for row in result["duplicates"]:
        print(f"duplicate, not counted: {row['job']}/{row['trial']} {row['instance']} {row['arm']} {row['model']}")
    for row in result["truncated"]:
        print(f"truncated: {row['job']}/{row['trial']} {row['instance']} {row['arm']} {row['model']}")
    print("\n## models (missed seat-rounds over every seat played in counted trials)\n\n| model | arm | seats played | "
          "seat-rounds | missed share | truncated seats | validity |\n|---|---|---|---|---|---|---|")
    for entry in result["model_arms"]:
        print(f"| {entry['model']} | {entry['arm']} | {entry['seats_played']} | {entry['seat_rounds']} | "
              f"{fmt(entry['missed_share'])} | {entry['truncated_seats']} | "
              f"{'valid' if entry['valid'] else 'EXCLUDED from self/cross and advantage'} |")
    for found in result["arms"]:
        print_arm(found)
    print("\n## feasibility windows and below-outside deals (descriptive; added after the pre-registration, no test)")
    XM.print_windows(result["split_windows"])
    XM.print_below(result["below_outside_all"])
    print("\n## outside-option statements (speech_acts.py)\n\n| speaker | listener | true | inflated | deflated |"
          "\n|---|---|---|---|---|")
    for entry in result["speech"]:
        print(f"| {entry['speaker']} | {entry['listener']} | {entry['true']} | {entry['inflated']} | "
              f"{entry['deflated']} |")
    print(f"\nRecorded cost, every split trial: ${result['total_cost']:.4f}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", type=Path)
    parser.add_argument("pattern")
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    rows, unmapped = XM.read_trials(args.jobs, args.pattern, XM.load_manifests(args.tasks), reader=trial_row)
    split = [r for r in rows if r["family"] == "split"]
    result = analyse(split)
    report(result, unmapped, len(rows) - len(split))
    if args.json:
        args.json.write_text(json.dumps(result | {"unmapped": unmapped, "other_families": len(rows) - len(split)},
                                        indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
