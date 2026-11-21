import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID

from ..visibility import calendar_packet
from . import repeat_receipt, repeat_summary, team_views, visible_summary
from . import transfer_registration as tr
from .visible_replay_summary import _sign_flip_p

RECEIPT_SCHEMA = "seatlab.calendar-visible-transfer-receipt/v1"
SUMMARY_SCHEMA = "seatlab.calendar-visible-transfer-summary/v1"
_ARMS = team_views.JOIN_ARMS
_CALL_LIMIT = repeat_receipt._CALL_LIMIT
_LEDGER_FIELDS = {"ledger_error", "unassigned_calls", "settled_calls", "held_calls"}
_RECEIPT_FIELDS = _LEDGER_FIELDS | {
    "schema",
    "cohort_sha256",
    "evidence_complete",
    "cells",
}
_CELL_FIELDS = {
    "world",
    "arm",
    "control",
    "run",
    "source_sha256",
    "source_bound",
    "trial_uuid",
    "result_sha256",
    "settled_calls",
    "held_calls",
    "transport_complete",
    "evidence_complete",
    "status",
    "reward",
    "target_visible",
}
_STATUSES = ("complete", "missing", "not_attempted", "model_invalid")
type _Row = dict[str, Any]
type _Enrollment = dict[str, tuple[tr.TransferCell, str]]


def _enroll(manifest: Path, cohort_sha256: str) -> _Enrollment:
    enrolled: _Enrollment = {}
    for world, (_, source) in tr.public_rows(manifest, cohort_sha256).items():
        for arm in _ARMS:
            cell = tr.TransferCell(world, arm, cohort_sha256=cohort_sha256)
            enrolled[cell.run] = (cell, source)
    return enrolled


def _rows(
    data: Any, enrolled: _Enrollment, cohort_sha256: str, *, private: bool
) -> dict[str, _Row]:
    if (
        not isinstance(data, dict)
        or data.get("schema") != (tr.AUDIT_SCHEMA if private else RECEIPT_SCHEMA)
        or data.get("cohort_sha256") != cohort_sha256
        or (private and (data.get("mode") != "live" or "repetition" in data))
        or (not private and data.keys() != _RECEIPT_FIELDS)
    ):
        raise ValueError("invalid live transfer cohort binding")
    cells = data.get("cells")
    if not isinstance(cells, list) or len(cells) != len(enrolled):
        raise ValueError("transfer requires one row for every enrolled cell")
    rows: dict[str, _Row] = {}
    for row in cells:
        if not isinstance(row, dict):
            raise TypeError("invalid transfer cell")
        if private:
            if (
                not repeat_receipt._ROW_FIELDS <= row.keys()
                or row.get("cohort_sha256") != cohort_sha256
                or "repetition" in row
            ):
                raise ValueError("invalid transfer audit cell fields")
        elif row.keys() != _CELL_FIELDS:
            raise ValueError("invalid transfer receipt cell fields")
        run = row.get("run")
        if not isinstance(run, str) or run not in enrolled or run in rows:
            raise ValueError("unknown or duplicate transfer run")
        cell, _ = enrolled[run]
        if (row.get("world"), row.get("arm"), row.get("control")) != (
            cell.world,
            cell.arm,
            cell.control,
        ):
            raise ValueError("transfer run does not match its enrolled cell")
        rows[run] = row
    return rows


def _ledger(data: _Row, rows: dict[str, _Row]) -> dict[str, tuple[int, int]]:
    totals = repeat_receipt._call_counts(data)
    if (
        not _LEDGER_FIELDS <= data.keys()
        or totals is None
        or any(repeat_receipt._call_counts(row) is None for row in rows.values())
        or (data["ledger_error"] == "ledger_unavailable" and totals != (0, 0))
    ):
        raise ValueError("invalid transfer ledger evidence")
    return repeat_receipt._accounting(data, rows)


def _proof(
    row: _Row, trials: set[UUID], results: set[str], *, private: bool
) -> tuple[str | None, str | None]:
    trial, digest = row["trial_uuid"], row["result_sha256"]
    if trial is not None:
        if not isinstance(trial, str):
            raise ValueError("invalid transfer trial UUID")
        identifier = visible_summary._trial_id(trial, trials)
        if identifier is None or identifier.version != 4 or digest is None:
            raise ValueError(
                "transfer trial proof requires a version 4 UUID and digest"
            )
    if digest is not None:
        digest = calendar_packet._check_digest(digest)
        if digest in results:
            raise ValueError("duplicate transfer result digest")
        results.add(digest)
        if trial is None and not private:
            raise ValueError("transfer result digest lacks a native UUID binding")
    return trial, digest if trial is not None else None


def _audit_status(row: _Row, native: str, bound: bool) -> str:
    error = row["error"]
    if error in ("duplicate_native_trial_uuid", "ledger_role_binding"):
        raise ValueError("transfer audit reports duplicate or unregistered evidence")
    if not row["attempted"]:
        if (
            error != "not_attempted"
            or bound
            or row["trial_uuid"] is not None
            or row["result_sha256"] is not None
            or row["reward"] is not None
            or any(value is not None for value in row["stock"].values())
            or repeat_receipt._call_counts(row) != (0, 0)
        ):
            raise ValueError("unattempted transfer cell contains native evidence")
        return "not_attempted"
    if error in ("not_attempted", "ledger_without_attempt") or (
        not row["evidence_complete"] and error is None
    ):
        raise ValueError("inconsistent transfer attempt status")
    if error == "model_invalid":
        if row["reward_verified"] and row["reward"] != 0:
            raise ValueError("verified model-invalid transfer reward must be zero")
        return "model_invalid"
    return "complete" if native == "complete" else "missing"


def receipt(audit: Any, manifest: Path, cohort_sha256: str) -> _Row:
    enrolled = _enroll(manifest, cohort_sha256)
    rows = _rows(audit, enrolled, cohort_sha256, private=True)
    calls = _ledger(audit, rows)
    tasks: dict[str, str] = {}
    trials: set[UUID] = set()
    results: set[str] = set()
    cells: list[_Row] = []
    for run, (cell, pinned) in enrolled.items():
        row = rows[run]
        trial, digest = _proof(row, trials, results, private=True)
        bound = repeat_receipt._bind_source(row, cell.world, pinned, tasks)
        native, _, visible = repeat_receipt._outcome(row, bound, calls.get(run))
        status = _audit_status(row, native, bound)
        cells.append(
            {
                "world": cell.world,
                "arm": cell.arm,
                "control": cell.control,
                "run": run,
                "source_sha256": pinned,
                "source_bound": bound,
                "trial_uuid": trial,
                "result_sha256": digest,
                "settled_calls": row["settled_calls"],
                "held_calls": row["held_calls"],
                "transport_complete": row["evidence_complete"],
                "evidence_complete": status == "complete",
                "status": status,
                "reward": row["reward"] if row["reward_verified"] else None,
                "target_visible": visible,
            }
        )
    return {
        "schema": RECEIPT_SCHEMA,
        "cohort_sha256": cohort_sha256,
        **{field: audit[field] for field in sorted(_LEDGER_FIELDS)},
        "evidence_complete": all(cell["evidence_complete"] for cell in cells),
        "cells": cells,
    }


def _validate_cell(row: _Row, pinned: str, calls: tuple[int, int] | None) -> None:
    bound = row["source_bound"]
    transport, evidence = row["transport_complete"], row["evidence_complete"]
    status, reward, visible = row["status"], row["reward"], row["target_visible"]
    if (
        row["source_sha256"] != pinned
        or any(type(value) is not bool for value in (bound, transport, evidence))
        or status not in _STATUSES
        or (reward is not None and (type(reward) is not int or reward not in (0, 1)))
    ):
        raise ValueError("invalid transfer receipt outcome")
    if (reward is not None or transport) and not (
        bound and row["trial_uuid"] is not None and reward is not None
    ):
        raise ValueError("transfer reward or transport lacks native evidence")
    if (
        evidence != (transport and calls == (_CALL_LIMIT, 0))
        or evidence != (status == "complete")
        or (type(visible) is not bool if evidence else visible is not None)
    ):
        raise ValueError("transfer evidence disagrees with transport or ledger")
    if status == "not_attempted" and (
        bound
        or transport
        or row["trial_uuid"] is not None
        or repeat_receipt._call_counts(row) != (0, 0)
    ):
        raise ValueError("unattempted transfer cell contains receipt evidence")
    if status == "model_invalid" and (transport or reward not in (None, 0)):
        raise ValueError("invalid model-invalid transfer receipt")


def _arm_counts(rows: list[_Row]) -> dict[str, int]:
    return {
        "enrolled": len(rows),
        "eligible": sum(row["evidence_complete"] for row in rows),
        "successes": sum(
            row["evidence_complete"] and row["reward"] == 1 for row in rows
        ),
        "known_native_rewards": sum(row["reward"] is not None for row in rows),
        "known_native_successes": sum(row["reward"] == 1 for row in rows),
        "known_native_losses": sum(row["reward"] == 0 for row in rows),
        "target_visible": sum(row["target_visible"] is True for row in rows),
        "target_visibility_observed": sum(
            row["target_visible"] is not None for row in rows
        ),
        **{
            state: sum(row["status"] == state for row in rows)
            for state in _STATUSES
            if state != "complete"
        },
    }


def _primary(grid: dict[str, dict[str, _Row]]) -> tuple[_Row, str | None]:
    differences = []
    for pair in grid.values():
        lists, checked = (pair[arm] for arm in _ARMS)
        if lists["evidence_complete"] and checked["evidence_complete"]:
            differences.append(checked["reward"] - lists["reward"])
    wins = differences.count(1)
    losses = differences.count(-1)
    result = {
        "enrolled_worlds": len(grid),
        "matched_worlds": len(differences),
        "wins": wins,
        "losses": losses,
        "ties": differences.count(0),
        "discordant_worlds": wins + losses,
        "checked_minus_lists": None,
        "mean_delta": None,
        "two_sided_sign_p": None,
    }
    if len(differences) != len(grid):
        return result, None
    difference = wins - losses
    delta = Fraction(difference, len(grid))
    p = _sign_flip_p(differences)
    result.update(
        checked_minus_lists=difference,
        mean_delta=float(delta),
        two_sided_sign_p=repeat_summary._ratio(p),
    )
    verdict = (
        "pass"
        if delta >= Fraction(1, 5) and p <= Fraction(1, 20)
        else "not_established"
    )
    return result, verdict


def summarize(receipt: Any, manifest: Path, cohort_sha256: str) -> _Row:
    enrolled = _enroll(manifest, cohort_sha256)
    rows = _rows(receipt, enrolled, cohort_sha256, private=False)
    calls = _ledger(receipt, rows)
    trials: set[UUID] = set()
    results: set[str] = set()
    grid: dict[str, dict[str, _Row]] = {}
    for run, (cell, pinned) in enrolled.items():
        row = rows[run]
        _proof(row, trials, results, private=False)
        _validate_cell(row, pinned, calls.get(run))
        grid.setdefault(cell.world, {})[cell.arm] = row
    primary, verdict = _primary(grid)
    complete = verdict is not None
    if receipt["evidence_complete"] is not complete:
        raise ValueError("transfer receipt completion disagrees with cell evidence")
    return {
        "schema": SUMMARY_SCHEMA,
        "cohort_sha256": cohort_sha256,
        "evidence_complete": complete,
        "status": "complete" if complete else "inconclusive",
        "verdict": verdict,
        "arms": {
            arm: _arm_counts([pair[arm] for pair in grid.values()]) for arm in _ARMS
        },
        "primary": primary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export and summarize registered calendar transfer evidence.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command, input_name in (("receipt", "AUDIT"), ("summarize", "RECEIPT")):
        subparser = commands.add_parser(command, allow_abbrev=False)
        subparser.add_argument("input", type=Path, metavar=input_name)
        subparser.add_argument("--manifest", type=Path, required=True)
        subparser.add_argument("--sha256", required=True, metavar="HASH")
    args = parser.parse_args(argv)
    operation = receipt if args.command == "receipt" else summarize
    try:
        data = repeat_receipt._read_audit(args.input)
        result = operation(data, args.manifest, args.sha256)
    except (OSError, TypeError, ValueError):
        parser.error("invalid or unreadable calendar transfer evidence")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
