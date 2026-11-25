import json
from copy import deepcopy
from typing import Any

import pytest

from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp
from examples.seatlab.physical_dispatch import certificate, policies
from examples.seatlab.physical_dispatch.physical_history import match_history

ROLES = ("loader", "registrar", "depot")
TURNS = (*ROLES, "loader")
PHASES = ("request", "registrar", "depot", "dispatch")
NEXT = ("dispatch", "finished", "finished", "finished")
INSTRUCTIONS = {role: f"Instruction for {role}" for role in ROLES}


def seat_view(session: Any, role: str) -> dict[str, Any]:
    return json.loads(json.dumps(session.view(role), sort_keys=True))


def fixture(
    count: int, map_name: str, filing: str, *, alternate_at: int = -1
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    _, cases = certificate.native_pair(10)
    session = certificate.Session(cases[map_name], "linked")
    metadata = {
        role: {
            "role": role,
            "phase": None,
            "accepted_actions": [],
            "accepted_requests": [],
        }
        for role in ROLES
    }
    rows = []
    for step, (role, phase) in enumerate(
        zip(TURNS[:count], PHASES[:count], strict=True)
    ):
        view = seat_view(session, role)
        owner = metadata[role]
        actions = owner["accepted_actions"]
        previous = actions[-1] if actions else None
        stamp = request_stamp(
            encode_request(render_turn(INSTRUCTIONS[role], view, previous))
        )
        if step == 0:
            action = policies.nominate(view)
        elif step == 1:
            action = policies.registrar(view)
        elif step == 2:
            action = policies.depot(view, filing)
        else:
            action = {
                "op": "commit",
                "routes": {item: min(view["bays"]) for item in view["order"]["items"]},
            }
        if step == alternate_at:
            action = {"op": action["op"], "slips" if step == 0 else "ids": []}
        session.apply(role, action)
        actions.append(action)
        owner["accepted_requests"].append(stamp)
        owner["phase"] = NEXT[step]
        rows.append({"seq": step + 1, "role": role, "phase": phase, "request": stamp})
    return session.snapshot(), metadata, rows


@pytest.mark.parametrize("count", range(5))
@pytest.mark.parametrize("filing", ("other", "relevant"))
@pytest.mark.parametrize("map_name", ("original", "swapped"))
def test_history_prefix(count: int, filing: str, map_name: str) -> None:
    cut, metadata, rows = fixture(count, map_name, filing)
    assert match_history(cut, metadata, INSTRUCTIONS, filing) == rows
    if count < 4:
        _, longer, _ = fixture(count + 1, map_name, filing)
        with pytest.raises(ValueError):
            match_history(cut, longer, INSTRUCTIONS, filing)
    for role in ROLES:
        if not metadata[role]["accepted_actions"]:
            metadata[role] = None
    assert match_history(cut, metadata, INSTRUCTIONS, filing) == rows


@pytest.mark.parametrize("step", range(3))
def test_history_rejects_legal_but_unpublished_action(step: int) -> None:
    cut, metadata, _ = fixture(step + 1, "original", "relevant", alternate_at=step)
    with pytest.raises(ValueError, match="published policy"):
        match_history(cut, metadata, INSTRUCTIONS, "relevant")


def test_history_rejects_cross_prefix_substitution() -> None:
    cut, metadata, _ = fixture(4, "original", "relevant")
    _, other, _ = fixture(4, "original", "other")
    for role in ("loader", "depot"):
        substituted = {**metadata, role: other[role]}
        with pytest.raises(ValueError):
            match_history(cut, substituted, INSTRUCTIONS, "relevant")
    _, swapped, _ = fixture(4, "swapped", "relevant")
    with pytest.raises(ValueError, match="request stamp"):
        match_history(
            cut, {**metadata, "depot": swapped["depot"]}, INSTRUCTIONS, "relevant"
        )
    with pytest.raises(ValueError, match="published policy"):
        match_history(cut, metadata, INSTRUCTIONS, "other")


def test_history_final_request_requires_own_previous_action_and_private_view() -> None:
    cut, metadata, _ = fixture(4, "original", "relevant")
    session = certificate.Session(cut["case"], "linked")
    for event in cut["events"][:3]:
        session.apply(event["role"], event["action"])
    view = seat_view(session, "loader")
    previous = metadata["loader"]["accepted_actions"][0]
    contaminated = {**view, "cards": seat_view(session, "depot")["cards"]}
    for wrong_view, wrong_previous in (
        (view, None),
        (view, metadata["depot"]["accepted_actions"][0]),
        (contaminated, previous),
    ):
        wrong = deepcopy(metadata)
        wrong["loader"]["accepted_requests"][1] = request_stamp(
            encode_request(
                render_turn(INSTRUCTIONS["loader"], wrong_view, wrong_previous)
            )
        )
        with pytest.raises(ValueError, match="request stamp"):
            match_history(cut, wrong, INSTRUCTIONS, "relevant")
    changed = {**INSTRUCTIONS, "loader": "Different instruction"}
    with pytest.raises(ValueError, match="request stamp"):
        match_history(cut, metadata, changed, "relevant")


def test_history_rejects_unsupported_assumptions_and_invalid_metadata() -> None:
    cut, metadata, _ = fixture(4, "original", "relevant")
    for wrong in (
        {**cut, "mode": "independent"},
        {**cut, "case": certificate.make_case(7, "fork", layout="draw")},
        {**cut, "events": list(reversed(cut["events"]))},
    ):
        with pytest.raises(ValueError):
            match_history(wrong, metadata, INSTRUCTIONS, "relevant")
    with pytest.raises(ValueError):
        match_history(cut, metadata, INSTRUCTIONS, "unknown")
    bad = deepcopy(metadata)
    bad["loader"]["accepted_requests"][0]["bytes"] += 1
    with pytest.raises(ValueError, match="request stamp"):
        match_history(cut, bad, INSTRUCTIONS, "relevant")
    bad = deepcopy(metadata)
    bad["loader"]["phase"] = "dispatch"
    with pytest.raises(ValueError):
        match_history(cut, bad, INSTRUCTIONS, "relevant")
