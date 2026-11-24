"""Reliability ledger: what breaks in Flotilla trials and what it costs, across every wave.

    python3 flotilla/docs/results/scripts/reliability.py .flotilla-local/jobs ['xm1-??????' ...] \
        [--runs .flotilla-local/runs] [--json out.json] [--threshold 500] [--window 120] [--burst-gap 600]

Every trial folder of the matching jobs (default glob '*') is valid, invalid with one primary cause, abandoned
(no result.json, its job finished or no write for watch.ABANDONED_SECONDS) or running (left out of every count).
A finished trial is valid by the lab's rules: no Harbor or seat exception, a verifier score without an error and a
world state (xm_analysis.harness_problems), a usable reward (load._reward), the run_wave cell check
(run_wave.trial_valid, for jobs listed in a --runs state.json), one world start, and every seat under test started.
Causes of an invalid trial, in precedence order (the first found is primary): cancelled (CancelledError),
environment (Docker, Compose or Modal setup), provider (a LiteLLM error class with its HTTP code, from the
exception or from trial.log), world-restart (more than one world start), seat-never-started (a seat under test
with no execution start and no agent step), verifier, agent-timeout, missing-reward, other. A seat stalls at its
first gap above --threshold seconds between trajectory steps (stall_scan.first_gap); stalled seats group into
events (stall_scan.events_of). Cost is the Harbor-recorded seat cost_usd (an abandoned trial: its trajectory
step costs). Wave = the plan of the run_wave state.json that lists the job, else the job name before '-<6 hex>',
else before the first '-'. Environment = the job config's environment.type. Invalid trials whose starts are at
most --burst-gap seconds apart form a burst. Times shown in ET. Standard library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3] / "analysis"))

from flotilla_analysis import load as LOAD
from flotilla_analysis import watch as WATCH


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


SS = _load("reliability_stall", HERE.parent / "stall_scan.py")
XM = _load("reliability_xm", HERE.parent / "xm_analysis.py")
RW = _load("reliability_run_wave", HERE.parents[3] / "run_wave.py")

ET = ZoneInfo("America/New_York")
CAUSES = ("cancelled", "environment", "provider", "world-restart", "seat-never-started", "verifier", "agent-timeout",
          "missing-reward", "other", "abandoned")
LITELLM_STATUS = {
    "BadRequestError": 400, "ContextWindowExceededError": 400, "ContentPolicyViolationError": 400,
    "UnsupportedParamsError": 400, "AuthenticationError": 401, "PermissionDeniedError": 403, "NotFoundError": 404,
    "Timeout": 408, "UnprocessableEntityError": 422, "RateLimitError": 429, "InternalServerError": 500,
    "BadGatewayError": 502, "ServiceUnavailableError": 503,
}
VERIFIER_TYPES = frozenset({"VerifierTimeoutError", "VerifierOutputParseError", "RewardFileNotFoundError",
                            "RewardFileEmptyError", "RegradeError"})
ENVIRONMENT_TYPE = re.compile(r"Environment|Docker|Compose|Modal|Sandbox|Build", re.IGNORECASE)
ENVIRONMENT_TEXT = re.compile(r"docker|compose|modal|sandbox|image build", re.IGNORECASE)
HTTP_CODE = re.compile(r'"code"\s*:\s*"?(\d{3})\b|status[_ ]?code\W{1,3}(\d{3})\b|Error code:? (\d{3})\b')
TIMEOUT_SECONDS = re.compile(r"after ([0-9.]+) seconds")
RESTART = re.compile(r"restart", re.IGNORECASE)
JOB_SUFFIX = re.compile(r"^(?P<wave>.+)-[0-9a-f]{6}$")
NORMALIZE = (
    (re.compile(r"^(Trial \S+ failed: |Seat '[^']*': )+"), ""),
    (re.compile(r"\b\S+__[A-Za-z0-9]{7}\b"), "<trial>"),
    (re.compile(r"\bta-[0-9A-Z]{10,}\b"), "<id>"),
    (re.compile(r"\(Error code: [0-9A-Z]+\)"), "(Error code: <code>)"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<hex>"),
    (re.compile(r"(?:/[\w.@-]+){2,}"), "<path>"),
    (re.compile(r"\b\d+\.\d+\b|\b\d{4,}\b"), "N"),
    (re.compile(r"\bp\d+\b"), "pN"),
    (re.compile(r"\s+"), " "),
)
MESSAGE_CHARS = 160
TOP_MESSAGES = 10
MIN_BURST = 3
MIN_MODEL_TRIALS = 10
FIRST_HOUR_S = 3600.0


def block(data: Any, name: str) -> dict:
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def span_s(phase: dict) -> float | None:
    start, end = LOAD.parse_time(phase.get("started_at")), LOAD.parse_time(phase.get("finished_at"))
    return (end - start).total_seconds() if start and end else None


def normalize(text: str) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    for pattern, replacement in NORMALIZE:
        line = pattern.sub(replacement, line)
    return line.strip()[:MESSAGE_CHARS]


def provider_detail(kind: str, message: str) -> str:
    found = WATCH.LITELLM_ERROR.search(message)
    name = found.group(1) if found else kind
    code = HTTP_CODE.search(message)
    status = next((group for group in code.groups() if group), None) if code else None
    status = status or LITELLM_STATUS.get(name)
    return f"{name} {status}" if status else name


def exception_cause(kind: str, message: str) -> tuple[str, str]:
    assert kind, "an exception has a type"
    if kind == "CancelledError":
        return "cancelled", kind
    if kind == "AgentTimeoutError":
        seconds = TIMEOUT_SECONDS.search(message)
        return "agent-timeout", f"{kind} {float(seconds.group(1)):g}s" if seconds else kind
    if kind in VERIFIER_TYPES:
        return "verifier", kind
    if "litellm." in message or kind in LITELLM_STATUS:
        return "provider", provider_detail(kind, message)
    if ENVIRONMENT_TYPE.search(kind) or ENVIRONMENT_TEXT.search(message):
        return "environment", f"{kind}: {normalize(message)[:80]}"
    return "other", kind


def seat_list(directory: Path, config: dict, result: dict | None) -> list[dict]:
    participants = {p.get("seat"): p for p in config.get("participants") or [] if isinstance(p, dict)}
    blocks = [s for s in (result or {}).get("seat_results") or [] if isinstance(s, dict)]
    if not blocks:
        blocks = [{"seat": seat} for seat in participants] if participants else [{**(result or {}), "seat": "agent"}]
    seats = []
    for data in blocks:
        name = str(data.get("seat"))
        part = participants.get(name) or (block(config, "agent") if name == "agent" else {})
        info = block(block(data, "agent_info"), "model_info")
        model = part.get("model_name") or (f"{info['provider']}/{info['name']}" if info.get("provider")
                                           and info.get("name") else info.get("name"))
        agent = part.get("name") or block(data, "agent_info").get("name")
        exception = block(data, "exception_info")
        folder = directory / "agent" if name == "agent" else directory / "seats" / name / "agent"
        seats.append({
            "seat": name, "model": model or "unknown",
            "scripted": agent == "oracle" or data.get("under_test") is False,
            "cost_usd": LOAD._money(block(data, "agent_result").get("cost_usd")),
            "started": LOAD.parse_time(block(data, "agent_execution").get("started_at")) is not None,
            "exception": (exception.get("exception_type"), exception.get("exception_message") or "")
            if exception.get("exception_type") else None,
            "trajectory": folder / "trajectory.json",
        })
    return seats


def seat_activity(seat: dict, job: str, trial: str, threshold: float) -> dict:
    data = LOAD.read_json(seat["trajectory"])
    steps = [step for step in (data.get("steps") or [] if isinstance(data, dict) else []) if isinstance(step, dict)]
    stamps = (LOAD.parse_time(step.get("timestamp")) for step in steps)
    times = sorted(moment.timestamp() for moment in stamps if moment)
    gap = SS.first_gap(times, threshold) if times else None
    return {
        "job": job, "trial": trial, "seat": seat["seat"], "model": seat["model"],
        "first": times[0] if times else None, "last": times[-1] if times else None,
        "gap_start": gap[0] if gap else None, "gap_s": gap[1] if gap else None,
        "agent_steps": sum(1 for step in steps if step.get("source") == "agent"),
    }


def world_starts(directory: Path) -> int:
    events = LOAD.read_jsonl(directory / "artifacts" / "logs" / "world" / "events.jsonl", [])
    return sum(1 for event in events or [] if event.get("kind") == "start")


def finished_causes(directory: Path, result: dict, seats: list[dict], activity: list[dict],
                    cell: dict | None) -> tuple[list[tuple[str, str]], list[str]]:
    """Causes (empty when valid) and the normalized error messages of a finished trial."""
    exceptions = [(None, *(block(result, "exception_info").get(key) for key in ("exception_type",
                                                                               "exception_message")))]
    exceptions += [(seat["seat"], *seat["exception"]) for seat in seats if seat["exception"]]
    exceptions = [(owner, kind, message or "") for owner, kind, message in exceptions if kind]
    verdict = LOAD.read_json(directory / "verifier" / "score.json")
    verdict = verdict if isinstance(verdict, dict) else None
    state = {} if (directory / "verifier" / "world-state.json").is_file() else None
    task = ({"instance": cell["instance"], "arm": cell["arm"]} if cell
            else {"instance": (verdict or {}).get("instance"), "arm": (verdict or {}).get("condition")})
    problems = XM.harness_problems(result, verdict, state, task)
    reward, _unscored = LOAD._reward(result)
    log = WATCH.read_log_errors(directory / "trial.log")
    messages = {normalize(message or kind) for _owner, kind, message in exceptions}
    messages |= {normalize(info["line"]) for info in log.values()}
    found = [exception_cause(kind, message) for _owner, kind, message in exceptions]
    error = (verdict or {}).get("error")
    if world_starts(directory) > 1 or (error is not None and RESTART.search(str(error))):
        found.append(("world-restart", "more than one world start"))
    for row, seat in zip(activity, seats, strict=True):
        if not seat["scripted"] and not seat["started"] and not row["agent_steps"]:
            found.append(("seat-never-started", f"seat {seat['seat']}"))
    cell_ok = RW.trial_valid(directory, result, cell) if cell else True
    if not found and not problems and reward is not None and cell_ok:
        return [], sorted(messages)
    found += [("provider", provider_detail(kind, info["line"])) for kind, info in log.items() if kind != "error"]
    if error is not None and not RESTART.search(str(error)):
        found.append(("verifier", f"score error: {normalize(str(error))[:60]}"))
    for problem in problems:
        if problem in ("no verifier score", "no world state"):
            found.append(("verifier", problem))
        elif problem.startswith("score is for"):
            found.append(("other", "score for another task"))
    if reward is None:
        found.append(("missing-reward", "no usable reward"))
    if not cell_ok:
        found.append(("other", "run_wave cell check (seat models or instance)"))
    if not found:
        found.append(("other", "; ".join(problems) or "invalid by an unnamed rule"))
    return sorted(set(found), key=lambda item: (CAUSES.index(item[0]), item[1])), sorted(messages)


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def trial_row(directory: Path, job: dict, run: dict | None, threshold: float, now: float) -> tuple[dict, list]:
    result = LOAD.read_json(directory / "result.json")
    result = result if isinstance(result, dict) and "trial_name" in result else None
    config = LOAD.read_json(directory / "config.json")
    config = (result or {}).get("config") or (config if isinstance(config, dict) else {})
    seats = seat_list(directory, config, result)
    activity = [seat_activity(seat, job["name"], directory.name, threshold) for seat in seats]
    tested = [(seat, row) for seat, row in zip(seats, activity, strict=True) if not seat["scripted"]]
    stalls = [row for _seat, row in tested if row["gap_start"] is not None]
    row = {
        "path": f"{job['name']}/{directory.name}", "dir": str(directory), "job": job["name"],
        "wave": run["wave"] if run else job["wave"], "env": job["env"],
        "models": sorted({seat["model"] for seat, _row in tested}),
        "stalled": bool(stalls), "stall_gap_s": max((r["gap_s"] for r in stalls), default=None),
        "stall_start": min((r["gap_start"] for r in stalls), default=None),
        "cell": run["key"] if run else None, "attempt": run["attempt"] if run else None,
        "attempts": run["attempts"] if run else 1, "state_valid": run["state_valid"] if run else None,
        "charged_usd": run["charged_usd"] if run else None, "provider_errors": {}, "messages": [],
        "exceptions": [], "reward": None, "duration_s": None, "setup_s": None,
    }
    if result is None:
        return unfinished(row, directory, job, seats, activity, now), stall_rows(tested)
    costs = {seat["model"]: 0.0 for seat, _row in tested}
    for seat, _activity in tested:
        costs[seat["model"]] += seat["cost_usd"] or 0.0
    causes, messages = finished_causes(directory, result, seats, activity, run["cell"] if run else None)
    started = LOAD.parse_time(result.get("started_at"))
    finished = LOAD.parse_time(result.get("finished_at"))
    setups = [span_s(block(result, phase)) for phase in ("environment_setup", "agent_setup")]
    log = WATCH.read_log_errors(directory / "trial.log")
    row |= {
        "status": "invalid" if causes else "valid", "valid": not causes,
        "cause": causes[0][0] if causes else None, "causes": [list(item) for item in causes],
        "detail": causes[0][1] if causes else None, "messages": messages,
        "exceptions": [{"owner": seat["seat"], "model": seat["model"], "type": seat["exception"][0]}
                       for seat in seats if seat["exception"]],
        "provider_errors": {kind: info["count"] for kind, info in log.items() if kind != "error"},
        "reward": LOAD._reward(result)[0],
        "cost_usd": sum(seat["cost_usd"] or 0.0 for seat in seats), "model_costs": costs,
        "started": started.timestamp() if started else None,
        "duration_s": (finished - started).total_seconds() if started and finished else None,
        "setup_s": sum(s for s in setups if s is not None) if any(s is not None for s in setups) else None,
    }
    kind = block(result, "exception_info").get("exception_type")
    if kind:
        row["exceptions"].insert(0, {"owner": None, "model": None, "type": kind})
    return row, stall_rows(tested)


def stall_rows(tested: list[tuple[dict, dict]]) -> list[dict]:
    return [row for _seat, row in tested if row["first"] is not None]


def unfinished(row: dict, directory: Path, job: dict, seats: list[dict], activity: list[dict], now: float) -> dict:
    writes = [m for m in [mtime(directory / "trial.log"), mtime(directory / "config.json"),
                          *(mtime(seat["trajectory"]) for seat in seats)] if m is not None]
    abandoned = job["finished"] or not writes or now - max(writes) > WATCH.ABANDONED_SECONDS
    costs: dict[str, float] = {}
    for seat in seats:
        if not seat["scripted"]:
            spent = (WATCH.read_trajectory(seat["trajectory"]) or {}).get("cost_usd", 0.0)
            costs[seat["model"]] = costs.get(seat["model"], 0.0) + spent
    firsts = [r["first"] for r in activity if r["first"] is not None]
    return row | {
        "status": "abandoned" if abandoned else "running", "valid": False,
        "cause": "abandoned" if abandoned else None, "causes": [["abandoned", "no result.json"]] if abandoned else [],
        "detail": "no result.json" if abandoned else None, "cost_usd": sum(costs.values()), "model_costs": costs,
        "started": min(firsts) if firsts else (min(writes) if writes else None),
    }


def load_runs(runs: Path | None) -> dict[str, dict]:
    """Job name -> wave, cell, attempt number, attempts of the cell, run_wave's verdict and charge."""
    found: dict[str, dict] = {}
    for run in sorted(runs.iterdir()) if runs else []:
        state = LOAD.read_json(run / "state.json")
        if not isinstance(state, dict):
            continue
        plan = state.get("plan")
        wave = Path(plan).stem if isinstance(plan, str) and plan else run.name
        cells = {cell.get("key"): cell for cell in state.get("order") or [] if isinstance(cell, dict)}
        for key, entry in (state.get("finished") or {}).items():
            attempts = RW.attempts_of(entry)
            for number, attempt in enumerate(attempts, 1):
                assert attempt["job"] not in found, f"job {attempt['job']} is in two run states"
                found[attempt["job"]] = {
                    "wave": wave, "key": f"{wave}:{key}", "cell": cells.get(key), "attempt": number,
                    "attempts": len(attempts), "state_valid": attempt.get("valid"),
                    "charged_usd": attempt.get("cost_usd"),
                }
    return found


def job_dirs(jobs_dir: Path, patterns: list[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in patterns:
        for job in sorted(jobs_dir.glob(pattern)):
            if job.is_dir() and not job.is_symlink() and (job / "config.json").is_file():
                seen.setdefault(job)
    return list(seen)


def scan(jobs_dir: Path, patterns: list[str], runs: Path | None, threshold: float,
         now: float) -> tuple[list[dict], list[dict]]:
    assert threshold > 0, threshold
    by_job = load_runs(runs)
    trials, activity = [], []
    for job in job_dirs(jobs_dir, patterns):
        config = LOAD.read_json(job / "config.json")
        environment = block(config, "environment").get("type") or "unknown"
        found = JOB_SUFFIX.match(job.name)
        job_result = LOAD.read_json(job / "result.json")
        done = isinstance(job_result, dict) and bool(job_result.get("finished_at"))
        info = {"name": job.name, "wave": found["wave"] if found else job.name.split("-")[0],
                "env": environment, "finished": done}
        for directory in WATCH._trial_dirs(job):
            row, seats = trial_row(directory, info, by_job.get(job.name), threshold, now)
            trials.append(row)
            activity += seats
    return trials, activity


def nearest_rank(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summarize(items: list[tuple[dict, float]]) -> dict[str, Any]:
    trials = [trial for trial, _cost in items]
    durations = [t["duration_s"] for t in trials if t["duration_s"] is not None]
    setups = [t["setup_s"] for t in trials if t["setup_s"] is not None]
    valid = sum(1 for t in trials if t["valid"])
    return {
        "trials": len(trials), "valid": valid, "valid_share": valid / len(trials) if trials else None,
        "invalid": dict(Counter(t["cause"] for t in trials if not t["valid"]).most_common()),
        "stalled": sum(1 for t in trials if t["stalled"]),
        "retried_cells": len({t["cell"] for t in trials if t["cell"] and t["attempts"] > 1}),
        "provider_errors_recovered": sum(1 for t in trials if t["valid"] and t["provider_errors"]),
        "cost_usd": sum(cost for _t, cost in items),
        "invalid_cost_usd": sum(cost for t, cost in items if not t["valid"]),
        "duration_median_s": statistics.median(durations) if durations else None,
        "duration_p90_s": nearest_rank(durations, 0.9),
        "setup_median_s": statistics.median(setups) if setups else None, "setup_p90_s": nearest_rank(setups, 0.9),
    }


def provider_of(model: str) -> str:
    parts = model.split("/")
    return "/".join(parts[:2]) if parts[0] == "openrouter" and len(parts) > 2 else parts[0]


def grouped(trials: list[dict], keys_of) -> dict[str, dict]:
    buckets: dict[str, list[tuple[dict, float]]] = defaultdict(list)
    for trial in trials:
        for key, cost in keys_of(trial):
            buckets[key].append((trial, cost))
    return {key: summarize(items) for key, items in sorted(buckets.items())}


def by_wave(trial: dict) -> list[tuple[str, float]]:
    return [(trial["wave"], trial["cost_usd"])]


def by_model(trial: dict) -> list[tuple[str, float]]:
    return list(trial["model_costs"].items()) or [("no seat under test", trial["cost_usd"])]


def by_provider(trial: dict) -> list[tuple[str, float]]:
    costs: dict[str, float] = defaultdict(float)
    for model, cost in by_model(trial):
        costs[provider_of(model)] += cost
    return list(costs.items())


def bursts(trials: list[dict], gap: float) -> list[dict]:
    """Invalid trials whose starts are at most ``gap`` seconds apart, with every trial started in the window."""
    assert gap > 0, gap
    bad = sorted((t for t in trials if not t["valid"] and t["started"] is not None), key=lambda t: t["started"])
    groups: list[list[dict]] = []
    for trial in bad:
        if groups and trial["started"] - groups[-1][-1]["started"] <= gap:
            groups[-1].append(trial)
        else:
            groups.append([trial])
    found = []
    for group in groups:
        start, end = group[0]["started"], group[-1]["started"]
        started = sum(1 for t in trials if t["started"] is not None and start <= t["started"] <= end)
        found.append({
            "start": start, "end": end, "invalid": len(group), "started": started,
            "causes": dict(Counter(t["cause"] for t in group).most_common()),
            "waves": dict(Counter(t["wave"] for t in group).most_common()),
            "cost_usd": sum(t["cost_usd"] for t in group), "example": group[0]["path"],
        })
    return found


def wave_starts(trials: list[dict]) -> dict[str, dict]:
    """Per wave: invalid share among trials started in its first hour and after."""
    firsts: dict[str, float] = {}
    for trial in trials:
        if trial["started"] is not None:
            firsts[trial["wave"]] = min(firsts.get(trial["wave"], trial["started"]), trial["started"])
    found: dict[str, dict] = {}
    for wave, first in sorted(firsts.items()):
        rows = [t for t in trials if t["wave"] == wave and t["started"] is not None]
        early = [t for t in rows if t["started"] - first <= FIRST_HOUR_S]
        late = [t for t in rows if t["started"] - first > FIRST_HOUR_S]
        found[wave] = {"first_start": first, "first_hour": len(early),
                       "first_hour_invalid": sum(1 for t in early if not t["valid"]),
                       "later": len(late), "later_invalid": sum(1 for t in late if not t["valid"])}
    return found


def top_messages(trials: list[dict]) -> list[dict]:
    counts: Counter[str] = Counter()
    example: dict[str, str] = {}
    invalid: Counter[str] = Counter()
    for trial in trials:
        for message in trial["messages"]:
            counts[message] += 1
            invalid[message] += not trial["valid"]
            example.setdefault(message, trial["dir"])
    return [{"message": message, "trials": count, "invalid_trials": invalid[message], "example": example[message]}
            for message, count in counts.most_common(TOP_MESSAGES)]


def et(seconds: float | None) -> str:
    return "n/a" if seconds is None else datetime.fromtimestamp(seconds, ET).strftime("%Y-%m-%d %H:%M ET")


def cause_table(counted: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for cause in CAUSES:
        rows = [t for t in counted if not t["valid"] and t["cause"] == cause]
        if rows:
            found[cause] = {"trials": len(rows), "cost_usd": sum(t["cost_usd"] for t in rows),
                            "details": dict(Counter(t["detail"] for t in rows).most_common())}
    return found


def seat_exceptions(counted: list[dict]) -> dict[str, dict[str, int]]:
    found: dict[str, Counter] = defaultdict(Counter)
    for trial in counted:
        for exception in trial["exceptions"]:
            if exception["owner"] is not None:
                found[exception["model"]][exception["type"]] += 1
    return {model: dict(counts.most_common()) for model, counts in sorted(found.items())}


def stall_events(counted: list[dict], activity: list[dict], window: float) -> list[dict]:
    waves = {tuple(t["path"].split("/", 1)): t["wave"] for t in counted}
    events = SS.events_of(activity, window)
    stalled = sorted((row for row in activity if row["gap_start"] is not None), key=lambda row: row["gap_start"])
    for event in events:
        members = [row for row in stalled if event["start"] <= row["gap_start"] <= event["end"]]
        event["waves"] = dict(Counter(waves.get((row["job"], row["trial"]), "?") for row in members).most_common())
        event["models"] = {model: row for model, row in event["models"].items() if row["stalled"]}
    return events


def build(trials: list[dict], activity: list[dict], args: argparse.Namespace) -> dict[str, Any]:
    counted = [t for t in trials if t["status"] != "running"]
    charged = [t for t in counted if t["charged_usd"] is not None]
    payload = {
        "inputs": {"jobs": str(args.jobs), "globs": args.globs, "runs": str(args.runs) if args.runs else None,
                   "threshold_s": args.threshold, "window_s": args.window, "burst_gap_s": args.burst_gap},
        "totals": summarize([(t, t["cost_usd"]) for t in counted]) | {
            "abandoned": sum(1 for t in counted if t["status"] == "abandoned"),
            "running": len(trials) - len(counted),
            "trials_with_stall_data": len({(r["job"], r["trial"]) for r in activity}),
            "run_wave_trials": len(charged), "run_wave_charged_usd": sum(t["charged_usd"] for t in charged),
            "run_wave_charged_invalid_usd": sum(t["charged_usd"] for t in charged if not t["valid"]),
            "state_disagreements": [t["path"] for t in charged if t["state_valid"] is not t["valid"]],
            "invalid_with_reward": sum(1 for t in counted if not t["valid"] and t["reward"] is not None),
        },
        "causes": cause_table(counted),
        "groups": {
            "wave": grouped(counted, by_wave),
            "model": grouped(counted, by_model),
            "provider": grouped(counted, by_provider),
            "env": grouped(counted, lambda trial: [(trial["env"], trial["cost_usd"])]),
        },
        "seat_exceptions": seat_exceptions(counted),
        "messages": top_messages(counted),
        "bursts": [b for b in bursts(counted, args.burst_gap) if b["invalid"] >= MIN_BURST],
        "wave_starts": wave_starts(counted),
        "stall_events": stall_events(counted, activity, args.window),
        "trials": trials,
    }
    payload["answers"] = answers(payload, counted)
    return payload


def share(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def answers(payload: dict, counted: list[dict]) -> list[str]:
    lines = []
    models = [(model, row) for model, row in payload["groups"]["model"].items()
              if row["trials"] >= MIN_MODEL_TRIALS and row["trials"] > row["valid"]]
    models.sort(key=lambda item: (item[1]["valid_share"], -item[1]["trials"]))
    for model, row in models[:3]:
        details = Counter(t["detail"] for t in counted if not t["valid"] and model in t["models"])
        lines.append(f"{model}: {row['trials'] - row['valid']} of {row['trials']} trials invalid "
                     f"({share(row['trials'] - row['valid'], row['trials'])}); causes {row['invalid']}; "
                     f"top detail {details.most_common(1)[0][0] if details else 'n/a'}.")
    providers = sorted(payload["groups"]["provider"].items(), key=lambda item: item[1]["valid_share"])
    worst = [f"{name} {share(row['trials'] - row['valid'], row['trials'])} of {row['trials']}"
             for name, row in providers[:3] if row["trials"] > row["valid"]]
    lines.append("Providers by invalid share: " + ("; ".join(worst) or "none invalid") + ".")
    totals = payload["totals"]
    by_cause = ", ".join(f"{cause} ${row['cost_usd']:.2f}" for cause, row in payload["causes"].items())
    lines.append(f"Recorded spend ${totals['cost_usd']:.2f}; on invalid trials ${totals['invalid_cost_usd']:.2f} "
                 f"({share(totals['invalid_cost_usd'], totals['cost_usd'])}): {by_cause or 'none'}.")
    for env, row in payload["groups"]["env"].items():
        setup_failures = row["invalid"].get("environment", 0)
        lines.append(f"{env}: {row['trials']} trials, {setup_failures} environment setup failures "
                     f"({share(setup_failures, row['trials'])}); setup median {WATCH.duration(row['setup_median_s'])},"
                     f" p90 {WATCH.duration(row['setup_p90_s'])}.")
    invalid = sum(1 for t in counted if not t["valid"])
    bursty = sum(b["invalid"] for b in payload["bursts"])
    lines.append(f"{bursty} of {invalid} invalid trials ({share(bursty, invalid)}) fall in "
                 f"{len(payload['bursts'])} bursts of {MIN_BURST}+ (starts <= {payload['inputs']['burst_gap_s']:g} s "
                 "apart).")
    for burst in sorted(payload["bursts"], key=lambda b: -b["invalid"])[:3]:
        lines.append(f"Burst {et(burst['start'])} to {et(burst['end'])[11:]}: {burst['invalid']} of "
                     f"{burst['started']} started trials invalid; {burst['causes']}; waves {burst['waves']}.")
    for event in payload["stall_events"]:
        if event["seats"] >= MIN_BURST:
            lines.append(f"Stall event {et(event['start'])} to {et(event['end'])[11:]}: {event['seats']} seats in "
                         f"{event['trials']} trials; waves {event['waves']}; "
                         + ", ".join(f"{m} {r['stalled']}/{r['in_flight']}" for m, r in event["models"].items())
                         + " stalled/in flight.")
    return lines


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(row) + " |" for row in rows)]


def group_rows(groups: dict[str, dict]) -> list[list[str]]:
    rows = []
    for key, row in groups.items():
        causes = ", ".join(f"{cause} {n}" for cause, n in row["invalid"].items()) or "-"
        rows.append([
            f"`{key}`", str(row["trials"]), share(row["valid"], row["trials"]), causes,
            f"{row['stalled']} ({share(row['stalled'], row['trials'])})", str(row["retried_cells"]),
            str(row["provider_errors_recovered"]), f"${row['cost_usd']:.2f}", f"${row['invalid_cost_usd']:.2f}",
            f"{WATCH.duration(row['duration_median_s'])} / {WATCH.duration(row['duration_p90_s'])}",
            f"{WATCH.duration(row['setup_median_s'])} / {WATCH.duration(row['setup_p90_s'])}",
        ])
    return rows


GROUP_HEADER = ["group", "trials", "valid", "invalid by cause", "stalled", "retried cells",
                "provider errors recovered", "cost", "invalid cost", "duration med / p90", "setup med / p90"]


def render(payload: dict) -> str:
    totals, inputs = payload["totals"], payload["inputs"]
    invalid = totals["trials"] - totals["valid"]
    lines = [
        "# Flotilla reliability ledger", "",
        (f"Jobs `{inputs['jobs']}` globs {' '.join(f'`{g}`' for g in inputs['globs'])}; runs `{inputs['runs']}`; "
         f"stall threshold {inputs['threshold_s']:g} s."), "",
        (f"- Trials: {totals['trials']} counted ({totals['running']} running left out); valid {totals['valid']} "
         f"({share(totals['valid'], totals['trials'])}); invalid {invalid}, of them abandoned {totals['abandoned']}."),
        (f"- Stalled trials (a seat gap > {inputs['threshold_s']:g} s): {totals['stalled']} "
         f"({share(totals['stalled'], totals['trials'])})."),
        (f"- Recorded cost ${totals['cost_usd']:.2f}; on invalid trials ${totals['invalid_cost_usd']:.2f} "
         f"({share(totals['invalid_cost_usd'], totals['cost_usd'])})."),
        (f"- run_wave trials {totals['run_wave_trials']}: charged ${totals['run_wave_charged_usd']:.2f}, on invalid "
         f"${totals['run_wave_charged_invalid_usd']:.2f}; state.json verdicts that disagree with this ledger: "
         f"{len(totals['state_disagreements'])}."),
        f"- Trials whose trial.log shows provider errors but which ended valid: {totals['provider_errors_recovered']}.",
        ("- Invalid trials that still have a verifier reward (left out by the lab's rules): "
         f"{totals['invalid_with_reward']}."),
        "", "## Answers", "", *(f"- {line}" for line in payload["answers"]),
        "", "## Invalid trials by cause", "",
        *table(["cause", "trials", "cost", "details"],
               [[cause, str(row["trials"]), f"${row['cost_usd']:.2f}",
                 "; ".join(f"{detail} {n}" for detail, n in row["details"].items())]
                for cause, row in payload["causes"].items()]),
    ]
    for name, title in (("wave", "wave"), ("model", "model (a trial counts once per model it seats)"),
                        ("provider", "provider"), ("env", "environment")):
        lines += ["", f"## Per {title}", "", *table(GROUP_HEADER, group_rows(payload["groups"][name]))]
    lines += ["", "## Seat exceptions by model", "",
              *table(["model", "seat exceptions"], [[f"`{model}`", ", ".join(f"{k} {n}" for k, n in row.items())]
                                                    for model, row in payload["seat_exceptions"].items()])]
    lines += ["", f"## Top {TOP_MESSAGES} error messages (normalized; trials that show each)", "",
              *table(["trials", "invalid", "message", "example"],
                     [[str(m["trials"]), str(m["invalid_trials"]), "`" + m["message"].replace("|", "/") + "`",
                       f"`{m['example']}`"] for m in payload["messages"]])]
    return "\n".join(lines + render_time(payload)) + "\n"


def render_time(payload: dict) -> list[str]:
    gap = payload["inputs"]["burst_gap_s"]
    lines = ["", f"## Invalid bursts ({MIN_BURST}+ invalid trials, starts <= {gap:g} s apart)", ""]
    lines += table(["start", "end", "invalid / started", "causes", "waves", "cost", "example"],
                   [[et(b["start"]), et(b["end"]), f"{b['invalid']} / {b['started']}",
                     ", ".join(f"{k} {n}" for k, n in b["causes"].items()),
                     ", ".join(f"{k} {n}" for k, n in b["waves"].items()), f"${b['cost_usd']:.2f}", f"`{b['example']}`"]
                    for b in payload["bursts"]])
    lines += ["", "## Wave starts (invalid in the first hour of a wave vs later)", ""]
    lines += table(["wave", "first start", "first hour invalid", "later invalid"],
                   [[w, et(r["first_start"]), f"{r['first_hour_invalid']} / {r['first_hour']}",
                     f"{r['later_invalid']} / {r['later']}"] for w, r in payload["wave_starts"].items()
                    if r["first_hour_invalid"] or r["later_invalid"]])
    lines += ["", f"## Stall events (gap starts <= {payload['inputs']['window_s']:g} s apart)", ""]
    lines += table(["start", "end", "seats", "trials", "gap s", "waves", "models stalled / in flight"],
                   [[et(e["start"]), et(e["end"]), str(e["seats"]), str(e["trials"]),
                     f"{e['gap_min_s']:.0f}-{e['gap_max_s']:.0f}", ", ".join(f"{k} {n}" for k, n in e["waves"].items()),
                     ", ".join(f"{m} {r['stalled']}/{r['in_flight']}" for m, r in e["models"].items())]
                    for e in payload["stall_events"]])
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify every Flotilla trial and cost its failures.")
    parser.add_argument("jobs", type=Path)
    parser.add_argument("globs", nargs="*", default=["*"])
    parser.add_argument("--runs", type=Path, help="run_wave run folders (each child holds a state.json)")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--threshold", type=float, default=500.0)
    parser.add_argument("--window", type=float, default=120.0)
    parser.add_argument("--burst-gap", type=float, default=600.0)
    args = parser.parse_args(argv)
    args.globs = args.globs or ["*"]
    trials, activity = scan(args.jobs, args.globs, args.runs, args.threshold, time.time())
    payload = build(trials, activity, args)
    print(render(payload), end="")
    if args.json:
        args.json.write_text(json.dumps(payload, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
