"""Reconcile attention and briefing command reports with OMP service receipts.

The report supplies trial completeness. Receipts attest service usage for the exact
role and digest multiset, not individual trial IDs or native model usage.
"""

import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from .handoff.external_audit import Identity, _Arguments, _digest, _receipts, _report
from .visibility.clarify_run import _directory, _private
from .visibility.omp_seat import _validate_model

_ROLES = ("inventory", "dispatch")
_KNOWN_FIELDS = ("input_tokens", "output_tokens", "cache_tokens")
_STATES = ("unclaimed", "claimed", "native_failed", "unverified", "complete")
_TURN_FIELDS = {
    "stage",
    "run",
    "role",
    "status",
    "prompt_sha256",
    "response_sha256",
    "usage",
}
_MAX_ROWS = 512

type Report = dict[str, Any]
type Stages = tuple[tuple[str, str], ...]


@contextmanager
def audit_import_path() -> Iterator[None]:
    """Load the native summarizers with their existing sibling imports."""
    seatlab = str(Path(__file__).resolve().parent)
    sys.path.insert(0, seatlab)
    try:
        yield
    finally:
        sys.path.remove(seatlab)


def _expected(rows: list[Any], stages: Stages) -> tuple[int, Counter[Identity]]:
    if (
        not 3 <= len(stages) <= 4
        or len({stage for stage, _ in stages}) != len(stages)
        or {role for _, role in stages} != set(_ROLES)
        or not isinstance(rows, list)
        or not 0 < len(rows) <= _MAX_ROWS
    ):
        raise ValueError
    expected: Counter[Identity] = Counter()
    observed = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError
        cell_id, state, outcome = row["cell_id"], row["state"], row["outcome"]
        if (
            not isinstance(cell_id, str)
            or not 0 < len(cell_id) <= 128
            or not isinstance(state, str)
            or (state == "complete") != (outcome is not None)
        ):
            raise ValueError
        if outcome is None:
            continue
        if not isinstance(outcome, dict):
            raise ValueError
        turns = outcome["turns"]
        if not isinstance(turns, list) or len(turns) != len(stages):
            raise ValueError
        for raw, (stage, role) in zip(turns, stages, strict=True):
            if not isinstance(raw, dict):
                raise ValueError
            turn = cast(Report, raw)
            if (
                turn.keys() != _TURN_FIELDS
                or turn["stage"] != stage
                or turn["run"] != f"{cell_id}-{stage}"
                or turn["role"] != role
                or turn["status"] != "received"
                or turn["usage"] is not None
            ):
                raise ValueError
            prompt = _digest(turn["prompt_sha256"])
            response = _digest(turn["response_sha256"])
            expected[role, prompt, response] += 1
        observed += 1
    if not observed:
        raise ValueError
    return observed, expected


def _match_receipts(
    inventory: Path, dispatch: Path, model: str, expected: Counter[Identity]
) -> dict[str, dict[str, int]]:
    if any(
        not path.is_absolute() or ".." in path.parts for path in (inventory, dispatch)
    ):
        raise ValueError
    with _directory(inventory) as inventory_fd, _directory(dispatch) as dispatch_fd:
        _private(inventory_fd, mode=0o700)
        _private(dispatch_fd, mode=0o700)
        if os.path.samestat(os.fstat(inventory_fd), os.fstat(dispatch_fd)):
            raise ValueError
        roles = {}
        for role, fd in zip(_ROLES, (inventory_fd, dispatch_fd), strict=True):
            totals = _receipts(fd, role, model, expected, 0)
            roles[role] = {
                field: totals[field] for field in ("matched", *_KNOWN_FIELDS)
            }
        return roles


def audit_native_receipts(
    report: Path,
    inventory_receipts: Path,
    dispatch_receipts: Path,
    model: str,
    *,
    report_schema: str,
    verify: Callable[[Report], None],
    stages: Stages,
) -> Report:
    _validate_model(model)
    source = _report(report)
    if source["schema"] != report_schema:
        raise ValueError
    observed, expected = _expected(source["rows"], stages)
    verify(source)
    roles = _match_receipts(inventory_receipts, dispatch_receipts, model, expected)
    return {
        "verified_served_model": model,
        "native_trials": observed,
        "known_fields": list(_KNOWN_FIELDS),
        "roles": roles,
        "native_model_usage_declared": False,
    }


def _stamp(stamp: object, names: set[str]) -> None:
    if not isinstance(stamp, dict) or stamp.keys() != names:
        raise ValueError
    for digest in stamp.values():
        _digest(digest)


def _check_outcome(
    outcome: Report,
    *,
    episodes: int,
    metrics: tuple[str, ...],
    validate_opened: Callable[[object], object],
    extra_outcome_fields: tuple[str, ...],
) -> None:
    if outcome.keys() != {
        "trial_id",
        "fingerprint",
        "native_score",
        "actions",
        "opened",
        "turns",
        *metrics,
        *extra_outcome_fields,
    }:
        raise ValueError
    _digest(outcome["fingerprint"])
    if not isinstance(outcome["opened"], list):
        raise ValueError
    validate_opened(outcome["opened"])
    actions = outcome["actions"]
    if not isinstance(actions, list) or len(actions) != episodes:
        raise ValueError
    for index, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise ValueError
        action = cast(Report, raw)
        if (
            action.keys() != {"id", "choice", "optimal", "feasible"}
            or type(action["id"]) is not int
            or action["id"] != index
            or action["choice"] not in ("A", "B", "C", "D")
            or type(action["feasible"]) is not bool
            or type(action["optimal"]) is not bool
            or action["optimal"]
            and not action["feasible"]
        ):
            raise ValueError
    optimal = sum(action["optimal"] for action in actions)
    score = outcome["native_score"]
    if type(score) not in (int, float) or score != optimal / episodes:
        raise ValueError
    for name in metrics:
        limit = len(outcome["opened"]) if name.startswith("opened_") else episodes
        if type(outcome[name]) is not int or not 0 <= outcome[name] <= limit:
            raise ValueError
    if (
        outcome["opened_report_changes"] > outcome["total_report_changes"]
        or outcome["opened_sensitive"] > outcome["opened_stock_changes"]
    ):
        raise ValueError


def verify_command_report(
    source: Report,
    *,
    plan_schema: str,
    cells: list[Report],
    episodes: int,
    seat_sources: set[str],
    metrics: tuple[str, ...],
    validate_opened: Callable[[object], object],
    summarize: Callable[[list[Report], list[Report]], Report],
    extra_outcome_fields: tuple[str, ...] = (),
) -> None:
    from .run_panel import _config_matches, _source_names

    if source.keys() != {"schema", "plan", "rows", "summary"}:
        raise ValueError
    plan, rows = source["plan"], source["rows"]
    if (
        not isinstance(plan, dict)
        or len(rows) != len(cells)
        or plan.keys()
        != {
            "schema",
            "transport",
            "command_config_sha256",
            "episodes",
            "max_direct_calls",
            "sources",
            "seat_sources",
            "cells",
        }
        or plan["schema"] != plan_schema
        or plan["transport"] != "commands"
        or type(plan["max_direct_calls"]) is not int
        or plan["max_direct_calls"] != 0
        or type(plan["episodes"]) is not int
        or plan["episodes"] != episodes
        or plan["cells"] != cells
        or not _config_matches(plan["cells"], cells)
    ):
        raise ValueError
    _digest(plan["command_config_sha256"])
    _stamp(plan["sources"], set(_source_names("arrival")))
    _stamp(plan["seat_sources"], seat_sources)
    fields = cells[0].keys() | {"state", "outcome"}
    for row in rows:
        if row.keys() != fields or row["state"] not in _STATES:
            raise ValueError
        if row["outcome"] is not None:
            _check_outcome(
                row["outcome"],
                episodes=episodes,
                metrics=metrics,
                validate_opened=validate_opened,
                extra_outcome_fields=extra_outcome_fields,
            )
    expected = summarize(cells, rows)
    if source["summary"] != expected or not _config_matches(
        source["summary"], expected
    ):
        raise ValueError


def receipt_main(
    argv: Sequence[str] | None,
    *,
    study: str,
    collect: Callable[[Path, Path, Path, str], Report],
) -> int:
    parser = _Arguments(allow_abbrev=False)
    parser.add_argument("report", type=Path)
    parser.add_argument("--inventory-receipts", type=Path, required=True)
    parser.add_argument("--dispatch-receipts", type=Path, required=True)
    parser.add_argument("--model", required=True)
    try:
        args = parser.parse_args(argv)
        evidence = collect(
            args.report, args.inventory_receipts, args.dispatch_receipts, args.model
        )
        print(json.dumps(evidence, sort_keys=True))
    except (OSError, KeyError, IndexError, TypeError, ValueError, RecursionError):
        print(f"Cannot verify native {study} receipt evidence.", file=sys.stderr)
        return 1
    return 0
