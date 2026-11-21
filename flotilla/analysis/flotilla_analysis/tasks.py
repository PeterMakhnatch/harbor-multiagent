"""Task directories: the answer key, each seat's briefing and the family module.

Only the answer-dependent measures need them (clobbers, the acted-on proxy, found and lost answers;
the last also uses the family's optional text hooks, ``HOOKS``). Task directories are
git-ignored and are rebuilt with fresh seat tokens, so a directory is used for a trial only when it
matches that trial: ``environment/task.json`` names the same family, variant, instance and condition;
a folder that is not provably the one the trial ran (rebuilt or moved) has the build parameters of the
run, the seats and limits the world recorded, and the instruction each model seat saw
(``rebuilt_differences``); and the family scorer gives the final answer the score the verifier recorded.

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

from flotilla_analysis.load import Evidence, Trajectory, Trial, read_json
from flotilla_analysis.names import (
    MANIFEST,
    Identity,
    from_name,
    read_manifest,
    read_task_toml,
    rebuilt_folders,
)

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
    hooks: Any | None = None  # the module whose analysis hooks are used (HOOKS); scores use ``family``
    hooks_source: str = ""  # "task folder", "--family-hooks <file>" or "" (none)
    detail: str = ""  # for a folder rebuilt with different parameters: what differs from the run

    @property
    def usable(self) -> bool:
        return self.problem is None


# Optional analysis hooks of a family module (CONTRACT.md 7). They read message texts; scores never
# depend on them.
HOOKS = ("carries_own_data", "answer_in_text")


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


# Build parameters that decide what a task folder holds: two folders of one label, instance and condition
# built with other values hold other briefings, answer keys or rules. build_tasks.py records them in
# task.toml [metadata] and, with a sha256 of each seat's instructions/<seat>.md, in the build manifest.
BUILD_PARAMETERS = (
    "seed",
    "difficulty",
    "n_seats",
    "team_seats",
    "max_text",
    "max_messages",
    "scripted_seats",
    "submitter",
    "announce",
)
_UNSET = {"scripted_seats": [], "submitter": "", "announce": False}  # [metadata] leaves these out when unset


@dataclass(frozen=True)
class Build:
    """What a build put in one task folder: its build parameters (None for one that the source does not
    record) and the sha256 of each seat's instruction file."""

    parameters: dict[str, Any]
    instructions: dict[str, str]


def build_parameters(record: dict[str, Any] | None) -> dict[str, Any]:
    """The build parameters of task.toml [metadata] or of a manifest entry's ``build`` object."""
    if not record:
        return dict.fromkeys(BUILD_PARAMETERS)
    found: dict[str, Any] = {}
    for key in BUILD_PARAMETERS:
        value = record.get(key, _UNSET.get(key))
        if key == "scripted_seats" and isinstance(value, list):
            value = sorted(str(seat) for seat in value)
        elif key == "announce" and value is not None:
            value = bool(value)
        found[key] = value
    return found


def _holds(folder: Path, task_name: str) -> bool:
    task = read_task_toml(folder).get("task")
    return isinstance(task, dict) and task.get("name") == task_name


def folder_build(folder: Path) -> Build:
    meta = read_task_toml(folder).get("metadata")
    digests = {
        path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((folder / "instructions").glob("*.md"))
        if path.is_file()
    }
    return Build(build_parameters(meta if isinstance(meta, dict) else None), digests)


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def ran_here(trial: Trial, folder: Path) -> bool:
    """Whether the folder is provably the one the trial ran: the folder at the trial's task path that holds
    its task under an opaque name, which no build reuses. A readable name may have been rebuilt in place."""
    if not trial.task_path or from_name(trial.task_name) is not None:
        return False
    return _same_folder(folder, Path(trial.task_path)) and _holds(folder, trial.task_name)


def run_build(trial: Trial, roots: list[Path]) -> Build | None:
    """The build that made the task the trial ran: its own folder, when that folder still holds the trial's
    task; else, for an opaque name, the entry of that name in a build manifest (at the build root of the task
    path, or at a root). A readable name's entry may describe a later build of the same name, so it is not
    used. None when unknown (also for an entry written before manifests recorded build parameters)."""
    own = Path(trial.task_path) if trial.task_path else None
    if own is not None and _holds(own, trial.task_name):
        return folder_build(own)
    if from_name(trial.task_name) is not None:
        return None
    build_roots = [own.parent, own.parent.parent] if own is not None else []
    for root in [*build_roots, *roots]:
        entry = read_manifest(root / MANIFEST).entries.get(trial.task_name)
        if isinstance(entry, dict) and isinstance(entry.get("build"), dict):
            digests = entry.get("instructions_sha256")
            return Build(
                build_parameters(entry["build"]),
                {str(k): str(v) for k, v in digests.items()} if isinstance(digests, dict) else {},
            )
    return None


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ",".join(map(str, value)) or "none"
    return str(value) if value != "" else "none"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _recorded_limits(evidence: Evidence | None) -> dict[str, Any]:
    """The message limits in the world's ``start`` event (world version 2), or {}."""
    for event in (evidence.events or []) if evidence is not None else []:
        if event.get("kind") == "start":
            data = event.get("data")
            limits = data.get("limits") if isinstance(data, dict) else None
            return dict(limits) if isinstance(limits, dict) else {}
    return {}


def _instruction_head(text: str) -> str:
    """An instruction up to its "Answer format" section: the seat's information, the team, the rules, the
    limits and the protocols. The example-leak check reads the answer-format section from the trajectory, so a
    later fix of a family's answer example must not make the folder of an old run unusable."""
    match = _ANSWER_HEADING.search(text)
    return _flat(text[: match.start()] if match else text)


def rebuilt_differences(
    trial: Trial,
    folder: Path,
    task: dict[str, Any],
    evidence: Evidence | None,
    trajectories: dict[str, Trajectory],
    roots: list[Path],
) -> list[tuple[str, str]]:
    """(kind, what differs) for a task folder that is not provably the one the trial ran; [] when it may be
    used.

    A rebuilt or moved folder of the same label, instance and condition is used only when it has:

    - the seats and message limits that the world recorded for the run (``world-state.json`` or
      ``score.json`` seats, the ``start`` event's limits);
    - for each seat under test whose first messages show the instruction (its trajectory), the folder's
      instruction text up to the answer-format section;
    - the build parameters of the run, and for seats not checked by their trajectory the same
      instruction file (sha256), when the run's build is known (``run_build``).
    """
    if ran_here(trial, folder):
        return []
    found: list[tuple[str, str]] = []
    recorded = evidence.seats if evidence is not None and evidence.seats else [s.seat for s in trial.seats]
    seats = task.get("seats")
    if isinstance(seats, list) and sorted(map(str, seats)) != sorted(recorded):
        found.append(
            ("seats", f"seats {_show(sorted(map(str, seats)))} (the run: {_show(sorted(recorded))})")
        )
    limits = _recorded_limits(evidence)
    for key in ("max_text", "max_messages"):
        if limits.get(key) is not None and task.get(key) is not None and limits[key] != task[key]:
            found.append((key, f"{key} {task[key]} (the world recorded {limits[key]})"))
    checked: set[str] = set()
    for seat in trial.under_test:
        trajectory = trajectories.get(seat.seat)
        path = folder / "instructions" / f"{seat.seat}.md"
        if trajectory is None or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        anchor = _flat(next((line for line in text.splitlines() if line.strip()), ""))
        head, seen = _instruction_head(text), _flat(trajectory.preamble)
        if not anchor or not head or anchor not in seen:
            continue  # its first messages show no instruction of this task: nothing to compare
        checked.add(seat.seat)
        if head not in seen:
            found.append(("instruction seen", f"instructions/{seat.seat}.md is not the instruction it saw"))
    own = Path(trial.task_path) if trial.task_path else None
    run = None if own is not None and _same_folder(own, folder) else run_build(trial, roots)
    if run is not None:
        here = folder_build(folder)
        for key in BUILD_PARAMETERS:
            expected, actual = run.parameters.get(key), here.parameters.get(key)
            if expected is not None and actual is not None and expected != actual:
                # only the name: a build's seed is secret (CONTRACT.md 2), and reports are published
                found.append((key, f"{key} differs"))
        if run.instructions:
            for seat in sorted((set(run.instructions) | set(here.instructions)) - checked):
                if run.instructions.get(seat) != here.instructions.get(seat):
                    found.append(("instructions", f"instructions/{seat}.md is not the run's"))
    return found


def final_answer(evidence: Evidence | None) -> tuple[bool, Any]:
    """(known, answer): the last submission's answer; (True, None) when nothing was submitted."""
    if evidence is None:
        return False, None
    if evidence.submissions is not None:
        if not evidence.submissions:
            return True, None
        last = evidence.submissions[-1]
        return "answer" in last, last.get("answer")
    if evidence.score is not None and "answer" in evidence.score:
        return True, evidence.score.get("answer")
    return False, None


def hook_module(family_name: str, family: Any, hook_dirs: list[Path]) -> tuple[Any | None, str]:
    """The module whose analysis hooks a trial uses, and where it came from.

    A ``<dir>/<family>.py`` from ``--family-hooks`` comes first, when its ``FAMILY`` is the trial's family:
    task folders built before a hook existed carry an older copy of the family module. Otherwise the
    task folder's own ``family.py``. Scores always come from the task folder's module.
    """
    for directory in hook_dirs:
        path = directory / f"{family_name}.py"
        if not path.is_file():
            continue
        try:
            module = load_family(path)
        except Exception:  # noqa: BLE001 - a broken hook module is not used
            continue
        if getattr(module, "FAMILY", None) == family_name:
            return module, f"--family-hooks {path.name}"
    if any(callable(getattr(family, name, None)) for name in HOOKS):
        return family, "task folder"
    return None, ""


def task_info(
    trial: Trial,
    evidence: Evidence | None,
    roots: list[Path],
    hook_dirs: list[Path] | None = None,
    trajectories: dict[str, Trajectory] | None = None,
) -> TaskInfo:
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
    differences = rebuilt_differences(trial, directory, task, evidence, trajectories or {}, roots)
    if differences:
        kinds = ", ".join(sorted({kind for kind, _what in differences}))
        return TaskInfo(
            directory,
            problem=f"task folder rebuilt with different parameters ({kinds})",
            detail="; ".join(what for _kind, what in differences),
        )
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
    hooks, hooks_source = hook_module(trial.family, family, hook_dirs or [])
    info = TaskInfo(
        directory,
        family,
        task["answer_key"],
        briefings,
        task_json=task,
        hooks=hooks,
        hooks_source=hooks_source,
    )
    known, answer = final_answer(evidence)
    score = trial.reward
    if not known or score is None:
        info.problem = "cannot check the task directory: no recorded final answer and score"
        return info
    try:
        check = float(family.score(answer, task["answer_key"])["score"])
    except Exception as exc:  # noqa: BLE001
        info.problem = f"family score() failed ({type(exc).__name__})"
        return info
    if not 0.0 <= check <= 1.0:
        info.problem = "family score() did not return a finite score in [0, 1]"
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


def seat_private(info: TaskInfo, seat: str) -> Any | None:
    """A seat's own private information: its briefing, or the ``part`` that a scripted peer posts (its
    briefing also holds the script and the text it sends)."""
    briefing = info.briefings.get(seat)
    if isinstance(briefing, dict) and "script" in briefing and "part" in briefing:
        return briefing["part"]
    return briefing


def _hook(info: TaskInfo, name: str) -> tuple[Any | None, str]:
    if not info.usable:
        return None, info.problem or "task directory unusable"
    hook = getattr(info.hooks, name, None) if info.hooks is not None else None
    if not callable(hook):
        family = getattr(info.family, "FAMILY", None) or (info.task_json or {}).get("family") or "task's"
        return None, f"the {family} family module has no {name} hook (see --family-hooks)"
    return hook, ""


def carries_own_data(info: TaskInfo, sender: str, text: str) -> tuple[bool | None, str]:
    """Whether a message carries its sender's own information (the family's ``carries_own_data(text,
    private)``), with the reason when unknown."""
    hook, why = _hook(info, "carries_own_data")
    if hook is None:
        return None, why
    private = seat_private(info, sender)
    if private is None:
        return None, f"no briefing for seat {sender}"
    try:
        return bool(hook(text, private)), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"carries_own_data failed ({type(exc).__name__})"


def answer_in_text(info: TaskInfo, text: str) -> tuple[bool | None, str]:
    """Whether a message names the answer key (the family's ``answer_in_text(text, key)``), with the
    reason when unknown."""
    hook, why = _hook(info, "answer_in_text")
    if hook is None:
        return None, why
    try:
        return bool(hook(text, info.answer_key)), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"answer_in_text failed ({type(exc).__name__})"
