import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..native_study import as_record
from ..ship_inflight import inflight_verify as verify
from ..ship_inflight.replay import (
    MAX_PACK_BYTES,
    PLACEHOLDER_RUN,
    REGISTERED,
    load_source,
    parse_rows,
    read_bounded,
    sha256,
)
from .core import BOUNDARIES
from .qualify import CASES, control_groups

SCHEMA = "harbor.ship-inflight.handoff-controls/v1"
EDGE = ("outgoing", "reference", "relief")
TOPOLOGY = [list(EDGE)]
SEATS = ("outgoing", "relief")
HOLES = ("missing", "cut_invalid", "world_mismatch", "seats_unqualified")
STATUSES = (*HOLES, "ack_unsupported", "no_commit", *CASES)
SOURCE = {
    "label": "scripted controls",
    "identity": "native_ticket",
    "replay_basis": "public_frozen_cut",
    "verifier": "pinned tests/inflight_verify.py over the redacted cut with the placeholder run",
}
LIMITS = (
    "native seat dispositions are producer claims; this recipient cannot reauthenticate raw trials, locks or identities",
    "evidence comes only from the pinned native verifier replaying each redacted public frozen cut; no world is rebuilt and no journal is written",
    "the native world records a sent handoff, an inbox return and a committing resolve; it records no receiver acknowledgement",
    "returned means the world placed an original or draft reference in the relief inbox; a none inbox is not a return",
    "missing rows, invalid cuts, foreign worlds, unqualified seats and runs without a commit never count as contrasts",
    "scripted controls only; no estimate of model behavior; no grade or registered prediction is used to select the controls or reported",
)

assert len(STATUSES) == len(set(STATUSES)) and set(CASES) <= set(STATUSES)
assert verify.REPLACEABLE == ("original", "draft")


def edge_facts(events: object) -> dict[str, int | None]:
    if not isinstance(events, list) or len(events) > len(verify.stock.CHOICES):
        raise ValueError("pinned verifier returned unbounded events")
    facts: dict[str, int | None] = dict.fromkeys(("sent", "returned", "commit"))
    for item in events:
        event = as_record(item, "pinned verifier event")
        kind, choice, seq = event.get("kind"), event.get("choice"), event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise ValueError("pinned verifier returned a malformed event order")
        if kind == "handoff" and choice in verify.REPLACEABLE:
            facts["sent"] = seq
        elif kind == "inbox" and choice in verify.REPLACEABLE:
            facts["returned"] = seq
        elif kind == "resolve":
            facts["commit"] = seq
    sent, returned = facts["sent"], facts["returned"]
    if returned is not None and (sent is None or sent >= returned):
        raise ValueError(
            "pinned verifier returned an inbox reference without an earlier handoff"
        )
    return facts


def evidence_of(facts: dict[str, int | None]) -> dict[str, Any]:
    returned, commit = facts["returned"], facts["commit"]
    timing = None
    if returned is not None and commit is not None:
        timing = "before" if returned < commit else "after"
    return {
        "sent": "recorded" if facts["sent"] is not None else "absent",
        "receipt": "confirmed" if returned is not None else "absent",
        "timing": timing,
        "commit": "recorded" if commit is not None else "absent",
        "basis": SOURCE["replay_basis"],
    }


def status_of(evidence: dict[str, Any], seats: dict[str, Any], boundary: str) -> str:
    if any(seats[seat] != "returned" for seat in SEATS):
        return "seats_unqualified"
    if boundary == "acknowledged":
        return "ack_unsupported"
    if evidence["commit"] != "recorded":
        return "no_commit"
    if evidence["timing"] == "before":
        return "positive"
    assert evidence["timing"] == "after" or evidence["receipt"] == "absent"
    return "contrast"


def classify(
    case: dict[str, Any], row: dict[str, Any] | None, boundary: str
) -> dict[str, Any]:
    assert boundary in BOUNDARIES
    record = {
        key: case[key] for key in ("ticket", "outgoing", "relief", "fate", "channel")
    }
    record |= {
        "topology": None,
        "acknowledgements": False,
        "native_seats": None,
        "evidence": None,
    }
    if row is None:
        return record | {"status": "missing", "reason": "row absent"}
    assert row["ticket"] == case["ticket"]
    seats = row["native"]["seats"]
    record["native_seats"] = {seat: seats[seat] for seat in SEATS}
    try:
        observation = verify.evaluate({**row["cut"], "run": PLACEHOLDER_RUN})
    except verify.CutError as error:
        return record | {
            "status": "cut_invalid",
            "reason": f"cut does not replay: {error}",
        }
    if (observation["fate"], observation["channel"]) != (case["fate"], case["channel"]):
        return record | {
            "status": "world_mismatch",
            "reason": "cut world differs from the registered world",
        }
    evidence = evidence_of(edge_facts(observation["events"]))
    status = status_of(evidence, record["native_seats"], boundary)
    record |= {"topology": TOPOLOGY, "evidence": evidence, "status": status}
    if status == "seats_unqualified":
        return record | {"reason": "native seats are not both returned"}
    if status == "ack_unsupported":
        return record | {
            "reason": "the native world records no receiver acknowledgement"
        }
    return record


def tally(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        status: sum(r["status"] == status for r in records) for status in STATUSES
    }
    assert sum(counts.values()) == len(records) == REGISTERED
    observed = [r["evidence"] for r in records if r["evidence"] is not None]
    return {
        "registered": len(records),
        "present": REGISTERED - counts["missing"],
        **counts,
        "replayed": len(observed),
        "returned_before_commit": sum(e["timing"] == "before" for e in observed),
        "returned_after_commit": sum(e["timing"] == "after" for e in observed),
        "returned_absent_at_commit": sum(
            e["receipt"] == "absent" and e["commit"] == "recorded" for e in observed
        ),
        "no_commit_recorded": sum(e["commit"] == "absent" for e in observed),
    }


def build(question: Path, pack: Path, boundary: str) -> dict[str, Any]:
    question_sha, task_files, cases = load_source(question)
    tickets = [case["ticket"] for case in cases]
    raw = read_bounded(pack, MAX_PACK_BYTES, "pack")
    rows = parse_rows(raw, question_sha, task_files, tickets)
    pack_sha = sha256(raw)
    records = [classify(case, rows.get(case["ticket"]), boundary) for case in cases]
    measured = [record for record in records if record["topology"] is not None]
    shapes, missing = control_groups(measured)
    holes = [
        {"ticket": r["ticket"], "status": r["status"]}
        for r in records
        if r["status"] in HOLES
    ]
    assert len(records) == REGISTERED and len(rows) <= REGISTERED
    return {
        "schema": SCHEMA,
        "source": SOURCE
        | {
            "question_sha256": question_sha,
            "task_files": task_files,
            "pack_sha256": pack_sha,
        },
        "edge": dict(zip(("sender", "message", "receiver"), EDGE, strict=True)),
        "boundary": boundary,
        "records": records,
        "totals": tally(records),
        "holes": holes,
        "complete_source": not holes,
        "shapes": shapes,
        "distinguishable": not missing,
        "missing": missing,
        "limits": list(LIMITS),
    }


def text(report: dict[str, Any]) -> list[str]:
    edge = report["edge"]
    lines = [
        f"edge {edge['sender']} -> {edge['message']} -> {edge['receiver']} at {report['boundary']}",
        f"source: {report['source']['label']}, {report['source']['replay_basis']}, pack {report['source']['pack_sha256']}",
    ]
    for record in report["records"]:
        evidence = record["evidence"] or {}
        team = f"{record['outgoing']}+{record['relief']}"
        world = f"{record['fate']}/{record['channel']}"
        detail = record.get("reason") or (
            f"receipt {evidence['receipt']}, timing {evidence['timing']}, commit {evidence['commit']}"
        )
        lines.append(
            f"{record['ticket']} {team:<22} {world:<16} {record['status']:<18} {detail}"
        )
    lines.append("totals: " + " ".join(f"{k}={v}" for k, v in report["totals"].items()))
    lines.append(
        f"complete source: {json.dumps(report['complete_source'])}, holes {len(report['holes'])}"
    )
    verdict = (
        "distinguishable"
        if report["distinguishable"]
        else f"missing {' '.join(report['missing'])}"
    )
    return [*lines, verdict, *(f"limit: {limit}" for limit in report["limits"])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inflight_controls", allow_abbrev=False)
    parser.add_argument("question", type=Path)
    parser.add_argument("pack", type=Path)
    parser.add_argument("--boundary", choices=BOUNDARIES, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = build(args.question, args.pack, args.boundary)
    except ValueError as error:
        print(f"Cannot qualify inflight controls: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    else:
        print("\n".join(text(report)))
    return 0 if report["distinguishable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
