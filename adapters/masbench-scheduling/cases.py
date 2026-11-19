"""Read the published MASBench Scheduling inputs without exporting activity text."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Iterable
from itertools import chain
from pathlib import Path
from typing import Any, TypedDict

DATASET_COMMIT = "c0301a650db95958924bb080192c09fef39389a5"
MINUTES_PER_TICK = 10
SLOTS_PER_DAY = 144
DAYS = 5
HORIZON = DAYS * SLOTS_PER_DAY
DURATION = 2

_STRATA = ("2", "3", "4", "5")
_MEMBER_FILES = {f"m{i}": f"Member{i}_final_schedule.json" for i in range(1, 6)}
_CASE_NUMBER = re.compile(r"[1-9][0-9]*")
_INTERVAL = re.compile(r"([0-9]{2}):([0-9]{2})-([0-9]{2}):([0-9]{2})")


class ReferenceAgreement(TypedDict):
    """Compare published gold with the feasible starts shared by all calendars."""

    agrees: bool
    only_in_reference: list[int]
    only_in_calendars: list[int]
    intersection: list[int]


def _require_directory(path: Path) -> Path:
    if path.is_symlink() or not stat.S_ISDIR(path.stat().st_mode):
        raise ValueError(f"{path}: expected a non-symlink directory")
    return path


def _source_directory(dataset: Path) -> Path:
    path = dataset.resolve(strict=True)
    for part in ("Scheduling", "meeting", "minutes_10", "5days"):
        path = _require_directory(path / part)
    return path


def list_cases(dataset: Path) -> list[str]:
    """List present case directories numerically within each supported stratum."""
    source = _source_directory(dataset)
    result = []
    for stratum in _STRATA:
        try:
            directory = _require_directory(source / stratum)
        except FileNotFoundError:
            continue
        names = []
        for entry in directory.iterdir():
            if _CASE_NUMBER.fullmatch(entry.name):
                _require_directory(entry)
                names.append(entry.name)
        result.extend(f"{stratum}/{n}" for n in sorted(names, key=int))
    return result


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON constant")


def _read_source(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"{path}: expected a non-symlink regular file")
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError(f"{path}: invalid source JSON") from None
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _busy_ticks(data: dict[str, Any], member_id: int, path: Path) -> list[int]:
    for field, expected in (
        ("start_time", 0),
        ("end_time", HORIZON),
        ("member_id", member_id),
    ):
        value = data.get(field)
        if type(value) is not int or value != expected:
            raise ValueError(f"{path}: expected {field}={expected}")
    if "total_days" in data and (
        type(data["total_days"]) is not int or data["total_days"] != DAYS
    ):
        raise ValueError(f"{path}: expected total_days={DAYS}")
    if data.get("time_unit") != f"minutes_{MINUTES_PER_TICK}":
        raise ValueError(f"{path}: expected ten-minute ticks")
    activities = data.get("activities")
    if not isinstance(activities, list):
        raise ValueError(f"{path}: expected an activities array")

    occupied = set()
    for index, activity in enumerate(activities):
        context = f"{path}: activities[{index}]"
        if not isinstance(activity, dict):
            raise ValueError(f"{context}: expected an object")
        date = activity.get("date")
        if type(date) is not int or not 1 <= date <= DAYS:
            raise ValueError(f"{context}: expected a date from 1 to {DAYS}")
        duration = activity.get("duration")
        interval = _INTERVAL.fullmatch(duration) if isinstance(duration, str) else None
        if interval is None:
            raise ValueError(f"{context}: expected an HH:MM-HH:MM interval")
        sh, sm, eh, em = map(int, interval.groups())
        if not (
            sh < 24
            and eh <= 24
            and (eh != 24 or em == 0)
            and sm < 60
            and em < 60
            and sm % MINUTES_PER_TICK == em % MINUTES_PER_TICK == 0
        ):
            raise ValueError(f"{context}: invalid ten-minute clock boundary")
        day_start = (date - 1) * SLOTS_PER_DAY
        start = day_start + (60 * sh + sm) // MINUTES_PER_TICK
        end = day_start + (60 * eh + em) // MINUTES_PER_TICK
        # The upstream exporter formats clock values modulo 24 hours. Its
        # occupancy generator lifts a wrapped endpoint into the following day.
        if end <= start:
            end += SLOTS_PER_DAY
        if end > HORIZON:
            raise ValueError(f"{context}: interval exceeds the source horizon")
        occupied.update(range(start, end))
    return sorted(occupied)


def _gold_starts(data: dict[str, Any], path: Path) -> list[int]:
    meeting = data.get("meeting")
    if not isinstance(meeting, dict) or meeting.get("task_type") != "meeting":
        raise ValueError(f"{path}: expected a meeting reference object")
    solutions = meeting.get("solutions")
    if not isinstance(solutions, list):
        raise ValueError(f"{path}: expected a solutions array")
    duration = meeting.get("duration")
    # The upstream exporter falls back to the task spec (minutes) when there
    # are no solutions, instead of measuring an existing answer (ticks).
    if type(duration) is not int or (
        duration != DURATION and (solutions or duration != DURATION * MINUTES_PER_TICK)
    ):
        raise ValueError(f"{path}: expected a {DURATION}-tick meeting")

    starts = set()
    for index, solution in enumerate(solutions):
        context = f"{path}: meeting.solutions[{index}]"
        if not isinstance(solution, dict):
            raise ValueError(f"{context}: expected an object")
        start = solution.get("start_time")
        end = solution.get("end_time")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= HORIZON
            or end - start != DURATION
        ):
            raise ValueError(f"{context}: invalid {DURATION}-tick pair")
        # Keep published cross-midnight answers. The upstream generator did
        # not reset its free run at midnight; native feasibility is separate.
        starts.add(start)
    return sorted(starts)


def load_case(dataset: Path, case_id: str) -> dict[str, Any]:
    """Parse one case and hash its six files without verifying checkout identity."""
    if not isinstance(case_id, str):
        raise ValueError("expected a case ID of the form stratum/number")
    stratum, _, number = case_id.partition("/")
    if stratum not in _STRATA or not _CASE_NUMBER.fullmatch(number):
        raise ValueError("expected a case ID of the form stratum/number")
    root = _source_directory(dataset)
    directory = _require_directory(_require_directory(root / stratum) / number)

    members = {}
    hashes = {}
    for member_id, (role, filename) in enumerate(_MEMBER_FILES.items(), start=1):
        path = directory / filename
        data, hashes[filename] = _read_source(path)
        members[role] = _busy_ticks(data, member_id, path)
    path = directory / "task_plans"
    data, hashes[path.name] = _read_source(path)
    return {
        "schema": "seatlab.calendar-case/v1",
        "case_id": case_id,
        "dataset_commit": DATASET_COMMIT,
        "duration": DURATION,
        "horizon": HORIZON,
        "slots_per_day": SLOTS_PER_DAY,
        "members": members,
        "gold": _gold_starts(data, path),
        "source_sha256": hashes,
    }


def member_starts(
    busy: Iterable[int],
    duration: int = DURATION,
    horizon: int = HORIZON,
    slots_per_day: int = SLOTS_PER_DAY,
) -> list[int]:
    """Return all starts whose full half-open interval is free on one day."""
    for dimension in (duration, horizon, slots_per_day):
        if type(dimension) is not int or dimension <= 0:
            raise ValueError(
                "duration, horizon, and slots_per_day must be positive integers"
            )
    occupied = set()
    for tick in busy:
        if type(tick) is not int or not 0 <= tick < horizon:
            raise ValueError("busy tick must be an integer inside the horizon")
        occupied.add(tick)

    starts = []
    run = 0
    for tick in range(horizon):
        if tick % slots_per_day == 0:
            run = 0
        run = 0 if tick in occupied else run + 1
        if run >= duration:
            starts.append(tick - duration + 1)
    return starts


def evaluate_reference(case: dict[str, Any]) -> ReferenceAgreement:
    """Compare published gold with strict common feasibility; do not alter gold."""
    members = case["members"]
    if members.keys() != _MEMBER_FILES.keys():
        raise ValueError("expected all five calendar roles")
    feasible = set(
        member_starts(
            chain.from_iterable(members.values()),
            duration=case["duration"],
            horizon=case["horizon"],
            slots_per_day=case["slots_per_day"],
        )
    )
    reference = set(case["gold"])
    return {
        "agrees": reference == feasible,
        "only_in_reference": sorted(reference - feasible),
        "only_in_calendars": sorted(feasible - reference),
        "intersection": sorted(reference & feasible),
    }
