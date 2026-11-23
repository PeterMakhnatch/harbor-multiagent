"""Preregistered fan-in pass test over released native evidence rows and separate controls."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from . import protocol

ENVELOPE = "seatlab.peer-fanin-rows/v1"
ROW = "seatlab.peer-fanin-evidence/v1"
OUTPUT = "seatlab.peer-fanin-panel/v1"
WORLDS = tuple(range(protocol.FIRST, protocol.FIRST + 8))
COMMIT = "71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3"
SEED = 1709000311
LIVE = "live"
CONTROL_TAGS = ("control-correct", "control-inexact", "control-wrong")
STATUSES = ("absent", "not_admitted", "unanswered", "bound", "answered_unused")
MAX_BYTES = 4 << 20
MAX_LIVE = len(WORLDS) * len(protocol.ARMS)
MAX_CONTROLS = MAX_LIVE * len(CONTROL_TAGS)
MIN_SAVED = 2
HEX = re.compile(r"[0-9a-f]{64}")
SOURCE_KEYS = frozenset(
    ("generator_commit", "seed", "task_checksum", "calendar_sha256")
)
INPUT_KEYS = frozenset(
    ("launch", "config", "native_result", "messages", "events", "world_state")
)
KEYS = frozenset(
    (
        "schema",
        "world",
        "arm",
        "tag",
        "trial_id",
        "complete",
        "incomplete_reason",
        "native_reward",
        "booker",
        "reporters",
        "reporter_turns",
        "booker_turns",
        "model_sessions",
        "session_sha256s",
        "client_tokens",
        "reporters_asked",
        "reporters_skipped",
        "all_reports_exact",
        "control_as_designed",
        "turns",
        "claims",
        "inputs",
        "source",
        "notes",
    )
)
TURN_KEYS = frozenset(
    ("seat", "role", "position", "status", "session_sha256", "total_tokens")
)


class Rejected(ValueError):
    pass


def require(test: bool, reason: str) -> None:
    if not test:
        raise Rejected(reason)


def _fields(
    value: object, keys: frozenset[str] | set[str], reason: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Rejected(reason)
    return cast("dict[str, Any]", value)


def arm_order(world: int) -> tuple[str, str]:
    require(world in WORLDS, "world_outside_panel")
    first, second = protocol.ARMS
    return (first, second) if (world - protocol.FIRST) % 2 == 0 else (second, first)


def _count(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _optional_text(value: object) -> bool:
    return value is None or type(value) is str


def _digest(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _trial(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.version == 4


def _turn(raw: object, position: int, seat: str, live: bool) -> dict[str, Any]:
    turn = _fields(raw, TURN_KEYS, "turn_fields")
    require(
        turn["position"] == position and type(turn["position"]) is int, "turn_position"
    )
    require(turn["seat"] == seat, "turn_seat")
    require(turn["role"] == ("booker" if position == 0 else "reporter"), "turn_role")
    require(turn["status"] in STATUSES, "turn_status")
    digest, tokens = turn["session_sha256"], turn["total_tokens"]
    require(
        turn["status"] != "not_admitted" or (digest is None and tokens is None),
        "not_admitted_with_session",
    )
    if not live:
        require(digest is None and tokens is None, "control_claims_session_or_tokens")
        return turn
    require(digest is None or _digest(digest), "session_digest")
    require((digest is None) == (tokens is None), "session_without_tokens")
    require(tokens is None or _count(tokens, 1, 1 << 40), "turn_tokens")
    require(
        digest is None or turn["status"] in ("bound", "answered_unused"),
        "session_on_unopened_turn",
    )
    require(
        turn["status"] != "answered_unused" or digest is not None,
        "answered_turn_without_session",
    )
    return turn


def _partition(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    reporters, asked, skipped = (
        row["reporters"],
        row["reporters_asked"],
        row["reporters_skipped"],
    )
    require(type(asked) is list and type(skipped) is list, "partition_type")
    require(asked == reporters[: len(asked)], "asked_not_registered_prefix")
    require(
        skipped == reporters[len(asked) : len(asked) + len(skipped)],
        "skipped_out_of_order",
    )
    require(row["arm"] == "stop" or not skipped, "all_arm_skipped")
    require(
        not (skipped and asked) or row["all_reports_exact"] is True,
        "skip_without_exact",
    )
    return asked, skipped


def _complete(
    row: dict[str, Any], asked: list[str], turns: list[dict[str, Any]]
) -> None:
    require(
        len(asked) + len(row["reporters_skipped"]) == len(protocol.SEATS) - 1,
        "partition_incomplete",
    )
    require(row["booker_turns"] == 1, "booker_turns")
    require(row["reporter_turns"] == len(asked), "reporter_turns")
    for turn in turns:
        wanted = "bound" if turn["position"] == 0 or turn["seat"] in asked else "absent"
        require(turn["status"] == wanted, "complete_turn_status")
    require(row["incomplete_reason"] is None, "complete_with_reason")


def _sessions(row: dict[str, Any], turns: list[dict[str, Any]], live: bool) -> None:
    digests = [t["session_sha256"] for t in turns if t["session_sha256"] is not None]
    require(len(digests) == len(set(digests)), "session_reused_in_cell")
    if not live:
        require(
            row["model_sessions"] == 0 and row["session_sha256s"] == [],
            "control_sessions",
        )
        require(row["client_tokens"] is None, "control_claims_tokens")
        return
    if row["model_sessions"] is None:
        require(
            row["session_sha256s"] is None and row["client_tokens"] is None,
            "partial_validation",
        )
        return
    require(_count(row["model_sessions"], 0, len(protocol.SEATS)), "model_sessions")
    require(row["model_sessions"] == len(digests), "model_sessions_mismatch")
    require(row["session_sha256s"] == sorted(digests), "session_list_mismatch")
    total = sum(t["total_tokens"] for t in turns if t["total_tokens"] is not None)
    require(row["client_tokens"] == total, "client_tokens_mismatch")
    bound = [t["session_sha256"] is not None for t in turns]
    require(
        not row["complete"] or bound == [t["status"] == "bound" for t in turns],
        "complete_session_turns",
    )


def _no_result(row: dict[str, Any]) -> dict[str, Any]:
    require(
        not row["complete"] and row["native_reward"] is None, "no_result_with_grade"
    )
    absent = (
        "turns",
        "reporters_asked",
        "reporters_skipped",
        "all_reports_exact",
        "model_sessions",
        "session_sha256s",
        "client_tokens",
    )
    require(all(row[key] is None for key in absent), "no_result_with_evidence")
    return row


def _header(
    raw: object, live: bool
) -> tuple[dict[str, Any], tuple[str, str, str, str]]:
    row = _fields(raw, KEYS, "row_fields")
    require(row["schema"] == ROW, "row_schema")
    world, arm, tag = row["world"], row["arm"], row["tag"]
    require(
        type(world) is int and world in WORLDS and arm in protocol.ARMS, "world_or_arm"
    )
    require(tag == LIVE if live else tag in CONTROL_TAGS, "live_control_mixed")
    roles = protocol.order(world)
    require(
        row["booker"] == roles[0] and row["reporters"] == list(roles[1:]), "role_order"
    )
    require(
        _trial(row["trial_id"]) and type(row["complete"]) is bool, "trial_or_complete"
    )
    require(
        row["native_reward"] is None or _count(row["native_reward"], 0, 1),
        "native_reward",
    )
    require(_optional_text(row["incomplete_reason"]), "incomplete_reason")
    require(
        row["complete"] or row["incomplete_reason"] is not None,
        "incomplete_without_reason",
    )
    require(
        _count(row["reporter_turns"], 0, 3) and _count(row["booker_turns"], 0, 1),
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
    source = _fields(row["source"], SOURCE_KEYS, "source")
    require(
        source["generator_commit"] == COMMIT and source["seed"] == SEED,
        "source_registration",
    )
    result = row["trial_id"] is not None
    checksum = source["task_checksum"]
    require(type(checksum) is str or (checksum is None and not result), "task_checksum")
    calendar = source["calendar_sha256"]
    require(_digest(calendar) if result else calendar is None, "calendar_sha256")
    inputs = _fields(row["inputs"], INPUT_KEYS if result else {"launch"}, "inputs")
    require(_digest(inputs["launch"]), "launch_digest")
    require(
        all(_digest(v) or (v is None and not row["complete"]) for v in inputs.values()),
        "evidence_digests",
    )
    return row, roles


def _row(raw: object, live: bool) -> dict[str, Any]:
    row, roles = _header(raw, live)
    if row["trial_id"] is None:
        return _no_result(row)
    turns = row["turns"]
    require(type(turns) is list and len(turns) == len(roles), "turns")
    checked = [_turn(turns[i], i, roles[i], live) for i in range(len(roles))]
    asked, _ = _partition(row)
    if row["complete"]:
        _complete(row, asked, checked)
    _sessions(row, checked, live)
    return row


def _authenticated(row: dict[str, Any]) -> bool:
    return (
        row["complete"]
        and row["trial_id"] is not None
        and row["native_reward"] is not None
        and row["model_sessions"] is not None
    )


def _missing_reason(row: dict[str, Any]) -> str | None:
    if not row["complete"]:
        return row["incomplete_reason"]
    if row["native_reward"] is None:
        return "no_verified_grade"
    return None if row["model_sessions"] is not None else "sessions_unvalidated"


def _cell(row: dict[str, Any]) -> dict[str, Any]:
    by_role = {
        "booker": {"sessions": 0, "tokens": 0},
        "reporter": {"sessions": 0, "tokens": 0},
    }
    for turn in row["turns"] or ():
        if turn["session_sha256"] is not None:
            by_role[turn["role"]]["sessions"] += 1
            by_role[turn["role"]]["tokens"] += turn["total_tokens"]
    validated = row["model_sessions"] is not None
    return {
        "world": row["world"],
        "arm": row["arm"],
        "trial_id": row["trial_id"],
        "complete": row["complete"],
        "authenticated": _authenticated(row),
        "unaccounted_reason": None if _authenticated(row) else _missing_reason(row),
        "native_reward": row["native_reward"],
        "reporters_asked": row["reporters_asked"],
        "reporters_skipped": row["reporters_skipped"],
        "booker_turns": row["booker_turns"],
        "reporter_turns": row["reporter_turns"],
        "sessions": by_role if validated else None,
        "client_tokens": row["client_tokens"],
    }


def _ingest(
    rows: list[Any], live: bool, seen_trials: set[str], calendars: dict[int, str]
) -> dict[tuple[int, str, str], dict[str, Any]]:
    cells: dict[tuple[int, str, str], dict[str, Any]] = {}
    sessions: set[str] = set()
    for raw in rows:
        row = _row(raw, live)
        key = (row["world"], row["arm"], row["tag"])
        require(key not in cells, "duplicate_cell")
        calendar = row["source"]["calendar_sha256"]
        if calendar is not None:
            known = calendars.setdefault(row["world"], calendar)
            require(known == calendar, "calendar_differs_within_world")
            require(
                list(calendars.values()).count(calendar) == 1,
                "calendar_repeats_across_worlds",
            )
        trial = row["trial_id"]
        require(trial is None or trial not in seen_trials, "trial_reused")
        if trial is not None:
            seen_trials.add(trial)
        for turn in row["turns"] or ():
            digest = turn["session_sha256"]
            require(
                digest is None or digest not in sessions, "session_reused_across_cells"
            )
            if digest is not None:
                sessions.add(digest)
        cells[key] = row
    return cells


def _pair(all_cell: dict[str, Any] | None, stop_cell: dict[str, Any] | None) -> str:
    if all_cell is None or stop_cell is None:
        return "missing"
    if not (all_cell["authenticated"] and stop_cell["authenticated"]):
        return "unaccounted"
    return f"all{all_cell['native_reward']}_stop{stop_cell['native_reward']}"


def _totals(cells: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    chosen = [c for c in cells if c["arm"] == arm and c["sessions"] is not None]
    total = {"validated_cells": len(chosen), "booker_turns": 0, "reporter_turns": 0}
    for role in ("booker", "reporter"):
        total[f"{role}_sessions"] = sum(c["sessions"][role]["sessions"] for c in chosen)
        total[f"{role}_tokens"] = sum(c["sessions"][role]["tokens"] for c in chosen)
    for cell in (c for c in cells if c["arm"] == arm):
        total["booker_turns"] += cell["booker_turns"]
        total["reporter_turns"] += cell["reporter_turns"]
    total["client_tokens"] = total["booker_tokens"] + total["reporter_tokens"]
    return total


def _grades(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts = {arm: {"0": 0, "1": 0, "null": 0} for arm in protocol.ARMS}
    for cell in cells:
        grade = cell["native_reward"]
        counts[cell["arm"]]["null" if grade is None else str(grade)] += 1
    return counts


def _controls(cells: dict[tuple[int, str, str], dict[str, Any]]) -> dict[str, Any]:
    by_tag = {
        tag: {
            "cells": 0,
            "complete": 0,
            "as_designed": 0,
            "not_as_designed": 0,
            "unknown": 0,
        }
        for tag in CONTROL_TAGS
    }
    rows = []
    for (world, arm, tag), row in sorted(cells.items()):
        counts = by_tag[tag]
        counts["cells"] += 1
        counts["complete"] += int(row["complete"])
        flag = row["control_as_designed"]
        counts[
            "unknown" if flag is None else "as_designed" if flag else "not_as_designed"
        ] += 1
        rows.append(
            {
                "world": world,
                "arm": arm,
                "tag": tag,
                "complete": row["complete"],
                "native_reward": row["native_reward"],
                "control_as_designed": flag,
                "booker_turns": row["booker_turns"],
                "reporter_turns": row["reporter_turns"],
                "reporters_skipped": row["reporters_skipped"],
            }
        )
    return {"by_tag": by_tag, "rows": rows}


def _verdict(
    pairs: list[dict[str, Any]], all_total: dict[str, Any], stop_total: dict[str, Any]
) -> tuple[str, list[str], int | None]:
    accounted = all(p["category"] not in ("missing", "unaccounted") for p in pairs)
    if not accounted:
        return "inconclusive", ["missing_or_unauthenticated_first_attempt"], None
    saved = all_total["reporter_sessions"] - stop_total["reporter_sessions"]
    reasons = []
    if saved < MIN_SAVED:
        reasons.append("reporter_sessions_saved_below_2")
    if any(p["category"] == "all1_stop0" for p in pairs):
        reasons.append("stop_lost_a_pair_all_won")
    return ("no_benefit" if reasons else "pass"), reasons, saved


def summarize(envelope: object) -> dict[str, Any]:
    body = _fields(envelope, {"schema", "rows", "controls"}, "envelope_fields")
    require(body["schema"] == ENVELOPE, "envelope_schema")
    rows, controls = body["rows"], body["controls"]
    if not isinstance(rows, list) or len(rows) > MAX_LIVE:
        raise Rejected("rows_bound")
    if not isinstance(controls, list) or len(controls) > MAX_CONTROLS:
        raise Rejected("controls_bound")
    trials: set[str] = set()
    calendars: dict[int, str] = {}
    live = _ingest(rows, True, trials, calendars)
    control_cells = _ingest(controls, False, trials, calendars)
    cells = {(w, a): _cell(r) for (w, a, _), r in live.items()}
    pairs, missing = [], []
    for world in WORLDS:
        for arm in arm_order(world):
            if (world, arm) not in cells:
                missing.append({"world": world, "arm": arm})
        pair = {arm: cells.get((world, arm)) for arm in protocol.ARMS}
        pairs.append(
            {
                "world": world,
                "arm_order": list(arm_order(world)),
                **pair,
                "category": _pair(pair["all"], pair["stop"]),
            }
        )
    categories: dict[str, int] = {}
    for pair in pairs:
        categories[pair["category"]] = categories.get(pair["category"], 0) + 1
    observed = list(cells.values())
    complete = [c for c in observed if c["authenticated"]]
    totals = {arm: _totals(complete, arm) for arm in protocol.ARMS}
    verdict, reasons, saved = _verdict(pairs, totals["all"], totals["stop"])
    return {
        "schema": OUTPUT,
        "verdict": verdict,
        "reasons": reasons,
        "accounted_cells": sum(c["authenticated"] for c in cells.values()),
        "required_cells": MAX_LIVE,
        "missing": missing,
        "unaccounted": [
            {"world": c["world"], "arm": c["arm"], "reason": c["unaccounted_reason"]}
            for c in cells.values()
            if not c["authenticated"]
        ],
        "native_grades_all_first_attempts": _grades(observed),
        "native_grades_complete_cells": _grades(complete),
        "reporter_sessions_saved": saved,
        "pairs": pairs,
        "categories": dict(sorted(categories.items())),
        "complete_cell_totals": totals,
        "observed_first_attempt_totals": {
            arm: _totals(observed, arm) for arm in protocol.ARMS
        },
        "controls": _controls(control_cells),
        "claims": {
            "throughput": False,
            "model_equivalence": False,
            "non_inferiority": False,
            "billing": False,
        },
        "scope": "Small resource-use pilot over worlds 1200-1207; native_reward null means no verified grade and is never counted as 0.",
    }


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    require(len(pairs) == len(dict(pairs)), "duplicate_key")
    return dict(pairs)


def _constant(name: str) -> object:
    raise Rejected("non_finite_number")


def load(path: Path) -> object:
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "input_too_large")
    return json.loads(data, object_pairs_hook=_object, parse_constant=_constant)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: python -BS -m examples.seatlab.fanin.panel ROWS.json",
            file=sys.stderr,
        )
        return 2
    try:
        summary = summarize(load(Path(args[0])))
    except Rejected as rejected:
        print(f"fanin panel: {rejected}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError) as error:
        print(f"fanin panel: unreadable_input {type(error).__name__}", file=sys.stderr)
        return 2
    print(protocol.wire(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
