"""Join released gather-time rows with public mailbox cuts and split each booking interval."""

from __future__ import annotations

import hashlib
import json
from itertools import pairwise
from typing import Any, cast

from . import evidence, fanout_panel, panel, protocol
from .panel import Rejected, require

CUT = "seatlab.fanin-mailbox-cut/v1"
OUTPUT = "seatlab.fanin-mailbox-panel/v1"
CUT_KEYS = frozenset(
    ("schema", "events_sha256", "booker", "reporters", "edges", "submit", "through")
)
STAGES = ("request", "request_inbox", "reply", "reply_inbox")
EDGE_KEYS = frozenset(("seat", *STAGES))
PHASES = (
    "before_issue",
    "issue_to_receiver_inbox",
    "receiver_inbox_to_reply",
    "reply_to_booker_inbox",
    "booker_inbox_to_submit",
)
ARMS = fanout_panel.ARMS
REPORTERS = fanout_panel.REPORTERS
FIRST_ACTION_SEQ = 3
MAX_SEQ = 8192
MAX_MS = fanout_panel.MAX_MS
MAX_CUTS = fanout_panel.MAX_LIVE + len(fanout_panel.CONTROLS)
Row = dict[str, Any]
Point = tuple[int, int]
Path = list[Point | None]

__all__ = ["OUTPUT", "PHASES", "STAGES", "Rejected", "summarize"]


def _point(value: object) -> Point | None:
    if value is None:
        return None
    if type(value) is not list or len(value) != 2:
        raise Rejected("cut_point_shape")
    seq, ms = value
    require(
        panel._count(seq, FIRST_ACTION_SEQ, MAX_SEQ) and panel._count(ms, 0, MAX_MS),
        "cut_point_range",
    )
    return cast(int, seq), cast(int, ms)


def _reached(path: Path) -> int:
    reached = 0
    for point in path:
        if point is None:
            break
        reached += 1
    return reached


def _present(path: Path) -> list[Point]:
    return [point for point in path if point is not None]


def _edge(raw: object, seat: str) -> Path:
    edge = panel._fields(raw, EDGE_KEYS, "cut_edge_fields")
    require(edge["seat"] == seat, "cut_edge_order")
    path = [_point(edge[stage]) for stage in STAGES]
    require(_reached(path) == len(_present(path)), "cut_edge_not_prefix")
    for before, after in pairwise(_present(path)):
        require(before[0] < after[0] and before[1] <= after[1], "cut_edge_impossible")
    return path


def _roster(booker: object, reporters: object) -> tuple[str, ...]:
    require(type(booker) is str and booker in protocol.SEATS, "cut_booker")
    require(
        type(reporters) is list
        and len(reporters) == REPORTERS
        and all(type(seat) is str for seat in reporters)
        and {booker, *reporters} == set(protocol.SEATS),
        "cut_reporters",
    )
    assert isinstance(reporters, list)
    return tuple(cast(list[str], reporters))


def _requests(paths: list[Path]) -> None:
    issued = [path[0] for path in paths]
    count = sum(point is not None for point in issued)
    require(
        all(point is not None for point in issued[:count]), "cut_requests_not_prefix"
    )
    requests = [point for point in issued if point is not None]
    require(not requests or requests[0][1] == 0, "cut_first_request_not_zero")
    for before, after in pairwise(requests):
        require(before[0] < after[0] and before[1] <= after[1], "cut_request_order")


def _booker_inbox(paths: list[Path]) -> None:
    snapshots = [path[3][0] for path in paths if path[3] is not None]
    for path in paths:
        reply, inbox = path[2], path[3]
        if reply is None:
            continue
        for snapshot in snapshots:
            if reply[0] < snapshot:
                require(
                    inbox is not None and inbox[0] <= snapshot,
                    "cut_snapshot_missed_reply",
                )


def _submit(paths: list[Path], submit: Point | None) -> None:
    if submit is None:
        return
    inboxes = [path[3] for path in paths]
    require(all(point is not None for point in inboxes), "cut_submit_before_replies")
    last = max(point[0] for point in inboxes if point is not None)
    require(submit[0] > last, "cut_submit_before_replies")


def _clock(paths: list[Path], submit: Point | None, through: Point | None) -> None:
    marks = [
        (point[0], point[1], stage)
        for path in paths
        for stage, point in zip(STAGES, path, strict=True)
        if point is not None
    ]
    if submit is not None:
        marks.append((submit[0], submit[1], "submit"))
    marks.sort()
    for before, after in pairwise(marks):
        if before[0] == after[0]:
            require(
                before[2] == after[2] == STAGES[3] and before[1] == after[1],
                "cut_event_reused",
            )
        else:
            require(before[1] <= after[1], "cut_clock_backwards")
    if not marks:
        require(through is None, "cut_horizon_without_request")
        return
    require(through is not None, "cut_missing_horizon")
    assert through is not None
    seq, ms, _ = marks[-1]
    require(
        (through[0] == seq and through[1] == ms)
        or (through[0] > seq and through[1] >= ms),
        "cut_horizon_before_event",
    )


def _cut(raw: object) -> Row:
    cut = panel._fields(raw, CUT_KEYS, "cut_fields")
    require(cut["schema"] == CUT, "cut_schema")
    require(panel._digest(cut["events_sha256"]), "cut_events_digest")
    reporters = _roster(cut["booker"], cut["reporters"])
    edges = cut["edges"]
    require(type(edges) is list and len(edges) == REPORTERS, "cut_edges")
    paths = [_edge(edges[i], reporters[i]) for i in range(REPORTERS)]
    submit = _point(cut["submit"])
    through = _point(cut["through"])
    _requests(paths)
    _booker_inbox(paths)
    _submit(paths, submit)
    _clock(paths, submit, through)
    return {
        "events": cut["events_sha256"],
        "cut_sha256": hashlib.sha256(
            json.dumps(cut, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest(),
        "booker": cut["booker"],
        "reporters": reporters,
        "paths": paths,
        "submit": submit,
        "through": through,
    }


def _counters(paths: list[Path]) -> tuple[int | None, int]:
    marks = sorted(
        [(path[0][0], 1) for path in paths if path[0] is not None]
        + [(path[2][0], -1) for path in paths if path[2] is not None]
    )
    issued = outstanding = peak = 0
    before = None
    for _, step in marks:
        if step > 0:
            issued += 1
            outstanding += 1
            peak = max(peak, outstanding)
            continue
        if before is None:
            before = issued
        outstanding -= 1
        require(outstanding >= 0, "cut_reply_without_request")
    return before, peak


def _serial(paths: list[Path]) -> None:
    for previous, path in pairwise(paths):
        if path[0] is None:
            continue
        inbox = previous[3]
        require(inbox is not None and inbox[0] < path[0][0], "cut_serial_overlap")


def _bind(cut: Row, row: Row) -> None:
    require(
        cut["booker"] == row["booker"] and list(cut["reporters"]) == row["reporters"],
        "cut_roles_differ",
    )
    timing = row["timing"]
    require(isinstance(timing, dict), "cut_without_release_timing")
    paths = cut["paths"]
    asked = sum(path[0] is not None for path in paths)
    require(asked == len(row["reporters_asked"]), "cut_requests_differ")
    if row["arm"] == ARMS[0]:
        _serial(paths)
    before, peak = _counters(paths)
    require(
        before == timing["before_first_reply"] and peak == timing["max_outstanding"],
        "cut_counters_differ",
    )
    replies = [path[2][1] for path in paths if path[2] is not None]
    gather = max(replies) if len(replies) == REPORTERS else None
    require(gather == timing["gather_ms"], "cut_gather_differs")
    submit = cut["submit"]
    require(
        (None if submit is None else submit[1]) == timing["submit_ms"],
        "cut_submit_differs",
    )


def _join(release: Row, cuts: object) -> dict[str, Row]:
    by_events: dict[str, Row] = {}
    for row in (*release["rows"], *release["controls"]):
        events = row["inputs"].get("events")
        if events is None:
            continue
        require(events not in by_events, "release_events_reused")
        by_events[events] = row
    if type(cuts) is not list or len(cuts) > MAX_CUTS:
        raise Rejected("cuts_bound")
    joined: dict[str, Row] = {}
    for raw in cuts:
        cut = _cut(raw)
        row = by_events.get(cut["events"])
        if row is None:
            raise Rejected("cut_foreign")
        require(cut["events"] not in joined, "cut_duplicate")
        _bind(cut, row)
        joined[cut["events"]] = cut
    return joined


def _path(cut: Row, submit_ms: int | None) -> Row | None:
    submit = cut["submit"]
    if submit is None:
        return None
    assert submit_ms is not None
    paths = cut["paths"]
    position = max(range(REPORTERS), key=lambda i: paths[i][2][0])
    request, inbox, reply, returned = _present(paths[position])
    marks = (0, request[1], inbox[1], reply[1], returned[1], submit[1])
    parts = [after - before for before, after in pairwise(marks)]
    require(all(part >= 0 for part in parts), "path_phase_negative")
    require(sum(parts) == submit_ms, "path_phase_sum")
    return {
        "seat": cut["reporters"][position],
        "position": position,
        "submit_ms": submit_ms,
        "phase_ms": dict(zip(PHASES, parts, strict=True)),
    }


def _known_stop(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    seat, sep, code = value.partition(":")
    return value if seat in protocol.SEATS and sep and code in evidence.ERRORS else None


def _team(row: Row, joined: dict[str, Row]) -> Row:
    events = row["inputs"].get("events")
    timing = row["timing"] or {}
    team: Row = {
        "world": row["world"],
        "arm": row["arm"],
        "tag": row["tag"],
        "control": row["tag"] != fanout_panel.LIVE,
        "events_sha256": events,
        "cut_sha256": None,
        "complete": row["complete"],
        "native_reward": row["native_reward"],
        "native_seat_stop": _known_stop(row.get("incomplete_reason")),
        "gather_ms": timing.get("gather_ms"),
        "submit_ms": timing.get("submit_ms"),
        "coverage": "missing",
        "stages": None,
        "stage_counts": None,
        "submitted": None,
        "path": None,
    }
    cut = joined.get(events) if events is not None else None
    if cut is None:
        return team
    paths = cut["paths"]
    reached = [_reached(path) for path in paths]
    return team | {
        "coverage": "recorded",
        "cut_sha256": cut["cut_sha256"],
        "stages": [
            {"seat": seat, "stage": STAGES[n - 1] if n else None}
            for seat, n in zip(cut["reporters"], reached, strict=True)
        ],
        "stage_counts": {
            stage: sum(n > i for n in reached) for i, stage in enumerate(STAGES)
        },
        "submitted": cut["submit"] is not None,
        "path": _path(cut, team["submit_ms"]),
    }


def _arm(teams: list[Row]) -> Row:
    recorded = [t for t in teams if t["coverage"] == "recorded"]
    full = [t["path"] for t in recorded if t["path"] is not None]
    return {
        "teams": len(teams),
        "recorded_teams": len(recorded),
        "missing_teams": len(teams) - len(recorded),
        "without_release_row": sum(t["coverage"] == "no_release_row" for t in teams),
        "edges_reached": {
            s: sum(t["stage_counts"][s] for t in recorded) for s in STAGES
        },
        "teams_all_edges_reached": {
            s: sum(t["stage_counts"][s] == REPORTERS for t in recorded) for s in STAGES
        },
        "teams_submitted": sum(t["submitted"] for t in recorded),
        "incomplete": [
            {"world": t["world"], "stages": t["stages"], "submitted": t["submitted"]}
            for t in recorded
            if t["path"] is None
        ],
        "eligible_paths": {
            "teams": len(full),
            "submit_ms_sum": sum(p["submit_ms"] for p in full),
            "phase_ms_sum": {ph: sum(p["phase_ms"][ph] for p in full) for ph in PHASES},
        },
    }


def _ordered(release: Row, joined: dict[str, Row]) -> list[Row]:
    teams = {
        (row["world"], row["arm"], row["tag"]): _team(row, joined)
        for row in (*release["rows"], *release["controls"])
    }
    registered = (
        (world, arm, fanout_panel.LIVE) for world in fanout_panel.WORLDS for arm in ARMS
    )
    for world, arm, tag in (*registered, *fanout_panel.CONTROLS):
        if (world, arm, tag) not in teams:
            absent = {
                "world": world,
                "arm": arm,
                "tag": tag,
                "inputs": {},
                "timing": None,
                "complete": None,
                "native_reward": None,
            }
            teams[world, arm, tag] = _team(absent, {}) | {"coverage": "no_release_row"}
    return sorted(
        teams.values(),
        key=lambda t: (t["control"], t["world"], ARMS.index(t["arm"]), t["tag"]),
    )


def _joined_teams(release: Row, cuts: list[Row]) -> tuple[list[Row], dict[str, Row]]:
    fanout_panel.summarize(release)
    joined = _join(release, cuts)
    return _ordered(release, joined), joined


def summarize(release: dict[str, Any], cuts: list[dict[str, Any]]) -> Row:
    teams, _ = _joined_teams(release, cuts)
    live = [t for t in teams if not t["control"]]
    return {
        "schema": OUTPUT,
        "release_schema": fanout_panel.ENVELOPE,
        "cut_schema": CUT,
        "stages": list(STAGES),
        "phases": list(PHASES),
        "arms": {arm: _arm([t for t in live if t["arm"] == arm]) for arm in ARMS},
        "teams": teams,
        "claims": {
            "origin_proven": False,
            "model_authenticated": False,
            "receiver_acknowledged": False,
            "model_compute_ms": False,
            "service_speed": False,
        },
        "scope": (
            "Stock world mailbox events of the gather-time release only. A cut is checked for "
            "internal order and against the release roster, arm and timing; it does not prove it "
            "came from the native log. Deliver means the world returned an inbox snapshot, not that "
            "a seat or model read it. Phases split submit_ms along the last reply accepted by the "
            "world; they are not the time any agent spent. Missing cuts are unmeasured, never failures."
        ),
    }
