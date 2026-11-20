"""Reconcile the box ownership host receipts and local spend ledger offline."""

import argparse
import json
import math
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Generator
from contextlib import ExitStack, closing, contextmanager
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import (
    boxes_choice,
    boxes_packet,
    boxes_report,
    boxes_run,
    clarify_run,
    intent_packet,
    intent_protocol,
    ownership_driver,
    ownership_protocol,
    spend,
)

_SEATS = ownership_driver.SEATS
_PREFIX = "bo-"
_LEDGER = "direct-flash.sqlite3"
_MAX_LEDGER_BYTES = 32 * 1024 * 1024
_MAX_TURN_BYTES = 6 * boxes_run.MAX_RESPONSE_BYTES + 4096
_PREFIX_WRITES = {1: (6, 9), 2: (6, 7), 6: (7, 9)}
_METER_KEYS = {
    "schema",
    "run",
    "role",
    "seat",
    "prompt_sha256",
    "response_sha256",
    "served_model",
    "input",
    "cached",
    "output",
}
_COUNTS = (
    "expected_calls",
    "native_receipts",
    "received_calls",
    "grammar_invalid_calls",
    "matched_calls",
    "unmatched_receipts",
    "ledger_rows",
    "settled_calls",
    "unsettled_calls",
    "unmatched_rows",
    "unexpected_rows",
    "invalid_price_rows",
)
_WINDOW_STATES = ("confirmed", "violated", "unknown")
_BAD_READ = (OSError, ValueError, RecursionError)


@contextmanager
def _root(path: Path) -> Generator[tuple[int | None, str]]:
    with ExitStack() as stack:
        try:
            path = path.expanduser().absolute()
            boxes_run._require_absolute(path)
            directory = stack.enter_context(boxes_run._directory(path))
            boxes_run._require_private(os.fstat(directory), directory=True)
        except FileNotFoundError:
            yield None, "missing"
        except _BAD_READ:
            yield None, "invalid"
        else:
            yield directory, "available"


@contextmanager
def _child(parent: int, name: str) -> Generator[int]:
    fd = os.open(name, boxes_run._DIRECTORY, dir_fd=parent)
    try:
        boxes_run._require_private(os.fstat(fd), directory=True)
        yield fd
    finally:
        os.close(fd)


def _signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_ctime_ns,
        info.st_mtime_ns,
        info.st_mode,
    )


def _read(directory: int, names: tuple[str, ...], limit: int) -> bytes:
    with ExitStack() as stack:
        for name in names[:-1]:
            directory = stack.enter_context(_child(directory, name))
        file = stack.enter_context(clarify_run._regular(directory, names[-1], 0o600))
        before = os.fstat(file.fileno())
        if before.st_size > limit:
            raise ValueError("file_too_large")
        data = file.read(limit + 1)
        if len(data) != before.st_size or _signature(before) != _signature(
            os.fstat(file.fileno())
        ):
            raise ValueError("file_changed")
        return data


def _check_sidecars(directory: int) -> None:
    for extension in ("-journal", "-wal", "-shm"):
        try:
            os.stat(_LEDGER + extension, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise ValueError("ledger_not_quiescent")


def _price(counts: Counter[str], row: sqlite3.Row, *, expected: bool) -> None:
    counts["ledger_rows"] += 1
    counts["unexpected_rows"] += not expected
    low, high = row["cost_low"], row["cost_high"]
    if high is None:
        counts["unsettled_calls"] += 1
        counts["ledger_hold"] += spend._HOLD
        counts["invalid_price_rows"] += low is not None
    else:
        counts["settled_calls"] += 1
        if boxes_report._count(low) and boxes_report._count(high) and low <= high:
            # Local charges stay in the totals even if evidence fails to match.
            counts["ledger_low"] += low
            counts["ledger_high"] += high
        else:
            counts["invalid_price_rows"] += 1


def _ledger(
    spend_dir: Path, calls: dict[str, str]
) -> tuple[str, dict[str, sqlite3.Row], dict[str, Counter[str]], Counter[str]]:
    rows: dict[str, sqlite3.Row] = {}
    counts: dict[str, Counter[str]] = {}
    outside: Counter[str] = Counter()
    with _root(spend_dir) as (directory, status):
        if directory is None:
            return status, rows, counts, outside
        try:
            # A bounded copy from a no-follow descriptor lets SQLite read the exact
            # checked bytes, without opening any untrusted paths or creating sidecars.
            _check_sidecars(directory)
            data = _read(directory, (_LEDGER,), _MAX_LEDGER_BYTES)
            _check_sidecars(directory)
            with closing(sqlite3.connect(":memory:")) as db:
                db.deserialize(data)
                db.execute("PRAGMA trusted_schema = OFF")
                if not any(
                    entry[1:3] == ("calls", "table")
                    for entry in db.execute("PRAGMA main.table_list('calls')")
                ):
                    raise ValueError("ledger_table_invalid")
                db.row_factory = sqlite3.Row
                for row in db.execute(
                    """SELECT run, role, reserved_at, started_at, finished_at,
                              served_model, hit, miss, output, cost_low, cost_high
                       FROM calls WHERE run GLOB ?""",
                    (_PREFIX + "*",),
                ):
                    run = calls.get(row["run"])
                    if run is None:
                        _price(outside, row, expected=False)
                        continue
                    arm = counts.setdefault(run, Counter())
                    expected = row["role"] == ownership_driver.ROLE
                    _price(arm, row, expected=expected)
                    if expected:
                        # A real ledger enforces this identity as its primary key.
                        if row["run"] in rows:
                            raise ValueError("ledger_identity_duplicate")
                        rows[row["run"]] = row
        except FileNotFoundError:
            return "missing", {}, {}, Counter()
        except (*_BAD_READ, sqlite3.Error, AttributeError):
            return "invalid", {}, {}, Counter()
    return "available", rows, counts, outside


def _turn(
    directory: int, run: str, seat: str, prompt: str, numbering: int
) -> tuple[str, dict[str, Any] | None]:
    try:
        turn = clarify_run._object(
            _read(directory, ("turns", f"{seat}.json"), _MAX_TURN_BYTES)
        )
        text = turn.get("response_text")
        if not isinstance(text, str):
            return "invalid", None
        raw = text.encode()
        if len(raw) > boxes_run.MAX_RESPONSE_BYTES:
            return "invalid", None
        response = sha256(raw).hexdigest()
        if turn != {
            "schema": ownership_driver.RECEIPT,
            "run": run,
            "seat": seat,
            "prompt_sha256": prompt,
            "response_sha256": response,
            "response_text": text,
        }:
            return "invalid", None
        try:
            box = boxes_choice.decode(text, numbering)
        except (ValueError, RecursionError):
            box = None
        return "valid", {
            "prompt_sha256": prompt,
            "response_sha256": response,
            "original_box": box,
        }
    except FileNotFoundError:
        return "missing", None
    except _BAD_READ:
        return "invalid", None


def _meter(
    directory: int, call: str, seat: str, native: dict[str, Any] | None
) -> tuple[str, dict[str, Any] | None]:
    try:
        meter = clarify_run._object(_read(directory, ("meter", f"{seat}.json"), 4096))
    except FileNotFoundError:
        return "missing", None
    except _BAD_READ:
        return "invalid", None
    if (
        meter.keys() != _METER_KEYS
        or meter["schema"] != ownership_driver.METER
        or meter["run"] != call
        or meter["seat"] != seat
        or meter["role"] != ownership_driver.ROLE
        or not boxes_report._model(meter["served_model"])
        or not all(
            boxes_report._count(meter[key]) for key in ("input", "cached", "output")
        )
        or meter["cached"] > meter["input"]
    ):
        return "invalid", None
    if native is None or any(
        meter[key] != native[key] for key in ("prompt_sha256", "response_sha256")
    ):
        return "unbound", None
    hit, miss, output = (
        meter["cached"],
        meter["input"] - meter["cached"],
        meter["output"],
    )
    low = hit * 3 + miss * 150 + output * 600
    if low > spend._HOLD:
        return "invalid", None
    return "verified", {
        **native,
        "served_model": meter["served_model"],
        "cache_hit": hit,
        "cache_miss": miss,
        "output": output,
        "estimated_usd_low": spend._usd(low),
        "estimated_usd_high": spend._usd(2 * low),
    }


def _reconcile(
    row: sqlite3.Row | None, receipt: dict[str, Any] | None, status: str
) -> str:
    if row is None:
        return "missing_ledger_row" if status == "available" else "ledger_unavailable"
    try:
        if boxes_report._spend_state(row, available=True) == "held":
            return "unsettled"
    except ValueError:
        return "invalid_ledger_row"
    if receipt is None:
        return "missing_verified_receipt"
    if (row["served_model"], row["hit"], row["miss"], row["output"]) != (
        receipt["served_model"],
        receipt["cache_hit"],
        receipt["cache_miss"],
        receipt["output"],
    ):
        return "usage_or_model_mismatch"
    return "matched"


def _service_boundaries(
    directory: int, run: str, world: intent_packet.World, choices: dict[str, int]
) -> tuple[float, float]:
    data = _read(
        directory,
        ("trials", run, "verifier", "events.jsonl"),
        boxes_packet._NATIVE_LIMIT,
    )
    opens, messages = _PREFIX_WRITES[world.instance]
    writes: list[tuple[str, str, object, float]] = []
    closed = False
    previous = -math.inf
    for sequence, line in enumerate(
        (row for row in data.splitlines() if row.strip()), 1
    ):
        event = boxes_packet._decode(line)
        if not isinstance(event, dict) or not boxes_packet._integer(
            event.get("seq"), sequence, sequence
        ):
            raise ValueError("event_sequence_invalid")
        time = event.get("t")
        if (
            type(time) not in (int, float)
            or not 0 < time <= 253_402_300_799
            or time < previous
        ):
            raise ValueError("event_clock_invalid")
        previous = time
        status, ok = event.get("status"), event.get("ok")
        if (
            type(status) is not int
            or (status != 200 and not 400 <= status <= 599)
            or ok is not (status == 200)
        ):
            raise ValueError("event_status_invalid")
        if not ok:
            continue
        if closed:
            raise ValueError("event_after_close")
        kind = event.get("kind")
        if kind == "admin.close":
            if event.get("method") != "POST" or event.get("seat") is not None:
                raise ValueError("event_close_invalid")
            closed = True
        elif kind in ("open", "msg"):
            seat, request = event.get("seat"), event.get("request")
            if (
                seat not in _SEATS
                or event.get("method") != "POST"
                or not isinstance(request, dict)
            ):
                raise ValueError("event_write_invalid")
            writes.append((kind, seat, request.get("box"), time))
        elif (
            kind not in ("mine", "rules", "msgs")
            or event.get("method") != "GET"
            or event.get("seat") not in _SEATS
        ):
            raise ValueError("event_read_invalid")
    cut = opens + messages
    if (
        not closed
        or len(writes) != cut + len(_SEATS)
        or sum(row[0] == "open" for row in writes[:cut]) != opens
        or any(
            kind != "open"
            or actual != seat
            or type(box) is not int
            or box != choices[seat]
            for (kind, actual, box, _), seat in zip(writes[cut:], _SEATS, strict=True)
        )
    ):
        raise ValueError("event_boundary_invalid")
    # The pinned packet fixes the old open choices by seat. The private
    # messages matter here only as successful mutations in service order.
    for seat, case in zip(_SEATS, world.seats, strict=True):
        restored = [
            box
            for kind, actor, box, _ in writes[:cut]
            if kind == "open" and actor == seat
        ]
        if not all(type(box) is int for box in restored) or restored != [
            box for box, _, _ in case.own
        ]:
            raise ValueError("event_prefix_open_mismatch")
    return writes[cut - 1][3], writes[cut][3]


def _native_window(
    directory: int | None,
    run: str,
    world: intent_packet.World,
    turns: list[dict[str, Any]],
    rows: dict[str, sqlite3.Row],
) -> dict[str, str]:
    if directory is None or any(turn["match"] != "matched" for turn in turns):
        return {"status": "unknown", "reason": "joint_choice_not_fully_matched"}
    if any(turn["direct"]["original_box"] is None for turn in turns):
        return {"status": "unknown", "reason": "model_invalid_no_new_open"}
    choices = {turn["seat"]: turn["direct"]["original_box"] for turn in turns}
    try:
        prefix, first_open = _service_boundaries(directory, run, world, choices)
    except FileNotFoundError:
        return {"status": "unknown", "reason": "native_events_missing"}
    except _BAD_READ:
        return {"status": "unknown", "reason": "native_chronology_invalid"}

    # If a stock result is present, its host clock should enclose the Docker
    # event times. Neither this check nor the comparison proves clock sync.
    try:
        result = clarify_run._object(
            _read(directory, ("trials", run, "result.json"), boxes_packet._NATIVE_LIMIT)
        )
        started, finished = result.get("started_at"), result.get("finished_at")
        if (
            result.get("trial_name") != run
            or (
                started is not None
                and boxes_report._timestamp(started).timestamp() > prefix
            )
            or (
                finished is not None
                and boxes_report._timestamp(finished).timestamp() < first_open
            )
        ):
            return {"status": "unknown", "reason": "native_clock_inconsistent"}
    except FileNotFoundError:
        pass
    except (*_BAD_READ, OverflowError):
        return {"status": "unknown", "reason": "native_chronology_invalid"}

    stamps = ("reserved_at", "started_at", "finished_at")
    intervals = [
        tuple(
            boxes_report._timestamp(rows[turn["call"]][key]).timestamp()
            for key in stamps
        )
        for turn in turns
    ]
    if any(a[2] > b[0] for a, b in pairwise(intervals)):
        return {"status": "violated", "reason": "within_arm_call_order"}
    if any(start < prefix or finish > first_open for _, start, finish in intervals):
        return {"status": "violated", "reason": "generation_outside_native_window"}
    if prefix == first_open or any(
        start == prefix or finish == first_open for _, start, finish in intervals
    ):
        return {"status": "unknown", "reason": "equal_clock_boundaries"}
    return {"status": "confirmed", "reason": "within_observed_native_window"}


def _summarize(counts: Counter[str], ledger_status: str) -> dict[str, Any]:
    direct = None
    if counts["received_calls"]:
        direct = {
            "cache_hit": counts["direct_cache_hit"],
            "cache_miss": counts["direct_cache_miss"],
            "output": counts["direct_output"],
            "estimated_usd_low": spend._usd(counts["direct_low"]),
            "estimated_usd_high": spend._usd(counts["direct_high"]),
        }
    ledger = None
    if ledger_status == "available":
        ledger = {
            "known_usd_low": spend._usd(counts["ledger_low"]),
            "known_usd_high": spend._usd(counts["ledger_high"]),
            "unsettled_hold_usd": spend._usd(counts["ledger_hold"]),
            "upper_usd_including_holds": (
                None
                if counts["invalid_price_rows"]
                else spend._usd(counts["ledger_high"] + counts["ledger_hold"])
            ),
        }
    return {
        "counts": {name: counts[name] for name in _COUNTS},
        "direct_usage": direct,
        "ledger_cost": ledger,
    }


def _arm(
    directory: int | None,
    block: intent_protocol.Block,
    arm: str,
    run: str,
    world: intent_packet.World,
    proposals: intent_protocol.Choices,
    rows: dict[str, sqlite3.Row],
    ledger_status: str,
    counts: Counter[str],
    arm_status: str,
) -> dict[str, Any]:
    turns = []
    for label, name in enumerate(intent_protocol.LABELS):
        seat = _SEATS[block.seat_index(label)]
        call = f"{run}-{seat}"
        native_status, native = arm_status, None
        meter_status, direct = arm_status, None
        if directory is not None:
            prompt = sha256(
                ownership_protocol.prompt(world, block, label, arm, proposals).encode()
            ).hexdigest()
            native_status, native = _turn(directory, run, seat, prompt, block.numbering)
            meter_status, direct = _meter(directory, call, seat, native)
        match = _reconcile(rows.get(call), direct, ledger_status)
        counts["expected_calls"] += 1
        counts["native_receipts"] += native is not None
        counts["matched_calls"] += match == "matched"
        counts["unmatched_rows"] += call in rows and match != "matched"
        if direct is not None:
            counts["received_calls"] += 1
            counts["unmatched_receipts"] += match != "matched"
            counts["grammar_invalid_calls"] += direct["original_box"] is None
            hit, miss, output = (
                direct[k] for k in ("cache_hit", "cache_miss", "output")
            )
            for key, number in (
                ("cache_hit", hit),
                ("cache_miss", miss),
                ("output", output),
            ):
                counts[f"direct_{key}"] += number
            low = hit * 3 + miss * 150 + output * 600
            counts["direct_low"] += low
            counts["direct_high"] += 2 * low
        turns.append(
            {
                "call": call,
                "label": name,
                "seat": seat,
                "native_receipt": native_status,
                "meter_receipt": meter_status,
                "direct": direct,
                "match": match,
            }
        )
    return {
        "run": run,
        "block": block.id,
        "instance": block.instance,
        "arm": arm,
        "turns": turns,
        "native_generation_window": _native_window(directory, run, world, turns, rows),
        **_summarize(counts, ledger_status),
    }


@contextmanager
def _arm_directory(
    root: int | None, run: str, status: str
) -> Generator[tuple[int | None, str]]:
    with ExitStack() as stack:
        if root is not None:
            try:
                yield_dir = stack.enter_context(_child(root, run))
            except FileNotFoundError:
                status = "missing"
            except _BAD_READ:
                status = "invalid"
            else:
                yield yield_dir, "available"
                return
        yield None, status


def analyze(study: Path, spend_dir: Path) -> dict[str, Any]:
    """Return all registered arm costs without contacting a provider or a game."""
    seeds, worlds = ownership_protocol.seeds(), intent_packet.load()
    plan = [
        (block, arm, ownership_protocol.run_id(block, arm))
        for block in intent_protocol.blocks()
        for arm in ownership_protocol.arms(block)
    ]
    calls = {f"{run}-{seat}": run for _, _, run in plan for seat in _SEATS}
    ledger_status, rows, ledger_counts, outside = _ledger(spend_dir, calls)
    cohort: Counter[str] = Counter(outside)
    windows: Counter[str] = Counter()
    arms = []
    with _root(study) as (root, study_status):
        for block, arm, run in plan:
            counts = ledger_counts.get(run, Counter())
            with _arm_directory(root, run, study_status) as (directory, arm_status):
                result = _arm(
                    directory,
                    block,
                    arm,
                    run,
                    worlds[block.instance],
                    seeds[block.id],
                    rows,
                    ledger_status,
                    counts,
                    arm_status,
                )
            arms.append(result)
            cohort.update(counts)
            windows[result["native_generation_window"]["status"]] += 1
    return {
        "schema": "seatlab.box-ownership-meter/v1",
        "currency": "USD",
        "provider_attested": False,
        "basis": (
            "Direct usage comes from verified local reply receipts. The separate local ledger "
            "includes unmatched costs and unresolved holds. Neither is a provider invoice. "
            "Missing direct usage is unknown, including non-metered native turns."
        ),
        "timing_basis": (
            "The actor requests A, B, and C after the restored writes and before any new open. "
            "The timing check compares the host ledger clock with the native service clock. "
            "It does not prove clock synchronization, program execution, or provider identity."
        ),
        "study_status": study_status,
        "ledger_status": ledger_status,
        **_summarize(cohort, ledger_status),
        "native_generation_window": {name: windows[name] for name in _WINDOW_STATES},
        "arms": arms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--spend-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.study, args.spend_dir)
    except _BAD_READ:
        print("ownership meter: registered inputs unavailable", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
