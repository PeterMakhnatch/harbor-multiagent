"""Live monitor for a running Flotilla wave or any Harbor jobs folder: ``flotilla-watch TARGET``.

TARGET is a ``run_wave.py`` run folder (``state.json``, ``progress.log``, ``configs/<job>.json``), a jobs
folder, or one job folder. Every pass reads the files below, prints the wave's progress, spend, running
trials with per-seat activity, stalls, errors and a reward summary of finished trials, and optionally
writes the same status as JSON (``--json``), an HTML dashboard with a timeline page per finished trial
(``--html``, see ``dashboard.py``) and sends one macOS notification per new alert (``--notify``).
Inputs are only read. Standard library only.

What the host can see while a trial runs (fork Harbor, read 2026-11-24; Docker and Modal alike):

- ``seats/<seat>/agent/trajectory.json``: terminus-2 runs on the host and rewrites this file in place (not
  atomically, so a read can catch it half written) after every episode, i.e. one model call plus its
  commands (``terminus_2.py`` ``_run_agent_loop`` -> ``_dump_trajectory``), and once more when the seat's
  run ends (the ``finally`` of ``run``). Only that last write carries
  ``final_metrics.total_prompt_tokens``: the agent context totals stay None until the run ends and ATIF
  drops None fields. Step ``metrics.cost_usd`` gives the seat's spend so far. Nothing is written before
  the first episode ends. A single-agent trial writes ``agent/trajectory.json`` the same way.
- ``trial.log``: the trial's file logger, open from the start (``trial.py`` ``_init_logger``). Seat loggers
  are its children, so model errors appear live: ``Unknown Error in LLM interaction: litellm.<Error>``
  (``terminus_2.py`` ``_query_llm``, once per failed retry round; LiteLLM's own three attempts are
  silent). Lines carry no time stamps.
- ``config.json`` of the trial is written at trial start (``seats.py`` ``_init_result``): participants,
  task path. The trial's ``result.json`` is written once, after verification (``trial.py`` ``_finalize``).
- The job's ``result.json`` is rewritten when a trial starts or ends (``job.py`` ``_on_trial_started``,
  ``_on_trial_completed``); ``finished_at`` is set when the job ends. Its times are naive local times.
- Not live: ``recording.cast`` and ``terminus_2.pane`` live in the seat container's ``/logs/agent`` and
  are downloaded when play ends (``tmux_session.py`` stop, ``seats.py`` ``_collect_seat``). The world's
  ``/logs/world`` (``events.jsonl``, ``messages.jsonl``) stays in the world container: Docker binds only
  main's ``/logs/agent``, ``/logs/verifier`` and ``/logs/artifacts`` (``environments/base.py``), Modal
  binds nothing, so the world record reaches the host with the artifacts after play.
- ``run_wave.py`` appends ``start <job> set ...`` and ``end <job> set ...`` lines (ET) to
  ``progress.log`` and rewrites ``state.json`` (``spent_usd``, finished cells) after every end.

Seat activity is the modification time of the seat's trajectory: the only per-seat signal that changes
during play. Its age includes command time (a command may wait up to 180 s, a world ``/wait`` 60 s), so the
default stall threshold is 300 s. A seat under test stalls when the trial has no ``result.json``, the seat
has not ended, and its trajectory (or, before its first episode, the trial start) is older than the
threshold. Scripted seats (Harbor's ``oracle`` agent, or ``under_test = false`` in ``task.toml``) write no
trajectory and never stall.

``--blind`` is for pre-registered waves that forbid interim looks at outcomes: every surface (text,
``--json``, ``--html``) keeps progress, validity, spend, stalls and errors, and drops the reward of each
trial, the reward summary and the reason a trial is unscored (it can quote the reward); no timeline page is
written (it shows rewards, ``score.json`` and answers).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from flotilla_analysis.load import parse_time, parse_trial, read_json
from flotilla_analysis.names import Names, read_task_toml

ABANDONED_SECONDS = 3 * 3600  # no write for this long: longer than any agent timeout plus verification
NOTIFY_PER_PASS = 5
SHOWN_ERRORS = 10
BLIND_UNSCORED = "reason hidden (blind)"
PROGRESS = re.compile(r"^(?P<at>\S+ \S+) ET (?P<kind>start|end) (?P<job>[A-Za-z0-9._-]+) set ")
STOP = re.compile(r"^\S+ \S+ ET stop (?P<reason>[A-Za-z-]+): spent ")
RESUME = re.compile(r"^\S+ \S+ ET (resume|order|start spacing)\b")
PROVIDER_LINE = re.compile(r"Unknown Error in LLM interaction|litellm\.[A-Za-z]*Error|\bTrial \S+ failed:")
ERROR_KINDS = (
    ("rate-limit", re.compile(r"RateLimitError|\b429\b|Too Many Requests|rate.limit", re.IGNORECASE)),
    ("timeout", re.compile(r"Timeout|timed out", re.IGNORECASE)),
    (
        "server",
        re.compile(r"InternalServerError|ServiceUnavailable|BadGateway|APIConnectionError|\b50[0-4]\b"),
    ),
)
LITELLM_ERROR = re.compile(r"litellm\.([A-Za-z]*Error)")

Notifier = Callable[[str, str], None]


def notify(title: str, message: str) -> None:
    """One macOS notification (the same call as ``~/.local/bin/daily-report``); silent elsewhere."""
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run",
                title,
                message,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _mtime(path: Path) -> float | None:
    stamp = _stamp(path)
    return stamp[0] / 1e9 if stamp else None


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def error_kind(line: str) -> str:
    """rate-limit, timeout, server, the LiteLLM error class, or ``error``."""
    for kind, pattern in ERROR_KINDS:
        if pattern.search(line):
            return kind
    found = LITELLM_ERROR.search(line)
    return found.group(1) if found else "error"


def read_log_errors(path: Path) -> dict[str, dict[str, Any]]:
    """Provider and trial errors in a Harbor log, per kind: count and the last such line."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t")) or line.startswith("Sending keys") or not PROVIDER_LINE.search(line):
            continue
        row = found.setdefault(error_kind(line), {"count": 0, "line": ""})
        row["count"] += 1
        row["line"] = line.strip()[:300]
    return found


def read_trajectory(path: Path) -> dict[str, Any] | None:
    """Agent steps, the last step time, spend so far and whether the run ended; None when unreadable."""
    data = read_json(path)
    if not isinstance(data, dict):
        return None
    steps = [s for s in data.get("steps") or [] if isinstance(s, dict)]
    agent = [s for s in steps if s.get("source") == "agent"]
    costs = [
        _money((s.get("metrics") or {}).get("cost_usd")) for s in agent if isinstance(s.get("metrics"), dict)
    ]
    final = data.get("final_metrics") if isinstance(data.get("final_metrics"), dict) else {}
    prompt = final.get("total_prompt_tokens")
    return {
        "steps": len(agent),
        "last_step": steps[-1].get("timestamp") if steps else None,
        "cost_usd": sum(c for c in costs if c is not None),
        "ended": isinstance(prompt, int) and not isinstance(prompt, bool),
    }


def find_plan(run_dir: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    """The plan of a run folder at ``<repo>/.flotilla-local/runs/<plan>``, that is
    ``<repo>/flotilla/plans/<plan>.json``, used only when its sha256 is the one ``state.json`` recorded."""
    name, digest = state.get("plan"), state.get("plan_sha256")
    if not isinstance(name, str) or not isinstance(digest, str) or len(run_dir.parents) < 3:
        return None
    path = run_dir.parents[2] / "flotilla" / "plans" / f"{name}.json"
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != digest:
        return None
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return plan if isinstance(plan, dict) else None


def read_progress(path: Path) -> dict[str, Any]:
    """Jobs started (with their ET start), jobs ended, and the stop reason when the wave last stopped."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    started: dict[str, str] = {}
    ended: set[str] = set()
    stopped: str | None = None
    for line in lines:
        if found := PROGRESS.match(line):
            stopped = None
            if found["kind"] == "start":
                started[found["job"]] = found["at"]
            else:
                ended.add(found["job"])
        elif found := STOP.match(line):
            stopped = found["reason"]
        elif RESUME.match(line):
            stopped = None
    return {"started": started, "ended": ended, "stopped": stopped}


def et_seconds(text: str) -> float | None:
    """A ``progress.log`` stamp (local wall time of the run) as epoch seconds."""
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def is_run_folder(path: Path) -> bool:
    return (path / "state.json").is_file() or (path / "progress.log").is_file()


def _is_job(path: Path) -> bool:
    config = read_json(path / "config.json")
    return isinstance(config, dict) and "job_name" in config


def _trial_dirs(job: Path) -> list[Path]:
    try:
        children = sorted(entry for entry in job.iterdir() if entry.is_dir() and not entry.is_symlink())
    except OSError:
        return []
    return [
        child
        for child in children
        if any((child / name).is_file() for name in ("result.json", "config.json", "trial.log"))
    ]


class Watcher:
    """Reads a target once per ``poll``; caches parsed files until they change and remembers alerts sent."""

    def __init__(
        self,
        target: Path,
        *,
        stall_seconds: float = 300.0,
        cap_usd: float | None = None,
        notifier: Notifier | None = None,
        blind: bool = False,
    ) -> None:
        self.target = target.resolve()
        self.stall_seconds = stall_seconds
        self.cap_usd = cap_usd
        self.notifier = notifier
        self.blind = blind
        self.names = Names()
        self.seen: dict[str, float] = {}
        self.passes = 0
        self._cache: dict[tuple[str, str], tuple[tuple[int, int], Any]] = {}
        self._tasks: dict[str, tuple[str | None, str | None, dict[str, bool]]] = {}

    def _cached(self, kind: str, path: Path, read: Callable[[Path], Any]) -> Any:
        """The parsed file, re-read only when its mtime or size changed. A file caught half written keeps
        the last good value and is read again next pass."""
        stamp = _stamp(path)
        if stamp is None:
            return None
        key = (kind, str(path))
        hit = self._cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        value = read(path)
        if value is None:
            return hit[1] if hit is not None else None
        self._cache[key] = (stamp, value)
        return value

    def poll(self, now: float | None = None) -> dict[str, Any]:
        """One pass: the status, its alerts, and notifications for alerts not seen before. On the first pass
        alerts of trials that already finished are history: shown, not notified."""
        now = time.time() if now is None else now
        status = self.status(now)
        alerts = alerts_of(status)
        new = [a for a in alerts if a["key"] not in self.seen]
        notified = [a for a in new if a["live"] or self.passes > 0]
        if self.notifier is not None and notified:
            for alert in notified[:NOTIFY_PER_PASS]:
                self.notifier(alert["title"], alert["message"])
            if len(notified) > NOTIFY_PER_PASS:
                self.notifier(
                    "flotilla-watch", f"{len(notified) - NOTIFY_PER_PASS} more new alerts; see the monitor"
                )
        for alert in new:
            self.seen[alert["key"]] = now
        for alert in alerts:
            alert["first_seen"] = self.seen[alert["key"]]
        self.passes += 1
        status["alerts"] = alerts
        status["new_alerts"] = [a["key"] for a in notified]
        return status

    def status(self, now: float) -> dict[str, Any]:
        if is_run_folder(self.target):
            return self._run_status(now)
        jobs = [self.target] if _is_job(self.target) else _child_dirs(self.target)
        trials, running = self._scan(jobs, now)
        return self._assemble(now, "jobs", trials, running, wave=self._jobs_wave(jobs, trials, running, now))

    def _run_status(self, now: float) -> dict[str, Any]:
        state = read_json(self.target / "state.json")
        state = state if isinstance(state, dict) else {}
        progress = read_progress(self.target / "progress.log")
        jobs_dir = self._jobs_dir_of_run()
        finished_jobs = {
            attempt.get("job")
            for cell in (state.get("finished") or {}).values()
            if isinstance(cell, dict)
            for attempt in cell.get("attempts") or []
            if isinstance(attempt, dict)
        }
        names = sorted({*progress["started"], *(j for j in finished_jobs if isinstance(j, str))})
        live = [j for j in progress["started"] if j not in progress["ended"] and j not in finished_jobs]
        live = [] if progress["stopped"] else live
        trials, running = self._scan([jobs_dir / name for name in names], now, live_jobs=set(live))
        with_trial = {t["job"] for t in running}
        for job in live:
            if job not in with_trial:
                started = et_seconds(progress["started"][job])
                running.append(
                    {
                        "job": job,
                        "trial": None,
                        "state": "starting",
                        "path": f"{jobs_dir.name}/{job}",
                        "label": None,
                        "condition": None,
                        "seats": [],
                        "errors": {},
                        "cost_usd": 0.0,
                        "elapsed_s": None if started is None else max(0.0, now - started),
                    }
                )
        cells = state.get("finished") or {}
        valid = sum(1 for c in cells.values() if isinstance(c, dict) and c.get("valid"))
        n_running = sum(1 for r in running if r["state"] in ("running", "starting"))
        total = len(state.get("order") or [])
        plan = find_plan(self.target, state)
        budget = (plan or {}).get("budget") or {}
        wave = {
            "kind": "run",
            "plan": state.get("plan"),
            "state": f"stopped ({progress['stopped']})" if progress["stopped"] else "running",
            "units": "cells",
            "total": total,
            "done": len(cells),
            "valid": valid,
            "invalid": len(cells) - valid,
            "running": n_running,
            "pending": max(0, total - len(cells) - n_running),
            "abandoned": sum(1 for r in running if r["state"] == "abandoned"),
            "spent_usd": _money(state.get("spent_usd")),
            "cap_usd": self.cap_usd if self.cap_usd is not None else _money(budget.get("cap_usd")),
            "concurrency": ((plan or {}).get("make_jobs") or {}).get("concurrency"),
        }
        return self._assemble(now, "run", trials, running, wave=wave)

    def _jobs_dir_of_run(self) -> Path:
        try:
            configs = sorted((self.target / "configs").glob("*.json"))
        except OSError:
            configs = []
        for path in configs[:1]:
            config = read_json(path)
            if isinstance(config, dict) and isinstance(config.get("jobs_dir"), str):
                return Path(config["jobs_dir"])
        return self.target.parent.parent / "jobs"

    def _jobs_wave(
        self, jobs: list[Path], trials: list[dict], running: list[dict], now: float
    ) -> dict[str, Any]:
        """Pending trials are those a live job has not started; a job that wrote nothing for
        ``ABANDONED_SECONDS`` without finishing was killed, and its unstarted trials never run."""
        pending = 0
        for job in jobs:
            result = self._cached("json", job / "result.json", read_json)
            if not isinstance(result, dict) or result.get("finished_at"):
                continue
            writes = [m for m in (_mtime(job / "result.json"), _mtime(job / "job.log")) if m is not None]
            if not writes or now - max(writes) > ABANDONED_SECONDS:
                continue
            total = result.get("n_total_trials")
            if isinstance(total, int) and not isinstance(total, bool):
                pending += max(0, total - len(_trial_dirs(job)))
        valid = sum(1 for t in trials if t["valid"])
        costs = [t["cost_usd"] for t in trials if t["cost_usd"] is not None]
        return {
            "kind": "jobs",
            "plan": None,
            "state": "running" if any(r["state"] == "running" for r in running) or pending else "idle",
            "units": "trials",
            "total": len(trials) + sum(1 for r in running if r["state"] == "running") + pending,
            "done": len(trials),
            "valid": valid,
            "invalid": len(trials) - valid,
            "running": sum(1 for r in running if r["state"] == "running"),
            "pending": pending,
            "abandoned": sum(1 for r in running if r["state"] == "abandoned"),
            "spent_usd": sum(costs) if costs else None,
            "cap_usd": self.cap_usd,
            "concurrency": None,
        }

    def _scan(
        self, jobs: list[Path], now: float, *, live_jobs: set[str] | None = None
    ) -> tuple[list[dict], list[dict]]:
        trials: list[dict] = []
        running: list[dict] = []
        for job in jobs:
            job_result = self._cached("json", job / "result.json", read_json)
            job_done = isinstance(job_result, dict) and bool(job_result.get("finished_at"))
            if live_jobs is not None:
                job_done = job_done or job.name not in live_jobs
            for directory in _trial_dirs(job):
                summary = self._cached("result", directory / "result.json", self._finished)
                if summary is not None:
                    trials.append(summary)
                    continue
                running.append(self._running(directory, now, abandoned=job_done))
        return trials, running

    def _relative(self, directory: Path) -> str:
        return f"{directory.parent.name}/{directory.name}"

    def _finished(self, path: Path) -> dict[str, Any] | None:
        data = read_json(path)
        if not isinstance(data, dict) or "trial_name" not in data or "task_name" not in data:
            return None
        directory = path.parent
        relative = self._relative(directory)
        try:
            trial = parse_trial(data, directory=directory, relative=relative, names=self.names)
        except (KeyError, TypeError, ValueError):
            trial = None
        started = parse_time(data.get("started_at"))
        finished = parse_time(data.get("finished_at"))
        if trial is not None:
            label, condition, team = trial.label, trial.condition, trial.team
            reward, unscored, exceptions = trial.reward, trial.unscored, trial.exceptions
            costs = [s.cost_usd for s in trial.seats]
        else:
            label, condition, team, reward, unscored, exceptions, costs = _other_trial(data)
        known = [c for c in costs if c is not None]
        errors = self._cached("log", directory / "trial.log", read_log_errors) or {}
        summary = {
            "path": relative,
            "directory": str(directory),
            "job": directory.parent.name,
            "trial": directory.name,
            "label": label,
            "condition": condition,
            "team": team,
            "reward": reward,
            "unscored": unscored,
            "exceptions": exceptions,
            "provider_errors": errors,
            "valid": finished is not None and reward is not None and not exceptions,
            "cost_usd": sum(known) if known else None,
            "started_at": data.get("started_at"),
            "finished_at": data.get("finished_at"),
            "duration_s": (finished - started).total_seconds() if started and finished else None,
        }
        if self.blind:
            del summary["reward"]
            summary["unscored"] = BLIND_UNSCORED if unscored else None
        return summary

    def _task(self, task_path: str | None) -> tuple[str | None, str | None, dict[str, bool]]:
        """Label, condition and per-seat under_test of a task folder (cached for the watch)."""
        key = task_path or ""
        if key not in self._tasks:
            label = condition = None
            under_test: dict[str, bool] = {}
            if task_path:
                known = self.names.identity("flotilla/" + Path(task_path).name, task_path)
                if known is not None:
                    label, condition = known.label, known.condition
                agents = read_task_toml(Path(task_path)).get("agents")
                for seat, row in (agents if isinstance(agents, dict) else {}).items():
                    if isinstance(row, dict):
                        under_test[str(seat)] = row.get("under_test") is not False
            self._tasks[key] = (label, condition, under_test)
        return self._tasks[key]

    def _running(self, directory: Path, now: float, *, abandoned: bool) -> dict[str, Any]:
        config = self._cached("json", directory / "config.json", read_json)
        config = config if isinstance(config, dict) else {}
        task = config.get("task") if isinstance(config.get("task"), dict) else {}
        task_path = task.get("path") if isinstance(task.get("path"), str) else None
        label, condition, under_test = self._task(task_path)
        start = _mtime(directory / "config.json") or _mtime(directory / "trial.log") or now
        seats = _participants(config, directory)
        writes = [m for m in (_mtime(directory / "trial.log"), start) if m is not None]
        rows = []
        for seat in seats:
            scripted = seat["agent"] == "oracle" or not under_test.get(seat["seat"], True)
            trajectory = self._cached("trajectory", seat["path"], read_trajectory)
            written = _mtime(seat["path"])
            if written is not None:
                writes.append(written)
            rows.append({**seat, "scripted": scripted, "trajectory": trajectory, "written": written})
        last_write = max(writes)
        state = "abandoned" if abandoned or now - last_write > ABANDONED_SECONDS else "running"
        out_seats = []
        for row in rows:
            trajectory = row["trajectory"] or {}
            activity = row["written"] if row["written"] is not None else start
            ended = bool(trajectory.get("ended"))
            age = max(0.0, now - activity)
            out_seats.append(
                {
                    "seat": row["seat"],
                    "agent": row["agent"],
                    "model": row["model"],
                    "scripted": row["scripted"],
                    "steps": trajectory.get("steps", 0),
                    "ended": ended,
                    "last_activity": activity,
                    "age_s": age,
                    "first_step": row["written"] is not None,
                    "cost_usd": trajectory.get("cost_usd", 0.0),
                    "stalled": state == "running"
                    and not row["scripted"]
                    and not ended
                    and age > self.stall_seconds,
                }
            )
        return {
            "path": self._relative(directory),
            "job": directory.parent.name,
            "trial": directory.name,
            "state": state,
            "label": label,
            "condition": condition,
            "elapsed_s": max(0.0, now - start),
            "seats": out_seats,
            "errors": self._cached("log", directory / "trial.log", read_log_errors) or {},
            "cost_usd": sum(s["cost_usd"] for s in out_seats),
        }

    def _assemble(
        self, now: float, mode: str, trials: list[dict], running: list[dict], *, wave: dict[str, Any]
    ) -> dict[str, Any]:
        durations = [t["duration_s"] for t in trials if t["duration_s"] is not None]
        mean = sum(durations) / len(durations) if durations else None
        live = [r for r in running if r["state"] in ("running", "starting")]
        wave["in_flight_usd"] = sum(r["cost_usd"] for r in live)
        wave["mean_trial_s"] = mean
        wave["eta_s"] = eta(wave, live, mean)
        status = {
            "generated_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
            "now": now,
            "target": str(self.target),
            "mode": mode,
            "blind": self.blind,
            "stall_seconds": self.stall_seconds,
            "wave": wave,
            "running": sorted(running, key=lambda r: (r["state"] != "running", r["path"])),
            "errors": [
                {
                    "path": t["path"],
                    "finished_at": t["finished_at"],
                    "exceptions": t["exceptions"],
                    "provider_errors": t["provider_errors"],
                    "unscored": t["unscored"],
                }
                for t in sorted(trials, key=lambda t: (t["finished_at"] or "", t["path"]))
                if t["exceptions"] or t["provider_errors"]
            ],
            "finished": sorted(trials, key=lambda t: (t["finished_at"] or "", t["path"]), reverse=True),
        }
        if not self.blind:
            status["rewards"] = reward_summary(trials)
        return status


def _child_dirs(target: Path) -> list[Path]:
    """The job folders of a jobs folder: child folders, sorted, links not followed."""
    try:
        return sorted(e for e in target.iterdir() if e.is_dir() and not e.is_symlink())
    except OSError:
        return []


def _participants(config: dict[str, Any], directory: Path) -> list[dict[str, Any]]:
    """Seats of a running trial from its config: multi-seat participants, else the single agent."""
    rows = []
    for item in config.get("participants") or []:
        if isinstance(item, dict) and isinstance(item.get("seat"), str):
            rows.append(
                {
                    "seat": item["seat"],
                    "agent": item.get("name"),
                    "model": item.get("model_name"),
                    "path": directory / "seats" / item["seat"] / "agent" / "trajectory.json",
                }
            )
    if rows:
        return rows
    agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    return [
        {
            "seat": "agent",
            "agent": agent.get("name"),
            "model": agent.get("model_name"),
            "path": directory / "agent" / "trajectory.json",
        }
    ]


def _other_trial(data: dict[str, Any]) -> tuple:
    """Label, condition, team, reward, unscored reason, exceptions and seat costs of a non-Flotilla trial."""
    seats = [s for s in data.get("seat_results") or [] if isinstance(s, dict)]
    exceptions = []
    for owner, block in [(None, data), *((s.get("seat"), s) for s in seats)]:
        info = block.get("exception_info")
        kind = info.get("exception_type") if isinstance(info, dict) else None
        if kind:
            exceptions.append(f"{owner}:{kind}" if owner else str(kind))
    rewards = (
        (data.get("verifier_result") or {}).get("rewards")
        if isinstance(data.get("verifier_result"), dict)
        else None
    )
    value = rewards.get("reward") if isinstance(rewards, dict) else None
    reward = _money(value) if data.get("finished_at") and data.get("reward_valid") is not False else None
    costs = [
        _money((s.get("agent_result") or {}).get("cost_usd"))
        for s in seats
        if isinstance(s.get("agent_result"), dict)
    ]
    if not seats and isinstance(data.get("agent_result"), dict):
        costs = [_money(data["agent_result"].get("cost_usd"))]
    task = str(data.get("task_name") or "?")
    label = "flotilla (task unresolved)" if task.startswith("flotilla/") else task
    return label, "-", "-", reward, None if reward is not None else "no usable reward", exceptions, costs


def eta(wave: dict[str, Any], live: list[dict], mean: float | None) -> float | None:
    """Seconds left: the pending cells at the mean trial time plus what the running ones have left, over the
    concurrency (the plan's, else the number running)."""
    if mean is None or wave["state"].startswith("stopped") or not (wave["pending"] or live):
        return None
    left = wave["pending"] * mean + sum(max(0.0, mean - (r["elapsed_s"] or 0.0)) for r in live)
    if left == 0:
        return 0.0
    concurrency = wave.get("concurrency")
    lanes = concurrency if isinstance(concurrency, int) and concurrency > 0 else max(1, len(live))
    return left / lanes


def reward_summary(trials: list[dict]) -> list[dict[str, Any]]:
    """Finished trials per label, condition and team: trials, scored, valid and the mean reward."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for trial in trials:
        key = (str(trial["label"]), str(trial["condition"]), str(trial["team"]))
        groups.setdefault(key, []).append(trial)
    rows = []
    for (label, condition, team), members in sorted(groups.items()):
        rewards = [t["reward"] for t in members if t["reward"] is not None]
        rows.append(
            {
                "label": label,
                "condition": condition,
                "team": team,
                "trials": len(members),
                "scored": len(rewards),
                "valid": sum(1 for t in members if t["valid"]),
                "mean_reward": sum(rewards) / len(rewards) if rewards else None,
            }
        )
    return rows


def alerts_of(status: dict[str, Any]) -> list[dict[str, Any]]:
    """Stalls and errors. A key names one event: a stall by its seat and last activity (a seat that moves
    and stalls again is a new alert), an error by its trial and kind."""
    alerts = []
    for trial in status["running"]:
        if trial["state"] != "running":
            continue
        name = trial["path"]
        for seat in trial["seats"]:
            if seat["stalled"]:
                what = "no step" if not seat["first_step"] else "no new step"
                alerts.append(
                    {
                        "key": f"stall:{name}:{seat['seat']}:{seat['last_activity']:.3f}",
                        "kind": "stall",
                        "live": True,
                        "title": f"Flotilla stall: {trial['job']}",
                        "message": f"{seat['seat']} ({seat['model']}) {what} for {duration(seat['age_s'])} "
                        f"in {trial['trial']}",
                    }
                )
        for kind, row in sorted(trial["errors"].items()):
            alerts.append(
                {
                    "key": f"error:{name}:{kind}",
                    "kind": "error",
                    "live": True,
                    "title": f"Flotilla {kind}: {trial['job']}",
                    "message": f"{row['count']}x in {trial['trial']}: {row['line'][:160]}",
                }
            )
    for error in status["errors"]:
        if error["exceptions"]:
            alerts.append(
                {
                    "key": f"exception:{error['path']}",
                    "kind": "error",
                    "live": False,
                    "title": "Flotilla trial error",
                    "message": f"{error['path']}: {', '.join(error['exceptions'])}",
                }
            )
    return alerts


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def render(status: dict[str, Any]) -> str:
    wave = status["wave"]
    blind = " BLIND" if status.get("blind") else ""
    head = f"flotilla-watch{blind} {status['generated_at']}  {status['mode']} {status['target']}"
    lines = [head + (f"  plan {wave['plan']}" if wave["plan"] else "") + f"  [{wave['state']}]"]
    lines.append(
        f"{wave['units']} {wave['total']}: done {wave['done']} "
        f"(valid {wave['valid']}, invalid {wave['invalid']}), "
        f"running {wave['running']}, pending {wave['pending']}"
        + (f", abandoned {wave['abandoned']}" if wave["abandoned"] else "")
    )
    cap = wave["cap_usd"]
    share = f" ({100 * wave['spent_usd'] / cap:.0f}%)" if cap and wave["spent_usd"] is not None else ""
    lines.append(
        f"spend {_usd(wave['spent_usd'])} of cap {_usd(cap) if cap is not None else 'n/a'}{share}, "
        f"in flight ~{_usd(wave['in_flight_usd'])}; mean trial {duration(wave['mean_trial_s'])}, "
        f"ETA {duration(wave['eta_s'])}"
    )
    lines.extend(_render_running(status))
    alerts = status.get("alerts") or []
    stalls = sum(1 for a in alerts if a["kind"] == "stall")
    lines.append(
        f"alerts: {stalls} stall, {len(alerts) - stalls} error ({len(status.get('new_alerts') or [])} new)"
    )
    errors = status["errors"]
    if errors:
        lines.append(f"errors in finished trials: {len(errors)} (latest {min(SHOWN_ERRORS, len(errors))})")
        for error in errors[-SHOWN_ERRORS:]:
            provider = ", ".join(f"{k} x{v['count']}" for k, v in sorted(error["provider_errors"].items()))
            parts = [", ".join(error["exceptions"]), provider]
            lines.append(f"  {error['path']}: " + "; ".join(p for p in parts if p))
    if status.get("blind"):
        lines.append("rewards: hidden (blind)")
        return "\n".join(lines) + "\n"
    lines.append("rewards (finished trials):")
    if not status["rewards"]:
        lines.append("  none")
    for row in status["rewards"]:
        mean = "n/a" if row["mean_reward"] is None else f"{row['mean_reward']:.3f}"
        lines.append(
            f"  {row['label']}/{row['condition']} {row['team']}: trials {row['trials']}, "
            f"scored {row['scored']}, valid {row['valid']}, mean {mean}"
        )
    return "\n".join(lines) + "\n"


def _render_running(status: dict[str, Any]) -> list[str]:
    running = status["running"]
    live = [r for r in running if r["state"] != "abandoned"]
    abandoned = len(running) - len(live)
    lines = [
        f"running trials: {len(live)}"
        + (f" (+{abandoned} abandoned, no write for 3 h or job ended)" if abandoned else "")
    ]
    for trial in live:
        what = f"{trial['label']}/{trial['condition']}" if trial["label"] else "?"
        lines.append(
            f"  {trial['job']} {trial['trial'] or '(no trial folder yet)'} {what} [{trial['state']}] "
            f"elapsed {duration(trial['elapsed_s'])} spend {_usd(trial['cost_usd'])}"
        )
        for seat in trial["seats"]:
            if seat["scripted"]:
                lines.append(f"    {seat['seat']} scripted ({seat['agent']})")
                continue
            flag = " STALL" if seat["stalled"] else ""
            state = (
                "ended" if seat["ended"] else ("last step" if seat["first_step"] else "no step yet, started")
            )
            age = duration(seat["age_s"])
            lines.append(f"    {seat['seat']} {seat['model']} steps {seat['steps']} {state} {age} ago{flag}")
        for kind, row in sorted(trial["errors"].items()):
            lines.append(f"    {kind} x{row['count']}: {row['line'][:140]}")
    return lines


def replace_text(path: Path, text: str) -> None:
    """Replace the file atomically, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_json(path: Path, status: dict[str, Any]) -> None:
    replace_text(path, json.dumps(status, indent=2, sort_keys=True, default=str) + "\n")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flotilla-watch",
        description="Watch a running Flotilla wave (run_wave run folder) or a Harbor jobs folder: progress, "
        "spend, per-seat activity, stalls, errors and rewards. Reads only.",
    )
    p.add_argument("target", type=Path, metavar="TARGET", help="run folder, jobs folder or job folder")
    p.add_argument("--interval", type=float, default=30.0, help="seconds between passes (default 30)")
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    p.add_argument("--json", type=Path, metavar="PATH", help="write the status as JSON here every pass")
    p.add_argument(
        "--html",
        type=Path,
        metavar="DIR",
        help="write DIR/index.html (a live dashboard) every pass and DIR/trials/<job>__<trial>.html once "
        "per finished trial",
    )
    p.add_argument("--notify", action="store_true", help="one macOS notification per new stall or error")
    p.add_argument(
        "--stall-seconds",
        type=float,
        default=300.0,
        help="seat quiet this long while its trial runs (default 300)",
    )
    p.add_argument("--cap-usd", type=float, help="spend cap to show (default: the run's plan budget)")
    p.add_argument(
        "--blind",
        action="store_true",
        help="pre-registered wave: show progress, validity, spend, stalls and errors only; no rewards, "
        "scores, answers or trial timeline pages",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.target.is_dir():
        print(f"flotilla-watch: {args.target} is not a folder", file=sys.stderr)
        return 2
    if args.interval <= 0 or args.stall_seconds <= 0:
        print("flotilla-watch: --interval and --stall-seconds must be positive", file=sys.stderr)
        return 2
    watcher = Watcher(
        args.target,
        stall_seconds=args.stall_seconds,
        cap_usd=args.cap_usd,
        notifier=notify if args.notify else None,
        blind=args.blind,
    )
    dashboard = None
    if args.html:
        from flotilla_analysis.dashboard import Dashboard

        dashboard = Dashboard(args.html, names=watcher.names, refresh_s=None if args.once else args.interval)
    clear = sys.stdout.isatty() and not args.once
    try:
        while True:
            status = watcher.poll()
            if args.json:
                write_json(args.json, status)
            if dashboard is not None:
                dashboard.update(status)
            sys.stdout.write(("\033[H\033[2J" if clear else "") + render(status))
            sys.stdout.flush()
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
