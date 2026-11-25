from typing import Any

from ..exhibit_authored import canonical, require
from ..exhibit_owner import _arguments, _check_view
from ..exhibit_turn_receipt import _ROLE_PHASES, _validate_metadata
from ..exhibit_wire import encode_request, render_turn, request_stamp
from .certificate import Session, cli_view
from .route_evidence import assess


def replay_team(
    cut: dict[str, Any], metadata: dict[str, Any], instructions: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    require(
        metadata.keys() == instructions.keys() == _ROLE_PHASES.keys(),
        "role roster differs",
    )
    events = cut["events"]
    require(type(events) is list and len(events) <= 4, "invalid number of team actions")
    require(
        cut["mode"] == "linked" and cut["case"]["profile"] == "fork",
        "not a linked physical case",
    )
    for role in _ROLE_PHASES:
        _validate_metadata(
            role, sum(event["role"] == role for event in events), metadata[role]
        )
    session = Session(cut["case"], "linked")
    offsets = dict.fromkeys(_ROLE_PHASES, 0)
    turns: list[dict[str, Any]] = []
    route_evidence = None
    for event in events:
        role = event["role"]
        index = offsets[role]
        phase = _ROLE_PHASES[role][index]
        record = metadata[role]
        require(type(record) is dict, "missing accepted owner metadata")
        actions = record["accepted_actions"]
        raw = actions[index]
        require(type(raw) is dict, "invalid accepted owner response")
        view = cli_view(session.view(role))
        _check_view(view, role, phase)
        require(view["turn"] == role, "owner turn differs")
        previous = actions[index - 1] if index else None
        stamp = request_stamp(
            encode_request(render_turn(instructions[role], view, previous))
        )
        require(
            stamp == record["accepted_requests"][index], "owner request stamp mismatch"
        )
        _arguments(raw, view, phase)
        if phase == "dispatch":
            route_evidence = assess(view, raw)
        session.apply(role, raw)
        require(
            canonical(session.snapshot()["events"][-1]) == canonical(event),
            "accepted event differs",
        )
        turns.append(
            {"seq": event["seq"], "role": role, "phase": phase, "request": stamp}
        )
        offsets[role] += 1
    require(
        canonical(session.snapshot()) == canonical(cut), "reconstructed cut differs"
    )
    if len(events) == 3:
        route_evidence = assess(cli_view(session.view("loader")), None)
    return turns, route_evidence
