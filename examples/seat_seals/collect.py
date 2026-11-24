import argparse
import json
import re
import sys
import types
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, cast

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
    strict_object,
)
from ..seat_checkpoints.bracket import (
    MARK_ID_PATTERN,
    MAX_EVENTS,
    MAX_REVISION,
    SeatCutInvalid,
    analyze,
)
from ..seat_checkpoints.collect import (
    HOST_JOURNAL as HOST_JOURNAL,
    MAX_JOURNAL_BYTES,
    WORLD_LOG as WORLD_LOG,
    _host,
    _marker_log,
    _message_file,
    _prefixes,
    _roster,
)
from ..seat_checkpoints.native_timeout import Disposition, deadline_seconds
from ..seat_checkpoints.native_timeout import classify as native_disposition
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
    _check_identity,
    _grade,
    _quarantined,
    _registered_teams,
    _scan,
    _tsv,
    load_verifier,
)
from .contract import environment, load_registration
from .schedules import VERDICTS, classify

SCHEMA = "harbor.seat-seal.collection/v1"
SEAL_SCHEMA = "harbor.seat-seal.world/v1"
ATTEMPT_SCHEMA = "harbor.seat-seal.attempt/v1"
SCHEDULE_SCHEMA = "harbor.seat-cut.schedule/v1"
LOCK_TASK_NAME = "shift-board-seal"
SEAL_LOG = ("artifacts", "logs", "world", "seat-seal.json")
ATTEMPT = ("seat-seal-attempt.json",)
SCHEDULE_RECEIPT = ("seat-cut-schedule.json",)
MODES = ("continue", "seal")
NATURAL = "natural"
CELL_FIELDS = (
    "ticket",
    "team",
    "seed",
    "fixture",
    "rule",
    "target",
    "peer",
    "observe",
    "schedule",
    "mode",
)
BLOCK_FIELDS = ("team", "schedule", "fixture", "seed")
ATTEMPT_FIELDS = frozenset(
    {
        "schema",
        "ticket",
        "trial_id",
        "team",
        "target",
        "peer",
        "schedule",
        "mode",
        "task_files",
        "interval_seconds",
        "max_samples",
        "timeout_seconds",
    }
)
ATTEMPT_BOUND = ("ticket", "team", "target", "peer", "schedule", "mode")
SEAL_FIELDS = frozenset(
    {"schema", "ticket", "run", "mode", "decision", "refusals", "truncated"}
)
DECISION_FIELDS = frozenset({"id", "after_seq", "revision"})
REFUSAL_FIELDS = frozenset({"seat", "path", "after_seq", "revision"})
REFUSAL_OWNERS: dict[str, tuple[str, ...]] = {
    "/prepare": ("dispatcher",),
    "/publish": SEATS,
    "/leave": ("dispatcher",),
}
SCHEDULE_FIELDS = frozenset(
    {"schema", "trial_id", "ticket", "schedule", "status", "trace"}
)
GATE_TOKEN = re.compile(r"[A-Za-z0-9_:-]{1,64}")
STATUSES = (
    "graded",
    "native_error",
    "incomplete",
    "invalid",
    "ambiguous",
    "duplicate_id",
    "missing",
)
JOURNALS = ("bounded", "no_clean_return", "absent", "invalid", "unavailable")
DECISIONS = ("committed", "none", "absent", "invalid", "unknown")
GATES = ("complete", "incomplete", "absent", "invalid", "natural", "unknown")
COMPARISONS = ("intervention", "finals", "incomplete")
MAX_REFUSALS = 128
MAX_TRACE = 64
MAX_RECEIPT_BYTES = 16 << 10
LIFECYCLE = ("not_started", "running", "clean", "error")


class Native(NamedTuple):
    native_id: str
    run: str | None
    grade: int | None
    error: str | None
    states: dict[str, str] | None
    events: list[Record] | None
    scores: list[int] | None
    revisions: list[int] | None
    final_action: str | None
    observed_change: str | None


class Journal(NamedTuple):
    status: str
    reason: str | None
    bound: Record | None
    request: Record | None
    marks: list[Record] | None


def _check_config(
    config: Record, cell: Record, team: dict[str, Record], label: str
) -> None:
    require(
        config.get("trial_name") == cell["ticket"],
        f"{label} trial_name is not the ticket",
    )
    check_binding(config, environment(cell), team, SEATS, label)


def _check_lock(
    lock: Record, cell: Record, team: dict[str, Record], task: Record
) -> None:
    version = lock.get("schema_version")
    require(type(version) is int and version == 2, "lock.json is not schema version 2")
    pinned = lock.get("task")
    require(type(pinned) is dict, "lock.json has no task")
    assert type(pinned) is dict
    require(pinned.get("type") == "local", "lock.json task is not local")
    require(pinned.get("name") == LOCK_TASK_NAME, "lock.json task is not the seal task")
    path = pinned.get("path")
    require(
        type(path) is str and PurePosixPath(path).name == LOCK_TASK_NAME,
        "lock.json task path is not the seal task",
    )
    require(
        pinned.get("digest") == task["digest"],
        "lock.json digest differs from the registration",
    )
    check_binding(lock, environment(cell), team, SEATS, "lock.json")


def _check_result(
    result: Record, cell: Record, team: dict[str, Record], task: Record
) -> str:
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
    config = result.get("config")
    require(type(config) is dict, "result.json has no config")
    assert type(config) is dict
    _check_config(config, cell, team, "result.json config")
    return native_id


def _attempt(trial: Path, cell: Record, native_id: str, files: str) -> None:
    receipt = strict_object(read_bytes(trial, ATTEMPT, MAX_SMALL_BYTES), "attempt")
    require(
        set(receipt) == ATTEMPT_FIELDS and receipt["schema"] == ATTEMPT_SCHEMA,
        "attempt fields or version differ",
    )
    require(
        receipt["trial_id"] == native_id and receipt["task_files"] == files,
        "attempt native id or full task hash differs",
    )
    require(
        all(
            type(receipt[key]) is str and receipt[key] == cell[key]
            for key in ATTEMPT_BOUND
        ),
        "attempt differs from the registered cell",
    )
    count, interval, timeout = (
        receipt[key] for key in ("max_samples", "interval_seconds", "timeout_seconds")
    )
    require(
        type(count) is int
        and 2 <= count <= 32
        and type(interval) in (int, float)
        and 0.05 <= interval <= 60
        and type(timeout) in (int, float)
        and 1 <= timeout <= 60,
        "invalid sampling parameters",
    )


def _seat_states(result: Record, cell: Record) -> dict[str, str] | None:
    rows = result.get("seat_results")
    if type(rows) is not list or len(rows) != 2:
        return None
    states: dict[str, str] = {}
    for row in rows:
        require(type(row) is dict and row.get("seat") in SEATS, "native seat row")
        execution = row.get("agent_execution")
        if row.get("exception_info") is not None:
            state = "error"
        elif type(execution) is dict and execution.get("finished_at") is not None:
            state = "clean"
        elif type(execution) is dict and execution.get("started_at") is not None:
            state = "running"
        else:
            state = "not_started"
        states[row["seat"]] = state
    require(set(states) == set(SEATS), "native seat rows are not both seats")
    assert set(states.values()) <= set(LIFECYCLE)
    return {"target": states[cell["target"]], "peer": states[cell["peer"]]}


def _graded(
    trial: Path,
    cell: Record,
    result: Record,
    native_id: str,
    error: str | None,
    verifier: types.ModuleType,
) -> Native:
    identity = read_bytes(trial, IDENTITY, MAX_SMALL_BYTES)
    run = _check_identity(strict_object(identity, "world identity"), cell["ticket"])
    require(native_id != run, "native id repeats the world run")
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
    return Native(
        native_id,
        run,
        expected["reward"],
        error,
        _seat_states(result, cell),
        list(cut["events"]),
        scores,
        revisions,
        expected["final_action"],
        expected["observed_change"],
    )


def _error_reason(disposition: Disposition) -> str | None:
    assert disposition.kind in ("clean", "agent_timeout", "other_error")
    if disposition.kind == "clean":
        assert disposition.timed_out == () and disposition.reason is None
        return None
    assert disposition.reason is not None
    if disposition.kind == "other_error":
        assert disposition.timed_out is None
        assert disposition.reason.startswith("native error recorded for ")
        return disposition.reason
    assert disposition.timed_out
    named = " and ".join(("trial", *disposition.timed_out))
    return f"native error recorded for {named}; agent timeout: {disposition.reason}"


def _native(
    trial: Path,
    cell: Record,
    team: dict[str, Record],
    task: Record,
    verifier: types.ModuleType,
) -> Native:
    result = strict_object(read_bytes(trial, RESULT, MAX_RESULT_BYTES), "result.json")
    native_id = _check_result(result, cell, team, task)
    _attempt(trial, cell, native_id, task["files"])
    disposition = native_disposition(
        result,
        SEATS,
        deadline_seconds(False),
        _message_file(trial) if result.get("exception_info") is not None else None,
    )
    error = _error_reason(disposition)
    config = strict_object(read_bytes(trial, CONFIG, MAX_CONFIG_BYTES), "config.json")
    _check_config(config, cell, team, "config.json")
    lock = strict_object(read_bytes(trial, LOCK, MAX_CONFIG_BYTES), "lock.json")
    _check_lock(lock, cell, team, task)
    ungraded = Native(native_id, None, None, error, None, None, None, None, None, None)
    if error is None:
        return _graded(trial, cell, result, native_id, error, verifier)
    if result.get("reward_valid") is not True:
        return ungraded
    try:
        return _graded(trial, cell, result, native_id, error, verifier)
    except (Incomplete, Invalid) as problem:
        return ungraded._replace(error=f"{error}; grade evidence: {problem}")


def _request(host: Record) -> Record | None:
    for sample in host["samples"]:
        if sample["before"]["target"] == "clean":
            return sample
    return None


def _journal(trial: Path, cell: Record, native: Native) -> Journal:
    if native.run is None or native.scores is None or native.revisions is None:
        return Journal("unavailable", "no validated native cut", None, None, None)
    if native.states is None:
        return Journal("unavailable", "native seat lifecycle absent", None, None, None)
    try:
        world = _marker_log(trial, cell, native.run)
        host = _host(trial, cell, native.native_id)
    except Incomplete as problem:
        return Journal("absent", str(problem), None, None, None)
    except Invalid as problem:
        return Journal("invalid", str(problem), None, None, None)
    if host is None:
        return Journal("absent", "host journal is missing", None, None, None)
    try:
        bound = analyze(
            host,
            world,
            native_target=native.states["target"],
            native_peer=native.states["peer"],
            scores=native.scores,
            revisions=native.revisions,
        )
    except SeatCutInvalid as problem:
        return Journal("invalid", str(problem), None, None, None)
    assert bound["status"] in JOURNALS[:2]
    return Journal(bound["status"], None, bound, _request(host), world["marks"])


def _bounded_int(value: object, high: int, label: str) -> int:
    require(type(value) is int and 0 <= value <= high, f"{label} is out of bounds")
    assert type(value) is int
    return value


def _check_refusal(value: object) -> Record:
    require(type(value) is dict, "seal refusal is not an object")
    refusal = cast(Record, value)
    require(
        set(refusal) == REFUSAL_FIELDS, "seal refusal fields do not match the contract"
    )
    seat, path = refusal["seat"], refusal["path"]
    require(type(seat) is str and seat in SEATS, "seal refusal names an unknown seat")
    require(
        type(path) is str and path in REFUSAL_OWNERS,
        "seal refusal names a non-write route",
    )
    require(seat in REFUSAL_OWNERS[path], "seal refusal route is not owned by its seat")
    _bounded_int(refusal["after_seq"], MAX_EVENTS, "seal refusal after_seq")
    _bounded_int(refusal["revision"], MAX_REVISION, "seal refusal revision")
    return refusal


def _seal_log(trial: Path, cell: Record, run: str) -> Record:
    seal = strict_object(read_bytes(trial, SEAL_LOG, MAX_JOURNAL_BYTES), "seal log")
    require(
        set(seal) == SEAL_FIELDS and seal["schema"] == SEAL_SCHEMA,
        "seal log fields do not match the contract",
    )
    require(seal["ticket"] == cell["ticket"], "seal log ticket is not the ticket")
    require(seal["run"] == run, "seal log run is not the world run")
    require(seal["mode"] == cell["mode"], "seal log mode is not the registered mode")
    require(type(seal["truncated"]) is bool, "seal log truncated is not a boolean")
    refusals = seal["refusals"]
    require(
        type(refusals) is list and len(refusals) <= MAX_REFUSALS,
        f"seal refusals are not a list of at most {MAX_REFUSALS}",
    )
    for refusal in refusals:
        _check_refusal(refusal)
    decision = seal["decision"]
    if decision is not None:
        require(
            type(decision) is dict and set(decision) == DECISION_FIELDS,
            "seal decision fields do not match the contract",
        )
        require(
            type(decision["id"]) is str
            and MARK_ID_PATTERN.fullmatch(decision["id"]) is not None,
            "seal decision id is malformed",
        )
        _bounded_int(decision["after_seq"], MAX_EVENTS, "seal decision after_seq")
        _bounded_int(decision["revision"], MAX_REVISION, "seal decision revision")
    return seal


def _blank_decision(status: str, reason: str | None) -> Record:
    assert status in DECISIONS
    return {
        "status": status,
        "reason": reason,
        "joined": None,
        "intervention": False,
        "after_seq": None,
        "revision": None,
        "grade": None,
        "ack": None,
        "peer_before": None,
        "peer_after": None,
        "refusals": None,
        "refusal_paths": None,
        "refusal_seats": None,
        "truncated": None,
        "refusal_ledger": None,
        "writes_after": None,
        "views_after": None,
    }


def _check_refusals(seal: Record, cell: Record, native: Native) -> None:
    refusals, decision = seal["refusals"], seal["decision"]
    if decision is None or cell["mode"] == "continue":
        require(not refusals, "refusals recorded without an enforced decision")
        return
    assert native.events is not None and native.revisions is not None
    previous = decision["after_seq"]
    for refusal in refusals:
        after = refusal["after_seq"]
        require(
            previous <= after <= len(native.events),
            "seal refusal is out of order or outside the decision and cut",
        )
        require(
            native.revisions[after] == refusal["revision"],
            "seal refusal revision mismatches its ordinal",
        )
        previous = after


def _commit(row: Record, seal: Record, cell: Record, native: Native) -> None:
    decision = seal["decision"]
    assert native.events is not None and native.scores is not None
    assert native.revisions is not None and decision is not None
    after = decision["after_seq"]
    require(after <= len(native.events), "seal decision exceeds the final cut")
    require(
        native.revisions[after] == decision["revision"],
        "seal decision revision mismatches its ordinal",
    )
    tail = native.events[after:]
    writes = sum(
        event["type"] != "view"
        and not (event["type"] == "publish" and event["outcome"] == "rejected")
        for event in tail
    )
    row.update(
        after_seq=after,
        revision=decision["revision"],
        grade=native.scores[after],
        writes_after=writes,
        views_after=sum(event["type"] == "view" for event in tail),
    )
    if cell["mode"] == "seal":
        require(
            all(event["type"] == "view" for event in tail),
            "seal arm holds a non-view board event after the decision",
        )


def _join(row: Record, seal: Record, journal: Journal) -> None:
    decision, request = seal["decision"], journal.request
    if journal.marks is None:
        return
    marked = {mark["id"] for mark in journal.marks}
    if decision is None:
        row["joined"] = False
        require(
            request is None or request["id"] not in marked,
            "world marked the decision request but its ledger holds no decision",
        )
        return
    matched = [mark for mark in journal.marks if mark["id"] == decision["id"]]
    require(
        not matched or same(matched[0], decision),
        "seal decision differs from its ordinary world marker",
    )
    row["joined"] = request is not None and request["id"] == decision["id"]
    if not row["joined"]:
        row["reason"] = "decision is not the first clean-target host request"
    elif not matched:
        row["reason"] = "decision joins the host request without a passive marker"


def _decision(trial: Path, cell: Record, native: Native, journal: Journal) -> Record:
    if native.run is None or native.events is None:
        return _blank_decision("unknown", "no validated native cut")
    row = _blank_decision("unknown", None)
    if journal.request is not None:
        row.update(
            ack=journal.request["ack"],
            peer_before=journal.request["before"]["peer"],
            peer_after=(journal.request["after"] or {}).get("peer"),
        )
    try:
        seal = _seal_log(trial, cell, native.run)
    except Incomplete as problem:
        row.update(status="absent", reason=str(problem))
        return row
    except Invalid as problem:
        row.update(status="invalid", reason=str(problem))
        return row
    refusals = seal["refusals"]
    row.update(
        status="none" if seal["decision"] is None else "committed",
        refusals=len(refusals),
        refusal_paths=dict(Counter(refusal["path"] for refusal in refusals)),
        refusal_seats=dict(Counter(refusal["seat"] for refusal in refusals)),
        truncated=seal["truncated"],
        refusal_ledger="truncated" if seal["truncated"] else "recorded",
    )
    try:
        if seal["decision"] is not None:
            _commit(row, seal, cell, native)
        _check_refusals(seal, cell, native)
        _join(row, seal, journal)
    except Invalid as problem:
        row.update(status="invalid", reason=str(problem), intervention=False)
        return row
    if row["status"] == "none" and seal["truncated"]:
        row.update(status="unknown", reason="world write fault before any decision")
    row["intervention"] = row["status"] == "committed" and row["joined"] is True
    return row


def _gate_row(status: str, reason: str | None, receipt: Record | None) -> Record:
    assert status in GATES
    return {
        "status": status,
        "reason": reason,
        "receipt_status": None if receipt is None else receipt["status"],
        "trace": None if receipt is None else receipt["trace"],
    }


def _check_schedule(receipt: Record, cell: Record, native_id: str) -> None:
    require(
        set(receipt) == SCHEDULE_FIELDS and receipt["schema"] == SCHEDULE_SCHEMA,
        "schedule receipt fields do not match the contract",
    )
    require(
        receipt["trial_id"] == native_id and receipt["ticket"] == cell["ticket"],
        "schedule receipt is bound to another trial",
    )
    require(
        receipt["schedule"] == cell["schedule"],
        "schedule receipt names another schedule",
    )
    status, trace = receipt["status"], receipt["trace"]
    require(
        type(status) is str and GATE_TOKEN.fullmatch(status) is not None,
        "schedule receipt status is malformed",
    )
    require(
        type(trace) is list and len(trace) <= MAX_TRACE,
        "schedule receipt trace is not a bounded list",
    )
    require(
        all(type(step) is str and GATE_TOKEN.fullmatch(step) for step in trace),
        "schedule receipt trace holds a malformed step",
    )


def _gate(trial: Path, cell: Record, native_id: str) -> Record:
    natural = cell["schedule"] == NATURAL
    try:
        data = read_bytes(trial, SCHEDULE_RECEIPT, MAX_RECEIPT_BYTES)
    except Incomplete:
        if natural:
            return _gate_row("natural", None, None)
        return _gate_row("absent", "schedule receipt is missing", None)
    except Invalid as problem:
        return _gate_row("invalid", str(problem), None)
    if natural:
        return _gate_row("invalid", "natural roster holds a schedule receipt", None)
    try:
        receipt = strict_object(data, "schedule receipt")
        _check_schedule(receipt, cell, native_id)
    except Invalid as problem:
        return _gate_row("invalid", str(problem), None)
    verdict = classify(cell["schedule"], receipt["status"], receipt["trace"])
    assert verdict in VERDICTS
    if verdict == "invalid":
        return _gate_row(
            "invalid",
            "schedule receipt status and trace do not follow the published protocol",
            None,
        )
    return _gate_row(verdict, None, receipt)


def _unresolved(status: str, reason: str | None) -> Record:
    assert status in STATUSES[2:]
    return {
        "status": status,
        "reason": reason,
        "native_error": None,
        "native_grade": None,
        "final_action": None,
        "observed_change": None,
        "journal": "unavailable",
        "journal_reason": "no validated native trial",
        "bound": None,
        "return_grade": None,
        "decision": _blank_decision("unknown", "no validated native trial"),
        "gate": _gate_row("unknown", "no validated native trial", None),
    }


def _return_grade(bound: Record | None) -> Record | None:
    if bound is None or bound["status"] != "bounded":
        return None
    keys = ("lower_seq", "upper_seq", "min_grade", "max_grade", "grade_identified")
    return {key: bound[key] for key in keys}


def _trial(
    trial: Path,
    cell: Record,
    team: dict[str, Record],
    task: Record,
    verifier: types.ModuleType,
) -> Record:
    try:
        native = _native(trial, cell, team, task, verifier)
    except Incomplete as problem:
        return _unresolved("incomplete", str(problem))
    except Invalid as problem:
        return _unresolved("invalid", str(problem))
    journal = _journal(trial, cell, native)
    return {
        "status": "graded" if native.error is None else "native_error",
        "reason": native.error,
        "native_error": native.error,
        "native_grade": native.grade,
        "final_action": native.final_action,
        "observed_change": native.observed_change,
        "journal": journal.status,
        "journal_reason": journal.reason,
        "bound": journal.bound,
        "return_grade": _return_grade(journal.bound),
        "decision": _decision(trial, cell, native, journal),
        "gate": _gate(trial, cell, native.native_id),
    }


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
    if cell["ticket"] in quarantined:
        reason = "a native or world id repeats under another ticket"
        evidence = _unresolved("duplicate_id", reason)
    elif not paths:
        evidence = _unresolved("missing", None)
    elif len(paths) > 1:
        reason = f"{len(paths)} directories claim the ticket"
        evidence = _unresolved("ambiguous", reason)
    else:
        evidence = _trial(paths[0], cell, team, task, verifier)
    row.update(evidence)
    assert row["status"] in STATUSES and row["journal"] in JOURNALS
    assert row["decision"]["status"] in DECISIONS and row["gate"]["status"] in GATES
    assert row["status"] != "graded" or row["native_grade"] is not None
    assert not row["decision"]["intervention"] or row["decision"]["joined"] is True
    return row


def _arm(row: Record | None) -> Record | None:
    if row is None:
        return None
    decision = row["decision"]
    return {
        "ticket": row["ticket"],
        "status": row["status"],
        "native_grade": row["native_grade"],
        "final_action": row["final_action"],
        "decision": decision["status"],
        "joined": decision["joined"],
        "intervention": decision["intervention"],
        "writes_after": decision["writes_after"],
        "refusals": decision["refusals"],
    }


def _block(key: tuple[Any, ...], arms: dict[str, Record]) -> Record:
    present = [arms.get(mode) for mode in MODES]
    graded = all(row is not None and row["native_grade"] is not None for row in present)
    joined = graded and all(
        row is not None
        and row["status"] == "graded"
        and row["decision"]["intervention"] is True
        for row in present
    )
    comparison = "intervention" if joined else "finals" if graded else "incomplete"
    delta = None
    if joined:
        delta = arms["seal"]["native_grade"] - arms["continue"]["native_grade"]
    return {
        **dict(zip(BLOCK_FIELDS, key)),
        "continue": _arm(arms.get("continue")),
        "seal": _arm(arms.get("seal")),
        "comparison": comparison,
        "seal_minus_continue": delta,
    }


def _blocks(rows: list[Record]) -> list[Record]:
    groups: dict[tuple[Any, ...], dict[str, Record]] = {}
    for row in rows:
        arms = groups.setdefault(tuple(row[field] for field in BLOCK_FIELDS), {})
        require_usage(row["mode"] not in arms, "registration repeats a block arm")
        arms[row["mode"]] = row
    return [_block(key, arms) for key, arms in groups.items()]


def _count(rows: list[Record], values: tuple[str, ...], pick: Any) -> Record:
    counts = Counter(pick(row) for row in rows)
    assert set(counts) <= set(values)
    return {value: counts[value] for value in values}


def collect(registration_path: Path, roots: list[Path]) -> Record:
    registration = load_registration(registration_path)
    verifier = load_verifier()
    cells = registration["cells"]
    tickets = frozenset(cell["ticket"] for cell in cells)
    require_usage(len(tickets) == len(cells), "registration repeats a ticket")
    claims, directories = _scan(roots, tickets)
    quarantined = _quarantined(claims, verifier)
    teams = _registered_teams(registration)
    task = {**registration["task"], "files": registration["task_files"]}
    rows = [
        _row(
            cell,
            claims.get(cell["ticket"], []),
            quarantined,
            task,
            teams[cell["team"]],
            verifier,
        )
        for cell in cells
    ]
    blocks = _blocks(rows)
    return {
        "schema": SCHEMA,
        "task": {
            key: registration["task"][key] for key in ("name", "digest", "checksum")
        },
        "task_files": registration["task_files"],
        "registered": len(rows),
        "statuses": _count(rows, STATUSES, lambda row: row["status"]),
        "journals": _count(rows, JOURNALS, lambda row: row["journal"]),
        "decisions": _count(rows, DECISIONS, lambda row: row["decision"]["status"]),
        "gates": _count(rows, GATES, lambda row: row["gate"]["status"]),
        "comparisons": _count(blocks, COMPARISONS, lambda block: block["comparison"]),
        "directories": directories,
        "cells": rows,
        "blocks": blocks,
    }


def _cell_line(row: Record) -> Record:
    returned = row["return_grade"] or {}
    decision = row["decision"]
    paths = decision["refusal_paths"] or {}
    return {
        "ticket": row["ticket"],
        "team": row["team"],
        "schedule": row["schedule"],
        "fixture": row["fixture"],
        "seed": row["seed"],
        "mode": row["mode"],
        "status": row["status"],
        "grade": row["native_grade"],
        "action": row["final_action"],
        "journal": row["journal"],
        "return_min": returned.get("min_grade"),
        "return_max": returned.get("max_grade"),
        "decision": decision["status"],
        "joined": decision["joined"],
        "after_seq": decision["after_seq"],
        "decision_grade": decision["grade"],
        "ack": decision["ack"],
        "peer_open": decision["peer_before"],
        "refusals": decision["refusals"],
        "refused_paths": ",".join(f"{p}={n}" for p, n in sorted(paths.items())) or None,
        "truncated": decision["truncated"],
        "writes_after": decision["writes_after"],
        "gate": row["gate"]["status"],
    }


def _block_line(block: Record) -> Record:
    arms = {mode: block[mode] or {} for mode in MODES}
    return {
        **{field: block[field] for field in BLOCK_FIELDS},
        "continue_grade": arms["continue"].get("native_grade"),
        "continue_action": arms["continue"].get("final_action"),
        "continue_decision": arms["continue"].get("decision"),
        "seal_grade": arms["seal"].get("native_grade"),
        "seal_action": arms["seal"].get("final_action"),
        "seal_decision": arms["seal"].get("decision"),
        "comparison": block["comparison"],
        "seal_minus_continue": block["seal_minus_continue"],
    }


def render(report: Record) -> str:
    totals = {
        "registered": report["registered"],
        **report["statuses"],
        **{f"journal_{key}": value for key, value in report["journals"].items()},
        **{f"decision_{key}": value for key, value in report["decisions"].items()},
        **{f"gate_{key}": value for key, value in report["gates"].items()},
        **report["comparisons"],
        **{f"{key}_dirs": value for key, value in report["directories"].items()},
    }
    sections = [_tsv([totals]), _tsv([_cell_line(row) for row in report["cells"]])]
    if report["blocks"]:
        sections.append(_tsv([_block_line(block) for block in report["blocks"]]))
    return "\n\n".join("\n".join(section) for section in sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -BS -m examples.seat_seals.collect")
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
