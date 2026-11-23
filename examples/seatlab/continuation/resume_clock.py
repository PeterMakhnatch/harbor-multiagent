from __future__ import annotations

import argparse
import errno
import json
import math
import os
import re
import stat
import sys
from datetime import UTC, datetime
from typing import Any, NoReturn

SCHEMA = "seatlab.continuation-resume-clock/v2"
PROG = "python3.12 -BS -m examples.seatlab.continuation.resume_clock"
SOURCE_COMMIT = "dbec43ff757c86c17077e61b37c7f584c880bdee"
LIMITS = {"office.json": 64 << 20, "live.json": 1 << 20, "manifest.json": 1 << 20}
STATE_DIR = "/var/lib/seedprofit-hub"
FORMATS = (1, 2)
MIN_LEFT_S = 120.0
CREATED_RESOLUTION_S = 1.0
OFFICE_DEFAULT_MONTHS = 37
PUBLISHED = (3600.0, 283.78, 37, 14400.0)
LAUNCH_SHARE = 0.25
LAST_MONTH_MARGIN_S = 300.0
CREATED = "%Y-%m-%dT%H:%M:%SZ"
UNIX = re.compile(r"[0-9]{1,12}(\.[0-9]{1,9})?")
OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
NOT_APPLICABLE = {
    "market_ended",
    "office_state_not_in_checkpoint",
    "harness_refuses_resume",
}
MISMATCH = {"prior_lag", "outage", "prior_lag_and_outage"}
EXIT = {
    "no_mismatch_observed": 0,
    "mismatch_observed": 1,
    "inconclusive": 3,
    "not_applicable": 3,
}
SNAPSHOT_RULES = {
    "caller_supplied": "owner attests --snapshot-at as the instant office.json was packed",
    "manifest_created_bound": "upper bound: whole-second manifest created + 1 s, written after the pack",
}
EPILOG = (
    "Times are Unix seconds (UTC epoch, e.g. `date -u +%s`).\n"
    "Exit 0 no_mismatch_observed, 1 mismatch_observed, 3 inconclusive or not_applicable "
    "(never a pass), 2 input rejected.\n"
    f"Clock: tasks/seed-to-profit/task/environment/hub/market_plugin.py L167-L215 at {SOURCE_COMMIT}.\n"
    "Agent time: harness/src/multi_agent_harness/harbor/checkpoint.py L193-L197, L231-L234."
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in out, "duplicate JSON key")
        out[key] = value
    return out


def _read(name: str, path: str) -> tuple[dict[str, Any], tuple[int, int]]:
    _require(
        os.path.basename(path) == name, f"{name}: path must name a file called {name}"
    )
    limit = LIMITS[name]
    try:
        fd = os.open(path, OPEN_FLAGS)
    except FileNotFoundError:
        raise ValueError(f"{name}: missing") from None
    except OSError as exc:
        raise ValueError(
            f"{name}: {'is a symbolic link' if exc.errno == errno.ELOOP else 'cannot be opened'}"
        ) from None
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode), f"{name}: not a regular file")
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            chunk = os.read(fd, limit + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        raise ValueError(f"{name}: cannot be read") from None
    finally:
        os.close(fd)
    _require(size <= limit, f"{name}: exceeds {limit} bytes")
    try:
        document = json.loads(
            b"".join(chunks).decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise ValueError(f"{name}: not strict finite JSON") from None
    _require(type(document) is dict, f"{name}: not a JSON object")
    return document, (info.st_dev, info.st_ino)


def _num(value: Any, label: str, positive: bool = False) -> float:
    _require(type(value) in (int, float), f"{label} must be a non-boolean JSON number")
    number = float(value)
    _require(
        math.isfinite(number) and number >= 0,
        f"{label} must be finite and not negative",
    )
    _require(not positive or number > 0, f"{label} must be positive")
    return number


def _unset(value: Any) -> bool:
    return value is None or (type(value) in (int, float) and value == 0)


def _unix(text: str, label: str) -> float:
    _require(
        UNIX.fullmatch(text) is not None,
        f"{label} must be Unix seconds, e.g. `date -u +%s`",
    )
    return float(text)


def _manifest(doc: dict[str, Any], task: str) -> dict[str, Any]:
    _require(
        type(doc.get("format")) is int and doc["format"] in FORMATS,
        "manifest.json: format must be 1 or 2",
    )
    _require(
        type(doc.get("task")) is str and doc["task"] == task,
        "manifest.json: task differs from --task",
    )
    raw = doc.get("agent_timeout_sec")
    timeout = (
        None
        if _unset(raw)
        else _num(raw, "manifest.json: agent_timeout_sec", positive=True)
    )
    elapsed = _num(doc.get("elapsed_agent_sec"), "manifest.json: elapsed_agent_sec")
    created = doc.get("created")
    _require(
        created is None or type(created) is str,
        "manifest.json: created must be a string",
    )
    stamp = None
    if created is not None:
        try:
            stamp = datetime.strptime(created, CREATED).replace(tzinfo=UTC).timestamp()
        except ValueError:
            raise ValueError(
                "manifest.json: created is not YYYY-MM-DDTHH:MM:SSZ"
            ) from None
    hub = doc.get("hub_state")
    _require(
        hub is None or (type(hub) is list and all(type(d) is str for d in hub)),
        "manifest.json: hub_state must be a list of folders",
    )
    return {
        "timeout": timeout,
        "elapsed": elapsed,
        "created": stamp,
        "saved": hub is not None and STATE_DIR in hub,
    }


def _live(doc: dict[str, Any]) -> dict[str, Any]:
    launch = _num(doc.get("launch_sec"), "live.json: launch_sec")
    month = _num(doc.get("month_sec"), "live.json: month_sec", positive=True)
    defaulted = "months" not in doc
    months = OFFICE_DEFAULT_MONTHS if defaulted else doc["months"]
    _require(
        type(months) is int and 1 <= months <= 1000,
        "live.json: months must be an integer from 1 to 1000",
    )
    return {"launch": launch, "month": month, "months": months, "defaulted": defaulted}


def _office(doc: dict[str, Any], months: int) -> dict[str, Any]:
    created = _num(doc.get("created_at"), "office.json: created_at", positive=True)
    raw = doc.get("run_started_at")
    started = None if _unset(raw) else _num(raw, "office.json: run_started_at")
    _require(
        started is None or started >= created,
        "office.json: run_started_at is before created_at",
    )
    done = doc.get("live_months", 0)
    _require(
        type(done) is int and 0 <= done <= months,
        "office.json: live_months is not an integer within months",
    )
    ended = doc.get("live_ended", False)
    _require(type(ended) is bool, "office.json: live_ended must be a boolean")
    return {"created": created, "started": started, "done": done, "ended": ended}


def _shape(live: dict[str, Any], timeout: float | None) -> str:
    if (live["launch"], live["month"], live["months"], timeout) == PUBLISHED:
        return "published_4h"
    run = live["launch"] / LAUNCH_SHARE
    rule = (
        round((run - live["launch"] - LAST_MONTH_MARGIN_S) / live["months"], 2)
        == live["month"]
    )
    return "scaled" if rule and timeout == run else "unmatched"


def _snapshot(created: float | None, supplied: float | None) -> tuple[float, str]:
    bound = None if created is None else created + CREATED_RESOLUTION_S
    if supplied is not None:
        _require(
            bound is None or supplied < bound,
            "--snapshot-at is after manifest.json was written",
        )
        return supplied, "caller_supplied"
    if bound is None:
        raise ValueError("manifest.json records no created instant; pass --snapshot-at")
    return bound, "manifest_created_bound"


def _market(
    live: dict[str, Any], office: dict[str, Any], snap: float, resume: float
) -> dict[str, Any]:
    start = office["started"] + live["launch"]
    months = live["months"]

    def due(now: float) -> int:
        return (
            0 if now < start else min(months, int((now - start) // live["month"]) + 1)
        )

    _require(
        snap >= office["started"], "snapshot_at is before the office clock started"
    )
    at_snap, at_resume, done = due(snap), due(resume), office["done"]
    _require(
        done <= at_snap, "office.json ran more months than were due at snapshot_at"
    )
    backlog, extra = at_snap - done, at_resume - at_snap
    if at_resume == done:
        kind = "none"
    elif extra == 0:
        kind = "normal_carryover" if backlog == 1 else "prior_lag"
    else:
        kind = "outage" if backlog <= 1 else "prior_lag_and_outage"
    return {
        "months_in_all": months,
        "months_run_at_snapshot": done,
        "due_at_snapshot": at_snap,
        "due_at_restart": at_resume,
        "backlog_at_snapshot": backlog,
        "extra_due_across_interval": extra,
        "overdue_at_restart": at_resume - done,
        "restart_in_launch_phase": resume < start,
        "backlog_kind": kind,
    }


def _reasons(
    manifest: dict[str, Any], live: dict[str, Any], office: dict[str, Any], shape: str
) -> list[str]:
    left = (
        None
        if manifest["timeout"] is None
        else manifest["timeout"] - manifest["elapsed"]
    )
    checks = (
        ("office_state_not_in_checkpoint", not manifest["saved"]),
        ("market_ended", office["ended"] or office["done"] == live["months"]),
        ("harness_refuses_resume", left is not None and left < MIN_LEFT_S),
        ("clock_not_started", office["started"] is None),
        ("agent_timeout_absent", manifest["timeout"] is None),
        ("months_defaulted", live["defaulted"]),
        ("clock_shape_unmatched", shape == "unmatched"),
    )
    return [code for code, hit in checks if hit]


def evaluate(
    task: str, paths: dict[str, str], resume: float, supplied: float | None
) -> dict[str, Any]:
    _require(0 < len(task) <= 256, "--task must be 1 to 256 characters")
    loaded = {name: _read(name, path) for name, path in paths.items()}
    _require(
        len({identity for _, identity in loaded.values()}) == 3,
        "the 3 paths must be 3 different files",
    )
    manifest = _manifest(loaded["manifest.json"][0], task)
    live = _live(loaded["live.json"][0])
    office = _office(loaded["office.json"][0], live["months"])
    snap, source = _snapshot(manifest["created"], supplied)
    _require(resume >= snap, "--resume-at is before snapshot_at")
    _require(snap >= office["created"], "snapshot_at is before the office was created")
    shape = _shape(live, manifest["timeout"])
    reasons = _reasons(manifest, live, office, shape)
    market = None
    if not set(reasons) & (NOT_APPLICABLE | {"clock_not_started"}):
        market = _market(live, office, snap, resume)
        bounded = source == "manifest_created_bound"
        if bounded and market["backlog_kind"] in ("normal_carryover", "prior_lag"):
            reasons.append("snapshot_split_uncertain")
        if bounded and market["backlog_kind"] == "prior_lag_and_outage":
            market["backlog_kind"] = "outage"
    if set(reasons) & NOT_APPLICABLE:
        verdict = "not_applicable"
    elif reasons:
        verdict = "inconclusive"
    elif market is not None and market["backlog_kind"] in MISMATCH:
        verdict = "mismatch_observed"
    else:
        verdict = "no_mismatch_observed"
    timeout, elapsed = manifest["timeout"], manifest["elapsed"]
    return {
        "schema": SCHEMA,
        "source_commit": SOURCE_COMMIT,
        "verdict": verdict,
        "reasons": reasons,
        "assumptions": {
            "time_input": "unix_seconds_utc",
            "snapshot_at": SNAPSHOT_RULES[source],
            "months": "due by the restored office clock; not months run or lost",
        },
        "agent": {
            "timeout_s": timeout,
            "elapsed_s": elapsed,
            "remaining_s": None if timeout is None else round(timeout - elapsed, 1),
        },
        "clock_shape": shape,
        "interval_s": round(resume - snap, 3),
        "market": market,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Check a saved Seed to Profit office against its market clock at a planned restart.",
        epilog=EPILOG,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--task",
        required=True,
        help="owner-attested task name; must equal manifest.json task",
    )
    parser.add_argument(
        "--resume-at",
        required=True,
        metavar="UNIX_SECONDS",
        help="planned restart instant",
    )
    parser.add_argument(
        "--snapshot-at", metavar="UNIX_SECONDS", help="instant office.json was packed"
    )
    for name in ("office", "live", "manifest"):
        parser.add_argument(name, metavar=f"{name.upper()}_JSON")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    paths = {
        "office.json": args.office,
        "live.json": args.live,
        "manifest.json": args.manifest,
    }
    try:
        resume = _unix(args.resume_at, "--resume-at")
        supplied = (
            None
            if args.snapshot_at is None
            else _unix(args.snapshot_at, "--snapshot-at")
        )
        result = evaluate(args.task, paths, resume, supplied)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    sys.stdout.buffer.write((text + "\n").encode())
    sys.stdout.buffer.flush()
    return EXIT[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
