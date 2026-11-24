import argparse
import json
import os
import stat
import sys
import types
from collections import Counter
from pathlib import Path

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
from .owner_evidence import inspect_owners
from .plan import load_manifest

SCHEMA = "harbor.shift-board.collection/v1"
IDENTITY_SCHEMA = "harbor.shift-board.world/v1"
VERIFIER_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "tasks"
    / "shift-board"
    / "tests"
    / "verify.py"
)
VERIFIER_NAMES = ("evaluate", "parse_cut", "CutError", "MAX_CUT_BYTES")
LOCK_TASK_NAME = "shift-board"
SEATS = ("planner", "dispatcher")
RULES = ("observe", "guard")
STATUSES = ("usable", "missing", "duplicate", "quarantined", "incomplete", "invalid")
CELL_FIELDS = ("ticket", "pair", "seed", "fixture", "rule", "team", "repeat")
OBSERVATION_FIELDS = (
    "prepared",
    "incumbent_correct",
    "draft_correct",
    "attempts",
    "stale_attempts",
    "stale_refusals",
    "first_stale_current_returned",
    "first_stale_effect",
    "final_action",
    "final_correct",
    "reward",
    "observed_change",
)
IDENTITY_KEYS = frozenset(("schema", "run", "ticket"))
MAX_ROOTS = 64
MAX_ROOT_ENTRIES = 1024
MAX_ENTRIES = 2048
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_SMALL_BYTES = 4096
RESULT = ("result.json",)
CONFIG = ("config.json",)
LOCK = ("lock.json",)
IDENTITY = ("artifacts", "logs", "world", "shift-board-id.json")
CUT = ("verifier", "shift-board-cut.json")
OBSERVATION = ("verifier", "shift-board.json")
REWARD = ("verifier", "reward.json")


def load_verifier() -> types.ModuleType:
    assert VERIFIER_SOURCE.is_absolute()
    source = VERIFIER_SOURCE.read_bytes()
    module = types.ModuleType("shift_board_trusted_verify")
    module.__file__ = str(VERIFIER_SOURCE)
    exec(compile(source, str(VERIFIER_SOURCE), "exec"), module.__dict__)
    missing = [name for name in VERIFIER_NAMES if not hasattr(module, name)]
    if missing:
        raise ValueError(f"trusted verifier lacks {', '.join(missing)}")
    return module


def _environment(cell: Record) -> dict[str, str]:
    return {
        "BOARD_SEED": str(cell["seed"]),
        "BOARD_FIXTURE": cell["fixture"],
        "BOARD_RULE": cell["rule"],
        "BOARD_TICKET": cell["ticket"],
    }


def _check_config(
    config: Record, cell: Record, team: dict[str, Record], label: str
) -> None:
    require(
        config.get("trial_name") == cell["ticket"],
        f"{label} trial_name is not the ticket",
    )
    check_binding(config, _environment(cell), team, SEATS, label)


def _check_lock(
    lock: Record, cell: Record, team: dict[str, Record], task: Record
) -> None:
    version = lock.get("schema_version")
    require(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    require(type(pinned) is dict, "lock.json has no task")
    assert type(pinned) is dict
    require(pinned.get("type") == "local", "lock.json task is not local")
    require(pinned.get("name") == LOCK_TASK_NAME, "lock.json task is not shift-board")
    require(
        pinned.get("digest") == task["digest"],
        "lock.json digest differs from the registration",
    )
    check_binding(lock, _environment(cell), team, SEATS, "lock.json")


def _check_seats(result: Record) -> None:
    rows = result.get("seat_results")
    require(
        type(rows) is list and len(rows) == 2, "native seat results are not two seats"
    )
    assert type(rows) is list
    seats: set[str] = set()
    for row in rows:
        require(isinstance(row, dict), "native seat result is not an object")
        assert isinstance(row, dict)
        require(row.get("under_test") is True, "native seat is not under test")
        require(
            "exception_info" in row and row["exception_info"] is None,
            "native seat error record is absent or not null",
        )
        seat = row.get("seat")
        require(
            isinstance(seat, str) and seat not in seats,
            "native seat result repeats a seat",
        )
        assert isinstance(seat, str)
        seats.add(seat)
    require(seats == set(SEATS), "native seat results are not planner and dispatcher")


def _check_result(
    result: Record, cell: Record, team: dict[str, Record], task: Record
) -> tuple[str, int]:
    version = result.get("schema_version")
    require(
        type(version) is int and version == 1, "result.json is not schema version 1"
    )
    native_id = result.get("id")
    require(canonical_uuid(native_id), "result.json id is not a canonical UUID")
    assert type(native_id) is str
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
    require(
        "exception_info" in result and result["exception_info"] is None,
        "native trial error record is absent or not null",
    )
    _check_seats(result)
    config = result.get("config")
    require(type(config) is dict, "result.json has no config")
    assert type(config) is dict
    _check_config(config, cell, team, "result.json config")
    require(result.get("reward_valid") is True, "native reward is not valid")
    return native_id, native_reward(result)


def _check_identity(identity: Record, ticket: str) -> str:
    require(
        set(identity) == IDENTITY_KEYS,
        "world identity fields do not match the contract",
    )
    require(
        identity["schema"] == IDENTITY_SCHEMA, "world identity schema does not match"
    )
    require(
        identity["ticket"] == ticket,
        "world identity ticket is not the directory ticket",
    )
    run = identity["run"]
    require(canonical_uuid(run, 4), "world identity run is not a canonical UUID4")
    return run


def _grade(
    verifier: types.ModuleType, data: dict[str, bytes], cell: Record
) -> tuple[Record, Record]:
    try:
        cut = verifier.parse_cut(data["cut"])
        expected = verifier.evaluate(
            cut, seed=cell["seed"], fixture=cell["fixture"], rule=cell["rule"]
        )
    except verifier.CutError:
        raise Invalid("cut does not replay under the trusted verifier") from None
    assert type(expected) is dict and expected["reward"] in (0, 1)
    published = strict_object(data["observation"], "verifier/shift-board.json")
    require(same(expected, published), "published observation differs from the replay")
    recorded = strict_object(data["reward"], "verifier/reward.json")
    require(
        same(recorded, {"reward": expected["reward"]}),
        "verifier/reward.json differs from the replay",
    )
    return cut, expected


def _evidence(trial: Path, verifier: types.ModuleType) -> dict[str, bytes]:
    return {
        "result": read_bytes(trial, RESULT, MAX_RESULT_BYTES),
        "config": read_bytes(trial, CONFIG, MAX_CONFIG_BYTES),
        "lock": read_bytes(trial, LOCK, MAX_CONFIG_BYTES),
        "identity": read_bytes(trial, IDENTITY, MAX_SMALL_BYTES),
        "cut": read_bytes(trial, CUT, verifier.MAX_CUT_BYTES),
        "observation": read_bytes(trial, OBSERVATION, MAX_SMALL_BYTES),
        "reward": read_bytes(trial, REWARD, MAX_SMALL_BYTES),
    }


def _evaluate(
    trial: Path,
    cell: Record,
    team: dict[str, Record],
    task: Record,
    verifier: types.ModuleType,
) -> tuple[str, Record, Record | None]:
    data = _evidence(trial, verifier)
    result = strict_object(data["result"], "result.json")
    native_id, reward = _check_result(result, cell, team, task)
    config = strict_object(data["config"], "config.json")
    _check_config(config, cell, team, "config.json")
    _check_lock(strict_object(data["lock"], "lock.json"), cell, team, task)
    run = _check_identity(
        strict_object(data["identity"], "world identity"), cell["ticket"]
    )
    cut, expected = _grade(verifier, data, cell)
    require(expected["run"] == run, "world identity run differs from the cut")
    require(expected["reward"] == reward, "native reward differs from the replay")
    require(native_id != run, "native id repeats the world run")
    return native_id, expected, inspect_owners(team, result, cut)


def _identifiers(trial: Path, verifier: types.ModuleType) -> set[str]:
    sources = (
        (RESULT, MAX_RESULT_BYTES, "id"),
        (IDENTITY, MAX_SMALL_BYTES, "run"),
        (OBSERVATION, MAX_SMALL_BYTES, "run"),
        (CUT, verifier.MAX_CUT_BYTES, "run"),
    )
    found: set[str] = set()
    for parts, limit, key in sources:
        try:
            data = read_bytes(trial, parts, limit)
            value = strict_object(data, "/".join(parts)).get(key)
        except (Incomplete, Invalid):
            continue
        if isinstance(value, str) and canonical_uuid(value):
            found.add(value)
    return found


def _scan(
    roots: list[Path], tickets: frozenset[str]
) -> tuple[dict[str, list[Path]], dict[str, int]]:
    require_usage(1 <= len(roots) <= MAX_ROOTS, f"give 1 to {MAX_ROOTS} trials roots")
    seen: set[tuple[int, int]] = set()
    claims: dict[str, list[Path]] = {}
    counts = {"roots": len(roots), "claimed": 0, "unknown": 0, "skipped": 0}
    total = 0
    for root in roots:
        info = os.stat(root)
        require_usage(stat.S_ISDIR(info.st_mode), f"{root} is not a directory")
        require_usage(
            (info.st_dev, info.st_ino) not in seen, f"{root} repeats a trials root"
        )
        seen.add((info.st_dev, info.st_ino))
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries, start=1):
                total += 1
                require_usage(
                    index <= MAX_ROOT_ENTRIES,
                    f"{root} has over {MAX_ROOT_ENTRIES} entries",
                )
                require_usage(
                    total <= MAX_ENTRIES,
                    f"trials roots have over {MAX_ENTRIES} entries",
                )
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    counts["skipped"] += 1
                elif entry.name in tickets:
                    counts["claimed"] += 1
                    claims.setdefault(entry.name, []).append(Path(entry.path))
                else:
                    counts["unknown"] += 1
    assert counts["claimed"] + counts["unknown"] + counts["skipped"] == total
    return claims, counts


def _quarantined(
    claims: dict[str, list[Path]], verifier: types.ModuleType
) -> frozenset[str]:
    owners: dict[str, set[str]] = {}
    for ticket, paths in claims.items():
        for path in paths:
            for identifier in _identifiers(path, verifier):
                owners.setdefault(identifier, set()).add(ticket)
    return frozenset(
        ticket for tickets in owners.values() if len(tickets) > 1 for ticket in tickets
    )


def _registered_teams(manifest: Record) -> dict[str, dict[str, Record]]:
    try:
        return {
            label: seat_map(team, f"team {label}", SEATS)
            for label, team in manifest["teams"].items()
        }
    except Invalid as error:
        raise ValueError(f"registration {error}") from None


def _cell(
    cell: Record,
    paths: list[Path],
    quarantined: frozenset[str],
    task: Record,
    team: dict[str, Record],
    verifier: types.ModuleType,
) -> Record:
    row = {field: cell[field] for field in CELL_FIELDS}
    native_id: str | None = None
    observation: Record | None = None
    reason: str | None = None
    if cell["ticket"] in quarantined:
        status, reason = (
            "quarantined",
            "a native or world id repeats under another ticket",
        )
    elif not paths:
        status = "missing"
    elif len(paths) > 1:
        status, reason = "duplicate", f"{len(paths)} directories claim the ticket"
    else:
        try:
            native_id, expected, owners = _evaluate(
                paths[0], cell, team, task, verifier
            )
            if owners is not None:
                row["owner_evidence"] = owners
            status = "usable"
            observation = {field: expected[field] for field in OBSERVATION_FIELDS}
        except Incomplete as error:
            status, reason = "incomplete", str(error)
        except Invalid as error:
            status, reason = "invalid", str(error)
    assert status in STATUSES and (status == "usable") == (observation is not None)
    row.update(
        status=status, reason=reason, native_id=native_id, observation=observation
    )
    return row


def _reward(row: Record) -> int | None:
    return None if row["observation"] is None else row["observation"]["reward"]


def _pairs(rows: list[Record]) -> list[Record]:
    arms: dict[str, dict[str, Record]] = {}
    for row in rows:
        assert row["rule"] not in arms.get(row["pair"], {})
        arms.setdefault(row["pair"], {})[row["rule"]] = row
    pairs = []
    for pair, rules in arms.items():
        assert set(rules) == set(RULES)
        observe, guard = rules["observe"], rules["guard"]
        assert all(
            observe[key] == guard[key] for key in ("team", "seed", "fixture", "repeat")
        )
        observed, guarded = _reward(observe), _reward(guard)
        complete = observed is not None and guarded is not None
        pairs.append(
            {
                "pair": pair,
                "team": observe["team"],
                "seed": observe["seed"],
                "fixture": observe["fixture"],
                "repeat": observe["repeat"],
                "status": "complete" if complete else "incomplete",
                "observe_reward": observed,
                "guard_reward": guarded,
                "guard_minus_observe": guarded - observed if complete else None,
            }
        )
    return pairs


def _team_counts(pairs: list[Record], teams: list[str]) -> list[Record]:
    counts = []
    for team in teams:
        members = [pair for pair in pairs if pair["team"] == team]
        directions = Counter(
            pair["guard_minus_observe"]
            for pair in members
            if pair["status"] == "complete"
        )
        counts.append(
            {
                "team": team,
                "pairs": len(members),
                "complete_pairs": sum(directions.values()),
                "guard_higher": directions[1],
                "equal": directions[0],
                "observe_higher": directions[-1],
            }
        )
    return counts


def _mechanisms(rows: list[Record], teams: list[str]) -> list[Record]:
    counts = []
    for team in teams:
        for rule in RULES:
            members = [
                row for row in rows if row["team"] == team and row["rule"] == rule
            ]
            seen = [
                row["observation"] for row in members if row["observation"] is not None
            ]
            actions = Counter(observation["final_action"] for observation in seen)
            changes = Counter(observation["observed_change"] for observation in seen)
            counts.append(
                {
                    "team": team,
                    "rule": rule,
                    "cells": len(members),
                    "usable": len(seen),
                    "rewarded": sum(observation["reward"] for observation in seen),
                    "stale_attempted": sum(
                        observation["stale_attempts"] > 0 for observation in seen
                    ),
                    "stale_refused": sum(
                        observation["stale_refusals"] > 0 for observation in seen
                    ),
                    "publish": actions["publish"],
                    "leave": actions["leave"],
                    "no_action": actions["none"],
                    "repair": changes["repair"],
                    "damage": changes["damage"],
                    "unchanged": changes["unchanged"],
                }
            )
    return counts


def collect(manifest_path: Path, roots: list[Path]) -> Record:
    manifest = load_manifest(manifest_path)
    verifier = load_verifier()
    cells = manifest["cells"]
    tickets = frozenset(cell["ticket"] for cell in cells)
    assert len(tickets) == len(cells)
    claims, directories = _scan(roots, tickets)
    quarantined = _quarantined(claims, verifier)
    bound = _registered_teams(manifest)
    rows = [
        _cell(
            cell,
            claims.get(cell["ticket"], []),
            quarantined,
            manifest["task"],
            bound[cell["team"]],
            verifier,
        )
        for cell in cells
    ]
    pairs = _pairs(rows)
    teams = sorted(manifest["teams"])
    statuses = Counter(row["status"] for row in rows)
    complete = sum(pair["status"] == "complete" for pair in pairs)
    return {
        "schema": SCHEMA,
        "task": {key: manifest["task"][key] for key in ("name", "digest", "checksum")},
        "registered": len(rows),
        "usable": statuses["usable"],
        "registered_pairs": len(pairs),
        "complete_pairs": complete,
        "statuses": {status: statuses[status] for status in STATUSES},
        "directories": directories,
        "teams": _team_counts(pairs, teams),
        "mechanisms": _mechanisms(rows, teams),
        "cells": rows,
        "pairs": pairs,
    }


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
    directories = report["directories"]
    totals = {
        "registered": report["registered"],
        "registered_pairs": report["registered_pairs"],
        "complete_pairs": report["complete_pairs"],
        **report["statuses"],
        "roots": directories["roots"],
        "claimed_dirs": directories["claimed"],
        "unknown_dirs": directories["unknown"],
        "skipped_entries": directories["skipped"],
    }
    sections = [_tsv([totals]), _tsv(report["teams"]), _tsv(report["mechanisms"])]
    return "\n\n".join("\n".join(section) for section in sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m examples.shift_board.collect")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIALS_ROOT")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = collect(args.manifest, args.roots)
    except (OSError, ValueError) as error:
        print(f"collect: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
