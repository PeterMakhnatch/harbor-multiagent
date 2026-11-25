"""Build a zero-model EvalCard from real task builds and deterministic policy simulations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import secrets
import statistics
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from itertools import pairwise
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
LEVELS = ("canary", "standard", "heavy")
POLICIES = ("careful", "naive", "nop", "blind", "random", "lone")
PRICES = (1, 5, 15)
CHANCE_TOLERANCE = 0.10
TOKEN_MODEL = ("Estimate: 4 characters per input token; repeat the instruction, JSON briefing and seat files "
               "once per simulated world request (at least once per model seat). Excludes scripted seats, "
               "system prompts, replies, history and output tokens. Requests are not measured model turns.")


# Kit adapter. Keep builder, policy, QA and simulator interfaces here.
class Kit:
    def __init__(self, args: argparse.Namespace):
        import qa
        import sim

        self.qa = qa
        self.sim = sim
        self.builder = sim.BUILD
        self.family = args.family
        self.module = self.builder.load_family(args.family)
        self.extension = self.builder.family_path(args.family).parent.name == "seams"
        self.variant = args.variant or getattr(self.module, "DEFAULT_VARIANT", "")
        if hasattr(self.module, "kind"):
            self.module = self.module.kind(self.variant)
        self.custom = self.builder.load_policy(args.family)
        self.seats = args.seats if args.seats is not None else getattr(self.module, "SEATS", 2)
        self.options = {"read_first_subject": args.read_first} if args.read_first else {}

    def arms(self) -> tuple[str, ...]:
        return tuple(getattr(self.module, "ARMS", ("solo", "silent", "talk")))

    def supports(self, policy: str) -> bool:
        if policy == "nop" or (self.custom is not None and self.custom.supports(policy, self.variant)):
            return True
        return not self.extension and (policy == "careful" or (
            policy == "naive" and getattr(self.module, "POOLING", True)))

    def seat_count(self, difficulty: str) -> int | None:
        return self.seats.get(difficulty) if isinstance(self.seats, dict) else self.seats

    def difficulty(self, level: str, seed: int) -> str | None:
        declared = next((getattr(self.module, name) for name in ("LEVELS", "DOSES", "SETTINGS")
                         if getattr(self.module, name, None)), None)
        if declared is not None:
            if level in declared:
                return level
            legacy = {"canary": "easy", "heavy": "hard"}.get(level)
            return legacy if legacy in declared and not set(LEVELS).intersection(declared) else None
        try:
            self.module.generate(seed, 0, self.seat_count(level), level)
        except ValueError as exc:
            message = str(exc).lower()
            if not any(word in message for word in ("difficulty", "dose", "level")):
                raise
            # Round 1's fixture uses easy/hard and declares no level metadata.
            if "easy or hard" in message:
                return {"canary": "easy", "heavy": "hard"}.get(level)
            return None
        return level

    def build(self, root: Path, level: str, difficulty: str, arms: tuple[str, ...], instances: int,
              seed: int) -> list[Any]:
        policy = "careful" if self.supports("careful") else "nop"
        paths = self.builder.build(self.family, list(range(instances)), self.seat_count(difficulty), difficulty,
                                   seed, arms, root / level, variant=self.variant, policy=policy, **self.options)
        return [self.qa.load_task(path) for path in paths]

    def views(self, task: Any, subjects_only: bool = False) -> dict[str, dict[str, str]]:
        seats = task.under_test if subjects_only else task.seats
        return {seat: {key: value for key, value in task.view(seat).items() if key != "env.SEAT_TOKEN"}
                for seat in seats}

    def leak_evidence(self, task: Any, arms: tuple[str, ...]) -> list[str]:
        findings = list(task.errors)
        for check in (self.qa.check_example_leak, self.qa.check_seat_secrets, self.qa.check_extension):
            findings.extend(check(task))
        findings.extend(f for f in task.errors if f not in findings)
        evidence = [f"{task.condition}/{task.instance}: {f.rule} ({f.seat})" for f in findings if f.level == "FAIL"]
        common = {word.casefold() for word in getattr(self.module, "COMMON_WORDS", ())}
        words = getattr(self.module, "ARM_WORDS", arms)
        words = [word for word in words if word.casefold() not in common]
        key = task.task_json.get("answer_key", task.task_json.get("params", {}).get("answer_key"))
        if key is None and self.family == "kit_fixture":
            key = task.task_json.get("params", {}).get("total")
        values = scalar_values(key)
        if hasattr(self.module, "leak_values"):
            values.extend(scalar_values(list(self.module.leak_values(task.task_json))))
        for seat, view in self.views(task).items():
            for field, text in view.items():
                decoded = self.qa.decoded(text)
                if any(word_hit(word, decoded) or word_hit(word, field) for word in words):
                    evidence.append(f"{task.condition}/{task.instance}: arm name in {seat}/{field}")
                if any(word_hit(value, decoded) for value in values):
                    evidence.append(f"{task.condition}/{task.instance}: answer value in {seat}/{field}")
        return evidence

    def chance(self, task: Any, level: str, policy: str) -> float | None:
        hook = getattr(self.module, "chance_baseline", None)
        if callable(hook):
            value = hook(task.task_json, policy)
        else:
            value = task.task_json.get("params", {}).get("chance_baseline")
            if value is None:
                value = getattr(self.module, "CHANCE", getattr(self.module, "CHANCE_BASELINE", None))
            for name in (level, task.condition, policy):
                if isinstance(value, dict) and name in value:
                    value = value[name]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
            return float(value)
        return None

    def pair(self, first: Any, second: Any) -> tuple[dict, list[str]]:
        a, b = self.views(first, True), self.views(second, True)
        flat_a = {(seat, field): text for seat, view in a.items() for field, text in view.items()}
        flat_b = {(seat, field): text for seat, view in b.items() for field, text in view.items()}
        changed = []
        for key in sorted(flat_a.keys() | flat_b.keys()):
            left, right = flat_a.get(key), flat_b.get(key)
            if left != right:
                changed.append({"seat": key[0], "field": key[1], "control_chars": len(left or ""),
                                "trap_chars": len(right or ""), "edit_chars": changed_chars(left or "", right or "")})
        evidence = {"instance": first.instance, "control": first.condition, "trap": second.condition,
                    "changed_fields": changed, "edit_chars": sum(item["edit_chars"] for item in changed)}
        hook = getattr(self.module, "pair_difference", None)
        if callable(hook):
            errors = list(hook(first.condition, second.condition, a, b))
        else:
            errors = ["subject-visible fields differ without a validating pair_difference hook"] if changed else []
        return evidence, errors

    def sizes(self, task: Any) -> dict[str, dict]:
        return {seat: {"briefing_chars": len(view.get("env.SEAT_BRIEFING", "")),
                       "instruction_chars": len(view.get("instruction", "")),
                       "file_chars": sum(len(text) for name, text in view.items() if name.startswith("image:")
                                         and not name.endswith("Dockerfile")), "under_test": seat in task.under_test}
                for seat, view in self.views(task).items()}


def simulate_job(job: tuple[Path, str, int, bool]) -> dict[str, Any]:
    import sim

    path, policy, schedule, stall = job
    limit = sim.MAX_STEPS
    if stall:
        sim.MAX_STEPS = 1
    try:
        result = sim.simulate_built(path, policy, schedule)
    except RuntimeError as exc:
        if stall and "scheduler steps" in str(exc):
            return {"unsupported_stall": True}
        return {"reward": None, "invalid": True, "error": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001 - a failed simulation must remain an invalid matrix cell
        return {"reward": None, "invalid": True, "error": type(exc).__name__}
    finally:
        sim.MAX_STEPS = limit
    score = result.get("score") or {}
    invalid = bool(result.get("invalid") or result.get("stalled") or score.get("invalid") or score.get("error"))
    events = json.dumps(result.get("events", []), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"reward": result.get("reward"), "invalid": invalid, "requests": result.get("requests", {}),
            "event_sha256": hashlib.sha256(events.encode()).hexdigest(), "event_count": len(result.get("events", []))}


# Card computation. No hidden task values or raw event traces enter the output.
def scalar_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in scalar_values(child)]
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in scalar_values(child)]
    return [str(value)] if isinstance(value, (str, int, float)) and not isinstance(value, bool) and value != "" else []


def word_hit(word: str, text: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text, re.IGNORECASE))


def changed_chars(left: str, right: str) -> int:
    """Length of differing middles after a shared prefix and suffix are removed."""
    prefix = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        prefix += 1
    suffix = 0
    for a, b in zip(reversed(left[prefix:]), reversed(right[prefix:]), strict=False):
        if a != b:
            break
        suffix += 1
    return len(left) + len(right) - 2 * (prefix + suffix)


def declared_pairs(module: Any) -> list[tuple[str, str]]:
    pairs = []
    for name in ("TWINS", "PAIRS"):
        entries = getattr(module, name, ())
        for entry in entries:
            if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                a, b = entry[:2]
                if isinstance(a, str) and isinstance(b, str) and (a, b) not in pairs and (b, a) not in pairs:
                    pairs.append((a, b))
    return pairs


def trap_pairs(arms: tuple[str, ...], twins: list[tuple[str, str]], overrides: list[str]) -> dict[str, str]:
    pairs = {arm: arm.removesuffix("-trap") + "-control" for arm in arms if arm.endswith("-trap")}
    for a, b in twins:
        if a.endswith("-trap") and b.endswith("-control"):
            pairs[a] = b
        elif b.endswith("-trap") and a.endswith("-control"):
            pairs[b] = a
    for override in overrides:
        trap, sep, control = override.partition("=")
        if not sep or not trap or not control or trap == control:
            raise ValueError("--pair must be TRAP=CONTROL with two distinct arms")
        pairs[trap] = control
    return pairs


def policy_matrix(runs: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for run in runs:
        groups[run["level"], run["arm"], run["policy"]].append(run)
    matrix = []
    for (level, arm, policy), group in groups.items():
        rewards = [r["reward"] for r in group if not r["invalid"] and r["reward"] is not None]
        matrix.append({"level": level, "arm": arm, "policy": policy, "n": len(group), "valid_n": len(rewards),
                       "invalid_n": len(group) - len(rewards), "mean": statistics.mean(rewards) if rewards else None,
                       "min": min(rewards) if rewards else None, "max": max(rewards) if rewards else None})
    return matrix


def check(name: str, rule: str, status: str, evidence: str) -> dict:
    return {"name": name, "rule": rule, "status": status, "evidence": evidence}


def limit_check(name: str, rule: str, rows: list[dict], field: str, predicate: Any, description: str) -> dict:
    if not rows:
        return check(name, rule, "NA", f"No applicable policy/arm/level; {description}")
    bad = [r for r in rows if r["invalid_n"] or r[field] is None or not predicate(r[field])]
    detail = ", ".join(f"{r['level']}/{r['arm']}/{r['policy']}: {r[field]} ({r['invalid_n']} invalid)" for r in bad)
    return check(name, rule, "FAIL" if bad else "PASS", detail or f"{sum(r['n'] for r in rows)} runs; {description}")


def separation_check(matrix: list[dict], pairs: dict[str, str], levels: list[str], arms: tuple[str, ...]) -> dict:
    cells = {(r["level"], r["arm"], r["policy"]): r for r in matrix}
    failures, missing, evidence = [], [], []
    controls = sorted(set(pairs.values()) | {arm for arm in arms if arm.endswith("-control")})
    for level in levels:
        for trap in pairs:
            label = f"{level}/{trap}"
            rows = [cells.get((level, trap, name)) for name in ("careful", "naive")]
            if any(r is None for r in rows):
                missing.append(label)
                continue
            good, bad = rows
            if any(r["invalid_n"] or r["mean"] is None for r in rows):
                failures.append(f"{label}: invalid simulation")
                continue
            gap = good["mean"] - bad["mean"]
            evidence.append(f"{label}: gap {gap:.3f}")
            if gap < 0.5:
                failures.append(label)
        for control in controls:
            label = f"{level}/{control}"
            row = cells.get((level, control, "naive"))
            if row is None:
                missing.append(label)
                continue
            evidence.append(f"{label}: naive min {row['min']}")
            if row["invalid_n"] or row["min"] != 1:
                failures.append(label)
    status = "FAIL" if failures else "NA" if missing or not evidence else "PASS"
    return check("separation", "A4", status, "; ".join(evidence + [f"failed: {v}" for v in failures]
                 + [f"missing: {v}" for v in missing]) or "No trap/control arms")


def chance_check(matrix: list[dict], runs: list[dict], pairs: dict[str, str]) -> dict:
    rows = [r for r in matrix if r["policy"] in ("blind", "random") and (r["arm"] == "team" or r["arm"] in pairs)]
    if not rows:
        return check("chance", "A3", "NA", "No blind/random policy in a team or trap arm")
    evidence, failures, missing = [], [], []
    for row in rows:
        level, arm, policy = row["level"], row["arm"], row["policy"]
        label = f"{level}/{arm}/{policy}"
        baselines = [r.get("chance_baseline") for r in runs if (r["level"], r["arm"], r["policy"])
                     == (level, arm, policy)]
        if not baselines or any(value is None for value in baselines):
            missing.append(label)
            continue
        baseline = statistics.mean(baselines)
        mean = row["mean"]
        evidence.append(f"{label}: actual {mean}, expected {baseline:.4f} (absolute tolerance {CHANCE_TOLERANCE})")
        if mean is None or row["invalid_n"] or abs(mean - baseline) > CHANCE_TOLERANCE:
            failures.append(label)
    return check("chance", "A3", "FAIL" if failures else "NA" if missing else "PASS",
                 "; ".join(evidence + [f"failed: {x}" for x in failures] + [f"missing baseline: {x}" for x in missing]))


def dose_check(matrix: list[dict]) -> dict:
    cells = {r["level"]: r for r in matrix if r["arm"] == "team" and r["policy"] == "naive"}
    if "canary" not in cells or "heavy" not in cells:
        return check("dose response", "A5/I1", "NA", "Needs naive team at canary and heavy")
    levels = [level for level in LEVELS if level in cells]
    means = [cells[level]["mean"] for level in levels]
    valid = all(not cells[level]["invalid_n"] and cells[level]["mean"] is not None for level in levels)
    passed = valid and all(a >= b for a, b in pairwise(means))
    return check("dose response", "A5/I1", "PASS" if passed else "FAIL", f"{' → '.join(levels)}: {means}")


def schedule_check(runs: list[dict], matrix: list[dict], seeds: int) -> dict:
    careful = [r for r in matrix if r["policy"] == "careful"]
    row = limit_check("schedule independence", "E3", careful, "min", lambda x: x == 1,
                      f"careful scores 1 across {seeds} schedule seed(s)")
    groups = defaultdict(list)
    for run in runs:
        if run["policy"] == "naive" and not run["invalid"] and run["reward"] is not None:
            groups[run["level"], run["arm"], run["instance"]].append(run["reward"])
    spread = max((max(values) - min(values) for values in groups.values()), default=None)
    row["evidence"] += f"; max within-instance naive spread: {spread}"
    row["naive_spread"] = spread
    if seeds < 2 and row["status"] == "PASS":
        row["status"] = "NA"
        row["evidence"] += "; at least 2 schedule seeds required"
    return row


def make_checks(card: dict, pairs: dict[str, str], twins: list[tuple[str, str]]) -> list[dict]:
    matrix, runs = card["matrix"], card["runs"]
    identity = card["identity"]
    levels = [level for level, difficulty in identity["levels"].items() if difficulty is not None]
    missing = [level for level, difficulty in identity["levels"].items() if difficulty is None]
    careful = [r for r in matrix if r["policy"] == "careful"]
    result = [check("levels", "A5", "NA" if missing else "PASS", f"Missing: {', '.join(missing)}" if missing
                    else "All requested dose levels available"),
              limit_check("solvable", "A2/B1", careful, "min", lambda x: x == 1, "careful reward = 1"),
              limit_check("easy alone", "A2", [r for r in careful if r["arm"] == "solo"], "min",
                          lambda x: x == 1, "solo careful reward = 1"),
              limit_check("floor", "A3", [r for r in matrix if r["policy"] == "nop"], "max",
                          lambda x: x <= .05, "every nop reward ≤ 0.05"),
              chance_check(matrix, runs, pairs), separation_check(matrix, pairs, levels, identity["arms"]),
              dose_check(matrix),
              limit_check("one seat is not enough", "A3/C1", [r for r in matrix if r["policy"] == "lone"
                          and r["arm"] == "team"], "max", lambda x: x <= .5, "every lone team reward ≤ 0.5"),
              schedule_check(runs, matrix, identity["schedule_seeds"])]
    result.append(limit_check("simulation validity", "F1", matrix, "invalid_n", lambda x: x == 0,
                              "No invalid runs in the ordinary sweep"))
    if missing:
        for row in result:
            if row["name"] in ("solvable", "easy alone") and row["status"] == "PASS":
                row["status"] = "NA"
                row["evidence"] += "; requested levels missing"
    replays = card["replays"]
    passed = all(r["same"] for r in replays)
    result.append(check("determinism", "E1/E2", "PASS" if replays and passed else "FAIL" if replays else "NA",
                        f"{sum(r['same'] for r in replays)}/{len(replays)} sample event hashes match"))
    static = card["static"]
    leaks = static["leaks"]
    result.append(check("no leak", "C1/D2", "FAIL" if leaks else "PASS" if static["tasks"] else "NA",
                        "; ".join(leaks) or f"{static['tasks']} built tasks; seat views and family QA scanned"))
    comparisons = static["pairs"]
    errors = [error for pair in comparisons for error in pair["errors"]]
    missing_pairs = [f"{trap}={control}" for trap, control in pairs.items()
                     if not any(p["trap"] == trap and p["control"] == control for p in comparisons)]
    summaries = []
    for trap, control in pairs.items():
        group = [p for p in comparisons if (p["trap"], p["control"]) == (trap, control)]
        if group:
            fields = sorted({f"{f['seat']}/{f['field']}" for p in group for f in p["changed_fields"]})
            summaries.append(f"{control}→{trap}: {len(group)} pairs, max {max(p['edit_chars'] for p in group)} "
                             f"changed chars; fields: {', '.join(fields) or 'none'}")
    result.append(check("pairs", "A1/D2", "FAIL" if errors else "NA" if missing_pairs or not comparisons else "PASS",
                        "; ".join(summaries + errors + [f"missing {p}" for p in missing_pairs]) or "No built pairs"))
    required = set(pairs) & set(identity["arms"])
    if "team" in identity["arms"]:
        required.add("team")
    known = set(identity["known_arms"])
    declared = {a for a, b in twins if b in known} | {b for a, b in twins if a in known}
    unpaired = sorted(required - declared)
    result.append(check("twin rule", "A1", "FAIL" if unpaired else "PASS" if required else "NA",
                        f"No declared twin: {', '.join(unpaired)}" if unpaired else "Every team/trap has a declared twin"
                        if required else "No team or trap arm"))
    stall = card["stall"]
    if not stall or stall.get("unsupported_stall"):
        result.append(check("invalid is not zero", "F1", "NA", "Kit does not return a result for scheduler exhaustion"))
    else:
        valid = stall.get("invalid") and stall.get("reward") is None and not stall.get("error")
        result.append(check("invalid is not zero", "F1", "PASS" if valid else "FAIL",
                            f"Budget-limited simulation: invalid={stall.get('invalid')}, reward={stall.get('reward')}"))
    return result


def static_checks(kit: Kit, tasks: list[Any], arms: tuple[str, ...], pairs: dict[str, str]) -> dict:
    result = {"tasks": len(tasks), "leaks": [], "pairs": []}
    grouped = defaultdict(dict)
    for task in tasks:
        result["leaks"].extend(kit.leak_evidence(task, arms))
        grouped[task.instance][task.condition] = task
    for group in grouped.values():
        for trap, control in pairs.items():
            if trap in group and control in group:
                evidence, errors = kit.pair(group[control], group[trap])
                result["pairs"].append(evidence | {"errors": errors})
    return result


def load_stats(runs: list[dict], sizes: dict) -> list[dict]:
    groups = defaultdict(list)
    for run in runs:
        if run["policy"] != "careful" or run["invalid"]:
            continue
        group = groups[run["level"], run["arm"]]
        tokens = 0
        per_seat = {}
        for seat, size in sizes[run["level"], run["arm"], run["instance"]].items():
            calls = run["requests"].get(seat, 0)
            chars = size["instruction_chars"] + size["briefing_chars"] + size["file_chars"]
            if size["under_test"]:
                tokens += math.ceil(chars / 4) * max(1, calls)
            per_seat[seat] = size | {"calls": calls}
        group.append({"seats": per_seat, "input_tokens_estimate": tokens})
    rows = []
    for (level, arm), group in groups.items():
        seats = defaultdict(list)
        for item in group:
            for seat, data in item["seats"].items():
                seats[seat].append(data)
        maxima = {seat: {key: max(s[key] for s in data) for key in data[0]} for seat, data in seats.items()}
        tokens = statistics.mean(item["input_tokens_estimate"] for item in group)
        rows.append({"level": level, "arm": arm, "seat_maxima": maxima, "mean_input_tokens_estimate": tokens,
                     "cost_usd_estimate": {str(price): tokens * price / 1_000_000 for price in PRICES},
                     "trials": group})
    return rows


def build_card(args: argparse.Namespace) -> dict:
    kit = Kit(args)
    arms = tuple(args.arms) if args.arms else kit.arms()
    twins = declared_pairs(kit.module)
    pairs = trap_pairs(arms, twins, args.pair)
    twins.extend((trap, ctrl) for trap, ctrl in (item.split("=") for item in args.pair)
                 if (trap, ctrl) not in twins and (ctrl, trap) not in twins)
    known_arms = tuple(dict.fromkeys((*kit.arms(), *getattr(kit.module, "EXTRA_ARMS", ()), *arms)))
    levels = {level: kit.difficulty(level, args.seed) for level in args.levels}
    supported = tuple(policy for policy in args.policies if kit.supports(policy))
    identity = {"family": args.family, "variant": kit.variant, "title": kit.module.TITLE,
                "seam": getattr(kit.module, "SEAM", "not declared"), "arms": arms, "levels": levels,
                "kit_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "instances": args.instances, "schedule_seeds": args.schedule_seeds, "seed": args.seed,
                "policies": supported, "unavailable_policies": [p for p in args.policies if p not in supported],
                "twins": twins, "trap_controls": pairs, "known_arms": known_arms}
    card = {"identity": identity, "runs": [], "replays": [], "stall": None, "token_model": TOKEN_MODEL}
    jobs, meta, sizes, samples = [], [], {}, []
    with tempfile.TemporaryDirectory(prefix="flotilla-card-") as directory:
        root = Path(directory)
        built = {}
        for level, difficulty in levels.items():
            if difficulty is None:
                continue
            tasks = kit.build(root, level, difficulty, arms, args.instances, args.seed)
            built[level] = tasks
            for task in tasks:
                sizes[level, task.condition, task.instance] = kit.sizes(task)
                for policy in supported:
                    for schedule in range(args.schedule_seeds):
                        jobs.append((task.path, policy, schedule, False))
                        meta.append({"level": level, "arm": task.condition, "instance": task.instance,
                                     "policy": policy, "schedule_seed": schedule})
                        if policy in ("blind", "random"):
                            meta[-1]["chance_baseline"] = kit.chance(task, level, policy)
                        if task.instance == 0 and schedule == 0 and policy in ("careful", "naive"):
                            samples.append(len(jobs) - 1)
        canary = built.get("canary")
        if canary is None and (difficulty := kit.difficulty("canary", args.seed)) is not None:
            canary = kit.build(root, "canary-static", difficulty, arms, args.instances, args.seed)
        card["static"] = static_checks(kit, canary or [], known_arms, pairs)
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            for item, result in zip(meta, pool.map(simulate_job, jobs), strict=True):
                card["runs"].append(item | result)
            for index, replay in zip(samples, pool.map(simulate_job, (jobs[i] for i in samples)), strict=True):
                original = card["runs"][index]
                same = (not original["invalid"] and not replay["invalid"] and original.get("event_sha256") is not None
                        and original["event_sha256"] == replay.get("event_sha256"))
                card["replays"].append(meta[index] | {"same": same, "original": original.get("event_sha256"),
                                                       "replay": replay.get("event_sha256")})
            if samples:
                path, policy, schedule, _ = jobs[samples[0]]
                card["stall"] = pool.submit(simulate_job, (path, policy, schedule, True)).result()
    card["matrix"] = policy_matrix(card["runs"])
    card["load"] = load_stats(card["runs"], sizes)
    identity["seats"] = {f"{level}/{arm}": sorted({seat for (l, a, _), roster in sizes.items()
                                                  if l == level and a == arm for seat in roster})
                         for level in levels for arm in arms if any(l == level and a == arm for l, a, _ in sizes)}
    card["checks"] = make_checks(card, pairs, twins)
    page = ROOT / "docs" / "seams" / f"{args.family}.md"
    card["evidence_page"] = str(page.relative_to(ROOT.parent)) if page.is_file() else None
    card["passed"] = not any(row["status"] == "FAIL" for row in card["checks"])
    return card


def render(card: dict) -> str:
    i = card["identity"]
    lines = [f"# EvalCard: {i['family']} — {i['title']}", "", f"**Seam:** {i['seam']}",
             f"**Kit commit:** `{i['kit_commit']}`; variant: `{i['variant'] or 'default'}`.",
             f"**Arms:** {', '.join(i['arms'])}. Levels: " + ", ".join(
                 f"{level} ({difficulty or 'NA'})" for level, difficulty in i["levels"].items()) + ".",
             f"**Sweep:** {i['instances']} instances × {i['schedule_seeds']} schedule seeds. No model calls.",
             f"**Policies unavailable:** {', '.join(i['unavailable_policies']) or 'none'}.", "",
             "## Policy matrix (mean / min / n; invalid count in parentheses)", "",
             "| Level | Arm | Seats | " + " | ".join(i["policies"]) + " |",
             "|---|---|---|" + "---|" * len(i["policies"])]
    cells = {(r["level"], r["arm"], r["policy"]): r for r in card["matrix"]}
    for level in i["levels"]:
        for arm in i["arms"]:
            values = []
            for name in i["policies"]:
                row = cells.get((level, arm, name))
                if row is None:
                    values.append("NA")
                elif row["mean"] is None:
                    values.append(f"— / — / {row['n']} ({row['invalid_n']})")
                else:
                    values.append(f"{row['mean']:.3f} / {row['min']:.3f} / {row['n']} ({row['invalid_n']})")
            lines.append(f"| {level} | {arm} | {', '.join(i['seats'].get(f'{level}/{arm}', ())) or 'NA'} | "
                         + " | ".join(values) + " |")
    lines += ["", "## Checks", "", "| Check | Standard | Result | Evidence |", "|---|---|---|---|"]
    for row in card["checks"]:
        evidence = row["evidence"].replace("|", "\\|").replace("\n", " ")
        if len(evidence) > 400:
            evidence = evidence[:397] + "… (full evidence in JSON)"
        lines.append(f"| {row['name']} | {row['rule']} | **{row['status']}** | {evidence} |")
    lines += ["", "## Load and cost (estimates)", "", TOKEN_MODEL, "",
              "| Level / arm | Per-seat max calls / briefing chars | Mean estimated tokens | $1 / $5 / $15 per M |",
              "|---|---|---|---|"]
    for row in card["load"]:
        counts = ", ".join(f"{seat}: {data['calls']}/{data['briefing_chars']}"
                           for seat, data in row["seat_maxima"].items())
        prices = " / ".join(f"${row['cost_usd_estimate'][str(price)]:.5f}" for price in PRICES)
        lines.append(f"| {row['level']} / {row['arm']} | {counts} | {row['mean_input_tokens_estimate']:.0f} | {prices} |")
    page = card["evidence_page"]
    link = f"[{page}]({(ROOT.parent / page).as_uri()})" if page else "missing"
    lines += ["", "## Evidence and predictions", "", link, "",
              "## Open risks", ""]
    lines += [f"- {r['status']}: {r['name']}" for r in card["checks"] if r["status"] != "PASS"] or ["- None flagged."]
    lines += ["", "Scripted policy results are not estimates of frontier-model performance.", ""]
    return "\n".join(lines)


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def csv_names(value: str) -> tuple[str, ...]:
    names = tuple(item.strip() for item in value.split(","))
    if not all(names) or len(set(names)) != len(names):
        raise argparse.ArgumentTypeError("use distinct nonempty comma-separated names")
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True)
    parser.add_argument("--levels", type=csv_names, default=LEVELS)
    parser.add_argument("--instances", type=positive, default=4)
    parser.add_argument("--schedule-seeds", type=positive, default=3)
    parser.add_argument("--jobs", type=positive, default=2)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--variant", default="")
    parser.add_argument("--arms", type=csv_names)
    parser.add_argument("--policies", type=csv_names, default=POLICIES)
    parser.add_argument("--pair", action="append", default=[], metavar="TRAP=CONTROL")
    parser.add_argument("--read-first", action="store_true")
    parser.add_argument("--seats", type=positive)
    parser.add_argument("--seed", type=int, default=None, help="repeat a disposable simulation seed")
    args = parser.parse_args(argv)
    if not args.family.isidentifier():
        parser.error("--family must be a Python identifier; use --variant separately")
    if not set(args.levels) <= {*LEVELS, "easy", "hard"}:
        parser.error("--levels must contain canary, standard, heavy (or legacy easy, hard)")
    if args.seed is None:
        args.seed = secrets.randbelow(2**53 - 1_000_000) + 1_000_000
    card = build_card(args)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.family}.card.json").write_text(json.dumps(card, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (args.out / f"{args.family}.card.md").write_text(render(card))
    return 0 if card["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
