"""Morning attention check over a spool of saved native jobs. Never starts a process, model or job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import NoReturn, override

from .job_stream import RESULT
from .native_account import _root, collect
from .seat_stream import MIB, Malformed, Record, _read, _require, _strict_json

SPOOL = "seatlab.workbench.spool/v1"
SCHEMA = "seatlab.workbench.attention/v2"
PROG = "python3.12 -BS -m examples.seatlab.workbench attention"
MAX_SPOOL_BYTES = MIB
MAX_JOBS = 32
MAX_PATH = 4096
MAX_FINDINGS = 2**53 - 1
CAPABILITIES = ("seats", "handoffs", "accounts", "witness")
OPTIONAL = ("starts",)
ROW_KEYS = frozenset({"job", *CAPABILITIES, *OPTIONAL})
STATUSES = {True: "closed", False: "open", None: "unknown"}
EXIT = {"clear": 0, "needs_attention": 1, "outstanding": 3}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
CAVEAT = (
    "Snapshot of saved native jobs; not an atomic file system snapshot. Only closed jobs are inventoried and "
    "analyzed. Open and unknown jobs are read from the job result alone; their trial directories are not "
    "inventoried, so retained is null. absent means the job directory or its result file did not exist, the same "
    "before and after the read; its job is null. open, unknown and absent are distinct states of this read, not "
    "conclusions: they are never clear and never findings. A capability marked not_requested was not checked. "
    "Findings count local evidence only. row is the 1-based position in the private spool. spool_sha256 is an opaque "
    "commitment to the exact spool bytes parsed; it does not authenticate the author or manage any worker. Only "
    "--expected-spool pins those bytes before any job is read; without it, the report cannot prove that no row was "
    "deleted."
)
STARTS_CAVEAT = (
    "starts checks only the presence and identity of private START receipts by native job, attempt and slot; it "
    "never shows a treatment, an input binding, a grade, a cost or a cause."
)

Entry = dict[str, str | None]


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid attention arguments. Use attention --help.\n")


def _location(base: str, value: object, label: str) -> str:
    _require(
        type(value) is str
        and value != ""
        and "\0" not in value
        and len(value.encode("utf-8", "surrogatepass")) <= MAX_PATH,
        f"{label} is not a path of at most 4096 bytes",
    )
    assert type(value) is str
    return value if os.path.isabs(value) else os.path.join(base, value)


def load_spool(path: str, expected: str | None) -> tuple[list[Entry], str]:
    _require(
        expected is None or DIGEST.fullmatch(expected) is not None,
        "spool digest is not sha256:<64 lowercase hex>",
    )
    data = _read(path, MAX_SPOOL_BYTES, "spool")
    digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
    _require(
        expected is None or digest == expected, "spool does not match --expected-spool"
    )
    document = _strict_json(data, "spool is not strict UTF-8 JSON")
    _require(
        type(document) is dict and document.keys() == {"schema", "jobs"},
        "spool does not hold exactly schema and jobs",
    )
    _require(document["schema"] == SPOOL, "spool has another schema")
    rows = document["jobs"]
    _require(
        type(rows) is list and 1 <= len(rows) <= MAX_JOBS,
        "spool jobs is not a list of 1 to 32 rows",
    )
    base = os.path.dirname(os.path.abspath(path))
    entries: list[Entry] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        label = f"spool row {index}"
        _require(
            type(row) is dict and "job" in row and row.keys() <= ROW_KEYS,
            f"{label} needs job and only known capabilities",
        )
        _require(
            "witness" not in row or "seats" in row,
            f"{label} requests witness without seats",
        )
        entry: Entry = {
            key: _location(base, row[key], f"{label} {key}") if key in row else None
            for key in ("job", *CAPABILITIES, *OPTIONAL)
        }
        job = os.path.normpath(str(entry["job"]))
        _require(job not in seen, f"{label} repeats a job path")
        seen.add(job)
        entries.append(entry)
    assert 1 <= len(entries) <= MAX_JOBS
    return entries, digest


def _stat(path: str, label: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise Malformed(f"{label} is unreadable") from error


def _stamp(found: os.stat_result) -> tuple[int, ...]:
    return (
        found.st_dev,
        found.st_ino,
        found.st_mode,
        found.st_size,
        found.st_mtime_ns,
        found.st_ctime_ns,
    )


def _missing(job: str) -> tuple[int, ...]:
    assert os.path.isabs(job)
    parent = job
    for _ in range(len(job)):
        parent = os.path.dirname(parent)
        if _stat(parent, "job directory") is not None:
            break
    try:
        found = os.stat(parent)
    except (OSError, ValueError) as error:
        raise Malformed("job path crosses a broken symlink") from error
    _require(stat.S_ISDIR(found.st_mode), "job path crosses a non-directory")
    return _stamp(found)


def _envelope(job: str) -> tuple[bool, tuple[int, ...]]:
    directory = _stat(job, "job directory")
    if directory is None:
        return False, _missing(job)
    _require(stat.S_ISDIR(directory.st_mode), "job is not a real non-symlink directory")
    result = _stat(os.path.join(job, RESULT), "job result")
    if result is None:
        return False, _stamp(directory)
    _require(
        stat.S_ISREG(result.st_mode), "job result is not a regular non-symlink file"
    )
    return True, _stamp(directory) + _stamp(result)


def _optional(value: str | None) -> Path | None:
    return None if value is None else Path(value)


def _findings(job: str, packet: Record, entry: Entry) -> dict[str, int]:
    from . import attention_details

    assert packet["job_closed"] is True
    optional = {
        name: path for name in OPTIONAL if (path := _optional(entry[name])) is not None
    }
    found = attention_details.analyze(
        Path(job),
        packet,
        seats=_optional(entry["seats"]),
        handoffs=_optional(entry["handoffs"]),
        accounts=_optional(entry["accounts"]),
        witness=_optional(entry["witness"]),
        **optional,
    )
    known = attention_details.ACTIONS
    _require(type(found) is dict, "attention analysis did not return counts")
    for code, count in found.items():
        _require(
            type(code) is str
            and code in known
            and type(count) is int
            and 0 < count <= MAX_FINDINGS,
            "attention analysis returned an unknown action or count",
        )
    return dict(sorted(found.items()))


def _checks(entry: Entry, closed: bool) -> dict[str, str]:
    asked = "requested" if closed else "not_run"
    checks = {"native_result": asked}
    for capability in CAPABILITIES:
        checks[capability] = asked if entry[capability] else "not_requested"
    for capability in OPTIONAL:
        if entry[capability]:
            checks[capability] = asked
    return checks


def _inspect(index: int, entry: Entry, seen: set[str]) -> Record:
    job = entry["job"]
    assert job is not None
    before = _envelope(job)
    row: Record = {
        "row": index + 1,
        "job": None,
        "status": "absent",
        "registered": None,
        "retained": None,
        "retries": None,
        "checks": _checks(entry, False),
        "findings": {},
        "outcome": "not_analyzed",
    }
    if before[0]:
        root = _root(job)
        scope, registered, retries, closed, _ = root
        _require(scope not in seen, "names a native job already in the spool")
        seen.add(scope)
        status = STATUSES[closed]
        row |= {
            "job": scope,
            "status": status,
            "registered": registered,
            "retries": retries,
            "checks": _checks(entry, status == "closed"),
        }
        if status == "closed":
            packet = collect(job, root)
            row["retained"] = len(packet["attempts"])
            row["findings"] = _findings(job, packet, entry)
            row["outcome"] = "needs_attention" if row["findings"] else "clear"
    _require(_envelope(job) == before, "job result changed during inspection")
    return row


def report(entries: list[Entry]) -> Record:
    _require(1 <= len(entries) <= MAX_JOBS, "spool jobs is not a list of 1 to 32 rows")
    rows: list[Record] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            rows.append(_inspect(index, entry, seen))
        except Malformed as error:
            raise Malformed(f"spool row {index} {error}") from error
    rows.sort(key=lambda row: (row["job"] is None, row["job"] or "", row["row"]))
    actions: dict[str, int] = {}
    for row in rows:
        for code, count in row["findings"].items():
            actions[code] = actions.get(code, 0) + count
    counts = {status: 0 for status in (*STATUSES.values(), "absent")}
    for row in rows:
        counts[row["status"]] += 1
    attention = sum(row["outcome"] == "needs_attention" for row in rows)
    if attention:
        state = "needs_attention"
    else:
        state = (
            "outstanding"
            if counts["open"] + counts["unknown"] + counts["absent"]
            else "clear"
        )
    caveat = CAVEAT
    if any(entry["starts"] for entry in entries):
        caveat = f"{CAVEAT} {STARTS_CAVEAT}"
    return {
        "schema": SCHEMA,
        "status": state,
        "counts": {"jobs": len(rows), "needs_attention": attention, **counts},
        "actions": dict(sorted(actions.items())),
        "jobs": rows,
        "caveat": caveat,
    }


def _names(checks: dict[str, str], state: str) -> str:
    return ", ".join(name for name, value in checks.items() if value == state)


def _line(row: Record) -> str:
    head = " ".join(
        str(part) for part in (f"row {row['row']}", row["job"], row["status"]) if part
    )
    checks = row["checks"]
    skipped = _names(checks, "not_requested") or "none"
    if row["status"] == "absent":
        return f"{head}: no job result; not analyzed; not run: {_names(checks, 'not_run')}; not requested: {skipped}"
    if row["status"] != "closed":
        return (
            f"{head}: not analyzed; job result only, trial directories not inventoried "
            f"({row['registered']} slots registered); not run: {_names(checks, 'not_run')}; "
            f"not requested: {skipped}"
        )
    counts = f"{row['retained']} of {row['registered']} slots retained"
    found = ", ".join(f"{code}={count}" for code, count in row["findings"].items())
    verdict = f"needs attention: {found}" if found else "no findings"
    return f"{head}, {verdict} ({counts}); requested: {_names(checks, 'requested')}; not requested: {skipped}"


def text(found: Record, pinned: bool) -> str:
    counts = found["counts"]
    first = (
        f"Attention {found['status']}: {counts['jobs']} jobs, {counts['closed']} closed "
        f"({counts['needs_attention']} need attention), {counts['open']} open, {counts['unknown']} unknown, "
        f"{counts['absent']} absent."
    )
    pin = "pinned." if pinned else "unpinned; cannot prove that no row was deleted."
    lines = [first, f"Spool {found['spool_sha256']}, {pin}"]
    lines.extend(_line(row) for row in found["jobs"])
    lines.append(found["caveat"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 4 or any(len(os.fsencode(arg)) > MAX_PATH for arg in args):
        print("Invalid attention arguments.", file=sys.stderr)
        return 2
    parser = Parser(
        prog=PROG,
        allow_abbrev=False,
        description=__doc__,
        epilog=(
            "Exit 0: every job is closed without findings. Exit 1: at least one closed job needs attention, "
            "whatever the other jobs are. Exit 2: input rejected, spool digest mismatch, or input changed during "
            "inspection; no report. Exit 3: no closed job needs attention, but at least one job is open, unknown or "
            "absent; none of these is ever 0."
        ),
    )
    parser.add_argument(
        "spool",
        metavar="SPOOL.json",
        help=f"a {SPOOL} manifest; relative paths resolve from its own directory",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the machine-readable report"
    )
    parser.add_argument(
        "--expected-spool",
        metavar="sha256:HEX",
        help="reject unless the raw spool bytes have this digest; checked before any job is read",
    )
    opts = parser.parse_args(args)
    try:
        entries, digest = load_spool(opts.spool, opts.expected_spool)
        found = report(entries) | {"spool_sha256": digest}
    except Malformed as error:
        print(f"Attention input rejected: {error}.", file=sys.stderr)
        return 2
    except OSError:
        print("Cannot read local attention data.", file=sys.stderr)
        return 2
    print(
        json.dumps(found, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if opts.json
        else text(found, opts.expected_spool is not None)
    )
    return EXIT[found["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
