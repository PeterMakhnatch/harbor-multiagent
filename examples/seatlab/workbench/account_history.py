from __future__ import annotations

import json
import os
import re
import stat
from decimal import Decimal, Inexact, Rounded, localcontext

from .account_contract import MAX_ATTEMPTS, _attempt_row, validate
from .seat_stream import MIB, UUID, Malformed, Record, _read, _require, _strict_json

SCHEMA = "seatlab.native-attempt-history/v1"
ACCOUNT_SCHEMA = "seatlab.native-attempt-account/v1"
SOURCE = "native-job"
MAX_RETRIES = 16_384
MAX_ACCOUNTS = MAX_ATTEMPTS + MAX_RETRIES
MAX_ENTRIES = 65_536
MAX_ACCOUNT_BYTES = MIB
MAX_ARCHIVE_BYTES = 256 * MIB
MAX_ARCHIVE_SEAT_ROWS = 262_144
USD_PRECISION = 100
MAX_TOTAL_DIGITS = 21
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
NAME = (
    rf"(?P<job>{UUID.pattern})\.(?P<slot>[0-9a-f]{{64}})\.(?P<attempt>{UUID.pattern})"
)
ACCOUNT = re.compile(rf"{NAME}\.json")
STAGING = re.compile(rf"\.{NAME}\.json\.[0-9a-f]{{32}}\.tmp")
JOURNAL = re.compile(rf"{NAME}\.jsonl")
PATTERNS = (("account", ACCOUNT), ("staging", STAGING), ("journals", JOURNAL))
SKIPPED = ("journals", "other_job", "staging_published", "staging_unpublished")
FINAL_STATES = ("confirmed", "disagreeing", "uncaptured")
NONFINAL_STATES = (
    "superseded_confirmed_slot",
    "superseded_unconfirmed_slot",
    "other_unordered_slot",
    "captured_without_final",
)
SHARED_SEAT_FIELDS = (
    "name",
    "under_test",
    "disposition",
    "harness",
    "reported_provider",
    "reported_model",
    "input_tokens",
    "cache_tokens",
    "output_tokens",
    "harness_usd",
)
CAVEAT = (
    "Joins retained native attempt accounts to one native-job final packet by attempt UUID, slot digest and exact "
    "projection. Entry names are trusted-collector labels, not authentication; the archive is assumed stationary "
    "and is only checked for the same directory identity and name set before and after reading. Only attempts in a "
    "slot whose final account is confirmed are nonfinal; a capture in a slot without a retained final is unknown, "
    "never a discarded retry. Unless job_closed is true, another attempt in a final slot is other_unordered_slot: "
    "the snapshot and archive may be asynchronous, so it is not provably earlier, and no expected count, agreement, "
    "completeness or USD is concluded. No earlier reward is read as a grade. harness_usd is the native harness "
    "figure, not a bill; total is set only when the job is closed, every count reconciles, every final is confirmed "
    "and every nonfinal seat is priced."
)


def _snapshot(archive: str) -> tuple[tuple[int, int], list[str]]:
    try:
        fd = os.open(archive, DIR_FLAGS)
    except (OSError, ValueError) as error:
        raise Malformed("archive is not a readable non-symlink directory") from error
    names: list[str] = []
    try:
        info = os.fstat(fd)
        _require(stat.S_ISDIR(info.st_mode), "archive is not a directory")
        with os.scandir(fd) as entries:
            for entry in entries:
                _require(
                    len(names) < MAX_ENTRIES, f"archive exceeds {MAX_ENTRIES} entries"
                )
                names.append(entry.name)
    except OSError as error:
        raise Malformed("archive is unreadable") from error
    finally:
        os.close(fd)
    return (info.st_dev, info.st_ino), sorted(names)


def _kind(name: str, job: str) -> str:
    for kind, pattern in PATTERNS:
        match = pattern.fullmatch(name)
        if match is not None:
            return kind if match["job"] == job else "other_job"
    raise Malformed("archive holds an entry that is not a retained attempt")


def _canonical(value: Record) -> bytes:
    text = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return text.encode("ascii")


def _account(data: bytes, name: str, job: str) -> Record:
    match = ACCOUNT.fullmatch(name)
    if match is None or match["job"] != job:
        raise Malformed("attempt account name names another job")
    document = _strict_json(data, "attempt account is not strict UTF-8 JSON")
    _require(
        type(document) is dict
        and document.keys() == {"schema", "job", "attempt"}
        and document["schema"] == ACCOUNT_SCHEMA,
        "unsupported attempt account",
    )
    _require(document["job"] == job, "attempt account belongs to another job")
    row = _attempt_row(document["attempt"])
    _require(
        row["trial"] == match["attempt"] and row["slot"] == match["slot"],
        "attempt account names another attempt or slot",
    )
    stored = {"schema": ACCOUNT_SCHEMA, "job": job, "attempt": row}
    _require(_canonical(stored) + b"\n" == data, "attempt account is not canonical")
    return row


def _accounts(job: str, archive: str) -> tuple[dict[str, Record], dict[str, int]]:
    _require(
        type(archive) is str and 0 < len(archive) <= 4096,
        "archive path is not a string",
    )
    pin, names = _snapshot(archive)
    skipped = dict.fromkeys(SKIPPED, 0)
    present = set(names)
    rows: dict[str, Record] = {}
    remaining = MAX_ARCHIVE_BYTES
    seats = 0
    for name in names:
        kind = _kind(name, job)
        if kind == "staging":
            kind = (
                "staging_published"
                if name[1:].rsplit(".", 2)[0] in present
                else "staging_unpublished"
            )
        if kind != "account":
            skipped[kind] += 1
            continue
        _require(
            len(rows) < MAX_ACCOUNTS, f"archive exceeds {MAX_ACCOUNTS} attempt accounts"
        )
        _require(remaining > 0, "attempt accounts exceed 256 MiB in total")
        data = _read(
            os.path.join(archive, name),
            min(MAX_ACCOUNT_BYTES, remaining),
            "attempt account",
        )
        remaining -= len(data)
        _require(
            0 <= remaining <= MAX_ARCHIVE_BYTES,
            "attempt accounts exceed 256 MiB in total",
        )
        row = _account(data, name, job)
        _require(row["trial"] not in rows, "archive holds one attempt in two slots")
        seats += len(row["seats"])
        _require(seats <= MAX_ARCHIVE_SEAT_ROWS, "archive has too many seat rows")
        rows[row["trial"]] = row
    _require(
        _snapshot(archive) == (pin, names), "archive moved or changed while reading"
    )
    return rows, skipped


def _final_states(finals: list[Record], rows: dict[str, Record]) -> dict[str, str]:
    states: dict[str, str] = {}
    for final in finals:
        kept = rows.get(final["trial"])
        if kept is None:
            states[final["slot"]] = "uncaptured"
        else:
            same = _canonical(kept) == _canonical(final)
            states[final["slot"]] = "confirmed" if same else "disagreeing"
    assert len(states) == len(finals)
    return states


def _nonfinal(
    finals: list[Record], rows: dict[str, Record], states: dict[str, str], closed: bool
) -> tuple[dict[str, int], list[Record]]:
    final_trials = {final["trial"] for final in finals}
    counts = dict.fromkeys(NONFINAL_STATES, 0)
    confirmed: list[Record] = []
    for trial in sorted(rows):
        if trial in final_trials:
            continue
        state = states.get(rows[trial]["slot"])
        if state is None:
            counts["captured_without_final"] += 1
        elif not closed:
            counts["other_unordered_slot"] += 1
        elif state == "confirmed":
            counts["superseded_confirmed_slot"] += 1
            confirmed.append(rows[trial])
        else:
            counts["superseded_unconfirmed_slot"] += 1
    assert closed or not confirmed
    assert sum(counts.values()) == len(rows.keys() - final_trials)
    return counts, confirmed


def _usd(rows: list[Record]) -> Record:
    seat_rows = priced = full = 0
    with localcontext() as context:
        context.prec = USD_PRECISION
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        total = Decimal(0)
        for row in rows:
            known = [
                Decimal(s["harness_usd"])
                for s in row["seats"]
                if s["harness_usd"] is not None
            ]
            seat_rows += len(row["seats"])
            priced += len(known)
            full += int(bool(row["seats"]) and len(known) == len(row["seats"]))
            for amount in known:
                total += amount
    assert 0 <= full <= len(rows) and 0 <= priced <= seat_rows
    _require(
        total.is_finite() and not total < 0 and total.adjusted() <= MAX_TOTAL_DIGITS,
        "USD total out of range",
    )
    text = format(total, "f")
    amount_text = text.rstrip("0").rstrip(".") if "." in text else text
    return {
        "known": amount_text,
        "total": None,
        "attempts": len(rows),
        "attempts_fully_priced": full,
        "seat_rows": seat_rows,
        "seat_rows_priced": priced,
    }


def _shared(rows: list[Record]) -> dict[str, Record]:
    return {
        row["trial"]: {
            "slot": row["slot"],
            "seats": [
                {field: s[field] for field in SHARED_SEAT_FIELDS} for s in row["seats"]
            ],
        }
        for row in rows
    }


def _history(final: Record, archive_directory: str) -> tuple[Record, dict[str, Record]]:
    packet = validate(final)
    _require(packet["source"] == SOURCE, "final packet is not a native-job account")
    job_closed = packet.get("job_closed")
    _require(
        job_closed is None or type(job_closed) is bool,
        "final packet job_closed is not a boolean or null",
    )
    closed = job_closed is True
    job, registered, retries, finals = (
        packet["scope"],
        packet["registered"],
        packet["retries"],
        packet["attempts"],
    )
    rows, skipped = _accounts(job, archive_directory)
    slots = {a["slot"] for a in finals} | {r["slot"] for r in rows.values()}
    _require(
        len(slots) <= registered, "archive and finals name more slots than registered"
    )
    states = _final_states(finals, rows)
    counts, confirmed = _nonfinal(finals, rows, states, closed)
    final_counts = {
        state: sum(v == state for v in states.values()) for state in FINAL_STATES
    }
    missing = registered - len(finals)
    superseded = (
        counts["superseded_confirmed_slot"] + counts["superseded_unconfirmed_slot"]
    )
    expected = retries if closed and retries is not None and missing == 0 else None
    agree = (
        expected is not None
        and counts["captured_without_final"] == 0
        and superseded == expected
    )
    usd = _usd(confirmed)
    if not closed:
        usd["known"] = None
    complete = (
        closed
        and agree
        and final_counts["confirmed"] == registered
        and skipped["staging_unpublished"] == 0
        and counts["superseded_unconfirmed_slot"] == 0
        and usd["attempts_fully_priced"] == usd["attempts"]
    )
    if complete:
        usd["total"] = usd["known"]
    return {
        "schema": SCHEMA,
        "job": job,
        "job_closed": job_closed,
        "registered": registered,
        "retries": retries,
        "final_attempts": len(finals),
        "missing_finals": missing,
        "archive_accounts": len(rows),
        "skipped": skipped,
        "finals": final_counts,
        "nonfinal": counts,
        "unique_executions": len(rows.keys() | {a["trial"] for a in finals}),
        "expected_nonfinal": expected,
        "counts_agree": agree,
        "harness_usd": usd,
        "complete": complete,
        "attempts": _shared(confirmed),
        "caveat": CAVEAT,
    }, rows


def history(final: Record, archive_directory: str) -> Record:
    report, _ = _history(final, archive_directory)
    return report


def history_slots(
    final: Record, archive_directory: str
) -> tuple[Record, dict[str, str]]:
    report, rows = _history(final, archive_directory)
    executions = {trial: row["slot"] for trial, row in rows.items()}
    assert len(executions) == report["archive_accounts"]
    return report, executions
