"""Oracle parity: did every cell of a finished local Docker oracle rehearsal get the simulator's reward?

Offline and read-only. It reads a committed oracle wave plan (run_wave.py), that wave's run directory (state.json,
configs/) and the Harbor jobs directory, then replays each planned task folder through sim.simulate_built with the
task's built seat policies. It launches no job, reads no key and writes nothing in the run, jobs or task folders.

A cell passes only when: its key is in the plan's order and in state.json, it has exactly one valid attempt, the
stored job config equals the config run_wave.py would write now, its job folder holds exactly one trial whose ids,
task path, roster and agents match, run_wave.trial_problem finds nothing wrong, Harbor recorded a finite reward in
[0, 1] that the verifier files repeat, and the simulator gives that same reward on every checked schedule seed. A
missing reward is a failure, never a zero. A cell may hold earlier invalid attempts (retries); it counts once.

    python flotilla/oracle_parity.py flotilla/plans/oracle/handoff-pilot-oracle.json /tmp/run
    python flotilla/oracle_parity.py flotilla/plans/oracle/handoff-pilot-oracle.json /tmp/run --rev 7bdf55d0 --json

Exit 0 when every cell matches, 1 otherwise. The receipt holds no seed, briefing, answer key or task folder path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUESTION = "did every task in this local Docker oracle rehearsal get the same reward as its simulator?"
SCHEDULES = 3
ABSOLUTE = re.compile(r"(?<![\w.~/-])~?/[^\s'\"(),]+")
NOT_BOUND = (
    "Docker image bytes: job configs set force_build false, so Docker may reuse an image built from older "
    "environment bytes; no retained file records the image digest.",
    "Wall-clock interleaving: the Docker run records no schedule; parity is checked against the simulator only on "
    "the listed number of schedule seeds.",
    "Retained files: state.json, configs and trial folders are local, unsigned and mutable; only their mutual "
    "consistency and the plan sha256 are checked.",
    "Harbor and its oracle agent code: the plan pins neither; the receipt names only the agent the trial recorded.",
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


RUN = _load(ROOT / "run_wave.py", "flotilla_parity_run_wave")


def task_digest_fn():
    """Harbor's own task content hash (the trial lock digest), or None when Harbor is not importable."""
    try:
        from harbor.publisher.packager import Packager
    except ImportError:
        return None
    return lambda folder: "sha256:" + Packager.compute_content_hash(Path(folder))[0]


def plan_bytes(plan_path: Path, repo: Path, rev: str | None) -> tuple[bytes, str]:
    if rev is None:
        RUN.assert_committed(plan_path, repo)
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True,
                              check=True).stdout.strip()
        return plan_path.read_bytes(), f"HEAD {head}"
    resolved = subprocess.run(["git", "rev-parse", "--verify", f"{rev}^{{commit}}"], cwd=repo, capture_output=True,
                              text=True, check=False)
    if resolved.returncode != 0:
        raise SystemExit(f"refused: {rev} is not a commit")
    commit = resolved.stdout.strip()
    rel = plan_path.resolve().relative_to(repo.resolve()).as_posix()
    shown = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=repo, capture_output=True, check=False)
    if shown.returncode != 0:
        raise SystemExit(f"refused: {rel} is not in commit {rev}")
    return shown.stdout, f"commit {commit[:12]}"


def assert_prereg_at(plan: dict, repo: Path, rev: str | None) -> None:
    """run_wave's prereg check; for a plan read from an older commit the prereg must precede that commit."""
    if rev is None:
        RUN.assert_prereg(plan, repo)
        return
    commit, doc = plan["prereg"]["commit"], plan["prereg"]["doc"]
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", commit, rev], cwd=repo, capture_output=True,
                              check=False)
    if ancestor.returncode != 0:
        raise SystemExit(f"refused: prereg commit {commit} is not an ancestor of {rev}")
    shown = subprocess.run(["git", "show", f"{commit}:{doc}"], cwd=repo, capture_output=True, text=True, check=False)
    if shown.returncode != 0 or plan["prereg"]["section"] not in shown.stdout:
        raise SystemExit(f"refused: prereg section {plan['prereg']['section']!r} is not in {commit}:{doc}")


def load_wave(plan_path: Path, rev: str | None, state: dict) -> tuple[dict, dict, Path, str, str]:
    """(plan, wave, repo, sha256, source) after the plan, prereg and state checks."""
    plan_path = plan_path.resolve()
    repo = RUN.repo_of(plan_path)
    data, source = plan_bytes(plan_path, repo, rev)
    digest = hashlib.sha256(data).hexdigest()
    with tempfile.TemporaryDirectory(prefix="flotilla-parity-") as scratch:
        copy = Path(scratch) / "plan.json"
        copy.write_bytes(data)
        plan = RUN.load_plan(copy)
    if plan["agent"] != "oracle":
        raise SystemExit(f"refused: plan agent is {plan['agent']}, not oracle")
    if "stages" in plan:
        raise SystemExit("refused: a staged plan is not an oracle rehearsal")
    assert_prereg_at(plan, repo, rev)
    mode, count = state.get("mode", "wave"), state.get("smoke")
    if mode == "wave" and count is None:
        wave = plan
    elif mode == "smoke" and RUN._whole(count) and count >= 1:
        wave = RUN.smoke_plan(plan, count)
    else:
        raise SystemExit("refused: state.json holds no wave or smoke mode")
    if state.get("plan") != wave["name"]:
        raise SystemExit(f"refused: state.json belongs to plan {state.get('plan')}, not {wave['name']}")
    if state.get("plan_sha256") != digest:
        raise SystemExit("refused: state.json was started from other plan bytes")
    if wave["make_jobs"]["env"] != "docker":
        raise SystemExit("refused: not a local Docker wave")
    return plan, wave, repo, digest, source


def jobs_dir_of(run_dir: Path, given: Path | None, jobs: list[str]) -> Path:
    named = set()
    for job in jobs:
        path = run_dir / "configs" / f"{job}.json"
        if path.is_file():
            named.add(Path(json.loads(path.read_text()).get("jobs_dir", "")).resolve())
    if given is not None:
        if named - {given.resolve()}:
            raise SystemExit("refused: --jobs-dir is not the jobs_dir the job configs name")
        return given.resolve()
    if len(named) != 1:
        raise SystemExit("refused: the job configs name no single jobs_dir; pass --jobs-dir")
    return named.pop()


def harbor_reward(trial: Path, result: dict) -> tuple[float | None, str | None]:
    """The reward Harbor recorded, repeated by every verifier file present; None with a reason otherwise."""
    if result.get("reward_valid") is not True:
        return None, "Harbor marked the reward invalid or did not record reward_valid"
    rewards = (result.get("verifier_result") or {}).get("rewards")
    value = rewards.get("reward") if isinstance(rewards, dict) else None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        return None, "no valid verifier reward in result.json"
    value = float(value)
    copies = []
    for name in ("reward.json", "score.json"):
        path = trial / "verifier" / name
        if path.is_file():
            copies.append((name, (RUN.read_score(path) or {}).get("reward")))
    text = trial / "verifier" / "reward.txt"
    if text.is_file():
        try:
            copies.append(("reward.txt", float(text.read_text().strip())))
        except ValueError:
            copies.append(("reward.txt", None))
    if not any(name in ("reward.json", "reward.txt") for name, _ in copies):
        return None, "no verifier reward file"
    for name, copy in copies:
        if type(copy) not in (int, float) or float(copy) != value:
            return None, f"verifier {name} reward differs from result.json"
    return value, None


def one_attempt(row: dict | None) -> tuple[dict | None, int, str | None]:
    attempts = RUN.attempts_of(row)
    good = [item for item in attempts if item.get("valid") is True]
    if not attempts:
        return None, 0, "missing: no finished attempt"
    if len(good) != 1:
        return None, len(attempts), f"ambiguous: {len(good)} valid attempts"
    if row.get("job") != good[0].get("job") or attempts[-1] is not good[0]:
        return None, len(attempts), "ambiguous: the valid attempt is not the last recorded job"
    return good[0], len(attempts), None


def the_trial(job_dir: Path) -> tuple[Path | None, str | None]:
    if not job_dir.is_dir():
        return None, "missing: no job folder"
    folders = [path for path in job_dir.iterdir() if path.is_dir()]
    if len(folders) != 1 or not (folders[0] / "result.json").is_file():
        return None, f"ambiguous: {len(folders)} trial folders"
    return folders[0], None


def rows_of(value) -> list[dict] | None:
    return value if isinstance(value, list) and all(isinstance(row, dict) for row in value) else None


def identity_problem(cell: dict, job: str, jobs_dir: Path, trial: Path, result, digest_of) -> str | None:
    if not isinstance(result, dict) or not isinstance(result.get("config"), dict):
        return "trial result.json is malformed"
    config, task = result["config"], cell["task"]
    if result.get("trial_name") != trial.name or config.get("trial_name") != trial.name:
        return "trial id differs from its folder"
    if (result.get("task_id") or {}).get("path") != task or (config.get("task") or {}).get("path") != task:
        return "trial ran another task path"
    if Path(str(config.get("trials_dir", ""))).resolve() != (jobs_dir / job).resolve():
        return "trial belongs to another job folder"
    if (result.get("agent_info") or {}).get("name") != "oracle" or (config.get("agent") or {}).get("name") != "oracle":
        return "trial agent is not oracle"
    planned, ran = rows_of(config.get("participants")), rows_of(result.get("seat_results"))
    if planned is None or ran is None:
        return "trial participants or seat results are malformed"
    seats, _scripted = RUN.roster_of(task)
    if tuple(sorted(str(row.get("seat")) for row in planned)) != seats:
        return "trial participants differ from the task.toml roster"
    if tuple(sorted(str(row.get("seat")) for row in ran)) != seats:
        return "trial seat results differ from the task.toml roster"
    for row in planned:
        if row.get("name") != "oracle" or row.get("model_name") is not None:
            return f"seat {row['seat']} was configured as {row.get('name')!r}, not the oracle with no model"
    for row in ran:
        name = (row.get("agent_info") or {}).get("name")
        if name != "oracle":
            return f"seat {row['seat']} ran {name!r}, not the oracle agent"
    lock_path = trial / "lock.json"
    lock = json.loads(lock_path.read_text()).get("task") if lock_path.is_file() else None
    if not isinstance(lock, dict) or lock.get("path") != task or not lock.get("digest"):
        return "trial lock does not name this task"
    if digest_of(task) != lock["digest"]:
        return "task folder bytes changed since the trial ran"
    return None


def simulated(task: str, schedules: int, simulate) -> tuple[float | None, list[str], str | None]:
    rewards, names = set(), set()
    for schedule in range(schedules):
        try:
            run = simulate(Path(task), None, schedule)
        except Exception as exc:  # noqa: BLE001 - a simulator failure is a failed cell, never a reward
            if "rebuild the task" in str(exc):
                return None, [], "simulator refused: the built files differ from this checkout"
            return None, [], f"simulator error {type(exc).__name__}"
        value = run.get("reward")
        if run.get("invalid") or run.get("stalled") or (run.get("score") or {}).get("invalid"):
            return None, [], "simulator run invalid or stalled"
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            return None, [], "simulator gave no valid reward"
        rewards.add(float(value))
        names.update((run.get("policies") or {}).values())
    if len(rewards) != 1:
        return None, sorted(names), "simulator reward depends on the schedule seed"
    return rewards.pop(), sorted(names), None


def blank_row(cell: dict) -> dict:
    return {"cell": f"set {cell['task_set']} instance {cell['instance']} {cell['arm']}",
            "task": Path(cell["task"]).name, "job": None, "trial": None, "attempts": 0, "oracle": None, "sim": None,
            "policies": [], "problem": None}


def check_cell(cell: dict, row: dict | None, run_dir: Path, jobs_dir: Path, wave: dict, schedules: int, simulate,
               digest_of) -> dict:
    out = blank_row(cell)
    attempt, out["attempts"], problem = one_attempt(row)
    if problem:
        return {**out, "problem": problem}
    job = out["job"] = attempt["job"]
    config_path = run_dir / "configs" / f"{job}.json"
    if not config_path.is_file():
        return {**out, "problem": "missing: no stored job config"}
    stored = json.loads(config_path.read_text())
    options = wave["make_jobs"]
    expected = RUN.job_config(job, Path(stored.get("jobs_dir", "")), cell["task"], wave["agent"], cell["model"],
                              options["max_turns"], options["env"], cell.get("assignment"))
    if stored != expected:
        return {**out, "problem": "stored job config differs from the plan's config"}
    trial, problem = the_trial(jobs_dir / job)
    if problem:
        return {**out, "problem": problem}
    out["trial"] = trial.name
    _trial, result = RUN.read_trial(jobs_dir, job)
    problem = (identity_problem(cell, job, jobs_dir, trial, result, digest_of)
               or RUN.trial_problem(trial, result, cell))
    if problem:
        return {**out, "problem": f"invalid trial: {problem}"}
    out["oracle"], problem = harbor_reward(trial, result)
    if problem:
        return {**out, "problem": problem}
    out["sim"], out["policies"], problem = simulated(cell["task"], schedules, simulate)
    if problem:
        return {**out, "problem": problem}
    if out["sim"] != out["oracle"]:
        return {**out, "problem": "mismatch: oracle reward differs from the simulator"}
    return out


def unaccounted(run_dir: Path, jobs_dir: Path, wave: dict, state: dict) -> list[str]:
    known = {item.get("job") for row in state["finished"].values() for item in RUN.attempts_of(row)}
    prefix = (f"smoke-{wave['name'].removesuffix(RUN.SMOKE_SUFFIX)}" if wave.get("smoke") else wave["name"]) + "-"
    configs = {path.stem for path in (run_dir / "configs").glob(f"{prefix}*.json")}
    folders = {path.name for path in jobs_dir.glob(f"{prefix}*") if path.is_dir()}
    return [f"unaccounted: job {job} is in no recorded attempt (in flight or lost)"
            for job in sorted((configs | folders) - known)]


def scrub(text: str) -> str:
    """A refusal from a run_wave helper without absolute machine paths (task folders, manifests, jobs)."""
    return ABSOLUTE.sub("<path>", text)


def verify(plan_path: Path, run_dir: Path, *, jobs_dir: Path | None = None, rev: str | None = None,
           schedules: int = SCHEDULES, simulate=None, digest_of=None) -> dict:
    """The receipt. simulate defaults to sim.simulate_built; digest_of to Harbor's task hash, which PASS requires."""
    if schedules < 1:
        raise SystemExit("refused: --schedules must be >= 1")
    receipt = {"question": QUESTION, "verdict": "FAIL", "plan": None, "plan_sha256": None, "plan_source": None,
               "mode": None, "planned": 0, "matched": 0, "schedule_seeds_checked": schedules,
               "task_digest": None, "problems": [], "cells": [], "not_bound": list(NOT_BOUND)}
    try:
        state_path = run_dir / "state.json"
        if not state_path.is_file():
            raise SystemExit("refused: run dir has no state.json")
        state = json.loads(state_path.read_text())
        _plan, wave, repo, digest, source = load_wave(plan_path, rev, state)
        receipt.update(plan=wave["name"], plan_sha256=digest, plan_source=source, mode=state.get("mode", "wave"))
        paths = RUN.resolve_paths(wave, repo)
        scripted = RUN.scripted_of(wave, repo, need_tasks=True)
        order = RUN.order_of(wave, paths, scripted)
        if state.get("order") != order:
            raise SystemExit("refused: state.json order differs from the plan's order")
        finished = state.get("finished")
        if not isinstance(finished, dict):
            raise SystemExit("refused: state.json has no finished cells")
        jobs = [item.get("job") for row in finished.values() for item in RUN.attempts_of(row)]
        jobs_dir = jobs_dir_of(run_dir, jobs_dir, jobs)
    except (SystemExit, ValueError, OSError, KeyError, TypeError, AttributeError,
            subprocess.CalledProcessError) as exc:
        receipt["problems"].append(scrub(str(exc)) if isinstance(exc, SystemExit)
                                   else f"refused: unreadable run record ({type(exc).__name__})")
        return receipt
    if digest_of is None:
        digest_of = task_digest_fn()
    if digest_of is None:
        receipt["problems"].append("refused: Harbor's task digest is not importable, so the task bytes the trials "
                                   "ran cannot be checked; run under the repo's environment (uv run)")
        return receipt
    if simulate is None:
        simulate = _load(ROOT / "sim.py", "flotilla_parity_sim").simulate_built
    receipt["task_digest"] = "checked against each trial lock"
    receipt["planned"] = len(order)
    keys = {cell["key"] for cell in order}
    receipt["problems"] += [f"extra: finished cell {key} is not in the plan" for key in sorted(set(finished) - keys)]
    receipt["problems"] += unaccounted(run_dir, jobs_dir, wave, state)
    for cell in order:
        try:
            row = check_cell(cell, finished.get(cell["key"]), run_dir, jobs_dir, wave, schedules, simulate, digest_of)
        except (SystemExit, ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            detail = scrub(str(exc)) if isinstance(exc, SystemExit) else type(exc).__name__
            row = {**blank_row(cell), "problem": f"invalid: unreadable record ({detail})"}
        if row["problem"]:
            row["problem"] = scrub(row["problem"])
        receipt["cells"].append(row)
    receipt["matched"] = sum(row["problem"] is None for row in receipt["cells"])
    if not receipt["problems"] and receipt["planned"] and receipt["matched"] == receipt["planned"]:
        receipt["verdict"] = "PASS"
    return receipt


def text_of(receipt: dict) -> str:
    lines = [f"oracle parity {receipt['verdict']}: {receipt['matched']}/{receipt['planned']} cells match",
             f"question: {receipt['question']}",
             f"plan {receipt['plan']} sha256 {receipt['plan_sha256']} from {receipt['plan_source']}, "
             f"mode {receipt['mode']}, {receipt['schedule_seeds_checked']} schedule seeds per cell",
             f"task digest: {receipt['task_digest']}"]
    lines += [f"problem: {item}" for item in receipt["problems"]]
    for row in receipt["cells"]:
        policies = ",".join(row["policies"]) or "-"
        lines.append(f"{'ok  ' if row['problem'] is None else 'FAIL'} {row['cell']} task {row['task']} job "
                     f"{row['job']} trial {row['trial']} attempts {row['attempts']} oracle {row['oracle']} sim "
                     f"{row['sim']} policies {policies}" + (f" -- {row['problem']}" if row["problem"] else ""))
    lines += [f"not bound: {item}" for item in receipt["not_bound"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a finished oracle rehearsal against the simulator, offline.")
    parser.add_argument("plan", type=Path, help="the committed oracle wave plan run_wave.py ran")
    parser.add_argument("run_dir", type=Path, help="the wave's run directory (state.json, configs/)")
    parser.add_argument("--jobs-dir", type=Path, help="Harbor jobs directory (default: the one the job configs name)")
    parser.add_argument("--rev", help="read the plan from this commit instead of HEAD (a wave run from older plan "
                        "bytes)")
    parser.add_argument("--schedules", type=int, default=SCHEDULES, help="simulator schedule seeds per cell")
    parser.add_argument("--json", action="store_true", help="print the receipt as JSON")
    args = parser.parse_args(argv)
    receipt = verify(args.plan, args.run_dir, jobs_dir=args.jobs_dir, rev=args.rev, schedules=args.schedules)
    print(json.dumps(receipt, indent=2) if args.json else text_of(receipt))
    return 0 if receipt["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
