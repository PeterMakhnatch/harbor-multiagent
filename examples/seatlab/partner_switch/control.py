from __future__ import annotations

import json
from typing import cast


def _slots(value: object, size: int) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != size
        or any(not isinstance(slot, str) for slot in value)
        or len(set(value)) != size
    ):
        raise ValueError("invalid slot list")
    return cast(list[str], value)


def choose(direction: int, role: str, packet: bytes) -> str:
    if type(direction) is not int or direction not in (-1, 1):
        raise ValueError("direction must be +1 or -1")
    if role not in ("east", "west") or not isinstance(packet, bytes):
        raise ValueError("invalid role or packet")
    data = json.loads(packet)
    if not isinstance(data, dict) or data.get("role") != role:
        raise ValueError("packet role does not match")
    stage = data.get("stage")
    keys = {
        "schema",
        "stage",
        "role",
        "instruction",
        "incumbent",
        "options",
        "active",
        "own",
    }
    if stage == "vote":
        keys.update(("sent", "peer"))
    elif stage != "report":
        raise ValueError("invalid packet stage")
    if set(data) != keys:
        raise ValueError("invalid packet fields")
    active = _slots(data["active"], 3)
    own = _slots(data["own"], 2)
    if active != sorted(active) or any(
        slot not in ("A", "B", "C", "D", "E", "F") for slot in active
    ):
        raise ValueError("invalid public cycle")
    if not set(own).issubset(active):
        raise ValueError("own slots must belong to the public cycle")
    if stage == "report":
        (missing,) = set(active).difference(own)
        return active[(active.index(missing) + direction) % 3]
    sent, peer = data["sent"], data["peer"]
    if (
        not isinstance(sent, str)
        or sent not in own
        or not isinstance(peer, str)
        or peer not in active
    ):
        raise ValueError("invalid singleton report")
    partner_missing = active[(active.index(peer) - direction) % 3]
    candidates = [slot for slot in own if slot != partner_missing]
    if len(candidates) == 1:
        return candidates[0]
    return peer if peer in own else min(own)
