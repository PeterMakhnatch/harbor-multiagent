import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from ..handoff.external_audit import Identity, _Arguments, _digest, _receipts, _report
from ..visibility.clarify_run import _directory, _private
from ..visibility.omp_seat import _validate_model
from . import protocol


def _load_native_audit() -> ModuleType:
    seatlab = str(Path(__file__).resolve().parents[1])
    sys.path.insert(0, seatlab)
    try:
        from . import audit

        return audit
    finally:
        sys.path.remove(seatlab)


audit = _load_native_audit()
run_panel = audit.run_panel

_REPORT_SCHEMA = "seatlab.native-attention-report/v1"
_STAGES = (("inventory", "inventory"), ("select", "dispatch"), ("decide", "dispatch"))
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
_OUTCOME_FIELDS = {
    "trial_id",
    "fingerprint",
    "native_score",
    "actions",
    "opened",
    "turns",
    *audit._METRICS,
}
_MAX_ROWS = 512


def _expected(
    rows: list[Any], stages: tuple[tuple[str, str], ...]
) -> tuple[int, Counter[Identity]]:
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
        for turn, (stage, role) in zip(turns, stages, strict=True):
            if (
                not isinstance(turn, dict)
                or turn.keys() != _TURN_FIELDS
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
    verify: Callable[[dict[str, Any]], None],
    stages: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
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


def _exact(actual: object, expected: object) -> bool:
    return actual == expected and run_panel._config_matches(actual, expected)


def _plan(plan: dict[str, Any], cells: list[dict[str, Any]]) -> None:
    if (
        plan.keys()
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
        or plan["schema"] != "seatlab.native-attention-plan/v1"
        or plan["transport"] != "commands"
        or not _exact(plan["max_direct_calls"], 0)
        or not _exact(plan["episodes"], protocol.EPISODES)
        or not _exact(plan["cells"], cells)
    ):
        raise ValueError
    _digest(plan["command_config_sha256"])
    _stamp(plan["sources"], set(run_panel._source_names("arrival")))
    _stamp(plan["seat_sources"], {"agent.py", "protocol.py"})


def _check_outcome(outcome: dict[str, Any]) -> None:
    if outcome.keys() != _OUTCOME_FIELDS:
        raise ValueError
    _digest(outcome["fingerprint"])
    if not isinstance(outcome["opened"], list):
        raise ValueError
    protocol._open_pair(outcome["opened"])
    actions = outcome["actions"]
    if not isinstance(actions, list) or len(actions) != protocol.EPISODES:
        raise ValueError
    for index, action in enumerate(actions):
        if (
            not isinstance(action, dict)
            or action.keys() != {"id", "choice", "optimal", "feasible"}
            or not _exact(action["id"], index)
            or action["choice"] not in ("A", "B", "C", "D")
            or type(action["feasible"]) is not bool
            or type(action["optimal"]) is not bool
            or action["optimal"]
            and not action["feasible"]
        ):
            raise ValueError
    optimal = sum(action["optimal"] for action in actions)
    score = outcome["native_score"]
    if type(score) not in (int, float) or score != optimal / protocol.EPISODES:
        raise ValueError
    for name in audit._METRICS:
        limit = (
            len(outcome["opened"]) if name.startswith("opened_") else protocol.EPISODES
        )
        if type(outcome[name]) is not int or not 0 <= outcome[name] <= limit:
            raise ValueError
    if (
        outcome["opened_report_changes"] > outcome["total_report_changes"]
        or outcome["opened_sensitive"] > outcome["opened_stock_changes"]
    ):
        raise ValueError


def _verify_attention(source: dict[str, Any]) -> None:
    if source.keys() != {"schema", "plan", "rows", "summary"}:
        raise ValueError
    cells = list(protocol.grid())
    plan, rows = source["plan"], source["rows"]
    if not isinstance(plan, dict) or len(rows) != len(cells):
        raise ValueError
    _plan(plan, cells)
    fields = cells[0].keys() | {"state", "outcome"}
    for row in rows:
        if row.keys() != fields or row["state"] not in _STATES:
            raise ValueError
        if row["outcome"] is not None:
            _check_outcome(row["outcome"])
    if not _exact(source["summary"], audit.summarize(cells, rows)):
        raise ValueError


def audit_receipts(
    report: Path, inventory_receipts: Path, dispatch_receipts: Path, model: str
) -> dict[str, Any]:
    return {
        "schema": "seatlab.native-attention-external/v1",
        **audit_native_receipts(
            report,
            inventory_receipts,
            dispatch_receipts,
            model,
            report_schema=_REPORT_SCHEMA,
            verify=_verify_attention,
            stages=_STAGES,
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Arguments(allow_abbrev=False)
    parser.add_argument("report", type=Path)
    parser.add_argument("--inventory-receipts", type=Path, required=True)
    parser.add_argument("--dispatch-receipts", type=Path, required=True)
    parser.add_argument("--model", required=True)
    try:
        args = parser.parse_args(argv)
        evidence = audit_receipts(
            args.report, args.inventory_receipts, args.dispatch_receipts, args.model
        )
        print(json.dumps(evidence, sort_keys=True))
    except (OSError, KeyError, IndexError, TypeError, ValueError, RecursionError):
        print("Cannot verify native attention receipt evidence.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
