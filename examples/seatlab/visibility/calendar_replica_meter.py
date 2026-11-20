"""Replay calendar replica turns against local direct-call and budget records.

A match is local corroboration, not provider attestation or an invoice. Native
scores and the budget ledger each stand independently of the meter records.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
from collections import Counter
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import calendar_client as client
from . import calendar_protocol as protocol
from . import calendar_replica_report as native
from . import deepseek, spend

_Row = dict[str, Any]
_Call = tuple[str, str]
_Usage = tuple[int, int, int]  # cache hit, cache miss, output
_WORLDS = (0, 4, 8)
_ARMS = ("blind", "peer")
_SEATS = ("p1", "p2")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_METER_BYTES = 4096
_MAX_TOKENS = spend._HOLD // 3
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
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
_SCORED = {"complete", "model_invalid_scored_zero"}
_ARM_STATUSES = _SCORED | {
    "not_started",
    "invalid_evidence",
    "missing_evidence",
    "native_failure",
}
_BLOCK_STATUSES = {
    "complete",
    "not_started",
    "partial",
    "invalid_seed",
    "invalid_evidence",
    "native_failure",
    "unavailable",
}
_SEED_STATUSES = {"not_started", "accepted", "model_invalid", "invalid_evidence"}
_LEDGER_COUNTS = (
    "ledger_rows",
    "settled_calls",
    "unsettled_calls",
    "invalid_ledger_rows",
    "duplicate_ledger_rows",
    "foreign_ledger_rows",
    "foreign_settled_calls",
)


class _UnsafePath(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Charge:
    status: str
    model: str | None = None
    usage: _Usage | None = None
    price: tuple[int, int] | None = None


@dataclass(slots=True)
class _Ledger:
    rows: dict[_Call, _Charge]
    counts: Counter[str]
    spend: _Row


def _plan() -> list[tuple[str, protocol.Block, tuple[_Call, ...]]]:
    plan = []
    for block in protocol.blocks():
        if block.world not in _WORLDS:
            continue
        base = f"rx-w{block.world:02d}-d{block.direction}-r{block.repetition}"
        calls = []
        for identifier in client._request_plan(block):
            run, _, role = identifier.partition(":")
            calls.append((run, role))
        plan.append((base, block, tuple(calls)))
    return plan


def _private(info: os.stat_result, mode: int) -> bool:
    return info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == mode


@contextmanager
def _directory(
    path: str | Path, parent: int | None = None
) -> Generator[int, None, None]:
    if isinstance(path, Path) and (".." in path.parts or path.resolve() != path):
        raise _UnsafePath("nonphysical_directory")
    fd = os.open(path, _DIR_FLAGS, dir_fd=parent)
    try:
        if not _private(os.fstat(fd), 0o700):
            raise _UnsafePath("nonprivate_directory")
        yield fd
    finally:
        os.close(fd)


def _unexpected(directory: int, allowed: set[str]) -> int:
    with os.scandir(directory) as entries:
        return sum(entry.name not in allowed for entry in entries)


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and _private(info, 0o600)


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _count(value: object, limit: int = _MAX_TOKENS) -> bool:
    return type(value) is int and 0 <= value <= limit


def _model(value: object) -> str | None:
    if isinstance(value, str):
        model = value.casefold()
        if model in deepseek._ACCEPTED_MODELS:
            return model
    return None


def _meter(directory: int, call: _Call) -> tuple[str, _Row]:
    run, role = call
    try:
        name = f"{run}_{role}.json"
        if not _regular(os.stat(name, dir_fd=directory, follow_symlinks=False)):
            return "invalid", {}
        fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
        try:
            if not _regular(os.fstat(fd)):
                return "invalid", {}
            with os.fdopen(fd, "rb", closefd=False) as source:
                raw = source.read(_MAX_METER_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > _MAX_METER_BYTES:
            return "invalid", {}
        row = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=protocol._unique_object,
            parse_constant=client._constant,
        )
    except FileNotFoundError:
        return "missing", {}
    except (OSError, ValueError, RecursionError):
        return "invalid", {}
    if (
        not isinstance(row, dict)
        or row.keys() != _METER_KEYS
        or row["schema"] != client._METER_SCHEMA
        or type(row["attempt"]) is not int
        or row["attempt"] != 1
        or row["run"] != run
        or row["role"] != role
        or not _digest(row["prompt_sha256"])
    ):
        return "invalid", {}
    if row["status"] == "failed" and all(row[key] is None for key in _METER_RESULT):
        return "failed", row
    if (
        row["status"] == "success"
        and _digest(row["response_sha256"])
        and _model(row["served_model"]) is not None
        and all(
            _count(row[key])
            for key in ("input_tokens", "cache_tokens", "output_tokens")
        )
        and row["cache_tokens"] <= row["input_tokens"]
    ):
        return "success", row
    return "invalid", {}


def _meter_block(
    parent: int, calls: tuple[_Call, ...]
) -> tuple[str, int | None, dict[_Call, tuple[str, _Row]]]:
    try:
        with _directory("meter", parent) as fd:
            foreign = _unexpected(fd, {f"{run}_{role}.json" for run, role in calls})
            return "available", foreign, {call: _meter(fd, call) for call in calls}
    except FileNotFoundError:
        return "missing", 0, {}
    except (OSError, ValueError):
        return "unsafe", None, {}


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not 25 <= len(value) <= 32:
        return None
    try:
        instant = datetime.fromisoformat(value)
        if (
            instant.utcoffset() == timedelta(0)
            and instant.astimezone(UTC).isoformat() == value
        ):
            return instant
    except (ValueError, OverflowError):
        pass
    return None


def _charge(row: sqlite3.Row) -> _Charge:
    low, high = row["cost_low"], row["cost_high"]
    reserved = _time(row["reserved_at"])
    model = row["served_model"]
    hit, miss, output = row["hit"], row["miss"], row["output"]
    if reserved is not None and all(
        value is None
        for value in (
            low,
            high,
            model,
            hit,
            miss,
            output,
            row["started_at"],
            row["finished_at"],
        )
    ):
        return _Charge("unsettled")
    if not (_count(low, spend._HOLD) and _count(high, spend._HOLD) and low <= high):
        return _Charge("invalid")
    # Keep a plausible recorded cost even when the row cannot corroborate a turn.
    invalid = _Charge("invalid", price=(low, high))
    start, finish = _time(row["started_at"]), _time(row["finished_at"])
    if (
        reserved is None
        or start is None
        or finish is None
        or not timedelta(0) <= finish - start <= timedelta(minutes=10)
        or not isinstance(model, str)
        or not model
        or not all(_count(value) for value in (hit, miss, output))
    ):
        return invalid
    rate = hit * 3 + miss * 150 + output * 600
    upper = rate if spend._offpeak(start) and spend._offpeak(finish) else 2 * rate
    if (low, high) != (rate, upper):
        return invalid
    return _Charge("settled", model, (hit, miss, output), (low, high))


def _scan_ledger(db: sqlite3.Connection, expected: set[_Call]) -> _Ledger:
    db.row_factory = sqlite3.Row
    # An unrelated malformed TEXT column must not hide other recorded costs.
    db.text_factory = lambda raw: raw.decode("utf-8", "replace")
    db.execute("PRAGMA trusted_schema=OFF")
    tables = db.execute("""
        SELECT name FROM sqlite_schema
        WHERE type = 'table' AND sql LIKE 'CREATE TABLE %' AND name IN ('budget', 'calls')
    """)
    if {row[0] for row in tables} != {"budget", "calls"}:
        raise ValueError("incompatible_ledger")

    rows: dict[_Call, _Charge] = {}
    counts: Counter[str] = Counter()
    statuses = {
        "settled": "settled_calls",
        "unsettled": "unsettled_calls",
        "invalid": "invalid_ledger_rows",
    }
    low = upper = unknown = 0
    for row in db.execute("""
        SELECT run, role, reserved_at, started_at, finished_at, served_model,
               hit, miss, output, cost_low, cost_high
        FROM calls WHERE substr(run, 1, 3) COLLATE BINARY = 'rx-'
    """):
        charge = _charge(row)
        counts["ledger_rows"] += 1
        counts[statuses[charge.status]] += 1
        if charge.price is not None:
            low += charge.price[0]
            upper += charge.price[1]
        elif charge.status == "unsettled":
            upper += spend._HOLD
        else:
            unknown += 1
        call = (row["run"], row["role"])
        if call not in expected:
            counts["foreign_ledger_rows"] += 1
            counts["foreign_settled_calls"] += charge.status == "settled"
        elif call in rows:
            if rows[call].status != "duplicate":
                counts["duplicate_ledger_rows"] += 1
            counts["duplicate_ledger_rows"] += 1
            rows[call] = _Charge("duplicate")
        else:
            rows[call] = charge
    return _Ledger(
        rows,
        counts,
        {
            "schema": "seatlab.direct-flash-cost/v1",
            "currency": "USD",
            "calls": [_spend_call(call, rows[call]) for call in sorted(rows)],
            "unsettled_calls": counts["unsettled_calls"],
            "known_usd_low": spend._usd(low),
            "upper_usd_including_holds": None if unknown else spend._usd(upper),
            "price_source": "https://api-docs.deepseek.com/quick_start/pricing",
        },
    )


def _spend_call(call: _Call, charge: _Charge) -> _Row:
    hit, miss, output = charge.usage or (None, None, None)
    low, high = charge.price or (None, None)
    return {
        "run": call[0],
        "role": call[1],
        "served_model": _model(charge.model),
        "cache_hit": hit,
        "cache_miss": miss,
        "output": output,
        "estimated_usd_low": spend._usd(low) if low is not None else None,
        "estimated_usd_high": spend._usd(high) if high is not None else None,
        "unsettled_hold_usd": spend._usd(
            spend._HOLD if charge.status == "unsettled" else 0
        ),
    }


def _snapshot(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _ledger(directory: Path, expected: set[_Call]) -> tuple[str, _Ledger | None]:
    path = directory / "direct-flash.sqlite3"
    try:
        with _directory(directory) as root:
            try:
                info = os.stat(path.name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                return "missing", None
            if not _regular(info):
                return "unsafe", None

            # The writer uses the default rollback journal. Only read a closed,
            # stable ledger: immutable mode neither creates sidecars nor replays them.
            def stable() -> bool:
                return _snapshot(path.lstat()) == _snapshot(info) and not any(
                    os.path.lexists(f"{path}{suffix}")
                    for suffix in ("-journal", "-wal", "-shm")
                )

            if not stable():
                return "busy", None
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as db:
                report = _scan_ledger(db, expected)
            if not stable():
                return "busy", None
    except FileNotFoundError:
        return "missing", None
    except (OSError, RuntimeError, _UnsafePath):
        return "unsafe", None
    except (ValueError, sqlite3.Error):
        return "invalid", None
    return "available", report


def _native_view(out: Path, parent: int, block: protocol.Block) -> _Row:
    try:
        with _directory("block", parent):
            report = native.analyze(out)
    except FileNotFoundError:
        return {"status": "not_started"}
    except (OSError, ValueError):
        return {"status": "unavailable"}
    position = tuple(
        report.get(field) for field in ("world", "direction", "repetition")
    )
    if position == (None, None, None):
        return {"status": report.get("status", "invalid_evidence")}
    if any(type(value) is not int for value in position) or position != (
        block.world,
        block.direction,
        block.repetition,
    ):
        return {"status": "invalid_evidence"}
    return report


def _category(value: object, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _public_native(report: _Row) -> _Row:
    native_status = _category(report.get("status"), _BLOCK_STATUSES, "invalid_evidence")
    absent = (
        native_status
        if native_status in ("unavailable", "invalid_evidence")
        else "not_started"
    )
    arms = {}
    for arm in _ARMS:
        row = report.get("arms", {}).get(arm, {})
        status = _category(row.get("status"), _ARM_STATUSES, absent)
        reward, slot = row.get("reward"), row.get("slot")
        arms[arm] = {
            "status": status,
            "reward": reward if status in _SCORED and _count(reward, 1) else None,
            "slot": slot if status == "complete" and _count(slot, 34) else None,
        }
    return {
        "native_status": native_status,
        "arms": arms,
        "seeds": {
            seat: _category(
                report.get("seeds", {}).get(seat, {}).get("status"),
                _SEED_STATUSES,
                absent,
            )
            for seat in _SEATS
        },
    }


def _bind(
    call: _Call, evidence: _Row | None, meter: tuple[str, _Row], ledger: _Ledger | None
) -> _Row:
    status, receipt = meter
    charge = (
        _Charge("unavailable")
        if ledger is None
        else ledger.rows.get(call, _Charge("missing"))
    )
    issues = []
    if status in ("invalid", "failed", "unavailable") or (
        status == "missing"
        and (evidence is not None or charge.status not in {"missing", "unavailable"})
    ):
        issues.append(f"meter_{status}")
    if (
        status in ("success", "failed")
        and evidence is not None
        and receipt["prompt_sha256"] != evidence["prompt_sha256"]
    ):
        issues.append("prompt_mismatch")
    if status == "success":
        if evidence is None:
            issues.append("native_turn_missing")
        elif receipt["response_sha256"] != evidence["response_sha256"]:
            issues.append("response_mismatch")
        if charge.status != "settled":
            issues.append(
                "ledger_not_settled" if ledger is not None else "ledger_unavailable"
            )
        else:
            if receipt["served_model"] != charge.model:
                issues.append("model_mismatch")
            hit = receipt["cache_tokens"]
            if charge.usage != (
                hit,
                receipt["input_tokens"] - hit,
                receipt["output_tokens"],
            ):
                issues.append("usage_mismatch")
    return {
        "run": call[0],
        "role": call[1],
        "received_turn": evidence is not None,
        "meter_status": status,
        "ledger_status": charge.status,
        "matched": None if ledger is None else status == "success" and not issues,
        "issues": issues,
    }


def _block(
    study: Path,
    root: int | None,
    study_status: str,
    base: str,
    block: protocol.Block,
    calls: tuple[_Call, ...],
    ledger: _Ledger | None,
) -> _Row:
    status = "missing" if study_status == "missing" else "unsafe"
    meter_status, foreign, records = status, (0 if status == "missing" else None), {}
    report: _Row = {"status": "not_started" if status == "missing" else "unavailable"}
    if root is not None:
        try:
            with _directory(base, root) as parent:
                report = _native_view(study / base / "block", parent, block)
                meter_status, foreign, records = _meter_block(parent, calls)
        except FileNotFoundError:
            meter_status, foreign = "missing", 0
            report = {"status": "not_started"}
        except (OSError, ValueError):
            pass
    default_status = "missing" if meter_status == "missing" else "unavailable"
    turns = report.get("turns", {})
    return {
        "id": base,
        **_public_native(report),
        "meter_status": meter_status,
        "foreign_meter_entries": foreign,
        "calls": [
            _bind(
                call,
                turns.get(":".join(call)),
                records.get(call, (default_status, {})),
                ledger,
            )
            for call in calls
        ],
    }


def analyze(study: Path, spend_dir: Path) -> dict[str, Any]:
    """Read the 12 registered cohorts and their original direct ledger, offline."""
    plan = _plan()
    study = study.absolute()
    expected = {call for _, _, calls in plan for call in calls}
    ledger_status, ledger = _ledger(spend_dir.absolute(), expected)
    study_status = "available"
    try:
        with _directory(study) as root:
            foreign = _unexpected(root, {base for base, _, _ in plan})
            blocks = [_block(study, root, study_status, *item, ledger) for item in plan]
    except (OSError, ValueError, RuntimeError) as error:
        study_status = "missing" if isinstance(error, FileNotFoundError) else "unsafe"
        foreign = 0 if study_status == "missing" else None
        blocks = [_block(study, None, study_status, *item, ledger) for item in plan]

    calls = [call for block in blocks for call in block["calls"]]
    meter = Counter(call["meter_status"] for call in calls)
    matched = sum(call["matched"] is True for call in calls)
    coverage = {
        "expected_blocks": len(plan),
        "expected_calls": len(expected),
        "received_turns": sum(call["received_turn"] for call in calls),
        "meter_files": meter["success"] + meter["failed"] + meter["invalid"],
        "valid_meter_records": meter["success"] + meter["failed"],
        "successful_meter_records": meter["success"],
        "failed_meter_records": meter["failed"],
        "invalid_meter_files": meter["invalid"],
        "unavailable_meter_slots": meter["unavailable"],
        "foreign_study_entries": foreign,
        "foreign_meter_entries": sum(
            block["foreign_meter_entries"] or 0 for block in blocks
        ),
        **{
            key: ledger.counts[key] if ledger is not None else None
            for key in _LEDGER_COUNTS
        },
        "matched_calls": None if ledger is None else matched,
        "unmatched_settled_calls": None
        if ledger is None
        else ledger.counts["settled_calls"] - matched,
    }
    return {
        "schema": "seatlab.calendar-replica-meter/v1",
        "study_status": study_status,
        "ledger_status": ledger_status,
        "coverage": coverage,
        "blocks": blocks,
        "spend": ledger.spend if ledger is not None else None,
        "provider_attested": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("study", type=Path)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.study, args.spend_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
