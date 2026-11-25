from copy import deepcopy
from importlib import import_module

import pytest

PACKAGE = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = import_module(f"{PACKAGE}.cases").make_case
Session = import_module(f"{PACKAGE}.engine").Session
grade = import_module(f"{PACKAGE}.grading").grade
act = import_module("examples.tasks.exhibit-relay.solution.policies").act
ROLES = ("loader", "registrar", "depot")
MODES = ("linked", "independent", "open", "silent")
TURNS = ("loader", "registrar", "depot", "loader")
PHASES = ("request", "registrar", "depot", "dispatch", "done")
VIEW_KEYS = {
    "role",
    "phase",
    "turn",
    "quota",
    "nominate",
    "exhibits",
    "crates",
    "bays",
    "briefs",
    "cards",
    "agenda",
    "registrar_file",
    "depot_file",
    "order",
}


def active_card(cards, key, epoch):
    matches = [c for c in cards if c["key"] == key and c["start"] <= epoch <= c["end"]]
    assert len(matches) == 1
    return matches[0]


def objective(cut):
    case, mode, events = cut["case"], cut["mode"], cut["events"]
    expected = {
        "status": "incomplete",
        "reward": None,
        "correct_items": None,
        "total_items": 2,
        "registrar_supported": None,
        "depot_supported": None,
        "chains_supported": None,
        "dispatch_supported": None,
    }
    if len(events) != 4:
        return expected
    order = next(b for b in case["briefs"] if b["id"] == case["realized"])
    reg_ids = set(events[1]["action"]["ids"]) if mode != "silent" else set()
    dep_ids = set(events[2]["action"]["ids"]) if mode != "silent" else set()
    routes = events[3]["action"]["routes"]
    correct = reg = dep = chains = dispatch = 0
    for item in order["items"]:
        a = active_card(case["registrar"], item, order["epoch"])
        b = active_card(case["depot"], a["value"], order["epoch"])
        r, d, c = a["id"] in reg_ids, b["id"] in dep_ids, routes[item] == b["value"]
        correct += c
        reg += r
        dep += d
        chains += r and d
        dispatch += r and d and c
    return {
        "status": "complete",
        "reward": int(correct == 2),
        "correct_items": correct,
        "total_items": 2,
        "registrar_supported": reg,
        "depot_supported": dep,
        "chains_supported": chains,
        "dispatch_supported": dispatch,
    }


def verify_views(session, case, mode, step):
    cut = session.snapshot()
    for role in ROLES:
        view = session.view(role)
        assert set(view) == VIEW_KEYS
        assert view["role"] == role
        assert view["phase"] == PHASES[step]
        assert view["turn"] == (TURNS[step] if step < 4 else None)
        for field in ("exhibits", "crates", "bays"):
            assert view[field] == case[field]
        if mode == "open":
            assert view["quota"] >= max(len(case[r]) for r in ROLES[1:])
            assert view["nominate"] == len(case["briefs"])
        else:
            assert view["quota"] == case["quota"]
            assert view["nominate"] == case["nominate"]
        assert sorted(view["cards"], key=lambda c: c["id"]) == sorted(
            case.get(role, []), key=lambda c: c["id"]
        )
        nominated = set(cut["events"][0]["action"]["slips"]) if step else set()
        briefs = (
            case["briefs"]
            if role == "loader"
            else [
                b
                for b in case["briefs"]
                if role == "registrar" and mode != "silent" and b["id"] in nominated
            ]
        )
        assert sorted(view["briefs"], key=lambda b: b["id"]) == sorted(
            briefs, key=lambda b: b["id"]
        )
        order = next(b for b in case["briefs"] if b["id"] == case["realized"])
        assert view["order"] == (order if role == "loader" and step >= 3 else None)
        for index, source in enumerate(ROLES[1:], 1):
            ids = cut["events"][index]["action"]["ids"] if step >= 3 else []
            expected = sorted(
                (c for c in case[source] if c["id"] in ids), key=lambda c: c["id"]
            )
            assert view[f"{source}_file"] == (
                expected if role == "loader" and mode != "silent" else []
            )
        intents = {}
        if role == "depot" and mode in ("linked", "open") and step >= 2:
            ids = set(cut["events"][1]["action"]["ids"])
            for brief in case["briefs"]:
                if brief["id"] not in nominated:
                    continue
                for item in brief["items"]:
                    card = active_card(case["registrar"], item, brief["epoch"])
                    if card["id"] in ids:
                        key = (card["value"], brief["epoch"])
                        intents[key] = intents.get(key, 0) + brief["weight"]
        assert view["agenda"] == [
            {"key": k, "epoch": e, "weight": w} for (k, e), w in sorted(intents.items())
        ]


def reject(session, role, action):
    before = session.snapshot()
    views = [session.view(r) for r in ROLES]
    with pytest.raises(ValueError):
        session.apply(role, action)
    assert session.snapshot() == before
    assert [session.view(r) for r in ROLES] == views


def run_lottery(case, mode):
    policy = "unfiltered" if mode == "open" else "portfolio"
    worlds = []
    for brief in case["briefs"]:
        variant = deepcopy(case)
        variant["realized"] = brief["id"]
        session = Session(variant, mode)
        variant["briefs"][0]["items"].clear()
        assert session.snapshot()["case"]["briefs"] == case["briefs"]
        worlds.append(session)
    weighted_supported = 0
    for step, role in enumerate(TURNS):
        baseline_views = [worlds[0].view(r) for r in ROLES]
        baseline_action = None
        for session in worlds:
            cut = session.snapshot()
            actual_case = cut["case"]
            verify_views(session, actual_case, mode, step)
            assert grade(cut) == objective(cut)
            if step < 3:
                assert [session.view(r) for r in ROLES] == baseline_views
            view = session.view(role)
            action = act(deepcopy(view), policy=policy)
            if step < 3:
                if baseline_action is None:
                    baseline_action = deepcopy(action)
                else:
                    assert action == baseline_action
            reject(session, ROLES[(ROLES.index(role) + 1) % len(ROLES)], action)
            field = "slips" if step == 0 else "ids"
            reject(session, role, {"op": "invalid"})
            if step < 3:
                available = view["briefs"] if step == 0 else view["cards"]
                reject(
                    session, role, {"op": action["op"], field: [available[0]["id"]] * 2}
                )
            else:
                reject(session, role, {"op": "commit", "routes": {}})
            original = deepcopy(view)
            view["exhibits"].clear()
            if view["cards"]:
                view["cards"][0]["value"] = None
            if view["briefs"]:
                view["briefs"][0]["items"].clear()
            if view["order"]:
                view["order"]["items"].clear()
            assert session.view(role) == original
            saved = session.snapshot()
            cut["case"]["briefs"].clear()
            cut["events"].clear()
            assert session.snapshot() == saved
            returned = session.apply(role, action)
            assert returned == session.view(role)
            event = session.snapshot()["events"][-1]
            assert event["seq"] == step + 1 and event["role"] == role
            if step < 3:
                assert event["action"] == {
                    "op": action["op"],
                    field: sorted(action[field]),
                }
            else:
                assert event["action"] == action
            action.clear()
            returned["bays"].clear()
            assert session.snapshot()["events"][-1] == event
            assert session.view(role)["bays"] == case["bays"]
    for session in worlds:
        cut = session.snapshot()
        actual_case = cut["case"]
        verify_views(session, actual_case, mode, 4)
        result = grade(cut)
        assert result == objective(cut)
        reject(session, "loader", cut["events"][-1]["action"])
        brief = next(b for b in case["briefs"] if b["id"] == actual_case["realized"])
        if result["chains_supported"] == 2:
            weighted_supported += brief["weight"]
            if mode in ("linked", "open"):
                assert result["reward"] == 1
        if mode == "open":
            assert result["dispatch_supported"] == 2
        if mode == "silent":
            assert result["chains_supported"] == result["dispatch_supported"] == 0
        changed = deepcopy(cut)
        changed["events"][-1]["action"]["routes"] = dict.fromkeys(
            brief["items"], case["bays"][0]
        )
        assert grade(changed) == objective(changed)
        assert grade(changed)["reward"] == 0
        assert grade(changed)["chains_supported"] == result["chains_supported"]
    return weighted_supported


def test_exhibit_relay_seeded_lottery_simulation():
    regimes = set()
    for profile, seeds in (("fork", range(16)), ("mesh", range(4))):
        for seed in seeds:
            case = make_case(seed, profile)
            assert make_case(seed, profile) == case
            if profile == "fork":
                regimes.add(tuple(sorted(b["weight"] for b in case["briefs"])))
            for mode in MODES:
                supported = run_lottery(case, mode)
                if profile == "fork" and mode == "linked":
                    assert sum(b["weight"] for b in case["briefs"]) == 10
                    assert supported == 6, (seed, mode)
    assert regimes == {(2, 2, 6), (3, 3, 4)}
