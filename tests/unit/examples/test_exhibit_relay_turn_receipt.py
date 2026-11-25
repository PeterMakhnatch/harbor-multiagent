import json
from copy import deepcopy
from importlib import import_module

import pytest

from examples.seatlab.exhibit_turn_receipt import reconcile
from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp

WORLD = "examples.tasks.exhibit-relay.environment.exhibit_relay"
Session = import_module(f"{WORLD}.engine").Session
make_case = import_module(f"{WORLD}.cases").make_case
act = import_module("examples.tasks.exhibit-relay.solution.policies").act
ROLES = ("loader", "registrar", "depot", "loader")
INSTRUCTIONS = {role: f"Native instruction for {role}." for role in ROLES}


def stamp(session, role, previous=None):
    view = json.loads(json.dumps(session.view(role), sort_keys=True))
    return request_stamp(
        encode_request(render_turn(INSTRUCTIONS[role], view, previous))
    )


def archive(mode="authored", count=4, seed=23):
    session = Session(make_case(seed, "coupling", layout="a"), mode)
    owners = dict.fromkeys(ROLES)
    expected = []
    for role in ROLES[:count]:
        view = session.view(role)
        action = act(view, "portfolio")
        if view["phase"] == "request":
            action = {"slips": sorted(action["slips"], reverse=True), "op": "request"}
        elif role == "registrar":
            notes = action["notes"]
            assert len(notes) > 1
            action = {
                "notes": [
                    {
                        "weight": row["weight"],
                        "keys": row["keys"][::-1],
                        "epoch": row["epoch"],
                    }
                    for row in reversed(notes)
                ],
                "ids": sorted(action["ids"], reverse=True),
                "op": "file",
            }
        elif role == "depot":
            action["ids"] = sorted(action["ids"], reverse=True)
        else:
            action = {
                "routes": dict(sorted(action["routes"].items(), reverse=True)),
                "op": "commit",
            }
        if owners[role] is None:
            owners[role] = {
                "role": role,
                "accepted_actions": [],
                "accepted_requests": [],
                "phase": None,
            }
        owner = owners[role]
        previous = owner["accepted_actions"][-1] if owner["accepted_actions"] else None
        request = stamp(session, role, previous)
        session.apply(role, action)
        owner["accepted_actions"].append(action)
        owner["accepted_requests"].append(request)
        owner["phase"] = (
            "dispatch"
            if role == "loader" and view["phase"] == "request"
            else "finished"
        )
        expected.append(request)
    return session.snapshot(), owners, expected


@pytest.mark.parametrize("mode", ["authored", "author-masked"])
@pytest.mark.parametrize("count", range(5))
def test_native_prefix(mode, count):
    cut, owners, expected = archive(mode, count)
    matched = reconcile(cut, owners, INSTRUCTIONS)
    assert isinstance(matched, list)
    assert [row["role"] for row in matched] == list(ROLES[:count])
    for row, request in zip(matched, expected, strict=True):
        encoded = json.dumps(row, sort_keys=True)
        assert request["sha256"] in encoded
        assert str(request["bytes"]) in encoded
    encoded = json.dumps(matched)
    for field in ("exhibits", "crates", "bays"):
        assert all(label not in encoded for label in cut["case"][field])
    for role in ("registrar", "depot"):
        assert all(card["id"] not in encoded for card in cut["case"][role])
    assert all(brief["id"] not in encoded for brief in cut["case"]["briefs"])
    assert '"notes"' not in encoded
    assert '"accepted_actions"' not in encoded


def test_world_normalization_preserves_native_request_context():
    cut, owners, expected = archive()
    request = owners["loader"]["accepted_actions"][0]
    assert list(request) == ["slips", "op"]
    assert request["slips"] == cut["events"][0]["action"]["slips"][::-1]
    raw = owners["registrar"]["accepted_actions"][0]
    accepted = cut["events"][1]["action"]
    assert raw["ids"] == accepted["ids"][::-1]
    assert raw["notes"] != accepted["notes"]
    assert raw["notes"][0]["keys"] == accepted["notes"][-1]["keys"][::-1]
    assert len(reconcile(cut, owners, INSTRUCTIONS)) == len(expected)


@pytest.mark.parametrize("field", ["sha256", "bytes"])
def test_request_stamp_mismatch(field):
    cut, owners, _ = archive()
    request = owners["depot"]["accepted_requests"][0]
    request[field] = "0" * 64 if field == "sha256" else request[field] + 1
    with pytest.raises(ValueError):
        reconcile(cut, owners, INSTRUCTIONS)


def test_mismatched_owner_role():
    cut, owners, _ = archive()
    owners["registrar"]["role"] = "depot"
    with pytest.raises(ValueError):
        reconcile(cut, owners, INSTRUCTIONS)


@pytest.mark.parametrize("role", ["loader", "registrar", "depot"])
def test_accepted_action_mismatch(role):
    cut, owners, _ = archive()
    action = owners[role]["accepted_actions"][0]
    action["slips" if role == "loader" else "ids"].pop()
    with pytest.raises(ValueError):
        reconcile(cut, owners, INSTRUCTIONS)


@pytest.mark.parametrize("previous_kind", ["normalized", "missing", "sorted_keys"])
def test_stale_loader_context(previous_kind):
    cut, owners, _ = archive()
    session = Session(cut["case"], cut["mode"])
    for event in cut["events"][:3]:
        session.apply(event["role"], event["action"])
    previous = {
        "normalized": cut["events"][0]["action"],
        "missing": None,
        "sorted_keys": json.loads(
            json.dumps(owners["loader"]["accepted_actions"][0], sort_keys=True)
        ),
    }[previous_kind]
    stale = stamp(session, "loader", previous)
    assert stale != owners["loader"]["accepted_requests"][1]
    owners["loader"]["accepted_requests"][1] = stale
    with pytest.raises(ValueError):
        reconcile(cut, owners, INSTRUCTIONS)


def test_unrelated_roster():
    cut, _, _ = archive(seed=23)
    _, unrelated, _ = archive(seed=24)
    with pytest.raises(ValueError):
        reconcile(cut, unrelated, INSTRUCTIONS)


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_extra_reported_actions(count):
    cut, owners, _ = archive(count=count)
    _, complete, _ = archive()
    role = ROLES[min(count, 3)]
    if count < 4:
        owners[role] = deepcopy(complete[role])
    else:
        owners[role]["accepted_actions"].append(
            deepcopy(owners[role]["accepted_actions"][-1])
        )
        owners[role]["accepted_requests"].append(
            deepcopy(owners[role]["accepted_requests"][-1])
        )
    with pytest.raises(ValueError):
        reconcile(cut, owners, INSTRUCTIONS)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_missing_metadata_for_accepted_event(count):
    cut, owners, _ = archive(count=count)
    owners[ROLES[count - 1]] = None
    with pytest.raises(ValueError):
        reconcile(cut, owners, INSTRUCTIONS)


@pytest.mark.parametrize("count", [0, 1])
def test_empty_report_is_not_a_substitute_for_accepted_event(count):
    cut, owners, _ = archive(count=count)
    owners["loader"] = {
        "role": "loader",
        "accepted_actions": [],
        "accepted_requests": [],
        "phase": "request",
    }
    if count == 0:
        assert reconcile(cut, owners, INSTRUCTIONS) == []
    else:
        with pytest.raises(ValueError):
            reconcile(cut, owners, INSTRUCTIONS)
