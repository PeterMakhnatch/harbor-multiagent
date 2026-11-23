"""Cross-model wave analysis (pre-registered before the paid wave xm1): the split family (arms silent, talk, propose)
and the commons family (arms silent, talk) with every seat of a trial on one model.

    python3 flotilla/docs/results/scripts/xm_analysis.py .flotilla-local/jobs 'xm1-??????' \
        --tasks .flotilla-local/tasks/<split build> --tasks .flotilla-local/tasks/<commons build> \
        --new-models m1,m2,m3 --replication-model m4 [--allow-other] [--json out.json]

Each trial folder of the matching jobs gets its family, instance and arm from the manifest.json of a --tasks folder
(by task folder name) and its model from the trial config (the participants' model_name). Every trial's model must be
a new model or the replication model; --allow-other reports any other model per model only. Every measure is
recomputed from the frozen round log with the family's score_game. A trial is valid when the verifier wrote a score
without an error and Harbor recorded no exception. A truncated trial (no deal, no collapse, fewer rounds logged than
planned) counts every unplayed seat-round as missed; split scores it as no deal, commons as survived when no played
round collapsed. In each (family, instance, arm, model) cell the first valid trial by start time counts. A model-arm is
valid when missed seat-rounds are at most 10% of its counted trials' seat-rounds; the tests use only counted trials of
valid model-arms. Primary contrasts (talk vs silent, both families): exact stratified test pooled over the new models,
alpha 0.025, at least 24 pairs, with a consistency label across the new models. Secondary (split propose vs silent):
alpha 0.05, at least 18 pairs; it cannot change a primary decision. Reported (split talk vs propose): the paired deal
difference with a 95% bootstrap interval. Every model also gets its own exact McNemar line. Sensitivity (reported
only): the primaries on first attempts only, and the commons primary without trials that missed more than 2
seat-rounds. Descriptive only (added after the pre-registration; no test, no decision): per split instance the
feasibility window, the rounds whose pot (split.py settle, from the task.json parameters) is at least the sum of the
outside options; per split model-arm the counted trials and deals by window (1 feasible round, 2 or more), the deals
in which a seat got less than its own outside option, and the rational deals (every seat at least its outside
option). Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


WAVE = _load("xm_wave", HERE.parent / "commons_wave.py")
GAMES = {family: _load(f"xm_{family}", HERE.parents[3] / "games" / f"{family}.py") for family in ("split", "commons")}
SCORERS = {family: game.score_game for family, game in GAMES.items()}
MAX_SENSITIVITY_MISSED = 2
CONSISTENT_MIN_MODELS = 2
MAX_MISSED = 0.10
PER_MODEL_ALPHA = 0.05
SUCCESS = {"split": ("agreed", "deals"), "commons": ("survived", "survived")}
TESTS = {"primary": (0.025, 24), "secondary": (0.05, 18)}
CONTRASTS = (("split", "talk", "silent", "primary"), ("split", "propose", "silent", "secondary"),
             ("split", "talk", "propose", "reported"), ("commons", "talk", "silent", "primary"))
PROPOSAL_KEYS = ("proposals", "acceptances", "accepted", "replaced", "lapsed", "expired", "feasible", "feasible_unseen")
BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED = 10_000, 1
WINDOW_BUCKETS = ("0", "1", "2+")


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_manifests(task_dirs: list[Path]) -> dict[str, dict]:
    """Task folder name -> family, instance, arm and the task folder, from each build's manifest.json."""
    tasks: dict[str, dict] = {}
    for root in task_dirs:
        manifest = read_json(root / "manifest.json")
        assert manifest and manifest.get("format") == "flotilla-manifest", f"no flotilla manifest in {root}"
        for entry in manifest["tasks"].values():
            folder = root / entry["path"]
            info = {"family": entry["family"], "instance": int(entry["instance"]), "arm": entry["condition"],
                    "task_dir": str(folder)}
            assert tasks.get(folder.name, info) == info, f"task {folder.name} is in two manifests"
            tasks[folder.name] = info
    return tasks


def trial_model(config: dict, result: dict) -> str:
    names = {p["model_name"] for p in config.get("participants") or [] if p.get("model_name")}
    if not names and (config.get("agent") or {}).get("model_name"):
        names = {config["agent"]["model_name"]}
    if not names:
        infos = [seat.get("agent_info") for seat in result.get("seat_results") or []] + [result.get("agent_info")]
        names = {f"{info['model_info']['provider']}/{info['model_info']['name']}"
                 for info in infos if info and info.get("model_info")}
    return "+".join(sorted(names)) or "unknown"


def usage(result: dict) -> tuple[float, int, dict[str, int]]:
    """Recorded cost in USD summed over seats, the number of seats without a recorded cost, and token counts."""
    results = [seat.get("agent_result") for seat in result.get("seat_results") or []] or [result.get("agent_result")]
    cost, unpriced, tokens = 0.0, 0, {"input": 0, "cache": 0, "output": 0}
    for seat in results:
        seat = seat or {}
        if seat.get("cost_usd") is None:
            unpriced += 1
        cost += seat.get("cost_usd") or 0.0
        tokens["input"] += seat.get("n_input_tokens") or 0
        tokens["cache"] += seat.get("n_cache_tokens") or 0
        tokens["output"] += seat.get("n_output_tokens") or 0
    return cost, unpriced, tokens


def harness_problems(result: dict | None, verdict: dict | None, state: dict | None, task: dict) -> list[str]:
    """Why a trial is invalid apart from its seat models: no result, Harbor or seat exceptions, verifier or world
    errors, a score for another task, no world state."""
    if result is None:
        return ["no result.json"]
    found = []
    if result.get("exception_info"):
        found.append(f"harbor exception {result['exception_info'].get('exception_type')}")
    found += [f"seat {seat.get('seat')} exception {seat['exception_info'].get('exception_type')}"
              for seat in result.get("seat_results") or [] if seat.get("exception_info")]
    if verdict is None:
        found.append("no verifier score")
    elif verdict.get("error") is not None:
        found.append(f"verifier or world error: {verdict['error']}")
    elif (verdict.get("instance"), verdict.get("condition")) != (task["instance"], task["arm"]):
        found.append(f"score is for instance {verdict.get('instance')} {verdict.get('condition')}, not the manifest's")
    if state is None:
        found.append("no world state")
    return found


def problems(result: dict | None, verdict: dict | None, state: dict | None, task: dict, model: str) -> list[str]:
    found = harness_problems(result, verdict, state, task)
    if result is not None and ("+" in model or model == "unknown"):
        found.append(f"seat model {model}")
    return found


def measures(family: str, score: dict) -> dict[str, Any]:
    if family == "commons":
        return {key: score[key] for key in ("sustained_share", "collapsed_in", "over_use")}
    outside, shares = score["outside"], score["surplus_shares"]
    found = {"efficiency": score["efficiency"], "deal_round": score["deal_round"], "via": score["via"],
             "strong_share": shares[max(outside, key=outside.get)] if shares else None}
    return found | {key: score[key] for key in PROPOSAL_KEYS if key in score}


def pot_sequence(params: dict) -> list[int]:
    """The pot of each planned round while no round ends in a deal, by split.py's settle (the world's rule)."""
    seats, stock, pots = list(params["seats"]), params["capacity"], []
    for _number in range(params["rounds"]):
        pots.append(stock)
        stock = GAMES["split"].settle(stock, dict.fromkeys(seats, 0), params, missed=seats)["next_stock"]
    return pots


def window_of(family: str, params: dict | None) -> dict[str, Any] | None:
    """A split instance's feasibility window: the rounds whose pot is at least the sum of the outside options."""
    if family != "split" or params is None:
        return None
    pots, needed = pot_sequence(params), sum(params["outside"].values())
    feasible = [number for number, pot in enumerate(pots, 1) if pot >= needed]
    assert feasible == list(range(1, len(feasible) + 1)), (pots, needed)
    return {"pots": pots, "outside": dict(params["outside"]), "outside_sum": needed, "feasible": feasible,
            "feasible_rounds": len(feasible)}


def window_bucket(feasible_rounds: int) -> str:
    assert feasible_rounds >= 0, feasible_rounds
    return WINDOW_BUCKETS[min(feasible_rounds, len(WINDOW_BUCKETS) - 1)]


def below_outside(family: str, score: dict) -> list[dict[str, Any]]:
    """The seats of a split deal whose payoff is below their own outside option."""
    if family != "split" or not score["agreed"]:
        return []
    totals, outside = score["totals"], score["outside"]
    found = [{"seat": seat, "payoff": totals[seat], "outside": outside[seat]}
             for seat in sorted(outside) if totals[seat] < outside[seat]]
    assert {entry["seat"] for entry in found} == set(score["below_outside"]), (found, score["below_outside"])
    return found


def locate(trial: Path, tasks: dict[str, dict]) -> tuple[dict, dict | None, dict | None]:
    """A trial folder's config, result (None without result.json) and manifest task (None when in no manifest)."""
    config = read_json(trial / "config.json") or {}
    result = read_json(trial / "result.json")
    config = (result or {}).get("config") or config
    return config, result, tasks.get(Path(((config.get("task") or {}).get("path")) or "").name)


def trial_row(trial: Path, tasks: dict[str, dict]) -> dict | None:
    """One trial; None when its task is in no manifest."""
    config, result, task = locate(trial, tasks)
    if task is None:
        return None
    verdict = read_json(trial / "verifier" / "score.json")
    state = read_json(trial / "verifier" / "world-state.json")
    model = trial_model(config, result or {})
    reasons = problems(result, verdict, state, task, model)
    cost, unpriced, tokens = usage(result or {})
    params = (read_json(Path(task["task_dir"]) / "environment" / "task.json") or {}).get("game")
    row = {"job": trial.parent.name, "trial": trial.name, "started": (result or {}).get("started_at") or "",
           "family": task["family"], "instance": task["instance"], "arm": task["arm"], "model": model,
           "valid": not reasons, "reasons": reasons, "cost": cost, "unpriced_seats": unpriced, "tokens": tokens,
           "seat_rounds": 0, "missed": 0, "passed": 0, "truncated": False, "success": None, "measures": {},
           "score": {}, "window": window_of(task["family"], params), "below_outside": []}
    if reasons:
        return row
    assert params is not None, f"no game parameters in {task['task_dir']}"
    log = (state.get("game") or {}).get("log") or []
    return row | played(task["family"], params, log, SCORERS[task["family"]](params, log))


def played(family: str, params: dict, log: list[dict], score: dict) -> dict[str, Any]:
    """Seat-rounds, missed and passed seat-rounds, truncation and success of a scored trial. A truncated game counts
    every unplayed seat-round as missed: split as no deal, commons as survived when no played round collapsed (the
    unplayed rounds would have zero catch). When every round record lists `inactive` seats (world v10 on), a
    seat-round is missed when the seat was inactive: no claim, pass, post, accept or decline (SPLIT.md section 16);
    in arms without proposals that equals `missed`."""
    seats, planned = len(params.get("players") or params["seats"]), params["rounds"]
    ended = any(record.get("agreed") or record.get("collapsed") for record in log)
    truncated = not ended and len(log) < planned
    assert score.get("truncated", truncated) == truncated, (score.get("truncated"), truncated)
    logged = len(log) * seats
    unplayed = (planned - len(log)) * seats if truncated else 0
    acted_rule = bool(log) and all("inactive" in record for record in log)
    missed = sum(len(record["inactive"]) for record in log) if acted_rule else round(score["missed"] * logged)
    if family == "split":
        success = bool(score["agreed"])
    else:
        success = score["collapsed_in"] is None if truncated else bool(score["survived"])
    assert not (truncated and family == "split" and success)
    return {"seat_rounds": logged + unplayed, "missed": missed + unplayed,
            "passed": round(score.get("passed", 0.0) * logged), "truncated": truncated, "success": success,
            "measures": measures(family, score), "score": score, "below_outside": below_outside(family, score)}


def read_trials(jobs: Path, pattern: str, tasks: dict[str, dict],
                reader: Callable[[Path, dict[str, dict]], dict | None] | None = None) -> tuple[list[dict], list[str]]:
    """Every trial folder of the matching jobs, read by `reader` (default trial_row; None means in no manifest)."""
    reader = reader or trial_row
    rows, unmapped = [], []
    for trial in sorted(jobs.glob(f"{pattern}/*/")):
        if not (trial / "config.json").is_file() and not (trial / "result.json").is_file():
            continue
        row = reader(trial, tasks)
        if row is None:
            unmapped.append(f"{trial.parent.name}/{trial.name}")
        else:
            rows.append(row)
    return rows, unmapped


def cell(row: dict) -> tuple[str, int, str, str]:
    return row["family"], row["instance"], row["arm"], row["model"]


def start_key(row: dict) -> tuple:
    """Start time order; a trial without a start time (no result.json) sorts last."""
    started = row["started"]
    when = datetime.fromisoformat(started) if started else None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when is None, when or datetime.min.replace(tzinfo=UTC), row["job"], row["trial"]


def choose(rows: list[dict]) -> tuple[dict[tuple, dict], list[dict]]:
    """The counted trial of each cell (the first valid one by start time) and every other trial of a cell that has
    several."""
    counted: dict[tuple, dict] = {}
    for row in sorted(rows, key=start_key):
        if row["valid"] and cell(row) not in counted:
            counted[cell(row)] = row
    sizes: dict[tuple, int] = {}
    for row in rows:
        sizes[cell(row)] = sizes.get(cell(row), 0) + 1
    duplicates = [row for row in rows if sizes[cell(row)] > 1 and counted.get(cell(row)) is not row]
    return counted, duplicates


def first_attempts(rows: list[dict]) -> dict[tuple, dict]:
    """The earliest trial of each cell, valid or not; a cell whose first attempt is invalid has no counted trial."""
    first: dict[tuple, dict] = {}
    for row in sorted(rows, key=start_key):
        first.setdefault(cell(row), row)
    return {key: row for key, row in first.items() if row["valid"]}


def mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def secondary(family: str, counted: list[dict]) -> dict[str, Any]:
    found = [row["measures"] for row in counted]
    if family == "commons":
        return {"mean_sustained_share": mean([m["sustained_share"] for m in found]),
                "mean_collapse_round": mean([m["collapsed_in"] for m in found]),
                "collapses": sum(m["collapsed_in"] is not None for m in found),
                "mean_over_use": mean([m["over_use"] for m in found])}
    vias: dict[str, int] = {}
    for m in found:
        if m["via"] is not None:
            vias[m["via"]] = vias.get(m["via"], 0) + 1
    return {"mean_efficiency": mean([m["efficiency"] for m in found]),
            "mean_deal_round": mean([m["deal_round"] for m in found]),
            "mean_strong_share": mean([m["strong_share"] for m in found]), "via": vias,
            **{key: sum(m.get(key, 0) for m in found) for key in PROPOSAL_KEYS if any(key in m for m in found)}}


def arm_table(rows: list[dict], counted: dict[tuple, dict]) -> dict[tuple, dict]:
    """Per (family, model, arm): trial counts, and over counted trials the missed and passed seat-rounds, validity,
    truncated trials, successes and the secondary measures."""
    table: dict[tuple, dict] = {}
    for key in sorted({(r["family"], r["model"], r["arm"]) for r in rows}):
        family, model, arm = key
        mine = [r for r in rows if (r["family"], r["model"], r["arm"]) == key]
        chosen = [r for (f, _i, a, m), r in counted.items() if (f, m, a) == key]
        seat_rounds = sum(r["seat_rounds"] for r in chosen)
        missed, passed = sum(r["missed"] for r in chosen), sum(r["passed"] for r in chosen)
        missed_share = missed / seat_rounds if seat_rounds else None
        table[key] = {"family": family, "model": model, "arm": arm, "trials": len(mine),
                      "valid": sum(r["valid"] for r in mine), "counted": len(chosen), "seat_rounds": seat_rounds,
                      "missed": missed, "passed": passed, "missed_share": missed_share,
                      "passed_share": passed / seat_rounds if seat_rounds else None,
                      "arm_valid": missed_share is not None and missed_share <= MAX_MISSED,
                      "truncated": sum(r["truncated"] for r in chosen),
                      "successes": sum(r["success"] for r in chosen), "secondary": secondary(family, chosen)}
    return table


def stratified_p(strata: list[tuple[int, int]]) -> float:
    """Exact two-sided conditional test over strata (b, c): b_m ~ Binomial(b_m + c_m, 1/2) independently under H0;
    p = P(|S - K/2| >= |s_obs - K/2|) for S = sum b_m, by convolution."""
    counts = [1]
    for b, c in strata:
        assert b >= 0 and c >= 0, (b, c)
        step = [math.comb(b + c, i) for i in range(b + c + 1)]
        counts = [sum(counts[j] * step[s - j] for j in range(max(0, s - len(step) + 1), min(s, len(counts) - 1) + 1))
                  for s in range(len(counts) + len(step) - 1)]
    total = len(counts) - 1
    observed = abs(2 * sum(b for b, _c in strata) - total)
    extreme = sum(n for s, n in enumerate(counts) if abs(2 * s - total) >= observed)
    assert sum(counts) == 2**total
    return float(Fraction(extreme, 2**total))


def decide(pairs: int, b: int, c: int, p: float, alpha: float, min_pairs: int) -> tuple[str, str]:
    if pairs < min_pairs:
        return "INCONCLUSIVE", f"{pairs} complete valid pairs, fewer than {min_pairs}"
    if b > c and p <= alpha:
        return "PASS", f"b > c and p <= {alpha}"
    if c > b and p <= alpha:
        return "FAIL", f"reversed: c > b and p <= {alpha}"
    return "FAIL", f"not (b > c and p <= {alpha})"


def pairs_of(counted: dict[tuple, dict], table: dict[tuple, dict], family: str, model: str, first: str,
             second: str) -> list[tuple[int, bool, bool]]:
    if not (table.get((family, model, first), {}).get("arm_valid")
            and table.get((family, model, second), {}).get("arm_valid")):
        return []
    instances = sorted(i for (f, i, a, m) in counted if (f, a, m) == (family, first, model)
                       and (family, i, second, model) in counted)
    return [(i, counted[(family, i, first, model)]["success"], counted[(family, i, second, model)]["success"])
            for i in instances]


def role(model: str, new_models: tuple[str, ...], replication: str) -> str:
    return "new" if model in new_models else "replication" if model == replication else "other"


def arm_problem(arm_row: dict | None, arm: str) -> str | None:
    if arm_row is None or not arm_row["counted"]:
        return f"no counted {arm} trials"
    return None if arm_row["arm_valid"] else f"{arm} missed share {arm_row['missed_share']:.3f} > {MAX_MISSED}"


def consistency(pool: list[dict]) -> dict[str, Any]:
    """How many valid new models point the pooled way (b_m > c_m) and which reverse with p <= 0.05."""
    valid = [line for line in pool if not line["excluded"]]
    positive = sum(line["b"] > line["c"] for line in valid)
    reversed_models = [line["model"] for line in valid if line["c"] > line["b"] and line["p"] <= PER_MODEL_ALPHA]
    label = ("consistent" if positive >= CONSISTENT_MIN_MODELS and not reversed_models
             else "driven by fewer models")
    return {"valid_models": len(valid), "positive": positive, "reversed": reversed_models, "label": label}


def contrast(counted: dict, table: dict, models: list[str], spec: tuple[str, str, str, str],
             new_models: tuple[str, ...], replication: str) -> dict[str, Any]:
    family, first, second, kind = spec
    lines = []
    for model in models:
        pairs = pairs_of(counted, table, family, model, first, second)
        excluded = [why for arm in (first, second) if (why := arm_problem(table.get((family, model, arm)), arm))]
        line: dict[str, Any] = {"model": model, "role": role(model, new_models, replication), "pairs": len(pairs),
                                "excluded": excluded}
        if kind == "reported":
            differences = [int(a) - int(b) for _i, a, b in pairs]
            interval = (WAVE.bootstrap(differences, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED) if differences
                        else (None, None))
            line |= {"mean": mean(differences), "low": interval[0], "high": interval[1]}
        else:
            b = sum(a and not other for _i, a, other in pairs)
            c = sum(other and not a for _i, a, other in pairs)
            p = WAVE.exact_mcnemar(b, c)
            label = (f"excluded: {', '.join(excluded)}" if excluded else
                     "replicates" if b > c and p <= PER_MODEL_ALPHA else "does not replicate")
            line |= {"b": b, "c": c, "p": p, "label": label}
        lines.append(line)
    found = {"family": family, "a": first, "b_arm": second, "kind": kind, "models": lines}
    if kind in TESTS:
        alpha, min_pairs = TESTS[kind]
        pool = [line for line in lines if line["role"] == "new"]
        b, c = sum(line["b"] for line in pool), sum(line["c"] for line in pool)
        n_pairs = sum(line["pairs"] for line in pool)
        p = stratified_p([(line["b"], line["c"]) for line in pool])
        decision, reason = decide(n_pairs, b, c, p, alpha, min_pairs)
        found["pooled"] = {"models": [line["model"] for line in pool], "pairs": n_pairs, "b": b, "c": c, "p": p,
                           "alpha": alpha, "min_pairs": min_pairs, "decision": decision, "reason": reason,
                           "consistency": consistency(pool)}
    return found


def run_contrasts(rows: list[dict], counted: dict[tuple, dict], specs: tuple, new_models: tuple[str, ...],
                  replication: str) -> list[dict]:
    table = arm_table(rows, counted)
    found = []
    for spec in specs:
        models = sorted({r["model"] for r in rows if r["family"] == spec[0]})
        if models:
            found.append(contrast(counted, table, models, spec, new_models, replication))
    return found


def costs_of(rows: list[dict]) -> dict[str, dict]:
    costs: dict[str, dict] = {}
    for r in rows:
        entry = costs.setdefault(r["model"], {"trials": 0, "cost": 0.0, "unpriced_seats": 0,
                                              "tokens": {"input": 0, "cache": 0, "output": 0}})
        entry["trials"] += 1
        entry["cost"] += r["cost"]
        entry["unpriced_seats"] += r["unpriced_seats"]
        for key in entry["tokens"]:
            entry["tokens"][key] += r["tokens"][key]
    return costs


def below_entry(row: dict) -> dict[str, Any]:
    return {"job": row["job"], "trial": row["trial"], "instance": row["instance"], "arm": row["arm"],
            "model": row["model"], "seats": row["below_outside"]}


def deal_quality(rows: list[dict]) -> dict[str, Any]:
    """Descriptive, over counted split trials: trials and deals by feasibility window, the deals in which a seat got
    less than its own outside option, and the rational deals (every seat at least its outside option)."""
    deals = [r for r in rows if r["success"]]
    below = [r for r in deals if r["below_outside"]]
    rational = len(deals) - len(below)
    window = {bucket: {"trials": 0, "deals": 0} for bucket in WINDOW_BUCKETS}
    for r in rows:
        assert r["window"] is not None, f"split trial {r['job']}/{r['trial']} without a window"
        entry = window[window_bucket(r["window"]["feasible_rounds"])]
        entry["trials"] += 1
        entry["deals"] += bool(r["success"])
    return {"trials": len(rows), "deals": len(deals), "by_window": window, "below_outside": len(below),
            "below_outside_trials": [below_entry(r) for r in below], "rational_deals": rational,
            "rational_rate": rational / len(rows) if rows else None,
            "rational_share_of_deals": rational / len(deals) if deals else None}


def instance_windows(rows: list[dict]) -> list[dict[str, Any]]:
    """Each split instance's feasibility window; every trial of an instance has the same one."""
    found: dict[int, dict] = {}
    for r in rows:
        if r["family"] == "split" and r["window"] is not None:
            known = found.setdefault(r["instance"], r["window"])
            assert known == r["window"], f"split instance {r['instance']} has two parameter sets"
    return [{"instance": instance, **window} for instance, window in sorted(found.items())]


def below_outside_all(rows: list[dict], counted: list[dict]) -> list[dict[str, Any]]:
    """Every valid split trial with a below-outside deal, counted or not."""
    chosen = {id(r) for r in counted}
    return [below_entry(r) | {"counted": id(r) in chosen} for r in rows if r["valid"] and r["below_outside"]]


def split_descriptive(rows: list[dict], counted: dict[tuple, dict]) -> dict[str, Any]:
    table = []
    for model, arm in sorted({(r["model"], r["arm"]) for r in rows if r["family"] == "split"}):
        chosen = [r for (f, _i, a, m), r in counted.items() if (f, m, a) == ("split", model, arm)]
        table.append({"model": model, "arm": arm, **deal_quality(chosen)})
    return {"instances": instance_windows(rows), "model_arms": table,
            "below_outside_all": below_outside_all(rows, list(counted.values()))}


def analyse(rows: list[dict], new_models: tuple[str, ...], replication: str) -> dict[str, Any]:
    assert replication and replication not in new_models, "the replication model must be set and not a new model"
    counted, duplicates = choose(rows)
    primaries = tuple(spec for spec in CONTRASTS if spec[3] == "primary")
    fewer_missed = {key: row for key, row in counted.items()
                    if key[0] != "commons" or row["missed"] <= MAX_SENSITIVITY_MISSED}
    sensitivity = {
        "first_attempt": run_contrasts(rows, first_attempts(rows), primaries, new_models, replication),
        "commons_at_most_2_missed": run_contrasts(rows, fewer_missed, tuple(s for s in primaries if s[0] == "commons"),
                                                  new_models, replication)}
    return {"new_models": list(new_models), "replication_model": replication, "rows": rows,
            "counted": [f"{r['job']}/{r['trial']}" for r in counted.values()],
            "invalid": [r for r in rows if not r["valid"]], "duplicates": duplicates,
            "truncated": [r for r in rows if r["truncated"]], "arms": list(arm_table(rows, counted).values()),
            "contrasts": run_contrasts(rows, counted, CONTRASTS, new_models, replication),
            "sensitivity": sensitivity, "cost": costs_of(rows), "total_cost": sum(r["cost"] for r in rows),
            "split_descriptive": split_descriptive(rows, counted)}


def fmt(value: Any, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def print_arms(result: dict, family: str) -> None:
    print(f"\n## {family}\n\n| model | arm | trials | valid | counted | truncated | missed share | passes share | "
          f"{SUCCESS[family][1]} | model-arm | secondary |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in (a for a in result["arms"] if a["family"] == family):
        extra = ", ".join(f"{key} {fmt(value) if not isinstance(value, dict) else value}"
                          for key, value in arm["secondary"].items())
        print(f"| {arm['model']} | {arm['arm']} | {arm['trials']} | {arm['valid']} | {arm['counted']} | "
              f"{arm['truncated']} | {fmt(arm['missed_share'])} | {fmt(arm['passed_share'])} | "
              f"{arm['successes']} of {arm['counted']} | {'valid' if arm['arm_valid'] else 'INVALID'} | {extra} |")


def print_contrast(found: dict, note: str = "") -> None:
    first, second, kind = found["a"], found["b_arm"], found["kind"]
    if kind == "reported":
        print(f"\n{found['family']} {first} - {second} (reported, no test): paired "
              f"{SUCCESS[found['family']][0]} difference, 95% bootstrap over instances (seed {BOOTSTRAP_SEED}, "
              f"{BOOTSTRAP_RESAMPLES:,} resamples)\n\n| model | role | pairs | mean | 95% interval | excluded |"
              "\n|---|---|---|---|---|---|")
        for line in found["models"]:
            print(f"| {line['model']} | {line['role']} | {line['pairs']} | {fmt(line['mean'])} | "
                  f"[{fmt(line['low'])}, {fmt(line['high'])}] | {', '.join(line['excluded']) or '-'} |")
        return
    print(f"\n{found['family']} {first} vs {second} ({kind}; b = only {first} succeeded, c = only {second} "
          f"succeeded; exact McNemar per model)\n\n| model | role | pairs | b | c | p | label |"
          "\n|---|---|---|---|---|---|---|")
    for line in found["models"]:
        print(f"| {line['model']} | {line['role']} | {line['pairs']} | {line['b']} | {line['c']} | "
              f"{line['p']:.5g} | {line['label']} |")
    pooled = found["pooled"]
    note = note or ("(secondary; cannot change the primary decision)" if kind == "secondary" else "")
    print(f"Pooled over the new models {pooled['models']} (exact stratified test, alpha {pooled['alpha']}, at least "
          f"{pooled['min_pairs']} pairs): {pooled['pairs']} pairs, b = {pooled['b']}, c = {pooled['c']}, "
          f"p = {pooled['p']:.5g}: {pooled['decision']} ({pooled['reason']})" + (f" {note}" if note else ""))
    same = pooled["consistency"]
    print(f"Consistency: b_m > c_m in {same['positive']} of {same['valid_models']} valid new models; reversed with "
          f"p <= {PER_MODEL_ALPHA}: {same['reversed'] or 'none'}: {same['label']}")


def report(result: dict, unmapped: list[str]) -> None:
    rows = result["rows"]
    print(f"trials: {len(rows)}, valid {len(rows) - len(result['invalid'])}, invalid {len(result['invalid'])}, "
          f"counted {len(result['counted'])}, duplicates not counted {len(result['duplicates'])}, "
          f"not in any manifest {len(unmapped)}")
    print(f"new models (pooled): {result['new_models']}; replication model (per model only): "
          f"{result['replication_model']}")
    for row in result["invalid"]:
        print(f"invalid: {row['job']}/{row['trial']} {row['family']} {row['instance']} {row['arm']} {row['model']}: "
              f"{'; '.join(row['reasons'])}")
    for row in result["duplicates"]:
        print(f"duplicate, not counted: {row['job']}/{row['trial']} {row['family']} {row['instance']} {row['arm']} "
              f"{row['model']} (valid {row['valid']}, started {row['started']})")
    for row in result["truncated"]:
        print(f"truncated: {row['job']}/{row['trial']} {row['family']} {row['instance']} {row['arm']} {row['model']}: "
              f"{row['score']['rounds_played']} of {row['score']['rounds_planned']} rounds logged, "
              f"{SUCCESS[row['family']][0]} {row['success']}")
    for name in unmapped:
        print(f"not in any manifest: {name}")
    for family in SUCCESS:
        if any(arm["family"] == family for arm in result["arms"]):
            print_arms(result, family)
            for found in result["contrasts"]:
                if found["family"] == family:
                    print_contrast(found)
    print_sensitivity(result["sensitivity"])
    if result["split_descriptive"]["instances"]:
        print_split_descriptive(result["split_descriptive"])
    print("\n## cost (recorded cost_usd summed over seats, every trial)\n\n| model | trials | cost USD | seats without "
          "cost | input tokens | cached input | output tokens |\n|---|---|---|---|---|---|---|")
    for model, entry in sorted(result["cost"].items()):
        tokens = entry["tokens"]
        print(f"| {model} | {entry['trials']} | {entry['cost']:.4f} | {entry['unpriced_seats']} | "
              f"{tokens['input']:,} | {tokens['cache']:,} | {tokens['output']:,} |")
    print(f"Total: ${result['total_cost']:.4f}")


def print_sensitivity(sensitivity: dict) -> None:
    print("\n## Sensitivity (reported only; cannot change any decision)\n\n### Primaries on first attempts only (the "
          "earliest trial of each cell, valid or not; an invalid first attempt drops the pair)")
    for found in sensitivity["first_attempt"]:
        print_contrast(found, "(sensitivity; reported only)")
    print(f"\n### Commons primary without trials that missed more than {MAX_SENSITIVITY_MISSED} seat-rounds")
    for found in sensitivity["commons_at_most_2_missed"]:
        print_contrast(found, "(sensitivity; reported only)")
    if not sensitivity["commons_at_most_2_missed"]:
        print("No commons trials.")


QUALITY_COLUMNS = ("deals of trials, 0 feasible rounds | 1 feasible round | 2+ feasible rounds | below-outside deals | "
                   "rational deals | rational deals / trials")


def quality_cells(entry: dict) -> str:
    window = entry["by_window"]
    return (" | ".join(f"{window[b]['deals']} of {window[b]['trials']}" for b in WINDOW_BUCKETS)
            + f" | {entry['below_outside']} | {entry['rational_deals']} | {fmt(entry['rational_rate'])} |")


def print_windows(instances: list[dict]) -> None:
    print("\n### Feasibility window per split instance (the rounds whose pot is at least the sum of the outside "
          "options)\n\n| instance | pot by round | outside options | outside sum | feasible rounds |\n"
          "|---|---|---|---|---|")
    for window in instances:
        outside = ", ".join(f"{seat} {value}" for seat, value in sorted(window["outside"].items()))
        print(f"| {window['instance']} | {' '.join(map(str, window['pots']))} | {outside} | {window['outside_sum']} | "
              f"{window['feasible_rounds']} |")


def print_below(entries: list[dict]) -> None:
    print("\nBelow-outside deals (a seat's payoff below its own outside option), every valid trial:")
    for entry in entries:
        seats = ", ".join(f"{seat['seat']} {seat['payoff']} < {seat['outside']}" for seat in entry["seats"])
        print(f"- {entry['job']}/{entry['trial']} instance {entry['instance']} {entry['arm']} {entry['model']}: "
              f"{seats}{'' if entry['counted'] else ' (not counted)'}")
    if not entries:
        print("- none")


def print_split_descriptive(found: dict) -> None:
    print("\n## split: feasibility windows and deal quality (descriptive; added after the pre-registration, no test)")
    print_windows(found["instances"])
    print(f"\n### Counted trials per model-arm\n\n| model | arm | trials | deals | {QUALITY_COLUMNS} |\n"
          + "|---" * 10 + "|")
    for entry in found["model_arms"]:
        print(f"| {entry['model']} | {entry['arm']} | {entry['trials']} | {entry['deals']} | {quality_cells(entry)}")
    print_below(found["below_outside_all"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", type=Path)
    parser.add_argument("pattern")
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument("--new-models", required=True, help="comma-separated; the models of the pooled tests")
    parser.add_argument("--replication-model", required=True, help="per-model lines only, never pooled")
    parser.add_argument("--allow-other", action="store_true", help="report other models per model instead of failing")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    new_models = tuple(m for m in args.new_models.split(",") if m)
    if not new_models or args.replication_model in new_models:
        parser.error("--new-models needs at least one model, and not the --replication-model")
    rows, unmapped = read_trials(args.jobs, args.pattern, load_manifests(args.tasks))
    other = sorted({r["model"] for r in rows} - {*new_models, args.replication_model})
    if other and not args.allow_other:
        parser.error(f"trials use models in neither --new-models nor --replication-model: {other} "
                     "(add them to a list, or pass --allow-other to report them under 'other')")
    result = analyse(rows, new_models, args.replication_model)
    report(result, unmapped)
    if args.json:
        args.json.write_text(json.dumps(result | {"unmapped": unmapped}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
