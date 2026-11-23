from __future__ import annotations

import math
import uuid
from collections import Counter
from fractions import Fraction
from typing import Any, NamedTuple

Record = dict[str, Any]


class TaskSpec(NamedTuple):
    worlds: frozenset[int]
    units: int
    replays: int


TASKS: dict[str, TaskSpec] = {
    "dispatch": TaskSpec(worlds=frozenset(range(6801, 6809)), units=6, replays=6),
    "calendar": TaskSpec(worlds=frozenset(range(24, 48)), units=1, replays=2),
}
WORLD_KEYS = frozenset(
    {"task", "world", "units", "prompt_sha256", "source_trial", "source_result_sha256", "native", "replays"}
)
DRAW_KEYS = frozenset({"id", "correct", "served_model"})
HEX = frozenset("0123456789abcdef")
MIN_MEAN_EXCESS = Fraction(1, 10)
MAX_UPPER_TAIL_P = Fraction(1, 40)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _exact(value: Fraction) -> dict[str, str | float]:
    return {"fraction": str(value), "float": float(value)}


def _check_digest(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{where}: digest must be a string")
    _require(len(value) == 64 and set(value) <= HEX, f"{where}: digest must be 64 lowercase hex characters")
    return value


def _check_uuid(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{where}: source_trial must be a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError(f"{where}: source_trial {value!r} is not a UUID") from error
    _require(str(parsed) == value, f"{where}: source_trial {value!r} is not canonical lowercase UUID text")
    return value


def _check_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{where}: expected an int, got {type(value).__name__}")
    return value


def _check_draw(draw: Any, units: int, where: str) -> int:
    if not isinstance(draw, dict):
        raise TypeError(f"{where}: draw must be a dict")
    _require(set(draw) == DRAW_KEYS, f"{where}: draw keys {sorted(map(str, draw))} != {sorted(DRAW_KEYS)}")
    draw_id = draw["id"]
    if not isinstance(draw_id, str) or draw_id == "":
        raise ValueError(f"{where}: draw id must be a non-empty string")
    served = draw["served_model"]
    if served is not None and (not isinstance(served, str) or served == ""):
        raise ValueError(f"{where}: served_model must be text or null")
    correct = _check_int(draw["correct"], f"{where} correct")
    _require(0 <= correct <= units, f"{where}: correct {correct} outside 0..{units}")
    return correct


def _check_world(row: Record, task: str, spec: TaskSpec, index: int) -> list[int]:
    where = f"row {index}"
    if not isinstance(row, dict):
        raise TypeError(f"{where}: world record must be a dict")
    _require(set(row) == WORLD_KEYS, f"{where}: keys {sorted(map(str, row))} != {sorted(WORLD_KEYS)}")
    _require(row["task"] == task, f"{where}: task {row['task']!r} mixed with {task!r}")
    world = _check_int(row["world"], f"{where} world")
    _require(world in spec.worlds, f"{where}: world {world} not registered for {task}")
    where = f"{task} world {world}"
    units = _check_int(row["units"], f"{where} units")
    _require(units == spec.units, f"{where}: units must be {spec.units}")
    _check_digest(row["prompt_sha256"], f"{where} prompt_sha256")
    _check_digest(row["source_result_sha256"], f"{where} source_result_sha256")
    _check_uuid(row["source_trial"], where)
    replays = row["replays"]
    if not isinstance(replays, list):
        raise TypeError(f"{where}: replays must be a list")
    _require(len(replays) == spec.replays, f"{where}: {len(replays)} replays, registered {spec.replays}")
    grades = [_check_draw(row["native"], spec.units, f"{where} native")]
    for position, draw in enumerate(replays):
        grades.append(_check_draw(draw, spec.units, f"{where} replay {position}"))
    return grades


def _check_unique(rows: list[Record], task: str, spec: TaskSpec) -> None:
    for key in ("world", "prompt_sha256", "source_trial", "source_result_sha256"):
        counts = Counter(row[key] for row in rows)
        repeated = sorted(str(value) for value, count in counts.items() if count > 1)
        _require(not repeated, f"{task}: duplicate {key}: {repeated}")
    ids = Counter(draw["id"] for row in rows for draw in [row["native"], *row["replays"]])
    repeated_ids = sorted(str(value) for value, count in ids.items() if count > 1)
    _require(not repeated_ids, f"{task}: duplicate draw ids: {repeated_ids}")
    worlds = {row["world"] for row in rows}
    missing = sorted(spec.worlds - worlds)
    _require(not missing, f"{task}: missing registered worlds {missing}")


def _components(native: int, replays: list[int], units: int) -> tuple[Fraction, Fraction]:
    count = len(replays)
    _require(count >= 2, "at least two replays are needed for within-replay disagreement")
    cross = Fraction(sum(abs(native - grade) for grade in replays), count * units)
    pairs = [abs(replays[a] - replays[b]) for a in range(count) for b in range(a + 1, count)]
    _require(len(pairs) == math.comb(count, 2), "pair enumeration mismatch")
    within = Fraction(sum(pairs), len(pairs) * units)
    _require(0 <= cross <= 1 and 0 <= within <= 1, "normalized disagreement left [0, 1]")
    return cross, within


def _outside(grades: list[int]) -> bool:
    replays = grades[1:]
    return grades[0] < min(replays) or grades[0] > max(replays)


def _world_null(grades: list[int], units: int, scale: int) -> list[int]:
    scaled: list[int] = []
    for index in range(len(grades)):
        cross, within = _components(grades[index], grades[:index] + grades[index + 1 :], units)
        value = (cross - within) * scale
        _require(value.denominator == 1, f"excess {cross - within} not on integer lattice 1/{scale}")
        _require(-scale <= value.numerator <= scale, "scaled excess outside bound")
        scaled.append(value.numerator)
    _require(len(scaled) == len(grades), "one null value per draw")
    return scaled


def _null_sum_distribution(nulls: list[list[int]], scale: int) -> dict[int, int]:
    bound = scale * len(nulls)
    distribution: dict[int, int] = {0: 1}
    for world_values in nulls:
        multiplicity = Counter(world_values)
        merged: dict[int, int] = {}
        for total, weight in distribution.items():
            for value, count in multiplicity.items():
                merged[total + value] = merged.get(total + value, 0) + weight * count
        _require(len(merged) <= 2 * bound + 1, "null support exceeded lattice bound")
        distribution = merged
    return distribution


def _per_world(row: Record, grades: list[int], units: int) -> Record:
    native, replays = grades[0], grades[1:]
    cross, within = _components(native, replays, units)
    native_level = Fraction(native, units)
    replay_level = Fraction(sum(replays), len(replays) * units)
    return {
        "world": row["world"],
        "prompt_sha256": row["prompt_sha256"],
        "source_trial": row["source_trial"],
        "native_grade": _exact(native_level),
        "replay_grades": [_exact(Fraction(grade, units)) for grade in replays],
        "native_replay_disagreement": _exact(cross),
        "within_replay_disagreement": _exact(within),
        "excess_disagreement": _exact(cross - within),
        "signed_level_difference": _exact(native_level - replay_level),
        "native_outside_replay_range": _outside(grades),
    }


def _totals(grades: list[list[int]], spec: TaskSpec, excess: Fraction) -> Record:
    count = len(grades)
    components = [_components(world[0], world[1:], spec.units) for world in grades]
    cross = sum((pair[0] for pair in components), Fraction(0)) / count
    within = sum((pair[1] for pair in components), Fraction(0)) / count
    _require(excess == cross - within, "lattice statistic disagrees with component means")
    native_mean = Fraction(sum(world[0] for world in grades), spec.units * count)
    replay_mean = Fraction(sum(sum(world[1:]) for world in grades), spec.units * spec.replays * count)
    outside = sum(1 for world in grades if _outside(world))
    return {
        "mean_native_replay_disagreement": _exact(cross),
        "mean_within_replay_disagreement": _exact(within),
        "mean_excess_disagreement": _exact(excess),
        "mean_native_grade": _exact(native_mean),
        "mean_replay_grade": _exact(replay_mean),
        "signed_level_difference": _exact(native_mean - replay_mean),
        "native_outside_replay_range": {"count": outside, **_exact(Fraction(outside, count))},
    }


def _rule() -> Record:
    return {
        "statistic": "equal-weight world mean of native-replay mean absolute grade difference minus "
        "mean absolute difference over distinct replay pairs",
        "min_mean_excess": _exact(MIN_MEAN_EXCESS),
        "max_upper_tail_p": _exact(MAX_UPPER_TAIL_P),
        "tail": "upper, inclusive of ties",
        "null": "each draw in turn is the native label within its world, literal ties kept, "
        "worlds independent, combined exactly",
        "flag_requires": "mean excess >= min_mean_excess and upper-tail p <= max_upper_tail_p",
        "no_flag_means": "screen_only, not statistical equivalence",
    }


def analyze(rows: list[Record]) -> Record:
    _require(len(rows) > 0, "rows must be a non-empty list")
    first = rows[0]
    if not isinstance(first, dict):
        raise TypeError("row 0: world record must be a dict")
    task = first.get("task")
    if not isinstance(task, str) or task not in TASKS:
        raise ValueError(f"unregistered task {task!r}")
    spec = TASKS[task]
    _require(len(rows) == len(spec.worlds), f"{task}: {len(rows)} worlds, registered {len(spec.worlds)}")
    checked = [_check_world(row, task, spec, index) for index, row in enumerate(rows)]
    _check_unique(rows, task, spec)
    order = sorted(range(len(rows)), key=lambda index: rows[index]["world"])
    ordered_rows = [rows[index] for index in order]
    grades = [checked[index] for index in order]
    draws = spec.replays + 1
    pairs = math.comb(spec.replays, 2)
    scale = spec.units * spec.replays * pairs // math.gcd(spec.replays, pairs)
    per_world = [_per_world(row, world, spec.units) for row, world in zip(ordered_rows, grades, strict=True)]
    nulls = [_world_null(world, spec.units, scale) for world in grades]
    observed = sum(values[0] for values in nulls)
    distribution = _null_sum_distribution(nulls, scale)
    denominator = sum(distribution.values())
    _require(denominator == draws ** len(rows), f"null mass {denominator} != {draws}**{len(rows)}")
    numerator = sum(weight for total, weight in distribution.items() if total >= observed)
    _require(1 <= numerator <= denominator, "observed assignment must lie in its own upper tail")
    excess = Fraction(observed, scale * len(rows))
    p_value = Fraction(numerator, denominator)
    flagged = excess >= MIN_MEAN_EXCESS and p_value <= MAX_UPPER_TAIL_P
    return {
        "task": task,
        "worlds": len(rows),
        "units": spec.units,
        "draws_per_world": draws,
        "per_world": per_world,
        "totals": _totals(grades, spec, excess),
        "permutation": {
            "upper_tail_p": _exact(p_value),
            "numerator": numerator,
            "denominator": denominator,
            "null_support_size": len(distribution),
            "lattice_scale": scale,
        },
        "rule": _rule(),
        "decision": "native_only_confirmation" if flagged else "screen_only",
    }
