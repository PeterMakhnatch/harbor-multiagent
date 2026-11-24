from __future__ import annotations

from pathlib import Path

from ...seat_handoffs import attempt_report, lifecycle_join
from . import account_history, attempt_history, attention_starts, native_account
from .account_contract import validate
from .seat_stream import Record, _require

SOURCE = "native-job"
SEAT_CLASSES = {
    "pending_header": "seat_journal_without_header",
    "open": "seat_journal_open",
    "closed_partial_roster": "seat_journal_partial_roster",
}
WITNESS = {
    "moved": "witness_slot_mismatch",
    "archive_only": "witness_execution_unobserved",
    "witness_only": "seat_journal_missing_witnessed",
}
LIFECYCLE = {
    "no_handoff_capture": "handoff_receipt_missing",
    "world_prefix": "handoff_receipt_world_prefix_only",
    "unavailable": "handoff_receipt_unavailable",
    "unusable": "handoff_receipt_unusable",
    "slot_disagrees": "handoff_receipt_slot_mismatch",
    "roster_disagrees": "handoff_receipt_roster_mismatch",
    "roster_unknown": "handoff_receipt_roster_unknown",
    "handoff_only": "handoff_receipt_without_seat_journal",
}
UNSEALED = {source: LIFECYCLE[source] for source in attempt_report.SOURCES[1:]}
ACCOUNT_FINALS = {
    "uncaptured": "account_final_missing",
    "disagreeing": "account_final_mismatch",
}
ACCOUNT_NONFINAL = {
    "captured_without_final": "account_attempt_without_final",
    "superseded_unconfirmed_slot": "account_attempt_final_unconfirmed",
}
OTHER = (
    "slot_without_result",
    "grade_invalid",
    "grade_unknown",
    "result_without_roster",
    "result_without_seat_results",
    "incomplete_no_registered_slots",
    "incomplete_native_retries_unreported",
    "seat_journal_missing",
    "seat_slot_without_journal",
    "seat_journal_final_missing",
    "seat_journal_final_slot_mismatch",
    "seat_journal_final_roster_mismatch",
    "handoff_receipt_slot_without_final",
    "handoff_executions_over_expected",
    "handoff_staging_unpublished",
    "account_attempt_missing",
    "account_attempts_over_expected",
    "account_attempt_unpriced",
    "account_staging_unpublished",
    "incomplete_account_retry_coverage",
)
ACTIONS = tuple(
    sorted(
        {
            *SEAT_CLASSES.values(),
            *WITNESS.values(),
            *LIFECYCLE.values(),
            *ACCOUNT_FINALS.values(),
            *ACCOUNT_NONFINAL.values(),
            *OTHER,
            *attention_starts.ACTIONS,
        }
    )
)
assert len(ACTIONS) == len(OTHER) + 18 + len(attention_starts.ACTIONS)
assert set(LIFECYCLE) | {"sealed"} == set(lifecycle_join.STATUSES)
assert set(SEAT_CLASSES) | {"closed_full_roster"} == set(attempt_history.CLASSES)
assert attempt_report.SOURCES[0] == "sealed" and set(UNSEALED) <= set(LIFECYCLE)


def _add(found: dict[str, int], code: str, count: int | None) -> None:
    assert code in ACTIONS
    assert count is None or (type(count) is int and count >= 0)
    if count:
        found[code] = found.get(code, 0) + count


def _base(packet: Record, requested: bool) -> dict[str, int]:
    found: dict[str, int] = {}
    attempts = packet["attempts"]
    _add(found, "slot_without_result", packet["registered"] - len(attempts))
    for grade in ("invalid", "unknown"):
        _add(
            found,
            f"grade_{grade}",
            sum(a["grade_state"] == grade for a in attempts),
        )
    _add(found, "result_without_roster", sum(not a["seats"] for a in attempts))
    _add(
        found,
        "result_without_seat_results",
        sum(any(s["under_test"] is None for s in a["seats"]) for a in attempts),
    )
    _add(found, "incomplete_no_registered_slots", int(packet["registered"] == 0))
    _add(
        found,
        "incomplete_native_retries_unreported",
        int(requested and packet["retries"] is None),
    )
    return found


def _envelope(job: Path, packet: Record) -> None:
    job_id, registered, retries, closed, _ = native_account._root(str(job))
    _require(
        (job_id, registered, retries, closed)
        == (packet["scope"], packet["registered"], packet["retries"], True),
        "job result does not match the closed native packet",
    )


def _finals_in_archive(
    found: dict[str, int],
    packet: Record,
    where: dict[str, str],
    rosters: dict[str, list[str] | None],
) -> None:
    assert where.keys() == rosters.keys()
    for final in packet["attempts"]:
        slot = where.get(final["trial"])
        if slot is None:
            _add(found, "seat_journal_final_missing", 1)
            continue
        _add(found, "seat_journal_final_slot_mismatch", int(slot != final["slot"]))
        declared = sorted(seat["name"] for seat in final["seats"])
        header = rosters[final["trial"]]
        mismatch = bool(declared) and header is not None and header != declared
        _add(found, "seat_journal_final_roster_mismatch", int(mismatch))


def _lifecycle(found: dict[str, int], coverage: Record) -> None:
    for status, code in LIFECYCLE.items():
        _add(found, code, coverage["counts"][status])
    _add(
        found, "handoff_staging_unpublished", coverage["skipped"]["staging_unpublished"]
    )


def _seats(
    found: dict[str, int],
    packet: Record,
    seats: Path,
    witness: Path | None,
    handoffs: Path | None,
) -> dict[str, str]:
    report, rosters = attempt_history.closed_history(
        packet["scope"],
        packet["registered"],
        packet["retries"],
        str(seats),
        witness=None if witness is None else str(witness),
        handoffs=None if handoffs is None else str(handoffs),
    )
    assert report["job"] == packet["scope"] and report["finalized"] is True
    _add(found, "seat_journal_missing", report["missing_executions"])
    _add(
        found,
        "seat_slot_without_journal",
        report["registered_slots"] - report["observed_slots"],
    )
    for name, code in SEAT_CLASSES.items():
        _add(found, code, report["classifications"][name])
    where = {
        attempt: slot["slot_key"]
        for slot in report["slots"]
        for attempt in slot["attempts"]
    }
    _finals_in_archive(found, packet, where, rosters)
    if witness is not None:
        for name, code in WITNESS.items():
            _add(found, code, report["witness"][name])
    if handoffs is not None:
        _lifecycle(found, report["handoff_coverage"])
    assert report["complete"] or found
    return where


def _handoffs(found: dict[str, int], packet: Record, handoffs: Path) -> None:
    record = attempt_report.report(packet, str(handoffs))
    executions, captures = record["executions"], record["captures"]
    assert record["job_closed"] is True
    _add(
        found,
        "handoff_receipt_missing",
        captures["packet_attempts"]["uncaptured"] + (executions["unaccounted"] or 0),
    )
    if executions["over_expected"]:
        _add(
            found,
            "handoff_executions_over_expected",
            executions["known"] - executions["expected"],
        )
    for source, code in UNSEALED.items():
        _add(found, code, record["journal_evidence"][source])
    _add(
        found,
        "handoff_receipt_slot_without_final",
        captures["other_without_packet_attempt"],
    )
    _add(found, "handoff_staging_unpublished", record["skipped"]["staging_unpublished"])
    assert record["coverage_complete"] or found


def _accounts(
    found: dict[str, int], packet: Record, accounts: Path, *, slots: bool
) -> dict[str, str] | None:
    executions: dict[str, str] | None = None
    if slots:
        history, executions = account_history.history_slots(packet, str(accounts))
    else:
        history = account_history.history(packet, str(accounts))
    nonfinal, expected = history["nonfinal"], history["expected_nonfinal"]
    assert history["job_closed"] is True and nonfinal["other_unordered_slot"] == 0
    for state, code in ACCOUNT_FINALS.items():
        _add(found, code, history["finals"][state])
    for state, code in ACCOUNT_NONFINAL.items():
        _add(found, code, nonfinal[state])
    superseded = (
        nonfinal["superseded_confirmed_slot"] + nonfinal["superseded_unconfirmed_slot"]
    )
    if expected is None:
        _add(found, "incomplete_account_retry_coverage", 1)
    else:
        _add(found, "account_attempt_missing", max(expected - superseded, 0))
        _add(found, "account_attempts_over_expected", max(superseded - expected, 0))
    usd = history["harness_usd"]
    _add(
        found,
        "account_attempt_unpriced",
        usd["attempts"] - usd["attempts_fully_priced"],
    )
    _add(
        found, "account_staging_unpublished", history["skipped"]["staging_unpublished"]
    )
    assert history["complete"] or found
    return executions


def _starts(
    found: dict[str, int],
    packet: Record,
    starts: Path,
    *,
    seat_slots: dict[str, str] | None,
    account_slots: dict[str, str] | None,
) -> None:
    counts = attention_starts.findings(
        packet["scope"],
        packet["registered"],
        packet["retries"],
        {e["trial"]: e["slot"] for e in packet["attempts"]},
        starts,
        seat_slots=seat_slots,
        account_slots=account_slots,
    )
    for code, count in counts.items():
        _add(found, code, count)


def analyze(
    job: Path,
    packet: Record,
    *,
    seats: Path | None,
    handoffs: Path | None,
    accounts: Path | None,
    witness: Path | None,
    starts: Path | None = None,
) -> dict[str, int]:
    assert isinstance(job, Path)
    sources = (seats, handoffs, accounts, witness, starts)
    assert all(p is None or isinstance(p, Path) for p in sources)
    _require(witness is None or seats is not None, "a witness needs a seat archive")
    final = validate(packet)
    _require(final["source"] == SOURCE, "packet is not a native-job account")
    _require(final["job_closed"] is True, "native job is not closed")
    _envelope(job, final)
    requested = any(p is not None for p in (seats, handoffs, accounts, starts))
    found = _base(final, requested)
    seat_slots: dict[str, str] | None = None
    account_slots: dict[str, str] | None = None
    if seats is not None:
        seat_slots = _seats(found, final, seats, witness, handoffs)
    elif handoffs is not None:
        _handoffs(found, final, handoffs)
    if accounts is not None:
        slots = starts is not None
        account_slots = _accounts(found, final, accounts, slots=slots)
    if starts is not None:
        _starts(
            found, final, starts, seat_slots=seat_slots, account_slots=account_slots
        )
    result = {code: found[code] for code in ACTIONS if code in found}
    assert result.keys() == found.keys()
    assert all(type(n) is int and n > 0 for n in result.values())
    return result
