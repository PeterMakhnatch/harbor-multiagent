"""Merge disjoint job scopes without counting a copied worker packet twice."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, localcontext

from .account_contract import MAX_SEAT_ROWS, validate
from .seat_stream import Malformed, Record, _require

SCHEMA = "seatlab.team-account-report/v1"
MAX_PACKETS = 128
MAX_TOTAL_ATTEMPTS = 16_384
CAVEAT = (
    "Use stationary job snapshots. A missing result is not a failed team. "
    "Only a closed native job establishes final outcomes; an open job can replace a surviving execution. "
    "Harness-reported USD and token-priced USD are separate observations, not an invoice. "
    "Unknown does not mean zero. Seat labels identify roles within a job, not people."
)


def unique(packets: list[Record]) -> tuple[list[Record], int]:
    _require(1 <= len(packets) <= MAX_PACKETS, "expected 1 to 128 accounting packets")
    scopes: dict[tuple[str, str], Record] = {}
    trials: set[str] = set()
    rows = 0
    for raw in packets:
        record = validate(raw)
        key = (record["source"], record["scope"])
        existing = scopes.get(key)
        if existing is not None:
            _require(
                existing == record,
                "two snapshots disagree for the same scope; select one",
            )
            continue
        ids = {a["trial"] for a in record["attempts"]}
        _require(
            not ids.intersection(trials),
            "a native execution belongs to more than one scope",
        )
        trials.update(ids)
        rows += sum(len(a["seats"]) for a in record["attempts"])
        _require(
            len(trials) <= MAX_TOTAL_ATTEMPTS and rows <= MAX_SEAT_ROWS,
            "combined account inventory exceeds supported size",
        )
        scopes[key] = record
    return [scopes[key] for key in sorted(scopes)], len(packets) - len(scopes)


def _sum_money(rows: list[Record], field: str) -> str | None:
    values = [row[field] for row in rows if row[field] is not None]
    if not values:
        return None
    with localcontext() as context:
        context.prec = 80
        total = sum((Decimal(value) for value in values), Decimal(0))
        text = format(total, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _coverage(rows: list[Record], field: str, *, dollars: bool = False) -> Record:
    known = [row[field] for row in rows if row[field] is not None]
    return {
        "known_seats": len(known),
        "unknown_seats": len(rows) - len(known),
        "known_sum": _sum_money(rows, field)
        if dollars
        else sum(known)
        if known
        else None,
    }


def _seat_totals(rows: list[Record]) -> Record:
    dispositions = Counter(row["disposition"] or "unknown" for row in rows)
    return {
        "seat_records": len(rows),
        "dispositions": dict(sorted(dispositions.items())),
        "harness_usd": _coverage(rows, "harness_usd", dollars=True),
        "priced_low_usd": _coverage(rows, "priced_low_usd", dollars=True),
        "priced_high_usd": _coverage(rows, "priced_high_usd", dollars=True),
        "input_tokens_including_cache": _coverage(rows, "input_tokens"),
        "cache_tokens": _coverage(rows, "cache_tokens"),
        "output_tokens": _coverage(rows, "output_tokens"),
        "recorded_calls": _coverage(rows, "calls"),
        "unsettled_calls": _coverage(rows, "held_calls"),
    }


def _routes(seats: list[Record]) -> list[Record]:
    counts = Counter(
        (
            s["harness"],
            s["reported_provider"],
            s["reported_model"],
            tuple(s["served_models"]),
        )
        for s in seats
    )
    return [
        {
            "harness": k[0],
            "reported_provider": k[1],
            "reported_model": k[2],
            "served_models": list(k[3]),
            "seat_records": n,
        }
        for k, n in sorted(
            counts.items(),
            key=lambda kv: (kv[0][0] or "", kv[0][1] or "", kv[0][2] or "", kv[0][3]),
        )
    ]


def summarize(
    packets: list[Record], *, copies: int = 0, trial: str | None = None
) -> Record:
    """Summarize canonical, deduplicated packets returned by unique()."""
    attempts = [a for p in packets for a in p["attempts"]]
    groups: dict[str, list[Record]] = defaultdict(list)
    seats: list[Record] = []
    selected: Record | None = None
    scopes = []
    for p in packets:
        for a in p["attempts"]:
            if a["trial"] == trial:
                selected = {
                    "source": p["source"],
                    "scope": p["scope"],
                    "job_closed": p["job_closed"],
                    **a,
                }
            for s in a["seats"]:
                groups[s["name"]].append(s)
                seats.append(s)
        scopes.append(
            {
                "source": p["source"],
                "scope": p["scope"],
                "job_closed": p["job_closed"],
                "registered": p["registered"],
                "surviving": len(p["attempts"]),
                "reported_retries": p["retries"],
            }
        )
    if trial is not None and selected is None:
        raise Malformed("requested execution is not present in the supplied results")
    registered = sum(p["registered"] for p in packets)
    retries = [p["retries"] for p in packets if p["retries"] is not None]
    grades = Counter(a["grade_state"] for a in attempts)
    report = {
        "schema": SCHEMA,
        "scopes": scopes,
        "duplicate_packets_ignored": copies,
        "registered_slots": registered,
        "surviving_results": len(attempts),
        "slots_without_result": registered - len(attempts),
        "reported_retries": {
            "known_sum": sum(retries) if retries else None,
            "unknown_scopes": len(packets) - len(retries),
        },
        "grade_counts": {k: grades[k] for k in ("valid", "invalid", "unknown")},
        "valid_binary_rewards": {
            "zero": sum(
                a["reward"] == 0 for a in attempts if a["grade_state"] == "valid"
            ),
            "one": sum(
                a["reward"] == 1 for a in attempts if a["grade_state"] == "valid"
            ),
            "other": sum(
                a["reward"] not in (0, 1)
                for a in attempts
                if a["grade_state"] == "valid"
            ),
        },
        "reported_routes": _routes(seats),
        "rosters_without_seats": sum(not a["seats"] for a in attempts),
        "seats": _seat_totals(seats),
        "by_seat_label": {k: _seat_totals(groups[k]) for k in sorted(groups)},
        "provider_receipts": None,
        "caveat": CAVEAT,
    }
    if selected is not None:
        report["selected_result"] = selected
    return report


def _value(value: object) -> str:
    return "unknown" if value is None else str(value)


def _usd(value: str | None) -> str:
    if value is None:
        return "unknown"
    amount = Decimal(value)
    if 0 < amount < Decimal("0.000000001"):
        return "<0.000000001"
    rounded = format(amount, ".9f").rstrip("0").rstrip(".")
    return rounded if amount == Decimal(rounded) else f"~{rounded}"


def _cost(label: str, coverage: Record) -> str:
    return f"{label}: {_usd(coverage['known_sum'])} USD; seats known {coverage['known_seats']}, unknown {coverage['unknown_seats']}"


def text(report: Record) -> str:
    grades, seats, retries = (
        report["grade_counts"],
        report["seats"],
        report["reported_retries"],
    )
    jobs = [scope for scope in report["scopes"] if scope["source"] == "native-job"]
    valid = report["valid_binary_rewards"]
    lines = [
        f"Surviving results: {report['surviving_results']}/{report['registered_slots']} slots; without a result {report['slots_without_result']}",
        f"Observed grades: valid {grades['valid']} ({valid['one']} ones, {valid['zero']} zeros, {valid['other']} other); invalid {grades['invalid']}, unknown {grades['unknown']}",
        f"Retries reported: {_value(retries['known_sum'])}; scopes without a retry count: {retries['unknown_scopes']}",
        _cost("Harness-reported subtotal", seats["harness_usd"]),
        _cost("Token-priced lower subtotal", seats["priced_low_usd"]),
        _cost(
            "Token-priced upper subtotal (including holds)", seats["priced_high_usd"]
        ),
    ]
    if jobs:
        closed = sum(scope["job_closed"] is True for scope in jobs)
        open_jobs = sum(scope["job_closed"] is False for scope in jobs)
        lines.append(
            f"Native jobs: {closed} closed, {open_jobs} not closed, {len(jobs) - closed - open_jobs} without closure evidence"
        )
    for r in report["reported_routes"]:
        lines.append(
            f"  Route ({r['seat_records']} seat records): harness {_value(r['harness'])}; reported provider {_value(r['reported_provider'])}; reported model {_value(r['reported_model'])}; served label {', '.join(r['served_models']) or 'unknown'}"
        )
    for label, counts in report["by_seat_label"].items():
        lines.append(
            f"  {label}: {counts['seat_records']} seat records; harness USD {_usd(counts['harness_usd']['known_sum'])}; token-priced USD {_usd(counts['priced_low_usd']['known_sum'])} to {_usd(counts['priced_high_usd']['known_sum'])}"
        )
    if (detail := report.get("selected_result")) is not None:
        lines.append(
            f"Selected execution {detail['trial']}: grade {detail['grade_state']}; reward {_value(detail['reward'])}"
        )
        for s in detail["seats"]:
            lines.append(
                f"  {s['name']}: {_value(s['disposition'])}; reported model {_value(s['reported_model'])}; served model(s) {', '.join(s['served_models']) or 'unknown'}; input {_value(s['input_tokens'])}; output {_value(s['output_tokens'])}; harness USD {_usd(s['harness_usd'])}; token-priced USD {_usd(s['priced_low_usd'])} to {_usd(s['priced_high_usd'])}"
            )
    if report["provider_receipts"] is None:
        lines.append("Provider charges: unknown (no settlement evidence supplied)")
    else:
        receipt = report["provider_receipts"]
        usd, gens = receipt["known_usd"], receipt["generations"]
        label = (
            "Saved generation details" if "documents" in receipt else "Provider ledger"
        )
        lines.append(
            f"{label}: {_usd(usd['settled'])} USD in supplied settlements; {_usd(usd['assigned_to_team'])} USD assigned to these teams ({gens['matched']['count']} generations); {_usd(usd['not_assigned'])} USD unassigned"
        )
        if "documents" in receipt:
            coverage = "Call starts, failures and missing bills are unknown."
        else:
            pending = sum(receipt["ledger"]["unsettled_calls"].values())
            coverage = f"Unsettled calls in this ledger: {pending}."
        lines.append(
            f"  Seats without an owner record: {receipt['seats']['without_owner_record']}. {coverage} This is not an invoice."
        )
    if (archive := report.get("attempt_archive")) is not None:
        method = archive["harness_usd"]
        lines.append(
            f"Retry archive: {archive['finals']['confirmed']} surviving captures agree, {archive['finals']['disagreeing']} disagree, {archive['finals']['uncaptured']} absent; {method['attempts']} confirmed earlier executions"
        )
        lines.append(
            f"  Earlier harness USD: {_usd(method['known'])} recorded; complete total {_usd(method['total'])}; priced seats {method['seat_rows_priced']}/{method['seat_rows']}. Earlier executions never add a grade."
        )
    if report["duplicate_packets_ignored"]:
        lines.append(
            f"Identical worker packets ignored: {report['duplicate_packets_ignored']}"
        )
    lines.append(CAVEAT)
    return "\n".join(lines)
