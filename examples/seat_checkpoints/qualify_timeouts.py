import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from ..native_evidence import (
    Incomplete,
    Invalid,
    Record,
    canonical_uuid,
    read_bytes,
    same,
    strict_object,
)
from .collect import collect, load_registration

SCHEMA = "harbor.seat-cut.timeout-qualification/v1"
SAMPLES = Path(__file__).with_name("samples")
QUESTION = SAMPLES / "timeout-question.json"
FROZEN = SAMPLES / "control-registration.json"
QUESTION_SCHEMA = "harbor.seat-cut.timeout-question/v1"
CONTROL = "examples.seat_checkpoints.control_seat:ControlSeat"
ROLES = ("planner", "dispatcher")
FIXTURES = ("planner-fit", "dispatcher-fit")
STAGES = {
    "planner": ("after_publish",),
    "dispatcher": ("before_decide", "after_decide"),
}
POLICY = {"planner": "publish", "dispatcher": "preferred"}
MULTIPLIER = 0.05
DEADLINE_SECONDS = 30.0
NATIVE_MESSAGE = f"Agent execution timed out after {DEADLINE_SECONDS} seconds"
TIMEOUT_TYPE = "AgentTimeoutError"
TIMEOUT_POLICY = {"should_retry": False, "should_grade": True, "has_valid_reward": True}
CLOCK_TOLERANCE = timedelta(seconds=0.5)
DEADLINE_SLACK = timedelta(seconds=5)
MAX_QUESTION_BYTES = 1 << 20
MAX_RESULT_BYTES = 8 << 20
MAX_CONFIG_BYTES = 1 << 20
MAX_RECEIPT_BYTES = 64 << 10
MAX_SMALL_BYTES = 4096
MAX_HOST_BYTES = 64 << 10
MAX_CONTROL_ENTRIES = 8
ATTEMPT_KEYS = frozenset(
    {
        "schema",
        "ticket",
        "trial_id",
        "team",
        "target",
        "peer",
        "observe",
        "task_files",
        "interval_seconds",
        "max_samples",
        "timeout_seconds",
        "agent_timeout_multiplier",
        "agent_timeout_seconds",
    }
)
MULTIPLIER_FIELDS = (
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
)
OPENING = [
    "arrived:planner:after_publish",
    "arrived:dispatcher:before_decide",
]


class Arm(NamedTuple):
    held: dict[str, str]
    observation: str
    final: str
    standing: str | None
    trace: list[str]


ARMS = {
    "timeout-peer-after": Arm(
        {"dispatcher": "after_decide"},
        "bounded",
        "dispatcher-fit",
        "planner-fit",
        [
            *OPENING,
            "witness:running:running",
            "release:planner:after_publish",
            "witness:clean:running",
            "release:dispatcher:before_decide",
            "arrived:dispatcher:after_decide",
        ],
    ),
    "timeout-peer-before": Arm(
        {"dispatcher": "after_decide"},
        "bounded",
        "dispatcher-fit",
        "dispatcher-fit",
        [
            *OPENING,
            "witness:running:running",
            "release:dispatcher:before_decide",
            "arrived:dispatcher:after_decide",
            "witness:running:running",
            "release:planner:after_publish",
            "witness:clean:running",
        ],
    ),
    "timeout-target": Arm(
        {"planner": "after_publish"},
        "no_clean_return",
        "dispatcher-fit",
        None,
        [
            *OPENING,
            "witness:running:running",
            "release:dispatcher:before_decide",
            "arrived:dispatcher:after_decide",
            "release:dispatcher:after_decide",
            "native:dispatcher:clean",
        ],
    ),
    "timeout-both": Arm(
        {"planner": "after_publish", "dispatcher": "before_decide"},
        "no_clean_return",
        "planner-fit",
        None,
        [*OPENING, "witness:running:running"],
    ),
    "timeout-off": Arm(
        {"dispatcher": "after_decide"},
        "unobserved_control",
        "dispatcher-fit",
        None,
        [
            *OPENING,
            "release:planner:after_publish",
            "native:planner:clean",
            "release:dispatcher:before_decide",
            "arrived:dispatcher:after_decide",
        ],
    ),
}

assert all(set(arm.held) <= set(ROLES) for arm in ARMS.values())
assert all(
    stage in STAGES[role] for arm in ARMS.values() for role, stage in arm.held.items()
)


class Setup(Exception):
    pass


Checks = list[tuple[str, bool]]


def timeouts(arm: Arm) -> list[str]:
    return [role for role in ROLES if role in arm.held]


def grade(category: str, fixture: str) -> int:
    assert category in FIXTURES and fixture in FIXTURES
    return int(category == fixture)


def is_grade(value: object) -> bool:
    return type(value) is int and value in (0, 1)


def is_float(value: object, expected: float) -> bool:
    return type(value) is float and value == expected


def trusted(path: Path) -> Record:
    try:
        data = read_bytes(path.parent, (path.name,), MAX_QUESTION_BYTES)
        return strict_object(data, path.name)
    except (Incomplete, Invalid) as error:
        raise Setup(f"{path.name}: {error}") from None


def arm_declared(label: str, declared: object) -> bool:
    arm = ARMS[label]
    return (
        type(declared) is dict
        and declared.get("seeds") == [17]
        and declared.get("observe") is (arm.observation != "unobserved_control")
        and declared.get("planner_gates") == list(STAGES["planner"])
        and declared.get("dispatcher_gates") == list(STAGES["dispatcher"])
        and declared.get("dispatcher_policy") == "publish"
        and declared.get("timeouts") == timeouts(arm)
    )


def contract() -> tuple[Record, Record]:
    question, frozen = trusted(QUESTION), trusted(FROZEN)
    arms = question.get("arms")
    if not (
        question.get("schema") == QUESTION_SCHEMA
        and question.get("fixtures") == list(FIXTURES)
        and question.get("target") == "planner"
        and question.get("peer") == "dispatcher"
        and question.get("rule") == "observe"
        and is_float(question.get("agent_timeout_multiplier"), MULTIPLIER)
        and is_float(question.get("agent_timeout_seconds"), DEADLINE_SECONDS)
        and question.get("task_files") == frozen.get("task_files")
        and type(arms) is dict
        and set(arms) == set(ARMS)
        and all(arm_declared(label, arms[label]) for label in ARMS)
    ):
        raise Setup("timeout question differs from the frozen qualifier contract")
    task = frozen.get("task")
    if type(task) is not dict or type(frozen.get("task_files")) is not str:
        raise Setup("frozen control registration has no task identity")
    return question, frozen


def expected_kwargs(role: str) -> Record:
    return {"role": role, "policy": POLICY[role], "gates": list(STAGES[role])}


def roster_ok(members: object) -> bool:
    if type(members) is not list or len(members) != 2:
        return False
    seen: set[str] = set()
    for member in members:
        if type(member) is not dict:
            return False
        seat = member.get("seat")
        if type(seat) is not str or seat not in ROLES or seat in seen:
            return False
        if member.get("import_path") != CONTROL:
            return False
        if not same(member.get("kwargs"), expected_kwargs(seat)):
            return False
        seen.add(seat)
    return seen == set(ROLES)


def check_population(registration: Record, report: Record, frozen: Record) -> None:
    if not (
        same(report["task"], frozen["task"])
        and report["task_files"] == frozen["task_files"]
        and same(registration["task"], frozen["task"])
    ):
        raise Setup("qualification requires the frozen original task and full hash")
    teams = registration["teams"]
    if set(teams) != set(ARMS) or not all(roster_ok(teams[label]) for label in ARMS):
        raise Setup("registration teams are not exactly the 5 scripted timeout arms")
    expected = Counter((label, 17, fixture) for label in ARMS for fixture in FIXTURES)
    seen = Counter((r["team"], r["seed"], r["fixture"]) for r in report["cells"])
    if seen != expected or len(report["cells"]) != len(ARMS) * len(FIXTURES):
        raise Setup("registration does not cover the 5x2 timeout population once")


def add(out: Checks, name: str, ok: object) -> None:
    out.append((name, ok is True))


def row_checks(row: Record, arm: Arm, out: Checks) -> None:
    add(out, "row:roster", row.get("roster") == "scripted")
    add(
        out,
        "row:cell",
        row.get("rule") == "observe"
        and row.get("target") == "planner"
        and row.get("peer") == "dispatcher"
        and row.get("observe") is (arm.observation != "unobserved_control"),
    )
    add(out, "row:status", row.get("status") == "native_error")
    add(out, "row:disposition", row.get("native_disposition") == "agent_timeout")
    add(out, "row:timed_out_seats", row.get("timed_out_seats") == timeouts(arm))
    add(out, "row:observation", row.get("observation_status") == arm.observation)
    final = grade(arm.final, row["fixture"])
    native = row.get("native_grade")
    add(out, "row:final_grade", is_grade(native) and native == final)


def bound_checks(row: Record, arm: Arm, out: Checks) -> None:
    bound = row.get("bound")
    if arm.observation == "unobserved_control":
        add(out, "bound:no_claim", bound is None)
        return
    if type(bound) is not dict:
        add(out, "bound:present", False)
        return
    final = grade(arm.final, row["fixture"])
    add(out, "bound:status", bound.get("status") == arm.observation)
    add(out, "bound:host_closed", bound.get("host_closed"))
    observed = bound.get("final_grade")
    add(out, "bound:final", is_grade(observed) and observed == final)
    add(
        out,
        "bound:native_states",
        bound.get("native_target") == ("error" if "planner" in arm.held else "clean")
        and bound.get("native_peer")
        == ("error" if "dispatcher" in arm.held else "clean"),
    )
    low, high = bound.get("min_grade"), bound.get("max_grade")
    if arm.standing is None:
        flags = bound.get("flags")
        add(
            out,
            "bound:no_standing_grade",
            low is None
            and high is None
            and bound.get("lower_seq") is None
            and bound.get("grade_identified") is False
            and type(flags) is list
            and flags[:1] == ["target_error"],
        )
        return
    standing = grade(arm.standing, row["fixture"])
    add(
        out,
        "bound:standing_grade",
        is_grade(low)
        and is_grade(high)
        and low == high == standing
        and bound.get("grade_identified") is True,
    )
    observed_final = bound.get("final_grade")
    add(
        out,
        "bound:standing_vs_final",
        is_grade(low)
        and is_grade(observed_final)
        and (low == observed_final) is (arm.standing == arm.final),
    )
    add(out, "bound:peer_open", bound.get("peer_open_after_return"))


def stamp(value: object) -> datetime:
    if type(value) is not str:
        raise Invalid("timestamp absent")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise Invalid("timestamp malformed") from None
    if parsed.utcoffset() is None:
        raise Invalid("timestamp has no zone")
    return parsed


def timing(record: object) -> tuple[datetime, datetime]:
    if type(record) is not dict:
        raise Invalid("timing absent")
    return stamp(record.get("started_at")), stamp(record.get("finished_at"))


def native_timeout(info: object, message: str) -> bool:
    if type(info) is not dict:
        return False
    trace = info.get("exception_traceback")
    return (
        info.get("exception_type") == TIMEOUT_TYPE
        and info.get("exception_message") == message
        and type(trace) is str
        and "Traceback" not in trace
        and info.get("policy_override") is None
        and same(info.get("policy", TIMEOUT_POLICY), TIMEOUT_POLICY)
    )


def seat_rows(result: Record) -> dict[str, Record]:
    rows = result.get("seat_results")
    if type(rows) is not list or len(rows) != 2:
        raise Invalid("seat results are not two seats")
    seats = {row.get("seat"): row for row in rows if type(row) is dict}
    if set(seats) != set(ROLES):
        raise Invalid("seat results are not planner and dispatcher")
    return seats


def deadline_checks(result: Record, arm: Arm, out: Checks) -> None:
    seats = seat_rows(result)
    start, end = timing(result.get("agent_execution"))
    deadline = start + timedelta(seconds=DEADLINE_SECONDS)
    held = timeouts(arm)
    first = seats[held[0]].get("exception_info")
    trial = result.get("exception_info")
    copy = f"Seat '{held[0]}': {NATIVE_MESSAGE}"
    under_test = all(seats[r].get("under_test") is True for r in ROLES)
    add(out, "native:under_test", under_test)
    add(out, "native:trial_copy", native_timeout(trial, copy))
    add(
        out,
        "native:trial_copy_time",
        type(first) is dict
        and type(trial) is dict
        and first.get("occurred_at") == trial.get("occurred_at"),
    )
    for role in ROLES:
        info = seats[role].get("exception_info")
        seat_start, seat_end = timing(seats[role].get("agent_execution"))
        add(out, f"native:{role}:within_play", start <= seat_start <= seat_end <= end)
        if role not in arm.held:
            add(out, f"native:{role}:clean", info is None and seat_end < deadline)
            continue
        add(out, f"native:{role}:timeout", native_timeout(info, NATIVE_MESSAGE))
        occurred = stamp(info.get("occurred_at") if type(info) is dict else None)
        add(
            out,
            f"native:{role}:at_deadline",
            deadline - CLOCK_TOLERANCE <= occurred <= deadline + DEADLINE_SLACK
            and occurred <= seat_end + CLOCK_TOLERANCE,
        )
    verifier_start, _ = timing(result.get("verifier"))
    add(out, "native:verifier_after_play", verifier_start >= end)


def budget_ok(record: object) -> bool:
    if type(record) is not dict:
        return False
    verifier = record.get("verifier")
    return (
        is_float(record.get("agent_timeout_multiplier"), MULTIPLIER)
        and is_float(record.get("timeout_multiplier"), 1.0)
        and all(record.get(field) is None for field in MULTIPLIER_FIELDS)
        and record.get("override_error_policy", {}) == {}
        and type(verifier) is dict
        and verifier.get("override_timeout_sec") is None
        and verifier.get("max_timeout_sec") is None
    )


def result_checks(trial: Path, row: Record, frozen: Record, out: Checks) -> Record:
    data = read_bytes(trial, ("result.json",), MAX_RESULT_BYTES)
    result = strict_object(data, "result")
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if type(verifier) is dict else None
    single = type(rewards) is dict and set(rewards) == {"reward"}
    reward = rewards.get("reward") if single else None
    add(
        out,
        "native:identity",
        canonical_uuid(result.get("id"))
        and result.get("trial_name") == row["ticket"]
        and result.get("task_name") == frozen["task"]["name"]
        and result.get("task_checksum") == frozen["task"]["checksum"],
    )
    add(out, "native:reward_valid", result.get("reward_valid"))
    add(
        out,
        "native:verifier_grade",
        type(reward) in (int, float)
        and reward in (0, 1)
        and reward == row.get("native_grade"),
    )
    data = read_bytes(trial, ("config.json",), MAX_CONFIG_BYTES)
    config = strict_object(data, "config")
    lock = strict_object(read_bytes(trial, ("lock.json",), MAX_CONFIG_BYTES), "lock")
    add(out, "native:budget_result", budget_ok(result.get("config")))
    add(out, "native:budget_config", budget_ok(config))
    add(out, "native:budget_lock", budget_ok(lock))
    return result


def expected_stages(role: str, arm: Arm) -> list[Record]:
    held = arm.held.get(role)
    stages = STAGES[role]
    reached = stages if held is None else stages[: stages.index(held) + 1]
    return [
        {"stage": stage, "gated": True, "released": stage != held} for stage in reached
    ]


def receipt_checks(trial: Path, result: Record, arm: Arm, out: Checks) -> None:
    seats = seat_rows(result)
    for role in ROLES:
        path = ("seats", role, "agent", "control-receipt.json")
        receipt = strict_object(read_bytes(trial, path, MAX_RECEIPT_BYTES), "receipt")
        agent = seats[role].get("agent_result")
        metadata = agent.get("metadata") if type(agent) is dict else None
        held = arm.held.get(role)
        acted = held != "before_decide"
        summary = receipt.get("summary")
        add(
            out,
            f"receipt:{role}:native_copy",
            type(metadata) is dict and same(receipt, metadata.get("seat_cut_control")),
        )
        add(
            out,
            f"receipt:{role}:identity",
            receipt.get("schema") == "harbor.seat-cut.control-receipt/v1"
            and receipt.get("role") == role
            and receipt.get("policy") == POLICY[role]
            and receipt.get("gates") == list(STAGES[role]),
        )
        add(
            out,
            f"receipt:{role}:outcome",
            (receipt.get("outcome"), receipt.get("failure"))
            == (("complete", None) if held is None else ("failed", "cancelled")),
        )
        stages = same(receipt.get("stages"), expected_stages(role, arm))
        add(out, f"receipt:{role}:stages", stages)
        add(
            out,
            f"receipt:{role}:world_action",
            type(summary) is dict and summary.get("terminal") == "publish"
            if acted
            else summary is None,
        )


def control_entries(trial: Path, role: str) -> list[str]:
    directory = trial / "seats" / role / "seat-cut-control"
    if directory.is_symlink() or not directory.is_dir():
        raise Invalid("control directory is not a plain directory")
    with os.scandir(directory) as entries:
        names = []
        for entry in entries:
            names.append(entry.name)
            if len(names) > MAX_CONTROL_ENTRIES:
                raise Invalid("control directory exceeds its entry bound")
    return sorted(names)


def marker_checks(trial: Path, arm: Arm, out: Checks) -> None:
    for role in ROLES:
        stages = expected_stages(role, arm)
        names = [f"arrived-{s['stage']}" for s in stages]
        names += [f"release-{s['stage']}" for s in stages if s["released"]]
        entries = control_entries(trial, role)
        add(out, f"markers:{role}:hold_release", entries == sorted(names))
        for entry in stages:
            path = ("seats", role, "seat-cut-control", f"arrived-{entry['stage']}")
            marker = strict_object(read_bytes(trial, path, MAX_SMALL_BYTES), "marker")
            add(
                out,
                f"markers:{role}:{entry['stage']}",
                same(
                    marker,
                    {
                        "schema": "harbor.seat-cut.control-marker/v1",
                        "role": role,
                        "stage": entry["stage"],
                        "gated": True,
                    },
                ),
            )


def run_checks(
    trial: Path, row: Record, arm: Arm, frozen: Record, trial_id: object, out: Checks
) -> None:
    schedule = strict_object(
        read_bytes(trial, ("seat-cut-schedule.json",), MAX_SMALL_BYTES), "schedule"
    )
    add(
        out,
        "schedule:complete",
        same(
            schedule,
            {
                "schema": "harbor.seat-cut.schedule/v1",
                "trial_id": trial_id,
                "ticket": row["ticket"],
                "schedule": row["team"],
                "status": "complete",
                "trace": arm.trace,
            },
        ),
    )
    attempt = strict_object(
        read_bytes(trial, ("seat-cut-attempt.json",), MAX_SMALL_BYTES), "attempt"
    )
    observed = arm.observation != "unobserved_control"
    add(
        out,
        "attempt:v2",
        set(attempt) == ATTEMPT_KEYS
        and attempt["schema"] == "harbor.seat-cut.attempt/v2"
        and attempt["trial_id"] == trial_id
        and all(attempt[k] == row[k] for k in ("ticket", "team", "target", "peer"))
        and attempt["observe"] is observed
        and attempt["task_files"] == frozen["task_files"]
        and is_float(attempt["agent_timeout_multiplier"], MULTIPLIER)
        and is_float(attempt["agent_timeout_seconds"], DEADLINE_SECONDS),
    )
    try:
        host = strict_object(
            read_bytes(trial, ("seat-cut-host.json",), MAX_HOST_BYTES), "host"
        )
    except Incomplete:
        add(out, "host:presence", not observed)
        return
    add(out, "host:presence", observed)
    host_checks(host, arm, trial_id, out)


def host_checks(host: Record, arm: Arm, trial_id: object, out: Checks) -> None:
    samples = host.get("samples")
    if type(samples) is not list:
        add(out, "host:samples", False)
        return
    rows = [s for s in samples if type(s) is dict]
    returned = {"target": "clean", "peer": "running"}
    closed = host.get("closed") is True and host.get("trial_id") == trial_id
    settled = all(s.get("ack") != "pending" for s in rows)
    add(out, "host:closed", closed)
    add(out, "host:settled", len(rows) == len(samples) and settled)
    witnessed = any(
        s.get("ack") == "ok"
        and same(s.get("before"), returned)
        and same(s.get("after"), returned)
        for s in rows
    )
    snapshots = [s.get(k) for s in rows for k in ("before", "after")]
    target_clean = any(
        type(x) is dict and x.get("target") == "clean" for x in snapshots
    )
    if "planner" in arm.held:
        add(out, "host:no_target_return", not target_clean)
    else:
        add(out, "host:peer_open_witness", witnessed)


def trial_path(roots: list[Path], ticket: str) -> Path | None:
    found = [root / ticket for root in roots if (root / ticket).is_dir()]
    return found[0] if len(found) == 1 else None


def independent(
    row: Record, arm: Arm, roots: list[Path], frozen: Record, out: Checks
) -> None:
    trial = trial_path(roots, row["ticket"])
    if trial is None:
        add(out, "trial:single_directory", False)
        return
    stage = "native"
    try:
        result = result_checks(trial, row, frozen, out)
        deadline_checks(result, arm, out)
        stage = "receipt"
        receipt_checks(trial, result, arm, out)
        stage = "markers"
        marker_checks(trial, arm, out)
        stage = "schedule"
        run_checks(trial, row, arm, frozen, result.get("id"), out)
    except (
        Incomplete,
        Invalid,
        OSError,
        LookupError,
        TypeError,
        ValueError,
        AttributeError,
    ):
        add(out, f"{stage}:readable", False)


def pair(bound: Record | None, low: str, high: str) -> list[object] | None:
    return [bound.get(low), bound.get(high)] if bound else None


def assess(row: Record, roots: list[Path], frozen: Record) -> Record:
    arm = ARMS[row["team"]]
    out: Checks = []
    row_checks(row, arm, out)
    bound_checks(row, arm, out)
    independent(row, arm, roots, frozen, out)
    bound = row.get("bound") if type(row.get("bound")) is dict else None
    return {
        "ticket": row["ticket"],
        "team": row["team"],
        "seed": row["seed"],
        "fixture": row["fixture"],
        "status": row.get("status"),
        "native_disposition": row.get("native_disposition"),
        "timed_out_seats": row.get("timed_out_seats"),
        "observation_status": row.get("observation_status"),
        "final_grade": row.get("native_grade"),
        "grade_at_return": pair(bound, "min_grade", "max_grade"),
        "prefix_at_return": pair(bound, "lower_seq", "upper_seq"),
        "peer_open_after_return": bound.get("peer_open_after_return")
        if bound
        else None,
        "checks": len(out),
        "failed": [name for name, ok in out if not ok],
    }


def tally(rows: list[Record], key: str) -> dict[str, int]:
    counts = Counter(str(row.get(key)) for row in rows)
    return dict(sorted(counts.items()))


def qualify(registration_path: Path, roots: list[Path]) -> Record:
    _, frozen = contract()
    registration = load_registration(registration_path)
    report = collect(registration_path, roots)
    check_population(registration, report, frozen)
    rows = [assess(row, roots, frozen) for row in report["cells"]]
    assert all(row["checks"] > 0 for row in rows)
    failed_cells = sum(bool(row["failed"]) for row in rows)
    return {
        "schema": SCHEMA,
        "source": "native scripted seats at the collective agent deadline",
        "task": report["task"],
        "task_files": report["task_files"],
        "agent_timeout_seconds": DEADLINE_SECONDS,
        "registered": len(rows),
        "qualified_cells": len(rows) - failed_cells,
        "failed_cells": failed_cells,
        "checks": sum(row["checks"] for row in rows),
        "failed_checks": sum(len(row["failed"]) for row in rows),
        "statuses": report["statuses"],
        "native_dispositions": tally(rows, "native_disposition"),
        "observation_statuses": tally(rows, "observation_status"),
        "cells": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -BS -m examples.seat_checkpoints.qualify_timeouts",
        description="Grade the frozen 10-cell native timeout qualification.",
    )
    parser.add_argument("registration", type=Path, metavar="REG")
    parser.add_argument("roots", type=Path, nargs="+", metavar="TRIAL_ROOT")
    args = parser.parse_args(argv)
    try:
        result = qualify(args.registration, args.roots)
    except (Setup, OSError, ValueError, KeyError, TypeError) as error:
        print(f"qualify_timeouts: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    passed = result["failed_checks"] == 0 and result["qualified_cells"] == len(ARMS) * 2
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
