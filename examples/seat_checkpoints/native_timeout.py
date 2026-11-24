from datetime import datetime, timedelta
from typing import NamedTuple

from ..native_evidence import Invalid, Record, require
from .timeout_contract import (
    AGENT_TIMEOUT_MULTIPLIER,
    AGENT_TIMEOUT_SECONDS,
    NATIVE_DEADLINE,
    TIMEOUTS,
)

TIMEOUT_LABELS = frozenset(TIMEOUTS)
TASK_AGENT_SECONDS = 600.0
TIMEOUT_MULTIPLIER = AGENT_TIMEOUT_MULTIPLIER
TIMEOUT_SECONDS = AGENT_TIMEOUT_SECONDS
DEADLINE_SLACK = timedelta(seconds=1)
TIMEOUT_TYPE = NATIVE_DEADLINE
TIMEOUT_CLASS = f"harbor.trial.errors.{NATIVE_DEADLINE}"
TIMEOUT_POLICY = {"should_retry": False, "should_grade": True, "has_valid_reward": True}
ERROR_KEYS = frozenset(
    {
        "exception_type",
        "exception_message",
        "exception_traceback",
        "occurred_at",
        "policy",
    }
)
UNSET_MULTIPLIERS = (
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
)
DISPOSITIONS = ("clean", "agent_timeout", "other_error", "unknown")
MAX_MESSAGE_BYTES = 4096

assert TASK_AGENT_SECONDS * TIMEOUT_MULTIPLIER == TIMEOUT_SECONDS


class Disposition(NamedTuple):
    kind: str
    timed_out: tuple[str, ...] | None
    reason: str | None


class Seat(NamedTuple):
    role: str
    error: Record | None
    execution: object


def deadline_seconds(timeout_cell: bool) -> float:
    return TIMEOUT_SECONDS if timeout_cell else TASK_AGENT_SECONDS


def timeout_message(seconds: float) -> str:
    assert type(seconds) is float and seconds > 0
    return f"Agent execution timed out after {seconds} seconds"


def _unset_timeouts(section: object, label: str) -> None:
    require(type(section) is dict, f"{label} is not an object")
    assert type(section) is dict
    for field in ("override_timeout_sec", "max_timeout_sec"):
        require(section.get(field) is None, f"{label} sets {field}")


def check_deadline(record: Record, timeout_cell: bool, label: str) -> None:
    found = record.get("agent_timeout_multiplier")
    if timeout_cell:
        require(
            "agent_timeout_multiplier" in record
            and type(found) is float
            and found == TIMEOUT_MULTIPLIER,
            f"{label} agent_timeout_multiplier is not {TIMEOUT_MULTIPLIER}",
        )
    else:
        require(found is None, f"{label} shortens the native agent deadline")
    base = record.get("timeout_multiplier", 1.0)
    require(
        type(base) in (int, float) and base == 1,
        f"{label} timeout_multiplier is not 1",
    )
    for field in UNSET_MULTIPLIERS:
        require(record.get(field) is None, f"{label} sets {field}")
    require(
        record.get("override_error_policy", {}) == {},
        f"{label} overrides the native error policy",
    )
    if "agent" in record:
        _unset_timeouts(record["agent"], f"{label} agent")
    verifier = record.get("verifier", {})
    _unset_timeouts(verifier, f"{label} verifier")
    require(verifier.get("disable", False) is False, f"{label} disables the verifier")


def native_time(value: object, where: str) -> datetime:
    if not isinstance(value, str):
        raise Invalid(f"native {where} has no timestamp")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise Invalid(f"native {where} has an invalid timestamp") from error
    require(stamp.utcoffset() is not None, f"native {where} has no time zone")
    return stamp


def timing(value: object, where: str) -> tuple[datetime, datetime]:
    require(type(value) is dict, f"native {where} has no finished execution")
    assert type(value) is dict
    started = native_time(value.get("started_at"), where)
    finished = native_time(value.get("finished_at"), where)
    require(started <= finished, f"native {where} ends before it starts")
    return started, finished


def _seats(result: Record, roles: tuple[str, ...]) -> list[Seat]:
    rows = result.get("seat_results")
    require(
        type(rows) is list and len(rows) == 2, "native seat results are not two seats"
    )
    assert type(rows) is list
    seats: list[Seat] = []
    for row in rows:
        require(type(row) is dict, "native seat result is not an object")
        require(row.get("under_test") is True, "native seat is not under test")
        require("exception_info" in row, "native seat error record is absent")
        role = row.get("seat")
        require(
            role in roles and role not in {seat.role for seat in seats},
            "native seat results repeat or misname a seat",
        )
        error = row["exception_info"]
        require(
            error is None or type(error) is dict,
            f"native {role} error is not an object",
        )
        seats.append(Seat(role, error, row.get("agent_execution")))
    return seats


def _native_policy(value: object) -> bool:
    return (
        type(value) is dict
        and value == TIMEOUT_POLICY
        and all(type(v) is bool for v in value.values())
    )


def _timeout_error(error: Record, message: str, role: str) -> datetime:
    require(set(error) == ERROR_KEYS, f"{role} error fields are not the native set")
    require(error["exception_type"] == TIMEOUT_TYPE, f"{role} error is not a timeout")
    require(
        error["exception_message"] == message,
        f"{role} timeout message differs from the native deadline",
    )
    require(
        error["exception_traceback"] == f"{TIMEOUT_CLASS}: {message}\n",
        f"{role} timeout was not recorded by the native play deadline",
    )
    require(_native_policy(error["policy"]), f"{role} timeout policy is not native")
    return native_time(error["occurred_at"], f"{role} timeout")


def _trial_copy(result: Record, first: Seat, message_file: bytes | None) -> str:
    trial = result["exception_info"]
    assert first.error is not None
    require(type(trial) is dict, "native trial holds no seat timeout copy")
    message = f"Seat {first.role!r}: {first.error['exception_message']}"
    require(
        trial == {**first.error, "exception_message": message}
        and _native_policy(trial.get("policy")),
        "native trial error is not the first timed-out seat's copy",
    )
    expected = f"{message}\n{first.error['exception_traceback']}".encode()
    require(
        message_file == expected, "native exception.txt is not the trial timeout copy"
    )
    return message


def _ordering(result: Record, seats: list[Seat], deadline: float) -> None:
    play_start, play_end = timing(result.get("agent_execution"), "play")
    stamps: list[datetime] = []
    returns: list[datetime] = []
    for seat in seats:
        started, finished = timing(seat.execution, f"{seat.role} execution")
        require(
            play_start <= started and finished <= play_end,
            f"native {seat.role} execution lies outside play",
        )
        if seat.error is None:
            returns.append(finished)
            continue
        occurred = native_time(seat.error["occurred_at"], f"{seat.role} timeout")
        require(occurred <= finished, f"native {seat.role} finished before its timeout")
        stamps.append(occurred)
    assert stamps
    require(stamps == sorted(stamps), "native timeouts are out of seat order")
    require(
        stamps[0] >= play_start + timedelta(seconds=deadline) - DEADLINE_SLACK,
        "native timeout precedes the play deadline",
    )
    require(stamps[-1] <= play_end, "native timeout follows the end of play")
    require(
        all(finished <= stamps[0] for finished in returns),
        "a clean seat returned after the deadline sweep",
    )
    started, _ = timing(result.get("verifier"), "verifier")
    require(play_end <= started, "native verifier precedes the end of play")


def _clean(result: Record, seats: list[Seat]) -> Disposition:
    play_start, play_end = timing(result.get("agent_execution"), "play")
    for seat in seats:
        if type(seat.execution) is not dict:
            raise Invalid(
                f"native {seat.role} seat has no finished execution and no error"
            )
        started, finished = timing(seat.execution, f"{seat.role} execution")
        require(
            play_start <= started <= finished <= play_end,
            f"native {seat.role} execution lies outside play",
        )
    verifier_start, _ = timing(result.get("verifier"), "verifier")
    require(play_end <= verifier_start, "native verifier precedes the end of play")
    return Disposition("clean", (), None)


def classify(
    result: Record,
    roles: tuple[str, ...],
    deadline: float,
    message_file: bytes | None,
) -> Disposition:
    assert len(roles) == 2 and roles[0] != roles[1] and deadline > 0
    require("exception_info" in result, "native trial error record is absent")
    trial_error = result["exception_info"]
    require(
        trial_error is None or type(trial_error) is dict, "invalid native trial error"
    )
    rows = result.get("seat_results")
    if trial_error is not None and (rows is None or rows == []):
        return Disposition("other_error", None, "native error recorded for trial")
    seats = _seats(result, roles)
    require(
        {seat.role for seat in seats} == set(roles),
        "native seat results are not planner and dispatcher",
    )
    failed = [seat for seat in seats if seat.error is not None]
    if trial_error is None and not failed:
        return _clean(result, seats)
    named = ["trial"] if trial_error is not None else []
    reason = "native error recorded for " + " and ".join(
        named + [seat.role for seat in failed]
    )
    message = timeout_message(deadline)
    try:
        require(failed != [], "native trial error names no seat")
        require(
            tuple(seat.role for seat in seats) == roles,
            "native seats are not in task order",
        )
        for seat in failed:
            assert seat.error is not None
            _timeout_error(seat.error, message, seat.role)
        copied = _trial_copy(result, failed[0], message_file)
        _ordering(result, seats, deadline)
    except Invalid as error:
        why = f"{reason}; not a native timeout: {error}"
        return Disposition("other_error", None, why)
    return Disposition("agent_timeout", tuple(seat.role for seat in failed), copied)
