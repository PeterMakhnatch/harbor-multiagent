import argparse
import json
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any
from uuid import UUID

from .ranking import grid

RECEIPT_SCHEMA = "seatlab.dispatch-ranked-receipt/v1"
_ARMS = ("shown", "ranked")
_WORLD_COUNT = 16
_SHIPMENTS = 6
_DIRECT_CALLS = 3
_MIN_OPTIMAL_DELTA = 10
_MAX_RECEIPT_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CELL_KEYS = {
    "cell_id",
    "seed",
    "arm",
    "status",
    "trial_uuid",
    "result_sha256",
    "optimal",
    "feasible",
    "settled_calls",
    "held_calls",
}
_STATUSES = ("complete", "invalid", "missing", "not_attempted")


@dataclass(frozen=True, slots=True)
class _Outcome:
    complete: bool
    optimal: int | None
    feasible: int | None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError
    return value


def _read_receipt(path: Path) -> Any:
    with open(
        path, "rb", opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK)
    ) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_RECEIPT_BYTES:
            raise ValueError
        content = source.read(_MAX_RECEIPT_BYTES + 1)
    if len(content) > _MAX_RECEIPT_BYTES:
        raise ValueError
    return json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)


def _count(value: object, limit: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError
    return value


def _proof(uuid: object, digest: object) -> tuple[UUID, str] | None:
    if uuid is None and digest is None:
        return None
    if not isinstance(uuid, str) or len(uuid) != 36:
        raise ValueError
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValueError
    trial = UUID(uuid)
    if trial.version != 4 or str(trial) != uuid.lower():
        raise ValueError
    return trial, digest


def _validate_outcome(row: dict[str, Any], bound: bool) -> _Outcome:
    status = row["status"]
    if status not in _STATUSES:
        raise ValueError
    optimal = _count(row["optimal"], _SHIPMENTS)
    feasible = _count(row["feasible"], _SHIPMENTS)
    if optimal is None:
        if feasible is not None:
            raise ValueError
    elif not bound or feasible is None or optimal > feasible:
        raise ValueError

    settled = _count(row["settled_calls"], _DIRECT_CALLS)
    held = _count(row["held_calls"], _DIRECT_CALLS)
    if settled is not None and held is not None and settled + held > _DIRECT_CALLS:
        raise ValueError
    if status == "complete":
        if not bound or optimal is None or settled != _DIRECT_CALLS or held != 0:
            raise ValueError
    elif status == "not_attempted" and (bound or settled != 0 or held != 0):
        raise ValueError
    return _Outcome(status == "complete", optimal, feasible)


def _enroll(document: Any) -> dict[tuple[int, str], _Outcome]:
    expected = {cell["cell_id"]: (cell["seed"], cell["arm"]) for cell in grid()}
    if (
        not isinstance(document, dict)
        or document.keys() != {"schema", "cells"}
        or document["schema"] != RECEIPT_SCHEMA
        or not isinstance(document["cells"], list)
        or len(document["cells"]) != _WORLD_COUNT * len(_ARMS)
        or len(expected) != len(document["cells"])
    ):
        raise ValueError
    outcomes: dict[tuple[int, str], _Outcome] = {}
    seen_trials: set[UUID] = set()
    seen_digests: set[str] = set()
    for row in document["cells"]:
        if not isinstance(row, dict) or row.keys() != _CELL_KEYS:
            raise ValueError
        cell_id, seed, arm = row["cell_id"], row["seed"], row["arm"]
        if not isinstance(cell_id, str) or type(seed) is not int:
            raise ValueError
        identity = expected.pop(cell_id, None)
        if identity is None or identity != (seed, arm) or identity in outcomes:
            raise ValueError
        proof = _proof(row["trial_uuid"], row["result_sha256"])
        if proof is not None:
            trial, digest = proof
            if trial in seen_trials or digest in seen_digests:
                raise ValueError
            seen_trials.add(trial)
            seen_digests.add(digest)
        outcomes[identity] = _validate_outcome(row, bound=proof is not None)
    return outcomes


def _arm_summary(outcomes: list[_Outcome]) -> dict[str, int | None]:
    grades = [
        (outcome.optimal, outcome.feasible)
        for outcome in outcomes
        if outcome.optimal is not None and outcome.feasible is not None
    ]
    complete = sum(outcome.complete for outcome in outcomes)
    return {
        "enrolled": len(outcomes),
        "complete": complete,
        "known_native_outcomes": len(grades),
        "first_attempt_optimal": sum(optimal for optimal, _ in grades)
        if grades
        else None,
        "first_attempt_feasible": sum(feasible for _, feasible in grades)
        if grades
        else None,
        "known_native_shipments": len(grades) * _SHIPMENTS,
        "eligible_shipments": complete * _SHIPMENTS,
    }


def _world_difference(shown: _Outcome, ranked: _Outcome) -> int | None:
    if not shown.complete or not ranked.complete:
        return None
    if shown.optimal is None or ranked.optimal is None:
        raise ValueError
    return ranked.optimal - shown.optimal


def _summarize(outcomes: dict[tuple[int, str], _Outcome]) -> dict[str, Any]:
    seeds = sorted({seed for seed, _ in outcomes})
    if len(seeds) != _WORLD_COUNT or any(
        (seed, arm) not in outcomes for seed in seeds for arm in _ARMS
    ):
        raise ValueError
    arms = {arm: _arm_summary([outcomes[seed, arm] for seed in seeds]) for arm in _ARMS}
    differences = {
        seed: _world_difference(outcomes[seed, "shown"], outcomes[seed, "ranked"])
        for seed in seeds
    }
    matched = [
        difference for difference in differences.values() if difference is not None
    ]
    complete = len(matched) == _WORLD_COUNT
    verdict = None
    delta = None
    p_value = None
    if complete:
        delta = sum(matched)
        observed = abs(delta)
        numerator = sum(
            abs(sum(draw)) >= observed
            for draw in product(*((-change, change) for change in matched))
        )
        denominator = 1 << _WORLD_COUNT
        p_value = {"numerator": numerator, "denominator": denominator}
        verdict = (
            "pass"
            if delta >= _MIN_OPTIMAL_DELTA and numerator * 20 <= denominator
            else "not_established"
        )
    return {
        "status": "complete" if complete else "incomplete",
        "verdict": verdict,
        "arms": arms,
        "primary": {
            "matched_worlds": len(matched),
            "world_differences": differences,
            "optimal_delta": delta,
            "p_value": p_value,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        result = _summarize(_enroll(_read_receipt(args.receipt)))
    except (OSError, ValueError, RecursionError):
        parser.error("cannot read a valid ranked receipt")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
