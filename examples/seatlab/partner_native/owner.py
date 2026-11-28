#!/usr/bin/env python3
"""Scripted owner with a public three-slot cycle, not an unrestricted model."""

import json
import re
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from examples.joint_reopen import owner_game, scripted_owner  # noqa: E402
from examples.seatlab.partner_switch.control import choose  # noqa: E402

NAME = re.compile(r"seatlab-switch/(forward|reverse)-([A-F]{3})")


def _report_packet(view: dict[str, Any], active: list[str]) -> dict[str, object]:
    return {
        "schema": "seatlab.partner-switch.role/v1",
        "stage": "report",
        "role": view["role"],
        "instruction": owner_game.PUBLIC_INSTRUCTIONS,
        "incumbent": view["incumbent"],
        "options": view["options"],
        "active": active,
        "own": view["card"],
    }


def _encode(packet: dict[str, object]) -> bytes:
    return json.dumps(packet, separators=(",", ":"), allow_nan=False).encode("ascii")


def _decide(direction: int, active: list[str], view: dict[str, Any]) -> str:
    stage, card, inbox = view["stage"], view["card"], view["inbox"]
    incumbent = view["incumbent"]
    if incumbent in active:
        raise ValueError("the public cycle includes the incumbent")
    if card is not None and (len(card) != 2 or not set(card) <= set(active)):
        raise ValueError("card is incompatible with the public cycle")
    if inbox is not None and (
        inbox["delivered"] is not True
        or len(inbox["slots"]) != 1
        or inbox["slots"][0] not in active
    ):
        raise ValueError("expected a delivered singleton report in the public cycle")
    seat = owner_game.seat_from_brief({key: view[key] for key in owner_game.BRIEF_KEYS})
    if card is not None:
        seat.card = tuple(card)
    if inbox is not None:
        seat.inbox = (True, tuple(inbox["slots"]))
    if stage == "endorse":
        if card is not None or inbox is not None or view["turn"] != 1:
            raise ValueError("unexpected endorsement state")
        choice = f"endorse:{incumbent}"
        offered = {choice, "stop"}
    elif stage == "report":
        if inbox is not None or view["turn"] != (2 if card is None else 3):
            raise ValueError("unexpected report state")
        offered = set(owner_game.report_choices(seat))
        if card is None:
            choice = "card"
        else:
            sent = choose(direction, seat.role, _encode(_report_packet(view, active)))
            choice = f"report:{sent}"
    else:
        if card is None or view["turn"] != (4 if inbox is None else 5):
            raise ValueError("unexpected vote state for the scripted convention")
        offered = set(owner_game.vote_choices(seat))
        if inbox is None:
            choice = "inbox"
        else:
            packet = _report_packet(view, active)
            sent = choose(direction, seat.role, _encode(packet))
            packet.update(stage="vote", sent=sent, peer=inbox["slots"][0])
            choice = f"vote:{choose(direction, seat.role, _encode(packet))}"
    if set(view["choices"]) != offered or len(view["choices"]) != len(offered):
        raise ValueError("offered choices differ from the native stage")
    if choice not in view["choices"]:
        raise ValueError("scripted choice was not offered")
    return choice


def reply(data: bytes) -> dict[str, object]:
    if not data or len(data) > scripted_owner.LIMIT:
        raise ValueError("request exceeds its bound")
    request = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=scripted_owner._unique,
        parse_constant=scripted_owner._constant,
    )
    if type(request) is not dict or set(request) != scripted_owner.REQUEST_KEYS:
        raise ValueError("request fields differ")
    if (
        request["schema"] != scripted_owner.REQUEST_SCHEMA
        or request["instructions"] != owner_game.PUBLIC_INSTRUCTIONS
    ):
        raise ValueError("request schema or instructions differ")
    name = request["requested"]
    match = NAME.fullmatch(name) if type(name) is str else None
    if match is None:
        raise ValueError("unsupported owner identifier")
    direction = 1 if match[1] == "forward" else -1
    active = list(match[2])
    if active != sorted(set(active)):
        raise ValueError("public cycle must be sorted and distinct")
    view = scripted_owner._check(request["view"])
    return {
        "schema": scripted_owner.REPLY_SCHEMA,
        "requested": name,
        "choice": _decide(direction, active, view),
        "served_model": None,
    }


def main() -> int:
    try:
        result = reply(sys.stdin.buffer.read(scripted_owner.LIMIT + 1))
    except (TypeError, ValueError, KeyError, RecursionError, owner_game.OwnerGameError):
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
