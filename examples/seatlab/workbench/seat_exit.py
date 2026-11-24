import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ...seat_handoffs.native_records import read_record_with_digest

SCHEMA = "seatlab.workbench.seat-exit/v1"
REGISTRATION_SCHEMA = "seatlab.native-disposition-control/v1"
OUTCOMES_SCHEMA = "seatlab.seat-exit-contract.outcomes/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.seat_exit"
MAX_BYTES = 1024 * 1024
MAX_ROWS = 16
MAX_EVENTS = 64
MAX_COUNT = 10**6
CONTROLLED = "hello-seats"
TASKS = {
    "hello-seats": {"p1": "tested", "p2": "supporting"},
    "ship-handover": {"outgoing": "tested", "relief": "tested"},
}
ROLES = {"under_test": "tested", "supporting": "supporting"}
DISPOSITIONS = ("returned", "raised", "cancelled", "stop_requested")
TIMEOUTS = {"AgentTimeoutError": "play", "AgentSetupTimeoutError": "setup"}
MARKS = {
    "hold_cancelled": ("stop_requested", "cancelled"),
    "hold_cancel_swallowed": ("stop_requested", "result"),
    "managed_fault_raised": ("raised", "result"),
    "spontaneous_cancel_raised": ("cancelled", "cancelled"),
}
GRADES = ("valid_positive", "valid_zero", "invalid_or_no_grade", "missing")
LEGACY = {
    "archived_native": ("trial_directories", "seats", "seats_disposition_unknown", "grade_one", "grade_zero", "invalid_without_rewards"),
    "older_scripted": ("trials", "seats", "seats_disposition_unknown", "grade_one", "grade_zero", None),
}
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
EXCEPTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
EVENT = re.compile(r"(driver|host|p1|p2):([a-z][a-z0-9_-]{0,47})(?:\((result|cancelled|returned)\))?")
CAVEAT = (
    "Control events are the released redacted harness log. They corroborate reader dispositions "
    "but do not authenticate raw trials. Grades sit beside dispositions and never enter the pass. "
    "Legacy counts are release aggregates, not reproduced raw trials; they can fail the pass but never make it. "
    "The secondary diagnosis is outside the registered denominator."
)

Record = dict[str, Any]


class Malformed(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Malformed(reason)


def _load(path: Path, label: str, schema: str) -> Record:
    try:
        read = read_record_with_digest(path, MAX_BYTES)
    except (OSError, ValueError) as error:
        raise Malformed(f"{label} is unreadable, oversized or not strict JSON") from error
    _require(read is not None and read[0].get("schema") == schema, f"{label} is missing or has another schema")
    assert read is not None
    return read[0]


def _registration(reg: Record) -> dict[str, tuple[str, dict[str, str]]]:
    roles = reg.get("roles")
    _require(isinstance(roles, dict) and roles.keys() == TASKS.keys(), "registration tasks differ from the pinned tasks")
    for task, seats in roles.items():
        mapped = {seat: ROLES.get(role) if isinstance(role, str) else None for seat, role in seats.items()} if isinstance(seats, dict) else None
        _require(mapped == TASKS[task], "registration roles differ from the pinned tasks")
    expected = reg.get("expected")
    _require(isinstance(expected, dict) and 0 < len(expected) <= MAX_ROWS, "registration expectations are malformed")
    cases = {}
    for case, seats in expected.items():
        task = next((name for name, pinned in TASKS.items() if isinstance(seats, dict) and seats.keys() == pinned.keys()), None)
        _require(NAME.fullmatch(case) is not None and task is not None, "registered case matches no pinned task")
        _require(all(value in DISPOSITIONS for value in seats.values()), "registered disposition is not supported")
        cases[case] = (task, dict(seats))
    return cases


def _grade(grade: object) -> Record:
    _require(isinstance(grade, dict), "grade is malformed")
    assert isinstance(grade, dict)
    validity, status, rewards = (grade.get(key) for key in ("validity", "rewards_status", "rewards"))
    _require(validity in ("valid", "invalid", "unknown"), "grade validity is not supported")
    if status == "not_recorded" and rewards is None:
        return {"class": "invalid_or_no_grade", "rewards": None}
    binary = isinstance(rewards, dict) and rewards.keys() == {"reward"} and type(rewards["reward"]) in (int, float)
    _require(status == "recorded" and binary and rewards["reward"] in (0, 1), "grade is not one binary reward")
    reward = rewards["reward"]
    if validity != "valid":
        return {"class": "invalid_or_no_grade", "rewards": {"reward": reward}}
    return {"class": "valid_positive" if reward == 1 else "valid_zero", "rewards": {"reward": reward}}


def _reader(reader: object, task: str) -> tuple[Record, dict[str, Record]]:
    _require(isinstance(reader, dict), "reader is malformed")
    assert isinstance(reader, dict)
    raw, pinned = reader.get("seats"), TASKS[task]
    _require(raw is None or (isinstance(raw, list) and len(raw) <= len(pinned)), "reader seats are malformed")
    seats: dict[str, Record] = {}
    for seat in raw or []:
        _require(isinstance(seat, dict), "reader seat is malformed")
        name, disposition, exception = (seat.get(key) for key in ("seat", "disposition", "exception_type"))
        _require(name in tuple(pinned) and name not in seats, "reader seat is unregistered for its task or duplicated")
        _require(seat.get("role") == pinned[name], "reader role conflicts with registration")
        _require(disposition in (*DISPOSITIONS, "unknown"), "reader disposition is not supported")
        _require(exception is None or (isinstance(exception, str) and EXCEPTION.fullmatch(exception) is not None), "reader exception is malformed")
        timeout = TIMEOUTS.get(exception or "")
        _require(seat.get("native_timeout") == timeout, "reader native timeout conflicts with its exception")
        seats[name] = {"disposition": disposition, "native_timeout": timeout}
    return _grade(reader.get("grade")), seats


def _seat_witness(events: list[tuple[str, ...]], seat: str) -> tuple[str | None, int | None, int | None]:
    done = [index for index, event in enumerate(events) if event[:2] == (seat, "seat_task_done")]
    _require(len(done) <= 1, "control events complete one seat twice")
    if not done:
        return None, None, None
    end, outcome = done[0], events[done[0]][2]
    before = {event[1]: index for index, event in enumerate(events[:end]) if event[0] == seat}
    marks = [name for name in MARKS if name in before]
    if len(marks) > 1:
        return None, None, end
    if marks:
        disposition, needed = MARKS[marks[0]]
        return (disposition if outcome == needed else None), before[marks[0]], end
    return ("returned" if "wrapper_returned" in before and outcome == "result" else None), end, end


def _witness(raw: object, reader: dict[str, Record], expected: dict[str, str]) -> Record:
    _require(isinstance(raw, list) and 0 < len(raw) <= MAX_EVENTS, "control events are malformed")
    assert isinstance(raw, list)
    matches = [EVENT.fullmatch(item) if isinstance(item, str) else None for item in raw]
    _require(all(matches), "control event is malformed")
    events = [match.groups() for match in matches if match]
    seen = {seat: _seat_witness(events, seat) for seat in expected}
    cancel = next((index for index, event in enumerate(events) if event[:2] == ("driver", "driver_cancel_issued")), None)
    causes = {}
    for seat, (disposition, at, _) in seen.items():
        if disposition != "stop_requested" or at is None:
            continue
        peers = [seen[peer][2] for peer, role in TASKS[CONTROLLED].items() if peer != seat and role == "tested"]
        finished = any(end is not None and end < at for end in peers)
        causes[seat] = "driver_cancel" if cancel is not None and cancel < at else "tested_seat_finished" if finished else "no_harness_cause"
    deadline = "no_harness_cause" in causes.values()
    timeout = all(
        reader.get(seat, {}).get("native_timeout", "absent") == ("play" if deadline and role == "tested" else None)
        for seat, role in TASKS[CONTROLLED].items()
    )
    finish = [event[2] for event in events if event[:2] == ("driver", "trial_run_finished")]
    harness = finish == ["cancelled" if cancel is not None else "returned"] and (("host", "hook_cancel", None) in events) == (cancel is not None)
    witnessed = {seat: found[0] for seat, found in seen.items()}
    agrees = all(witnessed[seat] is not None and witnessed[seat] == reader.get(seat, {}).get("disposition") for seat in expected)
    registered = witnessed == expected
    correct = harness and agrees and registered and timeout
    return {
        "status": "correct" if correct else "incorrect",
        "witnessed": witnessed,
        "stop_cause": causes,
        "harness_consistent": harness,
        "agrees_with_reader": agrees,
        "matches_registration": registered,
        "native_timeout": "correct" if timeout else "incorrect",
    }


def _attempts(outcomes: Record, cases: dict[str, tuple[str, dict[str, str]]]) -> dict[str, Record]:
    rows = outcomes.get("first_attempts")
    _require(isinstance(rows, list) and len(rows) <= MAX_ROWS, "first attempts are malformed")
    attempts: dict[str, Record] = {}
    for row in rows:
        _require(isinstance(row, dict), "first attempt is malformed")
        case = row.get("case")
        _require(case in tuple(cases) and case not in attempts, "first attempt case is unregistered or duplicated")
        task, expected = cases[case]
        _require(row.get("task") == task, "first attempt task conflicts with its registered case")
        grade, seats = _reader(row.get("reader"), task)
        raw = row.get("control_events")
        _require(task == CONTROLLED or raw is None, "uncontrolled task carries control events")
        control = None if raw is None else _witness(raw, seats, expected)
        attempts[case] = {"grade": grade, "seats": seats, "control": control}
    return attempts


def _slot(case: str, task: str, expected: dict[str, str], attempt: Record | None) -> Record:
    seats = {}
    for seat, want in expected.items():
        actual = attempt["seats"].get(seat, {}).get("disposition") if attempt else None
        status = "missing" if actual in (None, "unknown") else "matched" if actual == want else "incorrect"
        seats[seat] = {"expected": want, "actual": actual, "status": status}
    statuses = {entry["status"] for entry in seats.values()}
    outcome = "missing" if attempt is None else "correct" if statuses == {"matched"} else "incorrect" if "incorrect" in statuses else "incomplete"
    control = None
    if task == CONTROLLED:
        control = attempt["control"] if attempt and attempt["control"] else {"status": "missing"}
    grade = attempt["grade"] if attempt else {"class": "missing", "rewards": None}
    return {"case": case, "task": task, "attempt": outcome, "seats": seats, "grade": grade, "control": control}


def _count(record: Record, key: str) -> int:
    value = record.get(key)
    _require(type(value) is int and 0 <= value <= MAX_COUNT, "legacy count is malformed")
    return value


def _legacy(outcomes: Record) -> Record:
    older = outcomes.get("older_inputs")
    _require(isinstance(older, dict), "legacy inputs are malformed")
    report: Record = {"evidence": "release_aggregate", "raw_trials_reproduced": False}
    for section, keys in LEGACY.items():
        record = older.get(section)
        _require(isinstance(record, dict), "legacy section is malformed")
        trials, seats, unknown, one, zero, other = (_count(record, key) if key else None for key in keys)
        other = trials - one - zero if other is None else other
        _require(other >= 0 and one + zero + other == trials <= seats and unknown <= seats, "legacy counts are impossible")
        report[section] = {"trials": trials, "seats": seats, "disposition_missing": unknown, "valid_one": one, "valid_zero": zero, "other_or_no_grade": other}
    report["no_fabricated_exit"] = all(report[name]["disposition_missing"] == report[name]["seats"] for name in LEGACY)
    return report


def _secondary(outcomes: Record, cases: dict[str, tuple[str, dict[str, str]]]) -> Record | None:
    diagnosis = outcomes.get("secondary_diagnosis")
    if diagnosis is None:
        return None
    _require(isinstance(diagnosis, dict) and diagnosis.get("counted") is False, "secondary diagnosis is malformed or counted")
    run, task = diagnosis.get("run"), diagnosis.get("task")
    _require(isinstance(run, str) and NAME.fullmatch(run) is not None and run not in cases, "secondary diagnosis name is malformed or registered")
    _require(task in tuple(TASKS), "secondary diagnosis task is not pinned")
    grade, seats = _reader(diagnosis.get("reader"), task)
    dispositions = {seat: seats.get(seat, {}).get("disposition") for seat in TASKS[task]}
    return {"run": run, "task": task, "counted": False, "dispositions": dispositions, "grade": grade}


def build(registration: Path, outcomes_path: Path) -> Record:
    cases = _registration(_load(registration, "registration", REGISTRATION_SCHEMA))
    outcomes = _load(outcomes_path, "outcomes", OUTCOMES_SCHEMA)
    attempts = _attempts(outcomes, cases)
    slots = [_slot(case, task, expected, attempts.get(case)) for case, (task, expected) in cases.items()]
    pick = {
        status: [{"case": slot["case"], "seat": seat} for slot in slots for seat, entry in slot["seats"].items() if entry["status"] == status]
        for status in ("matched", "incorrect", "missing")
    }
    controls = [slot["control"] | {"case": slot["case"]} for slot in slots if slot["control"]]
    correct = [control["case"] for control in controls if control["status"] == "correct"]
    legacy = _legacy(outcomes)
    total = sum(len(slot["seats"]) for slot in slots)
    passed = len(pick["matched"]) == total and len(correct) == len(controls) and legacy["no_fabricated_exit"]
    return {
        "schema": SCHEMA,
        "result": "pass" if passed else "fail",
        "seats": {"matched": len(pick["matched"]), "of": total, "incorrect": pick["incorrect"], "missing": pick["missing"]},
        "attempts": {"correct": sum(slot["attempt"] == "correct" for slot in slots), "of": len(slots)},
        "controls": {
            "correct": len(correct),
            "of": len(controls),
            "witness_corroborated": sum(bool(c.get("agrees_with_reader") and c.get("harness_consistent")) for c in controls),
            "native_timeout_correct": sum(c.get("native_timeout") == "correct" for c in controls),
            "incorrect": [c["case"] for c in controls if c["status"] == "incorrect"],
            "missing": [c["case"] for c in controls if c["status"] == "missing"],
        },
        "grades": {name: sum(slot["grade"]["class"] == name for slot in slots) for name in GRADES},
        "slots": slots,
        "secondary_diagnosis": _secondary(outcomes, cases),
        "legacy": legacy,
        "caveat": CAVEAT,
    }


def text(report: Record) -> str:
    seats, controls = report["seats"], report["controls"]
    pairs = {key: ", ".join(f"{e['case']}/{e['seat']}" for e in seats[key]) or "none" for key in ("incorrect", "missing")}
    lines = [
        f"registered seats matched {seats['matched']}/{seats['of']}; attempts correct {report['attempts']['correct']}/{report['attempts']['of']}",
        f"incorrect seats: {pairs['incorrect']}; missing seats: {pairs['missing']}",
        f"controls correct {controls['correct']}/{controls['of']}; witness corroborated {controls['witness_corroborated']}; native timeout correct {controls['native_timeout_correct']}",
        "grades: " + ", ".join(f"{name} {count}" for name, count in report["grades"].items()),
    ]
    diagnosis = report["secondary_diagnosis"]
    if diagnosis:
        shown = ", ".join(f"{seat} {value}" for seat, value in diagnosis["dispositions"].items())
        lines.append(f"secondary {diagnosis['run']} (not counted): {shown}; grade {diagnosis['grade']['class']}")
    for name in LEGACY:
        lines.append(f"legacy {name} (release aggregate): " + ", ".join(f"{k} {v}" for k, v in report["legacy"][name].items()))
    lines.append(f"legacy no fabricated exit: {str(report['legacy']['no_fabricated_exit']).lower()}")
    return "\n".join([*lines, f"result: {report['result']}", report["caveat"]])


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(prog=PROG, description="Recompute registered seat dispositions and control witnesses.")
    cli.add_argument("registration", type=Path)
    cli.add_argument("outcomes", type=Path)
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    try:
        report = build(args.registration, args.outcomes)
    except Malformed as error:
        print(f"seat_exit: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True) if args.json else text(report))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
