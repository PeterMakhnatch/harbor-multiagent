from typing import Any

from ..exhibit_authored import canonical, require
from ..exhibit_owner import _arguments, _check_view
from ..exhibit_turn_receipt import _ROLE_PHASES, _validate_metadata
from ..exhibit_wire import encode_request, render_turn, request_stamp
from . import policies
from .certificate import Session, cli_view, grade


def _published(view: dict[str, Any], phase: str, filing: str) -> dict[str, Any] | None:
    if phase == "request":
        return policies.nominate(view)
    if phase == "registrar":
        return policies.registrar(view)
    if phase == "depot":
        return policies.depot(view, filing)
    require(phase == "dispatch", "unsupported phase")
    return None


def match_history(
    cut: dict[str, Any],
    meta_by_role: dict[str, dict[str, Any] | None],
    instructions: dict[str, str],
    filing: str,
) -> list[dict[str, Any]]:
    grade(cut)
    case = cut["case"]
    require(
        cut["mode"] == "linked" and case["profile"] == "fork", "not a linked fork cut"
    )
    require(
        case["realized"] == min(brief["id"] for brief in case["briefs"]),
        "not a focal cut",
    )
    require(filing in ("other", "relevant"), "unsupported filing policy")
    require(
        type(meta_by_role) is dict and meta_by_role.keys() == _ROLE_PHASES.keys(),
        "invalid owner roles",
    )
    require(
        type(instructions) is dict and instructions.keys() == _ROLE_PHASES.keys(),
        "invalid instruction roles",
    )
    require(
        all(type(text) is str for text in instructions.values()), "invalid instruction"
    )
    events = cut["events"]
    for role in _ROLE_PHASES:
        _validate_metadata(
            role, sum(event["role"] == role for event in events), meta_by_role[role]
        )

    session = Session(case, "linked")
    offsets = dict.fromkeys(_ROLE_PHASES, 0)
    receipts = []
    for event in events:
        role = event["role"]
        index = offsets[role]
        phase = _ROLE_PHASES[role][index]
        metadata = meta_by_role[role]
        if metadata is None:
            raise ValueError("missing accepted owner metadata")
        actions = metadata["accepted_actions"]
        raw = actions[index]
        require(type(raw) is dict, "invalid raw owner action")
        previous = actions[index - 1] if index else None
        view = cli_view(session.view(role))
        _check_view(view, role, phase)
        require(view["turn"] == role, "owner turn mismatch")
        stamp = request_stamp(
            encode_request(render_turn(instructions[role], view, previous))
        )
        require(
            stamp == metadata["accepted_requests"][index],
            "owner request stamp mismatch",
        )
        expected = _published(view, phase, filing)
        _arguments(raw, view, phase)
        session.apply(role, raw)
        accepted = session.snapshot()["events"][-1]
        require(
            canonical(accepted) == canonical(event), "owner accepted event mismatch"
        )
        if expected is not None:
            require(
                canonical(accepted["action"]) == canonical(expected),
                "accepted action differs from published policy",
            )
        offsets[role] += 1
        receipts.append(
            {"seq": event["seq"], "role": role, "phase": phase, "request": stamp}
        )
    require(
        canonical(session.snapshot()) == canonical(cut), "reconstructed cut mismatch"
    )
    return receipts
