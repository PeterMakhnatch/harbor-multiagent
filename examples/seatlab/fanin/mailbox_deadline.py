from __future__ import annotations

from typing import Any

from . import fanout_panel, mailbox_panel, panel
from .panel import Rejected, require

OUTPUT = "seatlab.fanin-mailbox-deadline/v1"
MILESTONES = ("requests-returned", "replies-returned", "booked")
STATUSES = ("reached", "not_reached", "coverage_ended", "no_origin", "no_cut")
STAGES = mailbox_panel.STAGES
ARMS = mailbox_panel.ARMS
REPORTERS = mailbox_panel.REPORTERS
MAX_MS = mailbox_panel.MAX_MS
LIVE = fanout_panel.LIVE
Row = dict[str, Any]
Point = mailbox_panel.Point

__all__ = ["MILESTONES", "OUTPUT", "STATUSES", "Rejected", "assess", "cohort"]


def _question(milestone: object, by_ms: object) -> None:
    require(type(milestone) is str and milestone in MILESTONES, "deadline_milestone")
    require(panel._count(by_ms, 0, MAX_MS), "deadline_by_ms")


def _stages(cut: Row, by_ms: int) -> list[Row]:
    stages = []
    for seat, path in zip(cut["reporters"], cut["paths"], strict=True):
        reached = 0
        for point in path:
            if point is None or point[1] > by_ms:
                break
            reached += 1
        stages.append({"seat": seat, "stage": STAGES[reached - 1] if reached else None})
    assert len(stages) == REPORTERS
    return stages


def _target(cut: Row, milestone: str) -> Point | None:
    if milestone == MILESTONES[2]:
        return cut["submit"]
    index = STAGES.index(
        "request_inbox" if milestone == MILESTONES[0] else "reply_inbox"
    )
    points = [path[index] for path in cut["paths"]]
    if any(point is None for point in points):
        return None
    return max((point for point in points if point is not None), key=lambda p: p[0])


def _assess(cut: Row, milestone: str, by_ms: int) -> Row:
    target, through = _target(cut, milestone), cut["through"]
    if through is None:
        assert target is None
        status = "no_origin"
    elif target is not None and target[1] <= by_ms:
        status = "reached"
    elif target is not None or through[1] > by_ms:
        status = "not_reached"
    else:
        status = "coverage_ended"
    assert target is None or (through is not None and target[0] <= through[0])
    return {
        "status": status,
        "milestone": milestone,
        "by_ms": by_ms,
        "observed_through": None if through is None else list(through),
        "target": None if target is None else list(target),
        "stages": _stages(cut, by_ms),
    }


def assess(cut: dict[str, Any], *, milestone: str, by_ms: int) -> Row:
    _question(milestone, by_ms)
    return _assess(mailbox_panel._cut(cut), milestone, by_ms)


def _counts(teams: list[Row]) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for team in teams:
        assessment = team["assessment"]
        counts["no_cut" if assessment is None else assessment["status"]] += 1
    assert sum(counts.values()) == len(teams)
    return counts


def cohort(
    release: dict[str, Any], cuts: list[dict[str, Any]], *, milestone: str, by_ms: int
) -> Row:
    _question(milestone, by_ms)
    registered, bound = mailbox_panel._joined_teams(release, cuts)
    teams = []
    for team in registered:
        cut = bound.get(team["events_sha256"])
        teams.append(
            {
                "world": team["world"],
                "arm": team["arm"],
                "tag": team["tag"],
                "coverage": team["coverage"],
                "assessment": None if cut is None else _assess(cut, milestone, by_ms),
            }
        )
    live = [t for t in teams if t["tag"] == LIVE]
    controls = [t for t in teams if t["tag"] != LIVE]
    assert len(live) == fanout_panel.MAX_LIVE and len(controls) == len(
        fanout_panel.CONTROLS
    )
    return {
        "schema": OUTPUT,
        "milestone": milestone,
        "by_ms": by_ms,
        "arms": {arm: _counts([t for t in live if t["arm"] == arm]) for arm in ARMS},
        "controls": _counts(controls),
        "teams": teams,
        "scope": (
            "The gather-time registry keeps all planned teams. Cuts must agree with the release "
            "on journal digest, seat order, message counts and timing. Times are "
            "milliseconds since the first accepted booker request; by_ms is inclusive. A milestone "
            "is not_reached only when the capture runs strictly past by_ms; a capture that ends at "
            "or before by_ms is coverage_ended. Teams without a cut are no_cut, never failures. "
            "Deliver means the world returned an inbox snapshot, not that a seat or model read it. "
            "No runtime, speed or causal effect is claimed."
        ),
    }
