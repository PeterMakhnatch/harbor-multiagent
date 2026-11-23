"""Preregistered certified-reporter verdict over external rows, owner provenance and controls."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

from ..wait_peer.receipt import canonical
from . import external_evidence, panel, protocol
from .panel import Rejected, require

INPUT = "seatlab.certified-reporters-input/v1"
OUTPUT = "seatlab.certified-reporters-panel/v1"
PROVENANCE = "seatlab.external-provenance/v1"
WORLDS = tuple(range(1232, 1240))
ARMS = protocol.ARMS
SEATS = protocol.SEATS
REPORTERS = len(SEATS) - 1
CONTROLS = {
    "all-correct": (1232, "all"),
    "stop-correct": (1232, "stop"),
    "stop-inexact": (1233, "stop"),
}
MAX_TEAMS = len(WORLDS) * len(ARMS)
MIN_SAVED_SESSIONS = 2
MIN_SAVED_WORLDS = 2
MAX_TOKENS = 1 << 40
REASON = re.compile(r"[A-Za-z0-9_:.-]{1,200}")
ROW_KEYS = frozenset(
    (
        "schema",
        "world",
        "arm",
        "tag",
        "trial_id",
        "booker",
        "reporters",
        "seats",
        "reports",
        "exact_reports",
        "submissions",
        "native_reward",
        "complete",
        "incomplete_reason",
        "model_sessions",
        "tokens",
        "source",
        "inputs",
    )
)
SOURCE_KEYS = frozenset(
    ("generator_commit", "seed", "task_checksum", "calendar_sha256", "stock_verified")
)
STOCK_INPUTS = frozenset(
    ("launch", "config", "native_result", "messages", "events", "world_state")
)
REQUIRED_INPUTS = frozenset(("launch", "config", "native_result"))
SEAT_KEYS = frozenset(
    (
        "seat",
        "role",
        "position",
        "action",
        "owner_accepted",
        "status",
        "request",
        "reply",
        "request_sha256",
        "reply_sha256",
    )
)
RECORDS = {
    "absent": (False, False),
    "unanswered": (True, False),
    "bound": (True, True),
    "answered_unused": (True, True),
}
PROVENANCE_KEYS = frozenset(
    (
        "schema",
        "world",
        "arm",
        "trial_id",
        "external_row_sha256",
        "seats",
        "attested_sessions",
        "attested_tokens",
        "fully_accounted",
        "served_model",
    )
)
PROVENANCE_SEAT_KEYS = frozenset(("seat", "status", "session_sha256", "client_tokens"))
PROVENANCE_STATUSES = ("bound", "skipped", "unverified")
Row = dict[str, Any]
assert external_evidence.SCHEMA == "seatlab.peer-fanin-external/v1"
assert ARMS == ("all", "stop")
assert set(CONTROLS.values()) <= {(w, a) for w in WORLDS for a in ARMS}


def roles(world: int) -> tuple[str, ...]:
    require(type(world) is int and world in WORLDS, "world_outside_cohort")
    first = (world - protocol.FIRST) % len(SEATS)
    return tuple(SEATS[(first + step) % len(SEATS)] for step in range(len(SEATS)))


assert roles(WORLDS[0]) == protocol.order(protocol.FIRST)


def arm_order(world: int) -> tuple[str, str]:
    require(world in WORLDS, "world_outside_cohort")
    return ("all", "stop") if world % 2 == 0 else ("stop", "all")


def row_sha256(row: Row) -> str:
    return hashlib.sha256(canonical(row) + b"\n").hexdigest()


def _flag(value: object) -> bool:
    return type(value) is bool


def _source(row: Row) -> bool:
    source = panel._fields(row["source"], SOURCE_KEYS, "source_fields")
    require(
        source["generator_commit"] == panel.COMMIT
        and type(source["seed"]) is int
        and source["seed"] == panel.SEED,
        "source_registration",
    )
    stocked = source["stock_verified"]
    require(_flag(stocked), "stock_verified")
    checksum, calendar = source["task_checksum"], source["calendar_sha256"]
    require(
        panel._digest(checksum) or checksum is None and not stocked, "task_checksum"
    )
    require(panel._digest(calendar) if stocked else calendar is None, "calendar_sha256")
    trial = row["trial_id"]
    require(panel._trial(trial) and (trial is not None) == stocked, "trial_id")
    keys = STOCK_INPUTS if stocked else frozenset(("launch",))
    inputs = panel._fields(row["inputs"], keys, "inputs_fields")
    for key, value in inputs.items():
        optional = key not in REQUIRED_INPUTS and not row["complete"]
        require(panel._digest(value) or value is None and optional, "input_digests")
    return stocked


def _seat(raw: object, position: int, name: str, stocked: bool) -> Row:
    seat = panel._fields(raw, SEAT_KEYS, "seat_fields")
    require(
        seat["seat"] == name
        and type(seat["position"]) is int
        and seat["position"] == position,
        "seat_rotation",
    )
    require(seat["role"] == ("booker" if position == 0 else "reporter"), "seat_role")
    request, reply, accepted = seat["request"], seat["reply"], seat["owner_accepted"]
    require(
        _flag(request) and _flag(reply) and (accepted is None or _flag(accepted)),
        "seat_flags",
    )
    for flag, key in ((request, "request_sha256"), (reply, "reply_sha256")):
        digest = seat[key]
        require(panel._digest(digest) if flag else digest is None, "seat_digests")
    require(request or not reply, "reply_without_request")
    require(not request or accepted is True, "record_without_acceptance")
    action, status = seat["action"], seat["status"]
    if not stocked:
        require(
            action is None and status == ("unverified" if request else "absent"),
            "unstocked_seat",
        )
        return seat
    require(RECORDS.get(status) == (request, reply), "seat_status_records")
    if position == 0:
        require(action is None, "booker_action")
        return seat
    require(action in (None, "report", "skip"), "reporter_action")
    require(action == "report" or status == "absent", "record_without_request")
    require(action != "skip" or accepted is not True, "skipped_owner_accepted")
    return seat


def _actions(row: Row, reporters: list[Row]) -> tuple[int, int, int]:
    actions = [seat["action"] for seat in reporters]
    asked = 0
    while asked < len(actions) and actions[asked] == "report":
        asked += 1
    skipped = asked
    while skipped < len(actions) and actions[skipped] == "skip":
        skipped += 1
    require(all(action is None for action in actions[skipped:]), "action_out_of_order")
    require(row["arm"] == "stop" or skipped == asked, "all_arm_skipped")
    return asked, skipped - asked, len(actions) - skipped


def _stocked(row: Row, seats: list[Row]) -> None:
    reporters = seats[1:]
    asked, skipped, unreached = _actions(row, reporters)
    reports, exact = row["reports"], row["exact_reports"]
    submissions, reward = row["submissions"], row["native_reward"]
    require(
        panel._count(reports, 0, REPORTERS)
        and panel._count(exact, 0, reports)
        and panel._count(submissions, 0, 1)
        and (reward is None or panel._count(reward, 0, 1)),
        "outcome_counts",
    )
    bound = sum(seat["status"] == "bound" for seat in reporters[:asked])
    require(reports <= bound, "report_without_bound_turn")
    require(not skipped or reports == exact == asked, "skip_without_exact_prefix")
    require(reward != 1 or submissions == 1, "grade_without_submission")
    if not row["complete"]:
        return
    require(
        unreached == 0 and reports == asked and reward is not None and submissions == 1,
        "complete_outcome",
    )
    require(
        seats[0]["status"] == "bound"
        and all(
            seat["status"] == ("bound" if seat["action"] == "report" else "absent")
            for seat in reporters
        ),
        "complete_turn_status",
    )


def external(raw: object) -> Row:
    row = panel._fields(raw, ROW_KEYS, "external_fields")
    require(
        row["schema"] == external_evidence.SCHEMA
        and row["tag"] == external_evidence.TAG,
        "external_schema",
    )
    require(row["arm"] in ARMS, "world_or_arm")
    order = roles(row["world"])
    require(
        row["booker"] == order[0] and row["reporters"] == list(order[1:]),
        "role_rotation",
    )
    require(row["model_sessions"] is None and row["tokens"] is None, "external_usage")
    complete, reason = row["complete"], row["incomplete_reason"]
    require(_flag(complete), "complete_flag")
    require(
        reason is None
        if complete
        else type(reason) is str and REASON.fullmatch(reason) is not None,
        "incomplete_reason",
    )
    stocked = _source(row)
    raw_seats = row["seats"]
    require(type(raw_seats) is list and len(raw_seats) == len(SEATS), "seats")
    seats = [_seat(raw_seats[i], i, order[i], stocked) for i in range(len(SEATS))]
    if stocked:
        _stocked(row, seats)
        return row
    require(
        not complete
        and all(
            row[key] is None
            for key in ("reports", "exact_reports", "submissions", "native_reward")
        ),
        "unstocked_outcome",
    )
    return row


def _provenance_seat(raw: object, wire: Row) -> Row:
    seat = panel._fields(raw, PROVENANCE_SEAT_KEYS, "provenance_seat_fields")
    require(seat["seat"] == wire["seat"], "provenance_seat_order")
    status, digest, tokens = (
        seat["status"],
        seat["session_sha256"],
        seat["client_tokens"],
    )
    require(status in PROVENANCE_STATUSES, "provenance_status")
    action = wire["action"]
    require((action == "skip") == (status == "skipped"), "skip_attestation")
    if status == "bound":
        require(
            panel._digest(digest) and panel._count(tokens, 1, MAX_TOKENS),
            "bound_receipt",
        )
        require(action == "report" and wire["request"], "bound_without_request")
    elif status == "skipped":
        require(
            digest is None
            and type(tokens) is int
            and tokens == 0
            and not wire["request"]
            and not wire["reply"],
            "skipped_receipt",
        )
    else:
        require(digest is None and tokens is None, "unverified_receipt")
    return seat


def provenance(raw: object, row: Row) -> Row:
    prov = panel._fields(raw, PROVENANCE_KEYS, "provenance_fields")
    require(prov["schema"] == PROVENANCE, "provenance_schema")
    require(
        type(prov["world"]) is int
        and prov["world"] == row["world"]
        and prov["arm"] == row["arm"],
        "provenance_cell",
    )
    require(prov["trial_id"] == row["trial_id"], "provenance_trial")
    require(prov["external_row_sha256"] == row_sha256(row), "external_row_sha256")
    require(prov["served_model"] is None, "served_model")
    raw_seats = prov["seats"]
    require(type(raw_seats) is list and len(raw_seats) == REPORTERS, "provenance_seats")
    seats = [
        _provenance_seat(raw_seats[i], row["seats"][i + 1]) for i in range(REPORTERS)
    ]
    bound = [seat for seat in seats if seat["status"] == "bound"]
    require(
        panel._count(prov["attested_sessions"], 0, REPORTERS)
        and prov["attested_sessions"] == len(bound),
        "attested_sessions",
    )
    require(
        panel._count(prov["attested_tokens"], 0, REPORTERS * MAX_TOKENS)
        and prov["attested_tokens"] == sum(seat["client_tokens"] for seat in bound),
        "attested_tokens",
    )
    accounted = all(seat["status"] != "unverified" for seat in seats)
    require(
        _flag(prov["fully_accounted"]) and prov["fully_accounted"] == accounted,
        "fully_accounted",
    )
    return prov


def _cell(row: Row, prov: Row) -> Row:
    reporters = row["seats"][1:]
    status = {seat["seat"]: seat["status"] for seat in prov["seats"]}
    requested = [s["seat"] for s in reporters if s["action"] == "report"]
    certified = (
        row["submissions"] == 1 and row["exact_reports"] == row["reports"]
        if row["source"]["stock_verified"]
        else None
    )
    cell: Row = {
        "world": row["world"],
        "arm": row["arm"],
        "trial_id": row["trial_id"],
        "stock_verified": row["source"]["stock_verified"],
        "complete": row["complete"],
        "incomplete_reason": row["incomplete_reason"],
        "native_reward": row["native_reward"],
        "submissions": row["submissions"],
        "reports": row["reports"],
        "exact_reports": row["exact_reports"],
        "certified_booking": certified,
        "requested": requested,
        "skipped_attested": [s["seat"] for s in reporters if s["action"] == "skip"],
        "unreached": [s["seat"] for s in reporters if s["action"] is None],
        "attested_sessions": prov["attested_sessions"],
        "bound_reporters": [seat for seat in requested if status[seat] == "bound"],
        "unverified_requested": [
            seat for seat in requested if status[seat] == "unverified"
        ],
        "attested_tokens": prov["attested_tokens"],
        "fully_accounted": prov["fully_accounted"],
    }
    return cell | _classify(cell)


def _classify(cell: Row) -> Row:
    reward = cell["native_reward"]
    if not cell["stock_verified"]:
        return {"outcome": "inconclusive", "reason": "native_outcome_missing"}
    if reward is None:
        return {"outcome": "inconclusive", "reason": "native_grade_missing"}
    if cell["unverified_requested"]:
        return {"outcome": "inconclusive", "reason": "requested_reporter_unverified"}
    if reward == 0:
        kind = "wrong_submission" if cell["submissions"] else "no_submission"
        return {"outcome": "wrong", "reason": kind}
    if not cell["certified_booking"]:
        return {"outcome": "wrong", "reason": "booking_without_exact_certificate"}
    if not cell["complete"]:
        return {"outcome": "inconclusive", "reason": "first_outcome_not_bound"}
    return {"outcome": "correct", "reason": None}


def _expected(case: str, row: Row) -> bool:
    reports, exact = row["reports"], row["exact_reports"]
    if case == "stop-inexact":
        return (
            row["native_reward"] == 0
            and row["submissions"] == 0
            and reports == REPORTERS
            and exact < REPORTERS
        )
    booked = row["complete"] and row["native_reward"] == 1 and row["submissions"] == 1
    if case == "all-correct":
        return booked and reports == exact == REPORTERS
    return booked and reports == exact


def _control(case: str, row: Row | None) -> Row:
    world, arm = CONTROLS[case]
    base: Row = {"case": case, "world": world, "arm": arm, "present": row is not None}
    if row is None:
        return (
            base
            | dict.fromkeys(
                ("trial_id", "complete", "native_reward", "submissions", "reports"),
                None,
            )
            | {"exact_reports": None, "as_designed": None, "reason": "control_missing"}
        )
    base |= {
        key: row[key]
        for key in (
            "trial_id",
            "complete",
            "native_reward",
            "submissions",
            "reports",
            "exact_reports",
        )
    }
    if row["native_reward"] is None:
        return base | {"as_designed": None, "reason": "control_grade_missing"}
    designed = _expected(case, row)
    return base | {
        "as_designed": designed,
        "reason": None if designed else "control_not_as_designed",
    }


class Seen:
    def __init__(self) -> None:
        self.trials: set[str] = set()
        self.digests: set[str] = set()
        self.sessions: set[str] = set()
        self.calendars: dict[int, str] = {}

    def row(self, row: Row) -> None:
        trial = row["trial_id"]
        require(trial is None or trial not in self.trials, "trial_reused")
        if trial is not None:
            self.trials.add(trial)
        for key in ("launch", "native_result"):
            digest = row["inputs"].get(key)
            require(digest is None or digest not in self.digests, "evidence_reused")
            if digest is not None:
                self.digests.add(digest)
        calendar = row["source"]["calendar_sha256"]
        if calendar is not None:
            world = row["world"]
            require(
                self.calendars.get(world, calendar) == calendar,
                "stock_differs_within_world",
            )
            self.calendars[world] = calendar
            require(
                len(self.calendars) == len(set(self.calendars.values())),
                "calendar_repeats_across_worlds",
            )

    def sessions_of(self, prov: Row) -> None:
        for seat in prov["seats"]:
            digest = seat["session_sha256"]
            require(digest is None or digest not in self.sessions, "session_reused")
            if digest is not None:
                self.sessions.add(digest)


def _teams(raw: object, seen: Seen) -> dict[tuple[int, str], Row]:
    if not isinstance(raw, list) or len(raw) > MAX_TEAMS:
        raise Rejected("teams_bound")
    cells: dict[tuple[int, str], Row] = {}
    for entry in raw:
        team = panel._fields(entry, {"external", "provenance"}, "team_fields")
        row = external(team["external"])
        prov = provenance(team["provenance"], row)
        key = (row["world"], row["arm"])
        require(key not in cells, "duplicate_cell")
        seen.row(row)
        seen.sessions_of(prov)
        cells[key] = _cell(row, prov)
    return cells


def _controls(raw: object, seen: Seen) -> list[Row]:
    if not isinstance(raw, list) or len(raw) > len(CONTROLS):
        raise Rejected("controls_bound")
    rows: dict[str, Row] = {}
    for entry in raw:
        control = panel._fields(entry, {"case", "external"}, "control_fields")
        case = control["case"]
        require(type(case) is str and case in CONTROLS, "control_case")
        require(case not in rows, "duplicate_control")
        row = external(control["external"])
        require((row["world"], row["arm"]) == CONTROLS[case], "control_world_arm")
        seen.row(row)
        rows[case] = row
    return [_control(case, rows.get(case)) for case in CONTROLS]


def _pair(world: int, cells: dict[tuple[int, str], Row]) -> Row:
    all_cell, stop_cell = cells.get((world, "all")), cells.get((world, "stop"))
    pair: Row = {
        "world": world,
        "arm_order": list(arm_order(world)),
        "all": all_cell,
        "stop": stop_cell,
        "saved_reporter_sessions": None,
    }
    if all_cell is None or stop_cell is None:
        return pair | {"category": "missing"}
    outcomes = (all_cell["outcome"], stop_cell["outcome"])
    if "wrong" in outcomes:
        return pair | {"category": "wrong"}
    if "inconclusive" in outcomes:
        return pair | {"category": "inconclusive"}
    saved = sum(
        seat in all_cell["bound_reporters"] for seat in stop_cell["skipped_attested"]
    )
    return pair | {"category": "both_correct", "saved_reporter_sessions": saved}


def _arm_totals(cells: list[Row], arm: str) -> Row:
    chosen = [cell for cell in cells if cell["arm"] == arm]
    grades = {"0": 0, "1": 0, "null": 0}
    for cell in chosen:
        grade = cell["native_reward"]
        grades["null" if grade is None else str(grade)] += 1
    return {
        "cells": len(chosen),
        "missing_cells": len(WORLDS) - len(chosen),
        "native_grades": grades,
        "requested": sum(len(c["requested"]) for c in chosen),
        "skipped_attested": sum(len(c["skipped_attested"]) for c in chosen),
        "unreached": sum(len(c["unreached"]) for c in chosen),
        "attested_sessions": sum(c["attested_sessions"] for c in chosen),
        "unverified_requested": sum(len(c["unverified_requested"]) for c in chosen),
        "attested_tokens": sum(c["attested_tokens"] for c in chosen),
        "tokens_complete": len(chosen) == len(WORLDS)
        and all(c["fully_accounted"] for c in chosen),
    }


def _verdict(pairs: list[Row], controls: list[Row]) -> tuple[str, list[str], Row]:
    cells = [pair[arm] for pair in pairs for arm in ARMS if pair[arm] is not None]
    failures = sorted({c["reason"] for c in cells if c["outcome"] == "wrong"})
    failures += sorted({c["reason"] for c in controls if c["as_designed"] is False})
    unresolved = sorted(
        {c["reason"] for c in cells if c["outcome"] == "inconclusive"}
        | {c["reason"] for c in controls if c["as_designed"] is None}
        | (
            {"cell_missing"}
            if any(p["category"] == "missing" for p in pairs)
            else set()
        )
    )
    saved = [p["saved_reporter_sessions"] or 0 for p in pairs]
    savings = {
        "confirmed_sessions": sum(saved),
        "worlds": sum(s > 0 for s in saved),
        "min_sessions": MIN_SAVED_SESSIONS,
        "min_worlds": MIN_SAVED_WORLDS,
        "final": not failures and not unresolved,
    }
    if failures:
        return "fail", failures, savings
    if unresolved:
        return "inconclusive", unresolved, savings
    if savings["confirmed_sessions"] < MIN_SAVED_SESSIONS:
        failures.append("confirmed_sessions_saved_below_2")
    if savings["worlds"] < MIN_SAVED_WORLDS:
        failures.append("saved_worlds_below_2")
    return ("fail" if failures else "pass"), failures, savings


def summarize(envelope: object) -> Row:
    body = panel._fields(envelope, {"schema", "teams", "controls"}, "envelope_fields")
    require(body["schema"] == INPUT, "envelope_schema")
    seen = Seen()
    cells = _teams(body["teams"], seen)
    controls = _controls(body["controls"], seen)
    pairs = [_pair(world, cells) for world in WORLDS]
    verdict, reasons, savings = _verdict(pairs, controls)
    return {
        "schema": OUTPUT,
        "verdict": verdict,
        "reasons": reasons,
        "required_cells": MAX_TEAMS,
        "accounted_cells": sum(c["outcome"] != "inconclusive" for c in cells.values()),
        "missing_cells": [
            {"world": world, "arm": arm}
            for world in WORLDS
            for arm in arm_order(world)
            if (world, arm) not in cells
        ],
        "worlds": pairs,
        "arms": {arm: _arm_totals(list(cells.values()), arm) for arm in ARMS},
        "savings": savings,
        "controls": {
            "cases": controls,
            "matched": sum(c["as_designed"] is True for c in controls),
            "required": len(CONTROLS),
        },
        "claims": {
            "non_inferiority": False,
            "spending": False,
            "outage_rate": False,
            "unrelated_tasks": False,
            "run_order_audited": False,
            "offline_controls_in_input": False,
            "first_report_inexact_audited": False,
        },
        "claim_limits": [
            "native_reward null is never counted as 0; graded 0 cells are retained with their reason",
            "a requested reporter without a bound owner receipt is never counted as zero usage",
            "tokens are attested client totals, not a bill; tokens_complete false means a lower bound",
            "stop-inexact control checks exact_reports below 3, not which report was inexact",
            "arm run order and offline wrong-order or ambiguous controls are not audited here",
        ],
        "scope": "Worlds 1232-1239 with a local fail-closed booker in both arms; a pass supports only the registered reporter-savings question.",
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "usage: python3.12 -BS -m examples.seatlab.fanin.certified_panel INPUT.json",
            file=sys.stderr,
        )
        return 2
    try:
        report = summarize(panel.load(Path(args[0])))
    except Rejected as rejected:
        print(f"certified panel: {rejected}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print(
            f"certified panel: unreadable_input {type(error).__name__}", file=sys.stderr
        )
        return 2
    sys.stdout.buffer.write(canonical(report) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
