import argparse
import json
import os
import stat
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ..native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    check_binding,
    native_reward,
    read_bytes,
    require,
    require_usage,
    same,
    seat_map,
    strict_object,
)
from .observation import ROLES, validate_observation
from .study import load_registration

SCHEMA = "harbor.joint-reopen.collection/v1"
STATUSES = (
    "usable",
    "missing",
    "incomplete",
    "ambiguous",
    "duplicate",
    "native_error",
    "invalid",
)
CELL_FIELDS = ("ticket", "team", "seed", "fixture", "channel")
GROUP_FIELDS = ("team", "fixture", "channel")
MAX_ROOTS = 64
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_OBSERVATION_BYTES = 16384
MAX_SMALL_BYTES = 4096
MAX_TIMESTAMP = 64
RESULT = ("result.json",)
CONFIG = ("config.json",)
LOCK = ("lock.json",)
OBSERVATION = ("verifier", "joint-reopen.json")
REWARD = ("verifier", "reward.json")

Claim = tuple[tuple[int, int], Path, bool]


class NativeError(Exception):
    pass


def _environment(cell: Record) -> dict[str, str]:
    return {
        "JR_SEED": str(cell["seed"]),
        "JR_FIXTURE": cell["fixture"],
        "JR_CHANNEL": cell["channel"],
    }


def _check_config(
    config: object, cell: Record, team: dict[str, Record], label: str
) -> None:
    require(
        type(config) is dict
        and cast(Record, config).get("trial_name") == cell["ticket"],
        f"{label} trial_name is not the ticket",
    )
    check_binding(config, _environment(cell), team, ROLES, label)


def _check_lock(
    lock: Record, cell: Record, team: dict[str, Record], task: Record
) -> None:
    version = lock.get("schema_version")
    require(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    require(type(pinned) is dict, "lock.json has no task")
    require(cast(Record, pinned).get("type") == "local", "lock.json task is not local")
    require(
        cast(Record, pinned).get("digest") == task["digest"],
        "lock.json digest differs from the registration",
    )
    check_binding(lock, _environment(cell), team, ROLES, "lock.json")


def _seat_errors(result: Record) -> bool:
    rows = result.get("seat_results")
    require(
        type(rows) is list and len(cast(list[object], rows)) == len(ROLES),
        "native seat results are not two seats",
    )
    seats: set[str] = set()
    failed = False
    for row in cast(list[object], rows):
        require(type(row) is dict, "native seat result is not an object")
        seat_row = cast(Record, row)
        seat = seat_row.get("seat")
        require(
            type(seat) is str and seat not in seats,
            "native seat result repeats a seat",
        )
        seats.add(cast(str, seat))
        require(seat_row.get("under_test") is True, "native seat is not under test")
        require("exception_info" in seat_row, "native seat error record is absent")
        failed = failed or seat_row["exception_info"] is not None
    require(seats == set(ROLES), "native seat results are not east and west")
    return failed


def _native_failure(result: Record, kind: str) -> NativeError:
    standing = "no valid binary standing reward"
    if result.get("reward_valid") is True:
        try:
            native_reward(result)
            standing = "valid binary standing reward not credited"
        except Invalid:
            pass
    return NativeError(f"native {kind} error; {standing}")


def _aware_timestamp(value: object) -> bool:
    if type(value) is not str or len(value) > MAX_TIMESTAMP:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _check_result(
    result: Record, cell: Record, team: dict[str, Record], task: Record
) -> tuple[str, int]:
    version = result.get("schema_version")
    require(
        type(version) is int and version == 1, "result.json is not schema version 1"
    )
    native_id = result.get("id")
    require(canonical_uuid(native_id), "result.json id is not a canonical UUID")
    require(
        result.get("trial_name") == cell["ticket"],
        "result.json trial_name is not the ticket",
    )
    require(
        result.get("task_name") == task["name"],
        "result.json task_name differs from the registration",
    )
    require(
        result.get("task_checksum") == task["checksum"],
        "result.json checksum differs from the registration",
    )
    _check_config(result.get("config"), cell, team, "result.json config")
    finished = result.get("finished_at")
    unfinished = finished is None or finished == ""
    require(
        unfinished or _aware_timestamp(finished),
        "result.json finished_at is not an offset-aware ISO timestamp",
    )
    require("exception_info" in result, "native trial error record is absent")
    if result["exception_info"] is not None:
        raise _native_failure(result, "trial")
    if _seat_errors(result):
        raise _native_failure(result, "seat")
    if unfinished:
        raise Incomplete("native trial has not finished")
    valid = result.get("reward_valid")
    require(type(valid) is bool, "native reward_valid is not a boolean")
    if not valid:
        raise NativeError("native reward is not valid")
    return cast(str, native_id), native_reward(result)


def _evaluate(
    trial: Path, cell: Record, team: dict[str, Record], task: Record
) -> tuple[str, Record]:
    config = strict_object(read_bytes(trial, CONFIG, MAX_CONFIG_BYTES), "config.json")
    _check_config(config, cell, team, "config.json")
    result = strict_object(read_bytes(trial, RESULT, MAX_RESULT_BYTES), "result.json")
    native_id, reward = _check_result(result, cell, team, task)
    lock = strict_object(read_bytes(trial, LOCK, MAX_CONFIG_BYTES), "lock.json")
    _check_lock(lock, cell, team, task)
    published = strict_object(
        read_bytes(trial, OBSERVATION, MAX_OBSERVATION_BYTES),
        "verifier/joint-reopen.json",
    )
    try:
        observation = validate_observation(
            published, cell["seed"], cell["fixture"], cell["channel"]
        )
    except ValueError as error:
        raise Invalid(f"observation: {error}") from None
    recorded = strict_object(
        read_bytes(trial, REWARD, MAX_SMALL_BYTES), "verifier/reward.json"
    )
    require(
        same(recorded, {"reward": observation["reward"]}),
        "verifier/reward.json differs from the observation",
    )
    require(
        observation["reward"] == reward, "native reward differs from the observation"
    )
    require(native_id != observation["run"], "native id repeats the world run")
    return native_id, observation


def _identifiers(trial: Path) -> set[str]:
    found: set[str] = set()
    for parts, limit, key in (
        (RESULT, MAX_RESULT_BYTES, "id"),
        (OBSERVATION, MAX_OBSERVATION_BYTES, "run"),
    ):
        try:
            data = read_bytes(trial, parts, limit)
            value = strict_object(data, "/".join(parts)).get(key)
        except (Incomplete, Invalid):
            continue
        if canonical_uuid(value):
            found.add(cast(str, value))
    return found


def _claims(
    roots: list[Path], tickets: list[str]
) -> tuple[dict[str, list[Claim]], Record]:
    require_usage(1 <= len(roots) <= MAX_ROOTS, f"give 1 to {MAX_ROOTS} trials roots")
    distinct: dict[tuple[int, int], Path] = {}
    for root in roots:
        info = os.stat(root)
        require_usage(stat.S_ISDIR(info.st_mode), f"{root} is not a directory")
        distinct.setdefault((info.st_dev, info.st_ino), root)
    claims: dict[str, list[Claim]] = {}
    for ticket in tickets:
        seen: set[tuple[int, int]] = set()
        for root in distinct.values():
            path = root / ticket
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                continue
            key = (info.st_dev, info.st_ino)
            if key not in seen:
                seen.add(key)
                plain = stat.S_ISDIR(info.st_mode)
                claims.setdefault(ticket, []).append((key, path, plain))
    directories = {
        "roots": len(roots),
        "distinct_roots": len(distinct),
        "claimed_tickets": len(claims),
        "claimed_entries": sum(len(found) for found in claims.values()),
    }
    return claims, directories


def _duplicated(claims: dict[str, list[Claim]]) -> frozenset[str]:
    owners: dict[str, set[tuple[str, tuple[int, int]]]] = {}
    for ticket, found in claims.items():
        for key, path, plain in found:
            for identifier in _identifiers(path) if plain else set():
                owners.setdefault(identifier, set()).add((ticket, key))
    return frozenset(
        ticket for copies in owners.values() if len(copies) > 1 for ticket, _ in copies
    )


def _empty(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def _row(
    cell: Record,
    found: list[Claim],
    duplicated: frozenset[str],
    task: Record,
    team: dict[str, Record],
) -> Record:
    row: Record = {field: cell[field] for field in CELL_FIELDS}
    native_id: str | None = None
    observation: Record | None = None
    reason: str | None = None
    if not found:
        status = "missing"
    elif len(found) > 1:
        status = "ambiguous"
        reason = f"{len(found)} distinct directories claim the ticket"
    elif cell["ticket"] in duplicated:
        status = "duplicate"
        reason = "a native or world id repeats in another directory"
    elif not found[0][2]:
        status, reason = "invalid", "ticket entry is not a plain directory"
    elif _empty(found[0][1]):
        status, reason = "incomplete", "trial directory is empty"
    else:
        try:
            native_id, observation = _evaluate(found[0][1], cell, team, task)
            status = "usable"
        except Incomplete as error:
            status, reason = "incomplete", str(error)
        except NativeError as error:
            status, reason = "native_error", str(error)
        except Invalid as error:
            status, reason = "invalid", str(error)
    assert status in STATUSES and (status == "usable") == (observation is not None)
    row.update(
        status=status, reason=reason, native_id=native_id, observation=observation
    )
    return row


def _groups(rows: list[Record]) -> list[Record]:
    groups: dict[tuple[Any, ...], Record] = {}
    for row in rows:
        key = tuple(row[field] for field in GROUP_FIELDS)
        group = groups.setdefault(
            key,
            {**dict(zip(GROUP_FIELDS, key)), "cells": 0, "usable": 0, "rewarded": 0},
        )
        group["cells"] += 1
        if row["observation"] is not None:
            group["usable"] += 1
            group["rewarded"] += row["observation"]["reward"]
    return [groups[key] for key in sorted(groups)]


def collect_registered(registration: Record, roots: list[Path]) -> Record:
    task = registration["task"]
    cells = registration["cells"]
    tickets = [cell["ticket"] for cell in cells]
    claims, directories = _claims(roots, tickets)
    duplicated = _duplicated(claims)
    teams = {
        label: seat_map(members, f"team {label}", ROLES)
        for label, members in registration["teams"].items()
    }
    rows = []
    for cell in cells:
        found = claims.get(cell["ticket"], [])
        rows.append(_row(cell, found, duplicated, task, teams[cell["team"]]))
    assert len(rows) == len(cells)
    statuses = Counter(row["status"] for row in rows)
    return {
        "schema": SCHEMA,
        "task": {key: task[key] for key in ("name", "digest", "checksum")},
        "registered": len(rows),
        "usable": statuses["usable"],
        "statuses": {status: statuses[status] for status in STATUSES},
        "directories": directories,
        "groups": _groups(rows),
        "cells": rows,
    }


def collect(registration_path: Path, roots: list[Path]) -> Record:
    return collect_registered(load_registration(registration_path), roots)


def _tsv(rows: list[Record]) -> list[str]:
    assert rows
    header = list(rows[0])
    lines = ["\t".join(header)]
    lines.extend(
        "\t".join("-" if row[key] is None else str(row[key]) for key in header)
        for row in rows
    )
    return lines


def render(report: Record) -> str:
    totals = {
        "registered": report["registered"],
        **report["statuses"],
        **report["directories"],
    }
    tickets = [
        {
            **{field: row[field] for field in CELL_FIELDS},
            "status": row["status"],
            "reward": (
                None if row["observation"] is None else row["observation"]["reward"]
            ),
            "reason": row["reason"],
        }
        for row in report["cells"]
    ]
    sections = [_tsv([totals]), _tsv(report["groups"]), _tsv(tickets)]
    return "\n\n".join("\n".join(section) for section in sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m examples.joint_reopen.collect")
    parser.add_argument("registration", type=Path)
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIAL_ROOT")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = collect(args.registration, args.roots)
    except (OSError, ValueError, Invalid) as error:
        print(f"collect: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
