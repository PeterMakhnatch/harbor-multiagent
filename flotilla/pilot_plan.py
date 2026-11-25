"""Generate a preview-only Seams pilot and price forecast (requires PyYAML). Never start a wave."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import build_tasks
import eval_card
import pricing
import run_wave
import stages
import token_profile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
PILOT_SEED = 1
CONTROL_N = 10
SIGNAL_N = 12
HEADLINE_CAP = 100
PROFILE_N = 3
LOOKS = (3, 6, 12)
LEVELS = ("canary", "standard", "heavy")
STAGES = ("A", "B", "C")
PRICE_URL = "https://openrouter.ai/api/v1/models"
SNAPSHOT = Path("/tmp/seams1125/frontier-prices.json")

# This exact text also serves as the prereg.section value: the runner rejects a notes field.
PROTOCOL = """PREVIEW ONLY. This is the maximum envelope, not an executable staged wave.
Stage A: 10 distinct instances per model, level and control arm; require at least 9 full successes in each arm.
Drop a level for a model if either control fails. Invalid or missing cells are not failures; retry once, then stop if incomplete.
Stage B: at the first passing level, run each matched probe pair on 12 fresh instances per model.
Use exact sequential McNemar at 3, 6 and 12 pairs, alpha 0.05, Holm over pairs within model.
At least 2 control-only successes in a pair for at least one model are required to continue.
Ensure 12 valid pairs before declaring a ceiling. An early efficacy stop with at least 2 control-only successes meets the signal gate;
a statistical futility stop does not establish a ceiling at this descriptive threshold. Continue the screen if 2 is still reachable.
If no pair meets the gate, report 'at the ceiling for this model class at this level' and try the next level that passed A.
Do not advance a model whose control gate failed. If no eligible level remains, stop the family.
Stage C: on fresh matched instances at the first signal level, compare team to team-fullrecord when present, otherwise solo.
Include all models that passed A at that level, even if only one model supplied the B signal.
Include hint and solo-seam when present. Use the smallest positive lapse effect from the card consistent with B;
choose n before C, at most 100, for 80% power at alpha 0.05. Use fixed-sample exact McNemar on full success.
A capped sample can have less than 80% power. If no card alternative fits, stop and revise the power plan before C.
A family without probe pairs has no attribution gate: week is only a descriptive demonstration (no headline claim).
Manual hard stops: do NOT run this entire preview. Split into separately committed stage/model/level plans after each gate;
rebuild with a fresh private seed, QA, oracle parity, and approval before paid work. Apply cumulative stage caps.
The runner has no cross-set eligibility gates, stage-specific samples, conditional sample sizing, or operator pause.
Repeat on Terminus 2 and one native command-line harness before claiming an effect; record the delivery mode.
Matched-token and cross-model team controls, and 5 human baselines, are required before publication.
The target result is a boundary cost on the same work, with a similar cost for self and teammate transfer;
hint recovers less than one third and a harness mechanism recovers at least two thirds. This pilot alone cannot establish that.
Kill if standard team reaches 0.9 of control and hint closes the gap, the effect reverses by harness, or heavy solo is below 0.9.
For digest heavy, require at least 0.95 solo success: 10/10 at this pilot size.
"""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def preregistration() -> dict:
    path = str(Path(__file__).resolve().relative_to(REPO))
    commit = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", path], cwd=REPO, text=True).strip()
    if not commit:
        raise ValueError("commit pilot_plan.py before generating a pilot, so the protocol is frozen")
    return {"commit": commit, "doc": path, "section": PROTOCOL}


def family_design(family: str, levels: tuple[str, ...]) -> tuple[list[str], list[tuple[str, str]], object]:
    if not family.isidentifier() or not (ROOT / "seams" / f"{family}.py").is_file():
        raise ValueError(f"no Seams family {family}")
    module = build_tasks.load_family(family)
    arms = list(dict.fromkeys((*module.ARMS, *getattr(module, "EXTRA_ARMS", ()))))
    if not {"solo", "team"} <= set(arms):
        raise ValueError(f"{family} requires solo and team arms")
    if not set(levels) <= set(getattr(module, "LEVELS", module.SEATS)):
        raise ValueError(f"{family} does not support every requested level: {levels}")
    pairs = [(control, trap) for trap, control in eval_card.trap_pairs(
        tuple(arms), eval_card.declared_pairs(module), []).items()]
    if any(control not in arms for control, _ in pairs):
        raise ValueError(f"{family} has a probe trap without a matched control")
    return arms, pairs, module


def power_rows(card: dict, level: str, pairs: list[tuple[str, str]]) -> list[dict]:
    """Positive binary success differences, not mean fractional reward differences."""
    rows = []
    for row in card.get("power", []):
        if (row["level"] != level or not row["policy"].startswith("lapse-")
                or (row["control"], row["treatment"]) not in pairs or not row["pairs"]
                or row["excluded_pairs"] or row["plus"] <= row["minus"]):
            continue
        rows.append({key: row[key] for key in ("policy", "control", "treatment", "pairs", "plus", "minus",
                                                "success_effect", "planned_n", "plan_status")})
    return sorted(rows, key=lambda row: (row["success_effect"], row["policy"], row["treatment"]))


def binomial_p(count: int, n: int, probability: float) -> float:
    """Two-sided exact binomial test, with equal-tailed (conservative) compatibility."""
    mass = [math.comb(n, k) * probability ** k * (1 - probability) ** (n - k) for k in range(n + 1)]
    return min(1.0, 2 * min(math.fsum(mass[:count + 1]), math.fsum(mass[count:])))


def compatible_power(rows: list[dict], signals: dict[tuple[str, str], tuple[int, int, int]]) -> dict | None:
    """Pick the smallest card effect compatible with an observed positive screen, not a fitted effect."""
    for row in rows:
        signal = signals.get((row["control"], row["treatment"]))
        if signal is None:
            continue
        b, c, n = signal
        if not (2 <= b <= n <= SIGNAL_N and 0 <= c <= n - b):
            continue
        if all(binomial_p(count, n, row[key] / row["pairs"]) >= 0.025
               for count, key in ((b, "plus"), (c, "minus"))):
            return row | {"n": min(HEADLINE_CAP, row["planned_n"] or HEADLINE_CAP)}
    return None


def stage_design(arms: list[str], pairs: list[tuple[str, str]], level: str, card: dict) -> tuple[list, list[dict]]:
    controls = [arm for arm in ("solo", "team-fullrecord") if arm in arms]
    primary = controls[-1]
    headline = [primary, "team", *(arm for arm in ("hint", "solo-seam") if arm in arms)]
    rows = power_rows(card, level, pairs)
    n = max((min(HEADLINE_CAP, row["planned_n"] or HEADLINE_CAP) for row in rows), default=HEADLINE_CAP)
    if not pairs:
        n = CONTROL_N
    return [("A", controls, list(range(CONTROL_N))),
            *(("B", list(pair), list(range(CONTROL_N, CONTROL_N + SIGNAL_N))) for pair in pairs),
            ("C", headline, list(range(CONTROL_N + SIGNAL_N, CONTROL_N + SIGNAL_N + n)))], rows


def task_sets(args: argparse.Namespace, module: object, arms: list[str], pairs: list, card: dict, scratch: Path):
    sets, details, samples, power = [], [], defaultdict(list), {}
    for level in args.levels:
        design, power[level] = stage_design(arms, pairs, level, card)
        seats = module.SEATS.get(level) if isinstance(module.SEATS, dict) else module.SEATS
        root = scratch / level
        for stage in STAGES:
            selected = [(arm_names, instances) for tag, arm_names, instances in design if tag == stage]
            if not selected:
                continue
            stage_root = root / f"stage-{stage.lower()}"
            groups = defaultdict(list)
            for arm_names, instances in selected:
                groups[tuple(instances)].extend(arm_names)
            for instances, arm_names in groups.items():
                paths = build_tasks.build(args.family, list(instances), seats, level, PILOT_SEED,
                                          tuple(dict.fromkeys(arm_names)), stage_root, policy="careful")
                by_arm = defaultdict(list)
                for path in paths:
                    meta = token_profile.task_meta(path)
                    by_arm[meta["condition"]].append((meta["instance"], path))
                for arm, entries in by_arm.items():
                    if len(entries) != len(instances):
                        raise ValueError(f"{level}/{stage}/{arm}: incomplete build")
                    key = (level, arm)
                    if not samples[key]:
                        samples[key].extend(path for _, path in sorted(entries)[:PROFILE_N])
                for requested_arms, requested_instances in selected:
                    if tuple(requested_instances) != instances:
                        continue
                    # The runner refuses a set with different scripted rosters; pairs still share instance IDs.
                    rosters = defaultdict(list)
                    for arm in requested_arms:
                        roster = {run_wave.roster_of(path) for _, path in by_arm[arm]}
                        if len(roster) != 1:
                            raise ValueError(f"{level}/{arm}: roster changes by instance")
                        _, scripted = next(iter(roster))
                        rosters[scripted].append(arm)
                    if stage == "B" and len(rosters) != 1:
                        raise ValueError(f"{level}: probe pair has different scripted rosters")
                    for group in rosters.values():
                        sets.append({"tasks_dir": str(stage_root), "manifest": str(stage_root / "manifest.json"),
                                     "arms": group, "instances": list(instances)})
                        details.append({"stage": stage, "level": level, "arms": group, "n": len(instances)})
    # Order A across every level, then B, then C. The runner's dry-run order is not this stage order.
    order = sorted(range(len(sets)), key=lambda i: STAGES.index(details[i]["stage"]))
    return [sets[i] for i in order], [details[i] for i in order], samples, power


def profile_rows(args: argparse.Namespace, samples: dict) -> list[dict]:
    runs = defaultdict(list)
    for directory in args.playtest_profile:
        result = json.loads((directory / "result.json").read_text())
        level = token_profile.task_meta(Path(result["task"]))["difficulty"]
        if level not in args.levels:
            raise ValueError(f"{directory}: playtest level {level} not in the pilot")
        runs[level].append(directory)
    rows = []
    for level in args.levels:
        paths = [path for (dose, _), group in samples.items() if dose == level for path in group]
        model_samples = token_profile.model_samples(paths, args.family, level, args.calls_per_turn, 0)
        declared = token_profile.profiles(model_samples, args.family, level, args.uncertainty)
        if runs[level]:
            measured = token_profile.profiles(token_profile.playtest_samples(runs[level], args.family, level),
                                              args.family, level, args.uncertainty)
            replacements = {(row["arm"], row["role"]): row for row in measured}
            if not replacements.keys() <= {(row["arm"], row["role"]) for row in declared}:
                raise ValueError(f"{level}: playtest includes a role or arm outside the pilot")
            declared = [replacements.get((row["arm"], row["role"]), row) for row in declared]
        rows.extend(declared)
    pricing.validate_profiles(rows)
    return rows


class PriceSnapshot:
    def __init__(self, path: Path):
        data = json.loads(path.read_text())
        if isinstance(data, list):
            self.payload = {"data": [{"id": row["id"], "pricing": {
                "prompt": str(row["in_per_m"] / 1e6), "completion": str(row["out_per_m"] / 1e6),
                **({"input_cache_read": str(row["cache_read_per_m"] / 1e6)}
                   if row.get("cache_read_per_m") is not None else {})}} for row in data]}
            self.zdr_payload = {"data": [{"model_id": row["id"], "provider_name": "snapshot"}
                                          for row in data if row.get("zdr_endpoints", 0)]}
        else:
            self.payload = data["catalog"]
            self.zdr_payload = data["zdr"]
        date = datetime.fromtimestamp(path.stat().st_mtime, UTC).astimezone(ZoneInfo("America/New_York"))
        self.source = f"OpenRouter public catalog ({PRICE_URL}), snapshot `{path.name}`, {date.date()} ET"
        self.prices = pricing.parse_openrouter_models(self.payload)

    def catalog(self) -> dict:
        return self.payload

    def zdr(self) -> dict:
        return self.zdr_payload


def snapshot_for(path: Path | None, scratch: Path) -> PriceSnapshot:
    if path is not None:
        return PriceSnapshot(path)
    if SNAPSHOT.is_file():
        return PriceSnapshot(SNAPSHOT)
    local = scratch / "openrouter-prices.json"
    if not local.is_file():
        public = run_wave.HttpSources({})
        write_json(local, {"catalog": public.catalog(), "zdr": public.zdr()})
    return PriceSnapshot(local)


def cost_rows(plan: dict, details: list[dict], snapshot: PriceSnapshot, history: Path) -> list[dict]:
    layouts = pricing.cell_layouts(plan, REPO, history)
    rows = {}
    for cell in run_wave.order_of(plan):
        index, arm, model = cell["task_set"], cell["arm"], cell["model"]
        kind, seats, scripted, level = layouts[index, cell["instance"], arm]
        detail = details[index]
        key = (detail["stage"], level, arm, model)
        row = rows.setdefault(key, {"stage": key[0], "level": level, "arms": arm, "model": model,
                                    "trials": 0, "model_seats": 0, "seats": 0, "low": 0., "expected": 0.,
                                    "high": 0., "model_high": 0.})
        row["trials"] += 1
        row["seats"] += len(seats)
        tested = pricing.cell_seat_models(cell, seats, scripted)
        row["model_seats"] += len(tested)
        for seat, name in tested:
            profile = pricing.declared_profile(plan["token_profiles"], kind, level, arm, seat)
            if profile is None:
                raise ValueError(f"missing declared profile for {kind}/{level}/{arm}/{seat}")
            cost = pricing.openrouter_cost(name, profile, snapshot.prices)
            uncertainty = profile["uncertainty"]
            row["low"] += cost / uncertainty
            row["expected"] += cost
            row["high"] += cost * uncertainty
            row["model_high"] += cost * uncertainty
        for field in ("low", "expected", "high"):
            row[field] += pricing.MODAL_PER_TRIAL_USD
    return sorted(rows.values(), key=lambda row: (STAGES.index(row["stage"]), LEVELS.index(row["level"]),
                                                   row["arms"], row["model"]))


TOTAL_FIELDS = ("trials", "seats", "model_seats", "low", "expected", "high", "model_high")


def totals(rows: list[dict]) -> dict:
    return {key: sum(row[key] for row in rows) for key in TOTAL_FIELDS}


def dry_run(path: Path, snapshot: PriceSnapshot, history: Path) -> str:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        run_wave.run(path, dry_run=True, sources=snapshot, base_env={}, history_jobs=history)
    return output.getvalue()


def check_forecast(text: str, rows: list[dict]) -> None:
    match = re.search(r"low / expected / high total: \$([\d.]+) / \$([\d.]+) / \$([\d.]+)", text)
    count = re.search(r"^\S+: (\d+) cells,", text, re.MULTILINE)
    summed = totals(rows)
    if not match or not count or int(count[1]) != summed["trials"]:
        raise ValueError("dry run did not price all of the pilot's trials")
    if any(abs(float(actual) - summed[key]) > 0.000051 for actual, key in
           zip(match.groups(), ("low", "expected", "high"), strict=True)):
        raise ValueError("dry run differs from snapshot forecast (check the runner's price cache and history)")


def build_plan(args: argparse.Namespace, scratch: Path, snapshot: PriceSnapshot, prereg: dict) -> dict:
    arms, pairs, module = family_design(args.family, args.levels)
    card_path = args.cards / f"{args.family}.card.json"
    card = json.loads(card_path.read_text()) if card_path.is_file() else {}
    if card and card["identity"]["family"] != args.family:
        raise ValueError(f"wrong family in {card_path}")
    if card and not card["passed"]:
        raise ValueError(f"eval card failed for {args.family}")
    sets, details, samples, power = task_sets(args, module, arms, pairs, card, scratch)
    profiles = profile_rows(args, samples)
    plan = {"name": f"seams-{args.family}-pilot", "prereg": prereg, "task_sets": sets, "models": list(args.models),
            "agent": "terminus-2", "make_jobs": {"max_turns": 100, "env": "modal", "concurrency": 10,
                                                   "start_spacing_s": 5.0},
            "budget": {"cap_usd": 0, "reserve_per_trial_usd": 0, "provider_floors": {"openrouter": 2.0}},
            "order_seed": 20261125, "token_profiles": profiles}
    history = scratch / "empty-history"
    rows = cost_rows(plan, details, snapshot, history)
    plan["budget"]["cap_usd"] = args.cap_usd if args.cap_usd is not None else math.ceil(totals(rows)["model_high"])
    plan["budget"]["reserve_per_trial_usd"] = math.ceil(max(row["model_high"] / row["trials"] for row in rows) * 100) / 100
    path = args.out / f"{args.family}.json"
    preview = scratch / "preview.json"
    write_json(preview, plan)
    text = dry_run(preview, snapshot, history)
    (scratch / "dry-run.txt").write_text(text)
    check_forecast(text, rows)
    write_json(path, plan)
    rules = stages.SequentialTest(list(LOOKS), family_size=max(1, len(pairs)), alpha=0.05)
    return {"family": args.family, "plan": path.name, "levels": list(args.levels), "models": list(args.models),
            "rows": rows, "sets": details, "cap": plan["budget"]["cap_usd"], "price_source": snapshot.source,
            "profiles": sorted({row["source"] for row in profiles}), "uncertainty": args.uncertainty,
            "card": str(card_path.name) if card else "unavailable", "power": power, "pairs": pairs,
            "looks": list(LOOKS), "futility_bounds": list(rules.futility_bounds),
            "scratch": str(scratch), "max_turns": plan["make_jobs"]["max_turns"]}


def range_text(row: dict) -> str:
    return " / ".join(f"${row[key]:.4f}" for key in ("low", "expected", "high"))


def count_text(row: dict) -> str:
    return f"{row['trials']} | {row['model_seats']} / {row['seats']} | {range_text(row)}"


def family_lines(result: dict) -> list[str]:
    family = result["family"]
    if "error" in result:
        return [f"## {family}", "", f"**Not forecast:** {result['error']}", ""]
    rows = result["rows"]
    lines = [f"## {family}", "", f"Plan: `{result['plan']}`. Price source: {result['price_source']}.",
             f"Model cap: ${result['cap']:.2f}. Profiles (uncertainty ×{result['uncertainty']:g}):", "",
             *(f"- {source}" for source in result["profiles"]), "",
             "| Stage | Level | Arm | Model | Trials | Model / all seats | Cost low / expected / high |",
             "|---|---|---|---|---:|---:|---:|"]
    by_cell = defaultdict(list)
    for row in rows:
        by_cell[row["stage"], row["level"], row["arms"]].append(row)
    for (stage, level, arm), group in by_cell.items():
        prefix = f"| {stage} | {level} | {arm} |"
        lines.extend(f"{prefix} {row['model'].removeprefix('openrouter/')} | {count_text(row)} |" for row in group)
        lines.append(f"{prefix} **Total** | {count_text(totals(group))} |")
    lines.extend(["", ("| Stage | Model | Added trials | Added model / all seats | Added low / expected / high | "
                       "Cumulative trials | Cumulative model / all seats | Cumulative low / expected / high | "
                       "Cumulative model high (stop) |"),
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|"])
    for stage in STAGES:
        for model in (*result["models"], "all"):
            selected = [row for row in rows if model == "all" or row["model"] == model]
            added = totals([row for row in selected if row["stage"] == stage])
            cumulative = totals([row for row in selected if STAGES.index(row["stage"]) <= STAGES.index(stage)])
            if not added["trials"]:
                continue
            lines.append(f"| {stage} | {model.removeprefix('openrouter/')} | {count_text(added)} | "
                         f"{count_text(cumulative)} | ${min(result['cap'], math.ceil(cumulative['model_high'])):.2f} |")
    lines.extend(["", (f"Power source: `{result['card']}`. Stage B exact looks: {result['looks']}; "
                       "no optional non-binding futility. Stage C envelope (the largest eligible card n, capped):"), ""])
    for level, options in result["power"].items():
        n = next(row["n"] for row in result["sets"] if row["stage"] == "C" and row["level"] == level)
        lines.append(f"- {level}: {n} pairs/model maximum. " + (
            "No probes; descriptive demonstration only." if not result["pairs"] else
            "No eligible card alternatives; forecast only, obtain a valid card before C." if not options else
            "Lapse alternatives (control-only minus trap-only full-success rate, card n): " + "; ".join(
                f"{row['treatment']} {row['policy']} {row['success_effect']:.3f}, "
                f"{row['planned_n'] or row['plan_status']}" for row in options)))
    lines.extend(["", "Task sets (zero-based runner index): " + "; ".join(
        f"{i}={row['stage']}/{row['level']}/{','.join(row['arms'])}" for i, row in enumerate(result["sets"])) + ".", ""])
    return lines


def render_forecast(results: list[dict]) -> str:
    lines = ["# Seams pilot forecast", "", "**No model run. No spend approval. Preview plans only.**", "",
             PROTOCOL.rstrip(), "", "## Forecast assumptions", "",
             ("- All eligible levels and models are included in the maximum envelope; actual B checks levels in order. "
              "C uses the first signal level and every model that passed A there. A, B and C use disjoint instance IDs."),
             ("- For B, b is control-only full success and c is trap-only full success. For C use the smallest positive "
              "lapse alternative not rejected by equal-tailed exact binomial tests of b and c at 0.025 each. "
              "The card's paired n is an estimate, not observed model power; probe effect may not transfer to team."),
             ("- Each row is a maximum count (no early stop, no retries). The cap is the cumulative model-only high "
              "estimate rounded up to USD, unless overridden. Sandbox spend is outside the model cap."),
             (f"- Modal is ${pricing.MODAL_PER_TRIAL_USD:.4f}/trial (measured 2026-11-24); scripted seats have no model cost. "
              "Costs include sandbox spend, not invalid retries or second-harness replication."),
             (f"- Profile simulation: first {PROFILE_N} instances per level and arm, careful policy, public disposable "
              "seed 1, schedule seed 0. Playtest rows override matching arm/role only; other rows use turn estimates."),
             ("- One homogeneous model per trial; Terminus 2, max 100 turns, Modal, concurrency 10, starts spaced 5 s. "
              "Turn estimates extrapolate probe history; model cache and turn costs may differ. No assumed compaction."),
             ("- The runner has a single agent and roster design; the second harness and cross-model and matched-token "
              "arms need separate approved plans. A second pass at the same counts and profile would double these totals."),
             "- Low/high = model estimate divided/multiplied by uncertainty, plus fixed sandbox cost. Not a confidence interval.",
             "- Rebuild using private seeds before real runs. The current built sets are for forecast and simulation only.", ""]
    for result in results:
        lines.extend(family_lines(result))
    lines.extend(["## Suite: maximum envelope, one harness", "",
                  "| Family | Trials | Model / all seats | Cost low / expected / high | Model cap |",
                  "|---|---:|---:|---:|---:|"])
    all_rows = []
    cap = 0.
    for result in results:
        if "error" in result:
            lines.append(f"| {result['family']} | unavailable | — | — | — |")
            continue
        all_rows.extend(result["rows"])
        cap += result["cap"]
        lines.append(f"| {result['family']} | {count_text(totals(result['rows']))} | ${result['cap']:.2f} |")
    lines.append(f"| **Total** | {count_text(totals(all_rows))} | ${cap:.2f} |")
    lines.append("")
    return "\n".join(lines)


def csv(value: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in value.split(","))
    if not all(values) or len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("expected distinct comma-separated names")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--models", required=True, type=csv)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--levels", type=csv, default=("standard", "heavy"))
    parser.add_argument("--playtest-profile", type=Path, nargs="+", default=[])
    parser.add_argument("--calls-per-turn", type=float, default=1.)
    parser.add_argument("--uncertainty", type=float, default=2.)
    parser.add_argument("--cap-usd", type=float)
    parser.add_argument("--prices", type=Path, help="cached public OpenRouter price snapshot")
    parser.add_argument("--cards", type=Path, default=ROOT / "docs/seams/cards")
    parser.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()) / "flotilla-pilot")
    parser.add_argument("--forecast", type=Path, default=ROOT / "docs/seams/pilot-forecast.md")
    args = parser.parse_args(argv)
    if not args.family.isidentifier() or not set(args.levels) <= set(LEVELS):
        parser.error("invalid family or dose level")
    args.levels = tuple(level for level in LEVELS if level in args.levels)
    if (not math.isfinite(args.calls_per_turn) or args.calls_per_turn <= 0 or
            not math.isfinite(args.uncertainty) or args.uncertainty < 1 or
            (args.cap_usd is not None and (not math.isfinite(args.cap_usd) or args.cap_usd < 0))):
        parser.error("invalid calls-per-turn, uncertainty or cap")
    args.models = tuple(model if model.startswith("openrouter/") else f"openrouter/{model}" for model in args.models)
    if len(set(args.models)) != len(args.models):
        parser.error("duplicate normalized model IDs")
    args.scratch = args.scratch.resolve()
    args.out = args.out.resolve()
    args.scratch.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"{args.family}-", dir=args.scratch))
    try:
        result = build_plan(args, scratch, snapshot_for(args.prices, args.scratch), preregistration())
    except (OSError, ValueError, KeyError, TypeError, SystemExit, RuntimeError, ImportError) as exc:
        result = {"family": args.family, "error": str(exc), "scratch": str(scratch)}
        print(f"{args.family}: no forecast: {exc}", file=sys.stderr)
    write_json(args.scratch / f"forecast-{args.family}.json", result)
    results = [json.loads(path.read_text()) for path in sorted(args.scratch.glob("forecast-*.json"))]
    args.forecast.parent.mkdir(parents=True, exist_ok=True)
    args.forecast.write_text(render_forecast(results))
    if "error" not in result:
        print(f"{args.family}: {totals(result['rows'])['trials']} trials; {range_text(totals(result['rows']))}; "
              f"cap ${result['cap']:.2f}; dry run: {scratch / 'dry-run.txt'}")
    return int("error" in result)


if __name__ == "__main__":
    raise SystemExit(main())
