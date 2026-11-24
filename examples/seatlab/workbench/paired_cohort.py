from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from .cohort import GRADES, summarize
from .input_evidence import MAX_TRIALS
from .isolate import PIN
from .seat_stream import Record

if TYPE_CHECKING:
    from .input_evidence import Evidence
    from .input_witness import Witness

SCHEMA = "seatlab.workbench.paired-cohort/v1"
ARMS = ("left", "right")
CAVEAT = (
    "Two independent native jobs, each planned to register only its one expected full trial-lock key. "
    "Grades follow the single-job cohort rules per arm and are withheld for both arms on any design, identity, "
    "registration or consistency failure. When the two jobs hold different keys with the same result view, a "
    "grade needs a matching host START receipt. A missing final or START is not a grade. This is a local "
    "coverage check, not a scheduler, a signature, proof of deployed bytes or messages, or a causal effect."
)

SHAPE = (
    "a paired cohort needs two evidence records, two keys and optional two START maps"
)

COPIES = "copies_out_of_range"
KEY_SHAPE = "input_key_not_canonical"
SAME_KEY = "input_keys_not_distinct"
SAME_JOB = "job_ids_not_distinct"
SAME_LOCK = "job_locks_not_distinct"
SHARED_EXECUTION = "execution_shared_across_jobs"
SHARED_VIEW = "shared_result_view_without_starts"
OTHER_INPUT = "registers_other_input"
COPIES_DIFFER = "registered_copies_differ"
START_OUTSIDE = "start_outside_arm"
EXCESS = "excess_finals"
RESULT_CONFLICT = "result_input_conflict"
START_CONFLICT = "start_conflict"
NOT_CLOSED = "job_not_closed"
MISSING = "finals_missing"
NO_LOCK = "finals_without_lock"
PENDING = "locks_without_result"
UNATTRIBUTED = "finals_unattributed"

_ARM_REFUSING = (
    OTHER_INPUT,
    COPIES_DIFFER,
    START_OUTSIDE,
    EXCESS,
    RESULT_CONFLICT,
    START_CONFLICT,
)
_ARM_INCOMPLETE = (NOT_CLOSED, MISSING, PENDING, NO_LOCK, UNATTRIBUTED)
REFUSING = frozenset(
    (COPIES, KEY_SHAPE, SAME_KEY, SAME_JOB, SAME_LOCK, SHARED_EXECUTION)
    + tuple(f"{arm}_{code}" for arm in ARMS for code in _ARM_REFUSING)
)
INCOMPLETE = frozenset(
    (SHARED_VIEW,) + tuple(f"{arm}_{code}" for arm in ARMS for code in _ARM_INCOMPLETE)
)


def _planned(copies: object) -> bool:
    return type(copies) is int and 1 <= copies <= MAX_TRIALS


def _shared(evidence: tuple[Evidence, Evidence]) -> frozenset[str]:
    keys_by_view: defaultdict[str, set[str]] = defaultdict(set)
    for side in evidence:
        for item in side.registered:
            keys_by_view[item.view].add(item.key)
        for final in side.finals:
            if final.cell is not None:
                keys_by_view[final.cell.view].add(final.cell.key)
    return frozenset(view for view, keys in keys_by_view.items() if len(keys) > 1)


def _executions(evidence: Evidence, starts: dict[str, Witness] | None) -> set[str]:
    found = {final.execution for final in evidence.finals}
    if starts is None:
        return found
    return found | starts.keys() | {witness.trial for witness in starts.values()}


def _design(
    evidence: tuple[Evidence, Evidence],
    keys: tuple[str, str],
    copies: object,
    starts: tuple[dict[str, Witness] | None, dict[str, Witness] | None],
) -> list[str]:
    planned = _planned(copies)
    left = _executions(evidence[0], starts[0])
    right = _executions(evidence[1], starts[1])
    found = [
        code
        for code, failed in (
            (COPIES, not planned),
            (KEY_SHAPE, not all(PIN.fullmatch(key) for key in keys)),
            (SAME_KEY, keys[0] == keys[1]),
            (SAME_JOB, evidence[0].job == evidence[1].job),
            (SAME_LOCK, evidence[0].lock_digest == evidence[1].lock_digest),
            (SHARED_EXECUTION, bool(left & right)),
        )
        if failed
    ]
    for arm, side, key, start in zip(ARMS, evidence, keys, starts, strict=True):
        registered = Counter(item.key for item in side.registered)
        outside = start is not None and any(
            w.job != side.job or w.input != key for w in start.values()
        )
        found += [
            f"{arm}_{code}"
            for code, failed in (
                (OTHER_INPUT, bool(registered.keys() - {key})),
                (COPIES_DIFFER, planned and registered[key] != copies),
                (START_OUTSIDE, outside),
            )
            if failed
        ]
    return found


def _native(arm: str, report: Record) -> list[str]:
    groups = report["groups"]
    archive = report["start_archive"]
    missing = report["registered"] > report["retained"]
    found = [
        f"{arm}_{code}"
        for code, failed in (
            (EXCESS, any(g["excess_lock_copies"] for g in groups)),
            (RESULT_CONFLICT, any(g["result_input_conflicts"] for g in groups)),
            (START_CONFLICT, archive is not None and archive["conflicts"] > 0),
            (NOT_CLOSED, report["job_closed"] is not True),
            (MISSING, missing),
            (PENDING, report["locks_without_result"] > 0),
            (NO_LOCK, report["finals_without_lock"] > 0),
            (UNATTRIBUTED, report["unattributed"] > 0),
        )
        if failed
    ]
    assert (report["status"] == "covered") == (not found)
    assert (report["status"] == "inconsistent") == bool(REFUSING.intersection(found))
    return found


def _arm(evidence: Evidence, key: str, report: Record, refused: bool) -> Record:
    group = next((g for g in report["groups"] if g["input"] == key), None)
    registered = Counter(item.key for item in evidence.registered)
    retained = len(evidence.finals)
    attributed: int | None = None
    unattributed: int | None = None
    grades: dict[str, int] | None = None
    if not refused:
        assert all(g["input"] == key for g in report["groups"])
        attributed = 0 if group is None else group["retained"] - group["unattributed"]
        unattributed = report["unattributed"]
        grades = None if group is None else group["grades"]
        assert attributed + unattributed == retained
        assert (grades is None) == (attributed == 0)
        assert grades is None or (
            tuple(grades) == GRADES and sum(grades.values()) == attributed
        )
    return {
        "input": key if PIN.fullmatch(key) else None,
        "job": evidence.job,
        "job_lock": evidence.lock_digest,
        "job_state": "unknown"
        if evidence.closed is None
        else "closed"
        if evidence.closed
        else "open",
        "job_reported_retries": evidence.retries,
        "registered": len(evidence.registered),
        "registered_input_copies": registered[key],
        "distinct_other_inputs": len(registered.keys() - {key}),
        "retained": retained,
        "finals_without_lock": report["finals_without_lock"],
        "locks_without_result": report["locks_without_result"],
        "missing_finals": max(0, len(evidence.registered) - retained),
        "missing_expected_lock_copies": 0
        if group is None
        else group["missing_lock_copies"],
        "shared_result_view": group is not None and group["shared_result_view"],
        "starts": report["start_archive"],
        "attributed": attributed,
        "unattributed": unattributed,
        "grades": grades,
    }


def combine(
    evidence: tuple[Evidence, Evidence],
    keys: tuple[str, str],
    copies: int,
    starts: tuple[dict[str, Witness], dict[str, Witness]] | None = None,
) -> Record:
    pairs = (evidence, keys) if starts is None else (evidence, keys, starts)
    if not all(type(part) is tuple and len(part) == 2 for part in pairs) or not all(
        type(key) is str for key in keys
    ):
        raise ValueError(SHAPE)
    if starts is not None and not all(type(side) is dict for side in starts):
        raise ValueError(SHAPE)
    sides = (None, None) if starts is None else starts
    shared = _shared(evidence)
    reports = tuple(
        summarize(side, starts=start, shared_views=shared)
        for side, start in zip(evidence, sides, strict=True)
    )
    reasons = _design(evidence, keys, copies, sides)
    for arm, report in zip(ARMS, reports, strict=True):
        reasons += _native(arm, report)
    if starts is None and any(
        g["shared_result_view"] and g["retained"]
        for report in reports
        for g in report["groups"]
    ):
        reasons.append(SHARED_VIEW)
    assert REFUSING.union(INCOMPLETE).issuperset(reasons)
    refused = bool(REFUSING.intersection(reasons))
    status = "inconsistent" if refused else "incomplete" if reasons else "covered"
    return {
        "schema": SCHEMA,
        "status": status,
        "copies_per_arm": copies if type(copies) is int else None,
        "reasons": sorted(set(reasons)),
        "arms": {
            arm: _arm(side, key, report, refused)
            for arm, side, key, report in zip(
                ARMS, evidence, keys, reports, strict=True
            )
        },
        "caveat": CAVEAT,
    }
