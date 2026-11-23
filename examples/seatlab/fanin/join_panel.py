"""Preregistered join-recovery verdict over audited hybrid-team rows and typed controls."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from . import join_evidence as je
from . import protocol

ENVELOPE = "seatlab.join-rows/v1"
OUTPUT = "seatlab.join-panel/v1"
SCREEN = "seatlab.peer-fanin-screen/v1"
SCREEN_PATH = Path(__file__).resolve().parents[1] / "results/peer-fanin/screen.json"
COMMIT = "71ddf40b14ea81dd8eb0c32663e11abfe21cd1e3"
SEED = 1709000311
WORLDS = tuple(protocol.RECOVERY_WORLDS)
ARMS = protocol.RECOVERY_ARMS
LIVE = "live"
CALENDARS = {
    1216: "5480345c9a505a89db7eac6b1a0734c68f6cf42b447fc2dd5b8adafc1c0dc89b",
    1217: "113d923f5e7adcbbee4438e5158497d38c6712caf5acd362f7aa77d60520bd02",
    1218: "d8d8d3d41dadc1c999c4dc618936676c9fb692c86d5ef1fe5a06cccd75778d5e",
    1219: "1e9921202d3fd8d991f644fc8229fcf57c1ae9ffa510c711660529de86e9f5e1",
}
CONTROLS = je.CONTROLS
REGISTERED = {
    (1216, "abstain", "control-correct"): ("held", 0, 0, [True, True, True]),
    (1216, "reduce", "control-correct"): ("reduced", 1, 1, [True, True, True]),
    (1217, "reduce", "control-inexact"): ("insufficient", 0, 0, [False, True, True]),
}
EXPECTED = {
    "abstain": {"reason": "held", "submissions": 0, "reward": 0},
    "reduce": {"reason": "reduced", "submissions": 1, "reward": 1},
}
DECISIONS = {"abstain": "hold", "reduce": "reduce"}
REASONS = {
    "abstain": ("held", "malformed"),
    "reduce": ("reduced", "insufficient", "malformed"),
}
STATUSES = (
    "bound",
    "absent",
    "unanswered",
    "not_admitted",
    "answered_unused",
    "unaudited",
)
REPORTERS = len(protocol.SEATS) - 1
MAX_BYTES = 4 << 20
MAX_LIVE = len(WORLDS) * len(ARMS)
MAX_SUBMISSIONS = 200
MAX_TOKENS = 1 << 40
HEX = re.compile(r"[0-9a-f]{64}")
INPUT_KEYS = frozenset(
    ("launch", "config", "native_result", "messages", "events", "world_state", "join")
)
KEYS = frozenset(
    (
        "schema",
        "world",
        "arm",
        "tag",
        "booker",
        "reporters",
        "trial_id",
        "generator_commit",
        "seed",
        "task_checksum",
        "calendar_sha256",
        "native_reward",
        "complete",
        "incomplete_reason",
        "reporters_expected",
        "reporter_sessions",
        "session_sha256s",
        "client_tokens",
        "reporters_exact",
        "reporter_rows",
        "booker_model_sessions",
        "outage",
        "join_decision",
        "join_reason",
        "submission_count",
        "control_as_designed",
        "inputs",
    )
)
REPORTER_KEYS = frozenset(
    ("seat", "position", "status", "exact", "session_sha256", "total_tokens")
)
CLAIMS = {
    "injected_final_seat_outage_only": True,
    "hybrid_teams_zero_model_bookers": True,
    "provider_failure_frequency": False,
    "real_outage_recovery": False,
    "paid_cost": False,
    "open_ended_decisions": False,
    "trust_unverified_messages": False,
    "regrade_motivating_study": False,
}
SCOPE = (
    "Four-world pilot, worlds 1216-1219: the owner explicitly withholds the final "
    "booker model at the role boundary of a hybrid team with three signed-in model "
    "reporters and zero model bookers. It measures only this injected final-seat "
    "outage; it does not estimate provider failure frequency, recovery from real "
    "outages, or paid cost. The exact flag comes from this task's local reporter "
    "adapter. native_reward null means no bound native grade and is never counted as 0."
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


def _count(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _digest(value: object) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _optional_text(value: object) -> bool:
    return value is None or (type(value) is str and value != "")


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


def arm_order(world: int) -> tuple[str, str]:
    require(world in WORLDS, "world_outside_pilot")
    first, second = ARMS
    return (first, second) if world % 2 == 0 else (second, first)


def frozen_screen(path: Path) -> None:
    screen = load(path)
    if not isinstance(screen, dict) or screen.get("schema") != SCREEN:
        raise Rejected("screen")
    registration = screen.get("registration")
    require(
        screen.get("generator_commit") == COMMIT
        and isinstance(registration, dict)
        and registration.get("seed") == SEED,
        "screen_registration",
    )
    rows = screen.get("worlds")
    if not isinstance(rows, list):
        raise Rejected("screen_worlds")
    for world, calendar in CALENDARS.items():
        found = [r for r in rows if isinstance(r, dict) and r.get("world") == world]
        roles = protocol.order(world)
        require(
            len(found) == 1
            and found[0].get("calendar_sha256") == calendar
            and found[0].get("booker") == roles[0]
            and found[0].get("reporters") == list(roles[1:]),
            "screen_calendar_mismatch",
        )


def _reporter(raw: object, position: int, seat: str, live: bool) -> dict[str, Any]:
    row = _fields(raw, REPORTER_KEYS, "reporter_fields")
    require(
        type(row["position"]) is int and row["position"] == position,
        "reporter_position",
    )
    require(row["seat"] == seat, "reporter_seat")
    status, exact = row["status"], row["exact"]
    require(status in STATUSES, "reporter_status")
    require(exact is None or type(exact) is bool, "reporter_exact")
    require(exact is None or status == "bound", "exact_without_bound_report")
    digest, tokens = row["session_sha256"], row["total_tokens"]
    require((digest is None) == (tokens is None), "session_without_tokens")
    if not live:
        require(digest is None, "control_claims_session")
        return row
    require(digest is None or _digest(digest), "session_digest")
    require(tokens is None or _count(tokens, 1, MAX_TOKENS), "reporter_tokens")
    opened = status in ("bound", "answered_unused")
    require(opened == (digest is not None), "session_status_mismatch")
    return row


def _sessions(row: dict[str, Any], reporters: list[dict[str, Any]], live: bool) -> None:
    digests = [r["session_sha256"] for r in reporters if r["session_sha256"]]
    require(len(digests) == len(set(digests)), "session_reused_in_row")
    require(row["reporter_sessions"] == len(digests), "reporter_sessions_mismatch")
    require(row["session_sha256s"] == sorted(digests), "session_list_mismatch")
    exact = sum(r["exact"] is True for r in reporters)
    require(row["reporters_exact"] == exact, "reporters_exact_mismatch")
    if not live:
        require(row["client_tokens"] is None, "control_claims_tokens")
        return
    total = sum(r["total_tokens"] for r in reporters if r["total_tokens"] is not None)
    wanted = total if digests else None
    require(row["client_tokens"] == wanted, "client_tokens_mismatch")


def _join(row: dict[str, Any]) -> None:
    arm, decision, reason = row["arm"], row["join_decision"], row["join_reason"]
    require(type(row["outage"]) is bool, "outage")
    require(decision in (None, DECISIONS[arm]), "join_decision")
    require((decision is None) == (reason is None), "join_reason_without_decision")
    require(reason is None or reason in REASONS[arm], "join_reason")
    require(row["outage"] == (decision is not None), "outage_without_join")
    require(_count(row["booker_model_sessions"], 0, 1), "booker_model_sessions")
    count = row["submission_count"]
    require(count is None or _count(count, 0, MAX_SUBMISSIONS), "submission_count")
    require(
        (count is None) == (row["native_reward"] is None), "submission_without_grade"
    )


def _source(row: dict[str, Any]) -> None:
    require(
        row["generator_commit"] == COMMIT and row["seed"] == SEED, "source_registration"
    )
    checksum, calendar = row["task_checksum"], row["calendar_sha256"]
    require(checksum is None or _digest(checksum), "task_checksum")
    frozen = CALENDARS[row["world"]]
    require(calendar is None or calendar == frozen, "calendar_not_frozen")
    if row["trial_id"] is not None:
        require(checksum is not None and calendar is not None, "trial_without_source")
    inputs = row["inputs"]
    require(
        isinstance(inputs, dict) and "launch" in inputs and set(inputs) <= INPUT_KEYS,
        "inputs",
    )
    require(_digest(inputs["launch"]), "launch_digest")
    require(all(v is None or _digest(v) for v in inputs.values()), "input_digests")


def _header(raw: object, live: bool) -> dict[str, Any]:
    row = _fields(raw, KEYS, "row_fields")
    require(row["schema"] == je.SCHEMA, "row_schema")
    world, arm, tag = row["world"], row["arm"], row["tag"]
    require(type(world) is int and world in WORLDS and arm in ARMS, "world_or_arm")
    require(tag == LIVE if live else (world, arm, tag) in CONTROLS, "unregistered_cell")
    roles = protocol.order(world)
    require(
        row["booker"] == roles[0] and row["reporters"] == list(roles[1:]), "role_order"
    )
    trial, complete = row["trial_id"], row["complete"]
    require(_trial(trial) and type(complete) is bool, "trial_or_complete")
    reward = row["native_reward"]
    require(reward is None or _count(reward, 0, 1), "native_reward")
    require(reward is None or row["trial_id"] is not None, "reward_without_trial")
    require(_optional_text(row["incomplete_reason"]), "incomplete_reason")
    reason = row["incomplete_reason"]
    require(complete == (reason is None), "complete_reason_mismatch")
    require(row["reporters_expected"] == REPORTERS, "reporters_expected")
    require(_count(row["reporter_sessions"], 0, REPORTERS), "reporter_sessions")
    require(_count(row["reporters_exact"], 0, REPORTERS), "reporters_exact")
    require(type(row["session_sha256s"]) is list, "session_sha256s")
    require(
        row["client_tokens"] is None or _count(row["client_tokens"], 1, MAX_TOKENS),
        "client_tokens",
    )
    flag = row["control_as_designed"]
    require(flag is None or (not live and type(flag) is bool), "control_as_designed")
    _source(row)
    _join(row)
    return row


def _row(raw: object, live: bool) -> dict[str, Any]:
    row = _header(raw, live)
    reporters = row["reporter_rows"]
    require(type(reporters) is list and len(reporters) == REPORTERS, "reporter_rows")
    checked = [
        _reporter(reporters[i], i + 1, row["reporters"][i], live)
        for i in range(REPORTERS)
    ]
    _sessions(row, checked, live)
    if row["complete"]:
        require(
            row["trial_id"] is not None
            and row["native_reward"] is not None
            and row["submission_count"] is not None
            and row["outage"],
            "complete_without_outcome",
        )
        require(
            all(r["status"] == "bound" and r["exact"] is not None for r in checked),
            "complete_reporter_status",
        )
        require(not live or row["reporter_sessions"] == REPORTERS, "complete_sessions")
    return row


def _ingest(
    rows: list[Any], live: bool, trials: set[str], sessions: set[str]
) -> dict[tuple[int, str, str], dict[str, Any]]:
    cells: dict[tuple[int, str, str], dict[str, Any]] = {}
    for raw in rows:
        row = _row(raw, live)
        key = (row["world"], row["arm"], row["tag"])
        require(key not in cells, "duplicate_cell")
        trial = row["trial_id"]
        require(trial is None or trial not in trials, "trial_reused")
        if trial is not None:
            trials.add(trial)
        for digest in row["session_sha256s"]:
            require(digest not in sessions, "session_reused_across_rows")
            sessions.add(digest)
        cells[key] = row
    return cells


def _violations(row: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    found = []
    if row["booker_model_sessions"] != 0:
        found.append("model_booked")
    if row["join_reason"] != expected["reason"]:
        found.append(f"join_reason_{row['join_reason']}")
    if row["submission_count"] != expected["submissions"]:
        found.append(f"submissions_{row['submission_count']}")
    if row["native_reward"] != expected["reward"]:
        found.append(f"native_reward_{row['native_reward']}")
    return found


def _accounted(row: dict[str, Any]) -> bool:
    return (
        row["complete"]
        and row["native_reward"] is not None
        and row["submission_count"] is not None
        and row["reporter_sessions"] == REPORTERS
    )


def _unaccounted_reason(row: dict[str, Any]) -> str | None:
    if _accounted(row):
        return None
    if not row["complete"]:
        return row["incomplete_reason"]
    return "no_bound_native_grade"


def _cell(row: dict[str, Any]) -> dict[str, Any]:
    accounted = _accounted(row)
    bound = row["native_reward"] is not None
    violations = _violations(row, EXPECTED[row["arm"]]) if bound else None
    return {
        "world": row["world"],
        "arm": row["arm"],
        "booker": row["booker"],
        "reporters": row["reporters"],
        "trial_id": row["trial_id"],
        "calendar_sha256": row["calendar_sha256"],
        "complete": row["complete"],
        "accounted": accounted,
        "unaccounted_reason": _unaccounted_reason(row),
        "native_reward": row["native_reward"],
        "submission_count": row["submission_count"],
        "outage": row["outage"],
        "join_reason": row["join_reason"],
        "booker_model_sessions": row["booker_model_sessions"],
        "reporter_sessions": row["reporter_sessions"],
        "reporters_exact": row["reporters_exact"],
        "reporter_status": [r["status"] for r in row["reporter_rows"]],
        "reporter_exact": [r["exact"] for r in row["reporter_rows"]],
        "client_tokens": row["client_tokens"],
        "meets_rule": None if violations is None else not violations,
        "rule_violations": violations,
    }


def _tally(values: list[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = "null" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _arm_totals(cells: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    chosen = [c for c in cells if c["arm"] == arm]
    tokens = [c["client_tokens"] for c in chosen if c["client_tokens"] is not None]
    statuses = [s for c in chosen for s in c["reporter_status"]]
    return {
        "attempts_observed": len(chosen),
        "attempts_accounted": sum(c["accounted"] for c in chosen),
        "native_grades": _tally([c["native_reward"] for c in chosen]),
        "submissions": _tally([c["submission_count"] for c in chosen]),
        "join_reasons": _tally([c["join_reason"] for c in chosen]),
        "incomplete_reasons": _tally(
            [c["unaccounted_reason"] for c in chosen if not c["complete"]]
        ),
        "outages": sum(c["outage"] for c in chosen),
        "booker_model_sessions": sum(c["booker_model_sessions"] for c in chosen),
        "reporter_sessions": sum(c["reporter_sessions"] for c in chosen),
        "reporter_status": _tally(list(statuses)),
        "reporters_exact": sum(c["reporters_exact"] for c in chosen),
        "reporter_tokens": sum(tokens),
        "cells_with_tokens": len(tokens),
    }


def _control(key: tuple[int, str, str], row: dict[str, Any] | None) -> dict[str, Any]:
    world, arm, tag = key
    spec = CONTROLS[key]
    base = {"world": world, "arm": arm, "tag": tag, "expected": spec}
    if row is None:
        return {**base, "present": False, "matches": False, "reasons": ["missing"]}
    exact = [r["exact"] for r in row["reporter_rows"]]
    reasons = []
    if not row["complete"]:
        reasons.append(f"incomplete_{row['incomplete_reason']}")
    if exact != spec["exact"]:
        reasons.append("exact_pattern")
    reasons.extend(_violations(row, spec))
    if row["control_as_designed"] is not True:
        reasons.append(f"control_as_designed_{row['control_as_designed']}")
    return {
        **base,
        "present": True,
        "matches": not reasons,
        "reasons": reasons,
        "trial_id": row["trial_id"],
        "native_reward": row["native_reward"],
        "submission_count": row["submission_count"],
        "join_reason": row["join_reason"],
        "reporter_exact": exact,
        "model_sessions": row["reporter_sessions"] + row["booker_model_sessions"],
    }


def _verdict(
    controls: list[dict[str, Any]], cells: dict[tuple[int, str], dict[str, Any]]
) -> tuple[str, list[str]]:
    reasons = []
    if not all(c["matches"] for c in controls):
        reasons.append("typed_control_gate_broken")
    if len(cells) != MAX_LIVE:
        reasons.append("missing_first_attempt")
    if not all(c["accounted"] for c in cells.values()):
        reasons.append("unaccounted_first_attempt_or_reporter")
    if reasons:
        return "inconclusive", reasons
    failed = sorted(
        {f"{c['arm']}_{v}" for c in cells.values() for v in c["rule_violations"]}
    )
    sessions = sum(c["reporter_sessions"] for c in cells.values())
    if sessions != MAX_LIVE * REPORTERS:
        failed.append("reporter_sessions_not_24")
    return ("fail", failed) if failed else ("pass", [])


def _worlds(cells: dict[tuple[int, str], dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for world in WORLDS:
        pair = {arm: cells.get((world, arm)) for arm in ARMS}
        if any(c is None for c in pair.values()):
            category = "missing"
        elif not all(c["accounted"] for c in pair.values() if c is not None):
            category = "unaccounted"
        else:
            category = "_".join(
                f"{arm}{c['native_reward']}s{c['submission_count']}"
                for arm, c in pair.items()
                if c is not None
            )
        out.append(
            {
                "world": world,
                "booker": protocol.order(world)[0],
                "calendar_sha256": CALENDARS[world],
                "arm_order": list(arm_order(world)),
                **pair,
                "category": category,
            }
        )
    return out


def _cells(envelope: object) -> tuple[dict[Any, Any], dict[Any, Any]]:
    body = _fields(envelope, {"schema", "rows", "controls"}, "envelope_fields")
    require(body["schema"] == ENVELOPE, "envelope_schema")
    require(je.ROW_KEYS == KEYS and je.REPORTER_KEYS == REPORTER_KEYS, "row_contract")
    require(tuple(je.ARMS) == ARMS and tuple(je.WORLDS) == WORLDS, "row_contract")
    require(all(je.first_arm(w) == arm_order(w)[0] for w in WORLDS), "arm_order")
    bookers = sorted(protocol.order(w)[0] for w in WORLDS)
    require(bookers == sorted(protocol.SEATS), "booker_rotation")
    require(len(set(CALENDARS.values())) == len(WORLDS), "calendar_repeats")
    registered = {
        key: (spec["reason"], spec["submissions"], spec["reward"], spec["exact"])
        for key, spec in CONTROLS.items()
    }
    require(registered == REGISTERED, "control_registration")
    rows, controls = body["rows"], body["controls"]
    require(isinstance(rows, list) and len(rows) <= MAX_LIVE, "rows_bound")
    require(
        isinstance(controls, list) and len(controls) <= len(CONTROLS), "controls_bound"
    )
    trials: set[str] = set()
    sessions: set[str] = set()
    live = _ingest(rows, True, trials, sessions)
    typed = _ingest(controls, False, trials, sessions)
    require(not any(r["session_sha256s"] for r in typed.values()), "control_session")
    return live, typed


def summarize(envelope: object, screen: Path = SCREEN_PATH) -> dict[str, Any]:
    frozen_screen(screen)
    live, typed = _cells(envelope)
    cells = {(w, a): _cell(r) for (w, a, _), r in live.items()}
    controls = [_control(key, typed.get(key)) for key in CONTROLS]
    verdict, reasons = _verdict(controls, cells)
    observed = list(cells.values())
    accounted = [c for c in observed if c["accounted"]]
    return {
        "schema": OUTPUT,
        "verdict": verdict,
        "reasons": reasons,
        "denominators": {
            "worlds": len(WORLDS),
            "first_attempts": MAX_LIVE,
            "first_attempts_per_arm": len(WORLDS),
            "reporter_sessions": MAX_LIVE * REPORTERS,
            "typed_controls": len(CONTROLS),
        },
        "observed_first_attempts": len(observed),
        "accounted_first_attempts": len(accounted),
        "missing": [
            {"world": w, "arm": a}
            for w in WORLDS
            for a in arm_order(w)
            if (w, a) not in cells
        ],
        "unaccounted": [
            {"world": c["world"], "arm": c["arm"], "reason": c["unaccounted_reason"]}
            for c in observed
            if not c["accounted"]
        ],
        "bound_rule_violations": [
            {"world": c["world"], "arm": c["arm"], "violations": c["rule_violations"]}
            for c in observed
            if c["rule_violations"]
        ],
        "booker_identities": {str(w): protocol.order(w)[0] for w in WORLDS},
        "worlds": _worlds(cells),
        "first_attempt_totals": {arm: _arm_totals(observed, arm) for arm in ARMS},
        "accounted_totals": {arm: _arm_totals(accounted, arm) for arm in ARMS},
        "sessions": {
            "reporter_model_sessions": sum(c["reporter_sessions"] for c in observed),
            "booker_model_sessions": sum(c["booker_model_sessions"] for c in observed),
            "control_model_sessions": sum(
                c["model_sessions"] for c in controls if c["present"]
            ),
        },
        "controls": {
            "gate": all(c["matches"] for c in controls),
            "rows": controls,
        },
        "claims": dict(CLAIMS),
        "scope": SCOPE,
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
            "usage: python -BS -m examples.seatlab.fanin.join_panel ROWS.json",
            file=sys.stderr,
        )
        return 2
    try:
        summary = summarize(load(Path(args[0])))
    except Rejected as rejected:
        print(f"join panel: {rejected}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError, AttributeError, TypeError) as error:
        print(f"join panel: unreadable_input {type(error).__name__}", file=sys.stderr)
        return 2
    print(protocol.wire(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
