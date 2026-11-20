"""Replay the registered final calendar decisions and audit optional direct spend."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import (
    calendar_client,
    calendar_list_packet,
    calendar_list_protocol,
    calendar_list_run,
    calendar_list_summary,
    calendar_protocol,
    calendar_turns,
    deepseek,
    spend,
)
from .replica_io import MAX_TEXT_BYTES

_SCHEMA = "seatlab.calendar-list-report/v1"
_RESPONSE_KEYS = {"schema", "run", "prompt_sha256", "response_sha256", "text"}
_METER_RESULT = (
    "response_sha256",
    "served_model",
    "input_tokens",
    "cache_tokens",
    "output_tokens",
)
_METER_KEYS = {
    "schema",
    "attempt",
    "status",
    "run",
    "role",
    "prompt_sha256",
    *_METER_RESULT,
}


@dataclass(frozen=True, slots=True)
class _Decision:
    slot: int | None = None
    reward: int | None = None
    response_digest: str | None = None
    issue: str | None = None

    @property
    def status(self) -> str:
        if self.reward is None:
            return "missing"
        return "model_invalid" if self.slot is None else "complete"


def _decision(
    directory: int, cell: calendar_list_protocol.Cell, prompt_digest: str
) -> _Decision:
    try:
        receipt = calendar_list_run._read(directory, "response.json")
    except FileNotFoundError:
        return _Decision(issue="receipt_missing")
    except (OSError, ValueError):
        return _Decision(issue="receipt_invalid")

    if (
        receipt.keys() != _RESPONSE_KEYS
        or receipt["schema"] != calendar_list_run.SCHEMA
        or receipt["run"] != cell.id
        or not isinstance(text := receipt["text"], str)
    ):
        return _Decision(issue="receipt_invalid")
    if receipt["prompt_sha256"] != prompt_digest:
        return _Decision(issue="prompt_mismatch")
    try:
        raw = text.encode("utf-8")
    except UnicodeError:
        return _Decision(issue="receipt_invalid")
    if len(raw) > MAX_TEXT_BYTES:
        return _Decision(issue="receipt_invalid")
    response_digest = sha256(raw).hexdigest()
    if receipt["response_sha256"] != response_digest:
        return _Decision(issue="response_mismatch")

    try:
        reward = calendar_list_protocol.grade(cell.case, text)
    except ValueError:
        return _Decision(reward=0, response_digest=response_digest)
    return _Decision(calendar_protocol.decode_slot(text), reward, response_digest)


def _meter(
    directory: int,
    run: str,
    prompt_digest: str,
    response_digest: str | None,
    ledger: dict[str, Any] | None,
) -> str:
    role = calendar_list_run.ROLE
    try:
        receipt = calendar_list_run._read(directory, f"{run}_{role}.json")
    except FileNotFoundError:
        return "meter_missing"
    except (OSError, ValueError):
        return "meter_invalid"

    if (
        receipt.keys() != _METER_KEYS
        or receipt["schema"] != calendar_client._METER_SCHEMA
        or type(receipt["attempt"]) is not int
        or receipt["attempt"] != 1
        or (receipt["run"], receipt["role"]) != (run, role)
    ):
        return "meter_invalid"
    if receipt["prompt_sha256"] != prompt_digest:
        return "prompt_mismatch"
    if receipt["status"] == "failed":
        return (
            "meter_failed"
            if all(receipt[key] is None for key in _METER_RESULT)
            else "meter_invalid"
        )
    if receipt["status"] != "success":
        return "meter_invalid"
    if response_digest is None:
        return "response_unverified"
    if receipt["response_sha256"] != response_digest:
        return "response_mismatch"
    try:
        deepseek._served_model(receipt["served_model"])
    except deepseek.SeatUnavailable:
        return "meter_invalid"
    try:
        row = None if ledger is None else calendar_turns._ledger_row(ledger, run, role)
        calendar_turns._join_usage(receipt, row, require_model=True)
    except calendar_turns.TurnError as exc:
        if ledger is None and exc.category == "ledger_missing":
            return "ledger_unavailable"
        return "meter_invalid" if exc.category == "receipt_invalid" else exc.category
    return "matched"


def _inspect(
    root: int | None,
    study_status: str,
    cell: calendar_list_protocol.Cell,
    *,
    audit_meter: bool,
    ledger: dict[str, Any] | None,
) -> tuple[_Decision, str | None]:
    meter_unavailable = "meter_unavailable" if audit_meter else None
    if root is None:
        issue = "receipt_missing" if study_status == "missing" else "receipt_invalid"
        return _Decision(issue=issue), meter_unavailable
    with ExitStack() as stack:
        try:
            directory = stack.enter_context(calendar_list_run._cell_dir(root, cell.id))
        except FileNotFoundError:
            return _Decision(issue="receipt_missing"), meter_unavailable
        except (OSError, ValueError):
            return _Decision(issue="receipt_invalid"), meter_unavailable
        prompt = calendar_list_protocol.prompt(cell.case, cell.condition)
        prompt_digest = sha256(prompt.encode("utf-8")).hexdigest()
        decision = _decision(directory, cell, prompt_digest)
        meter_status = (
            _meter(directory, cell.id, prompt_digest, decision.response_digest, ledger)
            if audit_meter
            else None
        )
        return decision, meter_status


def _meter_diagnostics(
    requested: bool,
    ledger: dict[str, Any] | None,
    matched: list[str],
    issues: Counter[str],
) -> dict[str, Any]:
    status = "not_requested" if not requested else "unavailable"
    return {
        "ledger_status": status if ledger is None else "available",
        "matched_calls": None if ledger is None else len(matched),
        "charged_only_calls": None
        if ledger is None
        else len(ledger["calls"]) - ledger["unsettled_calls"] - len(matched),
        "held_calls": None if ledger is None else ledger["unsettled_calls"],
        "matched_runs": None if ledger is None else matched,
        "issues": dict(issues),
        "spend": None
        if ledger is None
        else {
            key: ledger[key]
            for key in (
                "currency",
                "known_usd_low",
                "upper_usd_including_holds",
                "price_source",
            )
        },
    }


def analyze(study: Path, spend_dir: Path | None = None) -> dict[str, Any]:
    """Score sealed response text offline; a cost match is not a grading input."""
    cells = calendar_list_protocol.cells(calendar_list_packet.load())
    ledger = None
    if spend_dir is not None:
        try:
            ledger = spend.report(spend_dir.absolute(), run_prefix="cl-")
        except (ArithmeticError, OSError, sqlite3.Error, TypeError, ValueError):
            pass  # A missing or unreadable ledger does not change the decisions.

    rows = []
    receipt_issues: Counter[str] = Counter()
    meter_issues: Counter[str] = Counter()
    matched = []
    with ExitStack() as stack:
        root = None
        study_status = "available"
        try:
            root = stack.enter_context(calendar_list_run._root(study.absolute()))
        except FileNotFoundError:
            study_status = "missing"
        except (OSError, ValueError):
            study_status = "invalid"

        for cell in cells:
            decision, meter_status = _inspect(
                root,
                study_status,
                cell,
                audit_meter=spend_dir is not None,
                ledger=ledger,
            )
            case = cell.case
            rows.append(
                {
                    "run": cell.id,
                    "case": case.id,
                    "world": case.world,
                    "direction": case.direction,
                    "repetition": case.repetition,
                    "source_arm": case.arm,
                    "draw": cell.draw,
                    "condition": cell.condition,
                    "status": decision.status,
                    "slot": decision.slot,
                    "reward": decision.reward,
                }
            )
            if decision.issue is not None:
                receipt_issues[decision.issue] += 1
            if meter_status == "matched":
                matched.append(cell.id)
            elif meter_status is not None:
                meter_issues[meter_status] += 1

    missing = receipt_issues["receipt_missing"]
    return {
        "schema": _SCHEMA,
        "rows": rows,
        "summary": calendar_list_summary.summarize(rows),
        "diagnostics": {
            "study_status": study_status,
            "missing_receipts": missing,
            "invalid_receipts": receipt_issues.total() - missing,
            "receipt_issues": dict(receipt_issues),
            "meter": _meter_diagnostics(
                spend_dir is not None, ledger, matched, meter_issues
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(analyze(args.study, args.spend_dir), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
