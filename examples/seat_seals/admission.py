import argparse
import json
import os
import sqlite3
import stat
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote

from ..native_study import record_bytes, require
from .admission_wire import (
    ALREADY_ISSUED,
    GRANTED,
    MAX_ENVELOPE_BYTES,
    Message,
    make_reply,
    read_message,
    strict_object,
    validate_request,
    wire_bytes,
)
from .worker_layout import (
    MAX_PATH_BYTES,
    MAX_PLAN_BYTES,
    assigned,
    digest,
    load_plan,
    validate_plan,
)

LEDGER_SCHEMA = "harbor.seat-seal.admission-ledger/v1"
STATUS_SCHEMA = "harbor.seat-seal.admission-status/v1"
STORE_NAME = "admission.sqlite3"
APPLICATION_ID = 0x53534C41
USER_VERSION = 1
BUSY_SECONDS = 30.0
PRIVATE_BITS = 0o077
DIRECTORY_MODE = 0o700
STORE_MODE = 0o600
ISSUED = "issued"
UNISSUED = "unissued"

META_DDL = (
    "CREATE TABLE meta (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "schema TEXT NOT NULL, plan_digest TEXT NOT NULL, "
    "registration TEXT NOT NULL, plan TEXT NOT NULL)"
)
TICKETS_DDL = (
    "CREATE TABLE tickets (ticket TEXT NOT NULL PRIMARY KEY "
    "CHECK (typeof(ticket) = 'text'), "
    "worker INTEGER NOT NULL CHECK (typeof(worker) = 'integer' AND worker >= 0), "
    "nonce TEXT CHECK (nonce IS NULL OR (typeof(nonce) = 'text' "
    "AND length(nonce) = 32 AND nonce NOT GLOB '*[^0-9a-f]*'))) WITHOUT ROWID"
)
GUARDS = (
    ("meta_insert", "meta", "BEFORE INSERT ON meta", "the plan is immutable"),
    ("meta_update", "meta", "BEFORE UPDATE ON meta", "the plan is immutable"),
    ("meta_delete", "meta", "BEFORE DELETE ON meta", "the plan is immutable"),
    ("tickets_insert", "tickets", "BEFORE INSERT ON tickets", "inventory is fixed"),
    ("tickets_delete", "tickets", "BEFORE DELETE ON tickets", "rows are permanent"),
    (
        "tickets_update",
        "tickets",
        "BEFORE UPDATE ON tickets WHEN OLD.nonce IS NOT NULL OR NEW.nonce IS NULL "
        "OR NEW.ticket IS NOT OLD.ticket OR NEW.worker IS NOT OLD.worker",
        "rows only move to issued",
    ),
)


def _guard_ddl(name: str, event: str, reason: str) -> str:
    return f"CREATE TRIGGER {name} {event} BEGIN SELECT RAISE(ABORT, '{reason}'); END"


SCHEMA_OBJECTS = sorted(
    [("table", "meta", "meta", META_DDL), ("table", "tickets", "tickets", TICKETS_DDL)]
    + [
        ("trigger", name, table, _guard_ddl(name, event, reason))
        for name, table, event, reason in GUARDS
    ]
)

Inventory = list[tuple[str, int]]


@dataclass(frozen=True, slots=True)
class Ledger:
    plan: str
    registration: str
    owners: dict[str, int]


def _bounded(path: Path, label: str) -> Path:
    require(isinstance(path, Path), f"{label} path is not a Path")
    size = len(os.fsencode(path))
    require(0 < size <= MAX_PATH_BYTES, f"{label} path exceeds {MAX_PATH_BYTES} bytes")
    return path


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as error:
        raise ValueError(f"{label} {path} does not exist") from error


def _check_private(info: os.stat_result, label: str) -> None:
    require(info.st_uid == os.geteuid(), f"{label} is owned by another user")
    require(info.st_mode & PRIVATE_BITS == 0, f"{label} is not private to its owner")


def _check_directory(path: Path) -> None:
    info = _lstat(path, "admission ledger")
    require(
        stat.S_ISDIR(info.st_mode),
        f"admission ledger {path} is a symlink or not a directory",
    )
    _check_private(info, "admission ledger")


def _check_store(path: Path) -> None:
    info = _lstat(path, "admission store")
    require(stat.S_ISREG(info.st_mode), f"admission store {path} is not a plain file")
    require(info.st_nlink == 1, f"admission store {path} has other links")
    _check_private(info, "admission store")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _connect(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(os.path.abspath(path)) + "?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=BUSY_SECONDS, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA fullfsync=ON")
    except BaseException:
        db.close()
        raise
    return db


def _rollback(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        db.execute("ROLLBACK")


def _inventory(plan: dict[str, Any]) -> Inventory:
    rows = [
        (ticket, worker)
        for worker in range(plan["workers"])
        for ticket in assigned(plan, worker)
    ]
    rows.sort()
    assert len(rows) == 2 * len(plan["blocks"])
    assert len({ticket for ticket, _ in rows}) == len(rows)
    return rows


def _populate(db: sqlite3.Connection, plan: dict[str, Any], rows: Inventory) -> None:
    require(
        db.execute("SELECT count(*) FROM sqlite_master").fetchone() == (0,),
        "admission store is not empty",
    )
    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
    db.execute(f"PRAGMA user_version={USER_VERSION}")
    db.execute(META_DDL)
    db.execute(TICKETS_DDL)
    db.execute(
        "INSERT INTO meta (singleton, schema, plan_digest, registration, plan) "
        "VALUES (1, ?, ?, ?, ?)",
        (LEDGER_SCHEMA, digest(plan), plan["registration"], _plan_text(plan)),
    )
    db.executemany(
        "INSERT INTO tickets (ticket, worker, nonce) VALUES (?, ?, NULL)", rows
    )
    for name, _, event, reason in GUARDS:
        db.execute(_guard_ddl(name, event, reason))


def _plan_text(plan: dict[str, Any]) -> str:
    text = record_bytes(plan).decode("ascii")
    require(len(text) <= MAX_PLAN_BYTES, "worker plan exceeds its bound")
    return text


def init(plan_path: Path, ledger: Path, expected: str) -> Message:
    plan = load_plan(_bounded(plan_path, "worker plan"), expected=expected)
    rows = _inventory(plan)
    ledger = _bounded(ledger, "admission ledger")
    os.mkdir(ledger, DIRECTORY_MODE)
    _check_directory(ledger)
    store = ledger / STORE_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    os.close(os.open(store, flags, STORE_MODE))
    _check_store(store)
    db = _connect(store)
    try:
        db.execute("BEGIN IMMEDIATE")
        _populate(db, plan, rows)
        db.execute("COMMIT")
    finally:
        _rollback(db)
        db.close()
    _fsync_directory(ledger)
    _fsync_directory(ledger.parent)
    return {
        "plan": digest(plan),
        "registration": plan["registration"],
        "workers": plan["workers"],
        "tickets": len(rows),
    }


def _check_header(db: sqlite3.Connection) -> None:
    require(
        db.execute("PRAGMA quick_check").fetchall() == [("ok",)],
        "admission store fails its integrity check",
    )
    require(
        db.execute("PRAGMA application_id").fetchone() == (APPLICATION_ID,)
        and db.execute("PRAGMA user_version").fetchone() == (USER_VERSION,),
        "admission store is not an initialized admission ledger",
    )
    objects = db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    require(objects == SCHEMA_OBJECTS, "admission store schema differs")


def _read_meta(db: sqlite3.Connection) -> tuple[dict[str, Any], str]:
    rows = db.execute(
        "SELECT schema, plan_digest, registration, plan FROM meta"
    ).fetchall()
    require(len(rows) == 1, "admission store has no single plan record")
    schema, plan_digest, registration, text = rows[0]
    require(schema == LEDGER_SCHEMA, "admission ledger schema differs")
    require(
        isinstance(text, str) and len(text) <= MAX_PLAN_BYTES,
        "admission store plan is not bounded text",
    )
    plan = validate_plan(strict_object(text, "admission store plan"))
    require(_plan_text(plan) == text, "admission store plan is not canonical")
    require(digest(plan) == plan_digest, "admission store plan digest differs")
    require(plan["registration"] == registration, "admission registration differs")
    return plan, plan_digest


def _load(db: sqlite3.Connection) -> Ledger:
    db.execute("BEGIN")
    try:
        _check_header(db)
        plan, plan_digest = _read_meta(db)
        rows = db.execute("SELECT ticket, worker FROM tickets ORDER BY ticket").fetchall()
        expected = _inventory(plan)
        require(rows == expected, "admission store inventory differs from its plan")
        db.execute("COMMIT")
    finally:
        _rollback(db)
    return Ledger(plan_digest, plan["registration"], dict(expected))


def _open(ledger: Path) -> tuple[sqlite3.Connection, Ledger]:
    ledger = _bounded(ledger, "admission ledger")
    _check_directory(ledger)
    store = ledger / STORE_NAME
    _check_store(store)
    db = _connect(store)
    try:
        return db, _load(db)
    except BaseException:
        db.close()
        raise


def _check_binding(state: Ledger, request: Message) -> None:
    require(request["plan"] == state.plan, "request plan differs from the ledger plan")
    owner = state.owners.get(request["ticket"])
    require(owner is not None, "request ticket is not in the ledger plan")
    require(owner == request["worker"], "request ticket belongs to another worker")


def _claim(db: sqlite3.Connection, request: Message) -> str:
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT worker, nonce FROM tickets WHERE ticket = ?", (request["ticket"],)
        ).fetchone()
        require(row is not None and row[0] == request["worker"], "ticket owner differs")
        if row[1] is not None:
            return ALREADY_ISSUED
        cursor = db.execute(
            "UPDATE tickets SET nonce = ? WHERE ticket = ? AND worker = ? "
            "AND nonce IS NULL",
            (request["nonce"], request["ticket"], request["worker"]),
        )
        assert cursor.rowcount == 1
        db.execute("COMMIT")
        return GRANTED
    finally:
        _rollback(db)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    for _ in range(len(data)):
        if not view:
            return
        written = os.write(fd, view)
        require(written > 0, "admission reply was not written")
        view = view[written:]
    require(not view, "admission reply was not written")


def serve_once(ledger: Path, source: BinaryIO, sink: int) -> str:
    db, state = _open(ledger)
    try:
        request = validate_request(read_message(source.readline(MAX_ENVELOPE_BYTES + 1)))
        _check_binding(state, request)
        result = _claim(db, request)
    finally:
        db.close()
    _write_all(sink, wire_bytes(make_reply(request, result)))
    return result


def status(ledger: Path) -> Message:
    db, state = _open(ledger)
    try:
        rows = db.execute(
            "SELECT ticket, worker, nonce IS NOT NULL FROM tickets ORDER BY ticket"
        ).fetchall()
    finally:
        db.close()
    require(
        [(ticket, worker) for ticket, worker, _ in rows] == sorted(state.owners.items()),
        "admission store inventory changed",
    )
    tickets = [
        {"ticket": ticket, "worker": worker, "state": ISSUED if issued else UNISSUED}
        for ticket, worker, issued in rows
    ]
    counts = Counter(entry["state"] for entry in tickets)
    return {
        "schema": STATUS_SCHEMA,
        "plan": state.plan,
        "registration": state.registration,
        "counts": {ISSUED: counts[ISSUED], UNISSUED: counts[UNISSUED]},
        "tickets": tickets,
    }


def render(report: Message) -> str:
    counts = report["counts"]
    total = counts[ISSUED] + counts[UNISSUED]
    lines = [
        f"plan          {report['plan']}",
        f"registration  {report['registration']}",
        f"admitted      {counts[ISSUED]} of {total} tickets, {counts[UNISSUED]} unissued",
        "",
        "ticket               worker  state",
    ]
    lines += [
        f"{entry['ticket']:<20} {entry['worker']:>6}  {entry['state']}"
        for entry in report["tickets"]
    ]
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="python -m examples.seat_seals.admission")
    commands = cli.add_subparsers(dest="command", required=True)
    start = commands.add_parser("init", help="bind a new private ledger to one plan")
    start.add_argument("plan", type=Path)
    start.add_argument("ledger", type=Path)
    start.add_argument("--expected-plan", metavar="DIGEST", required=True)
    serve = commands.add_parser("serve-once", help="answer one request on stdin")
    serve.add_argument("ledger", type=Path)
    show = commands.add_parser("status", help="list issued and unissued tickets")
    show.add_argument("ledger", type=Path)
    show.add_argument("--json", action="store_true")
    return cli


def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        report = init(args.plan, args.ledger, args.expected_plan)
        print(json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True))
        return 0
    if args.command == "serve-once":
        result = serve_once(args.ledger, sys.stdin.buffer, sys.stdout.fileno())
        assert result in (GRANTED, ALREADY_ISSUED)
        return 0
    assert args.command == "status"
    report = status(args.ledger)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, allow_nan=False, sort_keys=True))
    else:
        print(render(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"admission: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
