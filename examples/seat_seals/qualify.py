import argparse
import json
import sys
import types
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    check_binding,
    native_reward,
    read_bytes,
    require,
    same,
    seat_map,
    strict_object,
)
from ..seat_checkpoints.bracket import (
    ACKS,
    HOST_KEYS,
    HOST_SCHEMAS,
    HOST_STATES,
    LEGACY_HOST_SCHEMA,
    MARK_ID_PATTERN,
    MARK_KEYS,
    MAX_EVENTS,
    SAMPLE_KEYS,
    SNAPSHOT_KEYS,
    WORLD_KEYS,
    WORLD_SCHEMA,
)
from ..seat_checkpoints.collect import DISPOSITION
from ..seat_checkpoints.lifecycle import terminal_state
from ..shift_board.collect import (
    CONFIG,
    CUT,
    IDENTITY,
    IDENTITY_KEYS,
    IDENTITY_SCHEMA,
    MAX_CONFIG_BYTES,
    MAX_RESULT_BYTES,
    MAX_ROOTS,
    MAX_SMALL_BYTES,
    OBSERVATION,
    RESULT,
    REWARD,
    load_verifier,
)
from .collect import (
    ATTEMPT,
    HOST_JOURNAL,
    SCHEDULE_RECEIPT,
    SEAL_LOG,
    WORLD_LOG,
    collect,
)
from .contract import (
    CONTROL_BLOCKS,
    CONTROL_IMPORT,
    MODES,
    SEATS,
    Block,
    environment,
    is_control,
    load_question,
    load_registration,
    question_blocks,
)
from .schedules import TRACES, classify

SCHEMA = "harbor.seat-seal.qualification/v1"
SEAL_SCHEMA = "harbor.seat-seal.world/v1"
ATTEMPT_SCHEMA = "harbor.seat-seal.attempt/v1"
SCHEDULE_SCHEMA = "harbor.seat-cut.schedule/v1"
RECEIPT_SCHEMA = "harbor.seat-cut.control-receipt/v1"
SEAL_KEYS = frozenset(
    {"schema", "ticket", "run", "mode", "decision", "refusals", "truncated"}
)
DECISION_KEYS = frozenset({"id", "after_seq", "revision"})
REFUSAL_KEYS = frozenset({"seat", "path", "after_seq", "revision"})
ATTEMPT_KEYS = frozenset(
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
SCHEDULE_KEYS = frozenset(
    {"schema", "trial_id", "ticket", "schedule", "status", "trace"}
)
BOUND_FIELDS = ("ticket", "team", "target", "peer", "schedule", "mode")
WRITE_PATHS = ("/prepare", "/publish", "/leave")
ROUTE_SEATS = {
    "/prepare": ("dispatcher",),
    "/publish": SEATS,
    "/leave": ("dispatcher",),
}
STAGES = {
    "planner": ("after_publish",),
    "dispatcher": ("before_decide", "after_decide"),
}
MAX_REFUSALS = 128
MAX_SAMPLES = 32
MAX_MARKS = 32
MAX_TRACE = 32
MAX_REVISION = 2
MAX_JOURNAL_BYTES = 64 << 10
MAX_RECEIPT_BYTES = 4096
EVIDENCE = (
    "native",
    "attempt",
    "binding",
    "board",
    "receipts",
    "schedule",
    "host",
    "marks",
    "seal",
)
SUPPORTED = frozenset({"acknowledged", "ack_lost"})
DECISION_CASES = (
    "acknowledged",
    "ack_lost",
    "unmarked",
    "not_applied",
    "unrecorded",
    "not_requested",
    "inconsistent",
    "invalid",
    "missing",
    "unchecked",
)
PRESENCE = ("found", "missing", "ambiguous", "unregistered")
CASES = (
    "supported",
    "unregistered",
    "missing",
    "ambiguous",
    "native_failure",
    "board_unverified",
    "decision_unsupported",
    "prediction_failed",
)
EFFECTS = (
    "prevented_replacement",
    "forfeited_repair",
    "equal_final",
    "other_difference",
    "unmeasured",
)
EARLY = frozenset({"held-early", "held-leave"})

assert all(len(trace) <= MAX_TRACE for trace in TRACES.values())
assert len(DECISION_CASES) == len(set(DECISION_CASES)) and SUPPORTED < set(
    DECISION_CASES
)


class Evidence:
    def __init__(self) -> None:
        self.states: dict[str, str] = {}

    def take(self, name: str, ready: bool, read: Callable[[], Any]) -> Any:
        assert name in EVIDENCE and name not in self.states
        if not ready:
            self.states[name] = "skipped"
            return None
        try:
            value = read()
        except Incomplete:
            self.states[name] = "missing"
            return None
        except Invalid:
            self.states[name] = "invalid"
            return None
        assert value is not None
        self.states[name] = "ok"
        return value

    def ok(self, name: str) -> bool:
        assert name in EVIDENCE
        return self.states.get(name) == "ok"


def is_int(value: object, low: int, high: int) -> bool:
    assert low <= high
    return type(value) is int and low <= value <= high


def is_number(value: object, low: float, high: float) -> bool:
    assert low <= high
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and low <= value <= high
    )


def is_mark_id(value: object) -> bool:
    return type(value) is str and MARK_ID_PATTERN.fullmatch(value) is not None


def read_record(trial: Path, parts: tuple[str, ...], limit: int) -> Record:
    assert parts and limit > 0
    return strict_object(read_bytes(trial, parts, limit), "/".join(parts))


def trial_dirs(roots: list[Path], ticket: str) -> list[Path]:
    assert 1 <= len(roots) <= MAX_ROOTS
    found: list[Path] = []
    for root in roots:
        path = root / ticket
        if path.is_dir() and not path.is_symlink():
            found.append(path)
    return found


def read_native(trial: Path, cell: Record, task: Record) -> Record:
    result = read_record(trial, RESULT, MAX_RESULT_BYTES)
    require(canonical_uuid(result.get("id")), "native id")
    require(result.get("trial_name") == cell["ticket"], "native ticket")
    require(
        result.get("task_name") == task["name"]
        and result.get("task_checksum") == task["checksum"],
        "native task",
    )
    require("exception_info" in result, "native trial error record")
    rows = result.get("seat_results")
    require(type(rows) is list and len(rows) == len(SEATS), "native seats")
    assert isinstance(rows, list)
    seats: dict[str, Record] = {}
    for row in rows:
        require(isinstance(row, dict) and "exception_info" in row, "native seat row")
        assert isinstance(row, dict)
        require(row.get("seat") in SEATS and row["seat"] not in seats, "native seat")
        seats[row["seat"]] = row
    error_free = result["exception_info"] is None and all(
        row["exception_info"] is None for row in seats.values()
    )
    reward = native_reward(result) if result.get("reward_valid") is True else None
    return {
        "id": result["id"],
        "error_free": error_free,
        "reward": reward,
        "checksum": result["task_checksum"],
        "seats": seats,
    }


def read_attempt(trial: Path, cell: Record, native_id: str, files: str) -> bool:
    receipt = read_record(trial, ATTEMPT, MAX_SMALL_BYTES)
    require(
        set(receipt) == ATTEMPT_KEYS and receipt["schema"] == ATTEMPT_SCHEMA,
        "attempt fields",
    )
    require(
        receipt["trial_id"] == native_id and receipt["task_files"] == files,
        "attempt binding",
    )
    require(all(receipt[key] == cell[key] for key in BOUND_FIELDS), "attempt cell")
    require(
        is_int(receipt["max_samples"], 2, MAX_SAMPLES)
        and is_number(receipt["interval_seconds"], 0.05, 60)
        and is_number(receipt["timeout_seconds"], 1, 60),
        "attempt sampling",
    )
    return True


def read_binding(trial: Path, cell: Record, team: list[Record]) -> bool:
    config = read_record(trial, CONFIG, MAX_CONFIG_BYTES)
    require(config.get("trial_name") == cell["ticket"], "config ticket")
    check_binding(
        config, environment(cell), seat_map(team, "team", SEATS), SEATS, "config"
    )
    return True


def prefixes(
    verifier: types.ModuleType, cut: Record, cell: Record
) -> tuple[list[int], list[int]]:
    events = cut["events"]
    require(type(events) is list and len(events) <= MAX_EVENTS, "cut events")
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
            raise Invalid("cut prefix does not replay") from None
        scores.append(observed["reward"])
        revisions.append(revision)
    assert len(scores) == len(revisions) == len(events) + 1
    assert all(0 <= value <= MAX_REVISION for value in revisions)
    return scores, revisions


def dispatcher_write(events: list[Record]) -> int | None:
    writes = [
        event["seq"]
        for event in events
        if event["seat"] == "dispatcher"
        and (
            event["type"] == "leave"
            or (event["type"] == "publish" and event["outcome"] == "accepted")
        )
    ]
    assert len(writes) <= 1
    return writes[0] if writes else None


def read_board(trial: Path, cell: Record, verifier: types.ModuleType) -> Record:
    identity = read_record(trial, IDENTITY, MAX_SMALL_BYTES)
    require(
        set(identity) == IDENTITY_KEYS
        and identity["schema"] == IDENTITY_SCHEMA
        and identity["ticket"] == cell["ticket"]
        and canonical_uuid(identity["run"], 4),
        "world identity",
    )
    try:
        cut = verifier.parse_cut(read_bytes(trial, CUT, verifier.MAX_CUT_BYTES))
        observed = verifier.evaluate(
            cut, seed=cell["seed"], fixture=cell["fixture"], rule=cell["rule"]
        )
    except verifier.CutError:
        raise Invalid("cut does not replay") from None
    require(observed["run"] == identity["run"], "cut run")
    published = read_record(trial, OBSERVATION, MAX_SMALL_BYTES)
    require(same(observed, published), "published observation")
    recorded = read_record(trial, REWARD, MAX_SMALL_BYTES)
    require(same(recorded, {"reward": observed["reward"]}), "recorded reward")
    scores, revisions = prefixes(verifier, cut, cell)
    require(scores[-1] == observed["reward"], "full prefix")
    return {
        "run": identity["run"],
        "events": cut["events"],
        "scores": scores,
        "revisions": revisions,
        "reward": observed["reward"],
        "final_action": observed["final_action"],
        "change": observed["observed_change"],
        "write_seq": dispatcher_write(cut["events"]),
    }


def read_receipt(trial: Path, seat: str, native: Record, member: Record) -> str:
    receipt = read_record(
        trial, ("seats", seat, "agent", "control-receipt.json"), MAX_RECEIPT_BYTES
    )
    agent = native["seats"][seat].get("agent_result")
    metadata = agent.get("metadata") if type(agent) is dict else None
    require(isinstance(metadata, dict), "seat metadata")
    assert isinstance(metadata, dict)
    require(same(receipt, metadata.get("seat_cut_control")), "seat metadata receipt")
    kwargs = member["kwargs"]
    gates = kwargs["gates"]
    stages = [
        {
            "stage": stage,
            "gated": stage in gates,
            "released": True if stage in gates else None,
        }
        for stage in STAGES[seat]
    ]
    summary = receipt.get("summary")
    require(
        receipt.get("schema") == RECEIPT_SCHEMA
        and receipt.get("role") == seat
        and receipt.get("policy") == kwargs["policy"]
        and receipt.get("outcome") == "complete"
        and receipt.get("failure") is None
        and same(receipt.get("gates"), gates)
        and same(receipt.get("stages"), stages),
        "seat receipt",
    )
    require(isinstance(summary, dict), "seat summary")
    assert isinstance(summary, dict)
    require(summary.get("role") == seat, "seat summary role")
    terminal = summary.get("terminal")
    require(isinstance(terminal, str), "seat terminal")
    assert isinstance(terminal, str)
    return terminal


def read_receipts(trial: Path, native: Record, team: list[Record]) -> dict[str, str]:
    members = {member["seat"]: member for member in team}
    require(set(members) == set(SEATS), "team seats")
    for member in members.values():
        require(
            member.get("import_path") == CONTROL_IMPORT and "model_name" not in member,
            "team is not scripted",
        )
    return {seat: read_receipt(trial, seat, native, members[seat]) for seat in SEATS}


def read_schedule(trial: Path, cell: Record, native_id: str) -> Record:
    record = read_record(trial, SCHEDULE_RECEIPT, MAX_SMALL_BYTES)
    require(
        set(record) == SCHEDULE_KEYS and record["schema"] == SCHEDULE_SCHEMA,
        "schedule fields",
    )
    require(
        record["ticket"] == cell["ticket"]
        and record["trial_id"] == native_id
        and record["schedule"] == cell["schedule"],
        "schedule binding",
    )
    trace = record["trace"]
    require(
        type(trace) is list
        and len(trace) <= MAX_TRACE
        and all(type(step) is str for step in trace),
        "schedule trace",
    )
    require(type(record["status"]) is str, "schedule status")
    return {
        "status": record["status"],
        "trace": tuple(trace),
        "verdict": classify(cell["schedule"], record["status"], trace),
    }


def snapshot(value: object, states: tuple[str, ...]) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == SNAPSHOT_KEYS
        and all(isinstance(state, str) and state in states for state in value.values())
    )


def read_host(trial: Path, cell: Record, native_id: str) -> Record:
    host = read_record(trial, HOST_JOURNAL, MAX_JOURNAL_BYTES)
    schema = host.get("schema")
    require(
        set(host) == HOST_KEYS and type(schema) is str and schema in HOST_SCHEMAS,
        "host fields",
    )
    require(
        host["trial_id"] == native_id
        and host["ticket"] == cell["ticket"]
        and host["target"] == cell["target"]
        and host["peer"] == cell["peer"]
        and host["closed"] is True,
        "host binding",
    )
    states = HOST_STATES[schema]
    samples = host["samples"]
    require(type(samples) is list and len(samples) <= MAX_SAMPLES, "host samples")
    for sample in samples:
        require(
            type(sample) is dict
            and set(sample) == SAMPLE_KEYS
            and is_mark_id(sample["id"])
            and snapshot(sample["before"], states)
            and (sample["after"] is None or snapshot(sample["after"], states))
            and sample["ack"] in ACKS
            and sample["ack"] != "pending",
            "host sample",
        )
    require(len({sample["id"] for sample in samples}) == len(samples), "host ids")
    return {"legacy": schema == LEGACY_HOST_SCHEMA, "samples": samples}


def returned(native: Record | None, host: Record | None) -> bool:
    if native is None or not native["error_free"]:
        return False
    legacy_host = host is not None and host["legacy"] is True
    for row in native["seats"].values():
        legacy = legacy_host and DISPOSITION not in row
        try:
            state = terminal_state(row.get(DISPOSITION), False, legacy=legacy)
        except ValueError:
            return False
        if state != "clean":
            return False
    return True


def read_marks(trial: Path, cell: Record, run: str) -> dict[str, Record]:
    world = read_record(trial, WORLD_LOG, MAX_JOURNAL_BYTES)
    require(
        set(world) == WORLD_KEYS and world["schema"] == WORLD_SCHEMA, "marker fields"
    )
    require(world["ticket"] == cell["ticket"] and world["run"] == run, "marker binding")
    marks = world["marks"]
    require(type(marks) is list and len(marks) <= MAX_MARKS, "marker list")
    found: dict[str, Record] = {}
    for mark in marks:
        require(
            type(mark) is dict
            and set(mark) == MARK_KEYS
            and is_mark_id(mark["id"])
            and mark["id"] not in found
            and is_int(mark["after_seq"], 0, MAX_EVENTS)
            and is_int(mark["revision"], 0, MAX_REVISION),
            "marker row",
        )
        found[mark["id"]] = mark
    return found


def read_seal(trial: Path, cell: Record, run: str) -> Record:
    seal = read_record(trial, SEAL_LOG, MAX_JOURNAL_BYTES)
    require(set(seal) == SEAL_KEYS and seal["schema"] == SEAL_SCHEMA, "seal fields")
    require(
        seal["ticket"] == cell["ticket"]
        and seal["run"] == run
        and seal["mode"] == cell["mode"],
        "seal binding",
    )
    decision = seal["decision"]
    require(
        decision is None
        or (
            type(decision) is dict
            and set(decision) == DECISION_KEYS
            and is_mark_id(decision["id"])
            and is_int(decision["after_seq"], 0, MAX_EVENTS)
            and is_int(decision["revision"], 0, MAX_REVISION)
        ),
        "seal decision",
    )
    refusals = seal["refusals"]
    require(type(refusals) is list and len(refusals) <= MAX_REFUSALS, "seal refusals")
    for row in refusals:
        require(
            type(row) is dict
            and set(row) == REFUSAL_KEYS
            and row["path"] in WRITE_PATHS
            and row["seat"] in ROUTE_SEATS[row["path"]]
            and is_int(row["after_seq"], 0, MAX_EVENTS)
            and is_int(row["revision"], 0, MAX_REVISION),
            "seal refusal",
        )
    require(type(seal["truncated"]) is bool, "seal truncation")
    require(decision is not None or not refusals, "refusal without a decision")
    return seal


def first_request(samples: list[Record]) -> int | None:
    assert len(samples) <= MAX_SAMPLES
    for index, sample in enumerate(samples):
        target = sample["before"]["target"]
        if target in ("error", "not_returned"):
            return None
        if target == "clean":
            return index
    return None


def unusable(found: Evidence) -> str | None:
    states = [found.states.get(name) for name in ("host", "marks", "seal", "board")]
    if "invalid" in states:
        return "invalid"
    if "missing" in states:
        return "missing"
    if any(state != "ok" for state in states):
        return "unchecked"
    return None


def consistent(decision: Record, seal: Record, board: Record) -> bool:
    after = decision["after_seq"]
    revisions = board["revisions"]
    if after >= len(revisions) or revisions[after] != decision["revision"]:
        return False
    return all(
        after <= row["after_seq"] < len(revisions)
        and revisions[row["after_seq"]] == row["revision"]
        for row in seal["refusals"]
    )


def judge(found: Evidence, facts: Record) -> Record:
    verdict: Record = {
        "case": unusable(found),
        "ack": None,
        "peer_before": None,
        "after_seq": None,
        "grade": None,
        "writes_after": None,
    }
    if verdict["case"] is not None:
        return verdict
    host, marks, seal, board = (facts[k] for k in ("host", "marks", "seal", "board"))
    samples = host["samples"]
    decision = seal["decision"]
    index = first_request(samples)
    if index is None:
        verdict["case"] = "not_requested" if decision is None else "inconsistent"
        return verdict
    sample = samples[index]
    verdict.update(ack=sample["ack"], peer_before=sample["before"]["peer"])
    if decision is None:
        if sample["id"] in marks:
            verdict["case"] = "inconsistent"
        else:
            verdict["case"] = "unrecorded" if seal["truncated"] else "not_applied"
        return verdict
    mark = marks.get(decision["id"])
    if decision["id"] != sample["id"] or not consistent(decision, seal, board):
        verdict["case"] = "inconsistent"
        return verdict
    if mark is None:
        verdict["case"] = "unmarked"
        return verdict
    if not same(mark, decision):
        verdict["case"] = "inconsistent"
        return verdict
    after = decision["after_seq"]
    verdict.update(
        case="acknowledged" if sample["ack"] == "ok" else "ack_lost",
        after_seq=after,
        grade=board["scores"][after],
        writes_after=sum(
            1 for event in board["events"][after:] if event["type"] != "view"
        ),
    )
    return verdict


def inspect(
    trial: Path,
    cell: Record,
    team: list[Record],
    task: Record,
    verifier: types.ModuleType,
) -> tuple[Evidence, Record]:
    found = Evidence()
    native = found.take("native", True, lambda: read_native(trial, cell, task))
    ready = native is not None
    found.take(
        "attempt", ready, lambda: read_attempt(trial, cell, native["id"], task["files"])
    )
    found.take("binding", True, lambda: read_binding(trial, cell, team))
    board = found.take("board", True, lambda: read_board(trial, cell, verifier))
    facts: Record = {"native": native, "board": board}
    facts["receipts"] = found.take(
        "receipts", ready, lambda: read_receipts(trial, native, team)
    )
    facts["schedule"] = found.take(
        "schedule", ready, lambda: read_schedule(trial, cell, native["id"])
    )
    facts["host"] = found.take(
        "host", ready, lambda: read_host(trial, cell, native["id"])
    )
    run = board["run"] if board is not None else None
    world_ready = isinstance(run, str)
    world_run = run if isinstance(run, str) else ""
    facts["marks"] = found.take(
        "marks", world_ready, lambda: read_marks(trial, cell, world_run)
    )
    facts["seal"] = found.take(
        "seal", world_ready, lambda: read_seal(trial, cell, world_run)
    )
    facts["decision"] = judge(found, facts)
    return found, facts


def blank() -> Record:
    facts: Record = {name: None for name in EVIDENCE}
    facts["decision"] = {
        "case": "unchecked",
        "ack": None,
        "peer_before": None,
        "after_seq": None,
        "grade": None,
        "writes_after": None,
    }
    return facts


def predict(schedule: str, fixture: str, mode: str) -> Record:
    assert schedule in TRACES and mode in MODES
    incumbent = int(fixture == "planner-fit")
    action = "leave" if schedule == "held-leave" else "publish"
    held = schedule in EARLY and mode == "seal"
    write = "none" if held else ("after" if schedule in EARLY else "before")
    return {
        "grade": incumbent if held or action == "leave" else 1 - incumbent,
        "terminal": "sealed" if held else action,
        "final_action": "none" if held else action,
        "refused": f"/{action}" if held else None,
        "write": write,
    }


def write_order(facts: Record, expect: Record) -> bool:
    board, decision = facts["board"], facts["decision"]
    if board is None or decision["case"] not in SUPPORTED:
        return False
    seq, after = board["write_seq"], decision["after_seq"]
    if expect["write"] == "none":
        return seq is None
    if seq is None:
        return False
    return seq > after if expect["write"] == "after" else seq <= after


def refusals_match(facts: Record, expect: Record) -> bool:
    seal = facts["seal"]
    if seal is None or seal["truncated"]:
        return False
    rows = seal["refusals"]
    if expect["refused"] is None:
        return rows == []
    return bool(rows) and all(
        row["seat"] == "dispatcher" and row["path"] == expect["refused"] for row in rows
    )


def window_order(trace: tuple[str, ...] | None) -> bool:
    returned, released = "native:planner:clean", "release:dispatcher:before_decide"
    if trace is None or returned not in trace or released not in trace:
        return False
    return trace.index(returned) < trace.index(released)


def collect_mismatch(row: Record | None, facts: Record, mode: str) -> list[str]:
    assert mode in MODES
    if row is None:
        return ["row"]
    board, decision, seal = facts["board"], facts["decision"], facts["seal"]
    native = facts["native"]
    mine: dict[str, object] = {}
    theirs: dict[str, object] = {}
    if board is not None:
        mine.update(native_grade=board["reward"], final_action=board["final_action"])
    if board is not None and native is not None and native["error_free"]:
        if native["reward"] == board["reward"]:
            mine["status"] = "graded"
    valid = decision["case"] in SUPPORTED and (
        decision["writes_after"] == 0 if mode == "seal" else not seal["refusals"]
    )
    if valid:
        mine.update(
            decision_status="committed",
            decision_joined=True,
            decision_after_seq=decision["after_seq"],
            decision_writes_after=decision["writes_after"],
            decision_refusals=len(seal["refusals"]),
        )
    schedule = facts["schedule"]
    if schedule is not None:
        mine["gate_status"] = schedule["verdict"]
        if schedule["verdict"] != "invalid":
            mine["gate_trace"] = list(schedule["trace"])
    for name in mine:
        group, _, field = name.partition("_")
        nested = row.get(group) if group in ("decision", "gate") else None
        if type(nested) is dict:
            theirs[name] = nested.get(field)
        else:
            theirs[name] = row.get(name)
    return sorted(name for name in mine if not same(mine[name], theirs[name]))


def arm_checks(
    presence: str,
    found: Evidence,
    facts: Record,
    slot: tuple[Block, str],
    mismatch: list[str],
) -> dict[str, bool]:
    (_, schedule_name, _, fixture), mode = slot
    expect = predict(schedule_name, fixture, mode)
    native, board, decision = facts["native"], facts["board"], facts["decision"]
    schedule = facts["schedule"]
    trace = schedule["trace"] if schedule is not None else None
    supported = decision["case"] in SUPPORTED
    checks = {
        "first_attempt": presence == "found",
        "native_complete": bool(
            returned(native, facts["host"]) and native["reward"] is not None
        ),
        "attempt_bound": found.ok("attempt"),
        "mode_bound": found.ok("binding"),
        "board_replayed": board is not None,
        "native_matches_board": bool(
            native and board and native["reward"] == board["reward"]
        ),
        "final_grade": bool(board and board["reward"] == expect["grade"]),
        "final_action": bool(board and board["final_action"] == expect["final_action"]),
        "seat_terminals": facts["receipts"]
        == {"planner": "publish", "dispatcher": expect["terminal"]},
        "schedule_complete": bool(schedule and schedule["verdict"] == "complete"),
        "schedule_trace": trace == TRACES[schedule_name],
        "decision_supported": supported,
        "decision_acknowledged": decision["case"] == "acknowledged",
        "peer_running_at_request": decision["peer_before"] == "running",
        "dispatcher_write_order": write_order(facts, expect),
        "refusals_expected": supported and refusals_match(facts, expect),
        "collect_agrees": not mismatch,
    }
    if mode == "seal":
        checks["no_write_after_decision"] = supported and decision["writes_after"] == 0
        checks["decision_grade_is_final"] = bool(
            supported and board and decision["grade"] == board["reward"]
        )
    if schedule_name == "held-window":
        checks["returned_before_write"] = window_order(trace)
    return checks


def collect_status(row: Record, presence: str) -> list[str]:
    assert presence in PRESENCE
    if presence in ("missing", "ambiguous") and row.get("status") != presence:
        return ["status"]
    return []


def arm(
    slot: tuple[Block, str],
    cell: Record | None,
    registration: Record | None,
    context: tuple[list[Path], types.ModuleType, dict[str, Record]],
) -> Record:
    roots, verifier, rows = context
    found, facts = Evidence(), blank()
    if cell is None or registration is None:
        mismatch = ["registration"]
        checks = arm_checks("unregistered", found, facts, slot, mismatch)
        return {
            "ticket": None,
            "presence": "unregistered",
            "facts": facts,
            "states": {},
            "mismatch": mismatch,
            "checks": checks,
        }
    paths = trial_dirs(roots, cell["ticket"])
    presence = "found" if len(paths) == 1 else ("missing" if not paths else "ambiguous")
    if presence == "found":
        task = {**registration["task"], "files": registration["task_files"]}
        team = registration["teams"][cell["team"]]
        found, facts = inspect(paths[0], cell, team, task, verifier)
    row = rows.get(cell["ticket"])
    mismatch = collect_mismatch(row, facts, cell["mode"])
    if row is not None:
        mismatch.extend(collect_status(row, presence))
    return {
        "ticket": cell["ticket"],
        "presence": presence,
        "facts": facts,
        "states": dict(found.states),
        "mismatch": mismatch,
        "checks": arm_checks(presence, found, facts, slot, mismatch),
    }


def effect(continued: Record | None, sealed: Record | None) -> str:
    if continued is None or sealed is None:
        return "unmeasured"
    kept, held = continued["reward"], sealed["reward"]
    if kept == held:
        return "equal_final"
    if continued["change"] == "damage" and held == 1:
        return "prevented_replacement"
    if continued["change"] == "repair" and held == 0:
        return "forfeited_repair"
    return "other_difference"


def pair_checks(block: Block, arms: dict[str, Record]) -> dict[str, bool]:
    _, schedule, _, fixture = block
    incumbent = int(fixture == "planner-fit")
    continued, sealed = arms["continue"]["facts"], arms["seal"]["facts"]
    traces = [
        facts["schedule"]["trace"] if facts["schedule"] else None
        for facts in (continued, sealed)
    ]
    natives = [continued["native"], sealed["native"]]
    boards = [continued["board"], sealed["board"]]
    grades = tuple(board["reward"] if board else None for board in boards)
    checks = {
        "registered_pair": all(
            arms[mode]["presence"] != "unregistered" for mode in MODES
        ),
        "same_trace": traces[0] is not None and traces[0] == traces[1],
        "same_task": all(natives) and natives[0]["checksum"] == natives[1]["checksum"],
    }
    if schedule == "held-early":
        checks["seal_prevents_or_forfeits"] = grades == (1 - incumbent, incumbent)
    elif schedule == "held-leave":
        actions = tuple(board["final_action"] if board else None for board in boards)
        checks["equal_reward_distinct_behavior"] = grades == (1, 1) and actions == (
            "leave",
            "none",
        )
    else:
        checks["late_decision_noop"] = grades == (1 - incumbent, 1 - incumbent)
    return checks


def case_of(arms: dict[str, Record], failed: list[str]) -> str:
    presence = {arms[mode]["presence"] for mode in MODES}
    for code in ("unregistered", "missing", "ambiguous"):
        if code in presence:
            return code
    for code, check in (
        ("native_failure", "native_complete"),
        ("board_unverified", "board_replayed"),
        ("decision_unsupported", "decision_supported"),
    ):
        if any(name.endswith(f".{check}") for name in failed):
            return code
    return "supported" if not failed else "prediction_failed"


def public_arm(record: Record, mode: str) -> Record:
    facts = record["facts"]
    board, seal, decision = facts["board"], facts["seal"], facts["decision"]
    receipts = facts["receipts"]
    return {
        "ticket": record["ticket"],
        "mode": mode,
        "presence": record["presence"],
        "evidence": record["states"],
        "final_grade": board["reward"] if board else None,
        "final_action": board["final_action"] if board else None,
        "dispatcher_terminal": receipts["dispatcher"] if receipts else None,
        "decision": decision["case"],
        "ack": decision["ack"],
        "decision_grade": decision["grade"],
        "writes_after_decision": decision["writes_after"],
        "refusals": len(seal["refusals"]) if seal else None,
        "truncated": seal["truncated"] if seal else None,
        "collect_mismatch": record.get("mismatch", []),
        "failed": [name for name, ok in record["checks"].items() if not ok],
    }


def qualify_block(
    block: Block,
    cells: dict[tuple[Block, str], Record],
    registration: Record | None,
    context: tuple[list[Path], types.ModuleType, dict[str, Record]],
) -> Record:
    arms = {
        mode: arm((block, mode), cells.get((block, mode)), registration, context)
        for mode in MODES
    }
    pair = pair_checks(block, arms)
    failed = [name for name, ok in pair.items() if not ok]
    for mode in MODES:
        failed.extend(
            f"{mode}.{name}" for name, ok in arms[mode]["checks"].items() if not ok
        )
    count = len(pair) + sum(len(arms[mode]["checks"]) for mode in MODES)
    team, schedule, seed, fixture = block
    return {
        "team": team,
        "schedule": schedule,
        "seed": seed,
        "fixture": fixture,
        "case": case_of(arms, failed),
        "effect": effect(
            arms["continue"]["facts"]["board"], arms["seal"]["facts"]["board"]
        ),
        "checks": count,
        "failed": failed,
        "arms": [public_arm(arms[mode], mode) for mode in MODES],
    }


def load_inputs(
    registration_path: Path, roots: list[Path]
) -> tuple[Record | None, Record | None, list[str]]:
    errors: list[str] = []
    registration: Record | None = None
    report: Record | None = None
    try:
        registration = load_registration(registration_path)
    except (OSError, ValueError, Invalid, Incomplete):
        errors.append("registration_loaded")
    try:
        report = collect(registration_path, roots)
    except (OSError, ValueError, Invalid, Incomplete):
        errors.append("collect_ran")
    return registration, report, errors


def registered_cells(
    registration: Record | None, blocks: list[Block]
) -> tuple[dict[tuple[Block, str], Record], bool]:
    if registration is None:
        return {}, False
    planned = {(block, mode) for block in blocks for mode in MODES}
    cells: dict[tuple[Block, str], Record] = {}
    extra = 0
    for cell in registration["cells"]:
        key = (
            (cell["team"], cell["schedule"], cell["seed"], cell["fixture"]),
            cell["mode"],
        )
        if key in planned and key not in cells:
            cells[key] = cell
        else:
            extra += 1
    return cells, extra == 0 and len(cells) == len(planned)


def scripted_only(registration: Record | None) -> bool:
    if registration is None or not is_control(registration):
        return False
    return all(
        member.get("import_path") == CONTROL_IMPORT
        and not {"model_name", "name", "base_url", "model_api_key_name"} & set(member)
        for team in registration["teams"].values()
        for member in team
    )


def global_checks(
    errors: list[str],
    registration: Record | None,
    population: bool,
    pairs: list[Record],
) -> dict[str, bool]:
    early = Counter(
        pair["effect"] for pair in pairs if pair["schedule"] == "held-early"
    )
    checks = {
        "registration_loaded": "registration_loaded" not in errors,
        "collect_ran": "collect_ran" not in errors,
        "registered_population": population,
        "scripted_only": scripted_only(registration),
        "held_early_tally": early
        == Counter(prevented_replacement=2, forfeited_repair=2),
    }
    return checks


def summary(report: Record | None) -> Record | None:
    if report is None:
        return None
    return {
        key: report.get(key)
        for key in (
            "registered",
            "statuses",
            "journals",
            "decisions",
            "gates",
            "comparisons",
            "directories",
        )
    }


def tally(values: Iterable[str], names: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(values)
    assert set(counts) <= set(names)
    return {name: counts[name] for name in names}


def qualify(registration_path: Path, roots: list[Path]) -> Record:
    assert 1 <= len(roots) <= MAX_ROOTS
    blocks = question_blocks(load_question())
    assert len(blocks) == CONTROL_BLOCKS
    registration, report, errors = load_inputs(registration_path, roots)
    cells, population = registered_cells(registration, blocks)
    rows = {row["ticket"]: row for row in report["cells"]} if report else {}
    context = (roots, load_verifier(), rows)
    pairs = [qualify_block(block, cells, registration, context) for block in blocks]
    assert len(pairs) == CONTROL_BLOCKS
    overall = global_checks(errors, registration, population, pairs)
    failed = [name for name, ok in overall.items() if not ok]
    arms = [entry for pair in pairs for entry in pair["arms"]]
    return {
        "schema": SCHEMA,
        "source": "native scripted seats; no model calls",
        "task": registration["task"]["name"] if registration else None,
        "planned_pairs": len(pairs),
        "registered_arms": len(cells),
        "checks": len(overall) + sum(pair["checks"] for pair in pairs),
        "failed_checks": len(failed) + sum(len(pair["failed"]) for pair in pairs),
        "failed": failed,
        "cases": tally((pair["case"] for pair in pairs), CASES),
        "effects": tally((pair["effect"] for pair in pairs), EFFECTS),
        "presence": tally((entry["presence"] for entry in arms), PRESENCE),
        "decisions": tally((entry["decision"] for entry in arms), DECISION_CASES),
        "collect": summary(report),
        "pairs": pairs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -BS -m examples.seat_seals.qualify")
    parser.add_argument("registration", type=Path, metavar="REG")
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIAL_ROOT")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if len(args.roots) > MAX_ROOTS:
        parser.error(f"give at most {MAX_ROOTS} trial roots")
    result = qualify(args.registration, args.roots)
    print(json.dumps(result, indent=2))
    return int(result["failed_checks"] != 0)


if __name__ == "__main__":
    sys.exit(main())
