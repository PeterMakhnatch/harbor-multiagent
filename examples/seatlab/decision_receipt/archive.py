"""Compare what saved shipping releases can establish about the relief seat's decision."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...native_evidence import Invalid
from . import archive_current, archive_inflight, archive_legacy

SCHEMA = "harbor.shipping.decision-evidence/v1"
WORLD_STATES = {"stock": ("committed", "dropped"), "inflight": ("settles", "fails")}
SELECTION = {
    "stock-controls": "published native stock controls; physical outcomes, seat exits not established",
    "inflight-cuts": "registered scripted controls; public native claims and replayed frozen cut",
    "two-worlds": "published native qualification for the two-world scripted controls",
    "disconnected": "published worker qualification, not raw inbox events; a missing ticket and an unspecified receipt remain unknown",
    "supplied-cuts": "operator-selected frozen world cuts only; no enrollment, native seat identities or team completion established",
}
KEYS = {"world", "axis_state", "decision", "inbox", "citation", "reward", "status"}
REFS = {"original", "draft"}
LIMITS = (
    "Reports are separate releases, not independent replications. Do not add their team counts.",
    "Stock prior state occurs before the seats act. In-flight supplier fate occurs after they stop.",
    "An empty inbox response is not a returned reference. A returned reference is not a citation.",
    "Only a returned reference before an accepted decision creates a citation opportunity. Unrecorded is not zero.",
    "A citation records the accepted action, not comprehension, intent or a causal effect on reward.",
)


def _check(condition: bool) -> None:
    if not condition:
        raise ValueError("shipping evidence has inconsistent fields")


def classify(row: dict[str, object], *, current: bool) -> tuple[str, str, str]:
    _check(set(row) == KEYS)
    world, state = row["world"], row["axis_state"]
    _check(isinstance(world, str) and isinstance(state, str))
    assert isinstance(world, str) and isinstance(state, str)
    _check(world in WORLD_STATES and state in WORLD_STATES[world])
    decision, inbox, citation = row["decision"], row["inbox"], row["citation"]
    if row["status"] == "missing":
        _check(
            not current
            and (decision, inbox, citation, row["reward"])
            == ("unrecorded", "unrecorded", "unrecorded", None)
        )
        return world, state, "missing_result"
    _check(
        row["status"] == "observed"
        and type(row["reward"]) is int
        and row["reward"] in (0, 1)
    )
    _check(decision in ("accepted", "absent", "unrecorded"))
    _check(inbox in ("absent", "empty", "original", "draft", "unrecorded"))
    _check(
        citation in ("at_decision", "not_cited", "not_applicable")
        if current
        else citation == "unrecorded"
    )
    if decision != "accepted":
        _check(
            inbox in ("unrecorded", "absent")
            and citation in ("not_applicable", "unrecorded")
        )
        return (
            world,
            state,
            "no_decision" if decision == "absent" else "decision_unrecorded",
        )
    if inbox not in REFS:
        _check(citation in ("not_applicable", "unrecorded"))
        return (
            world,
            state,
            "reference_unrecorded"
            if inbox == "unrecorded"
            else "decided_without_reference",
        )
    _check(citation != "not_applicable")
    return (
        world,
        state,
        {
            "unrecorded": "returned_citation_unrecorded",
            "at_decision": "cited_at_decision",
            "not_cited": "returned_not_cited",
        }[str(citation)],
    )


def release(name: str, rows: list[dict[str, object]]) -> dict[str, Any]:
    current = name == "supplied-cuts"
    _check(name in SELECTION and 0 < len(rows) <= 256)
    counts: Counter[str] = Counter()
    bins: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for row in rows:
        world, state, observation = classify(row, current=current)
        cell = bins[world, state, observation]
        cell[0] += 1
        reward = row["reward"]
        if type(reward) is int:
            cell[1] += 1
            cell[2] += reward
        counts[observation] += 1
        if row["decision"] == "accepted":
            counts["decisions"] += 1
            if row["inbox"] == "empty":
                counts["empty"] += 1
            if row["inbox"] in REFS:
                counts["references"] += 1
    checked = counts["cited_at_decision"] + counts["returned_not_cited"]
    assert checked + counts["returned_citation_unrecorded"] == counts["references"]
    return {
        "release": name,
        "source": SELECTION[name],
        "registered": None if current else len(rows),
        "present": len(rows) - counts["missing_result"],
        "missing": None if current else counts["missing_result"],
        "accepted_decisions": counts["decisions"],
        "decision_unrecorded": counts["decision_unrecorded"],
        "no_decision": counts["no_decision"],
        "inbox_returns_before_decision": counts["references"] + counts["empty"],
        "inbox_unrecorded_at_decision": counts["reference_unrecorded"],
        "empty_returns_before_decision": counts["empty"],
        "references_before_decision": counts["references"],
        "citation_opportunities_measured": checked,
        "cited_at_decision": counts["cited_at_decision"] if checked else None,
        "strata": [
            {
                "world": world,
                "axis": "prior" if world == "stock" else "fate",
                "state": state,
                "observation": observation,
                "rows": n,
                "known_rewards": k,
                "reward_one": wins if k else None,
            }
            for (world, state, observation), (n, k, wins) in sorted(bins.items())
        ],
    }


def summarize(cuts: list[Path]) -> dict[str, Any]:
    cohorts = archive_legacy.collect()
    _check(set(cohorts) == {"stock-controls", "two-worlds", "disconnected"})
    cohorts["inflight-cuts"] = archive_inflight.collect()
    if cuts:
        cohorts["supplied-cuts"] = archive_current.collect(cuts)
    return {
        "schema": SCHEMA,
        "releases": [
            release(name, cohorts[name]) for name in SELECTION if name in cohorts
        ],
        "limits": list(LIMITS),
    }


def render(report: dict[str, Any]) -> str:
    lines = []
    for group in report["releases"]:
        kept = group["present"]
        source = (
            f"{kept} supplied cuts"
            if group["registered"] is None
            else f"{kept}/{group['registered']} registered records present"
        )
        lines.append(f"{group['release']}: {source}")
        cite = (
            "unmeasured"
            if group["cited_at_decision"] is None
            else f"{group['cited_at_decision']}/{group['citation_opportunities_measured']} measured opportunities"
        )
        lines.append(
            f"  Recorded inbox returns before a decision: {group['inbox_returns_before_decision']}; "
            f"empty: {group['empty_returns_before_decision']}; reference: {group['references_before_decision']}. "
            f"Inbox unrecorded at {group['inbox_unrecorded_at_decision']} accepted decisions. Citation: {cite}."
        )
        for row in group["strata"]:
            grade = (
                "no recorded reward"
                if row["known_rewards"] == 0
                else f"reward 1: {row['reward_one']}/{row['known_rewards']}"
            )
            lines.append(
                f"  {row['world']} {row['axis']}={row['state']}  {row['observation']}: {row['rows']} ({grade})"
            )
    return "\n".join([*lines, *report["limits"]])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--receipt-cut",
        action="append",
        type=Path,
        default=[],
        metavar="CUT",
        help="also inspect a saved decision-receipt cut, without assigning it to any team (repeatable)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = summarize(args.receipt_cut)
    except (Invalid, OSError, ValueError):
        print(
            "Shipping evidence refused: check the published source files and requested cuts.",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(report, indent=2, allow_nan=False) if args.json else render(report)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
