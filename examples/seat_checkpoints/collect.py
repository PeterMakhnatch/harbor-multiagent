import argparse
import json
import sys
import types
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from ..native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    check_binding,
    native_reward,
    read_bytes,
    require,
    strict_object,
)
from ..native_study import (
    AGENT_NAME,
    DIGEST,
    API_FORMAT,
    KEY_NAME,
    NATIVE_DEFAULTS,
    as_choice,
    as_int,
    as_list,
    as_record,
    as_text,
    check_native_defaults,
    check_task,
)
from ..native_study import require as require_registration
from ..seat_handoffs.native_records import read_record
from ..shift_board.collect import (
    CONFIG,
    CUT,
    IDENTITY,
    LOCK,
    MAX_CONFIG_BYTES,
    MAX_RESULT_BYTES,
    MAX_SMALL_BYTES,
    OBSERVATION,
    RESULT,
    REWARD,
    SEATS,
    _check_config,
    _check_identity,
    _environment,
    _grade,
    _quarantined,
    _registered_teams,
    _scan,
    _tsv,
    load_verifier,
)
from ..shift_board.plan import (
    FIXTURES,
    MAX_CELLS,
    MAX_SEED,
    MODEL_NAME,
    RULES,
    TICKET,
    _public_url,
)
from .bracket import (
    HOST_KEYS,
    HOST_SCHEMA,
    WORLD_KEYS,
    WORLD_SCHEMA,
    SeatCutInvalid,
    analyze,
)

REGISTRATION_SCHEMA = "harbor.seat-cut.study/v1"
SCHEMA = "harbor.seat-cut.collection/v1"
TASK_NAME = "harbor/shift-board-checkpoint"
LOCK_TASK_NAME = "shift-board-checkpoint"
CONTROL_IMPORT = "examples.seat_checkpoints.control_seat:ControlSeat"
CONTROL_KWARGS = frozenset({"role", "policy", "gates"})
CONTROL_POLICIES = {"planner": ("publish",), "dispatcher": ("preferred", "leave")}
CONTROL_GATES = {
    "planner": ("after_publish",),
    "dispatcher": ("before_decide", "after_decide"),
}
REGISTRATION_FIELDS = frozenset({"schema", "task", "task_files", "teams", "cells"})
CELL_FIELDS = (
    "ticket",
    "team",
    "seed",
    "fixture",
    "rule",
    "target",
    "peer",
    "observe",
)
REQUIRED_FIELDS = frozenset({"seat", "skip_model_parsing", *NATIVE_DEFAULTS})
PROVIDER_FIELDS = frozenset({"model_api_key_name", "model_api_format", "base_url"})
OPTIONAL_FIELDS = frozenset({"name", "import_path", "model_name", *PROVIDER_FIELDS})
PATTERNS = (
    ("name", AGENT_NAME),
    ("model_name", MODEL_NAME),
    ("model_api_key_name", KEY_NAME),
    ("model_api_format", API_FORMAT),
)
STATUSES = (
    "bounded",
    "no_clean_return",
    "unobserved_control",
    "native_error",
    "journal_incomplete",
    "journal_invalid",
    "incomplete",
    "invalid",
    "ambiguous",
    "duplicate_id",
    "missing",
)
BOUND_STATUSES = frozenset({"bounded", "no_clean_return"})
MAX_TEAMS = 64
MAX_REGISTRATION_BYTES = 1 << 20
MAX_JOURNAL_BYTES = 64 << 10
WORLD_LOG = ("artifacts", "logs", "world", "seat-cut.json")
HOST_JOURNAL = ("seat-cut-host.json",)


class Native(NamedTuple):
    native_id: str
    run: str
    grade: int | None
    error: str | None
    scores: list[int] | None
    revisions: list[int] | None


def _other(seat: str) -> str:
    assert seat in SEATS
    return SEATS[1] if seat == SEATS[0] else SEATS[0]


def _check_control_kwargs(value: object, seat: str, where: str) -> None:
    kwargs = as_record(value, f"{where} kwargs", CONTROL_KWARGS)
    require_registration(kwargs["role"] == seat, f"{where} control role is not the seat")
    as_choice(kwargs["policy"], f"{where} control policy", CONTROL_POLICIES[seat])
    gates = as_list(kwargs["gates"], f"{where} control gates", 0, len(CONTROL_GATES[seat]))
    for gate in gates:
        as_choice(gate, f"{where} control gate", CONTROL_GATES[seat])
    require_registration(len(set(gates)) == len(gates), f"{where} control gates repeat")


def _check_participant(value: object, label: str) -> str:
    where = f"team {label!r}"
    member = as_record(value, f"{where} participant")
    seat = as_choice(member.get("seat"), f"{where} seat", SEATS)
    where = f"team {label!r} seat {seat!r}"
    keys = set(member)
    require_registration(keys >= REQUIRED_FIELDS, f"{where} lacks normalized native fields")
    require_registration(
        keys <= REQUIRED_FIELDS | OPTIONAL_FIELDS,
        f"{where} has fields outside the public allowlist",
    )
    require_registration(
        ("name" in member) != ("import_path" in member),
        f"{where} needs name xor import_path",
    )
    for field, pattern in PATTERNS:
        if field in member:
            as_text(member[field], f"{where} {field}", pattern)
    if "import_path" in member:
        require_registration(
            member["import_path"] == CONTROL_IMPORT,
            f"{where} import_path is not the scripted control",
        )
        require_registration(
            not keys & (PROVIDER_FIELDS | {"model_name"}),
            f"{where} scripted control names a model or provider",
        )
        check_native_defaults({**member, "kwargs": {}}, where)
        _check_control_kwargs(member["kwargs"], seat, where)
        return seat
    require_registration("model_name" in member, f"{where} names no model")
    check_native_defaults(member, where)
    require_registration(
        "base_url" not in member or _public_url(member["base_url"]),
        f"{where} base_url",
    )
    return seat


def _check_teams(value: object) -> dict[str, Any]:
    teams = as_record(value, "teams")
    require_registration(1 <= len(teams) <= MAX_TEAMS, f"teams are not 1..{MAX_TEAMS}")
    for label, members in teams.items():
        as_text(label, "team label", AGENT_NAME)
        team = as_list(members, f"team {label!r}", 2, 2)
        seats = {_check_participant(member, label) for member in team}
        require_registration(seats == set(SEATS), f"team {label!r} does not bind both seats")
    return teams


def _check_cells(value: object, teams: dict[str, Any]) -> None:
    cells = as_list(value, "cells", 1, MAX_CELLS)
    tickets: set[str] = set()
    for raw in cells:
        cell = as_record(raw, "cell", frozenset(CELL_FIELDS))
        ticket = as_text(cell["ticket"], "cell ticket", TICKET)
        require_registration(ticket not in tickets, "duplicate ticket")
        tickets.add(ticket)
        as_choice(cell["team"], "cell team", teams)
        as_int(cell["seed"], "cell seed", 0, MAX_SEED)
        as_choice(cell["fixture"], "cell fixture", FIXTURES)
        as_choice(cell["rule"], "cell rule", RULES)
        target = as_choice(cell["target"], "cell target", SEATS)
        require_registration(cell["peer"] == _other(target), "cell peer is not the other seat")
        require_registration(type(cell["observe"]) is bool, "cell observe is not a boolean")
    assert len(tickets) == len(cells)


def validate_registration(value: object) -> Record:
    registration = as_record(value, "registration", REGISTRATION_FIELDS)
    require_registration(
        registration["schema"] == REGISTRATION_SCHEMA, "registration schema differs"
    )
    check_task(registration["task"], TASK_NAME)
    as_text(registration["task_files"], "full task files", DIGEST)
    teams = _check_teams(registration["teams"])
    _check_cells(registration["cells"], teams)
    return registration


def load_registration(path: Path) -> Record:
    record = read_record(path, MAX_REGISTRATION_BYTES)
    require_registration(record is not None, f"registration {path} does not exist")
    return validate_registration(record)


def _check_lock(lock: Record, cell: Record, team: dict[str, Record], task: Record) -> None:
    version = lock.get("schema_version")
    require(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    require(type(pinned) is dict, "lock.json has no task")
    assert type(pinned) is dict
    require(pinned.get("type") == "local", "lock.json task is not local")
    require(pinned.get("name") == LOCK_TASK_NAME, "lock.json task is not the checkpoint task")
    path = pinned.get("path")
    require(
        type(path) is str and PurePosixPath(path).name == LOCK_TASK_NAME,
        "lock.json task path is not the checkpoint task",
    )
    require(
        pinned.get("digest") == task["digest"],
        "lock.json digest differs from the registration",
    )
    check_binding(lock, _environment(cell), team, SEATS, "lock.json")


def _check_result(result: Record, cell: Record, team: dict[str, Record], task: Record) -> str:
    version = result.get("schema_version")
    require(type(version) is int and version == 1, "result.json is not schema version 1")
    native_id = result.get("id")
    require(canonical_uuid(native_id), "result.json id is not a canonical UUID")
    assert type(native_id) is str
    require(result.get("trial_name") == cell["ticket"], "result.json trial_name is not the ticket")
    require(
        result.get("task_name") == task["name"],
        "result.json task_name differs from the registration",
    )
    require(
        result.get("task_checksum") == task["checksum"],
        "result.json checksum differs from the registration",
    )
    config = result.get("config")
    require(type(config) is dict, "result.json has no config")
    assert type(config) is dict
    _check_config(config, cell, team, "result.json config")
    return native_id


def _native_error(result: Record) -> str | None:
    require("exception_info" in result, "native trial error record is absent")
    rows = result.get("seat_results")
    require(type(rows) is list and len(rows) == 2, "native seat results are not two seats")
    assert type(rows) is list
    failed = [] if result["exception_info"] is None else ["trial"]
    seats: set[str] = set()
    for row in rows:
        require(type(row) is dict, "native seat result is not an object")
        require(row.get("under_test") is True, "native seat is not under test")
        require("exception_info" in row, "native seat error record is absent")
        seat = row.get("seat")
        require(seat in SEATS and seat not in seats, "native seat results repeat or misname a seat")
        seats.add(seat)
        if row["exception_info"] is not None:
            failed.append(seat)
            continue
        execution = row.get("agent_execution")
        require(
            type(execution) is dict and type(execution.get("finished_at")) is str,
            f"native {seat} seat has no finished execution and no error",
        )
    require(seats == set(SEATS), "native seat results are not planner and dispatcher")
    return None if not failed else "native error recorded for " + " and ".join(failed)


def _prefixes(verifier: types.ModuleType, cut: Record, cell: Record) -> tuple[list[int], list[int]]:
    events = cut["events"]
    assert type(events) is list and len(events) <= verifier.MAX_EVENTS
    scores: list[int] = []
    revisions: list[int] = []
    revision = 0
    for length in range(len(events) + 1):
        if length and events[length - 1]["type"] == "publish":
            revision += events[length - 1]["outcome"] == "accepted"
        try:
            observed = verifier.evaluate(
                {**cut, "events": events[:length]},
                seed=cell["seed"],
                fixture=cell["fixture"],
                rule=cell["rule"],
            )
        except verifier.CutError:
            raise Invalid("a cut prefix does not replay under the trusted verifier") from None
        assert observed["reward"] in (0, 1) and 0 <= revision <= 2
        scores.append(observed["reward"])
        revisions.append(revision)
    assert len(scores) == len(revisions) == len(events) + 1 and scores[0] == 0
    return scores, revisions


def _attempt(trial: Path, cell: Record, native_id: str, files: str) -> None:
    receipt = strict_object(read_bytes(trial, ("seat-cut-attempt.json",), MAX_SMALL_BYTES), "attempt")
    keys = {"schema", "ticket", "trial_id", "team", "target", "peer", "observe", "task_files",
            "interval_seconds", "max_samples", "timeout_seconds"}
    require(set(receipt) == keys and receipt["schema"] == "harbor.seat-cut.attempt/v1",
            "attempt fields or version differ")
    require(receipt["trial_id"] == native_id and receipt["task_files"] == files,
            "attempt native id or full task hash differs")
    require(all(receipt[k] == cell[k] for k in ("ticket", "team", "target", "peer"))
            and receipt["observe"] is cell["observe"], "attempt differs from the registered cell")
    count, interval, timeout = (receipt[k] for k in ("max_samples", "interval_seconds", "timeout_seconds"))
    require(type(count) is int, "invalid sample budget")
    if cell["observe"]:
        require(2 <= count <= 32 and type(interval) in (int, float) and 0.05 <= interval <= 60
                and type(timeout) in (int, float) and 1 <= timeout <= 60, "invalid sampling parameters")
    else:
        require(count == 0 and interval is None and timeout is None, "unobserved attempt configured marks")


def _native(
    trial: Path, cell: Record, team: dict[str, Record], task: Record, verifier: types.ModuleType
) -> Native:
    result = strict_object(read_bytes(trial, RESULT, MAX_RESULT_BYTES), "result.json")
    native_id = _check_result(result, cell, team, task)
    _attempt(trial, cell, native_id, task["files"])
    error = _native_error(result)
    config = strict_object(read_bytes(trial, CONFIG, MAX_CONFIG_BYTES), "config.json")
    _check_config(config, cell, team, "config.json")
    lock = strict_object(read_bytes(trial, LOCK, MAX_CONFIG_BYTES), "lock.json")
    _check_lock(lock, cell, team, task)
    identity = read_bytes(trial, IDENTITY, MAX_SMALL_BYTES)
    run = _check_identity(strict_object(identity, "world identity"), cell["ticket"])
    require(native_id != run, "native id repeats the world run")
    if error is not None and result.get("reward_valid") is not True:
        return Native(native_id, run, None, error, None, None)
    require(result.get("reward_valid") is True, "native reward is not valid")
    reward = native_reward(result)
    data = {
        "cut": read_bytes(trial, CUT, verifier.MAX_CUT_BYTES),
        "observation": read_bytes(trial, OBSERVATION, MAX_SMALL_BYTES),
        "reward": read_bytes(trial, REWARD, MAX_SMALL_BYTES),
    }
    cut, expected = _grade(verifier, data, cell)
    require(expected["run"] == run, "world identity run differs from the cut")
    require(expected["reward"] == reward, "native reward differs from the replay")
    scores, revisions = _prefixes(verifier, cut, cell)
    require(scores[-1] == expected["reward"], "full prefix differs from the replay")
    return Native(native_id, run, expected["reward"], error, scores, revisions)


def _marker_log(trial: Path, cell: Record, run: str) -> Record:
    data = read_bytes(trial, WORLD_LOG, MAX_JOURNAL_BYTES)
    world = strict_object(data, "world marker log")
    require(
        set(world) == WORLD_KEYS and world["schema"] == WORLD_SCHEMA,
        "world marker log fields do not match the contract",
    )
    require(world["ticket"] == cell["ticket"], "world marker log ticket is not the ticket")
    require(world["run"] == run, "world marker log run is not the world run")
    require(type(world["marks"]) is list, "world marker log marks are not a list")
    return world


def _host(trial: Path, cell: Record, native_id: str) -> Record | None:
    try:
        data = read_bytes(trial, HOST_JOURNAL, MAX_JOURNAL_BYTES)
    except Incomplete:
        return None
    host = strict_object(data, "host journal")
    require(
        set(host) == HOST_KEYS and host["schema"] == HOST_SCHEMA,
        "host journal fields do not match the contract",
    )
    require(host["trial_id"] == native_id, "host journal trial_id is not the native id")
    require(host["ticket"] == cell["ticket"], "host journal ticket is not the ticket")
    require(
        host["target"] == cell["target"] and host["peer"] == cell["peer"],
        "host journal roles differ from the registration",
    )
    return host


def _journal(trial: Path, cell: Record, native: Native) -> tuple[str, Record | None]:
    world = _marker_log(trial, cell, native.run)
    host = _host(trial, cell, native.native_id)
    if not cell["observe"]:
        require(host is None, "an unobserved control holds a host journal")
        require(world["marks"] == [], "an unobserved control holds world marks")
        return "unobserved_control", None
    if host is None:
        raise Incomplete("host journal is missing")
    assert native.scores is not None and native.revisions is not None
    bound = analyze(
        host,
        world,
        native_target="clean",
        native_peer="clean",
        scores=native.scores,
        revisions=native.revisions,
    )
    assert bound["status"] in BOUND_STATUSES
    return bound["status"], bound


def _trial(
    trial: Path, cell: Record, team: dict[str, Record], task: Record, verifier: types.ModuleType
) -> tuple[str, int | None, Record | None, str | None]:
    try:
        native = _native(trial, cell, team, task, verifier)
    except Incomplete as error:
        return "incomplete", None, None, str(error)
    except Invalid as error:
        return "invalid", None, None, str(error)
    if native.error is not None:
        return "native_error", native.grade, None, native.error
    assert native.grade is not None
    try:
        status, bound = _journal(trial, cell, native)
    except Incomplete as error:
        return "journal_incomplete", native.grade, None, str(error)
    except (Invalid, SeatCutInvalid) as error:
        return "journal_invalid", native.grade, None, str(error)
    return status, native.grade, bound, None


def _roster(team: dict[str, Record]) -> str:
    kinds = {"scripted" if "import_path" in member else "named" for member in team.values()}
    assert kinds and kinds <= {"scripted", "named"}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def _row(
    cell: Record,
    paths: list[Path],
    quarantined: frozenset[str],
    task: Record,
    team: dict[str, Record],
    verifier: types.ModuleType,
) -> Record:
    row: Record = {field: cell[field] for field in CELL_FIELDS}
    row["roster"] = _roster(team)
    grade: int | None = None
    bound: Record | None = None
    reason: str | None = None
    if cell["ticket"] in quarantined:
        status, reason = "duplicate_id", "a native or world id repeats under another ticket"
    elif not paths:
        status = "missing"
    elif len(paths) > 1:
        status, reason = "ambiguous", f"{len(paths)} directories claim the ticket"
    else:
        status, grade, bound, reason = _trial(paths[0], cell, team, task, verifier)
    assert status in STATUSES
    assert (bound is not None) == (status in BOUND_STATUSES)
    assert status != "unobserved_control" or grade is not None
    row.update(status=status, reason=reason, native_grade=grade, bound=bound)
    return row


def _unknowns(rows: list[Record]) -> Record:
    observed = [row for row in rows if row["observe"]]
    bounded = [row for row in observed if row["bound"] is not None]
    identified = sum(row["bound"]["grade_identified"] is True for row in bounded)
    return {
        "observed_cells": len(observed),
        "observed_without_bound": len(observed) - len(bounded),
        "bounded_grade_identified": identified,
        "bounded_grade_unidentified": len(bounded) - identified,
        "controls": len(rows) - len(observed),
        "without_native_grade": sum(row["native_grade"] is None for row in rows),
    }


def collect(registration_path: Path, roots: list[Path]) -> Record:
    registration = load_registration(registration_path)
    verifier = load_verifier()
    cells = registration["cells"]
    tickets = frozenset(cell["ticket"] for cell in cells)
    assert len(tickets) == len(cells)
    claims, directories = _scan(roots, tickets)
    quarantined = _quarantined(claims, verifier)
    teams = _registered_teams(registration)
    rows = [
        _row(
            cell,
            claims.get(cell["ticket"], []),
            quarantined,
            {**registration["task"], "files": registration["task_files"]},
            teams[cell["team"]],
            verifier,
        )
        for cell in cells
    ]
    statuses = Counter(row["status"] for row in rows)
    assert sum(statuses.values()) == len(rows) == len(cells)
    return {
        "schema": SCHEMA,
        "task": {key: registration["task"][key] for key in ("name", "digest", "checksum")},
        "task_files": registration["task_files"],
        "registered": len(rows),
        "statuses": {status: statuses[status] for status in STATUSES},
        "unknowns": _unknowns(rows),
        "directories": directories,
        "cells": rows,
    }


def _cell_line(row: Record) -> Record:
    bound = row["bound"] or {}
    return {
        "ticket": row["ticket"],
        "team": row["team"],
        "roster": row["roster"],
        "rule": row["rule"],
        "target": row["target"],
        "observe": row["observe"],
        "status": row["status"],
        "native_grade": row["native_grade"],
        "lower_seq": bound.get("lower_seq"),
        "upper_seq": bound.get("upper_seq"),
        "min_grade": bound.get("min_grade"),
        "max_grade": bound.get("max_grade"),
        "grade_identified": bound.get("grade_identified"),
    }


def render(report: Record) -> str:
    totals = {
        "registered": report["registered"],
        **report["statuses"],
        **report["unknowns"],
        **{f"{key}_dirs": value for key, value in report["directories"].items()},
    }
    sections = [_tsv([totals]), _tsv([_cell_line(row) for row in report["cells"]])]
    return "\n\n".join("\n".join(section) for section in sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -BS -m examples.seat_checkpoints.collect")
    parser.add_argument("registration", type=Path, metavar="REG")
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIAL_ROOT")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = collect(args.registration, args.roots)
    except (OSError, ValueError) as error:
        print(f"collect: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
