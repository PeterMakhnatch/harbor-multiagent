from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

from .isolate import PIN
from .seat_stream import Record

if TYPE_CHECKING:
    from .input_evidence import Evidence, Final
    from .input_keys import Cell

SCHEMA = "seatlab.workbench.input-cohort/v1"
GRADES = ("zero", "one", "other", "invalid", "unknown")
CAVEAT = (
    "Retained final directories only. A grade is grouped only when the saved lock matches a registered input "
    "and the result independently agrees on the task locator, ordered participants and explicit world settings. "
    "Two full locks with the same result view cannot be separated. No binding to deployed bytes, messages, "
    "earlier attempts, behavior or a causal effect. Matching hashes do not authenticate the records."
)


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid cohort arguments. Use cohort --help.\n")


def _group(key: str, count: int, finals: list[Final], shared: bool) -> Record:
    retained = len(finals)
    missing, excess = max(0, count - retained), max(0, retained - count)
    conflicts = sum(f.agrees is False for f in finals)
    unknown = sum(f.agrees is None for f in finals)
    supported = count > 0 and not (excess or shared or conflicts)
    counts = (
        Counter(f.grade for f in finals if f.agrees is True) if supported else Counter()
    )
    attributed = counts.total()
    assert 0 <= attributed <= min(count, retained)
    return {
        "input": key,
        "registered": count,
        "retained": retained,
        "missing_lock_copies": missing,
        "excess_lock_copies": excess,
        "shared_result_view": shared,
        "result_input_conflicts": conflicts,
        "unknown_result_inputs": unknown,
        "unattributed": retained - attributed,
        "grades": {grade: counts[grade] for grade in GRADES} if attributed else None,
    }


def _pair(wanted: tuple[str, str] | None, registered: dict[str, str]) -> Record | None:
    if wanted is None:
        return None
    left, right = wanted
    if left not in registered or right not in registered:
        status = "not_registered"
    elif len(registered) != 2:
        status = "outside_pair"
    elif registered[left] == registered[right]:
        status = "indistinguishable"
    else:
        status = "exact"
    return {"left": left, "right": right, "status": status}


def summarize(evidence: Evidence, pair: tuple[str, str] | None = None) -> Record:
    if pair is not None and (
        pair[0] == pair[1] or not all(PIN.fullmatch(k) for k in pair)
    ):
        raise ValueError("a pair needs two distinct full input keys")
    registered = {item.key: item.view for item in evidence.registered}
    counts = Counter(item.key for item in evidence.registered)
    all_cells: dict[str, str] = dict(registered)
    finals: dict[str, list[Final]] = defaultdict(list)
    no_lock = 0
    for final in evidence.finals:
        seen: Cell | None = final.cell
        if seen is None:
            no_lock += 1
            continue
        all_cells[seen.key] = seen.view
        finals[seen.key].append(final)
    view_keys = Counter(all_cells.values())
    groups = [
        _group(k, counts[k], finals[k], view_keys[all_cells[k]] > 1)
        for k in sorted(all_cells)
    ]
    requested = _pair(pair, registered)
    refused = requested is not None and requested["status"] in (
        "not_registered",
        "outside_pair",
    )
    inconsistent = refused or any(
        g["excess_lock_copies"] or g["result_input_conflicts"] for g in groups
    )
    unresolved = any(
        g["missing_lock_copies"] or g["shared_result_view"] or g["unattributed"]
        for g in groups
    )
    incomplete = (
        unresolved
        or no_lock
        or evidence.lock_without_result
        or evidence.closed is not True
    )
    status = (
        "inconsistent" if inconsistent else "incomplete" if incomplete else "covered"
    )
    return {
        "schema": SCHEMA,
        "job": evidence.job,
        "job_lock": evidence.lock_digest,
        "status": status,
        "job_closed": evidence.closed,
        "job_reported_retries": evidence.retries,
        "registered": len(evidence.registered),
        "distinct_registered_inputs": len(registered),
        "retained": len(evidence.finals),
        "finals_without_lock": no_lock,
        "locks_without_result": evidence.lock_without_result,
        "unattributed": no_lock + sum(g["unattributed"] for g in groups),
        "pair": requested,
        "groups": groups,
        "caveat": CAVEAT,
    }


def _text(report: Record) -> str:
    lines = [
        f"{report['status']}: {report['registered']} registered copies, {report['distinct_registered_inputs']} distinct inputs, {report['retained']} retained finals; {report['unattributed']} not attributed.",
        f"Job lock {report['job_lock']}. No final lock: {report['finals_without_lock']}. No result for a lock: {report['locks_without_result']}.",
    ]
    if report["pair"] is not None:
        lines.append(f"Requested two-input registration: {report['pair']['status']}.")
    for g in report["groups"]:
        grades = g["grades"]
        tally = (
            "grades withheld"
            if grades is None
            else ", ".join(f"{k}={v}" for k, v in grades.items())
        )
        lines.append(
            f"{g['input']}: {g['registered']} registered, {g['retained']} retained, {g['unattributed']} not attributed; {tally}."
        )
    lines.append(CAVEAT)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 7 or any(len(os.fsencode(a)) > 4096 for a in args):
        print("Invalid cohort arguments. Use cohort --help.", file=sys.stderr)
        return 2
    parser = Parser(
        prog="workbench cohort",
        allow_abbrev=False,
        description="Match registered native team inputs to retained final results. No job starts.",
    )
    parser.add_argument("job", type=Path, metavar="JOB")
    parser.add_argument(
        "--expected-job-lock",
        metavar="SHA256",
        help="Check the raw enrollment lock before reading final trial directories.",
    )
    parser.add_argument(
        "--pair",
        nargs=2,
        metavar=("LEFT_KEY", "RIGHT_KEY"),
        help="Require precisely these two full trial-lock keys in the registration.",
    )
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(args)
    pair = (opts.pair[0], opts.pair[1]) if opts.pair is not None else None
    if (
        opts.expected_job_lock is not None and not PIN.fullmatch(opts.expected_job_lock)
    ) or (
        pair is not None
        and (pair[0] == pair[1] or not all(PIN.fullmatch(k) for k in pair))
    ):
        print(
            "Use distinct input keys and full lowercase sha256: digests.",
            file=sys.stderr,
        )
        return 2
    try:
        from .input_evidence import read_job

        report = summarize(read_job(opts.job, opts.expected_job_lock), pair)
    except ImportError:
        print(
            "Input coverage needs the installed Harbor fork. Do not use -S.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError, RecursionError):
        print(
            "Cannot verify this job, its enrollment pin, or its retained files. No input report.",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(report, sort_keys=True, separators=(",", ":"))
        if opts.json
        else _text(report)
    )
    return {"covered": 0, "inconsistent": 1, "incomplete": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
