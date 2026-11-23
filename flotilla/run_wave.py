"""Run a pre-registered Flotilla wave: one Harbor job per cell, under a spend cap.

    python3 flotilla/run_wave.py --dry-run flotilla/plans/example.json
    keys run -- python3 flotilla/run_wave.py flotilla/plans/<name>.json --run-dir /tmp/flotilla/<name>

A cell is (task set, instance, arm, model). Blocks are (task set, instance). The order seed shuffles the
blocks, then shuffles the cells inside each block, so families interleave and the order is a function of
the seed. Each job is <plan>-<6 hex> with n_concurrent_trials 1. Docker runs at most 2 jobs, each through
/tmp/crucible-docker-slot.sh. Modal has no wrapper.

The plan file must be committed and unmodified at HEAD. The prereg commit must exist and be an ancestor
of HEAD. A rerun skips finished cells. --retry-invalid reruns a cell whose trial was not valid.
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
import tomllib
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
HARBOR = Path.home() / "Developer/harbor-multiagent-flotilla/.venv/bin/harbor"
WRAPPER = Path("/tmp/crucible-docker-slot.sh")
KEYS = {"deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
LEAKY = ("silent", "talk", "open", "propose", "solo", "deepseek", "qwen", "gpt", "oracle")
_SPEC = importlib.util.spec_from_file_location("flotilla_pricing", ROOT / "pricing.py")
PRICING = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(PRICING)


def provider_of(model: str) -> str:
    if model.startswith("openrouter/"):
        return "openrouter"
    if model.startswith("deepseek/"):
        return "deepseek"
    raise SystemExit(f"refused: {model} is not a deepseek/ or openrouter/ model")


def cell_key(task_set: int, instance: int, arm: str, model: str) -> str:
    return json.dumps([task_set, instance, arm, model], separators=(",", ":"))


def load_plan(path: Path) -> dict:
    plan = json.loads(path.read_text())
    for field in ("name", "prereg", "task_sets", "models", "agent", "make_jobs", "budget", "order_seed"):
        if field not in plan:
            raise SystemExit(f"refused: plan missing {field}")
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
    if not plan["models"] or not plan["task_sets"]:
        raise SystemExit("refused: plan has no models or no task sets")
    if any(word in plan["name"] for word in LEAKY):
        raise SystemExit(f"refused: plan name {plan['name']} contains a condition or model word")
    floors = plan["budget"]["provider_floors"]
    for model in plan["models"]:
        provider = provider_of(model)
        if provider not in floors:
            raise SystemExit(f"refused: no floor for {provider}")
    for index, task_set in enumerate(plan["task_sets"]):
        for field in ("tasks_dir", "manifest", "instances"):
            if field not in task_set:
                raise SystemExit(f"refused: task set {index} missing {field}")
        if not (task_set.get("arms") or task_set.get("conditions")):
            raise SystemExit(f"refused: task set {index} has no arms")
    return plan


def repo_of(path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path.parent, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit("refused: plan is not in a git checkout")
    return Path(result.stdout.strip())


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
    doc = repo / plan["prereg"]["doc"]
    if not doc.is_file() or plan["prereg"]["section"] not in doc.read_text():
        raise SystemExit(f"refused: prereg section {plan['prereg']['section']!r} is not in {plan['prereg']['doc']}")


def arms_of(task_set: dict) -> list[str]:
    return list(task_set.get("arms") or task_set["conditions"])


def blocks_of(plan: dict, paths: dict[tuple[int, int, str], str] | None) -> list[list[dict]]:
    """One block per (task set, instance), cells in listed arm then model order. paths is None for a dry run."""
    blocks = []
    for index, task_set in enumerate(plan["task_sets"]):
        for instance in task_set["instances"]:
            block = []
            for arm in arms_of(task_set):
                for model in plan["models"]:
                    task = "" if paths is None else paths[(index, instance, arm)]
                    block.append({"task_set": index, "instance": instance, "arm": arm, "model": model,
                                   "task": task, "key": cell_key(index, instance, arm, model)})
            blocks.append(block)
    return blocks


def order_of(plan: dict, paths: dict[tuple[int, int, str], str] | None = None) -> list[dict]:
    """Shuffle blocks, then shuffle cells inside each block, with one Random(order_seed)."""
    rng = random.Random(plan["order_seed"])
    blocks = blocks_of(plan, paths)
    rng.shuffle(blocks)
    ordered = []
    for block in blocks:
        rng.shuffle(block)
        ordered.extend(block)
    return ordered


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


def seats_of(task: str) -> tuple[str, ...]:
    config = tomllib.loads((Path(task) / "task.toml").read_text())
    seats = tuple(sorted(config.get("agents", {})))
    if not seats:
        raise SystemExit(f"refused: {task} has no seats")
    return seats


def job_config(job: str, jobs_dir: Path, task: str, agent: str, model: str, max_turns: int, env: str) -> dict:
    participant = {"name": agent, "model_name": model}
    if max_turns:
        participant["kwargs"] = {"max_turns": max_turns}
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
        "participants": [{"seat": seat, **participant} for seat in seats_of(task)],
        "tasks": [{"path": task}],
    }


def harbor_command(env: str, harbor: Path, config: Path, wrapper: Path) -> list[str]:
    command = [str(harbor), "run", "-c", str(config), "-y"]
    if env == "modal":
        return command
    return [str(wrapper), *command]


def harbor_env(base: dict[str, str], models: list[str]) -> dict[str, str]:
    needed = {KEYS[provider_of(model)] for model in models}
    return {key: value for key, value in base.items() if not key.endswith("_API_KEY") or key in needed}


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


def print_dry_run(plan: dict, order: list[dict]) -> None:
    reserve = float(plan["budget"]["reserve_per_trial_usd"])
    cap = float(plan["budget"]["cap_usd"])
    print(f"{plan['name']}: {len(order)} cells, reserve ${reserve:.2f} per trial, cap ${cap:.2f}, "
          f"reserve if every cell starts ${reserve * len(order):.2f}")
    for index, cell in enumerate(order, 1):
        print(f"{index}. set {cell['task_set']} instance {cell['instance']} {cell['arm']} {cell['model']}")


class HttpSources:
    """OpenRouter catalog and provider balances. Tests pass a fake with the same two methods."""

    def __init__(self, env: dict[str, str]):
        self.env = env

    def prices(self, models: list[str]) -> dict:
        payload = _fetch_json("https://openrouter.ai/api/v1/models", {})
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

    def balance(self, provider: str) -> float:
        key = KEYS[provider]
        token = self.env.get(key)
        if not token:
            raise SystemExit(f"refused: {key} is not set")
        header = {"Authorization": "Bearer " + token}
        if provider == "deepseek":
            payload = _fetch_json("https://api.deepseek.com/user/balance", header)
            return PRICING.parse_deepseek_balance(payload)
        payload = _fetch_json("https://openrouter.ai/api/v1/credits", header)
        return PRICING.parse_openrouter_credits(payload)


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


def trial_valid(trial: Path | None, result: dict | None) -> bool:
    if trial is None or result is None:
        return False
    score_path, state_path = trial / "verifier" / "score.json", trial / "verifier" / "world-state.json"
    verdict = json.loads(score_path.read_text()) if score_path.is_file() else {}
    failed = [seat.get("seat") for seat in result.get("seat_results") or [] if seat.get("exception_info")]
    return bool(verdict) and state_path.is_file() and verdict.get("error") is None and not result.get("exception_info") and not failed


def when_of(result: dict) -> datetime:
    started = result.get("started_at")
    return datetime.fromisoformat(started).astimezone(UTC) if started else datetime.now(UTC)


def charge(cell: dict, result: dict | None, prices: dict, reserve: float) -> dict:
    if result is None:
        return {"cost": reserve, "estimate": None, "source": "reserve", "flag": "no-result"}
    if PRICING.is_deepseek_direct(cell["model"]):
        amount = PRICING.deepseek_cost(PRICING.tokens_of(result), when_of(result))
        return {"cost": amount, "estimate": amount, "source": "deepseek", "flag": None}
    try:
        amount, estimate, source = PRICING.openrouter_spend(result, cell["model"], prices)
    except KeyError:
        return {"cost": reserve, "estimate": None, "source": "reserve", "flag": "no-price"}
    flag = "missing-recorded" if source == "token-estimate" else None
    return {"cost": amount, "estimate": estimate, "source": source, "flag": flag}


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
        if row is None or (retry_invalid and not row["valid"]):
            pending.append(cell)
    return pending


def log_order(run_dir: Path, order: list[dict]) -> None:
    log(run_dir, f"order {len(order)} cells")
    for index, cell in enumerate(order, 1):
        log(run_dir, f"order {index} set {cell['task_set']} instance {cell['instance']} {cell['arm']} {cell['model']}")


def check_resume(state: dict, plan: dict, order: list[dict], digest: str) -> None:
    if state.get("plan_sha256") != digest:
        raise SystemExit("refused: plan file changed since this run dir was started")
    stored = state.get("order") or []
    if [row["key"] for row in stored] != [cell["key"] for cell in order]:
        raise SystemExit("refused: order does not match the recorded order")
    by_key = {row["key"]: row.get("task") for row in stored}
    for cell in order:
        if by_key.get(cell["key"]) not in (None, "", cell["task"]):
            raise SystemExit("refused: a cell's task path changed; use a new run dir")


def initial_state(plan: dict, order: list[dict], digest: str) -> dict:
    return {"plan": plan["name"], "plan_sha256": digest, "order": order, "prices": {},
            "spent_usd": 0.0, "finished": {}}


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
    bill = charge(cell, result, state["prices"], reserve)
    valid = trial_valid(trial, result)
    with lock:
        state["spent_usd"] += bill["cost"]
        state["finished"][cell["key"]] = {
            "job": job, "valid": valid, "cost_usd": bill["cost"], "estimate_usd": bill["estimate"],
            "cost_source": bill["source"], "flag": bill["flag"], "exit_code": exit_code,
            "started_at": (result or {}).get("started_at"), "finished_at": datetime.now(UTC).isoformat(),
        }
        save_state(run_dir / "state.json", state)
        spent = state["spent_usd"]
    estimate = bill["estimate"]
    estimate_text = "none" if estimate is None else f"${estimate:.4f}"
    flag = f" flagged {bill['flag']}" if bill["flag"] else ""
    log(run_dir, f"end {job} set {cell['task_set']} instance {cell['instance']} {cell['arm']} {cell['model']} "
                 f"exit {exit_code} valid {valid} cost ${bill['cost']:.4f} estimate {estimate_text} "
                 f"source {bill['source']}{flag} spent ${spent:.4f}")
    events.append({"kind": "end", "key": cell["key"], "job": job, "valid": valid, "cost": bill["cost"],
                   "estimate": estimate, "source": bill["source"], "flag": bill["flag"], "exit_code": exit_code,
                   "spent": spent})


def drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock) -> None:
    for future in [item for item in futures if item.done()]:
        account(future, futures, state, events, jobs_dir, reserve, run_dir, lock)


def wait_one(futures):
    done, _pending = wait(set(futures), return_when=FIRST_COMPLETED)
    return done.pop()


def schedule(plan, pending, state, events, *, pool, jobs_dir, run_dir, harbor, wrapper, env, repo, sources, namer):
    options = plan["make_jobs"]
    reserve, cap = float(plan["budget"]["reserve_per_trial_usd"]), float(plan["budget"]["cap_usd"])
    floors, concurrency = plan["budget"]["provider_floors"], options["concurrency"]
    futures: dict = {}
    lock = threading.Lock()
    index, spins, stop = 0, 0, None
    bound = len(pending) * 8 + 8
    try:
        while index < len(pending):
            spins += 1
            if spins > bound:
                raise SystemExit("refused: scheduler did not advance")
            drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock)
            if len(futures) >= concurrency:
                account(wait_one(futures), futures, state, events, jobs_dir, reserve, run_dir, lock)
                continue
            cell = pending[index]
            if over_cap(state["spent_usd"], len(futures), reserve, cap):
                stop = "cap"
                break
            provider = provider_of(cell["model"])
            money = sources.balance(provider)
            drain_done(futures, state, events, jobs_dir, reserve, run_dir, lock)
            if len(futures) >= concurrency:
                continue
            if over_cap(state["spent_usd"], len(futures), reserve, cap):
                stop = "cap"
                break
            if money < float(floors[provider]):
                stop = "floor"
                log(run_dir, f"stop: {provider} balance ${money:.2f} is below floor ${float(floors[provider]):.2f}")
                break
            job = new_job(plan["name"], jobs_dir, namer)
            config_path = run_dir / "configs" / f"{job}.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(job_config(
                job, jobs_dir, cell["task"], plan["agent"], cell["model"], options["max_turns"], options["env"]), indent=2) + "\n")
            command = harbor_command(options["env"], harbor, config_path, wrapper)
            log(run_dir, f"start {job} set {cell['task_set']} instance {cell['instance']} {cell['arm']} {cell['model']}")
            submit(pool, futures, events, cell, job, command, run_dir / f"{job}.log", env, repo,
                   state["spent_usd"], reserve, cap, concurrency)
            index += 1
    finally:
        while futures:
            account(wait_one(futures), futures, state, events, jobs_dir, reserve, run_dir, lock)
    return stop or "done"


def run(plan_path: Path, *, run_dir: Path | None = None, dry_run: bool = False, retry_invalid: bool = False,
        harbor: Path = HARBOR, jobs_dir: Path | None = None, wrapper: Path = WRAPPER,
        sources=None, base_env: dict[str, str] | None = None, namer=None) -> list[dict]:
    plan_path = plan_path.resolve()
    plan = load_plan(plan_path)
    repo = repo_of(plan_path)
    assert_prereg(plan, repo)
    if dry_run:
        print_dry_run(plan, order_of(plan))
        return []
    assert_committed(plan_path, repo)
    if plan["make_jobs"]["env"] == "docker" and not wrapper.is_file():
        raise SystemExit(f"refused: docker wrapper {wrapper} is missing")
    paths = resolve_paths(plan, repo)
    order = order_of(plan, paths)
    run_dir = run_dir or Path("/tmp/flotilla") / plan["name"]
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir = jobs_dir or (repo / ".flotilla-local" / "jobs")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else None
    if state is None:
        state = initial_state(plan, order, digest)
        log_order(run_dir, order)
    else:
        check_resume(state, plan, order, digest)
        log(run_dir, f"resume {len(state['finished'])} finished")
    sources = sources or HttpSources(base_env or dict(os.environ))
    freeze_prices(sources, plan["models"], state)
    save_state(state_path, state)
    pending = pending_of(order, state["finished"], retry_invalid)
    events: list[dict] = []
    env = harbor_env(base_env or dict(os.environ), plan["models"])
    with ThreadPoolExecutor(max_workers=plan["make_jobs"]["concurrency"]) as pool:
        reason = schedule(plan, pending, state, events, pool=pool, jobs_dir=jobs_dir, run_dir=run_dir,
                          harbor=harbor, wrapper=wrapper, env=env, repo=repo, sources=sources,
                          namer=namer or secrets.token_hex)
    events.append({"kind": "stop", "reason": reason, "spent": state["spent_usd"]})
    log(run_dir, f"stop {reason}: spent ${state['spent_usd']:.4f}")
    save_state(state_path, state)
    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a pre-registered Flotilla wave, one Harbor job per cell.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--run-dir", type=Path, help="state and progress.log (default /tmp/flotilla/<plan>)")
    parser.add_argument("--dry-run", action="store_true", help="print the order and the cost reserve; do not call Harbor")
    parser.add_argument("--retry-invalid", action="store_true", help="rerun a finished cell whose trial was not valid")
    parser.add_argument("--harbor", type=Path, default=HARBOR)
    parser.add_argument("--jobs-dir", type=Path, help="where Harbor writes trial results")
    parser.add_argument("--docker-wrapper", type=Path, default=WRAPPER)
    args = parser.parse_args(argv)
    run(args.plan, run_dir=args.run_dir, dry_run=args.dry_run, retry_invalid=args.retry_invalid,
        harbor=args.harbor, jobs_dir=args.jobs_dir, wrapper=args.docker_wrapper)
    return 0


if __name__ == "__main__":
    sys.exit(main())
