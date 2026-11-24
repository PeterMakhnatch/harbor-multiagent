"""Run a pre-registered Flotilla wave: one Harbor job per cell, under a spend cap.

    python3 flotilla/run_wave.py --dry-run flotilla/plans/example.json
    keys run -- python3 flotilla/run_wave.py flotilla/plans/<name>.json --run-dir /tmp/flotilla/<name>

A cell is (task set, instance, arm, model). Blocks are (task set, instance). Each task set's blocks are shuffled
with the order seed, then the sets alternate in proportion to their block counts. Equal counts alternate
strictly, and the seed picks which set starts. Cells inside a block stay in a seeded random order. Each job
is <plan>-<6 hex> with n_concurrent_trials 1. Docker runs at most 2 jobs, each through
/tmp/crucible-docker-slot.sh. Modal has no wrapper.

The plan file must be committed and unmodified at HEAD. The prereg commit must exist and be an ancestor
of HEAD. A rerun skips finished cells. --retry-invalid reruns a cell whose trial was not valid.
Consecutive starts are at least make_jobs.start_spacing_s apart (default 10 s, 0 disables), also on a resume.

A scripted seat (`[agents.<seat>] under_test = false` in task.toml) runs Harbor's oracle agent with no model and
costs nothing. All tasks of a task set must have the same scripted seats; the optional plan key scripted_seats
must equal them. seat_assignments rows then list models for the seats under test only.

Guards: before each start, each provider's balance must be at or above its floor; the OpenRouter balance is the
lower of the account balance (/api/v1/credits) and the key's own limit left (/api/v1/key). After
budget.max_invalid_streak (default 6) trials in a row end invalid, in accounting order, overall or counting only one
model's trials, no new cell starts; the in-flight trials finish and are accounted, and the command exits 3. A rerun
resumes as usual.

OpenRouter key: FLOTILLA_OPENROUTER_API_KEY when it is set, else OPENROUTER_API_KEY. Harbor gets it as
OPENROUTER_API_KEY and the balance check reads with it. The dry run and progress.log name the variable, never its value.
The dry run also prints a cost forecast (pricing.forecast_lines); --history-jobs DIR picks the finished jobs it reads.

--smoke[=N] (default 1) is a free check of a committed plan: the first N distinct instances of each task set, each arm
and model once per instance, on local Docker through the wrapper (at most 2 jobs), cap and reserve $0, no provider
floor, no balance or price catalog read. Every subject must be free: Harbor's oracle agent with no model (agent
"oracle", models omitted or [null]), or an OpenRouter id ending ":free" on terminus-2, with each model in at most one
seat_assignments row. Run dir and job names end in -smoke, also with --run-dir. A trial that records spend stops new
starts and the command exits 4.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import secrets
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HARBOR = Path.home() / "Developer/harbor-multiagent-flotilla/.venv/bin/harbor"
WRAPPER = Path("/tmp/crucible-docker-slot.sh")
KEYS = {"deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
FLOTILLA_KEY = "FLOTILLA_OPENROUTER_API_KEY"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
FREE_SUFFIX = ":free"
FREE_AGENTS = ("terminus-2",)
FREE_OPTIONS = frozenset({"max_turns"})
SMOKE_SUFFIX = "-smoke"
SMOKE_MAX_CONCURRENCY = 2
KEEP = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TERM", "LANG",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
})
KEEP_PREFIXES = ("LC_", "XDG_", "MODAL_", "DOCKER_")
BALANCE_BACKOFF = (2, 5, 10)
START_SPACING_S = 10.0
LEAKY = ("silent", "talk", "open", "propose", "gated", "solo", "deepseek", "qwen", "gpt", "claude", "haiku", "luna",
          "oracle")
_SPEC = importlib.util.spec_from_file_location("flotilla_pricing", ROOT / "pricing.py")
PRICING = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(PRICING)
_JOBS = importlib.util.spec_from_file_location("flotilla_make_jobs", ROOT / "make_jobs.py")
JOBS = importlib.util.module_from_spec(_JOBS)  # type: ignore[arg-type]
assert _JOBS and _JOBS.loader
_JOBS.loader.exec_module(JOBS)
PLAN_KEYS = frozenset({
    "name", "prereg", "task_sets", "models", "agent", "make_jobs", "budget", "order_seed", "seat_assignments",
    "scripted_seats",
})
PREREG_KEYS = frozenset({"commit", "doc", "section"})
JOB_KEYS = frozenset({"max_turns", "env", "concurrency", "start_spacing_s"})
BUDGET_KEYS = frozenset({"cap_usd", "reserve_per_trial_usd", "provider_floors", "max_invalid_streak"})
MAX_INVALID_STREAK = 6
EXIT_INVALID_STREAK = 3
EXIT_SMOKE_CHARGED = 4
STREAK_KINDS_SHOWN = 3
SET_KEYS = frozenset({"tasks_dir", "manifest", "instances", "arms", "conditions"})


def provider_of(model: str) -> str:
    if model.startswith("openrouter/"):
        return "openrouter"
    if model.startswith("deepseek/"):
        return "deepseek"
    raise SystemExit(f"refused: {model} is not a deepseek/ or openrouter/ model")


def cell_key(task_set: int, instance: int, arm: str, model) -> str:
    return json.dumps([task_set, instance, arm, model], separators=(",", ":"))


def refuse_unknown(data: dict, known: frozenset[str], where: str) -> None:
    extra = sorted(set(data) - known)
    if extra:
        raise SystemExit(f"refused: unknown {where} field {extra[0]}")


def used_models(plan: dict) -> list[str]:
    rows = plan.get("seat_assignments")
    if not rows:
        return list(plan["models"])
    found = []
    for row in rows:
        for model in row:
            if model not in found:
                found.append(model)
    return found


def free_model(model) -> bool:
    """An OpenRouter id that ends in :free (the free variant OpenRouter bills at $0)."""
    return (isinstance(model, str) and model.startswith("openrouter/") and model.endswith(FREE_SUFFIX)
            and len(model) > len("openrouter/") + len(FREE_SUFFIX) and not any(ch.isspace() for ch in model))


def assert_free(plan: dict) -> None:
    """A smoke runs only subjects that cost nothing, each model at most once per instance and arm."""
    if plan["agent"] == "oracle":
        if plan.get("seat_assignments") or plan.get("models") != [None]:
            raise SystemExit("refused: a smoke on the oracle agent takes no model: omit models (or use [null]) and "
                             "seat_assignments")
        return
    if plan["agent"] not in FREE_AGENTS:
        raise SystemExit(f"refused: a smoke runs a :free model only on {', '.join(FREE_AGENTS)}, not {plan['agent']}")
    for model in used_models(plan):
        if not free_model(model):
            raise SystemExit(f"refused: smoke subject {model} is not free (Harbor's oracle agent with no model, or an "
                             "OpenRouter id ending :free)")
    rows = plan.get("seat_assignments")
    if not rows:
        if len(set(plan["models"])) != len(plan["models"]):
            raise SystemExit("refused: a smoke plan lists a model twice")
        return
    owner: dict[str, int] = {}
    for index, row in enumerate(rows):
        for model in dict.fromkeys(row):
            if model in owner:
                raise SystemExit(f"refused: {model} is in seat_assignments[{owner[model]}] and [{index}]; a smoke "
                                 "runs each model at most once per instance and arm")
            owner[model] = index


def load_plan(path: Path, *, smoke: bool = False) -> dict:
    plan = json.loads(path.read_text())
    refuse_unknown(plan, PLAN_KEYS, "plan")
    required = ["name", "prereg", "task_sets", "agent", "make_jobs", "budget", "order_seed"]
    model_less = smoke and plan.get("agent") == "oracle" and "seat_assignments" not in plan
    if "seat_assignments" not in plan and not (model_less and "models" not in plan):
        required.append("models")
    for field in required:
        if field not in plan:
            raise SystemExit(f"refused: plan missing {field}")
    if model_less and "models" not in plan:
        plan["models"] = [None]
    refuse_unknown(plan["prereg"], PREREG_KEYS, "prereg")
    refuse_unknown(plan["make_jobs"], JOB_KEYS, "make_jobs")
    refuse_unknown(plan["budget"], BUDGET_KEYS, "budget")
    for field in ("commit", "doc", "section"):
        if field not in plan["prereg"]:
            raise SystemExit(f"refused: prereg missing {field}")
    for field in ("max_turns", "env", "concurrency"):
        if field not in plan["make_jobs"]:
            raise SystemExit(f"refused: make_jobs missing {field}")
    for field in ("cap_usd", "reserve_per_trial_usd", "provider_floors"):
        if field not in plan["budget"]:
            raise SystemExit(f"refused: budget missing {field}")
    options = plan["make_jobs"]
    if options["env"] not in ("docker", "modal"):
        raise SystemExit(f"refused: env {options['env']}")
    if not isinstance(options["concurrency"], int) or options["concurrency"] < 1:
        raise SystemExit("refused: concurrency must be an integer >= 1")
    if options["env"] == "docker" and options["concurrency"] > 2:
        raise SystemExit("refused: docker concurrency must be <= 2")
    spacing = options.get("start_spacing_s", START_SPACING_S)
    if isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or not 0 <= spacing <= 600:
        raise SystemExit("refused: start_spacing_s must be a number from 0 to 600")
    if not plan["task_sets"] or not used_models(plan):
        raise SystemExit("refused: plan has no models or no task sets")
    declared = plan.get("scripted_seats")
    if declared is not None and (not isinstance(declared, list) or len(set(declared)) != len(declared)
                                 or any(not isinstance(seat, str) or not seat for seat in declared)):
        raise SystemExit("refused: scripted_seats must be a list of distinct seat names")
    if any(word in plan["name"] for word in LEAKY):
        raise SystemExit(f"refused: plan name {plan['name']} contains a condition or model word")
    rows = plan.get("seat_assignments")
    if rows is not None:
        if not isinstance(rows, list) or not rows:
            raise SystemExit("refused: seat_assignments is empty")
        width = len(rows[0])
        for index, row in enumerate(rows):
            if not isinstance(row, list) or not row or len(row) != width:
                raise SystemExit(f"refused: seat_assignments[{index}] is not a model list")
            if any(not PRICING.known_model(model) for model in row):
                raise SystemExit(f"refused: seat_assignments[{index}] has a model pricing.py cannot price")
    streak = plan["budget"].get("max_invalid_streak", MAX_INVALID_STREAK)
    if isinstance(streak, bool) or not isinstance(streak, int) or streak < 1:
        raise SystemExit("refused: max_invalid_streak must be an integer >= 1")
    floors = plan["budget"]["provider_floors"]
    if smoke:
        assert_free(plan)
    for model in [] if smoke else used_models(plan):
        if not PRICING.known_model(model):
            raise SystemExit(f"refused: {model} is not a model pricing.py can price")
        provider = provider_of(model)
        if provider not in floors:
            raise SystemExit(f"refused: no floor for {provider}")
    for index, task_set in enumerate(plan["task_sets"]):
        refuse_unknown(task_set, SET_KEYS, f"task set {index}")
        for field in ("tasks_dir", "manifest", "instances"):
            if field not in task_set:
                raise SystemExit(f"refused: task set {index} missing {field}")
        if not (task_set.get("arms") or task_set.get("conditions")):
            raise SystemExit(f"refused: task set {index} has no arms")
    return plan


def spacing_of(plan: dict) -> float:
    return float(plan["make_jobs"].get("start_spacing_s", START_SPACING_S))


def streak_of(plan: dict) -> int:
    return int(plan["budget"].get("max_invalid_streak", MAX_INVALID_STREAK))


def repo_of(path: Path) -> Path:
    for start in (path.parent, Path.cwd()):
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True)
        if result.returncode == 0:
            return Path(result.stdout.strip())
    raise SystemExit("refused: plan is not in a git checkout")


def assert_committed(plan_path: Path, repo: Path) -> None:
    rel = plan_path.resolve().relative_to(repo.resolve())
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(rel)], cwd=repo, capture_output=True, text=True)
    if tracked.returncode != 0:
        raise SystemExit(f"refused: {rel} is not committed")
    diff = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(rel)], cwd=repo)
    if diff.returncode != 0:
        raise SystemExit(f"refused: {rel} differs from HEAD")


def assert_prereg(plan: dict, repo: Path) -> None:
    commit = plan["prereg"]["commit"]
    exists = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo, capture_output=True)
    if exists.returncode != 0:
        raise SystemExit(f"refused: prereg commit {commit} does not exist")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=repo)
    if ancestor.returncode != 0:
        raise SystemExit(f"refused: prereg commit {commit} is not an ancestor of HEAD")
    shown = subprocess.run(
        ["git", "show", f"{commit}:{plan['prereg']['doc']}"], cwd=repo, capture_output=True, text=True)
    if shown.returncode != 0 or plan["prereg"]["section"] not in shown.stdout:
        raise SystemExit(f"refused: prereg section {plan['prereg']['section']!r} is not in {commit}:{plan['prereg']['doc']}")


def arms_of(task_set: dict) -> list[str]:
    return list(task_set.get("arms") or task_set["conditions"])


def choices_of(plan: dict) -> list:
    if plan.get("seat_assignments"):
        return list(plan["seat_assignments"])
    return list(plan["models"])


def seat_models(cell: dict) -> dict[str, str]:
    """Model per seat under test of an assignment cell. Seats are p1..pN; scripted seats take no model."""
    scripted = set(cell.get("scripted") or ())
    total = len(cell["assignment"]) + len(scripted)
    seats = [f"p{index}" for index in range(1, total + 1) if f"p{index}" not in scripted]
    return dict(zip(seats, cell["assignment"], strict=True))


def label_of(cell: dict) -> str:
    if cell.get("assignment"):
        return "models " + ",".join(f"{seat}={model}" for seat, model in seat_models(cell).items())
    return cell["model"] or "oracle (no model)"


def blocks_of(plan: dict, paths: dict[tuple[int, int, str], str] | None,
              scripted: list[list[str]] | None = None) -> list[list[list[dict]]]:
    """Blocks grouped by task set. A block is one (task set, instance). paths is None for a dry run."""
    grouped = []
    for index, task_set in enumerate(plan["task_sets"]):
        blocks = []
        for instance in task_set["instances"]:
            block = []
            for arm in arms_of(task_set):
                for choice in choices_of(plan):
                    task = "" if paths is None else paths[(index, instance, arm)]
                    assignment = choice if isinstance(choice, list) else None
                    model = choice if assignment is None else choice[0]
                    cell = {"task_set": index, "instance": instance, "arm": arm, "model": model,
                            "task": task, "key": cell_key(index, instance, arm, choice)}
                    if assignment is not None:
                        cell["assignment"] = list(assignment)
                    if scripted and scripted[index]:
                        cell["scripted"] = list(scripted[index])
                    block.append(cell)
            blocks.append(block)
        grouped.append(blocks)
    return grouped


def interleave(grouped: list[list[list[dict]]], rng: random.Random) -> list[list[dict]]:
    """Alternate sets in proportion to their block counts. Equal counts alternate strictly."""
    counts = [len(blocks) for blocks in grouped]
    total = sum(counts)
    rank = list(range(len(grouped)))
    rng.shuffle(rank)
    cursors = [0] * len(grouped)
    chosen = []
    for step in range(1, total + 1):
        best, best_score = None, None
        for index in rank:
            if cursors[index] >= counts[index]:
                continue
            score = counts[index] * step - cursors[index] * total
            if best is None or score > best_score:
                best, best_score = index, score
        assert best is not None
        chosen.append(grouped[best][cursors[best]])
        cursors[best] += 1
    return chosen


def order_of(plan: dict, paths: dict[tuple[int, int, str], str] | None = None,
             scripted: list[list[str]] | None = None) -> list[dict]:
    """Shuffle each set's blocks, shuffle cells inside a block, then alternate the sets."""
    rng = random.Random(plan["order_seed"])
    grouped = blocks_of(plan, paths, scripted)
    for blocks in grouped:
        rng.shuffle(blocks)
        for block in blocks:
            rng.shuffle(block)
    ordered = grouped[0] if len(grouped) == 1 else interleave(grouped, rng)
    return [cell for block in ordered for cell in block]


def resolve_paths(plan: dict, repo: Path) -> dict[tuple[int, int, str], str]:
    found = {}
    for index, task_set in enumerate(plan["task_sets"]):
        manifest_path = repo / task_set["manifest"]
        if not manifest_path.is_file():
            raise SystemExit(f"refused: manifest {task_set['manifest']} is missing")
        tasks_dir = repo / task_set["tasks_dir"]
        by_cell = {}
        for entry in json.loads(manifest_path.read_text())["tasks"].values():
            by_cell[(entry["instance"], entry["condition"])] = str((tasks_dir / entry["path"]).resolve())
        for instance in task_set["instances"]:
            for arm in arms_of(task_set):
                task = by_cell.get((instance, arm))
                if task is None:
                    raise SystemExit(f"refused: no task for set {index} instance {instance} {arm}")
                found[(index, instance, arm)] = task
    return found


def roster_of(task: str | Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(all seats, scripted seats) from task.toml, both sorted. A scripted seat has under_test = false."""
    config = tomllib.loads((Path(task) / "task.toml").read_text())
    agents = config.get("agents", {})
    seats = tuple(sorted(agents))
    if not seats:
        raise SystemExit(f"refused: {task} has no seats")
    scripted = tuple(seat for seat in seats if agents[seat].get("under_test", True) is False)
    if len(scripted) == len(seats):
        raise SystemExit(f"refused: {task} has no seat under test")
    return seats, scripted


def scripted_of(plan: dict, repo: Path, *, need_tasks: bool) -> list[list[str] | None]:
    """Scripted seats per task set, read from every planned task's task.toml. None for a set whose manifest is
    missing, which only a dry run allows."""
    declared = plan.get("scripted_seats")
    found: list[list[str] | None] = []
    for index, task_set in enumerate(plan["task_sets"]):
        manifest_path = repo / task_set["manifest"]
        if not manifest_path.is_file():
            if need_tasks:
                raise SystemExit(f"refused: manifest {task_set['manifest']} is missing")
            found.append(None)
            continue
        wanted = {(instance, arm) for instance in task_set["instances"] for arm in arms_of(task_set)}
        seen: tuple[str, ...] | None = None
        for entry in json.loads(manifest_path.read_text())["tasks"].values():
            if (entry.get("instance"), entry.get("condition")) not in wanted:
                continue
            _seats, scripted = roster_of(repo / task_set["tasks_dir"] / entry["path"])
            if seen is not None and scripted != seen:
                raise SystemExit(f"refused: task set {index} mixes scripted seats {list(seen)} and {list(scripted)}")
            seen = scripted
        if declared is not None and seen is not None and list(seen) != sorted(declared):
            raise SystemExit(f"refused: plan scripted_seats {sorted(declared)} but task set {index} tasks have "
                             f"{list(seen)} (under_test = false in task.toml)")
        found.append(list(seen or ()))
    return found


def assert_assignment_seats(plan: dict, repo: Path, scripted: list[list[str] | None]) -> None:
    rows = plan.get("seat_assignments")
    if not rows:
        return
    for index, task_set in enumerate(plan["task_sets"]):
        manifest = json.loads((repo / task_set["manifest"]).read_text())
        wanted = {(instance, arm) for instance in task_set["instances"] for arm in arms_of(task_set)}
        counts = set()
        for entry in manifest["tasks"].values():
            if (entry.get("instance"), entry.get("condition")) not in wanted:
                continue
            seats = entry.get("seats")
            if not seats:
                raise SystemExit(f"refused: task set {index} manifest entry has no seats")
            counts.add(len(seats))
        if len(counts) != 1:
            raise SystemExit(f"refused: task set {index} has no uniform seat count")
        count = counts.pop() - len(scripted[index] or ())
        for row in rows:
            if len(row) != count:
                raise SystemExit(f"refused: assignment has {len(row)} models, task set {index} has {count} seats "
                                 "under test")


def job_config(job: str, jobs_dir: Path, task: str, agent: str, model: str | None, max_turns: int, env: str,
               assignment: list[str] | None = None) -> dict:
    seats, scripted = roster_of(task)
    tested = [seat for seat in seats if seat not in scripted]
    specs = [f"{seat}=oracle" for seat in scripted]
    if assignment:
        if len(assignment) != len(tested):
            raise SystemExit(f"refused: assignment has {len(assignment)} models, {task} has {len(tested)} seats "
                             "under test")
        specs += [f"{seat}={agent}:{item}" for seat, item in zip(tested, assignment)]
        model = assignment[0]
    environment = {"type": env, "force_build": False, "delete": True}
    if env == "modal":
        environment["kwargs"] = {"modal_vm_runtime": True, "app_name": "flotilla"}
    return {
        "job_name": job,
        "jobs_dir": str(jobs_dir),
        "n_attempts": 1,
        "timeout_multiplier": 1.0,
        "n_concurrent_trials": 1,
        "quiet": True,
        "environment": environment,
        "participants": JOBS.participants(seats, agent, model, max_turns, specs),
        "tasks": [{"path": task}],
    }


def harbor_command(env: str, harbor: Path, config: Path, wrapper: Path) -> list[str]:
    command = [str(harbor), "run", "-c", str(config), "-y"]
    if env == "modal":
        return command
    return [str(wrapper), *command]


def key_name(env: dict[str, str], provider: str) -> str:
    """The variable that holds the provider's key: for OpenRouter the Flotilla-only key when it is set."""
    if provider == "openrouter" and env.get(FLOTILLA_KEY):
        return FLOTILLA_KEY
    return KEYS[provider]


def harbor_env(base: dict[str, str], models: list, *, keep_modal: bool = True) -> dict[str, str]:
    providers = {provider_of(model) for model in models if model is not None}
    kept = {}
    for key, value in base.items():
        if key in KEYS.values() or key == FLOTILLA_KEY or (key.startswith("MODAL_") and not keep_modal):
            continue
        if key in KEEP or key.startswith(KEEP_PREFIXES):
            kept[key] = value
    for provider in sorted(providers):
        value = base.get(key_name(base, provider))
        if value:
            kept[KEYS[provider]] = value
    return kept


def key_line(env: dict[str, str], models: list) -> str:
    """Which variable holds the OpenRouter key, by name only."""
    if not any(model is not None and provider_of(model) == "openrouter" for model in models):
        return "OpenRouter key: not used"
    name = key_name(env, "openrouter")
    if not env.get(name):
        return f"OpenRouter key: not set ({FLOTILLA_KEY} or {KEYS['openrouter']})"
    if name == FLOTILLA_KEY:
        return f"OpenRouter key: {FLOTILLA_KEY} (Harbor gets it as {KEYS['openrouter']})"
    return f"OpenRouter key: {name}"


def smoke_plan(plan: dict, count: int) -> dict:
    """The plan a smoke runs: the first count distinct instances of each task set, local Docker, a $0 cap and reserve,
    no provider floor. The smoke key marks it for pricing.forecast_lines (a plan file cannot set it)."""
    sets = []
    for index, task_set in enumerate(plan["task_sets"]):
        arms = arms_of(task_set)
        if len(set(arms)) != len(arms):
            raise SystemExit(f"refused: task set {index} lists an arm twice")
        instances = list(dict.fromkeys(task_set["instances"]))[:count]
        sets.append({**task_set, "instances": instances})
    options = {**plan["make_jobs"], "env": "docker",
               "concurrency": min(plan["make_jobs"]["concurrency"], SMOKE_MAX_CONCURRENCY)}
    budget = {**plan["budget"], "cap_usd": 0.0, "reserve_per_trial_usd": 0.0, "provider_floors": {}}
    return {**plan, "name": plan["name"] + SMOKE_SUFFIX, "task_sets": sets, "make_jobs": options, "budget": budget,
            "smoke": count}


def smoke_line(plan: dict) -> str:
    return (f"smoke: first {plan['smoke']} instances of each task set, local docker, concurrency "
            f"{plan['make_jobs']['concurrency']}, cap $0.00, no balance or catalog read; a trial that records spend "
            "stops new starts")


def assert_free_config(config: dict) -> None:
    """The Harbor config of a smoke job: docker, and every seat on the oracle with no model or on a free agent with a
    :free model and no other option (no base URL, key or fallback model)."""
    if config["environment"]["type"] != "docker":
        raise SystemExit("refused: a smoke job must run on docker")
    for row in config["participants"]:
        name, model = row.get("name"), row.get("model_name")
        free = (name == "oracle" and model is None) or (name in FREE_AGENTS and free_model(model))
        extra = set(row) - {"seat", "name", "model_name", "kwargs"} or set(row.get("kwargs") or {}) - FREE_OPTIONS
        if not free or extra:
            raise SystemExit(f"refused: smoke seat {row.get('seat')} is not free ({name} {model})")


def refuse_catalog() -> dict:
    raise SystemExit("refused: a smoke reads no price catalog")


def over_cap(spent: float, in_flight: int, reserve: float, cap: float) -> bool:
    return spent + reserve * (in_flight + 1) > cap + 1e-9


def stamp() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S ET ")


def log(run_dir: Path, line: str) -> None:
    with (run_dir / "progress.log").open("a") as handle:
        handle.write(stamp() + line + "\n")


def save_state(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(temporary, path)


def scripted_line(plan: dict, scripted: list[list[str] | None]) -> str:
    parts = []
    for index, seats in enumerate(scripted):
        if seats is not None:
            parts.append(f"set {index} {','.join(seats) or 'none'}")
        elif plan.get("scripted_seats") is not None:
            parts.append(f"set {index} {','.join(sorted(plan['scripted_seats'])) or 'none'} (declared; no manifest)")
        else:
            parts.append(f"set {index} unknown (no manifest)")
    return "scripted seats (oracle agent, no model, cost 0): " + "; ".join(parts)


def guard_line(plan: dict) -> str:
    floors = plan["budget"]["provider_floors"]
    sources = {"openrouter": "lower of account balance and key limit left", "deepseek": "account balance"}
    shown = ", ".join(f"{provider} ${float(amount):.2f} ({sources.get(provider, 'balance')})"
                      for provider, amount in sorted(floors.items())) or "none"
    return (f"guards: stop after {streak_of(plan)} invalid trials in a row, overall or for one model; "
            f"provider floors {shown}")


def print_dry_run(plan: dict, order: list[dict], scripted: list[list[str] | None], keys: str,
                  forecast: list[str]) -> None:
    reserve = float(plan["budget"]["reserve_per_trial_usd"])
    cap = float(plan["budget"]["cap_usd"])
    print(f"{plan['name']}: {len(order)} cells, reserve ${reserve:.2f} per trial, cap ${cap:.2f}, "
          f"reserve if every cell starts ${reserve * len(order):.2f}, start spacing {spacing_of(plan):.1f} s")
    if plan.get("smoke"):
        print(smoke_line(plan))
    print(scripted_line(plan, scripted))
    print(guard_line(plan))
    print(keys)
    for line in forecast:
        print(line)
    for index, cell in enumerate(order, 1):
        print(f"{index}. set {cell['task_set']} instance {cell['instance']} {cell['arm']} {label_of(cell)}")


class Balance(NamedTuple):
    """A provider balance and what set it (for OpenRouter: the account or the key's own limit)."""
    amount: float
    detail: str = ""


class HttpSources:
    """OpenRouter catalog and provider balances. Tests pass a fake with the same three methods."""

    def __init__(self, env: dict[str, str]):
        self.env = env

    def catalog(self) -> dict:
        """The public OpenRouter model list, with no key. A failed read raises RuntimeError (the forecast then
        falls back to its cache)."""
        try:
            with urllib.request.urlopen(urllib.request.Request(CATALOG_URL), timeout=20) as response:
                return json.load(response)
        except Exception as exc:
            raise RuntimeError(f"could not read {CATALOG_URL} ({type(exc).__name__})") from exc

    def prices(self, models: list[str]) -> dict:
        payload = _fetch_json(CATALOG_URL, {})
        catalog = PRICING.parse_openrouter_models(payload)
        frozen = {}
        for model in models:
            if not model.startswith("openrouter/"):
                continue
            row = catalog.get(model.removeprefix("openrouter/"))
            if row is None:
                raise SystemExit(f"refused: no OpenRouter price for {model}")
            frozen[model] = row
        return frozen

    def balance(self, provider: str) -> Balance:
        key = key_name(self.env, provider)
        token = self.env.get(key)
        if not token:
            raise SystemExit(f"refused: {key} is not set")
        header = {"Authorization": "Bearer " + token}
        if provider == "deepseek":
            payload = _fetch_json("https://api.deepseek.com/user/balance", header)
            return Balance(PRICING.parse_deepseek_balance(payload), "account balance")
        account = PRICING.parse_openrouter_credits(_fetch_json("https://openrouter.ai/api/v1/credits", header))
        key_left = PRICING.parse_openrouter_key(_fetch_json("https://openrouter.ai/api/v1/key", header))
        amount, detail = PRICING.openrouter_balance(account, key_left)
        return Balance(amount, detail)


def _fetch_json(url: str, headers: dict[str, str]) -> dict:
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except Exception as exc:
        raise SystemExit(f"refused: could not read {url} ({type(exc).__name__})") from exc


def read_trial(jobs_dir: Path, job: str) -> tuple[Path | None, dict | None]:
    job_dir = jobs_dir / job
    if not job_dir.is_dir():
        return None, None
    trials = [path for path in job_dir.iterdir() if (path / "result.json").is_file()]
    if not trials:
        return None, None
    trial = trials[0]
    return trial, json.loads((trial / "result.json").read_text())


def trial_config(trial: Path, result: dict) -> dict:
    config_path = trial / "config.json"
    config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    return result.get("config") or config


def exception_kind(info) -> str:
    if isinstance(info, dict):
        return str(info.get("exception_type") or "exception")
    return str(info)[:80]


def trial_problem(trial: Path | None, result: dict | None, cell: dict) -> str | None:
    """Why a trial is not valid, or None when it is."""
    if trial is None or result is None:
        return "no trial result"
    if result.get("exception_info"):
        return exception_kind(result["exception_info"])
    for seat in result.get("seat_results") or []:
        if seat.get("exception_info"):
            return f"{seat.get('seat')} {exception_kind(seat['exception_info'])}"
    score_path, state_path = trial / "verifier" / "score.json", trial / "verifier" / "world-state.json"
    if not score_path.is_file() or not state_path.is_file():
        return "no verifier output"
    verdict = json.loads(score_path.read_text())
    if verdict.get("error") is not None:
        return f"score error {verdict['error']}"
    if (verdict.get("instance"), verdict.get("condition")) != (cell["instance"], cell["arm"]):
        return "score for another cell"
    participants = trial_config(trial, result).get("participants") or []
    if not participants:
        return "no participants"
    scripted = set(cell.get("scripted") or ())
    ran = {row.get("seat"): (row.get("agent_info") or {}).get("name") for row in result.get("seat_results") or []}
    oracles = {row.get("seat") for row in participants if row.get("seat") in scripted
               and row.get("name") == "oracle" and ran.get(row.get("seat"), "oracle") == "oracle"}
    if oracles != scripted:
        return "scripted seat not on oracle"
    tested = [row for row in participants if row.get("seat") not in scripted]
    if cell.get("assignment"):
        expected = seat_models(cell)
        actual = {row.get("seat"): row.get("model_name") for row in tested}
        ok = len(tested) == len(expected) and actual == expected
    elif cell["model"] is None:
        ok = bool(tested) and all(row.get("name") == "oracle" and row.get("model_name") is None
                                  and ran.get(row.get("seat"), "oracle") == "oracle" for row in tested)
    else:
        ok = bool(tested) and all(row.get("model_name") == cell["model"] for row in tested)
    return None if ok else "seat model mismatch"


def trial_valid(trial: Path | None, result: dict | None, cell: dict) -> bool:
    return trial_problem(trial, result, cell) is None


def attempts_of(row: dict | None) -> list[dict]:
    if not row:
        return []
    if "attempts" in row:
        return list(row["attempts"])
    return [row]


def cell_valid(row: dict | None) -> bool:
    return any(item["valid"] for item in attempts_of(row))


def when_of(result: dict) -> datetime:
    started = result.get("started_at")
    return datetime.fromisoformat(started).astimezone(UTC) if started else datetime.now(UTC)


def tested_result(cell: dict, result: dict) -> dict:
    """The result without scripted seats: an oracle seat has no model, no tokens and no cost."""
    scripted = set(cell.get("scripted") or ())
    if not scripted:
        return result
    return {**result, "seat_results": [row for row in result.get("seat_results") or []
                                       if row.get("seat") not in scripted]}


def assigned_charge(cell: dict, result: dict, prices: dict, reserve: float) -> dict:
    mapping = seat_models(cell)
    try:
        estimate = PRICING.estimate_seats(result, mapping, when_of(result), prices)
    except KeyError:
        return {"cost": reserve, "estimate": None, "source": "reserve", "flag": "no-price"}
    recorded = PRICING.recorded_cost(result)
    tokens = PRICING.tokens_of(result)
    count = tokens["hit"] + tokens["miss"] + tokens["out"]
    if recorded is None or (recorded == 0 and count > 0):
        return {"cost": estimate, "estimate": estimate, "source": "token-estimate", "flag": "missing-recorded"}
    return {"cost": recorded, "estimate": estimate, "source": "recorded", "flag": None}


def charge(cell: dict, result: dict | None, prices: dict, reserve: float) -> dict:
    if result is None:
        return {"cost": reserve, "estimate": None, "source": "reserve", "flag": "no-result"}
    result = tested_result(cell, result)
    if cell.get("assignment"):
        return assigned_charge(cell, result, prices, reserve)
    if PRICING.is_deepseek_direct(cell["model"]):
        amount = PRICING.deepseek_cost(PRICING.tokens_of(result), when_of(result))
        return {"cost": amount, "estimate": amount, "source": "deepseek", "flag": None}
    try:
        amount, estimate, source = PRICING.openrouter_spend(result, cell["model"], prices)
    except KeyError:
        return {"cost": reserve, "estimate": None, "source": "reserve", "flag": "no-price"}
    flag = "missing-recorded" if source == "token-estimate" else None
    return {"cost": amount, "estimate": estimate, "source": source, "flag": flag}


def smoke_charge(cell: dict, result: dict | None) -> dict:
    seats = (result or {}).get("seat_results") or []
    amount = sum(max(0.0, float((row.get("agent_result") or {}).get("cost_usd") or 0.0)) for row in seats)
    source = "oracle" if cell["model"] is None else ("recorded" if amount else "free")
    return {"cost": amount, "estimate": 0.0, "source": source, "flag": "charged" if amount else None}


def run_process(command: list[str], log_path: Path, env: dict[str, str], cwd: Path) -> int:
    with log_path.open("w") as handle:
        return subprocess.run(command, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT).returncode


def new_job(name: str, jobs_dir: Path, namer) -> str:
    for _ in range(8):
        job = f"{name}-{namer(3)}"
        if not (jobs_dir / job).exists():
            return job
    raise SystemExit("refused: could not name a job")


def pending_of(order: list[dict], finished: dict, retry_invalid: bool) -> list[dict]:
    pending = []
    for cell in order:
        row = finished.get(cell["key"])
        attempts = attempts_of(row)
        if not attempts or (retry_invalid and not cell_valid(row) and len(attempts) < 2):
            pending.append(cell)
    return pending


def log_order(run_dir: Path, order: list[dict]) -> None:
    log(run_dir, f"order {len(order)} cells")
    for index, cell in enumerate(order, 1):
        log(run_dir, f"order {index} set {cell['task_set']} instance {cell['instance']} {cell['arm']} {label_of(cell)}")


def check_resume(state: dict, plan: dict, order: list[dict], digest: str, smoke: int | None = None) -> None:
    held = state.get("mode", "wave")
    if held != ("wave" if smoke is None else "smoke") or state.get("smoke") != smoke:
        count = f" of {state.get('smoke')}" if held == "smoke" else ""
        raise SystemExit(f"refused: this run dir holds a {held} run{count}; use another run dir")
    if state.get("plan") != plan["name"]:
        raise SystemExit(f"refused: this run dir belongs to plan {state.get('plan')}; use another run dir")
    if state.get("plan_sha256") != digest:
        raise SystemExit("refused: plan file changed since this run dir was started")
    stored = state.get("order") or []
    if [row["key"] for row in stored] != [cell["key"] for cell in order]:
        raise SystemExit("refused: order does not match the recorded order")
    by_key = {row["key"]: row.get("task") for row in stored}
    for cell in order:
        if by_key.get(cell["key"]) not in (None, "", cell["task"]):
            raise SystemExit("refused: a cell's task path changed; use a new run dir")


def initial_state(plan: dict, order: list[dict], digest: str, smoke: int | None = None) -> dict:
    return {"plan": plan["name"], "plan_sha256": digest, "mode": "wave" if smoke is None else "smoke",
            "smoke": smoke, "order": order, "prices": {}, "spent_usd": 0.0, "finished": {}}


def freeze_prices(sources, models: list[str], state: dict) -> None:
    if any(model.startswith("openrouter/") for model in models) and not state["prices"]:
        state["prices"] = sources.prices(models)


def submit(pool, futures, events, cell, job, command, log_path, env, repo, spent, reserve, cap, concurrency):
    future = pool.submit(run_process, command, log_path, env, repo)
    futures[future] = (cell, job)
    in_flight = len(futures)
    projected = spent + reserve * in_flight
    assert in_flight <= concurrency
    assert projected <= cap + 1e-9
    events.append({"kind": "start", "key": cell["key"], "job": job, "in_flight": in_flight,
                   "spent": spent, "projected": projected, "command": command})


def account(future, futures, state, events, jobs_dir, reserve, run_dir, lock: threading.Lock) -> None:
    cell, job = futures.pop(future)
    exit_code = future.result()
    trial, result = read_trial(jobs_dir, job)
    bill = smoke_charge(cell, result) if state.get("mode") == "smoke" else charge(cell, result, state["prices"], reserve)
    problem = trial_problem(trial, result, cell)
    valid = problem is None
    attempt = {
        "job": job, "valid": valid, "cost_usd": bill["cost"], "estimate_usd": bill["estimate"],
        "cost_source": bill["source"], "flag": bill["flag"], "exit_code": exit_code,
        "started_at": (result or {}).get("started_at"), "finished_at": datetime.now(UTC).isoformat(),
        "problem": problem,
    }
    with lock:
        state["spent_usd"] += bill["cost"]
        attempts = attempts_of(state["finished"].get(cell["key"]))
        attempts.append(attempt)
        state["finished"][cell["key"]] = {
            "attempts": attempts, "valid": any(item["valid"] for item in attempts), "job": job,
            "models": label_of(cell),
        }
        save_state(run_dir / "state.json", state)
        spent = state["spent_usd"]
    estimate = bill["estimate"]
    estimate_text = "none" if estimate is None else f"${estimate:.4f}"
    flag = f" flagged {bill['flag']}" if bill["flag"] else ""
    log(run_dir, f"end {job} set {cell['task_set']} instance {cell['instance']} {cell['arm']} {label_of(cell)} "
                 f"exit {exit_code} valid {valid} cost ${bill['cost']:.4f} estimate {estimate_text} "
                 f"source {bill['source']}{flag} spent ${spent:.4f}")
    events.append({"kind": "end", "key": cell["key"], "job": job, "valid": valid, "cost": bill["cost"],
                   "estimate": estimate, "source": bill["source"], "flag": bill["flag"], "exit_code": exit_code,
                   "spent": spent, "problem": problem, "model": label_of(cell)})


def drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock) -> None:
    for future in [item for item in futures if item.done()]:
        account(future, futures, state, events, jobs_dir, reserve, run_dir, lock)


def wait_one(futures):
    done, _pending = wait(set(futures), return_when=FIRST_COMPLETED)
    return done.pop()


def read_balance(sources, provider: str, sleep) -> tuple[Balance | None, BaseException | None]:
    last = None
    for attempt in range(1 + len(BALANCE_BACKOFF)):
        try:
            value = sources.balance(provider)
            return (value if isinstance(value, Balance) else Balance(float(value))), None
        except (Exception, SystemExit) as exc:
            last = exc
            if attempt == len(BALANCE_BACKOFF):
                return None, exc
            sleep(BALANCE_BACKOFF[attempt])
    return None, last


def invalid_streak(events: list[dict], model: str | None = None) -> list[dict]:
    """The end events of the invalid trials that ended last, in a row (accounting order); with a model, counting
    only that model's trials."""
    streak = []
    for event in reversed(events):
        if event["kind"] != "end" or (model is not None and event.get("model") != model):
            continue
        if event["valid"]:
            break
        streak.append(event)
    return streak[::-1]


def tripped_streak(events: list[dict], limit: int) -> tuple[list[dict], str | None]:
    """The first streak at or above the limit: the whole wave's, else one model's (None names no model)."""
    streak = invalid_streak(events)
    if len(streak) >= limit:
        return streak, None
    for model in dict.fromkeys(event.get("model") for event in events if event["kind"] == "end"):
        streak = invalid_streak(events, model)
        if len(streak) >= limit:
            return streak, model
    return [], None


def streak_stop(events: list[dict], limit: int, run_dir: Path, note: str = "draining in-flight jobs") -> bool:
    streak, model = tripped_streak(events, limit)
    if not streak:
        return False
    kinds = "; ".join(event.get("problem") or "unknown" for event in streak[-STREAK_KINDS_SHOWN:])
    whose = f" for {model}" if model else ""
    log(run_dir, f"stop: {len(streak)} invalid trials in a row{whose} (last: {kinds}); {note}")
    return True


def wait_under_cap(futures, state, events, jobs_dir, reserve, run_dir, lock, cap) -> str | None:
    """Wait until the next start fits. 'cap' only when nothing is in flight and it still does not."""
    while over_cap(state["spent_usd"], len(futures), reserve, cap):
        if not futures:
            return "cap"
        account(wait_one(futures), futures, state, events, jobs_dir, reserve, run_dir, lock)
        drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock)
    return None


def pause_before_start(spacing: float, last_start: float | None, clock, sleep) -> bool:
    """Sleep until spacing seconds have passed since the previous start. True when it slept."""
    if last_start is None:
        return False
    remaining = spacing - (clock() - last_start)
    if remaining <= 0:
        return False
    sleep(remaining)
    return True


def cell_gate(cell: dict, floors: dict, sources, sleep):
    seen = {}
    for model in cell.get("assignment") or [cell["model"]]:
        provider = provider_of(model)
        if provider in seen:
            continue
        money, error = read_balance(sources, provider, sleep)
        if error is not None:
            return "balance", provider, error
        if money.amount < float(floors[provider]):
            return "floor", provider, money
        seen[provider] = money
    return None, None, None


def schedule(plan, pending, state, events, *, pool, jobs_dir, run_dir, harbor, wrapper, env, repo, sources, namer,
             sleep, clock):
    options = plan["make_jobs"]
    reserve, cap = float(plan["budget"]["reserve_per_trial_usd"]), float(plan["budget"]["cap_usd"])
    floors, concurrency = plan["budget"]["provider_floors"], options["concurrency"]
    limit = streak_of(plan)
    futures: dict = {}
    lock = threading.Lock()
    smoke = plan.get("smoke")
    index, spins, stop, last_start = 0, 0, None, None
    bound = len(pending) * 16 + 32
    try:
        while index < len(pending):
            spins += 1
            if spins > bound:
                raise SystemExit("refused: scheduler did not advance")
            drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock)
            if smoke_charged(plan, state, run_dir):
                stop = "charged"
                break
            if streak_stop(events, limit, run_dir):
                stop = "invalid"
                break
            if len(futures) >= concurrency:
                account(wait_one(futures), futures, state, events, jobs_dir, reserve, run_dir, lock)
                continue
            cell = pending[index]
            if wait_under_cap(futures, state, events, jobs_dir, reserve, run_dir, lock, cap) == "cap":
                stop = "charged" if smoke_charged(plan, state, run_dir) else "cap"
                break
            if streak_stop(events, limit, run_dir):
                stop = "invalid"
                break
            kind, provider, money = (None, None, None) if smoke else cell_gate(cell, floors, sources, sleep)
            drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock)
            if smoke_charged(plan, state, run_dir):
                stop = "charged"
                break
            if streak_stop(events, limit, run_dir):
                stop = "invalid"
                break
            if len(futures) >= concurrency or over_cap(state["spent_usd"], len(futures), reserve, cap):
                continue
            if kind == "balance":
                if futures:
                    stop = "balance"
                    log(run_dir, f"stop: {provider} balance read failed ({money}); draining in-flight jobs")
                    break
                raise SystemExit(f"refused: could not read {provider} balance ({money})")
            if kind == "floor":
                stop = "floor"
                set_by = f" (set by the {money.detail})" if money.detail else ""
                log(run_dir, f"stop: {provider} balance ${money.amount:.2f} is below floor "
                             f"${float(floors[provider]):.2f}{set_by}")
                break
            if pause_before_start(spacing_of(plan), last_start, clock, sleep):
                drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock)
                if smoke_charged(plan, state, run_dir):
                    stop = "charged"
                    break
                if streak_stop(events, limit, run_dir):
                    stop = "invalid"
                    break
                if over_cap(state["spent_usd"], len(futures), reserve, cap):
                    continue
            job = new_job(plan["name"], jobs_dir, namer)
            config_path = run_dir / "configs" / f"{job}.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config = job_config(job, jobs_dir, cell["task"], plan["agent"], cell["model"], options["max_turns"],
                                options["env"], cell.get("assignment"))
            if smoke:
                assert_free_config(config)
            config_path.write_text(json.dumps(config, indent=2) + "\n")
            command = harbor_command(options["env"], harbor, config_path, wrapper)
            log(run_dir, f"start {job} set {cell['task_set']} instance {cell['instance']} {cell['arm']} {label_of(cell)}")
            submit(pool, futures, events, cell, job, command, run_dir / f"{job}.log", env, repo,
                   state["spent_usd"], reserve, cap, concurrency)
            last_start = clock()
            index += 1
    finally:
        while futures:
            account(wait_one(futures), futures, state, events, jobs_dir, reserve, run_dir, lock)
    if stop != "invalid" and streak_stop(events, limit, run_dir, note="found after the in-flight jobs finished"):
        stop = "invalid"
    if stop != "charged" and smoke_charged(plan, state, run_dir):
        stop = "charged"
    return stop or "done"


def smoke_charged(plan: dict, state: dict, run_dir: Path) -> bool:
    """A smoke that recorded any spend: it must cost $0, so no new cell starts."""
    if not plan.get("smoke") or state["spent_usd"] <= 0:
        return False
    log(run_dir, f"stop: smoke recorded spend ${state['spent_usd']:.4f}; a free smoke must cost $0")
    return True


def smoke_run_dir(run_dir: Path | None, wave: dict) -> Path:
    if run_dir is None:
        return Path("/tmp/flotilla") / wave["name"]
    if not wave.get("smoke"):
        return run_dir
    resolved = run_dir.resolve()
    return resolved.with_name(resolved.name + SMOKE_SUFFIX)


def run(plan_path: Path, *, run_dir: Path | None = None, dry_run: bool = False, retry_invalid: bool = False,
        harbor: Path = HARBOR, jobs_dir: Path | None = None, wrapper: Path = WRAPPER,
        sources=None, base_env: dict[str, str] | None = None, namer=None, sleep=None, clock=None,
        smoke: int | None = None, history_jobs: Path | None = None) -> list[dict]:
    if smoke is not None and (isinstance(smoke, bool) or not isinstance(smoke, int) or smoke < 1):
        raise SystemExit("refused: --smoke takes a whole number of instances >= 1")
    plan_path = plan_path.resolve()
    plan = load_plan(plan_path, smoke=smoke is not None)
    repo = repo_of(plan_path)
    assert_prereg(plan, repo)
    scripted = scripted_of(plan, repo, need_tasks=not dry_run)
    assert_assignment_seats(plan, repo, scripted)
    wave = plan if smoke is None else smoke_plan(plan, smoke)
    base = dict(os.environ) if base_env is None else base_env
    sources = sources or HttpSources(base)
    models = used_models(wave)
    if dry_run:
        declared = sorted(plan.get("scripted_seats") or [])
        order = order_of(wave, None, [declared if seats is None else seats for seats in scripted])
        fetch = sources.catalog if smoke is None else refuse_catalog
        print_dry_run(wave, order, scripted, key_line(base, models),
                      PRICING.forecast_lines(wave, order, repo, history_jobs, fetch_catalog=fetch))
        return []
    assert_committed(plan_path, repo)
    if wave["make_jobs"]["env"] == "docker" and not wrapper.is_file():
        raise SystemExit(f"refused: docker wrapper {wrapper} is missing")
    paths = resolve_paths(plan, repo)
    order = order_of(wave, paths, scripted)
    if smoke is not None and any(model is not None for model in models) and not base.get(key_name(base, "openrouter")):
        raise SystemExit(f"refused: a :free smoke needs {FLOTILLA_KEY} or {KEYS['openrouter']}")
    run_dir = smoke_run_dir(run_dir, wave)
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir = jobs_dir or (repo / ".flotilla-local" / "jobs")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else None
    if state is None:
        state = initial_state(wave, order, digest, smoke)
        log_order(run_dir, order)
    else:
        check_resume(state, wave, order, digest, smoke)
        log(run_dir, f"resume {len(state['finished'])} finished")
    log(run_dir, f"start spacing {spacing_of(wave):.1f} s")
    if smoke is not None:
        log(run_dir, smoke_line(wave))
    log(run_dir, key_line(base, models))
    if any(scripted):
        log(run_dir, scripted_line(wave, scripted))
    if smoke is None:
        freeze_prices(sources, models, state)
    save_state(state_path, state)
    pending = pending_of(order, state["finished"], retry_invalid)
    events: list[dict] = []
    env = harbor_env(base, models, keep_modal=smoke is None)
    with ThreadPoolExecutor(max_workers=wave["make_jobs"]["concurrency"]) as pool:
        reason = schedule(wave, pending, state, events, pool=pool, jobs_dir=jobs_dir, run_dir=run_dir,
                          harbor=harbor, wrapper=wrapper, env=env, repo=repo, sources=sources,
                          namer=namer or secrets.token_hex, sleep=sleep or time.sleep, clock=clock or time.monotonic)
    events.append({"kind": "stop", "reason": reason, "spent": state["spent_usd"]})
    log(run_dir, f"stop {reason}: spent ${state['spent_usd']:.4f}")
    save_state(state_path, state)
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a pre-registered Flotilla wave, one Harbor job per cell.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--run-dir", type=Path, help="state and progress.log (default /tmp/flotilla/<plan>; a smoke "
                        "adds -smoke)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the order, the cost reserve and the cost forecast; do not call Harbor")
    parser.add_argument("--retry-invalid", action="store_true", help="rerun a finished cell whose trial was not valid")
    parser.add_argument("--harbor", type=Path, default=HARBOR)
    parser.add_argument("--jobs-dir", type=Path, help="where Harbor writes trial results")
    parser.add_argument("--docker-wrapper", type=Path, default=WRAPPER)
    parser.add_argument("--history-jobs", type=Path,
                        help="finished Harbor jobs the dry-run forecast reads (default <repo>/.flotilla-local/jobs)")
    parser.add_argument("--smoke", nargs="?", const=1, type=int, metavar="N",
                        help="free smoke: first N instances per task set (default 1), local docker, cap $0; only "
                        "the oracle agent with no model or OpenRouter :free models; write --smoke=N before the plan")
    args = parser.parse_args(argv)
    events = run(args.plan, run_dir=args.run_dir, dry_run=args.dry_run, retry_invalid=args.retry_invalid,
                 harbor=args.harbor, jobs_dir=args.jobs_dir, wrapper=args.docker_wrapper, smoke=args.smoke,
                 history_jobs=args.history_jobs)
    reason = events[-1].get("reason") if events else None
    if reason == "charged":
        return EXIT_SMOKE_CHARGED
    return EXIT_INVALID_STREAK if reason == "invalid" else 0


if __name__ == "__main__":
    sys.exit(main())
