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
    from .input_witness import Witness

SCHEMA = "seatlab.workbench.input-cohort/v1"
GRADES = ("zero", "one", "other", "invalid", "unknown")
CAVEAT = (
    "Only retained finals. Grades require a registered full lock and corroborating saved result inputs. "
    "Without a START archive, different locks with the same result view cannot be separated. With an archive, "
    "the host START must also match the job, slot, execution, full lock and final configuration binding. "
    "A START without a final is not a failure or a grade. These are local consistency checks, not signatures, "
    "proof of deployed bytes or messages, or a causal effect."
)


class Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        self.exit(2, "Invalid cohort arguments. Use cohort --help.\n")


def _match(job: str, final: Final, witness: Witness | None) -> str:
    if witness is None:
        return "missing"
    if witness.job != job or witness.trial != final.execution:
        return "conflicts"
    if final.slot is not None and final.slot != witness.slot:
        return "conflicts"
    if final.cell is not None and final.cell.key != witness.input:
        return "conflicts"
    if final.binding is not None and final.binding != witness.binding:
        return "conflicts"
    if final.agrees is False:
        return "conflicts"
    if (
        final.slot is None
        or final.cell is None
        or final.binding is None
        or final.agrees is None
    ):
        return "unverifiable"
    return "matched"


def _start_counts(
    evidence: Evidence, starts: dict[str, Witness] | None
) -> tuple[dict[str, str] | None, Record | None]:
    if starts is None:
        return None, None
    marks = {
        f.execution: _match(evidence.job, f, starts.get(f.execution))
        for f in evidence.finals
    }
    counts = Counter(marks.values())
    return marks, {
        "rows": len(starts),
        **{
            key: counts[key]
            for key in ("matched", "missing", "conflicts", "unverifiable")
        },
        "without_final": len(starts.keys() - marks.keys()),
    }


def _group(
    key: str,
    count: int,
    finals: list[Final],
    shared: bool,
    marks: dict[str, str] | None,
) -> Record:
    retained = len(finals)
    missing, excess = max(0, count - retained), max(0, retained - count)
    conflicts = sum(f.agrees is False for f in finals)
    unknown = sum(f.agrees is None for f in finals)
    starts = None if marks is None else Counter(marks[f.execution] for f in finals)
    supported = count > 0 and not (excess or conflicts or (shared and starts is None))
    counts: Counter[str] = Counter()
    if supported and (starts is None or not starts["conflicts"]):
        counts.update(
            f.grade
            for f in finals
            if f.agrees is True and (marks is None or marks[f.execution] == "matched")
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
        "starts": None
        if starts is None
        else {
            tag: starts[tag]
            for tag in ("matched", "missing", "conflicts", "unverifiable")
        },
        "unattributed": retained - attributed,
        "grades": {grade: counts[grade] for grade in GRADES} if attributed else None,
    }


def _pair(
    wanted: tuple[str, str] | None, registered: dict[str, str], groups: list[Record]
) -> Record | None:
    if wanted is None:
        return None
    left, right = wanted
    if left not in registered or right not in registered:
        status = "not_registered"
    elif len(registered) != 2:
        status = "outside_pair"
    elif registered[left] == registered[right]:
        resolved = all(
            g["starts"] is not None
            and g["registered"] > 0
            and g["starts"]["matched"] == g["retained"] == g["registered"]
            and g["unattributed"] == 0
            for g in groups
        )
        status = "exact_with_start_receipts" if resolved else "indistinguishable"
    else:
        status = "exact"
    return {"left": left, "right": right, "status": status}


def summarize(
    evidence: Evidence,
    pair: tuple[str, str] | None = None,
    *,
    starts: dict[str, Witness] | None = None,
) -> Record:
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
    marks, archive = _start_counts(evidence, starts)
    view_keys = Counter(all_cells.values())
    groups = [
        _group(k, counts[k], finals[k], view_keys[all_cells[k]] > 1, marks)
        for k in sorted(all_cells)
    ]
    requested = _pair(pair, registered, groups)
    refused = requested is not None and requested["status"] in (
        "not_registered",
        "outside_pair",
    )
    inconsistent = (
        refused
        or (archive is not None and archive["conflicts"] > 0)
        or any(g["excess_lock_copies"] or g["result_input_conflicts"] for g in groups)
    )
    unresolved = any(g["missing_lock_copies"] or g["unattributed"] for g in groups)
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
        "start_archive": archive,
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
    if (archive := report["start_archive"]) is not None:
        lines.append(
            f"Host START receipts: {archive['matched']} matched, {archive['missing']} missing, {archive['conflicts']} conflicts, {archive['unverifiable']} unverifiable; {archive['without_final']} without a retained final."
        )
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
    if len(args) > 9 or any(len(os.fsencode(a)) > 4096 for a in args):
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
    parser.add_argument(
        "--starts",
        type=Path,
        metavar="PRIVATE_ARCHIVE",
        help="Require write-once host START receipts; never infer one from an END result.",
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
        from .input_witness import read_starts

        evidence = read_job(
            opts.job, opts.expected_job_lock, bind_starts=opts.starts is not None
        )
        starts = None if opts.starts is None else read_starts(opts.starts, evidence.job)
        report = summarize(evidence, pair, starts=starts)
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
