from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, NoReturn

ROLE = "decision_maker"
_REPO = Path(__file__).resolve().parents[3]
_SCHEMA = "seatlab.continuation-direct/v1"
_MAX_CALLS = 128
_MAX_CASES = 1024
_MAX_ID_CHARS = 256
_MAX_LINE_BYTES = 6 * 8192 + 1024
_STUDY = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CATEGORY = re.compile(r"[a-z][a-z_]{0,63}")
_FIELDS = {"id", "prompt_sha256", "prompt"}
_TABLES = (
    (
        "CREATE TABLE IF NOT EXISTS book (one INTEGER PRIMARY KEY CHECK (one = 1), "
        "schema TEXT NOT NULL, study TEXT NOT NULL, max_calls INTEGER NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, prompt_sha256 TEXT NOT NULL, "
        "run TEXT NOT NULL UNIQUE, state TEXT NOT NULL CHECK (state IN ('open', 'closed', 'failed')), "
        "category TEXT, reply_json TEXT, served_model TEXT, input_tokens INTEGER, "
        "cache_tokens INTEGER, output_tokens INTEGER, claimed_at TEXT NOT NULL, finished_at TEXT)"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS book_fixed BEFORE UPDATE ON book "
        "BEGIN SELECT RAISE(ABORT, 'book binding is immutable'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS book_kept BEFORE DELETE ON book "
        "BEGIN SELECT RAISE(ABORT, 'book binding is immutable'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS case_final BEFORE UPDATE ON cases "
        "WHEN OLD.state != 'open' OR NEW.state = 'open' OR NEW.id != OLD.id "
        "OR NEW.prompt_sha256 != OLD.prompt_sha256 OR NEW.run != OLD.run "
        "BEGIN SELECT RAISE(ABORT, 'booked case is final'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS case_kept BEFORE DELETE ON cases "
        "BEGIN SELECT RAISE(ABORT, 'booked cases are kept'); END"
    ),
)

_Case = tuple[str, str, str]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ledger_prefix(study: str) -> str:
    if _STUDY.fullmatch(study) is None:
        raise ValueError("study must be a lowercase slug of at most 63 characters")
    return "scd" + _sha256(f"{_SCHEMA}\0{study}")[:12] + "-"


def ledger_run(study: str, case_id: str) -> str:
    return ledger_prefix(study) + _sha256(f"{study}\0{case_id}")[:32]


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("non-finite JSON constant")


def _decode(data: bytes) -> Any:
    return json.loads(
        data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_reject_constant
    )


def _case(line: bytes, seen: set[str], deepseek: Any) -> _Case:
    if len(line) > _MAX_LINE_BYTES or not line.endswith(b"\n"):
        raise ValueError(
            f"expected a newline-terminated line of at most {_MAX_LINE_BYTES} bytes"
        )
    record = _decode(line[:-1])
    if not isinstance(record, dict) or record.keys() != _FIELDS:
        raise ValueError("expected exactly id, prompt_sha256, and prompt")
    case_id, digest, prompt = record["id"], record["prompt_sha256"], record["prompt"]
    if (
        not isinstance(case_id, str)
        or not isinstance(digest, str)
        or not isinstance(prompt, str)
    ):
        raise TypeError("id, prompt_sha256, and prompt must be strings")
    if not 1 <= len(case_id) <= _MAX_ID_CHARS or case_id in seen:
        raise ValueError(f"id must be unique and 1 to {_MAX_ID_CHARS} characters")
    _sha256(case_id)
    if _HEX64.fullmatch(digest) is None or _sha256(prompt) != digest:
        raise ValueError(
            "prompt_sha256 must be the lowercase SHA-256 of the exact UTF-8 prompt"
        )
    try:
        deepseek._payload(prompt)
    except deepseek.SeatUnavailable:
        raise ValueError("prompt does not fit the direct request payload") from None
    seen.add(case_id)
    return case_id, digest, prompt


def _requests(stream: BinaryIO, deepseek: Any) -> list[_Case]:
    cases: list[_Case] = []
    seen: set[str] = set()
    for number in range(1, _MAX_CASES + 2):
        line = stream.readline(_MAX_LINE_BYTES + 1)
        if not line:
            break
        if number > _MAX_CASES:
            raise ValueError(f"stdin: expected at most {_MAX_CASES} requests")
        try:
            cases.append(_case(line, seen, deepseek))
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"stdin:{number}: {exc}") from None
    if not cases:
        raise ValueError("stdin: expected at least one request")
    return cases


def _open(value: Path, *, create: bool) -> sqlite3.Connection:
    book = value.absolute()
    if book.parent.resolve().is_relative_to(_REPO):
        raise ValueError("BOOK must be outside the git checkout")
    parent = os.lstat(book.parent)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o077
    ):
        raise ValueError(
            "BOOK must be in a private 0700 directory you own, not a symlink"
        )
    if create:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with suppress(FileExistsError):
            os.close(os.open(book, flags, 0o600))
    info = os.lstat(book)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        raise ValueError("BOOK must be a private 0600 regular file you own, not a link")
    return sqlite3.connect(
        book.as_uri() + "?mode=rw", uri=True, timeout=30, isolation_level=None
    )


@contextmanager
def _transaction(db: sqlite3.Connection) -> Iterator[None]:
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def _bind(db: sqlite3.Connection, study: str, max_calls: int) -> None:
    with _transaction(db):
        for statement in _TABLES:
            db.execute(statement)
        row = db.execute("SELECT schema, study, max_calls FROM book").fetchone()
        if row is None:
            db.execute(
                "INSERT INTO book VALUES (1, ?, ?, ?)", (_SCHEMA, study, max_calls)
            )
        elif tuple(row) != (_SCHEMA, study, max_calls):
            raise ValueError("BOOK is bound to a different --study or --max-calls")


def _binding(db: sqlite3.Connection) -> tuple[str, int]:
    db.execute("PRAGMA query_only = ON")
    try:
        row = db.execute("SELECT schema, study, max_calls FROM book").fetchone()
    except sqlite3.OperationalError:
        row = None
    if (
        row is None
        or row[0] != _SCHEMA
        or not isinstance(row[1], str)
        or _STUDY.fullmatch(row[1]) is None
        or type(row[2]) is not int
        or not 1 <= row[2] <= _MAX_CALLS
    ):
        raise ValueError("BOOK has no valid study binding")
    return row[1], row[2]


def _unbooked(db: sqlite3.Connection, cases: list[_Case]) -> int:
    count = 0
    for number, (case_id, digest, _) in enumerate(cases, start=1):
        row = db.execute(
            "SELECT prompt_sha256 FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if row is None:
            count += 1
        elif row[0] != digest:
            raise ValueError(f"stdin:{number}: stale prompt_sha256 for a booked id")
    return count


def _claim(
    db: sqlite3.Connection,
    case: _Case,
    run: str,
    max_calls: int,
    ledger_has: Callable[[str], bool],
) -> str:
    case_id, digest, _ = case
    with _transaction(db):
        row = db.execute(
            "SELECT prompt_sha256, state FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if row is not None:
            if row[0] != digest:
                raise ValueError("stale prompt_sha256 for a booked id")
            return row[1]
        if ledger_has(run):
            return "blocked"
        if db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] >= max_calls:
            return "cap"
        db.execute(
            "INSERT INTO cases (id, prompt_sha256, run, state, claimed_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (case_id, digest, run, datetime.now(UTC).isoformat()),
        )
        return "claimed"


def _finish(
    db: sqlite3.Connection,
    case_id: str,
    run: str,
    outcome: tuple[str, str | None, str | None, str | None],
    usage: tuple[int, int, int] | tuple[None, None, None],
) -> None:
    changed = db.execute(
        "UPDATE cases SET state = ?, category = ?, reply_json = ?, served_model = ?, "
        "input_tokens = ?, cache_tokens = ?, output_tokens = ?, finished_at = ? "
        "WHERE id = ? AND run = ? AND state = 'open'",
        (*outcome, *usage, datetime.now(UTC).isoformat(), case_id, run),
    ).rowcount
    if changed != 1:
        raise ValueError("booked case is no longer open")


def _safe(category: object) -> str:
    if isinstance(category, str) and _CATEGORY.fullmatch(category) is not None:
        return category
    return "provider_error"


def _call(db: sqlite3.Connection, deepseek: Any, case: _Case, run: str) -> str | None:
    case_id, _, prompt = case
    try:
        turn = deepseek.turn(prompt, run_id=run, role=ROLE)
    except deepseek.SeatUnavailable as exc:
        category = _safe(exc.category)
        usage = exc.settled_usage
        if not (
            isinstance(usage, tuple)
            and len(usage) == 3
            and all(type(count) is int and count >= 0 for count in usage)
        ):
            usage = (None, None, None)
        _finish(db, case_id, run, ("failed", category, None, None), usage)
        return category
    except (OSError, ValueError, sqlite3.Error):
        category = "direct_or_ledger_error"
        _finish(db, case_id, run, ("failed", category, None, None), (None, None, None))
        return category
    usage = (turn.input_tokens, turn.cache_tokens, turn.output_tokens)
    outcome = ("closed", None, json.dumps(turn.text), turn.served_model)
    _finish(db, case_id, run, outcome, usage)
    return None


def _tally() -> dict[str, Any]:
    return {
        "closed_now": [],
        "skipped_closed": 0,
        "open_unknown": [],
        "failed": [],
        "blocked_ledger": [],
        "over_cap": 0,
        "not_reached": 0,
        "stopped": None,
    }


def _work(
    db: sqlite3.Connection,
    deepseek: Any,
    cases: list[_Case],
    study: str,
    max_calls: int,
    ledger_has: Callable[[str], bool],
) -> dict[str, Any]:
    tally = _tally()
    lists = {"open": "open_unknown", "failed": "failed", "blocked": "blocked_ledger"}
    for handled, case in enumerate(cases):
        run = ledger_run(study, case[0])
        state = _claim(db, case, run, max_calls, ledger_has)
        if state == "cap":
            tally["over_cap"] += 1
        elif state == "closed":
            tally["skipped_closed"] += 1
        elif state in lists:
            tally[lists[state]].append(case[0])
        else:
            stopped = _call(db, deepseek, case, run)
            tally["failed" if stopped else "closed_now"].append(case[0])
            if stopped is not None:
                tally["stopped"] = stopped
                tally["not_reached"] = len(cases) - handled - 1
                break
    return tally


def _run(
    book: Path, study: str, max_calls: int, stream: BinaryIO
) -> tuple[dict[str, Any], int]:
    from ..visibility import deepseek, spend

    cases = _requests(stream, deepseek)
    summary: dict[str, Any] = {
        "schema": f"{_SCHEMA}/run",
        "study": study,
        "max_calls": max_calls,
        "role": ROLE,
        "ledger_run_prefix": ledger_prefix(study),
        "requests": len(cases),
    }
    with closing(_open(book, create=True)) as db:
        _bind(db, study, max_calls)
        unbooked = _unbooked(db, cases)
        ledger = os.environ.get("SEATLAB_SPEND_DIR", "")
        try:
            deepseek.bind_host_key()
            spend.bind_host_spend_dir()
            if unbooked:
                deepseek.preflight()
        except deepseek.SeatUnavailable as exc:
            summary.update(
                _tally(), stopped=_safe(exc.category), not_reached=len(cases)
            )
            return summary, 1

        def ledger_has(run: str) -> bool:
            return bool(spend.report(Path(ledger), run_prefix=run)["calls"])

        summary.update(_work(db, deepseek, cases, study, max_calls, ledger_has))
    return summary, int(summary["stopped"] is not None)


def _export(book: Path) -> bytes:
    with closing(_open(book, create=False)) as db:
        _binding(db)
        rows = db.execute(
            "SELECT id, prompt_sha256, reply_json FROM cases WHERE state = 'closed' ORDER BY id"
        ).fetchall()
    lines = []
    for case_id, digest, reply_json in rows:
        reply = json.loads(reply_json) if isinstance(reply_json, str) else None
        if not isinstance(reply, str):
            raise TypeError("BOOK has a closed case without reply text")
        record = {"id": case_id, "prompt_sha256": digest, "reply": reply}
        lines.append(json.dumps(record, sort_keys=True) + "\n")
    return "".join(lines).encode("ascii")


def _ledger(prefix: str) -> dict[str, Any] | str | None:
    value = os.environ.get("SEATLAB_SPEND_DIR", "")
    if not value or not Path(value).is_absolute():
        return None
    from ..visibility import spend

    try:
        report = spend.report(Path(value), run_prefix=prefix)
    except (OSError, ValueError, sqlite3.Error):
        return "unavailable"
    calls = [call for call in report["calls"] if call["role"] == ROLE]
    low = sum(
        (
            Decimal(call["estimated_usd_low"])
            for call in calls
            if call["estimated_usd_low"]
        ),
        Decimal(0),
    )
    high = sum(
        (
            Decimal(call["estimated_usd_high"] or call["unsettled_hold_usd"])
            for call in calls
        ),
        Decimal(0),
    )
    return {
        "role": ROLE,
        "run_prefix": prefix,
        "calls": len(calls),
        "unsettled_calls": sum(call["estimated_usd_high"] is None for call in calls),
        "known_usd_low": f"{low:.9f}",
        "upper_usd_including_holds": f"{high:.9f}",
    }


def _status(book: Path) -> dict[str, Any]:
    with closing(_open(book, create=False)) as db:
        study, max_calls = _binding(db)
        counts = dict(
            db.execute("SELECT state, COUNT(*) FROM cases GROUP BY state").fetchall()
        )
        categories = dict(
            db.execute(
                "SELECT COALESCE(category, 'unknown'), COUNT(*) FROM cases "
                "WHERE state = 'failed' GROUP BY 1"
            ).fetchall()
        )
    prefix = ledger_prefix(study)
    return {
        "schema": f"{_SCHEMA}/status",
        "study": study,
        "max_calls": max_calls,
        "closed": counts.get("closed", 0),
        "open": counts.get("open", 0),
        "failed": counts.get("failed", 0),
        "failed_categories": categories,
        "remaining_slots": max(0, max_calls - sum(counts.values())),
        "role": ROLE,
        "ledger_run_prefix": prefix,
        "ledger": _ledger(prefix),
    }


def _study(value: str) -> str:
    if _STUDY.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "expected a lowercase slug of at most 63 characters"
        )
    return value


def _max_calls(value: str) -> int:
    if re.fullmatch(r"[1-9][0-9]{0,2}", value) is None or int(value) > _MAX_CALLS:
        raise argparse.ArgumentTypeError(f"expected an integer from 1 to {_MAX_CALLS}")
    return int(value)


def _arguments(argv: list[str]) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    parser = argparse.ArgumentParser(
        prog="python -m examples.seatlab.continuation.direct",
        description="Opt-in direct decision_maker worker over emitted request JSONL.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="claim each stdin request once in BOOK and call it at most once"
    )
    run.add_argument("book", type=Path, metavar="BOOK")
    run.add_argument("--study", type=_study, required=True)
    run.add_argument("--max-calls", type=_max_calls, required=True)
    export = commands.add_parser(
        "export", help="print closed cases as scorer reply JSONL"
    )
    export.add_argument("book", type=Path, metavar="BOOK")
    status = commands.add_parser("status", help="print BOOK counts and ledger prefix")
    status.add_argument("book", type=Path, metavar="BOOK")
    return parser, parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parser, args = _arguments(sys.argv[1:] if argv is None else argv)
    code = 0
    try:
        if args.command == "run":
            summary, code = _run(
                args.book, args.study, args.max_calls, sys.stdin.buffer
            )
            output = (json.dumps(summary, sort_keys=True) + "\n").encode("utf-8")
        elif args.command == "export":
            output = _export(args.book)
        else:
            output = (json.dumps(_status(args.book), sort_keys=True) + "\n").encode(
                "utf-8"
            )
    except (OSError, TypeError, ValueError, sqlite3.Error, RecursionError) as exc:
        parser.error(str(exc))
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return code


def _interrupt(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)
    raise SystemExit(main())
