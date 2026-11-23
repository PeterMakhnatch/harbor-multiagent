"""Four-seat fan-in role views, first-answer grammar and the early-stop rule."""

from __future__ import annotations

import json
from typing import Any, cast

SEATS = ("p1", "p2", "p3", "p4")
ARMS = ("all", "stop")
RECOVERY_ARMS = ("abstain", "reduce")
FIRST = 1200
COUNT = 32
PILOT_WORLDS = range(FIRST, FIRST + 8)
RECOVERY_WORLDS = range(FIRST + 16, FIRST + 20)
SLOTS = 35
MAX_REPLY = 8192
INPUT = "Input JSON: "
_SETUP = (
    "You are one of four scheduling agents booking a two-hour meeting for six attendees. "
    "The task guarantees that exactly one start ID fits all six attendees. "
    "Start IDs are integers from 0 through 34. "
    "Field own lists every start ID that fits all attendees assigned to you.\n"
)
_CLOSE = "Reply with only the JSON object, no Markdown and no extra fields. Do not use tools.\n"
_REPORT = (
    "Another person books for the team and asks for your report. "
    'Use this JSON shape: {"starts":[...]}. The list holds every start ID in own.\n'
)
_BOOK = (
    "You book for the team. Field reports lists the reports delivered to you, in the order received. "
    "In each report, seat names the sender. Field starts is the sender's reported list of start IDs "
    "that fit its calendar, or null if its reply could not be read. Field exact is true only when "
    "that list matched the sender's own calendar check.\n"
    'Book exactly one start. Use this JSON shape: {"slot":<start ID>}.\n'
)


def wire(obj: object) -> str:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def order(world: int) -> tuple[str, str, str, str]:
    if type(world) is not int or not FIRST <= world < FIRST + COUNT:
        raise ValueError("world is outside the registered screen")
    first = (world - FIRST) % len(SEATS)
    booker, a, b, c = (SEATS[(first + step) % len(SEATS)] for step in range(4))
    return booker, a, b, c


def admitted(world: object, arm: object) -> bool:
    if type(world) is not int:
        return False
    if arm in ARMS:
        return world in PILOT_WORLDS
    if arm in RECOVERY_ARMS:
        return world in RECOVERY_WORLDS
    return False


def _ids(value: object) -> list[int]:
    if type(value) is not list or len(value) > SLOTS:
        raise ValueError("expected a list of start IDs")
    seen: set[int] = set()
    for slot in value:
        if type(slot) is not int or not 0 <= slot < SLOTS or slot in seen:
            raise ValueError("expected distinct start IDs in range")
        seen.add(slot)
    return sorted(seen)


def _own(own: object) -> list[int]:
    valid = _ids(own)
    if not valid or valid != own:
        raise ValueError("own starts must be a sorted non-empty list")
    return valid


def _report(report: object, seen: set[str]) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != {"seat", "exact", "starts"}:
        raise ValueError("report fields")
    fields = cast("dict[str, object]", report)
    seat, exact, starts = fields["seat"], fields["exact"], fields["starts"]
    if not isinstance(seat, str) or seat not in SEATS or seat in seen:
        raise ValueError("report seat")
    if type(exact) is not bool:
        raise ValueError("report exact flag")
    if starts is None:
        if exact:
            raise ValueError("an unreadable report cannot be exact")
    elif _ids(starts) != starts:
        raise ValueError("report starts must be normalized")
    seen.add(seat)
    return {"seat": seat, "exact": exact, "starts": starts}


def _reports(reports: object) -> list[dict[str, Any]]:
    if not isinstance(reports, list) or len(reports) > len(SEATS) - 1:
        raise ValueError("expected at most three delivered reports")
    seen: set[str] = set()
    return [_report(report, seen) for report in reports]


def report_prompt(own: list[int]) -> str:
    return _SETUP + _REPORT + _CLOSE + INPUT + wire({"own": _own(own)})


def book_prompt(own: list[int], reports: list[dict[str, Any]]) -> str:
    view = {"own": _own(own), "reports": _reports(reports)}
    return _SETUP + _BOOK + _CLOSE + INPUT + wire(view)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) != len(dict(pairs)):
        raise ValueError("duplicate response key")
    return dict(pairs)


def _field(text: object, key: str) -> object:
    if type(text) is not str or len(text.encode("utf-8")) > MAX_REPLY:
        raise ValueError("reply size")
    obj = json.loads(text, object_pairs_hook=_object)
    if type(obj) is not dict or set(obj) != {key}:
        raise ValueError("unexpected reply fields")
    return obj[key]


def parse_report(text: str) -> list[int] | None:
    try:
        return _ids(_field(text, "starts"))
    except (ValueError, RecursionError):
        return None


def parse_book(text: str) -> int | None:
    try:
        slot = _field(text, "slot")
    except (ValueError, RecursionError):
        return None
    return slot if type(slot) is int and 0 <= slot < SLOTS else None


def outgoing(own: list[int], text: str) -> dict[str, Any]:
    local = _own(own)
    starts = parse_report(text)
    return {"exact": starts == local, "starts": starts}


def ask_next(arm: str, own: list[int], reports: list[dict[str, Any]]) -> bool:
    if arm not in ARMS and arm not in RECOVERY_ARMS:
        raise ValueError("unknown arm")
    remaining = set(_own(own))
    delivered = _reports(reports)
    if arm != "stop" or not all(report["exact"] for report in delivered):
        return True
    for report in delivered:
        remaining.intersection_update(report["starts"])
    return len(remaining) != 1
