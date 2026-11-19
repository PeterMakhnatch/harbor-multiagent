"""Find Flotilla trials under the given paths and read their evidence files.

The walk works like Sextant's: depth first in sorted order, it stops descending at the first
directory whose ``result.json`` is a trial result (it has ``trial_name`` and ``task_name``), and it
never follows a symbolic link to a directory. A trial reached twice counts once. Only JSON and JSONL
files are read; nothing is executed except a task's ``environment/family.py`` (see ``tasks.py``).

Missing is not zero: every value that a file does not record stays ``None``.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TASK_NAME = re.compile(
    r"^flotilla/(?P<label>[a-z0-9_]+(?:\.[a-z0-9_]+)?)-(?P<instance>\d{3})-(?P<condition>solo|silent|talk|talk-liar)$"
)
CONDITIONS = ("solo", "silent", "talk", "talk-liar")
SKIP_DIRS = frozenset({".git", ".hg", ".svn", ".venv", "__pycache__", "node_modules"})


@dataclass(frozen=True)
class Problem:
    path: str
    reason: str


@dataclass
class Seat:
    seat: str
    under_test: bool
    agent: str | None
    model: str | None  # "<provider>/<name>" when the provider is recorded
    n_input: int | None  # input tokens, cached tokens included (Harbor's convention)
    n_cache: int | None
    n_output: int | None
    cost_usd: float | None
    exception: str | None
    started: datetime | None
    finished: datetime | None

    @property
    def label(self) -> str:
        if self.model is None:
            return self.agent or "unknown"
        return f"{self.agent}:{self.model}" if self.agent else self.model


@dataclass
class Job:
    path: str
    name: str
    configured: int | None  # n_total_trials in the job's result.json
    finished_at: str | None
    unfinished_dirs: int  # trial directories without a result.json (running, or never finished)


@dataclass
class Trial:
    path: str  # <input root name>/<path below it>
    directory: Path
    job: str
    trial_name: str
    task_name: str
    label: str  # <family> or <family>.<variant>
    family: str
    variant: str
    instance: int
    condition: str
    finished: bool
    reward: float | None  # None when unscored
    unscored: str | None  # why the reward is not usable; None when scored
    exception: str | None  # trial-level exception type
    seats: list[Seat]
    team: str
    task_path: str | None
    environment_started: datetime | None
    evidence: Evidence | None = None
    trajectories: dict[str, Trajectory] = field(default_factory=dict)

    @property
    def under_test(self) -> list[Seat]:
        return [s for s in self.seats if s.under_test]

    @property
    def exceptions(self) -> list[str]:
        found = [self.exception] if self.exception else []
        return found + [f"{s.seat}:{s.exception}" for s in self.seats if s.exception]


@dataclass
class Evidence:
    """What the world recorded. ``world-state.json`` is authoritative for messages and submissions:
    the verifier wrote it after freezing the world, while ``events.jsonl`` and ``messages.jsonl`` were
    collected before verification and can miss the last moments of play."""

    events: list[dict[str, Any]] | None
    messages: list[dict[str, Any]] | None
    submissions: list[dict[str, Any]] | None
    seats: list[str] | None
    score: dict[str, Any] | None
    sources: list[str]
    problems: list[str]


@dataclass
class Step:
    step_id: int
    timestamp: datetime | None
    keystrokes: list[str]
    observation: str
    tool_calls: int


@dataclass
class Trajectory:
    seat: str
    preamble: str  # the system and user messages before the first agent step (the instruction)
    steps: list[Step]  # agent steps, copied context left out
    start: datetime | None  # timestamp of the first step in the file


def load(paths: list[str | Path]) -> tuple[list[Trial], list[Job], list[Problem]]:
    trials: dict[str, Trial] = {}
    identities: dict[tuple[str, str, str], str] = {}
    jobs: dict[str, Job] = {}
    problems: list[Problem] = []
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            problems.append(Problem(str(raw), "path does not exist"))
            continue
        root = root.resolve()
        if root.is_file():
            if root.name != "result.json":
                problems.append(Problem(str(raw), "not a directory or a result.json file"))
                continue
            root = root.parent
        for directory, relative, data in _walk(root, jobs, problems):
            key = str(directory)
            if key in trials:
                continue
            identity = (str(data.get("id")), str(data.get("trial_name")), str(data.get("task_name")))
            if data.get("id") and identity in identities:
                problems.append(
                    Problem(relative, f"duplicate of {identities[identity]} (same result id): ignored")
                )
                continue
            try:
                trial = parse_trial(data, directory=directory, relative=relative)
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(Problem(relative, f"unreadable trial result: {exc}"))
                continue
            if trial is None:
                problems.append(Problem(relative, f"not a Flotilla task name: {data.get('task_name')!r}"))
                continue
            trials[key] = trial
            if data.get("id"):
                identities[identity] = relative
    ordered = sorted(
        trials.values(), key=lambda t: (t.label, t.team, t.condition, t.instance, t.trial_name, t.path)
    )
    return (
        ordered,
        sorted(jobs.values(), key=lambda j: j.path),
        sorted(problems, key=lambda p: (p.path, p.reason)),
    )


def _walk(root: Path, jobs: dict[str, Job], problems: list[Problem]):
    name = root.name or str(root)
    stack = [root]
    while stack:
        directory = stack.pop()
        relative = (name + str(directory)[len(str(root)) :]).replace(os.sep, "/")
        data = read_json(directory / "result.json", problems, relative + "/result.json")
        if isinstance(data, dict) and "trial_name" in data and "task_name" in data:
            yield directory, relative, data
            continue
        try:
            children = sorted(
                entry.name
                for entry in os.scandir(directory)
                if entry.name not in SKIP_DIRS and entry.is_dir(follow_symlinks=False)
            )
        except OSError as exc:
            problems.append(Problem(relative, f"cannot list: {exc.strerror or type(exc).__name__}"))
            continue
        if (
            isinstance(data, dict)
            and ("n_total_trials" in data or "stats" in data)
            and str(directory) not in jobs
        ):
            unfinished = sum(
                1
                for child in children
                if not (directory / child / "result.json").exists()
                and (
                    (directory / child / "config.json").exists() or (directory / child / "trial.log").exists()
                )
            )
            total = data.get("n_total_trials")
            jobs[str(directory)] = Job(
                path=relative,
                name=directory.name,
                configured=total if isinstance(total, int) and not isinstance(total, bool) else None,
                finished_at=data.get("finished_at") if isinstance(data.get("finished_at"), str) else None,
                unfinished_dirs=unfinished,
            )
        stack.extend(directory / child for child in reversed(children))


def read_json(path: Path, problems: list[Problem] | None = None, shown: str | None = None) -> Any:
    """Parsed JSON, or None when the file is missing; unreadable files are reported."""
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except OSError as exc:
        if problems is not None:
            problems.append(Problem(shown or str(path), f"cannot read: {exc.strerror or type(exc).__name__}"))
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if problems is not None:
            problems.append(Problem(shown or str(path), f"invalid JSON: {exc}"))
        return None


def read_jsonl(path: Path, problems: list[str]) -> list[dict[str, Any]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{path.name}: cannot read ({type(exc).__name__})")
        return None
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"{path.name}:{number}: invalid JSON line skipped")
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _block(data: Any, name: str) -> dict[str, Any]:
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:  # Harbor writes trial times in UTC; read a naive time as UTC
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _model(agent_info: dict[str, Any]) -> tuple[str | None, str | None]:
    agent = agent_info.get("name") if isinstance(agent_info.get("name"), str) else None
    info = _block(agent_info, "model_info")
    name = info.get("name") if isinstance(info.get("name"), str) and info.get("name") else None
    provider = (
        info.get("provider") if isinstance(info.get("provider"), str) and info.get("provider") else None
    )
    if name and provider and not name.startswith(provider + "/"):
        name = f"{provider}/{name}"
    return agent, name


def _seat(data: dict[str, Any], seat: str, under_test: bool) -> Seat:
    agent, model = _model(_block(data, "agent_info"))
    result = _block(data, "agent_result")
    execution = _block(data, "agent_execution")
    exception = _block(data, "exception_info").get("exception_type")
    return Seat(
        seat=seat,
        under_test=under_test,
        agent=agent,
        model=model,
        n_input=_count(result.get("n_input_tokens")),
        n_cache=_count(result.get("n_cache_tokens")),
        n_output=_count(result.get("n_output_tokens")),
        cost_usd=_money(result.get("cost_usd")),
        exception=str(exception) if exception else None,
        started=parse_time(execution.get("started_at")),
        finished=parse_time(execution.get("finished_at")),
    )


def _reward(data: dict[str, Any]) -> tuple[float | None, str | None]:
    """The trial reward and None, or None and the reason it cannot be used."""
    if data.get("finished_at") is None:
        return None, "trial not finished"
    seats = [_block(s, "exception_info").get("exception_type") for s in data.get("seat_results") or []]
    types = sorted({str(t) for t in [_block(data, "exception_info").get("exception_type"), *seats] if t})
    because = f" ({', '.join(types)})" if types else ""
    rewards = _block(data, "verifier_result").get("rewards")
    if not isinstance(rewards, dict) or "reward" not in rewards:
        return None, "no reward" + because
    value = rewards["reward"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None, f"reward is not a finite number: {value!r}"
    if not 0.0 <= float(value) <= 1.0:
        return None, f"reward outside [0, 1]: {value!r}"
    if data.get("reward_valid") is False:
        return None, "reward_valid is false" + because
    return float(value), None


def parse_trial(data: dict[str, Any], *, directory: Path, relative: str) -> Trial | None:
    """A ``Trial`` from a trial ``result.json``; None when the task is not a Flotilla task."""
    trial_name, task_name = data["trial_name"], data["task_name"]
    if not isinstance(trial_name, str) or not isinstance(task_name, str):
        raise ValueError("trial_name and task_name must be strings")
    match = TASK_NAME.match(task_name)
    if match is None:
        return None
    label = match["label"]
    family, _, variant = label.partition(".")
    seat_results = [s for s in data.get("seat_results") or [] if isinstance(s, dict)]
    if seat_results:
        seats = [_seat(s, str(s.get("seat")), s.get("under_test") is not False) for s in seat_results]
    else:  # a single-agent trial: the trial-level agent is seat p1
        seats = [_seat(data, "p1", True)]
    seats.sort(key=lambda s: s.seat)
    labels = sorted({s.label for s in seats if s.under_test})
    if len(labels) == 1:
        team = labels[0]
    else:
        team = "mixed(" + ", ".join(f"{s.seat}={s.label}" for s in seats if s.under_test) + ")"
    reward, unscored = _reward(data)
    exception = _block(data, "exception_info").get("exception_type")
    task_path = _block(data, "task_id").get("path")
    return Trial(
        path=relative,
        directory=directory,
        job=directory.parent.name,
        trial_name=trial_name,
        task_name=task_name,
        label=label,
        family=family,
        variant=variant,
        instance=int(match["instance"]),
        condition=match["condition"],
        finished=data.get("finished_at") is not None,
        reward=reward,
        unscored=unscored,
        exception=str(exception) if exception else None,
        seats=seats,
        team=team,
        task_path=task_path if isinstance(task_path, str) and task_path else None,
        environment_started=parse_time(_block(data, "environment_setup").get("started_at")),
    )


def load_evidence(trial: Trial) -> Evidence:
    problems: list[str] = []
    sources: list[str] = []
    world = trial.directory / "artifacts" / "logs" / "world"
    events = read_jsonl(world / "events.jsonl", problems)
    logged = read_jsonl(world / "messages.jsonl", problems)
    if events is not None:
        sources.append("events.jsonl")
    if logged is not None:
        sources.append("messages.jsonl")
    state = read_json(trial.directory / "verifier" / "world-state.json")
    score = read_json(trial.directory / "verifier" / "score.json")
    if isinstance(state, dict):
        sources.append("world-state.json")
    else:
        state = None
    if isinstance(score, dict):
        sources.append("score.json")
    else:
        score = None

    messages: dict[str, dict[str, Any]] = {}
    for source in ((state or {}).get("messages"), logged):
        for message in source or []:
            if isinstance(message, dict) and isinstance(message.get("id"), str):
                messages.setdefault(message["id"], message)
    if state is None and logged is None:
        message_list = None
    else:
        message_list = sorted(messages.values(), key=_message_order)
        if state is not None and logged is not None:
            missing = sorted(set(messages) - {m.get("id") for m in logged}, key=_id_order)
            if missing:
                problems.append(f"messages.jsonl lacks {', '.join(missing)} (sent after artifact collection)")

    submissions: list[dict[str, Any]] | None
    if state is not None and isinstance(state.get("submissions"), list):
        submissions = [s for s in state["submissions"] if isinstance(s, dict)]
    elif events is not None:
        submissions = [
            {
                "n": e.get("data", {}).get("n"),
                "seat": e.get("seat"),
                "answer": e.get("data", {}).get("answer"),
                "t": e.get("t"),
            }
            for e in events
            if e.get("kind") == "submit"
        ]
    else:
        submissions = None
    if submissions is not None:
        submissions.sort(key=lambda s: s.get("n") if isinstance(s.get("n"), int) else 0)
        if events is not None and state is not None:
            logged_submits = sum(1 for e in events if e.get("kind") == "submit")
            if logged_submits != len(submissions):
                problems.append(
                    f"events.jsonl has {logged_submits} submissions, world-state.json {len(submissions)}"
                )

    seats = None
    for candidate in ((state or {}).get("seats"), (score or {}).get("seats")):
        if isinstance(candidate, list) and all(isinstance(s, str) for s in candidate):
            seats = sorted(candidate)
            break
    if seats is None and events:
        start = next((e for e in events if e.get("kind") == "start"), None)
        candidate = (start or {}).get("data", {}).get("seats")
        if isinstance(candidate, list):
            seats = sorted(str(s) for s in candidate)
    if seats is None:
        seats = sorted(s.seat for s in trial.seats)
    return Evidence(events, message_list, submissions, seats, score, sources, problems)


def _id_order(message_id: str) -> tuple[int, str]:
    digits = message_id[1:] if message_id.startswith("m") else ""
    return (int(digits), message_id) if digits.isdigit() else (1 << 60, message_id)


def _message_order(message: dict[str, Any]) -> tuple[int, str]:
    return _id_order(str(message.get("id")))


def _text(content: Any) -> str:
    """All text in an ATIF message or observation content (a string or a list of content parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(part.get("text") if isinstance(part, dict) else part) for part in content)
    return ""


def load_trajectory(path: Path, seat: str) -> Trajectory | None:
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        return None
    preamble: list[str] = []
    steps: list[Step] = []
    first = next((s for s in data["steps"] if isinstance(s, dict)), {})
    for raw in data["steps"]:
        if not isinstance(raw, dict) or raw.get("is_copied_context"):
            continue
        if raw.get("source") != "agent":
            if not steps:
                preamble.append(_text(raw.get("message")))
            continue
        calls = [c for c in raw.get("tool_calls") or [] if isinstance(c, dict)]
        keystrokes = []
        for call in calls:
            arguments = call.get("arguments")
            if isinstance(arguments, dict):
                keystrokes.extend(str(v) for k, v in sorted(arguments.items()) if isinstance(v, str))
        observation = (raw.get("observation") or {}) if isinstance(raw.get("observation"), dict) else {}
        results = [r for r in observation.get("results") or [] if isinstance(r, dict)]
        step_id = raw.get("step_id")
        steps.append(
            Step(
                step_id=step_id if isinstance(step_id, int) else len(steps) + 1,
                timestamp=parse_time(raw.get("timestamp")),
                keystrokes=keystrokes,
                observation="\n".join(_text(r.get("content")) for r in results),
                tool_calls=len(calls),
            )
        )
    return Trajectory(
        seat=seat, preamble="\n".join(preamble), steps=steps, start=parse_time(first.get("timestamp"))
    )


def load_trajectories(trial: Trial) -> dict[str, Trajectory]:
    found = {}
    for seat in sorted({s.seat for s in trial.seats}):
        trajectory = load_trajectory(trial.directory / "seats" / seat / "agent" / "trajectory.json", seat)
        if trajectory is not None:
            found[seat] = trajectory
    if not found and len(trial.seats) == 1:  # a single-agent trial keeps its trajectory in agent/
        single = load_trajectory(trial.directory / "agent" / "trajectory.json", trial.seats[0].seat)
        if single is not None:
            found[single.seat] = single
    return found
