import argparse
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ...native_evidence import Incomplete, Invalid
from . import control_inputs, control_outcomes

SCHEMA = "harbor.ship-decision-receipt.qualification/v1"
STATES = (
    "missing",
    "incomplete",
    "conflict",
    "seat_unqualified",
    "verified",
    "mismatch",
)
EXITS = {
    "qualified": 0,
    "inconsistent": 1,
    "contradicted": 1,
    "seat_unqualified": 1,
    "awaiting_evidence": 3,
}


def _unexpected(root: Path, tickets: set[str]) -> bool:
    try:
        mode = root.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise ValueError("invalid trial root")
    with os.scandir(root) as entries:
        return any(entry.name not in tickets for entry in entries)


def _reused(ids: list[str | None]) -> set[str]:
    return {
        value
        for value, count in Counter(i for i in ids if i is not None).items()
        if count > 1
    }


def _verdict(counts: Counter[str], outside: bool) -> str:
    if outside or counts["conflict"]:
        return "inconsistent"
    if counts["mismatch"]:
        return "contradicted"
    if counts["seat_unqualified"]:
        return "seat_unqualified"
    if counts["verified"] == 4:
        return "qualified"
    return "awaiting_evidence"


def summarize(enrollment: control_inputs.Enrollment) -> dict[str, Any]:
    cases = control_inputs.cases()
    assert len(cases) == len(enrollment.requests) == 4
    assert {c["ticket"] for c in cases} == set(enrollment.requests)
    outside = _unexpected(enrollment.trials, set(enrollment.requests))
    results = [
        control_outcomes.inspect_case(
            enrollment.trials / c["ticket"],
            enrollment.requests[c["ticket"]],
            enrollment.task,
            lock_sha256=enrollment.locks[c["ticket"]],
            client_sha256=enrollment.client_sha256,
            fate=c["fate"],
            cite=c["cite"],
        )
        for c in cases
    ]
    reused_trials = _reused([r.trial for r in results])
    reused_worlds = _reused([r.world for r in results])
    counts: Counter[str] = Counter()
    rows = []
    for c, r in zip(cases, results, strict=True):
        duplicate = r.trial in reused_trials or r.world in reused_worlds
        status = "conflict" if duplicate else r.status
        assert status in STATES
        counts[status] += 1
        observed = status in ("verified", "mismatch")
        rows.append(
            {
                "ticket": c["ticket"],
                "fate": c["fate"],
                "request_citation": c["cite"],
                "status": status,
                "cited_at_decision": r.citation if observed else None,
                "citation_status": r.citation_status if observed else None,
                "reason": "reused trial or world execution" if duplicate else r.reason,
                "reward": r.grade if observed else None,
                "action": r.action if observed else None,
                "outcome": r.outcome if observed else None,
            }
        )
    assert sum(counts.values()) == len(rows) == 4
    return {
        "schema": SCHEMA,
        "enrollment": enrollment.pin,
        "task_tree": enrollment.tree,
        "status": _verdict(counts, outside),
        "expected_controls": 4,
        "unexpected_entries": outside,
        "reused_trial_ids": len(reused_trials),
        "reused_world_ids": len(reused_worlds),
        "counts": {key: counts[key] for key in STATES},
        "cases": rows,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"{report['status']}: {report['counts']['verified']}/4 controls verified",
        "fixture\tcite requested\tstatus\tcitation observed\taction\treward\toutcome\treason",
    ]
    for row in report["cases"]:
        data = (
            row["fate"],
            "yes" if row["request_citation"] else "no",
            row["status"],
            row["citation_status"],
            row["action"],
            row["reward"],
            row["outcome"],
            row["reason"],
        )
        lines.append("\t".join("-" if value is None else str(value) for value in data))
    if report["unexpected_entries"]:
        lines.append(
            "The private trial root has entries outside the four enrolled controls."
        )
    if report["reused_trial_ids"] or report["reused_world_ids"]:
        lines.append("At least two controls share a native trial or world execution.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Read four native controls without starting any.",
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--expected-enrollment", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        enrollment = control_inputs.load(
            args.task, args.workspace, args.expected_enrollment
        )
        report = summarize(enrollment)
    except (OSError, ValueError, Invalid, Incomplete, SystemExit):
        print(
            "Qualification refused: enrollment, pinned sources or trial storage could not be verified.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report))
    return EXITS[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
