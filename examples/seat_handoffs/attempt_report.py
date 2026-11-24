from __future__ import annotations

import os
import re

from ..seatlab.workbench.account_contract import MAX_ATTEMPTS
from ..seatlab.workbench.account_contract import validate as validate_packet
from ..seatlab.workbench.account_history import _canonical, _snapshot
from ..seatlab.workbench.seat_stream import (
    MIB,
    UUID,
    Malformed,
    Record,
    _read,
    _require,
    _strict_json,
)
from .attempt_receipt import OBSERVED, SOURCES
from .attempt_receipt import validate as validate_receipt
from .core import MAX_EDGES

SCHEMA = "seatlab.attempt-handoff-report/v1"
PACKET_SOURCE = "native-job"
MAX_RETRIES = 16_384
MAX_RECEIPTS = MAX_ATTEMPTS + MAX_RETRIES
MAX_RECEIPT_BYTES = MIB
MAX_ARCHIVE_BYTES = 256 * MIB
MAX_REPORT_BYTES = 16 * MIB
NAME = rf"(?P<job>{UUID.pattern})\.(?P<slot>[0-9a-f]{{64}})\.(?P<trial>{UUID.pattern})"
RECEIPT = re.compile(rf"{NAME}\.handoffs\.json")
STAGING = re.compile(rf"\.{NAME}\.handoffs\.json\.[0-9a-f]{{32}}\.tmp")
PATTERNS = (("receipt", RECEIPT), ("staging", STAGING))
SKIPPED = ("other_job", "staging_published", "staging_unpublished")
CAPTURES = (*SOURCES, "uncaptured")
GRADES = ("valid", "invalid", "unknown")
STATUSES = ("same", "different", "unknown", "not_comparable")
SCORES = ("same", "different", "unknown")
EDGE_STATES = ("same", "different", "unknown")
REASONS = (
    "job_not_closed",
    "no_final",
    "final_uncaptured",
    "final_unavailable",
    "final_unusable",
    "unavailable",
    "unusable",
    "partial_evidence",
    "roster",
    "edge_set",
)
QUESTIONS = (
    ("commit", None, "commit"),
    ("returned.receipt", "returned", "receipt"),
    ("returned.timing", "returned", "timing"),
    ("acknowledged.receipt", "acknowledged", "receipt"),
    ("acknowledged.timing", "acknowledged", "timing"),
)
POSITIVE = {
    "commit": frozenset({"recorded"}),
    "receipt": frozenset({"confirmed"}),
    "timing": frozenset({"before", "after"}),
}
BOUNDARY_NOTE = (
    "World prepared (returned) versus receiver acknowledged are separate boundaries; neither shows that a model "
    "read, used or acted on a message."
)
CAVEAT = (
    "Joins retained handoff receipts to one native-job packet by job, slot digest and trial UUID. Packet attempts "
    "are slot finals, and expected executions are registered slots plus reported retries, only when the packet "
    "marks the job closed; otherwise receipts are listed as stored observations and every final, retry order, "
    "change and missing denominator is unknown. Grades come only from the packet, one per slot; receipt counts are "
    "coverage, not grades. Another execution in a slot is compared only with that slot's final receipt. 'same' "
    "needs two sealed journals with the same roster, the same declared edges and equal known answers; a timing is "
    "not applicable only where both sealed journals show its boundary absent. A world prefix can show a difference "
    "only from a positive finding against a sealed answer. Unknown against known is unknown, never a change. A "
    "different roster or edge set is not comparable, not a behavior change. Score agreement uses valid grades of "
    "the two compared executions only. No comparison claims order, cause, model reading or job success. "
    f"{BOUNDARY_NOTE} Entry names are trusted-collector labels; the archive is assumed stationary and is checked "
    "for the same directory identity and name set before and after reading."
)


def _kind(name: str, job: str) -> tuple[str, re.Match[str]]:
    for kind, pattern in PATTERNS:
        match = pattern.fullmatch(name)
        if match is not None:
            return (kind if match["job"] == job else "other_job"), match
    raise Malformed("archive holds an entry that is not a handoff receipt")


def _receipt(data: bytes, match: re.Match[str], job: str) -> Record:
    document = _strict_json(data, "handoff receipt is not strict UTF-8 JSON")
    try:
        row = validate_receipt(document)
    except ValueError as error:
        raise Malformed("handoff receipt is invalid") from error
    _require(
        row["job"] == job
        and row["slot"] == match["slot"]
        and row["trial"] == match["trial"],
        "handoff receipt name and identity disagree",
    )
    _require(_canonical(row) + b"\n" == data, "handoff receipt is not canonical")
    return row


def _receipts(job: str, archive: str) -> tuple[dict[str, Record], dict[str, int]]:
    _require(
        type(archive) is str and 0 < len(archive) <= 4096,
        "archive path is not a string",
    )
    pin, names = _snapshot(archive)
    present = set(names)
    skipped = dict.fromkeys(SKIPPED, 0)
    rows: dict[str, Record] = {}
    worlds: dict[str, str] = {}
    remaining = MAX_ARCHIVE_BYTES
    for name in names:
        kind, match = _kind(name, job)
        if kind == "staging":
            kind = (
                "staging_published"
                if name[1:].rsplit(".", 2)[0] in present
                else "staging_unpublished"
            )
        if kind != "receipt":
            skipped[kind] += 1
            continue
        _require(
            len(rows) < MAX_RECEIPTS, f"archive exceeds {MAX_RECEIPTS} handoff receipts"
        )
        _require(remaining > 0, "handoff receipts exceed 256 MiB in total")
        data = _read(
            os.path.join(archive, name),
            min(MAX_RECEIPT_BYTES, remaining),
            "handoff receipt",
        )
        remaining -= len(data)
        _require(
            0 <= remaining <= MAX_ARCHIVE_BYTES,
            "handoff receipts exceed 256 MiB in total",
        )
        row = _receipt(data, match, job)
        _require(row["trial"] not in rows, "archive holds one trial in two slots")
        if row["world"] is not None:
            bound = worlds.setdefault(row["world"], row["trial"])
            _require(
                bound == row["trial"], "one world identity is bound to two executions"
            )
        rows[row["trial"]] = row
    _require(
        _snapshot(archive) == (pin, names), "archive moved or changed while reading"
    )
    assert sum(skipped.values()) + len(rows) <= len(names)
    return rows, skipped


def _bind(finals: list[Record], rows: dict[str, Record]) -> None:
    for final in finals:
        kept = rows.get(final["trial"])
        if kept is None:
            continue
        roster = [seat["name"] for seat in final["seats"]]
        _require(
            kept["slot"] == final["slot"]
            and kept["grade_state"] == final["grade_state"]
            and kept["reward"] == final["reward"]
            and kept["roster"] == roster,
            "final capture disagrees with the native final",
        )


def _answer(edge: Record, boundary: str | None, field: str) -> str:
    value = edge[field] if boundary is None else edge[boundary][field]
    assert type(value) is str
    return value


def _known(value: str, sealed: bool, field: str) -> bool:
    return value != "unknown" and (sealed or value in POSITIVE[field])


def _question(
    final_value: str, value: str, final_sealed: bool, sealed: bool, field: str
) -> str:
    if not (_known(final_value, final_sealed, field) and _known(value, sealed, field)):
        return "unknown"
    if final_value != value:
        return "different" if final_sealed or sealed else "unknown"
    return "same" if final_sealed and sealed else "unknown"


def _not_applicable(
    final_edge: Record,
    edge: Record,
    both_sealed: bool,
    boundary: str | None,
    field: str,
) -> bool:
    if not both_sealed or boundary is None or field != "timing":
        return False
    return final_edge[boundary]["receipt"] == edge[boundary]["receipt"] == "absent"


def _blocked(status: str, reason: str, changed: list[str]) -> Record:
    assert status in ("unknown", "not_comparable") and reason in REASONS
    counts = {"comparable": 0, **dict.fromkeys(EDGE_STATES, 0)}
    return {"status": status, "reason": reason, "edges": counts, "changed": changed}


def _edges(row: Record) -> dict[tuple[str, str, str], Record]:
    edges = {(e["message"], e["sender"], e["receiver"]): e for e in row["edges"]}
    assert len(edges) == len(row["edges"]) <= MAX_EDGES
    return edges


def _edge_answers(
    final_edge: Record, edge: Record, final_sealed: bool, sealed: bool
) -> list[tuple[str, str]]:
    both_sealed = final_sealed and sealed
    answers = [
        (
            name,
            _question(
                _answer(final_edge, b, f), _answer(edge, b, f), final_sealed, sealed, f
            ),
        )
        for name, b, f in QUESTIONS
        if not _not_applicable(final_edge, edge, both_sealed, b, f)
    ]
    assert 3 <= len(answers) <= len(QUESTIONS)
    return answers


def _observe(final_row: Record, row: Record) -> Record:
    final_edges, edges = _edges(final_row), _edges(row)
    if final_row["roster"] != row["roster"]:
        return _blocked("not_comparable", "roster", ["roster"])
    if final_edges.keys() != edges.keys():
        return _blocked("not_comparable", "edge_set", ["edge_set"])
    final_sealed, sealed = final_row["source"] == "sealed", row["source"] == "sealed"
    counts = dict.fromkeys(EDGE_STATES, 0)
    changed: set[str] = set()
    for key in sorted(edges):
        answers = _edge_answers(final_edges[key], edges[key], final_sealed, sealed)
        states = {state for _, state in answers}
        changed.update(name for name, state in answers if state == "different")
        counts[
            "different"
            if "different" in states
            else "same"
            if states == {"same"}
            else "unknown"
        ] += 1
    assert sum(counts.values()) == len(edges)
    status = (
        "different"
        if counts["different"]
        else "same"
        if counts["same"] == len(edges)
        else "unknown"
    )
    return {
        "status": status,
        "reason": "partial_evidence" if status == "unknown" else None,
        "edges": {"comparable": len(edges), **counts},
        "changed": [name for name, _, _ in QUESTIONS if name in changed],
    }


def _score(final: Record, row: Record) -> str:
    if final["grade_state"] != "valid" or row["grade_state"] != "valid":
        return "unknown"
    return "same" if final["reward"] == row["reward"] else "different"


def _comparison(
    closed: bool, final: Record | None, final_row: Record | None, row: Record
) -> Record:
    if not closed:
        observed = _blocked("unknown", "job_not_closed", [])
    elif final is None:
        observed = _blocked("unknown", "no_final", [])
    elif final_row is None:
        observed = _blocked("unknown", "final_uncaptured", [])
    elif final_row["source"] not in OBSERVED:
        observed = _blocked("unknown", f"final_{final_row['source']}", [])
    elif row["source"] not in OBSERVED:
        observed = _blocked("unknown", row["source"], [])
    else:
        observed = _observe(final_row, row)
    assert observed["status"] in STATUSES
    score = "unknown" if not closed or final is None else _score(final, row)
    return {"trial": row["trial"], "source": row["source"], **observed, "score": score}


def _slots(
    closed: bool, attempts: list[Record], rows: dict[str, Record]
) -> list[Record]:
    by_slot = {attempt["slot"]: attempt for attempt in attempts}
    packet_trials = {attempt["trial"] for attempt in attempts}
    others: dict[str, list[Record]] = {}
    for trial in sorted(rows.keys() - packet_trials):
        others.setdefault(rows[trial]["slot"], []).append(rows[trial])
    slots: list[Record] = []
    for slot in sorted(by_slot.keys() | others.keys()):
        attempt = by_slot.get(slot)
        kept = None if attempt is None else rows.get(attempt["trial"])
        capture = (
            None
            if attempt is None
            else "uncaptured"
            if kept is None
            else kept["source"]
        )
        comparisons = [
            _comparison(closed, attempt, kept, row) for row in others.get(slot, [])
        ]
        slots.append(
            {
                "slot": slot,
                "packet_trial": None if attempt is None else attempt["trial"],
                "packet_capture": capture,
                "other_receipts": len(comparisons),
                "comparisons": comparisons,
            }
        )
    assert sum(s["other_receipts"] for s in slots) == len(rows.keys() - packet_trials)
    return slots


def _totals(slots: list[Record]) -> Record:
    packet = dict.fromkeys(CAPTURES, 0)
    statuses, scores, sources = (
        dict.fromkeys(STATUSES, 0),
        dict.fromkeys(SCORES, 0),
        dict.fromkeys(SOURCES, 0),
    )
    in_packet = without = divergent = 0
    for slot in slots:
        if slot["packet_capture"] is not None:
            packet[slot["packet_capture"]] += 1
        for item in slot["comparisons"]:
            statuses[item["status"]] += 1
            scores[item["score"]] += 1
            sources[item["source"]] += 1
            in_packet += slot["packet_trial"] is not None
            without += slot["packet_trial"] is None
            divergent += item["score"] == "same" and item["status"] == "different"
    assert (
        sum(statuses.values())
        == sum(scores.values())
        == sum(sources.values())
        == in_packet + without
    )
    return {
        "captures": {
            "packet_attempts": packet,
            "other_in_packet_slot": in_packet,
            "other_without_packet_attempt": without,
            "other_sources": sources,
        },
        "comparisons": statuses,
        "scores": scores,
        "same_score_different_handoffs": divergent,
    }


def _executions(
    closed: bool, registered: int, retries: int | None, known: int, captured: int
) -> Record:
    expected = registered + retries if closed and retries is not None else None
    gap = None if expected is None else expected - known
    assert 0 <= captured <= known
    return {
        "reported_retries": retries,
        "expected": expected,
        "known": known,
        "captured": captured,
        "unaccounted": gap if gap is not None and gap >= 0 else None,
        "counts_agree": None if gap is None else gap == 0,
        "over_expected": None if gap is None else gap < 0,
    }


def _complete(
    registered: int,
    attempts: list[Record],
    rows: dict[str, Record],
    executions: Record,
    totals: Record,
    skipped: dict[str, int],
) -> bool:
    captures = totals["captures"]
    return (
        executions["counts_agree"] is True
        and len(rows) == executions["known"]
        and len(attempts) == registered
        and captures["packet_attempts"]["uncaptured"] == 0
        and captures["other_without_packet_attempt"] == 0
        and skipped["staging_unpublished"] == 0
    )


def report(final_packet: Record, archive: str) -> Record:
    packet = validate_packet(final_packet)
    _require(
        packet["source"] == PACKET_SOURCE, "final packet is not a native-job account"
    )
    job, registered, retries, attempts = (
        packet["scope"],
        packet["registered"],
        packet["retries"],
        packet["attempts"],
    )
    job_closed = packet["job_closed"]
    _require(
        job_closed is None or type(job_closed) is bool,
        "final packet job_closed is not a boolean or null",
    )
    closed = job_closed is True
    rows, skipped = _receipts(job, archive)
    _bind(attempts, rows)
    slots = _slots(closed, attempts, rows)
    _require(
        len(slots) <= registered, "archive and packet name more slots than registered"
    )
    totals = _totals(slots)
    executions = _executions(
        closed,
        registered,
        retries,
        len(rows.keys() | {a["trial"] for a in attempts}),
        len(rows),
    )
    complete = _complete(registered, attempts, rows, executions, totals, skipped)
    evidence = {
        source: sum(row["source"] == source for row in rows.values())
        for source in SOURCES
    }
    assert sum(evidence.values()) == len(rows)
    record = {
        "schema": SCHEMA,
        "job": job,
        "job_closed": job_closed,
        "packet": {
            "registered": registered,
            "present": len(attempts),
            "final": closed,
            "missing_finals": registered - len(attempts) if closed else None,
            "grades": {
                grade: sum(a["grade_state"] == grade for a in attempts)
                for grade in GRADES
            },
        },
        "executions": executions,
        **totals,
        "journal_evidence": evidence,
        "skipped": skipped,
        "coverage_complete": complete,
        "all_expected_sealed": complete
        and executions["expected"] > 0
        and evidence["sealed"] == len(rows),
        "slots": slots,
        "caveat": CAVEAT,
    }
    _require(
        len(_canonical(record)) <= MAX_REPORT_BYTES, "handoff report exceeds 16 MiB"
    )
    return record


def _value(value: object) -> str:
    if value is None:
        return "unknown"
    return _yes(value) if type(value) is bool else str(value)


def _counts(counts: Record) -> str:
    return ", ".join(
        f"{name.replace('_', ' ')} {count}" for name, count in counts.items()
    )


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def text(record: Record) -> str:
    packet, runs, captures = record["packet"], record["executions"], record["captures"]
    role = "final" if packet["final"] else "present (job not closed; not final)"
    lines = [
        f"Handoff receipts for job {record['job']}; job closed {_value(record['job_closed'])}",
        (
            f"Native packet attempts, {role}: {packet['present']}/{packet['registered']} slots; "
            f"missing finals {_value(packet['missing_finals'])}; grades {_counts(packet['grades'])}"
        ),
        (
            f"Executions: expected {_value(runs['expected'])}; known {runs['known']}; captured {runs['captured']}; "
            f"unaccounted {_value(runs['unaccounted'])}; over expected {_value(runs['over_expected'])}; "
            f"reported retries {_value(runs['reported_retries'])}"
        ),
        f"Journal evidence over all captured executions: {_counts(record['journal_evidence'])}",
        f"Packet attempt captures: {_counts(captures['packet_attempts'])}",
        (
            f"Other receipts: {captures['other_in_packet_slot']} in a slot with a packet attempt; "
            f"{captures['other_without_packet_attempt']} without one; sources {_counts(captures['other_sources'])}"
        ),
        f"Skipped entries: {_counts(record['skipped'])}",
        f"Handoffs against the slot's native final: {_counts(record['comparisons'])}",
        (
            f"Grades of other executions against the slot's final (independent of journal coverage): "
            f"{_counts(record['scores'])}; same grade with different observed handoffs "
            f"{record['same_score_different_handoffs']} (not a causal effect)"
        ),
        (
            f"Coverage complete: {_yes(record['coverage_complete'])}; "
            f"all expected executions sealed: {_yes(record['all_expected_sealed'])}"
        ),
        BOUNDARY_NOTE,
    ]
    return "\n".join(lines)
