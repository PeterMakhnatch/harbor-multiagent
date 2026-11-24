import argparse
import json
import os
import re
import stat
import sys
from typing import Any, NoReturn, override

SCHEMA = "seatlab.workbench.seat-stream/v1"
STREAM = "harbor.seat-events/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench.seat_stream"
MIB = 1024 * 1024
MAX_BYTES = 2 * MIB
MAX_RESULT_BYTES = 64 * MIB
MAX_SEATS = 256
MAX_ROWS = 2 + 2 * MAX_SEATS
MAX_ELAPSED_NS = 2**63 - 1
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SEAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
DISPOSITIONS = ("returned", "raised", "cancelled", "stop_requested")
STATUSES = ("not_observed", "entered_only", "ended_unknown", *DISPOSITIONS)
RELATIONS = ("agree", "conflict", "journal_only", "result_only", "neither_known")
SEAT_FIELDS = frozenset({"name", "under_test"})
FIELDS = {
    "open": frozenset({"schema", "kind", "seq", "trial", "seats"}),
    "run_enter": frozenset({"kind", "seq", "elapsed_ns", "seat"}),
    "play_end": frozenset({"kind", "seq", "elapsed_ns", "seat", "disposition"}),
    "closed": frozenset({"kind", "seq", "elapsed_ns"}),
}
CAVEAT = (
    "Snapshot of passive native seat lifecycle rows only. entered_only means no play_end row was recorded yet, "
    "not that the seat is running. A missing play_end is never read as a return. closed means collection ended, "
    "not that seats completed or passed. No claim is made about grading, billing, actions or termination."
)

Record = dict[str, Any]


class Malformed(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Malformed(reason)


def _unique(pairs: list[tuple[str, Any]]) -> Record:
    record = dict(pairs)
    _require(len(record) == len(pairs), "duplicate JSON name")
    return record


def _constant(name: str) -> NoReturn:
    raise Malformed("non-finite JSON number")


def _read(path: str, limit: int, label: str) -> bytes:
    try:
        before = os.lstat(path)
    except (OSError, ValueError) as error:
        raise Malformed(f"{label} is missing or unreadable") from error
    _require(stat.S_ISREG(before.st_mode), f"{label} is not a regular non-symlink file")
    _require(before.st_size <= limit, f"{label} exceeds {limit // MIB} MiB")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except (OSError, ValueError) as error:
        raise Malformed(f"{label} is missing or unreadable") from error
    chunks: list[bytes] = []
    size = 0
    try:
        after = os.fstat(fd)
        same = (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        _require(
            stat.S_ISREG(after.st_mode) and same,
            f"{label} changed identity while opening",
        )
        for _ in range(limit + 2):
            chunk = os.read(fd, limit + 1 - size)
            if not chunk or size + len(chunk) > limit:
                size += len(chunk)
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError as error:
        raise Malformed(f"{label} is unreadable") from error
    finally:
        os.close(fd)
    _require(size <= limit, f"{label} exceeds {limit // MIB} MiB")
    return b"".join(chunks)


def _strict_json(data: bytes, reason: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique,
            parse_constant=_constant,
        )
    except (ValueError, RecursionError) as error:
        raise Malformed(reason) from error


def _row(raw: bytes, index: int) -> Record:
    row = _strict_json(raw, f"row {index} is not strict UTF-8 JSON")
    _require(type(row) is dict, f"row {index} is not a JSON object")
    kind = row.get("kind")
    _require(type(kind) is str and kind in FIELDS, f"row {index} has an unknown kind")
    _require(
        row.keys() == FIELDS[kind],
        f"row {index} does not have exactly the {kind} fields",
    )
    _require(
        type(row["seq"]) is int and row["seq"] == index,
        f"row {index} breaks the gapless seq",
    )
    return row


def _header(row: Record) -> tuple[str, list[Record]]:
    _require(row["kind"] == "open", "row 0 is not the open header")
    _require(row["schema"] == STREAM, "header has another schema")
    trial = row["trial"]
    _require(
        type(trial) is str and UUID.fullmatch(trial) is not None,
        "header trial is not a canonical lowercase UUID",
    )
    declared = row["seats"]
    _require(
        type(declared) is list and 1 <= len(declared) <= MAX_SEATS,
        "header must declare 1 to 256 seats",
    )
    seats: list[Record] = []
    names: set[str] = set()
    for seat in declared:
        _require(
            type(seat) is dict and seat.keys() == SEAT_FIELDS,
            "header seat does not have exactly name and under_test",
        )
        name, under_test = seat["name"], seat["under_test"]
        _require(
            type(name) is str and SEAT.fullmatch(name) is not None,
            "header seat name is not a safe name",
        )
        _require(name not in names, "header seat names are not unique")
        _require(type(under_test) is bool, "header seat under_test is not a boolean")
        names.add(name)
        seats.append(
            {
                "name": name,
                "under_test": under_test,
                "entered": False,
                "enter_seq": None,
                "enter_elapsed_ns": None,
                "terminal": None,
                "end_seq": None,
                "end_elapsed_ns": None,
                "play_end_without_enter": False,
            }
        )
    _require(any(seat["under_test"] for seat in seats), "header has no seat under test")
    return trial, seats


def _apply(row: Record, index: int, seats: dict[str, Record], last: int) -> int:
    kind = row["kind"]
    _require(kind != "open", f"row {index} repeats the open header")
    elapsed = row["elapsed_ns"]
    _require(
        type(elapsed) is int and 0 <= elapsed <= MAX_ELAPSED_NS,
        f"row {index} has an invalid elapsed_ns",
    )
    _require(elapsed >= last, f"row {index} moves elapsed_ns backwards")
    if kind == "closed":
        return elapsed
    name = row["seat"]
    _require(
        type(name) is str and name in seats, f"row {index} names an undeclared seat"
    )
    seat = seats[name]
    _require(
        seat["terminal"] is None, f"row {index} follows play_end for the same seat"
    )
    if kind == "run_enter":
        _require(
            not seat["entered"], f"row {index} repeats run_enter for the same seat"
        )
        seat.update(entered=True, enter_seq=index, enter_elapsed_ns=elapsed)
        return elapsed
    disposition = row["disposition"]
    known = disposition is None or (
        type(disposition) is str and disposition in DISPOSITIONS
    )
    _require(known, f"row {index} has an unknown disposition")
    seat.update(
        terminal="unknown" if disposition is None else disposition,
        end_seq=index,
        end_elapsed_ns=elapsed,
        play_end_without_enter=not seat["entered"],
    )
    return elapsed


def _status(seat: Record) -> str:
    terminal = seat["terminal"]
    if terminal is None:
        return "entered_only" if seat["entered"] else "not_observed"
    return "ended_unknown" if terminal == "unknown" else terminal


def _report(
    trial: str | None,
    seats: list[Record],
    rows: int,
    elapsed: int | None,
    closed: int | None,
    partial: bool,
) -> Record:
    for seat in seats:
        seat["status"] = _status(seat)
    tested = [seat for seat in seats if seat["under_test"]]
    return {
        "schema": SCHEMA,
        "stream": STREAM,
        "state": "no_header"
        if trial is None
        else "open"
        if closed is None
        else "closed",
        "trial": trial,
        "rows": rows,
        "last_seq": rows - 1 if rows else None,
        "last_elapsed_ns": elapsed,
        "closed_elapsed_ns": closed,
        "trailing_partial": partial,
        "seats": seats,
        "counts": {
            "seats": len(seats),
            "under_test": len(tested),
            "play_end_without_enter": sum(
                seat["play_end_without_enter"] for seat in seats
            ),
            "statuses": {
                status: sum(seat["status"] == status for seat in seats)
                for status in STATUSES
            },
            "under_test_statuses": {
                status: sum(seat["status"] == status for seat in tested)
                for status in STATUSES
            },
        },
        "caveat": CAVEAT,
    }


def _result_roster(path: str) -> tuple[str, dict[str, tuple[bool, str | None]]]:
    document = _strict_json(
        _read(path, MAX_RESULT_BYTES, "result"), "result is not strict UTF-8 JSON"
    )
    _require(type(document) is dict, "result is not a JSON object")
    version = document.get("schema_version", 1)
    _require(type(version) is int and version == 1, "result schema_version is not 1")
    trial = document.get("id")
    _require(
        type(trial) is str and UUID.fullmatch(trial) is not None,
        "result id is not a canonical lowercase UUID",
    )
    declared = document.get("seat_results")
    _require(
        type(declared) is list and 1 <= len(declared) <= MAX_SEATS,
        "result must record 1 to 256 seat_results",
    )
    roster: dict[str, tuple[bool, str | None]] = {}
    for seat in declared:
        _require(type(seat) is dict, "result seat is not a JSON object")
        name, under_test, disposition = (
            seat.get("seat"),
            seat.get("under_test"),
            seat.get("execution_disposition"),
        )
        _require(
            type(name) is str and SEAT.fullmatch(name) is not None,
            "result seat name is not a safe name",
        )
        _require(name not in roster, "result seat names are not unique")
        _require(type(under_test) is bool, "result seat under_test is not a boolean")
        known = disposition is None or (
            type(disposition) is str and disposition in DISPOSITIONS
        )
        _require(known, "result seat has an unknown execution_disposition")
        roster[name] = (under_test, disposition)
    return trial, roster


def _relation(journal: str | None, result: str | None) -> str:
    if journal is None:
        return "neither_known" if result is None else "result_only"
    if result is None:
        return "journal_only"
    return "agree" if journal == result else "conflict"


def _compare(report: Record, path: str) -> Record:
    _require(
        report["trial"] is not None,
        "journal has no complete header to bind the result to",
    )
    trial, roster = _result_roster(path)
    _require(
        trial == report["trial"], "result id does not match the journal header trial"
    )
    declared = {seat["name"]: seat["under_test"] for seat in report["seats"]}
    recorded = {name: under_test for name, (under_test, _) in roster.items()}
    _require(
        declared == recorded, "result seat roster does not match the journal header"
    )
    seats = []
    for seat in report["seats"]:
        journal = seat["terminal"] if seat["terminal"] in DISPOSITIONS else None
        result = roster[seat["name"]][1]
        relation = _relation(journal, result)
        seats.append(
            {
                "seat": seat["name"],
                "journal_disposition": journal,
                "result_disposition": result,
                "relation": relation,
            }
        )
    return {
        "seats": seats,
        "counts": {
            relation: sum(seat["relation"] == relation for seat in seats)
            for relation in RELATIONS
        },
    }


def build(path: str, result: str | None = None) -> Record:
    report = _journal(path)
    if result is not None:
        report["result_comparison"] = _compare(report, result)
    return report


def _journal(path: str) -> Record:
    data = _read(path, MAX_BYTES, "journal")
    _require(data.count(b"\n") <= MAX_ROWS, "journal exceeds 514 rows")
    *complete, tail = data.split(b"\n")
    partial = tail != b""
    if not complete:
        return _report(None, [], 0, None, None, partial)
    trial, seats = _header(_row(complete[0], 0))
    by_name = {seat["name"]: seat for seat in seats}
    last = 0
    elapsed: int | None = None
    closed: int | None = None
    for index, raw in enumerate(complete[1:], start=1):
        _require(closed is None, f"row {index} follows closed")
        row = _row(raw, index)
        last = _apply(row, index, by_name, last)
        elapsed = last
        if row["kind"] == "closed":
            closed = last
    _require(closed is None or not partial, "data follows closed")
    return _report(trial, seats, len(complete), elapsed, closed, partial)


def text(report: Record) -> str:
    partial = "yes" if report["trailing_partial"] else "no"
    if report["trial"] is None:
        return "\n".join(
            [
                f"stream: no complete header yet; no seats are known; trailing partial line: {partial}",
                report["caveat"],
            ]
        )
    elapsed, closed = (
        "none" if report[key] is None else report[key]
        for key in ("last_elapsed_ns", "closed_elapsed_ns")
    )
    lines = [
        f"trial {report['trial']}: stream {report['state']}; rows {report['rows']} (last seq {report['last_seq']})",
        f"last elapsed_ns: {elapsed}; closed elapsed_ns: {closed}; trailing partial line: {partial}",
    ]
    for seat in report["seats"]:
        role = "under test" if seat["under_test"] else "supporting"
        entered = "yes" if seat["entered"] else "not observed"
        flag = "; play_end without run_enter" if seat["play_end_without_enter"] else ""
        lines.append(
            f"  {seat['name']} ({role}): {seat['status']}; entered {entered}{flag}"
        )
    counts = report["counts"]
    shown = ", ".join(
        f"{status} {count}" for status, count in counts["statuses"].items() if count
    )
    lines.append(
        f"statuses: {shown}; play_end without run_enter: {counts['play_end_without_enter']}"
    )
    if "result_comparison" in report:
        comparison = report["result_comparison"]
        for seat in comparison["seats"]:
            journal, result = (
                seat[key] or "unknown"
                for key in ("journal_disposition", "result_disposition")
            )
            lines.append(
                f"  result {seat['seat']}: journal {journal}; native result {result}; {seat['relation']}"
            )
        lines.append(
            "result comparison: "
            + ", ".join(
                f"{name} {count}" for name, count in comparison["counts"].items()
            )
        )
    return "\n".join([*lines, report["caveat"]])


class _Cli(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        sys.stderr.write(
            "seat_stream: usage error; expected JOURNAL [--result RESULT] [--json]\n"
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    cli = _Cli(
        prog=PROG,
        description="Validate one snapshot of a native seat event journal and report seat exposure.",
    )
    cli.add_argument("journal")
    cli.add_argument("--result")
    cli.add_argument("--json", action="store_true")
    args = cli.parse_args(argv)
    try:
        report = build(args.journal, args.result)
    except Malformed as error:
        print(f"seat_stream: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(report, allow_nan=False, separators=(",", ":"), sort_keys=True)
        if args.json
        else text(report)
    )
    conflict = (
        "result_comparison" in report
        and report["result_comparison"]["counts"]["conflict"] > 0
    )
    return 1 if conflict else 0


if __name__ == "__main__":
    raise SystemExit(main())
