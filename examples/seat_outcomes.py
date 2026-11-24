import argparse
import json
import math
import os
import re
import stat
import sys
from collections import Counter
from datetime import datetime

REPORT_VERSION = 1
MAX_INPUTS = 1024
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_SEATS = 64
MAX_REWARD_KEYS = 64
MAX_LABEL_CHARS = 128

EXIT_COMPLETE = 0
EXIT_UNAVAILABLE = 1
EXIT_USAGE = 2

DISPOSITIONS = frozenset({"returned", "raised", "cancelled", "stop_requested"})
NATIVE_TIMEOUTS = {"AgentTimeoutError": "play", "AgentSetupTimeoutError": "setup"}

_SEAT_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_PLAIN_REWARD_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_EXCEPTION_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class _Malformed(Exception):
    def __init__(self, reason: str):
        assert reason
        super().__init__(reason)
        self.reason = reason


def _reject_constant(name: str) -> float:
    raise ValueError(name)


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_LABEL_CHARS
        and pattern.fullmatch(value) is not None
    )


def _finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _printable_key(key: str) -> bool:
    return 0 < len(key) <= MAX_LABEL_CHARS and key.isprintable()


def _load(trial_dir: str) -> dict:
    path = os.path.join(trial_dir, "result.json")
    try:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode):
            raise _Malformed("result_unreadable")
        if info.st_size > MAX_RESULT_BYTES:
            raise _Malformed("result_too_large")
        with open(path, "rb") as handle:
            data = handle.read(MAX_RESULT_BYTES + 1)
    except FileNotFoundError:
        raise _Malformed("result_missing") from None
    except OSError:
        raise _Malformed("result_unreadable") from None
    if len(data) > MAX_RESULT_BYTES:
        raise _Malformed("result_too_large")
    try:
        document = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise _Malformed("result_not_json") from None
    if not isinstance(document, dict):
        raise _Malformed("result_malformed")
    version = document.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise _Malformed("result_unsupported_schema")
    return document


def _validity(document: dict) -> str:
    value = document.get("reward_valid")
    if value is None:
        return "unknown"
    if value is True:
        return "valid"
    if value is False:
        return "invalid"
    raise _Malformed("grade_malformed")


def _rewards(document: dict) -> tuple[str, dict | None]:
    verifier = document.get("verifier_result")
    if verifier is None:
        return "not_recorded", None
    if not isinstance(verifier, dict):
        raise _Malformed("grade_malformed")
    rewards = verifier.get("rewards")
    if rewards is None:
        return "not_recorded", None
    if not isinstance(rewards, dict):
        raise _Malformed("grade_malformed")
    if not all(_finite_number(value) for value in rewards.values()):
        raise _Malformed("grade_malformed")
    if len(rewards) > MAX_REWARD_KEYS or not all(map(_printable_key, rewards)):
        return "grade_labels_unsupported", None
    return ("recorded" if rewards else "empty"), dict(rewards)


def _exception_type(holder: dict, reason: str) -> str | None:
    info = holder.get("exception_info")
    if info is None:
        return None
    if not isinstance(info, dict):
        raise _Malformed(reason)
    exception_type = info.get("exception_type")
    if not _matches(_EXCEPTION_TYPE, exception_type):
        raise _Malformed(reason)
    return exception_type


def _disposition(seat: dict) -> str:
    value = seat.get("execution_disposition")
    if value is None:
        return "unknown"
    if isinstance(value, str) and value in DISPOSITIONS:
        return value
    raise _Malformed("disposition_malformed")


def _play_timing(seat: dict) -> str:
    execution = seat.get("agent_execution")
    if execution is None:
        return "absent"
    if not isinstance(execution, dict):
        raise _Malformed("timing_malformed")
    finished_at = execution.get("finished_at")
    if finished_at is None:
        return "open"
    if not isinstance(finished_at, str) or len(finished_at) > MAX_LABEL_CHARS:
        raise _Malformed("timing_malformed")
    try:
        datetime.fromisoformat(finished_at)
    except ValueError:
        raise _Malformed("timing_malformed") from None
    return "finished"


def _seat(ordinal: int, seat: object, duplicates: set[str]) -> dict:
    assert ordinal >= 1
    unnamed = {"ordinal": ordinal, "seat": None, "status": "unavailable"}
    if not isinstance(seat, dict):
        return unnamed | {"reason": "seat_malformed"}
    label = seat.get("seat")
    if not _matches(_SEAT_LABEL, label):
        return unnamed | {"reason": "label_invalid"}
    entry: dict = {"ordinal": ordinal, "seat": label}
    if label in duplicates:
        return entry | {"status": "unavailable", "reason": "label_duplicate"}
    under_test = seat.get("under_test")
    if not isinstance(under_test, bool):
        return entry | {"status": "unavailable", "reason": "role_invalid"}
    try:
        disposition = _disposition(seat)
        exception_type = _exception_type(seat, "exception_malformed")
        play_timing = _play_timing(seat)
    except _Malformed as error:
        return entry | {"status": "unavailable", "reason": error.reason}
    return entry | {
        "status": "available",
        "role": "tested" if under_test else "supporting",
        "disposition": disposition,
        "exception_type": exception_type,
        "native_timeout": NATIVE_TIMEOUTS.get(exception_type or ""),
        "play_timing": play_timing,
    }


def _seats(document: dict) -> list[dict] | None:
    seats = document.get("seat_results")
    if seats is None:
        return None
    if not isinstance(seats, list):
        raise _Malformed("result_malformed")
    if len(seats) > MAX_SEATS:
        raise _Malformed("too_many_seats")
    labels = Counter(
        seat.get("seat")
        for seat in seats
        if isinstance(seat, dict) and isinstance(seat.get("seat"), str)
    )
    duplicates = {label for label, count in labels.items() if count > 1}
    return [_seat(index + 1, seat, duplicates) for index, seat in enumerate(seats)]


def _trial(ordinal: int, trial_dir: str) -> dict:
    assert ordinal >= 1
    try:
        document = _load(trial_dir)
        validity = _validity(document)
        rewards_status, rewards = _rewards(document)
        trial_exception_type = _exception_type(document, "result_malformed")
        seats = _seats(document)
    except _Malformed as error:
        return {"trial": ordinal, "status": "unavailable", "reason": error.reason}
    return {
        "trial": ordinal,
        "status": "available",
        "grade": {
            "validity": validity,
            "rewards_status": rewards_status,
            "rewards": rewards,
        },
        "trial_exception_type": trial_exception_type,
        "seats": seats,
    }


def _complete(trials: list[dict]) -> bool:
    for trial in trials:
        if trial["status"] != "available":
            return False
        if trial["grade"]["rewards_status"] == "grade_labels_unsupported":
            return False
        for seat in trial["seats"] or []:
            if seat["status"] != "available":
                return False
    return True


def _text_seat(seat: dict) -> str:
    name = seat["seat"] if seat["seat"] is not None else "-"
    head = f"  seat {seat['ordinal']} {name}"
    if seat["status"] != "available":
        return f"{head}: unavailable ({seat['reason']})"
    return (
        f"{head} [{seat['role']}]: disposition={seat['disposition']}"
        f" exception={seat['exception_type'] or 'none'}"
        f" native_timeout={seat['native_timeout'] or 'none'}"
        f" play_timing={seat['play_timing']}"
    )


def _text_key(key: str) -> str:
    return key if _PLAIN_REWARD_KEY.fullmatch(key) else json.dumps(key)


def _text_trial(trial: dict) -> list[str]:
    if trial["status"] != "available":
        return [f"trial {trial['trial']}: unavailable ({trial['reason']})"]
    grade = trial["grade"]
    rewards = grade["rewards_status"]
    if grade["rewards"]:
        pairs = " ".join(
            f"{_text_key(key)}={json.dumps(value)}"
            for key, value in grade["rewards"].items()
        )
        rewards = f"{rewards} {pairs}"
    lines = [
        f"trial {trial['trial']}: available",
        f"  grade: validity={grade['validity']} rewards={rewards}",
        f"  trial exception={trial['trial_exception_type'] or 'none'}",
    ]
    if trial["seats"] is None:
        return lines + ["  seats: not recorded"]
    return lines + [_text_seat(seat) for seat in trial["seats"]]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write("seat_outcomes: invalid arguments\n")
        raise SystemExit(EXIT_USAGE)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="python3 -BS -m examples.seat_outcomes",
        description="Report seat outcomes from native trial result.json files.",
    )
    parser.add_argument("trial_dirs", nargs="+", metavar="TRIAL_DIR")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if len(args.trial_dirs) > MAX_INPUTS:
        parser.error("too many inputs")
    trials = [_trial(index + 1, path) for index, path in enumerate(args.trial_dirs)]
    if args.json:
        report = {"report_version": REPORT_VERSION, "trials": trials}
        sys.stdout.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    else:
        for trial in trials:
            sys.stdout.write("\n".join(_text_trial(trial)) + "\n")
    return EXIT_COMPLETE if _complete(trials) else EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
