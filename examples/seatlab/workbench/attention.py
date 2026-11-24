"""Morning attention check over a spool of saved native jobs. Never starts a process, model or job."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn, override

from .job_stream import RESULT
from .native_account import _root, collect
from .seat_stream import MIB, Malformed, Record, _read, _require, _strict_json

SPOOL = "seatlab.workbench.spool/v1"
SCHEMA = "seatlab.workbench.attention/v1"
PROG = "python3.12 -BS -m examples.seatlab.workbench attention"
MAX_SPOOL_BYTES = MIB
MAX_JOBS = 32
MAX_PATH = 4096
MAX_FINDINGS = 2**53 - 1
CAPABILITIES = ("seats", "handoffs", "accounts", "witness")
ROW_KEYS = frozenset({"job", *CAPABILITIES})
STATUSES = {True: "closed", False: "open", None: "unknown"}
EXIT = {"clear": 0, "needs_attention": 1, "outstanding": 3}
CAVEAT = (
    "Snapshot of saved native jobs; not an atomic file system snapshot. Only closed jobs are inventoried and "
    "analyzed. Open and unknown jobs are read from the job result alone; their trial directories are not "
    "inventoried, so retained is null. open and unknown are distinct states of this read, not conclusions: they are "
    "never clear and never findings. A capability marked not_requested was not checked. Findings count local "
    "evidence only."
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


def spool(path: str) -> list[Entry]:
    data = _read(path, MAX_SPOOL_BYTES, "spool")
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
            for key in ("job", *CAPABILITIES)
        }
        job = os.path.normpath(str(entry["job"]))
        _require(job not in seen, f"{label} repeats a job path")
        seen.add(job)
        entries.append(entry)
    assert 1 <= len(entries) <= MAX_JOBS
    return entries


def _envelope(job: str) -> tuple[int, ...]:
    try:
        directory = os.lstat(job)
        result = os.lstat(os.path.join(job, RESULT))
    except (OSError, ValueError) as error:
        raise Malformed("job result is missing or unreadable") from error
    return (
        directory.st_dev,
        directory.st_ino,
        directory.st_mtime_ns,
        directory.st_ctime_ns,
        result.st_dev,
        result.st_ino,
        result.st_mode,
        result.st_size,
        result.st_mtime_ns,
        result.st_ctime_ns,
    )


def _optional(value: str | None) -> Path | None:
    return None if value is None else Path(value)


def _findings(job: str, packet: Record, entry: Entry) -> dict[str, int]:
    from . import attention_details

    assert packet["job_closed"] is True
    found = attention_details.analyze(
        Path(job),
        packet,
        seats=_optional(entry["seats"]),
        handoffs=_optional(entry["handoffs"]),
        accounts=_optional(entry["accounts"]),
        witness=_optional(entry["witness"]),
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
    return checks


def _inspect(entry: Entry, seen: set[str]) -> Record:
    job = entry["job"]
    assert job is not None
    before = _envelope(job)
    root = _root(job)
    scope, registered, retries, closed, _ = root
    _require(scope not in seen, "names a native job already in the spool")
    seen.add(scope)
    status = STATUSES[closed]
    row: Record = {
        "job": scope,
        "status": status,
        "registered": registered,
        "retained": None,
        "retries": retries,
        "checks": _checks(entry, status == "closed"),
        "findings": {},
        "outcome": "not_analyzed",
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
            rows.append(_inspect(entry, seen))
        except Malformed as error:
            raise Malformed(f"spool row {index} {error}") from error
    rows.sort(key=lambda row: row["job"])
    actions: dict[str, int] = {}
    for row in rows:
        for code, count in row["findings"].items():
            actions[code] = actions.get(code, 0) + count
    counts = {status: 0 for status in STATUSES.values()}
    for row in rows:
        counts[row["status"]] += 1
    attention = sum(row["outcome"] == "needs_attention" for row in rows)
    if attention:
        state = "needs_attention"
    else:
        state = "outstanding" if counts["open"] + counts["unknown"] else "clear"
    return {
        "schema": SCHEMA,
        "status": state,
        "counts": {"jobs": len(rows), "needs_attention": attention, **counts},
        "actions": dict(sorted(actions.items())),
        "jobs": rows,
        "caveat": CAVEAT,
    }


def _names(checks: dict[str, str], state: str) -> str:
    return ", ".join(name for name, value in checks.items() if value == state)


def _line(row: Record) -> str:
    head = f"{row['job']} {row['status']}"
    checks = row["checks"]
    skipped = _names(checks, "not_requested") or "none"
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


def text(found: Record) -> str:
    counts = found["counts"]
    first = (
        f"Attention {found['status']}: {counts['jobs']} jobs, {counts['closed']} closed "
        f"({counts['needs_attention']} need attention), {counts['open']} open, {counts['unknown']} unknown."
    )
    lines = [first]
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
            "whatever the other jobs are. Exit 2: input rejected or changed during inspection; no report. "
            "Exit 3: no closed job needs attention, but at least one job is open or unknown; unknown is never 0."
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
    opts = parser.parse_args(args)
    try:
        found = report(spool(opts.spool))
    except Malformed as error:
        print(f"Attention input rejected: {error}.", file=sys.stderr)
        return 2
    except OSError:
        print("Cannot read local attention data.", file=sys.stderr)
        return 2
    print(
        json.dumps(found, allow_nan=False, sort_keys=True, separators=(",", ":"))
        if opts.json
        else text(found)
    )
    return EXIT[found["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
