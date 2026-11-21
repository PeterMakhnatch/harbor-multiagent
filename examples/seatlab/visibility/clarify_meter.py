"""Compare registered clarification receipts with direct-call timing and estimates offline."""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import clarify_run as runner
from . import clarify_schedule as schedule
from . import spend

_STAGES = ("select", "act")
_STAGE_NAMES = frozenset(_STAGES)
_ANOMALY_LIMIT = 16  # per window, counted by distinct public run ID
_MAX_RECEIPT_BYTES = 16_384
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-5][0-9])"
)
_COUNTS = (
    "scheduled_max_calls",
    "no_ledger_row_calls",
    "unsettled_calls",
    "settled_calls",
    "attested_named_model_calls",
    "invalid_usage_calls",
    "invalid_price_calls",
    "unknown_start_calls",
    "invalid_start_calls",
    "starts_inside_window",
    "starts_outside_window",
    "starts_before_open",
    "starts_after_close",
    "unknown_finish_calls",
    "invalid_finish_calls",
    "finishes_after_close",
    "inside_starts_finishing_after_close",
    "missing_receipts",
    "invalid_receipts",
    "incomplete_receipts",
    "complete_receipts",
    "receipt_matched_calls",
    "complete_receipts_matched_to_ledger",
    "complete_receipts_with_in_window_starts",
    "anomalous_calls",
)


def _ledger_rows(directory: Path, run_ids: list[str]) -> dict[str, sqlite3.Row]:
    file = spend._location(directory.expanduser().absolute())
    slots = ",".join("?" for _ in run_ids)
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return {
            row["run"]: row
            for row in db.execute(
                f"""SELECT run, started_at, finished_at, served_model,
                           hit, miss, output, cost_low, cost_high
                    FROM calls WHERE role = ? AND run IN ({slots})""",
                ("dispatch", *run_ids),
            )
        }


def _receipt_turns(
    directory: int, cell: schedule.Cell, digest: str
) -> tuple[str, dict[str, str]]:
    """Check the metering envelope, not the selected cases, actions, or scores."""
    try:
        with runner._regular(directory, f"{cell.cell_id}.json", 0o600) as stream:
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(raw) > _MAX_RECEIPT_BYTES:
            return "invalid", {}
        document = runner._object(raw)
    except FileNotFoundError:
        return "missing", {}
    except (OSError, ValueError, RecursionError):
        return "invalid", {}

    expected = runner._receipt(cell, digest)
    if document.keys() != expected.keys() or any(
        type(document[key]) is not type(expected[key]) or document[key] != expected[key]
        for key in (
            "schema",
            "cell_id",
            "window",
            "seed",
            "mask",
            "arm",
            "packet_sha256",
        )
    ):
        return "invalid", {}
    status, turns = document["status"], document["turns"]
    if (
        not isinstance(status, str)
        or status not in runner._FAILURES | {"started", "complete"}
        or not isinstance(turns, dict)
        or not turns.keys() <= _STAGE_NAMES
        or ("act" in turns and "select" not in turns)
        or (status == "complete" and turns.keys() != _STAGE_NAMES)
        or (status == "started" and "act" in turns)
        or any(
            not isinstance(turn, dict)
            or turn.keys() != {"prompt_sha256", "served_model"}
            or not runner._is_digest(turn["prompt_sha256"])
            or not runner._served_identity(turn["served_model"])
            for turn in turns.values()
        )
    ):
        return "invalid", {}
    return ("complete" if status == "complete" else "incomplete"), {
        stage: turn["served_model"] for stage, turn in turns.items()
    }


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _measured_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _money_and_model(
    row: sqlite3.Row, model: str | None, counts: Counter[str], issues: list[str]
) -> bool:
    low, high = row["cost_low"], row["cost_high"]
    if high is None:
        counts["unsettled_calls"] += 1
        issues.append("unsettled_reservation")
        if low is not None:
            counts["invalid_price_calls"] += 1
            issues.append("ledger_price_invalid")
        if model is not None:
            issues.append("receipt_without_settlement")
        return False

    counts["settled_calls"] += 1
    price_valid = _measured_int(low) and _measured_int(high) and low <= high
    if price_valid:
        counts["low"] += low
        counts["high"] += high
    else:
        counts["invalid_price_calls"] += 1
        issues.append("ledger_price_invalid")
    usage_valid = all(_measured_int(row[key]) for key in ("hit", "miss", "output"))
    if usage_valid:
        for key in ("hit", "miss", "output"):
            counts[key] += row[key]
    else:
        counts["invalid_usage_calls"] += 1
        issues.append("ledger_usage_invalid")
    if runner._served_identity(row["served_model"]):
        counts["attested_named_model_calls"] += 1
    else:
        issues.append("ledger_model_unattested")
    if model is None:
        issues.append("settled_without_receipt_turn")
    elif model != row["served_model"]:
        issues.append("receipt_ledger_model_mismatch")
    else:
        return price_valid and usage_valid
    return False


def _timing(
    row: sqlite3.Row,
    opened: datetime,
    closed: datetime,
    counts: Counter[str],
    issues: list[str],
) -> tuple[datetime | None, datetime | None]:
    start, finish = _time(row["started_at"]), _time(row["finished_at"])
    if start is not None and finish is not None and finish < start:
        issues.append("finish_before_start")
        start = finish = None
    for label, instant, raw in (
        ("start", start, row["started_at"]),
        ("finish", finish, row["finished_at"]),
    ):
        if instant is None:
            reason = f"{'unknown' if raw is None else 'invalid'}_{label}"
            counts[f"{reason}_calls"] += 1
            issues.append(reason)
    if start is not None:
        if opened <= start <= closed:
            counts["starts_inside_window"] += 1
        else:
            counts["starts_outside_window"] += 1
            reason = "starts_before_open" if start < opened else "starts_after_close"
            counts[reason] += 1
            issues.append(reason)
    if finish is not None and finish > closed:
        counts["finishes_after_close"] += 1
        issues.append("finishes_after_close")
        if start is not None and opened <= start <= closed:
            counts["inside_starts_finishing_after_close"] += 1
    return start, finish


def _summary(counts: Counter[str]) -> dict[str, Any]:
    hold = counts["unsettled_calls"] * spend._HOLD
    return {
        "counts": {key: counts[key] for key in _COUNTS},
        "settled_tokens": {
            "cache_hit": counts["hit"],
            "cache_miss": counts["miss"],
            "output": counts["output"],
        },
        "estimated_cost_usd": {
            "known_settled_low": spend._usd(counts["low"]),
            "known_settled_high": spend._usd(counts["high"]),
            "unresolved_holds": spend._usd(hold),
            "upper_including_holds": (
                None
                if counts["invalid_price_calls"]
                else spend._usd(counts["high"] + hold)
            ),
        },
    }


def _window(
    bounds: dict[str, Any],
    cells: list[schedule.Cell],
    hashes: dict[str, str],
    directory: int,
    rows: dict[str, sqlite3.Row],
) -> tuple[dict[str, Any], Counter[str]]:
    opened = runner._utc(bounds["start_utc"])
    closed = runner._utc(bounds["end_utc"])
    counts: Counter[str] = Counter(scheduled_max_calls=len(cells) * len(_STAGES))
    first = last = None
    anomalies = []
    for cell in cells:
        digest = hashes[schedule.packet_path(Path(), cell.seed).name]
        status, turns = _receipt_turns(directory, cell, digest)
        counts[f"{status}_receipts"] += 1
        matches = in_window = 0
        select_end = None
        stage_order_valid = True
        for stage in _STAGES:
            run_id = f"{cell.cell_id}-{stage}"
            row, model = rows.get(run_id), turns.get(stage)
            issues = ["receipt_invalid"] if status == "invalid" else []
            if row is None:
                counts["no_ledger_row_calls"] += 1
                if model is not None:
                    issues.append("receipt_without_ledger_row")
            else:
                if status == "missing":
                    issues.append("ledger_without_receipt")
                matches += _money_and_model(row, model, counts, issues)
                start, finish = _timing(row, opened, closed, counts, issues)
                if start is not None:
                    first = start if first is None else min(first, start)
                if finish is not None:
                    last = finish if last is None else max(last, finish)
                if stage == "select":
                    select_end = finish if finish is not None else start
                elif (
                    start is not None and select_end is not None and start < select_end
                ):
                    stage_order_valid = False
                    issues.append("act_starts_before_select_ends")
                if (
                    start is not None
                    and finish is not None
                    and opened <= start <= closed
                ):
                    in_window += 1
            if issues:
                counts["anomalous_calls"] += 1
                if len(anomalies) < _ANOMALY_LIMIT:
                    anomalies.append({"run_id": run_id, "reasons": issues})
        counts["receipt_matched_calls"] += matches
        if status == "complete" and matches == len(_STAGES):
            counts["complete_receipts_matched_to_ledger"] += 1
            if in_window == len(_STAGES) and stage_order_valid:
                counts["complete_receipts_with_in_window_starts"] += 1
    return {
        **bounds,
        **_summary(counts),
        "first_actual_start_utc": runner._iso(first) if first is not None else None,
        "last_actual_finish_utc": runner._iso(last) if last is not None else None,
        "anomalous_calls": anomalies,
        "anomalous_calls_omitted": counts["anomalous_calls"] - len(anomalies),
    }, counts


def collect(study: Path, spend_dir: Path) -> dict[str, Any]:
    """Read only the registered call IDs and receipt paths; do not run any calls."""
    plan = runner.load_plan(study)
    cells = schedule.grid()
    rows = _ledger_rows(
        spend_dir, [f"{cell.cell_id}-{stage}" for cell in cells for stage in _STAGES]
    )
    totals: Counter[str] = Counter()
    windows = []
    with runner._directory(study / "cells") as directory:
        runner._private(directory, mode=0o700)
        for bounds in plan["windows"]:
            window, counts = _window(
                bounds,
                [cell for cell in cells if cell.window == bounds["window"]],
                plan["packet_sha256"],
                directory,
                rows,
            )
            windows.append(window)
            totals.update(counts)
    return {
        "schema": "seatlab-clarification-meter-v1",
        "basis": (
            "Direct ledger estimates and unresolved holds, not billed charges. "
            "Model names are ledger attestations, not independent verification. "
            "Window start bounds include both endpoints. A finish after close is not a late start. "
            "Missing ledger rows or timestamps do not establish that a call did not occur. "
            "Use the original study ledger; run IDs repeat across replications. "
            "Read after the runner stops. Receipt reconciliation does not replay scores."
        ),
        "overall": _summary(totals),
        "windows": windows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = collect(args.study, args.spend_dir)
    except (OSError, ValueError, sqlite3.Error):
        print(
            json.dumps({"error": "cannot_read_clarification_plan_receipts_or_ledger"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
