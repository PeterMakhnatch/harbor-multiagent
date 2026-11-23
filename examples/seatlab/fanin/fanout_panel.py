"""Preregistered gather-time pass test over native serial and parallel fan-out rows."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from fractions import Fraction
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import panel, protocol
from .panel import Rejected, require

ENVELOPE = "seatlab.fanout-rows/v1"
ROW = panel.ROW
OUTPUT = "seatlab.fanout-panel/v1"
WORLDS = tuple(range(1208, 1216))
ARMS = ("serial", "parallel")
LIVE = panel.LIVE
CONTROLS = {
    (1208, "serial", "control-correct"): 1,
    (1208, "parallel", "control-correct"): 1,
    (1209, "parallel", "control-wrong"): 0,
}
SCREEN_CALENDARS = {
    1208: "2711d353cf0cdf8e39b56eb82ff0d0b76047e68d0a0cf4116179c7a19c95242f",
    1209: "6262dc254f74cebc83a523a815c1c2518f2cb7747986d508b314127f6210e8fc",
    1210: "b73d69f643404bb244e66bda6906b5ee4ab370fbabb1067318406d74a1928ccc",
    1211: "ed094d360b5eb3bdc136964216351b9cc4e6074d79bf8982cc8fcfe699a6160d",
    1212: "b9b5d152af0490ad101d7e5fcbfaeff08002328b66112091b8dd48feb354f3a5",
    1213: "83238fa8d7547bf7e4293d4710b336f39eaec3ec4751b3752d2e9f8bab3e7fc6",
    1214: "af2d92789f2c2f29cf74cfd934097540e10297d48d8fec89c6e8b93cc0ad72b5",
    1215: "92f7afa8168b0007f53cde234e761501df523744696774373d3d8826d2a6f5d6",
}
TIMING_KEYS = frozenset(
    (
        "gather_ms",
        "submit_ms",
        "before_first_reply",
        "max_outstanding",
        "native_started_at",
        "native_finished_at",
    )
)
KEYS = panel.KEYS | {"timing"}
REPORTERS = len(protocol.SEATS) - 1
MAX_LIVE = len(WORLDS) * len(ARMS)
MAX_MS = 1 << 40
MIN_REDUCTION = Fraction(1, 4)
MIN_FASTER = 6
MIN_EARLY = 6
MIN_REQUESTS = 2
MS = timedelta(milliseconds=1)
Row = dict[str, Any]


def arm_order(world: int) -> tuple[str, str]:
    require(world in WORLDS, "world_outside_panel")
    return ARMS if world % 2 == 0 else (ARMS[1], ARMS[0])


def _identity(row: Row, live: bool) -> tuple[str, str, str, str]:
    require(row["schema"] == ROW, "row_schema")
    world, arm, tag = row["world"], row["arm"], row["tag"]
    require(type(world) is int and world in WORLDS and arm in ARMS, "world_or_arm")
    if live:
        require(tag == LIVE, "live_control_mixed")
    else:
        require((world, arm, tag) in CONTROLS, "control_outside_registration")
    roles = protocol.order(world)
    require(
        row["booker"] == roles[0] and row["reporters"] == list(roles[1:]), "role_order"
    )
    require(
        panel._trial(row["trial_id"]) and type(row["complete"]) is bool,
        "trial_or_complete",
    )
    require(
        row["native_reward"] is None or panel._count(row["native_reward"], 0, 1),
        "native_reward",
    )
    require(panel._optional_text(row["incomplete_reason"]), "incomplete_reason")
    require(
        row["complete"] or row["incomplete_reason"] is not None,
        "incomplete_without_reason",
    )
    require(
        panel._count(row["reporter_turns"], 0, REPORTERS)
        and panel._count(row["booker_turns"], 0, 1),
        "turn_counts",
    )
    require(row["all_reports_exact"] in (None, True, False), "all_reports_exact")
    designed = row["control_as_designed"]
    require(
        designed is None if live else designed in (None, True, False),
        "control_as_designed",
    )
    require(isinstance(row["claims"], dict), "claims")
    require(
        type(row["notes"]) is list and all(type(n) is str for n in row["notes"]),
        "notes",
    )
    return roles


def _source(row: Row) -> None:
    source = panel._fields(row["source"], panel.SOURCE_KEYS, "source")
    require(
        source["generator_commit"] == panel.COMMIT and source["seed"] == panel.SEED,
        "source_registration",
    )
    result = row["trial_id"] is not None
    checksum = source["task_checksum"]
    require(
        panel._digest(checksum) or (checksum is None and not result), "task_checksum"
    )
    calendar = source["calendar_sha256"]
    require(panel._digest(calendar) if result else calendar is None, "calendar_sha256")
    keys = panel.INPUT_KEYS if result else {"launch"}
    inputs = panel._fields(row["inputs"], keys, "inputs")
    require(panel._digest(inputs["launch"]), "launch_digest")
    require(
        all(
            panel._digest(v) or (v is None and not row["complete"])
            for v in inputs.values()
        ),
        "evidence_digests",
    )


def _moment(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is not str or len(value) > 64:
        raise Rejected("timing_moment")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise Rejected("timing_moment") from None
    require(parsed.utcoffset() == timedelta(0), "timing_not_utc")
    return parsed


def _timing(row: Row) -> Row:
    if row["trial_id"] is None:
        require(row["timing"] is None, "timing_without_result")
        return dict.fromkeys(
            (*sorted(TIMING_KEYS), "native_wall_ms", "start", "finish")
        )
    timing = panel._fields(row["timing"], TIMING_KEYS, "timing_fields")
    gather, submit = timing["gather_ms"], timing["submit_ms"]
    early, peak = timing["before_first_reply"], timing["max_outstanding"]
    asked = len(row["reporters_asked"])
    if row["complete"]:
        require(None not in timing.values(), "complete_without_timing")
    require(panel._count(peak, 0, asked), "timing_outstanding")
    require(early is None or panel._count(early, 1, peak), "timing_first_reply")
    require(gather is None or panel._count(gather, 0, MAX_MS), "timing_gather")
    require(
        gather is None or (asked == REPORTERS and early is not None),
        "gather_without_replies",
    )
    floor = 0 if gather is None else gather
    require(
        submit is None or (panel._count(submit, floor, MAX_MS) and peak >= 1),
        "timing_submit",
    )
    if row["arm"] == "serial":
        require(peak <= 1, "serial_overlapped")
    start = _moment(timing["native_started_at"])
    finish = _moment(timing["native_finished_at"])
    wall = None
    if start is not None and finish is not None:
        require(start <= finish, "native_interval")
        wall = (finish - start) // MS
    return timing | {"native_wall_ms": wall, "start": start, "finish": finish}


def _row(raw: object, live: bool) -> tuple[Row, Row]:
    row = panel._fields(raw, KEYS, "row_fields")
    roles = _identity(row, live)
    _source(row)
    if row["trial_id"] is None:
        panel._no_result(row)
        return row, _timing(row)
    turns = row["turns"]
    require(type(turns) is list and len(turns) == len(roles), "turns")
    checked = [panel._turn(turns[i], i, roles[i], live) for i in range(len(roles))]
    asked, skipped = row["reporters_asked"], row["reporters_skipped"]
    require(type(asked) is list and skipped == [], "fanout_partition")
    require(asked == row["reporters"][: len(asked)], "asked_not_registered_prefix")
    if row["complete"]:
        panel._complete(row, asked, checked)
    panel._sessions(row, checked, live)
    return row, _timing(row)


def _ingest(rows: object, live: bool, seen: Row) -> dict[tuple[int, str], Row]:
    limit = MAX_LIVE if live else len(CONTROLS)
    if not isinstance(rows, list) or len(rows) > limit:
        raise Rejected("rows_bound")
    cells: dict[tuple[int, str], Row] = {}
    for raw in rows:
        row, timing = _row(raw, live)
        key = (row["world"], row["arm"])
        require(key not in cells, "duplicate_cell")
        require(
            row["source"]["calendar_sha256"] in (None, SCREEN_CALENDARS[row["world"]]),
            "calendar_differs_from_screen",
        )
        trial = row["trial_id"]
        require(trial is None or trial not in seen["trials"], "trial_reused")
        if trial is not None:
            seen["trials"].add(trial)
        for digest in row["session_sha256s"] or ():
            require(digest not in seen["sessions"], "session_reused_across_cells")
            seen["sessions"].add(digest)
        cells[key] = {"row": row, "timing": timing}
    return cells


def _rank(row: Row) -> tuple[int, int]:
    world = row["world"]
    return WORLDS.index(world), arm_order(world).index(row["arm"])


def _schedule(
    live: dict[tuple[int, str], Row], controls: dict[tuple[int, str], Row]
) -> list[Row]:
    cells = (*live.values(), *controls.values())
    spans = [
        (c["timing"]["start"], c["timing"]["finish"], c["row"]["tag"] == LIVE, c["row"])
        for c in cells
        if c["timing"]["native_wall_ms"] is not None
    ]
    unknown = [
        {"world": c["row"]["world"], "arm": c["row"]["arm"], "tag": c["row"]["tag"]}
        for c in cells
        if c["row"]["trial_id"] is not None and c["timing"]["native_wall_ms"] is None
    ]
    spans.sort(key=lambda span: span[0])
    for before, after in pairwise(spans):
        require(before[1] <= after[0], "trials_overlap")
    live_starts = [s[0] for s in spans if s[2]]
    control_ends = [s[1] for s in spans if not s[2]]
    require(
        not live_starts or not control_ends or max(control_ends) <= min(live_starts),
        "control_after_live",
    )
    ranks = [_rank(s[3]) for s in spans if s[2]]
    require(ranks == sorted(ranks), "world_or_arm_order_violated")
    return unknown


def _cell(cell: Row) -> Row:
    timing = {k: v for k, v in cell["timing"].items() if k not in ("start", "finish")}
    return panel._cell(cell["row"]) | timing


def _pair(world: int, serial: Row | None, parallel: Row | None) -> Row:
    pair: Row = {
        "world": world,
        "arm_order": list(arm_order(world)),
        "serial": serial,
        "parallel": parallel,
        "gather_delta_ms": None,
        "submit_delta_ms": None,
        "gather_reduction": None,
        "parallel_faster": None,
    }
    if serial is None or parallel is None:
        return pair | {"category": "missing"}
    if not (serial["authenticated"] and parallel["authenticated"]):
        return pair | {"category": "unaccounted"}
    require(serial["gather_ms"] > 0, "serial_gather_zero")
    reduction = 1 - Fraction(parallel["gather_ms"], serial["gather_ms"])
    return pair | {
        "category": f"serial{serial['native_reward']}_parallel{parallel['native_reward']}",
        "gather_delta_ms": parallel["gather_ms"] - serial["gather_ms"],
        "submit_delta_ms": parallel["submit_ms"] - serial["submit_ms"],
        "gather_reduction": reduction,
        "parallel_faster": parallel["gather_ms"] < serial["gather_ms"],
    }


def _median(values: list[Fraction]) -> Fraction | None:
    if not values:
        return None
    ordered, middle = sorted(values), len(values) // 2
    if len(values) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _controls(controls: dict[tuple[int, str], Row]) -> tuple[list[Row], list[str]]:
    rows, reasons = [], []
    for (world, arm, tag), expected in CONTROLS.items():
        cell = controls.get((world, arm))
        row = None if cell is None else cell["row"]
        matched = (
            row is not None
            and row["tag"] == tag
            and row["complete"]
            and row["native_reward"] == expected
            and row["control_as_designed"] is True
        )
        if not matched:
            reasons.append(f"control_{world}_{arm}_{tag}_not_matched")
        rows.append(
            {
                "world": world,
                "arm": arm,
                "tag": tag,
                "expected_reward": expected,
                "present": row is not None,
                "complete": None if row is None else row["complete"],
                "native_reward": None if row is None else row["native_reward"],
                "control_as_designed": None
                if row is None
                else row["control_as_designed"],
                "matched": matched,
                **({} if cell is None else _cell(cell)),
            }
        )
    return rows, reasons


def _stats(pairs: list[Row]) -> Row:
    scored = [p for p in pairs if p["gather_reduction"] is not None]
    median = _median([p["gather_reduction"] for p in scored])
    return {
        "complete_pairs": len(scored),
        "median_gather_reduction": median,
        "parallel_faster_worlds": sum(p["parallel_faster"] for p in scored),
        "parallel_early_requests_worlds": sum(
            p["parallel"]["before_first_reply"] >= MIN_REQUESTS for p in scored
        ),
        "serial1_parallel0_worlds": [
            p["world"] for p in scored if p["category"] == "serial1_parallel0"
        ],
    }


def _verdict(
    pairs: list[Row], stats: Row, control_reasons: list[str]
) -> tuple[str, list[str]]:
    missing = [
        f"world_{p['world']}_{p['category']}"
        for p in pairs
        if p["category"] in ("missing", "unaccounted")
    ]
    if missing or control_reasons:
        return "inconclusive", missing + control_reasons
    reasons = []
    if stats["median_gather_reduction"] < MIN_REDUCTION:
        reasons.append("median_gather_reduction_below_0.25")
    if stats["parallel_faster_worlds"] < MIN_FASTER:
        reasons.append("parallel_faster_in_fewer_than_6_worlds")
    if stats["parallel_early_requests_worlds"] < MIN_EARLY:
        reasons.append("two_requests_before_first_reply_in_fewer_than_6_worlds")
    if stats["serial1_parallel0_worlds"]:
        reasons.append("serial_correct_parallel_wrong")
    return ("fail" if reasons else "pass"), reasons


def _ratio(value: Fraction | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _rewards(cells: list[Row], arm: str) -> Row:
    chosen = [c for c in cells if c["arm"] == arm]
    counts = {str(v): sum(c["native_reward"] == v for c in chosen) for v in (0, 1)}
    return counts | {"null": sum(c["native_reward"] is None for c in chosen)}


def summarize(envelope: object) -> Row:
    body = panel._fields(envelope, {"schema", "rows", "controls"}, "envelope_fields")
    require(body["schema"] == ENVELOPE, "envelope_schema")
    seen: Row = {"trials": set(), "sessions": set()}
    live = _ingest(body["rows"], True, seen)
    controls = _ingest(body["controls"], False, seen)
    unknown_windows = _schedule(live, controls)
    cells = {key: _cell(cell) for key, cell in live.items()}
    pairs = [
        _pair(world, cells.get((world, "serial")), cells.get((world, "parallel")))
        for world in WORLDS
    ]
    stats = _stats(pairs)
    control_rows, control_reasons = _controls(controls)
    verdict, reasons = _verdict(pairs, stats, control_reasons)
    for pair in pairs:
        pair["gather_reduction"] = _ratio(pair["gather_reduction"])
    listed = list(cells.values())
    complete = [c for c in listed if c["authenticated"]]
    return {
        "schema": OUTPUT,
        "verdict": verdict,
        "reasons": reasons,
        "accounted_cells": sum(c["authenticated"] for c in listed),
        "required_cells": MAX_LIVE,
        "missing": [
            {"world": w, "arm": a}
            for w in WORLDS
            for a in arm_order(w)
            if (w, a) not in cells
        ],
        "unknown_native_windows": unknown_windows,
        "unaccounted": [
            {"world": c["world"], "arm": c["arm"], "reason": c["unaccounted_reason"]}
            for c in listed
            if not c["authenticated"]
        ],
        "statistics": stats
        | {"median_gather_reduction": _ratio(stats["median_gather_reduction"])},
        "thresholds": {
            "median_gather_reduction": float(MIN_REDUCTION),
            "parallel_faster_worlds": MIN_FASTER,
            "parallel_early_requests_worlds": MIN_EARLY,
            "requests_before_first_reply": MIN_REQUESTS,
        },
        "native_grades_all_first_attempts": {a: _rewards(listed, a) for a in ARMS},
        "native_grades_complete_cells": {a: _rewards(complete, a) for a in ARMS},
        "complete_cell_totals": {a: panel._totals(complete, a) for a in ARMS},
        "observed_first_attempt_totals": {a: panel._totals(listed, a) for a in ARMS},
        "pairs": pairs,
        "controls": control_rows,
        "claims": {
            "service_speed": False,
            "model_concurrency": False,
            "cost": False,
            "billing": False,
            "throughput": False,
        },
        "scope": "Local one-at-a-time native trials over worlds 1208-1215; intervals are stock-service gather times, not provider speed or cost; native_reward null means no verified grade and is never counted as 0.",
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: python -BS -m examples.seatlab.fanin.fanout_panel ROWS.json",
            file=sys.stderr,
        )
        return 2
    try:
        summary = summarize(panel.load(Path(args[0])))
    except Rejected as rejected:
        print(f"fanout panel: {rejected}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError) as error:
        print(f"fanout panel: unreadable_input {type(error).__name__}", file=sys.stderr)
        return 2
    print(protocol.wire(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
