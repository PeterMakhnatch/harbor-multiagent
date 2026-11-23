"""Cross-model wave analysis (pre-registered before the paid wave xm1): the split family (arms silent, talk, propose)
and the commons family (arms silent, talk) with every seat of a trial on one model.

    python3 flotilla/docs/results/scripts/xm_analysis.py .flotilla-local/jobs 'xm1-??????' \
        --tasks .flotilla-local/tasks/<split build> --tasks .flotilla-local/tasks/<commons build> [--json out.json]

Each trial folder of the matching jobs gets its family, instance and arm from the manifest.json of a --tasks folder
(by task folder name) and its model from the trial config (the participants' model_name). Every measure is recomputed
from the frozen round log with the family's score_game. A trial is valid when the verifier wrote a score without an
error and Harbor recorded no exception. In each (family, instance, arm, model) cell the first valid trial by start time
counts. A model-arm is valid when missed seat-rounds are at most 10% of its valid trials' seat-rounds; the tests use
only counted trials of valid model-arms. Primary contrasts (talk vs silent, both families): exact stratified test
pooled over the new models, alpha 0.025, at least 24 pairs. Secondary (split propose vs silent): alpha 0.05, at least
18 pairs. Reported (split talk vs propose): the paired deal difference with a 95% bootstrap interval. Every model,
the replication model too, also gets its own exact McNemar line. Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
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


WAVE = _load("xm_wave", HERE.parent / "commons_wave.py")
SCORERS = {family: _load(f"xm_{family}", HERE.parents[3] / "games" / f"{family}.py").score_game
           for family in ("split", "commons")}
NEW_MODELS = ("openrouter/qwen/qwen3.8-27b", "openrouter/openai/gpt-oss-120b", "openrouter/anthropic/claude-haiku-5.5")
REPLICATION_MODEL = "openrouter/deepseek/deepseek-v4.1-flash-1017"
MAX_MISSED = 0.10
PER_MODEL_ALPHA = 0.05
SUCCESS = {"split": ("agreed", "deals"), "commons": ("survived", "survived")}
TESTS = {"primary": (0.025, 24), "secondary": (0.05, 18)}
CONTRASTS = (("split", "talk", "silent", "primary"), ("split", "propose", "silent", "secondary"),
             ("split", "talk", "propose", "reported"), ("commons", "talk", "silent", "primary"))
PROPOSAL_KEYS = ("proposals", "acceptances", "accepted", "replaced", "lapsed", "expired", "feasible", "feasible_unseen")
BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED = 10_000, 1


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


def problems(result: dict | None, verdict: dict | None, state: dict | None, task: dict, model: str) -> list[str]:
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
    if "+" in model or model == "unknown":
        found.append(f"seat model {model}")
    return found


def measures(family: str, score: dict) -> dict[str, Any]:
    if family == "commons":
        return {key: score[key] for key in ("sustained_share", "collapsed_in", "over_use")}
    outside, shares = score["outside"], score["surplus_shares"]
    found = {"efficiency": score["efficiency"], "deal_round": score["deal_round"], "via": score["via"],
             "strong_share": shares[max(outside, key=outside.get)] if shares else None}
    return found | {key: score[key] for key in PROPOSAL_KEYS if key in score}


def trial_row(trial: Path, tasks: dict[str, dict]) -> dict | None:
    """One trial; None when its task is in no manifest."""
    config = read_json(trial / "config.json") or {}
    result = read_json(trial / "result.json")
    config = (result or {}).get("config") or config
    task = tasks.get(Path(((config.get("task") or {}).get("path")) or "").name)
    if task is None:
        return None
    verdict = read_json(trial / "verifier" / "score.json")
    state = read_json(trial / "verifier" / "world-state.json")
    model = trial_model(config, result or {})
    reasons = problems(result, verdict, state, task, model)
    cost, unpriced, tokens = usage(result or {})
    row = {"job": trial.parent.name, "trial": trial.name, "started": (result or {}).get("started_at") or "",
           "family": task["family"], "instance": task["instance"], "arm": task["arm"], "model": model,
           "valid": not reasons, "reasons": reasons, "cost": cost, "unpriced_seats": unpriced, "tokens": tokens,
           "seat_rounds": 0, "missed": 0, "passed": 0, "success": None, "measures": {}, "score": {}}
    if reasons:
        return row
    params = read_json(Path(task["task_dir"]) / "environment" / "task.json")["game"]
    log = (state.get("game") or {}).get("log") or []
    score = SCORERS[task["family"]](params, log)
    seat_rounds = len(log) * len(params.get("players") or params["seats"])
    return row | {"seat_rounds": seat_rounds, "missed": round(score["missed"] * seat_rounds),
                  "passed": round(score.get("passed", 0.0) * seat_rounds),
                  "success": bool(score[SUCCESS[task["family"]][0]]), "measures": measures(task["family"], score),
                  "score": score}


def read_trials(jobs: Path, pattern: str, tasks: dict[str, dict]) -> tuple[list[dict], list[str]]:
    rows, unmapped = [], []
    for trial in sorted(jobs.glob(f"{pattern}/*/")):
        if not (trial / "config.json").is_file() and not (trial / "result.json").is_file():
            continue
        row = trial_row(trial, tasks)
        if row is None:
            unmapped.append(f"{trial.parent.name}/{trial.name}")
        else:
            rows.append(row)
    return rows, unmapped


def cell(row: dict) -> tuple[str, int, str, str]:
    return row["family"], row["instance"], row["arm"], row["model"]


def choose(rows: list[dict]) -> tuple[dict[tuple, dict], list[dict]]:
    """The counted trial of each cell (the first valid one by start time) and every other trial of a cell that has
    several."""
    counted: dict[tuple, dict] = {}
    for row in sorted(rows, key=lambda r: (r["started"], r["job"], r["trial"])):
        if row["valid"] and cell(row) not in counted:
            counted[cell(row)] = row
    sizes: dict[tuple, int] = {}
    for row in rows:
        sizes[cell(row)] = sizes.get(cell(row), 0) + 1
    duplicates = [row for row in rows if sizes[cell(row)] > 1 and counted.get(cell(row)) is not row]
    return counted, duplicates


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
            **{key: sum(m[key] for m in found) for key in PROPOSAL_KEYS if found and all(key in m for m in found)}}


def arm_table(rows: list[dict], counted: dict[tuple, dict]) -> dict[tuple, dict]:
    """Per (family, model, arm): trial counts, missed and passed seat-rounds over valid trials, validity, successes
    and the secondary measures over counted trials."""
    table: dict[tuple, dict] = {}
    for key in sorted({(r["family"], r["model"], r["arm"]) for r in rows}):
        family, model, arm = key
        mine = [r for r in rows if (r["family"], r["model"], r["arm"]) == key]
        valid = [r for r in mine if r["valid"]]
        chosen = [r for (f, _i, a, m), r in counted.items() if (f, m, a) == key]
        seat_rounds = sum(r["seat_rounds"] for r in valid)
        missed, passed = sum(r["missed"] for r in valid), sum(r["passed"] for r in valid)
        missed_share = missed / seat_rounds if seat_rounds else None
        table[key] = {"family": family, "model": model, "arm": arm, "trials": len(mine), "valid": len(valid),
                      "counted": len(chosen), "seat_rounds": seat_rounds, "missed": missed, "passed": passed,
                      "missed_share": missed_share, "passed_share": passed / seat_rounds if seat_rounds else None,
                      "arm_valid": missed_share is not None and missed_share <= MAX_MISSED,
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


def role(model: str, new_models: tuple[str, ...], replication: str | None) -> str:
    return "new" if model in new_models else "replication" if model == replication else "other"


def arm_problem(arm_row: dict | None, arm: str) -> str | None:
    if arm_row is None or not arm_row["valid"]:
        return f"no valid {arm} trials"
    return None if arm_row["arm_valid"] else f"{arm} missed share {arm_row['missed_share']:.3f} > {MAX_MISSED}"


def contrast(counted: dict, table: dict, models: list[str], spec: tuple[str, str, str, str],
             new_models: tuple[str, ...], replication: str | None) -> dict[str, Any]:
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
                           "alpha": alpha, "min_pairs": min_pairs, "decision": decision, "reason": reason}
    return found


def analyse(rows: list[dict], new_models: tuple[str, ...], replication: str | None) -> dict[str, Any]:
    assert replication not in new_models, "the replication model cannot be a new model"
    counted, duplicates = choose(rows)
    table = arm_table(rows, counted)
    contrasts = []
    for spec in CONTRASTS:
        models = sorted({r["model"] for r in rows if r["family"] == spec[0]})
        if models:
            contrasts.append(contrast(counted, table, models, spec, new_models, replication))
    costs: dict[str, dict] = {}
    for r in rows:
        entry = costs.setdefault(r["model"], {"trials": 0, "cost": 0.0, "unpriced_seats": 0,
                                              "tokens": {"input": 0, "cache": 0, "output": 0}})
        entry["trials"] += 1
        entry["cost"] += r["cost"]
        entry["unpriced_seats"] += r["unpriced_seats"]
        for key in entry["tokens"]:
            entry["tokens"][key] += r["tokens"][key]
    return {"new_models": list(new_models), "replication_model": replication, "rows": rows,
            "counted": [f"{r['job']}/{r['trial']}" for r in counted.values()],
            "invalid": [r for r in rows if not r["valid"]], "duplicates": duplicates,
            "arms": list(table.values()), "contrasts": contrasts, "cost": costs,
            "total_cost": sum(r["cost"] for r in rows)}


def fmt(value: Any, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def print_arms(result: dict, family: str) -> None:
    print(f"\n## {family}\n\n| model | arm | trials | valid | counted | missed share | passes share | "
          f"{SUCCESS[family][1]} | model-arm | secondary |\n|---|---|---|---|---|---|---|---|---|---|")
    for arm in (a for a in result["arms"] if a["family"] == family):
        extra = ", ".join(f"{key} {fmt(value) if not isinstance(value, dict) else value}"
                          for key, value in arm["secondary"].items())
        print(f"| {arm['model']} | {arm['arm']} | {arm['trials']} | {arm['valid']} | {arm['counted']} | "
              f"{fmt(arm['missed_share'])} | {fmt(arm['passed_share'])} | {arm['successes']} of {arm['counted']} | "
              f"{'valid' if arm['arm_valid'] else 'INVALID'} | {extra} |")


def print_contrast(found: dict) -> None:
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
    print(f"Pooled over the new models {pooled['models']} (exact stratified test, alpha {pooled['alpha']}, at least "
          f"{pooled['min_pairs']} pairs): {pooled['pairs']} pairs, b = {pooled['b']}, c = {pooled['c']}, "
          f"p = {pooled['p']:.5g}: {pooled['decision']} ({pooled['reason']})")


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
    for name in unmapped:
        print(f"not in any manifest: {name}")
    for family in SUCCESS:
        if any(arm["family"] == family for arm in result["arms"]):
            print_arms(result, family)
            for found in result["contrasts"]:
                if found["family"] == family:
                    print_contrast(found)
    print("\n## cost (recorded cost_usd summed over seats, every trial)\n\n| model | trials | cost USD | seats without "
          "cost | input tokens | cached input | output tokens |\n|---|---|---|---|---|---|---|")
    for model, entry in sorted(result["cost"].items()):
        tokens = entry["tokens"]
        print(f"| {model} | {entry['trials']} | {entry['cost']:.4f} | {entry['unpriced_seats']} | "
              f"{tokens['input']:,} | {tokens['cache']:,} | {tokens['output']:,} |")
    print(f"Total: ${result['total_cost']:.4f}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", type=Path)
    parser.add_argument("pattern")
    parser.add_argument("--tasks", type=Path, action="append", required=True)
    parser.add_argument("--new-models", default=",".join(NEW_MODELS))
    parser.add_argument("--replication-model", default=REPLICATION_MODEL)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    new_models = tuple(m for m in args.new_models.split(",") if m)
    rows, unmapped = read_trials(args.jobs, args.pattern, load_manifests(args.tasks))
    result = analyse(rows, new_models, args.replication_model or None)
    report(result, unmapped)
    if args.json:
        args.json.write_text(json.dumps(result | {"unmapped": unmapped}, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
