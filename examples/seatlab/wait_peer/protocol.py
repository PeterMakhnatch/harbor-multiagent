"""Public role views and first-answer grammar for a requestable peer report."""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

ARMS = ("auto", "neutral", "remind")
PHASES = ("report", "open", "book")
ORDERS = tuple(itertools.permutations(ARMS))
FIRST = 408
COUNT = 8
SLOTS = 35


def wire(obj: object) -> str:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def role_ids(world: int) -> tuple[str, str]:
    if type(world) is not int or not FIRST <= world < FIRST + COUNT:
        raise ValueError("world is outside the registered panel")
    return ("p1", "p2") if world % 2 == 0 else ("p2", "p1")


def ordered_arms(world: int) -> tuple[str, ...]:
    role_ids(world)
    return ORDERS[(world - FIRST) % len(ORDERS)]


def allowed(arm: str, requested: bool) -> bool:
    if arm not in ARMS or type(requested) is not bool:
        raise ValueError("invalid report policy or request")
    return arm == "auto" or requested


def _ids(value: object) -> list[int]:
    if not isinstance(value, list) or len(value) > SLOTS:
        raise ValueError("expected a list of slot IDs")
    valid: list[int] = []
    for slot in value:
        if type(slot) is not int or not 0 <= slot < SLOTS or slot in valid:
            raise ValueError("expected distinct slot IDs in range")
        valid.append(slot)
    return valid


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) != len(dict(pairs)):
        raise ValueError("duplicate response key")
    return dict(pairs)


def decode(phase: str, text: str) -> list[int] | bool | int:
    if (
        phase not in PHASES
        or not isinstance(text, str)
        or len(text.encode("utf-8")) > 8192
    ):
        raise ValueError("invalid role response")
    obj = json.loads(text, object_pairs_hook=_object)
    key = {"report": "starts", "open": "open", "book": "slot"}[phase]
    if type(obj) is not dict or set(obj) != {key}:
        raise ValueError("unexpected response fields")
    item = obj[key]
    if phase == "report":
        return _ids(item)
    if phase == "open" and type(item) is bool:
        return item
    if phase == "book" and type(item) is int and 0 <= item < SLOTS:
        return item
    raise ValueError("invalid first answer")


def render(
    phase: str, starts: list[int], arm: str, report: list[int] | None = None
) -> str:
    if (
        phase not in PHASES
        or arm not in ARMS
        or (phase != "book" and report is not None)
    ):
        raise ValueError("invalid seat view")
    own = _ids(starts)
    if not own or own != sorted(own):
        raise ValueError("invalid local feasible starts")
    setup = (
        "You are one of two people booking a two-hour meeting. Your team scores 1 only if the booked start "
        "fits both calendars. Slot IDs run from 0 through 34. Your own feasible start IDs: "
        + wire(own)
        + ".\n"
    )
    if phase == "report":
        action = (
            "You maintain your team's peer report. Supply your full list of feasible start IDs for the other seat. "
            'Use this JSON shape: {"starts":[0,1]}. The numbers in the example are not evidence.'
        )
    elif phase == "open":
        note = (
            "Before you book, consider checking the other seat's report."
            if arm == "remind"
            else "Before you book, note that slot IDs start at zero."
        )
        action = (
            "The other seat says its report is ready. You can open it at no cost before you book. "
            "If you open it, the next message includes the report. "
            + note
            + '\nReturn {"open":true} to open it '
            'or {"open":false} to skip it.'
        )
    else:
        detail = (
            "No detailed peer report was delivered."
            if report is None
            else (
                "The report delivered from the other seat lists these feasible start IDs: "
                + wire(_ids(report))
                + "."
            )
        )
        action = (
            detail
            + '\nBook exactly one start. Use this JSON shape: {"slot":0}. The example is not evidence.'
        )
    return (
        setup
        + action
        + "\nReply with only the JSON object, no Markdown and no extra fields. Do not use tools."
    )
