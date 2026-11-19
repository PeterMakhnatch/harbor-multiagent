"""Task directories: the answer key, each seat's briefing and the family module.

Only the answer-dependent measures need them (clobbers and the acted-on proxy). Task directories are
git-ignored and are rebuilt with fresh seat tokens, so a directory is used for a trial only when it
matches that trial: ``environment/task.json`` names the same family, variant, instance and condition,
and the family scorer gives the final answer the score the verifier recorded.

This module executes ``environment/family.py`` from the task directory: run the tool only on task
directories you built.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flotilla_analysis.load import Evidence, Trial, read_json
from flotilla_analysis.names import Identity, rebuilt_folders

TOLERANCE = 1e-9
_SEAT = re.compile(r'^\s+SEAT:\s*(".*")\s*$')
_BRIEFING = re.compile(r'^\s+SEAT_BRIEFING:\s*(".*")\s*$')
_MODULES: dict[str, Any] = {}


@dataclass
class TaskInfo:
    directory: Path | None
    family: Any | None = None
    answer_key: dict[str, Any] | None = None
    briefings: dict[str, Any] = field(default_factory=dict)
    problem: str | None = None  # why the directory cannot be used; None when it can
    task_json: dict[str, Any] | None = None  # set when task.json names this trial's task

    @property
    def usable(self) -> bool:
        return self.problem is None


def load_family(path: Path) -> Any:
    """Import a family module once per distinct file content."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in _MODULES:
        spec = importlib.util.spec_from_file_location(f"flotilla_family_{digest[:16]}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot import {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[digest] = module
    return _MODULES[digest]


def compose_briefings(text: str) -> dict[str, Any]:
    """``SEAT_BRIEFING`` of every seat service in a docker-compose.yaml written by build_tasks.py.

    Each value is a YAML double-quoted scalar holding JSON (``json.dumps`` of the JSON text), and it
    follows the service's ``SEAT`` line.
    """
    briefings: dict[str, Any] = {}
    seat: str | None = None
    for line in text.splitlines():
        if match := _SEAT.match(line):
            seat = json.loads(match[1])
        elif (match := _BRIEFING.match(line)) and seat is not None:
            briefings[seat] = json.loads(json.loads(match[1]))
    return briefings


def find_task_dir(trial: Trial, roots: list[Path]) -> Path | None:
    """The trial's task folder, or the folder of the same task rebuilt since the run: by name under each
    root, then the folders that a build manifest (``manifest.json`` at a root, or at the build root of the
    trial's task path) lists for the same label, instance and condition, since a rebuilt opaque task has
    another name."""
    name = trial.task_name.split("/", 1)[1]
    candidates = [Path(trial.task_path)] if trial.task_path else []
    for root in roots:
        candidates += [root / trial.label / name, root / name]
    same = Identity(trial.label, trial.instance, trial.condition)
    build_roots = [Path(trial.task_path).parent.parent] if trial.task_path else []
    for root in [*roots, *build_roots]:
        candidates += rebuilt_folders(root, same)
    for candidate in candidates:
        if (candidate / "environment" / "task.json").is_file():
            return candidate
    return None


def final_answer(evidence: Evidence | None) -> tuple[bool, Any]:
    """(known, answer): the last submission's answer; (True, None) when nothing was submitted."""
    if evidence is None:
        return False, None
    if evidence.submissions is not None:
        return True, evidence.submissions[-1].get("answer") if evidence.submissions else None
    if evidence.score is not None and "answer" in evidence.score:
        return True, evidence.score.get("answer")
    return False, None


def recorded_score(trial: Trial, evidence: Evidence | None) -> float | None:
    score = (evidence.score or {}).get("score") if evidence is not None else None
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return float(score)
    return trial.reward


def task_info(trial: Trial, evidence: Evidence | None, roots: list[Path]) -> TaskInfo:
    directory = find_task_dir(trial, roots)
    if directory is None:
        return TaskInfo(None, problem="task directory not found")
    task = read_json(directory / "environment" / "task.json")
    if not isinstance(task, dict) or not isinstance(task.get("answer_key"), dict):
        return TaskInfo(directory, problem="environment/task.json has no answer_key")
    expected = {"family": trial.family, "instance": trial.instance, "condition": trial.condition}
    found = {key: task.get(key) for key in expected}
    if found != expected or (task.get("variant") or "") != trial.variant:
        return TaskInfo(directory, problem="environment/task.json describes another task")
    try:
        family = load_family(directory / "environment" / "family.py")
    except Exception as exc:  # noqa: BLE001 - any failure of a family module makes the directory unusable
        return TaskInfo(
            directory, problem=f"environment/family.py failed to load ({type(exc).__name__})", task_json=task
        )
    try:
        compose = (directory / "environment" / "docker-compose.yaml").read_text(encoding="utf-8")
        briefings = compose_briefings(compose)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return TaskInfo(
            directory, problem=f"cannot read seat briefings ({type(exc).__name__})", task_json=task
        )
    info = TaskInfo(directory, family, task["answer_key"], briefings, task_json=task)
    known, answer = final_answer(evidence)
    score = recorded_score(trial, evidence)
    if not known or score is None:
        info.problem = "cannot check the task directory: no recorded final answer and score"
        return info
    try:
        check = float(family.score(answer, task["answer_key"])["score"])
    except Exception as exc:  # noqa: BLE001
        info.problem = f"family score() failed ({type(exc).__name__})"
        return info
    if abs(check - score) > TOLERANCE:
        info.problem = (
            f"task directory does not reproduce the recorded score ({check:g} vs {score:g}); "
            "rebuilt since the run?"
        )
    return info


def answer_score(info: TaskInfo, answer: Any) -> float | None:
    family = info.family
    if not info.usable or family is None:
        return None
    try:
        return float(family.score(answer, info.answer_key)["score"])
    except Exception:  # noqa: BLE001
        return None


def own_briefing_score(info: TaskInfo, seat: str) -> tuple[float | None, str]:
    """Score of ``oracle_answer([seat's own briefing])``: what the seat could answer alone."""
    family = info.family
    if not info.usable or family is None:
        return None, info.problem or "task directory unusable"
    if seat not in info.briefings:
        return None, f"no briefing for seat {seat}"
    try:
        own = family.oracle_answer([info.briefings[seat]])
        return float(family.score(own, info.answer_key)["score"]), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"oracle_answer failed ({type(exc).__name__})"


def _hour(value: Any) -> int | None:
    """The hour of a schedule start, read as leniently as the schedule scorer reads it."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        # Same rule as the schedule scorer: "14", "14:00" and "14:00:00" are 14:00; "14:30" is not a window.
        match = re.fullmatch(r"(\d{1,2})(?::00(?::00)?)?", value.strip(), flags=re.ASCII)
        return int(match.group(1)) if match else None
    return None


def answer_valid(info: TaskInfo, answer: Any) -> bool | None:
    """Whether a submitted answer is a well-formed answer of the family, or None when unknown.

    1. A family module may define ``valid_answer(answer) -> bool``; it decides.
    2. Otherwise the answer must be a non-empty JSON object that has every key of the answer key with a
       non-empty value.
    3. For the ``schedule`` family the (day, start) window must also be one of ``windows()``, the
       family's public list of the windows a meeting can take (so "Sun 10:00" and "Mon 08:00" are
       invalid).
    An invalid answer always scores 0; a valid answer can still be wrong.
    """
    if info.family is None or info.answer_key is None:
        return None
    hook = getattr(info.family, "valid_answer", None)
    if callable(hook):
        try:
            return bool(hook(answer))
        except Exception:  # noqa: BLE001
            return False
    if not isinstance(answer, dict) or not answer:
        return False
    if any(answer.get(key) in (None, "", [], {}) for key in info.answer_key):
        return False
    windows = getattr(info.family, "windows", None)
    if getattr(info.family, "FAMILY", None) == "schedule" and callable(windows):
        day = answer.get("day")
        if not isinstance(day, str):
            return False
        return (day.strip()[:3].title(), _hour(answer.get("start"))) in set(windows())
    return True


_ANSWER_HEADING = re.compile(r"^#+[ \t]*Answer format[ \t]*$", re.MULTILINE | re.IGNORECASE)
_NEXT_HEADING = re.compile(r"^#", re.MULTILINE)
_CODE_SPAN = re.compile(r"`([^`\n]+)`")


def answer_examples(instruction: str) -> list[dict[str, Any]] | None:
    """The JSON objects written as code spans in the "Answer format" section(s) of an instruction, or
    None when the instruction has no such section."""
    sections = list(_ANSWER_HEADING.finditer(instruction))
    if not sections:
        return None
    found: list[dict[str, Any]] = []
    for heading in sections:
        rest = instruction[heading.end() :]
        end = _NEXT_HEADING.search(rest)
        for span in _CODE_SPAN.findall(rest[: end.start()] if end else rest):
            try:
                value = json.loads(span)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                found.append(value)
    return found


def score_against(info: TaskInfo, answer: Any, key: Any) -> float | None:
    family = info.family
    if family is None or not isinstance(key, dict):
        return None
    try:
        return float(family.score(answer, key)["score"])
    except Exception:  # noqa: BLE001
        return None
