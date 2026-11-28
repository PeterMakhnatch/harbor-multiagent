import json
from collections import Counter
from dataclasses import replace
from itertools import product
from uuid import UUID

import pytest

from examples.seatlab.coupled_retention import native
from examples.seatlab.partner_switch import engine


def _read(packet):
    fields = json.loads(packet)
    assert packet == json.dumps(fields, separators=(",", ":")).encode("ascii")
    return fields


def _native_views(seed):
    world = native.Meeting(seed, "both", "deliver", str(UUID(int=seed)))
    briefs = {}
    for role in engine.ROLES:
        status, brief = world.handle(role, "GET", "/brief", {})
        assert status == 200
        briefs[role] = brief
        status, _ = world.handle(role, "POST", "/endorse", {"slot": brief["incumbent"]})
        assert status == 200
    cards = {}
    for role in engine.ROLES:
        status, card = world.handle(role, "GET", "/card", {})
        assert status == 200
        cards[role] = card["slots"]
    return world, briefs, cards


@pytest.mark.parametrize("start", [0, 321])
def test_six_balanced_calendars_expose_only_role_local_native_observations(start):
    cases = engine.cases(start)
    assert tuple(case.seed for case in cases) == native.balanced_seeds(start)
    assert len(cases) == 6
    active = sorted(native.permutation(start)[1:4])
    assert len({case.active for case in cases}) == 1
    for index in range(2):
        assert sorted(Counter(case.reports[index] for case in cases).values()) == [
            2,
            2,
            2,
        ]
    for case in cases:
        world, briefs, cards = _native_views(case.seed)
        outgoing = (cards["east"][0], cards["west"][1])
        for index, role in enumerate(engine.ROLES):
            report = _read(case.reports[index])
            assert report == {
                "schema": engine.SCHEMA,
                "stage": "report",
                "role": role,
                "instruction": engine.REPORT_INSTRUCTION,
                "incumbent": briefs[role]["incumbent"],
                "options": briefs[role]["options"],
                "active": active,
                "own": cards[role],
            }
            status, _ = world.handle(
                role, "POST", "/report", {"slots": [outgoing[index]]}
            )
            assert status == 200
        branch = engine.exchange(case, *outgoing)
        assert branch.reports == outgoing
        for index, role in enumerate(engine.ROLES):
            status, inbox = world.handle(role, "GET", "/inbox", {})
            assert status == 200 and inbox["delivered"] is True
            assert _read(branch.votes[index]) == {
                **_read(case.reports[index]),
                "stage": "vote",
                "instruction": engine.VOTE_INSTRUCTION,
                "sent": outgoing[index],
                "peer": inbox["slots"][0],
            }


@pytest.mark.parametrize("sender", [0, 1])
def test_replacement_report_changes_only_sent_and_receiver_peer(sender):
    for case in engine.cases(0):
        own = [_read(packet)["own"] for packet in case.reports]
        initial = [slots[0] for slots in own]
        before = engine.exchange(case, initial[0], initial[1])
        initial[sender] = own[sender][1]
        after = engine.exchange(case, initial[0], initial[1])
        assert before.reports[sender] != after.reports[sender]
        assert before.reports[1 - sender] == after.reports[1 - sender]
        for index in range(2):
            old, new = _read(before.votes[index]), _read(after.votes[index])
            changed = {key for key in old if old[key] != new[key]}
            assert changed == ({"sent"} if index == sender else {"peer"})
            assert new[next(iter(changed))] == initial[sender]


def test_exchange_rejects_unavailable_reports_and_stale_role_projection():
    case = engine.cases(0)[0]
    east, west = map(_read, case.reports)
    for slot in set(engine.OPTIONS) - set(east["own"]):
        with pytest.raises(ValueError, match="one own available slot"):
            engine.exchange(case, slot, west["own"][0])
    for slot in set(engine.OPTIONS) - set(west["own"]):
        with pytest.raises(ValueError, match="one own available slot"):
            engine.exchange(case, east["own"][0], slot)
    stale = replace(case, reports=(case.reports[1], case.reports[0]))
    with pytest.raises(ValueError, match="observations changed"):
        engine.exchange(stale, east["own"][0], west["own"][0])


def test_exchange_requires_native_delivery(monkeypatch):
    case = engine.cases(0)[0]
    meeting = native.Meeting

    def withhold(seed, fixture, channel, run):
        assert channel == "deliver"
        return meeting(seed, fixture, "withhold", run)

    monkeypatch.setattr(native, "Meeting", withhold)
    east, west = map(_read, case.reports)
    with pytest.raises(RuntimeError, match="peer report was not delivered"):
        engine.exchange(case, east["own"][0], west["own"][0])


def test_native_grading_clones_the_branch_for_every_legal_sealed_vote(monkeypatch):
    evaluate = native._verify.evaluate
    cuts = []

    def inspect(cut):
        cuts.append(cut)
        return evaluate(cut)

    monkeypatch.setattr(native._verify, "evaluate", inspect)
    case = engine.cases(0)[0]
    east, west = map(_read, case.reports)
    branch = engine.exchange(case, east["own"][0], west["own"][1])
    correct = native.permutation(case.seed)[1]
    actions = list(product(engine.OPTIONS, repeat=2)) + [(correct, correct)]
    for left, right in actions:
        assert engine.grade(branch, left, right) == int(left == right == correct)
    assert len(cuts) == len(actions)
    history = cuts[0]["events"][:-2]
    assert [(event["role"], event["kind"]) for event in history] == [
        (role, kind)
        for kind in ("endorse", "card", "report", "inbox")
        for role in engine.ROLES
    ]
    assert [event["value"] for event in history[4:6]] == [
        [slot] for slot in branch.reports
    ]
    assert all(event["value"] is True for event in history[6:8])
    for cut, (left, right) in zip(cuts, actions, strict=True):
        assert cut["fixture"] == "both"
        assert cut["channel"] == "deliver"
        assert cut["events"][:-2] == history
        assert cut["events"][-2:] == [
            {"n": 9, "role": "east", "kind": "vote", "value": left},
            {"n": 10, "role": "west", "kind": "vote", "value": right},
        ]
    with pytest.raises(ValueError, match="one legal slot"):
        engine.grade(branch, "?", correct)
