import itertools
from collections import Counter
from datetime import datetime
from typing import Any
from uuid import UUID

from .cohort_draw import CELLS as CONDITIONS
from .contrast import CASES_MAX, CASES_MIN, CASES_STEP, check_cell

TRACE_KEYS = ("native_trial_id", "native_started_at", "native_finished_at")
ORDER_KEYS = ("not_comparable", "partial_consistent", "complete_consistent", "conflict")


def aware(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("native timestamp must be an ISO string")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("native timestamp is not ISO 8601") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("native timestamp must be timezone aware")
    return moment


def canonical_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("native trial id must be a string")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError("native trial id is not a UUID") from error
    if str(parsed) != value:
        raise ValueError("native trial id must be canonical")
    return value


def check_report(report: dict[str, Any]) -> tuple[str, datetime, datetime] | None:
    status, _ = check_cell(
        {"status": report["status"], "native_reward": report["native_reward"]}
    )
    if status not in ("complete", "incomplete"):
        if any(key in report for key in TRACE_KEYS):
            raise ValueError("unrecorded report with trial identity")
        return None
    started = aware(report["native_started_at"])
    finished = aware(report["native_finished_at"])
    if finished < started:
        raise ValueError("negative native interval")
    return canonical_id(report["native_trial_id"]), started, finished


def check_scope(
    orders: dict[str, list[str]], reports: dict[str, dict[str, dict[str, Any]]]
) -> None:
    if set(orders) != set(reports):
        raise ValueError("orders and reports must cover the same cases")
    if not CASES_MIN <= len(orders) <= CASES_MAX or len(orders) % CASES_STEP:
        raise ValueError("case count must be 4..64")
    for case, order in orders.items():
        if not isinstance(case, str):
            raise TypeError("case id must be a string")
        if (
            not isinstance(order, list)
            or len(order) != len(CONDITIONS)
            or set(order) != set(CONDITIONS)
        ):
            raise ValueError("order must list each condition exactly once")
        if not isinstance(reports[case], dict) or set(reports[case]) != set(CONDITIONS):
            raise ValueError("case must report exactly the four conditions")


def order_class(intervals: list[tuple[datetime, datetime]]) -> str:
    assert len(intervals) <= len(CONDITIONS)
    if len(intervals) < 2:
        return "not_comparable"
    for (start, finish), (next_start, _) in itertools.pairwise(intervals):
        if finish > next_start or start == next_start:
            return "conflict"
    if len(intervals) == len(CONDITIONS):
        return "complete_consistent"
    return "partial_consistent"


def reconcile(
    orders: dict[str, list[str]], reports: dict[str, dict[str, dict[str, Any]]]
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    check_scope(orders, reports)
    traces = {
        case: {name: check_report(reports[case][name]) for name in CONDITIONS}
        for case in orders
    }
    uses = Counter(
        trace[0]
        for cells in traces.values()
        for trace in cells.values()
        if trace is not None
    )
    reused = frozenset(trial for trial, count in uses.items() if count > 1)
    assert all(uses[trial] >= 2 for trial in reused)
    result: dict[str, dict[str, dict[str, Any]]] = {}
    counts = dict.fromkeys(ORDER_KEYS, 0)
    reused_cells = 0
    for case, order in orders.items():
        cells: dict[str, dict[str, Any]] = {}
        intervals: list[tuple[datetime, datetime]] = []
        for name in order:
            trace = traces[case][name]
            if trace is not None and trace[0] in reused:
                cells[name] = {
                    "status": "inconsistent_or_unreadable",
                    "native_reward": None,
                }
                reused_cells += 1
                continue
            cells[name] = dict(reports[case][name])
            if trace is not None:
                intervals.append((trace[1], trace[2]))
        result[case] = {name: cells[name] for name in CONDITIONS}
        counts[order_class(intervals)] += 1
    assert sum(counts.values()) == len(orders)
    assert reused_cells == sum(uses[trial] for trial in reused)
    summary = {
        "duplicated_trial_ids": len(reused),
        "cells_with_reused_trial_ids": reused_cells,
        "recorded_order_counts": counts,
    }
    return result, summary
