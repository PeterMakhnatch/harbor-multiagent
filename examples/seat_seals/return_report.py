import argparse
import json
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

from ..native_evidence import Incomplete, Invalid, Record, native_reward, read_bytes
from ..native_evidence import require, require_usage, same, strict_object
from ..native_study import DIGEST, NATIVE_DEFAULTS, as_choice, as_int, as_list
from ..native_study import as_record, as_text, check_task
from ..seat_checkpoints.bracket import HOST_SCHEMA, LEGACY_HOST_SCHEMA, SAMPLE_KEYS
from ..seat_checkpoints.bracket import SeatCutInvalid, _check_lifecycle
from ..seat_checkpoints.bracket import _match_marks, _validate_host, _validate_world
from ..seat_checkpoints.collect import Exit, _exits, _host, _marker_log
from ..seat_checkpoints.collect import _message_file, _terminal
from ..seat_checkpoints.lifecycle import OPEN_STATES, native_disposition
from ..seat_checkpoints.lifecycle import terminal_state
from ..seat_checkpoints.native_timeout import classify, deadline_seconds
from ..seat_handoffs.native_records import read_record
from ..shift_board.collect import CONFIG, LOCK, MAX_CONFIG_BYTES, MAX_RESULT_BYTES
from ..shift_board.collect import MAX_SMALL_BYTES, RESULT, SEATS, _quarantined
from ..shift_board.collect import _scan, _tsv, load_verifier
from ..shift_board.plan import FIXTURES, MAX_SEED, TICKET
from .collect import DECISIONS, STATUSES, Native, _check_config, _check_lock
from .collect import _check_refusals, _check_result, _commit, _error_reason
from .collect import _graded, _seal_log
from .contract import RULE, TASK_NAME, control_team

SCHEMA = "harbor.seat-seal.return-report/v1"
REGISTRATION_SCHEMA = "harbor.seat-seal.return-check/v1"
WITNESS_SCHEMA = "harbor.seat-seal.return-witness/v1"
WITNESS = ("return-witness.json",)
RELEASE = "seats/dispatcher/seat-cut-control/release-before_decide"
TARGET, PEER, MODE, CASES = "planner", "dispatcher", "seal", 8
OBSERVERS = {"v1": LEGACY_HOST_SCHEMA, "v2": HOST_SCHEMA}
EXITS = {"return": "returned", "cancel": "cancelled"}
STOCK_TEAM = {
    member["seat"]: {**NATIVE_DEFAULTS, "skip_model_parsing": False, **member}
    for member in control_team("preferred")
}
CASE_ORDER = ("id", "ticket", "observer", "exit", "fixture", "seed", "mode")
EXPECTED_KEYS = tuple(
    "native_planner native_dispatcher planner_task_cancelled target_when_peer_waits"
    " board_decision refused_peer_write reward".split()
)
CHECK_KEYS = ("observer", "exit", "terminal", *EXPECTED_KEYS)
GATE_TRACE = (
    "arrived:planner:after_publish arrived:dispatcher:before_decide"
    " witness:running:running release:planner:after_publish planner:done"
    " terminal:{}:running release:dispatcher:before_decide"
    " arrived:dispatcher:after_decide release:dispatcher:after_decide"
).split()
CANCELLING = ("entry_cancelling", "action_cancelling", "done_cancelling")
WITNESS_PARTS = {
    "declared": frozenset(CASE_ORDER[2:]),
    "observer": frozenset({"host_schema", "source"}),
    "planner": frozenset({"stock", "action", "done", *CANCELLING}),
    "gate": frozenset(
        {"status", "trace", "terminal_sample", "peer_at_release", "release_marker"}
    ),
}
WITNESS_FIELDS = frozenset({"schema", "case", "ticket", "trial_id", "order"})
SECTIONS = {
    "grade": ("reward", "native_reward", "final_action"),
    "host": ("version",),
    "terminal": ("target", "after_seq", "prefix_grade", "prefix_writes"),
    "world": ("after_seq", "decision_grade", "writes_after", "refusals")
    + ("truncated", "peer_refused", "on_terminal"),
    "witness": ("planner_stock", "planner_action", "planner_done", "voluntary"),
}
STATES = {
    "grade": ("verified", "conflict", "unverified", "unavailable"),
    "host": ("valid", "mismatch", "conflict", "absent", "invalid", "unavailable"),
    "terminal": ("acknowledged", "none", "conflict", "unavailable"),
    "world": DECISIONS,
    "witness": ("valid", "incomplete", "mismatch", "absent", "invalid", "unavailable"),
}
RESULTS = ("match", "mismatch", "conflict", "unavailable")
VERDICTS = ("pass", "fail", "unresolved")


class Read(NamedTuple):
    native_id: str
    exits: dict[str, Exit]
    seats: dict[str, Record]
    error: str | None
    grade: Record
    cut: Native | None


def _blank(section: str, status: str, reason: str | None) -> Record:
    assert status in STATES[section]
    return {"status": status, "reason": reason, **dict.fromkeys(SECTIONS[section])}


def _failed(section: str, problem: Exception) -> Record:
    status = "absent" if isinstance(problem, Incomplete) else "invalid"
    return _blank(section, status, str(problem))


def load(path: Path) -> Record:
    record = read_record(path, 1 << 20)
    require_usage(record is not None, f"registration {path} does not exist")
    assert record is not None
    require_usage(record.get("schema") == REGISTRATION_SCHEMA, "registration schema")
    check_task(record.get("task"), TASK_NAME)
    as_text(record.get("task_files"), "full task files", DIGEST)
    cases = as_list(record.get("cases"), "cases", CASES, CASES)
    for case in cases:
        as_record(case, "case", frozenset(CASE_ORDER))
        require_usage(type(case["id"]) is str and case["mode"] == MODE, "case id/mode")
        as_text(case["ticket"], "case ticket", TICKET)
        as_choice(case["observer"], "case observer", OBSERVERS)
        as_choice(case["exit"], "case exit", EXITS)
        as_choice(case["fixture"], "case fixture", FIXTURES)
        as_int(case["seed"], "case seed", 0, MAX_SEED)
    require_usage(
        len({(c["observer"], c["exit"], c["fixture"]) for c in cases}) == CASES
        and len({c["id"] for c in cases}) == len({c["ticket"] for c in cases}) == CASES
        and len({c["seed"] for c in cases}) == 1,
        "cases do not cross observer, exit and fixture once at one seed",
    )
    expected = as_record(record.get("expected"), "expected")
    require_usage(set(expected) == {c["id"] for c in cases}, "expected names cases")
    for name, row in expected.items():
        as_record(row, f"expected {name}", frozenset(EXPECTED_KEYS))
    return record


def _cell(case: Record) -> Record:
    keys = ("ticket", "seed", "fixture", "mode", "observer")
    cell = {key: case[key] for key in keys}
    return cell | {"rule": RULE, "target": TARGET, "peer": PEER}


def _seats(result: Record, exits: dict[str, Exit]) -> dict[str, Record]:
    seats: dict[str, Record] = {}
    for row in result["seat_results"]:
        try:
            code = native_disposition(exits[row["seat"]].value)
        except ValueError as problem:
            raise Invalid(f"native {row['seat']} disposition: {problem}") from None
        error = row.get("exception_info")
        seats[row["seat"]] = {
            "disposition": "unknown" if code is None else code,
            "error": error.get("exception_type") if type(error) is dict else None,
        }
    return seats


def _native(
    trial: Path, cell: Record, task: Record, verifier: types.ModuleType
) -> Read:
    result = strict_object(read_bytes(trial, RESULT, MAX_RESULT_BYTES), "result.json")
    native_id = _check_result(result, cell, STOCK_TEAM, task)
    failed = result.get("exception_info") is not None
    message = _message_file(trial) if failed else None
    error = _error_reason(classify(result, SEATS, deadline_seconds(False), message))
    config = strict_object(read_bytes(trial, CONFIG, MAX_CONFIG_BYTES), "config.json")
    _check_config(config, cell, STOCK_TEAM, "config.json")
    lock = strict_object(read_bytes(trial, LOCK, MAX_CONFIG_BYTES), "lock.json")
    _check_lock(lock, cell, STOCK_TEAM, task)
    exits = _exits(result)
    require(exits is not None, "native seat rows are not both seats")
    assert exits is not None
    seats = _seats(result, exits)
    grade = _blank("grade", "unavailable", "native reward is not valid")
    if result.get("reward_valid") is not True:
        return Read(native_id, exits, seats, error, grade, None)
    grade.update(native_reward=native_reward(result), reason=None)
    try:
        cut = _graded(trial, cell, result, native_id, error, verifier)
    except (Incomplete, Invalid) as problem:
        status = "unverified" if isinstance(problem, Incomplete) else "conflict"
        grade.update(status=status, reason=str(problem))
        return Read(native_id, exits, seats, error, grade, None)
    grade.update(status="verified", reward=cut.grade, final_action=cut.final_action)
    return Read(native_id, exits, seats, error, grade, cut)


def _final(found: Exit, legacy: bool) -> str:
    if not legacy:
        return _terminal(found, False)
    if found.error or found.scope == "finished":
        return terminal_state(None, found.error, legacy=True)
    return found.scope


def _host_row(trial: Path, cell: Record, read: Read) -> tuple[Record, Record | None]:
    try:
        host = _host(trial, cell, read.native_id)
        if host is None:
            return _blank("host", "absent", "host journal is missing"), None
        samples = _validate_host(host)[2]
    except (Invalid, SeatCutInvalid) as problem:
        return _blank("host", "invalid", str(problem)), None
    legacy = host["schema"] == LEGACY_HOST_SCHEMA
    row = _blank("host", "valid", None) | {"version": "v1" if legacy else "v2"}
    try:
        finals = [_final(read.exits[seat], legacy) for seat in (TARGET, PEER)]
        _check_lifecycle(samples, *finals)
    except (Invalid, SeatCutInvalid) as problem:
        row.update(status="conflict", reason=str(problem))
    if row["status"] == "valid" and row["version"] != cell["observer"]:
        row.update(status="mismatch", reason="host schema is not the case observer")
    return row, {"raw": host["samples"], "samples": samples}


def _event(event: Record) -> str:
    label = f"{event['seat']}:{event['type']}"
    return f"{label}:{event['outcome']}" if event["type"] == "publish" else label


def _terminal_row(
    trial: Path, cell: Record, read: Read, journal: Record | None
) -> tuple[Record, dict[str, int] | None, int | None]:
    cut = read.cut
    if cut is None or journal is None:
        why = "no verified cut and readable host journal"
        return _blank("terminal", "unavailable", why), None, None
    assert cut.run and cut.events is not None and cut.scores and cut.revisions
    try:
        marks = _validate_world(_marker_log(trial, cell, cut.run))[1]
        samples = journal["samples"]
        ordinals = _match_marks(samples, marks, cut.revisions, len(cut.events))
    except Incomplete as problem:
        return _blank("terminal", "unavailable", str(problem)), None, None
    except (Invalid, SeatCutInvalid) as problem:
        return _blank("terminal", "conflict", str(problem)), None, None
    for index, sample in enumerate(samples):
        before = sample.before
        if sample.ack != "ok" or sample.after != before or before.peer != "running":
            continue
        if before.target in OPEN_STATES:
            continue
        after = ordinals[sample.mark_id]
        writes = [_event(e) for e in cut.events[:after] if e["type"] != "view"]
        row = _blank("terminal", "acknowledged", None)
        row.update(
            target=before.target,
            after_seq=after,
            prefix_grade=cut.scores[after],
            prefix_writes=writes,
        )
        return row, ordinals, index
    why = "no acknowledged terminal sample with the dispatcher running"
    return _blank("terminal", "none", why), ordinals, None


def _world(
    trial: Path,
    cell: Record,
    read: Read,
    ordinals: dict[str, int] | None,
    mark: str | None,
) -> Record:
    if read.cut is None or read.cut.run is None:
        return _blank("world", "unknown", "no verified native cut")
    try:
        seal = _seal_log(trial, cell, read.cut.run)
    except (Incomplete, Invalid) as problem:
        return _failed("world", problem)
    decision, refusals = seal["decision"], seal["refusals"]
    refused = any(refusal["seat"] == PEER for refusal in refusals)
    row = _blank("world", "none" if decision is None else "committed", None)
    row.update(
        refusals=len(refusals),
        truncated=seal["truncated"],
        peer_refused=True if refused else None if seal["truncated"] else False,
    )
    try:
        if decision is not None:
            committed: Record = {}
            _commit(committed, seal, cell, read.cut)
            row.update(
                after_seq=committed["after_seq"],
                decision_grade=committed["grade"],
                writes_after=committed["writes_after"],
                on_terminal=None if mark is None else decision["id"] == mark,
            )
            if ordinals is not None:
                linked = ordinals.get(decision["id"]) == row["after_seq"]
                require(linked, "seal decision differs from its world marker")
        _check_refusals(seal, cell, read.cut)
    except Invalid as problem:
        return row | {"status": "invalid", "reason": str(problem)}
    if decision is None and seal["truncated"]:
        row.update(status="unknown", reason="world write fault before any decision")
    return row


def _trace(target: object) -> list[str]:
    return [step.format(target) for step in GATE_TRACE]


def _follows(order: object, steps: list[str]) -> bool:
    remaining = iter(order if type(order) is list else ())
    return all(step in remaining for step in steps)


def _zero(value: object, nullable: bool) -> bool:
    return (nullable and value is None) or (type(value) is int and value == 0)


def _released(trial: Path, gate: Record) -> bool:
    try:
        read_bytes(trial, tuple(RELEASE.split("/")), MAX_SMALL_BYTES)
    except (Incomplete, Invalid):
        return False
    return gate["release_marker"] == RELEASE


def _witness_problem(
    trial: Path, witness: Record, case: Record, read: Read, context: tuple[Any, ...]
) -> str | None:
    host, journal, index = context
    planner, gate, sample = witness["planner"], witness["gate"], None
    raw = journal["raw"][index]
    if type(gate["terminal_sample"]) is dict:
        sample = gate["terminal_sample"]
    schema = witness["observer"]["host_schema"]
    trace = _trace(raw["before"]["target"])
    steps = ["planner:entry", trace[3], "planner:stock:returned"]
    steps += [f"planner:action:{planner['action']}", f"planner:done:{planner['done']}"]
    rules = (
        (witness["case"] == case["id"], "witness names another case"),
        (witness["ticket"] == case["ticket"], "witness names another ticket"),
        (witness["trial_id"] == read.native_id, "witness names another trial"),
        (
            same(witness["declared"], {k: case[k] for k in WITNESS_PARTS["declared"]}),
            "witness declares another case",
        ),
        (schema == OBSERVERS[case["observer"]], "witness ran another observer"),
        (schema == OBSERVERS[host["version"]], "witness differs from the host"),
        (
            all(_zero(planner[k], k != CANCELLING[0]) for k in CANCELLING),
            "planner task saw an outside cancellation request",
        ),
        (
            sample is not None
            and set(sample) == SAMPLE_KEYS | {"index"}
            and sample["index"] == index
            and all(same(sample[key], raw[key]) for key in SAMPLE_KEYS),
            "witness terminal sample is not the host terminal sample",
        ),
        (gate["peer_at_release"] == "running", "dispatcher was not running"),
        (_released(trial, gate), "dispatcher release marker is absent"),
        (gate["trace"] == trace, "gate trace differs from the protocol"),
        (
            _follows(witness["order"], [*steps, *trace[5:7]]),
            "planner exit does not follow the stock return and precede release",
        ),
    )
    return next((reason for held, reason in rules if not held), None)


def _witness(trial: Path, case: Record, read: Read, context: tuple[Any, ...]) -> Record:
    try:
        witness = strict_object(read_bytes(trial, WITNESS, 64 << 10), "witness")
        require(
            set(witness) == WITNESS_FIELDS | set(WITNESS_PARTS)
            and witness["schema"] == WITNESS_SCHEMA,
            "witness fields or schema differ",
        )
        for key, fields in WITNESS_PARTS.items():
            part = witness[key]
            require(type(part) is dict and set(part) == fields, f"{key} differs")
    except (Incomplete, Invalid) as problem:
        return _failed("witness", problem)
    planner, gate = witness["planner"], witness["gate"]
    row = _blank("witness", "valid", None)
    row.update(
        planner_stock=planner["stock"],
        planner_action=planner["action"],
        planner_done=planner["done"],
        voluntary=all(_zero(planner[k], True) for k in CANCELLING),
    )
    if gate["status"] != "complete" or None in (planner["action"], planner["done"]):
        return row | {"status": "incomplete", "reason": "gate or planner exit is open"}
    if context[2] is None or context[0]["version"] is None:
        return row | {"status": "incomplete", "reason": "host cannot corroborate it"}
    problem = _witness_problem(trial, witness, case, read, context)
    return row if problem is None else row | {"status": "mismatch", "reason": problem}


def _evidence(trial: Path | None, case: Record, read: Read | None) -> Record:
    if trial is None or read is None:
        why = "no validated native trial"
        sections = {key: _blank(key, STATES[key][-1], why) for key in SECTIONS}
        return {"native": {"error": None, "seats": None}, **sections}
    cell = _cell(case)
    host, journal = _host_row(trial, cell, read)
    terminal, ordinals, index = _terminal_row(trial, cell, read, journal)
    mark = None if index is None or journal is None else journal["raw"][index]["id"]
    return {
        "native": {"error": read.error, "seats": read.seats},
        "grade": read.grade,
        "host": host,
        "terminal": terminal,
        "world": _world(trial, cell, read, ordinals, mark),
        "witness": _witness(trial, case, read, (host, journal, index)),
    }


def _observed(row: Record) -> Record:
    seats = row["native"]["seats"] or {}
    witness, world = row["witness"], row["world"]
    known = world["status"] in ("committed", "none")
    cancelled = witness["planner_done"] == "cancelled"
    return {
        "native_planner": (seats.get(TARGET) or {}).get("disposition"),
        "native_dispatcher": (seats.get(PEER) or {}).get("disposition"),
        "planner_task_cancelled": cancelled if witness["status"] == "valid" else None,
        "target_when_peer_waits": row["terminal"]["target"],
        "board_decision": world["status"] == "committed" if known else None,
        "refused_peer_write": world["peer_refused"] if known else None,
        "reward": row["grade"]["reward"],
    }


def _checks(row: Record, case: Record) -> Record:
    host, terminal, witness = row["host"]["status"], row["terminal"], row["witness"]
    invalid_world = row["world"]["status"] == "invalid"
    conflicted = {
        "planner_task_cancelled": witness["status"] == "mismatch",
        "target_when_peer_waits": "conflict" in (host, terminal["status"]),
        "board_decision": invalid_world,
        "refused_peer_write": invalid_world,
        "reward": row["grade"]["status"] == "conflict",
    }
    claimed = ("returned", case["exit"], EXITS[case["exit"]])
    keys = ("planner_stock", "planner_action", "planner_done")
    exited = tuple(witness[key] for key in keys) == claimed
    checks = {
        "observer": {"valid": "match", "mismatch": "mismatch", "conflict": "conflict"}
        .get(host, "unavailable"),
        "exit": {"valid": "match" if exited else "mismatch", "mismatch": "conflict"}
        .get(witness["status"], "unavailable"),
        "terminal": {"acknowledged": "match", "none": "mismatch"}
        .get(terminal["status"], terminal["status"]),
    }
    for key in EXPECTED_KEYS:
        value = row["observed"][key]
        if conflicted.get(key, False):
            checks[key] = "conflict"
        elif value is None:
            checks[key] = "unavailable"
        else:
            checks[key] = "match" if value == row["expected"][key] else "mismatch"
    assert set(checks.values()) <= set(RESULTS) and list(checks) == list(CHECK_KEYS)
    return checks


def _intervention(row: Record) -> bool | None:
    world, terminal, witness = row["world"], row["terminal"], row["witness"]
    if world["status"] != "committed":
        return False if world["status"] == "none" else None
    if terminal["status"] != "acknowledged" or witness["status"] != "valid":
        return None
    return (
        world["on_terminal"] is True
        and terminal["target"] == "clean"
        and witness["planner_action"] == "return"
        and witness["planner_done"] == "returned"
    )


def _case_row(
    case: Record, paths: list[Path], quarantined: frozenset[str], context: Any
) -> tuple[Record, bool]:
    task, expected, verifier = context
    status, reason, read = "missing", None, None
    if case["ticket"] in quarantined:
        status, reason = "duplicate_id", "a native or world id repeats elsewhere"
    elif len(paths) > 1:
        status, reason = "ambiguous", f"{len(paths)} directories claim the ticket"
    elif paths:
        try:
            read = _native(paths[0], _cell(case), task, verifier)
            status = "graded" if read.error is None else "native_error"
            reason = read.error
        except (Incomplete, Invalid) as problem:
            status = "incomplete" if isinstance(problem, Incomplete) else "invalid"
            reason = str(problem)
    row: Record = {key: case[key] for key in CASE_ORDER}
    row |= {"status": status, "reason": reason}
    row |= _evidence(paths[0] if paths else None, case, read)
    row["intervention"] = _intervention(row)
    row["expected"] = expected[case["id"]]
    row["observed"] = _observed(row)
    row["checks"] = _checks(row, case)
    results = set(row["checks"].values())
    passed = "pass" if results == {"match"} else "unresolved"
    row["verdict"] = "fail" if "mismatch" in results else passed
    assert row["status"] in STATUSES
    assert all(row[key]["status"] in STATES[key] for key in SECTIONS)
    return row, read is not None


def _pairs(rows: list[Record]) -> list[Record]:
    pairs = []
    for observer in OBSERVERS:
        for fixture in FIXTURES:
            arms = {
                row["exit"]: row
                for row in rows
                if (row["observer"], row["fixture"]) == (observer, fixture)
            }
            writes = [arms[name]["terminal"]["prefix_writes"] for name in EXITS]
            prefix = "same" if writes[0] == writes[1] else "differs"
            pair = {"observer": observer, "fixture": fixture}
            pair["prefix_at_terminal"] = "unavailable" if None in writes else prefix
            for name in EXITS:
                for key in ("board_decision", "refused_peer_write", "reward"):
                    pair[f"{name}_{key}"] = arms[name]["observed"][key]
            pairs.append(pair)
    return pairs


def _count(rows: list[Record], values: tuple[str, ...], pick: Any) -> Record:
    counts = Counter(pick(row) for row in rows)
    assert set(counts) <= set(values)
    return {value: counts[value] for value in values}


def report(registration_path: Path, roots: list[Path]) -> Record:
    registration = load(registration_path)
    verifier = load_verifier()
    cases = registration["cases"]
    claims, directories = _scan(roots, frozenset(case["ticket"] for case in cases))
    quarantined = _quarantined(claims, verifier)
    task = {**registration["task"], "files": registration["task_files"]}
    context = (task, registration["expected"], verifier)
    built = [
        _case_row(case, claims.get(case["ticket"], []), quarantined, context)
        for case in cases
    ]
    rows = [row for row, _ in built]
    roster = "same" if all(bound for _, bound in built) else "partial"
    verdicts = _count(rows, VERDICTS, lambda row: row["verdict"])
    complete = verdicts["pass"] == CASES and roster == "same"
    verdict = "fail" if verdicts["fail"] else "pass" if complete else "unresolved"
    return {
        "schema": SCHEMA,
        "registered": len(rows),
        "directories": directories,
        "statuses": _count(rows, STATUSES, lambda row: row["status"]),
        "grades": _count(rows, STATES["grade"], lambda row: row["grade"]["status"]),
        "verdicts": verdicts,
        "roster": roster,
        "verdict": verdict,
        "cases": rows,
        "pairs": _pairs(rows),
    }


def render(found: Record) -> str:
    totals = {key: found[key] for key in ("registered", "roster", "verdict")}
    for key in ("statuses", "grades", "verdicts", "directories"):
        totals |= {f"{key}_{name}": value for name, value in found[key].items()}
    lines = []
    for row in found["cases"]:
        line = {key: row[key] for key in ("id", "status", "intervention", "verdict")}
        line |= {f"seen_{key}": value for key, value in row["observed"].items()}
        open_checks = [key for key, value in row["checks"].items() if value != "match"]
        lines.append(line | {"open": ",".join(open_checks) or None})
    sections = [_tsv([totals]), _tsv(lines), _tsv(found["pairs"])]
    return "\n\n".join("\n".join(section) for section in sections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="examples.seat_seals.return_report")
    parser.add_argument("registration", type=Path, metavar="REG")
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIAL_ROOT")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        found = report(args.registration, args.roots)
    except (OSError, ValueError) as error:
        print(f"return_report: {error}", file=sys.stderr)
        return 2
    print(json.dumps(found, indent=2) if args.json else render(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
