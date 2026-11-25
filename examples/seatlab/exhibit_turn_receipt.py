import json
import re
from importlib import import_module
from typing import Any

from .exhibit_authored import MODES, WORLD, canonical, require
from .exhibit_owner import _arguments, _check_view
from .exhibit_wire import encode_request, render_turn, request_stamp

_ROLE_PHASES = {
    "loader": ("request", "dispatch"),
    "registrar": ("registrar",),
    "depot": ("depot",),
}


def _validate_stamp(stamp: Any) -> None:
    require(
        type(stamp) is dict and stamp.keys() == {"bytes", "sha256"},
        "invalid request stamp fields",
    )
    require(
        type(stamp["bytes"]) is int and stamp["bytes"] > 0, "invalid request byte count"
    )
    require(
        type(stamp["sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", stamp["sha256"]) is not None,
        "invalid request digest",
    )


def _validate_metadata(role: str, count: int, metadata: dict[str, Any] | None) -> None:
    phases = _ROLE_PHASES[role]
    if metadata is None or (type(metadata) is dict and not metadata):
        require(count == 0, "missing accepted owner metadata")
        return
    require(
        type(metadata) is dict
        and metadata.keys()
        == {"role", "phase", "accepted_actions", "accepted_requests"},
        "invalid owner metadata fields",
    )
    require(metadata["role"] == role, "owner role mismatch")
    actions = metadata["accepted_actions"]
    stamps = metadata["accepted_requests"]
    require(
        type(actions) is list
        and type(stamps) is list
        and len(actions) == len(stamps) == count <= len(phases),
        "owner acceptance count mismatch",
    )
    phase = metadata["phase"]
    if count == len(phases):
        require(phase == "finished", "completed owner not finished")
    else:
        require(
            phase == phases[count] or (count == 0 and phase is None),
            "invalid incomplete owner phase",
        )
    for stamp in stamps:
        _validate_stamp(stamp)


def reconcile(
    cut: dict[str, Any],
    metadata_by_role: dict[str, dict[str, Any] | None],
    instructions: dict[str, str],
) -> list[dict[str, Any]]:
    grade = import_module(f"{WORLD}.grading").grade
    session_type = import_module(f"{WORLD}.engine").Session
    grade(cut)
    require(
        cut["mode"] in MODES and cut["case"]["profile"] == "coupling",
        "not an authored coupling cut",
    )
    require(
        type(metadata_by_role) is dict
        and metadata_by_role.keys() == _ROLE_PHASES.keys(),
        "invalid owner roles",
    )
    require(
        type(instructions) is dict and instructions.keys() == _ROLE_PHASES.keys(),
        "invalid instruction roles",
    )
    require(
        all(type(text) is str for text in instructions.values()),
        "invalid owner instruction",
    )
    events = cut["events"]
    for role in _ROLE_PHASES:
        count = sum(event["role"] == role for event in events)
        _validate_metadata(role, count, metadata_by_role[role])

    session = session_type(cut["case"], cut["mode"])
    offsets = dict.fromkeys(_ROLE_PHASES, 0)
    receipts = []
    for event in events:
        role = event["role"]
        index = offsets[role]
        phase = _ROLE_PHASES[role][index]
        metadata = metadata_by_role[role]
        if metadata is None:
            raise ValueError("missing owner metadata")
        actions = metadata["accepted_actions"]
        raw = actions[index]
        require(type(raw) is dict, "invalid raw owner action")
        previous = actions[index - 1] if index else None
        view = json.loads(canonical(session.view(role)))
        _check_view(view, role, phase)
        require(view["turn"] == role, "owner turn mismatch")
        stamp = request_stamp(
            encode_request(render_turn(instructions[role], view, previous))
        )
        require(
            stamp == metadata["accepted_requests"][index],
            "owner request stamp mismatch",
        )
        _arguments(raw, view, phase)
        session.apply(role, raw)
        accepted = session.snapshot()["events"][-1]
        require(
            canonical(accepted) == canonical(event), "owner accepted event mismatch"
        )
        offsets[role] += 1
        receipts.append(
            {"seq": event["seq"], "role": role, "phase": phase, "request": stamp}
        )
    require(
        canonical(session.snapshot()) == canonical(cut), "reconstructed cut mismatch"
    )
    return receipts
