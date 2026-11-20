"""Reduce the first-attempt receipts for the fixed native DeepSeek visibility cohort.

Run from the fork root: python -m examples.seatlab.visibility.pair_report STUDY

Only the sealed cell receipts are inputs. The report checks the frozen world and
accepted dispatch choices independently. It compares other hashes as attested by
the runner, without disclosing the digests or inspecting provider text.
"""

import argparse
import json
import re
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cache
from itertools import combinations
from math import fsum
from pathlib import Path
from runpy import run_path
from typing import Any
from uuid import UUID

from ..capsule import _reject_constant, _unique_object, view_hash

# Frozen in replication.md and emitted by native_pairs. Do not import the
# launch module: collecting offline does not require Harbor or the API client.
_SEEDS = (3101, 3103, 3109, 3119)
_REPETITIONS = range(3)
_CONDITIONS = ("fresh", "silent")
_ROLES = ("inventory", "dispatch")
_EPISODES = 4
_MODEL = "deepseek-flash"
_REVISION = "DeepSeek-V4.1-Flash-1017"
_SERVED = {_MODEL, _REVISION.casefold()}
_AGENT = {
    "name": "seatlab-direct-flash",
    "version": "1.0.0",
    "model_info": {"provider": "deepseek", "name": _MODEL},
}
_CELL_SCHEMA = "seatlab.visibility-native-cell/v1"
_SEAT_SCHEMA = "seatlab.visibility-native-seat/v1"
_HASH_FIELDS = ("view_sha256", "prompt_sha256", "decision_sha256")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TRIAL_NAME = re.compile(r"seatlab-team-[0-9a-f]{24}")
_FAILURES = (
    "preparation_failed",
    "preflight_failed",
    "native_launch_failed",
    "native_evidence_invalid",
)
_WORLD_PATH = (
    Path(__file__).resolve().parents[2]
    / "tasks/dispatch-handoff/environment/seatworld.py"
)


@dataclass(frozen=True, slots=True)
class _Cell:
    seed: int
    repetition: int
    condition: str

    @property
    def cell_id(self) -> str:
        return f"s{self.seed}-r{self.repetition}-{self.condition}"

    def identity(self) -> dict[str, str | int]:
        return {
            "cell_id": self.cell_id,
            "seed": self.seed,
            "repetition": self.repetition,
            "condition": self.condition,
            "pairing": "MM",
        }


def _grid() -> list[_Cell]:
    return [
        _Cell(seed, repetition, condition)
        for index, seed in enumerate(_SEEDS)
        for repetition in _REPETITIONS
        for condition in _CONDITIONS[:: 1 if (index + repetition) % 2 == 0 else -1]
    ]


@dataclass(frozen=True, slots=True)
class _Seat:
    requested: str | None = None
    returned: str | None = None

    def public(self) -> dict[str, str | None]:
        return {"requested_model": self.requested, "returned_model": self.returned}


@dataclass(slots=True)
class _Evidence:
    score: float
    choices: dict[int, str]
    hashes: dict[str, dict[str, str]]
    fingerprint: str


@dataclass(slots=True)
class _Result:
    attempted: bool = False
    reason: str | None = None
    trial_id: str | None = None  # Used only to detect reuse; never serialized.
    models: dict[str, _Seat] = field(default_factory=dict)
    evidence: _Evidence | None = None

    @property
    def status(self) -> str:
        if self.evidence is not None:
            return "valid"
        return "incomplete" if self.attempted else "unattempted"

    @property
    def score(self) -> float | None:
        return None if self.evidence is None else self.evidence.score

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "attempted": self.attempted,
            "reason": self.reason,
            "native_score": self.score,
            "seats": {role: self.models.get(role, _Seat()).public() for role in _ROLES},
        }


class _InvalidReceipt(ValueError):
    """An allowlisted report reason, never a parser or operating system error."""


def _read_sealed(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o400
        or info.st_nlink != 1
    ):
        raise ValueError("expected a sealed receipt")
    value = json.loads(
        path.read_bytes(),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("expected a receipt object")
    return value


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return value if parsed.int and str(parsed) == value else None


def _expected(cell: _Cell) -> dict[str, Any]:
    return {
        "schema": _CELL_SCHEMA,
        **cell.identity(),
        "requested_model": _MODEL,
        "episodes": _EPISODES,
        "reserved_model_turns": 2,
    }


def _matches(row: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        key in row and type(row[key]) is type(value) and row[key] == value
        for key, value in expected.items()
    )


def _marker_matches(marker: dict[str, Any], expected: dict[str, Any]) -> bool:
    name = marker.get("trial_name")
    return (
        isinstance(name, str)
        and _TRIAL_NAME.fullmatch(name) is not None
        and _matches(
            marker,
            {
                **expected,
                "task_name": None,
                "trial_id": None,
                "native_returncode": None,
                "native_score": None,
                "outcome_valid": False,
                "error": "preparation_failed",
                "fingerprint": None,
                "actions": None,
                "seats": dict.fromkeys(_ROLES),
            },
        )
    )


def _invocation(seat: object, role: str, cell_id: str) -> dict[str, Any] | None:
    if not isinstance(seat, dict) or seat.get("agent_info") != _AGENT:
        return None
    invocation = seat.get("invocation")
    if not isinstance(invocation, dict) or not _matches(
        invocation,
        {
            "schema": _SEAT_SCHEMA,
            "role": role,
            "model_turns": 1,
            "run_id": cell_id,
            "requested_model": _MODEL,
        },
    ):
        return None
    return invocation


def _seat_model(invocation: dict[str, Any] | None) -> _Seat:
    if invocation is None:
        return _Seat()
    returned = invocation.get("served_model")
    # Preserve the actual spelling only for an allowlisted model. Unknown
    # provider metadata may contain private text and is not an attestation.
    if (
        isinstance(returned, str)
        and returned.isascii()
        and returned.casefold() in _SERVED
    ):
        return _Seat(_MODEL, returned)
    return _Seat(_MODEL)


@cache
def _latent_cases(seed: int) -> list[dict[str, Any]]:
    # Use the canonical generator, not a script supplied in the study folder.
    world = run_path(str(_WORLD_PATH))["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": seed,
            "episodes": _EPISODES,
            "condition": "fresh",
        }
    )
    return [
        {key: case[key] for key in ("id", "utilities", "before", "after")}
        for case in world.evidence()["cases"]
    ]


def _check_actions(
    value: object, latent: list[dict[str, Any]], score: float
) -> dict[int, str]:
    if not isinstance(value, list) or len(value) != len(latent):
        raise _InvalidReceipt("action_rows_invalid")
    choices = {}
    optimal_count = 0
    for action, case in zip(value, latent, strict=True):
        if not isinstance(action, dict) or action.keys() != {
            "id",
            "choice",
            "optimal",
            "feasible",
            "changed",
        }:
            raise _InvalidReceipt("action_rows_invalid")
        choice = action["choice"]
        if not isinstance(choice, str) or choice not in case["utilities"]:
            raise _InvalidReceipt("action_rows_invalid")
        optimal = choice == max(case["after"], key=case["utilities"].__getitem__)
        if not _matches(
            action,
            {
                "id": case["id"],
                "optimal": optimal,
                "feasible": choice in case["after"],
                "changed": case["before"] != case["after"],
            },
        ):
            raise _InvalidReceipt("action_rows_invalid")
        choices[action["id"]] = choice
        optimal_count += optimal
    if score != optimal_count / len(latent):
        raise _InvalidReceipt("native_score_mismatch")
    return choices


def _check_evidence(
    cell: _Cell,
    row: dict[str, Any],
    trial_id: str | None,
    invocations: dict[str, dict[str, Any] | None],
    models: dict[str, _Seat],
) -> _Evidence:
    if trial_id is None or not _matches(
        row,
        {"native_returncode": 0, "task_name": "harbor/dispatch-handoff", "error": None},
    ):
        raise _InvalidReceipt("native_completion_invalid")
    raw_score = row.get("native_score")
    if type(raw_score) not in (float, int) or not 0 <= raw_score <= 1:
        raise _InvalidReceipt("native_score_invalid")
    score = float(raw_score)
    latent = _latent_cases(cell.seed)
    fingerprint = view_hash(latent)
    if row.get("fingerprint") != fingerprint:
        raise _InvalidReceipt("world_fingerprint_mismatch")
    choices = _check_actions(row.get("actions"), latent, score)
    hashes = {}
    for role, invocation in invocations.items():
        if (
            invocation is None
            or invocation.get("stage") != "complete"
            or (models[role].returned is None)
        ):
            raise _InvalidReceipt("seat_attestation_invalid")
        digests = {}
        for key in _HASH_FIELDS:
            digest = invocation.get(key)
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise _InvalidReceipt("evidence_hash_invalid")
            digests[key] = digest
        hashes[role] = digests
    if hashes["dispatch"]["decision_sha256"] != view_hash(
        [{"id": decision_id, "bay": bay} for decision_id, bay in choices.items()]
    ):
        raise _InvalidReceipt("dispatch_decision_hash_mismatch")
    return _Evidence(score, choices, hashes, fingerprint)


def _inspect(directory: Path, cell: _Cell) -> _Result:
    result = _Result()
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return result
    result.attempted = True  # The directory itself is the exclusive reservation.
    if not stat.S_ISDIR(info.st_mode):
        result.reason = "cell_directory_invalid"
        return result

    expected = _expected(cell)
    # A surviving reservation is never promoted from native files alone.
    for filename in ("attempt", "receipt"):
        try:
            row = _read_sealed(directory / f"{filename}.json")
        except FileNotFoundError:
            result.reason = f"{filename}_missing"
            return result
        except (OSError, ValueError, RecursionError):
            result.reason = f"{filename}_invalid"
            return result
        if filename == "attempt":
            if not _marker_matches(row, expected):
                result.reason = "attempt_identity_mismatch"
                return result
            expected["trial_name"] = row["trial_name"]

    result.trial_id = _canonical_uuid(row.get("trial_id"))
    if not _matches(row, expected):
        result.reason = "receipt_identity_mismatch"
        return result
    seats = row.get("seats")
    if not isinstance(seats, dict) or seats.keys() != set(_ROLES):
        result.reason = "seat_attestation_invalid"
        return result
    invocations = {
        role: _invocation(seats[role], role, cell.cell_id) for role in _ROLES
    }
    result.models = {role: _seat_model(value) for role, value in invocations.items()}
    if row.get("outcome_valid") is not True:
        reason = row.get("error")
        if row.get("outcome_valid") is False and reason in _FAILURES:
            result.reason = reason
        else:
            result.reason = "receipt_outcome_invalid"
        return result
    try:
        result.evidence = _check_evidence(
            cell, row, result.trial_id, invocations, result.models
        )
    except _InvalidReceipt as error:
        result.reason = str(error)
    return result


def _reject_reused_trials(results: dict[_Cell, _Result]) -> None:
    by_trial: dict[str, list[_Result]] = defaultdict(list)
    for result in results.values():
        if result.trial_id is not None:
            by_trial[result.trial_id].append(result)
    for matches in by_trial.values():
        if len(matches) > 1:
            for result in matches:
                result.reason = "duplicate_native_trial_id"
                result.evidence = None
                result.models.clear()


def _compare(left: _Evidence | None, right: _Evidence | None) -> dict[str, int] | None:
    if left is None or right is None:
        return None
    matched = left.choices.keys() & right.choices.keys()
    return {
        "matched_decisions": len(matched),
        "changed_decisions": sum(
            left.choices[key] != right.choices[key] for key in matched
        ),
    }


def _comparison_total(
    comparisons: list[dict[str, int] | None],
) -> dict[str, int | None]:
    present = [comparison for comparison in comparisons if comparison is not None]
    return {
        "scheduled_comparisons": len(comparisons),
        "complete_comparisons": len(present),
        "matched_decisions": sum(item["matched_decisions"] for item in present),
        "changed_decisions": (
            sum(item["changed_decisions"] for item in present) if present else None
        ),
    }


def _hash_variation(digests: list[str]) -> dict[str, str | int]:
    distinct = len(set(digests))
    if len(digests) < 2:
        status = "unsupported"
    else:
        status = "identical" if distinct == 1 else "variable"
    return {"observations": len(digests), "distinct": distinct, "status": status}


def _repeat_variation(results: dict[_Cell, _Result]) -> list[dict[str, Any]]:
    groups = []
    for seed in _SEEDS:
        for condition in _CONDITIONS:
            repeats = {
                rep: results[_Cell(seed, rep, condition)].evidence
                for rep in _REPETITIONS
            }
            valid = [evidence for evidence in repeats.values() if evidence is not None]
            comparisons = [
                {
                    "repetitions": [first, second],
                    "dispatch_choices": _compare(repeats[first], repeats[second]),
                }
                for first, second in combinations(_REPETITIONS, 2)
            ]
            groups.append(
                {
                    "seed": seed,
                    "condition": condition,
                    "comparisons": comparisons,
                    "dispatch_choices": _comparison_total(
                        [item["dispatch_choices"] for item in comparisons]
                    ),
                    "world_fingerprint": _hash_variation(
                        [evidence.fingerprint for evidence in valid]
                    ),
                    "evidence_hashes": {
                        role: {
                            key: _hash_variation(
                                [evidence.hashes[role][key] for evidence in valid]
                            )
                            for key in _HASH_FIELDS
                        }
                        for role in _ROLES
                    },
                }
            )
    return groups


def _mean(values: list[float]) -> float | None:
    return fsum(values) / len(values) if values else None


def _pair(results: dict[_Cell, _Result], seed: int, repetition: int) -> dict[str, Any]:
    fresh, silent = (_Cell(seed, repetition, condition) for condition in _CONDITIONS)
    fresh_result, silent_result = results[fresh], results[silent]
    fresh_score, silent_score = fresh_result.score, silent_result.score
    return {
        "seed": seed,
        "repetition": repetition,
        "fresh_cell": fresh.cell_id,
        "silent_cell": silent.cell_id,
        "fresh_status": fresh_result.status,
        "silent_status": silent_result.status,
        "fresh_score": fresh_score,
        "silent_score": silent_score,
        "fresh_minus_silent": (
            fresh_score - silent_score
            if fresh_score is not None and silent_score is not None
            else None
        ),
        "dispatch_choices": _compare(fresh_result.evidence, silent_result.evidence),
    }


def collect(study: Path | str) -> dict[str, Any]:
    """Read a study root without running a seat or publishing private evidence."""
    study = Path(study).expanduser().resolve(strict=True)
    if not study.is_dir():
        raise ValueError("study root is not a directory")
    cells = study / "cells"
    grid = _grid()
    known = {cell.cell_id for cell in grid}
    if cells.is_symlink() or (
        cells.exists()
        and (
            not cells.is_dir()
            or any(entry.name not in known for entry in cells.iterdir())
        )
    ):
        raise ValueError("unexpected cell set")
    results = {cell: _inspect(cells / cell.cell_id, cell) for cell in grid}
    _reject_reused_trials(results)

    pairs = [_pair(results, seed, rep) for seed in _SEEDS for rep in _REPETITIONS]
    # Repetitions estimate a within-seed contrast, not extra independent worlds.
    blocks = []
    for seed in _SEEDS:
        seed_pairs = [pair for pair in pairs if pair["seed"] == seed]
        deltas = [
            pair["fresh_minus_silent"]
            for pair in seed_pairs
            if pair["fresh_minus_silent"] is not None
        ]
        blocks.append(
            {
                "seed": seed,
                "complete_pairs": len(deltas),
                "fresh_minus_silent": _mean(deltas),
            }
        )
    block_means = [
        block["fresh_minus_silent"]
        for block in blocks
        if block["fresh_minus_silent"] is not None
    ]
    repeats = _repeat_variation(results)
    valid = sum(result.evidence is not None for result in results.values())
    attempted = sum(result.attempted for result in results.values())
    return {
        "schema": "seatlab.visibility-native-pair-report/v1",
        "registered_model": _MODEL,
        "published_model_revision": _REVISION,
        "episodes_per_cell": _EPISODES,
        "independent_task_seeds": len(_SEEDS),
        "hash_basis": {
            "world_and_dispatch_decision": "recomputed",
            "inventory_decision_and_both_prompts_and_views": (
                "native_runner_attested_in_first_attempt_receipt"
            ),
        },
        "counts": {
            "scheduled": len(grid),
            "valid": valid,
            "attempted": attempted,
            "incomplete": attempted - valid,
            "unattempted": len(grid) - attempted,
            "scheduled_pairs": len(pairs),
            "complete_pairs": sum(block["complete_pairs"] for block in blocks),
        },
        "effect": {
            "contrast": "fresh_minus_silent_native_score",
            "seed_blocks": blocks,
            "equal_seed_weight_mean": (
                _mean(block_means) if len(block_means) == len(_SEEDS) else None
            ),
        },
        "cells": [
            {**cell.identity(), **result.public()} for cell, result in results.items()
        ],
        "pairs": pairs,
        "across_arms_dispatch_choices": _comparison_total(
            [pair["dispatch_choices"] for pair in pairs]
        ),
        "within_arm_dispatch_choices": {
            condition: _comparison_total(
                [
                    item["dispatch_choices"]
                    for group in repeats
                    if group["condition"] == condition
                    for item in group["comparisons"]
                ]
            )
            for condition in _CONDITIONS
        },
        "within_arm_repeats": repeats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    args = parser.parse_args(argv)
    try:
        report = collect(args.study)
    except (OSError, ValueError, RuntimeError):
        parser.exit(1, "Cannot read the native pair study or its registered grid.\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
