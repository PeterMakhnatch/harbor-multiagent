from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .core import BOUNDARIES, Finding, check_choice, summarize
from .job_members import members
from .trial import MissingJournal, UnsealedDecision, read_native_result, read_trial

SCHEMA = "harbor.handoff-job/v1"
SEALED = ("verifier", "handoffs.jsonl")
DECISION = ("verifier", "decision.json")
WORLD = ("artifacts", "logs", "world", "handoffs.jsonl")
RESULT = ("result.json",)
SEALED_GRADE_STATUS = {
    "valid": "usable",
    "invalid": "invalid_native_grade",
    "unknown": "ungraded_native",
    "unavailable": "no_native_result",
}
STATUSES = (
    "usable",
    "partial_world",
    "missing_journal",
    "unsealed_decision",
    "invalid_evidence",
    "no_native_result",
    "ungraded_native",
    "invalid_native_grade",
    "duplicate_identity",
    "ambiguous_group",
)


def _linked(child: Path, parts: tuple[str, ...]) -> bool:
    current = child
    for part in parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _row(name: str, group: str, status: str) -> dict[str, Any]:
    return {
        "directory": name,
        "group": group,
        "status": status,
        "evidence_status": status,
        "conflicts": [],
        "world_run": None,
        "native_trial": None,
        "log": None,
        "native_grade_status": None,
        "reward": None,
        "decision": None,
        "findings": [],
    }


def _failed(child: Path, group: str, status: str) -> dict[str, Any]:
    row = _row(child.name, group, status)
    result = child.joinpath(*RESULT)
    if _linked(child, RESULT):
        row["native_grade_status"] = "unreadable"
        return row
    if not result.exists():
        row["native_grade_status"] = "unavailable"
        return row
    try:
        native_id, grade_status, grade = read_native_result(child)
    except (OSError, ValueError):
        row["native_grade_status"] = "unreadable"
        return row
    row.update(native_trial=native_id, native_grade_status=grade_status, reward=grade)
    return row


def _read_child(
    child: Path, group: str, boundary: str
) -> tuple[dict[str, Any], list[Finding]]:
    name = child.name
    if any(_linked(child, parts) for parts in (SEALED, DECISION, WORLD, RESULT)):
        return _failed(child, group, "invalid_evidence"), []
    try:
        found, link = read_trial(child, boundary=boundary)
    except UnsealedDecision:
        return _failed(child, group, "unsealed_decision"), []
    except MissingJournal:
        return _failed(child, group, "missing_journal"), []
    except (OSError, ValueError):
        return _failed(child, group, "invalid_evidence"), []
    if link["log"] == "verifier":
        status = SEALED_GRADE_STATUS[str(link["native_grade_status"])]
    else:
        status = "partial_world"
    row = _row(name, group, status)
    row.update(
        world_run=link["world_run"],
        native_trial=link["native_trial"],
        log=link["log"],
        native_grade_status=link["native_grade_status"],
        reward=found[0].reward,
        decision=link["decision"],
        findings=[asdict(finding) for finding in found],
    )
    return row, found


def _mark_conflicts(rows: list[dict[str, Any]], oversubscribed: set[str]) -> None:
    claims: dict[str, Counter[str]] = {
        key: Counter(row[key] for row in rows if row[key] is not None)
        for key in ("native_trial", "world_run")
    }
    for row in rows:
        for key, counts in claims.items():
            if row[key] is not None and counts[row[key]] > 1:
                row["conflicts"].append(key)
        if row["group"] in oversubscribed:
            row["conflicts"].append("group_oversubscribed")
            row["status"] = "ambiguous_group"
        elif row["conflicts"]:
            row["status"] = "duplicate_identity"


def _group(digest: str, planned: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if planned < 1:
        raise ValueError("planned group count must be positive")
    candidates = len(rows)
    matched = min(candidates, planned)
    usable = sum(row["status"] == "usable" for row in rows)
    graded = sum(
        row["native_grade_status"] == "valid" and not row["conflicts"] for row in rows
    )
    statuses = Counter(row["status"] for row in rows)
    group = {
        "group": digest,
        "planned": planned,
        "candidates": candidates,
        "matched": matched,
        "missing": planned - matched,
        "surplus": candidates - matched,
        "usable": usable,
        "valid_native_grades": graded,
        "ambiguous": candidates > planned,
        "statuses": {status: statuses[status] for status in STATUSES},
    }
    if usable > graded or graded > matched or (group["ambiguous"] and graded):
        raise ValueError("group usable count exceeds attributable members")
    return group


def _totals(
    planned: dict[str, int], groups: list[dict[str, Any]], usable: int
) -> dict[str, int]:
    registered = sum(planned.values())
    matched = sum(group["matched"] for group in groups)
    graded = sum(group["valid_native_grades"] for group in groups)
    missing = registered - matched
    if not 0 <= usable <= graded <= matched <= registered or missing < 0:
        raise ValueError("job counts break slot accounting")
    if missing != sum(group["missing"] for group in groups):
        raise ValueError("group missing counts disagree with the job")
    return {
        "registered_slots": registered,
        "matched_trials": matched,
        "valid_native_grades": graded,
        "missing_slots": missing,
        "usable_trials": usable,
    }


def inspect_job(path: Path, *, boundary: str) -> dict[str, Any]:
    check_choice(boundary, BOUNDARIES, "boundary")
    job_id, planned, eligible, unassigned = members(path)
    rows: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = {digest: [] for digest in planned}
    findings: dict[str, list[Finding]] = {}
    for child, digest in eligible:
        row, found = _read_child(child, digest, boundary)
        by_group[digest].append(row)
        rows.append(row)
        findings[child.name] = found
    oversubscribed = {
        digest for digest, count in planned.items() if len(by_group[digest]) > count
    }
    _mark_conflicts(rows, oversubscribed)
    groups = [
        _group(digest, planned[digest], by_group[digest]) for digest in sorted(planned)
    ]
    usable_rows = [row for row in rows if row["status"] == "usable"]
    usable = [finding for row in usable_rows for finding in findings[row["directory"]]]
    return {
        "schema": SCHEMA,
        "job_id": job_id,
        "boundary": boundary,
        **_totals(planned, groups, len(usable_rows)),
        "groups": groups,
        "trials": rows,
        "unassigned": unassigned,
        "summary": summarize(usable),
    }
