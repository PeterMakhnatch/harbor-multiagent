import argparse
import json
import math
import os
import re
import stat
import sys
import time
from typing import Any, NoReturn, override

from .seat_stream import SEAT, UUID, Malformed, decode_snapshot, read_journal_bytes

SCHEMA = "seatlab.workbench.wait-return/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.wait_return"
POLL_S = 0.15
MAX_TIMEOUT_S = 300.0
MAX_POLLS = math.ceil(MAX_TIMEOUT_S / POLL_S) + 2
TIMEOUT = re.compile(r"(0|[1-9][0-9]{0,2})(\.[0-9]{1,3})?")
NOT_RETURNED = ("raised", "cancelled", "stop_requested", "ended_unknown")
EXIT = {
    "returned": 0,
    "not_returned": 1,
    "closed_unobserved": 1,
    "timed_out": 1,
    "no_evidence": 1,
    "refused": 2,
}
CAVEAT = (
    "Passive native seat journal only. returned means the named seat's play_end row recorded returned; "
    "it does not mean any seat or peer is alive, graded or passed. No result, command or intervention was used."
)

Record = dict[str, Any]


class Refused(Exception):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class Watch:
    __slots__ = ("identity", "prefix")

    def __init__(self) -> None:
        self.identity: tuple[int, int] | None = None
        self.prefix = b""


def _lstat(path: str, watch: Watch) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if watch.identity is not None:
            raise Refused("journal_replaced") from None
        return False
    except (OSError, ValueError):
        raise Refused("journal_unreadable") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused("journal_not_regular_file")
    identity = (info.st_dev, info.st_ino)
    if watch.identity not in (None, identity):
        raise Refused("journal_replaced")
    watch.identity = identity
    return True


def _snapshot(path: str, trial: str, seat: str, watch: Watch) -> Record | None:
    if not _lstat(path, watch):
        return None
    try:
        data = read_journal_bytes(path)
    except Malformed as error:
        raise Refused("journal_unreadable", str(error)) from None
    _lstat(path, watch)
    if not data.startswith(watch.prefix):
        raise Refused(
            "journal_truncated"
            if len(data) < len(watch.prefix)
            else "journal_rewritten"
        )
    watch.prefix = data
    try:
        report = decode_snapshot(data)
    except Malformed as error:
        raise Refused("journal_invalid", str(error)) from None
    if report["trial"] is None:
        return report
    if report["trial"] != trial:
        raise Refused("trial_mismatch")
    if all(declared["name"] != seat for declared in report["seats"]):
        raise Refused("seat_not_declared")
    return report


def _seat(report: Record | None, seat: str) -> Record | None:
    if report is None or report["trial"] is None:
        return None
    return next(declared for declared in report["seats"] if declared["name"] == seat)


def _outcome(report: Record | None, found: Record | None) -> str | None:
    if report is None or found is None:
        return None
    if found["status"] == "returned":
        if not found["entered"]:
            raise Refused("return_without_run_enter")
        return "returned"
    if found["status"] in NOT_RETURNED:
        return "not_returned"
    return "closed_unobserved" if report["state"] == "closed" else None


def _record(
    outcome: str, trial: str, seat: str, report: Record | None, found: Record | None
) -> Record:
    journal = (
        None
        if outcome == "refused"
        else "absent"
        if report is None
        else "no_header"
        if found is None
        else "bound"
    )
    return {
        "schema": SCHEMA,
        "outcome": outcome,
        "trial": trial,
        "seat": seat,
        "journal": journal,
        "stream": None if found is None or report is None else report["state"],
        "under_test": None if found is None else found["under_test"],
        "status": None if found is None else found["status"],
        "enter_seq": None if found is None else found["enter_seq"],
        "end_seq": None if found is None else found["end_seq"],
        "reason": None,
        "detail": None,
        "caveat": CAVEAT,
    }


def wait(path: str, trial: str, seat: str, timeout: float) -> Record:
    assert UUID.fullmatch(trial) is not None and SEAT.fullmatch(seat) is not None
    assert math.isfinite(timeout) and 0 <= timeout <= MAX_TIMEOUT_S
    watch = Watch()
    deadline = time.monotonic() + timeout
    report: Record | None = None
    try:
        for poll in range(MAX_POLLS):
            if poll and time.monotonic() >= deadline:
                break
            report = _snapshot(path, trial, seat, watch)
            found = _seat(report, seat)
            outcome = _outcome(report, found)
            if outcome is not None:
                return _record(outcome, trial, seat, report, found)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(POLL_S, remaining))
    except Refused as refusal:
        return _record("refused", trial, seat, None, None) | {
            "reason": refusal.reason,
            "detail": refusal.detail,
        }
    found = _seat(report, seat)
    return _record(
        "no_evidence" if found is None else "timed_out", trial, seat, report, found
    )


def text(record: Record) -> str:
    head = f"{record['outcome']}: trial {record['trial']} seat {record['seat']}"
    if record["outcome"] == "refused":
        detail = "" if record["detail"] is None else f" ({record['detail']})"
        return f"{head}; refused: {record['reason']}{detail}"
    if record["status"] is None:
        return f"{head}; journal {record['journal']}; seat unknown\n{record['caveat']}"
    role = "under test" if record["under_test"] else "supporting"
    seqs = f"run_enter seq {record['enter_seq']}; play_end seq {record['end_seq']}"
    return f"{head} ({role}); status {record['status']}; {seqs}; stream {record['stream']}\n{record['caveat']}"


def _pattern(pattern: re.Pattern[str]) -> Any:
    def check(value: str) -> str:
        if pattern.fullmatch(value) is None:
            raise ValueError
        return value

    return check


def _timeout(value: str) -> float:
    seconds = float(_pattern(TIMEOUT)(value))
    if seconds > MAX_TIMEOUT_S:
        raise ValueError
    return seconds


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        sys.stderr.write(
            "wait_return: usage error; expected JOURNAL --trial UUID --seat NAME --timeout SECONDS [--json]\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    cli = _Cli(
        prog=PROG,
        description="Wait, read-only and bounded, for one named seat's native return row.",
    )
    cli.add_argument("journal")
    cli.add_argument("--trial", required=True, type=_pattern(UUID))
    cli.add_argument("--seat", required=True, type=_pattern(SEAT))
    cli.add_argument(
        "--timeout",
        required=True,
        type=_timeout,
        help="0 to 300 seconds; 0 takes one snapshot",
    )
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    record = wait(args.journal, args.trial, args.seat, args.timeout)
    if args.json:
        print(
            json.dumps(record, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
    else:
        print(
            text(record),
            file=sys.stderr if record["outcome"] == "refused" else sys.stdout,
        )
    return EXIT[record["outcome"]]


if __name__ == "__main__":
    raise SystemExit(main())
