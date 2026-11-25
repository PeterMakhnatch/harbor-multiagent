import importlib
from copy import deepcopy

import pytest

PACKAGE = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = importlib.import_module(f"{PACKAGE}.cases").make_case
Session = importlib.import_module(f"{PACKAGE}.engine").Session
grade = importlib.import_module(f"{PACKAGE}.grading").grade
act = importlib.import_module("examples.tasks.exhibit-relay.solution.policies").act

SEEDS = (0, 17, 983, 2**63 - 1)
LAYOUTS = ("a", "b")
MODES = ("authored", "author-masked")
ROLES = ("loader", "registrar", "depot")


def current_card(cards, key, epoch):
    matches = [c for c in cards if c["key"] == key and c["start"] <= epoch <= c["end"]]
    assert len(matches) == 1
    return matches[0]


def sources(case, brief):
    return [
        current_card(case["registrar"], item, brief["epoch"]) for item in brief["items"]
    ]


def destinations(case, brief):
    return [
        current_card(case["depot"], card["value"], brief["epoch"])
        for card in sources(case, brief)
    ]


def claims(case):
    return sorted(
        [
            {
                "epoch": brief["epoch"],
                "keys": sorted(card["value"] for card in sources(case, brief)),
                "weight": brief["weight"],
            }
            for brief in case["briefs"]
        ],
        key=lambda row: (row["epoch"], row["keys"]),
    )


def request(case, mode):
    session = Session(case, mode)
    session.apply("loader", act(session.view("loader"), "portfolio"))
    return session


def check_view_pair(sessions, expected_notes):
    left, right = sessions
    for role in ROLES:
        a, b = left.view(role), right.view(role)
        assert ("note_capacity" in a) == (role == "registrar")
        assert ("note_capacity" in b) == (role == "registrar")
        if role == "registrar":
            assert a["note_capacity"] == b["note_capacity"] == 4
        if role == "depot":
            assert a["bundles"] == expected_notes
            masked = [{**row, "weight": 0} for row in expected_notes]
            assert b["bundles"] == masked
            assert {k: v for k, v in a.items() if k != "bundles"} == {
                k: v for k, v in b.items() if k != "bundles"
            }
        else:
            assert a == b


def check_physical_grade(case, session):
    cut = session.snapshot()
    events = cut["events"]
    brief = next(b for b in case["briefs"] if b["id"] == case["realized"])
    src, dst = sources(case, brief), destinations(case, brief)
    registrar_ids = set(events[1]["action"]["ids"])
    depot_ids = set(events[2]["action"]["ids"])
    routes = events[3]["action"]["routes"]
    correct = registrar = depot = chains = dispatch = 0
    for item, source, destination in zip(brief["items"], src, dst, strict=True):
        r = source["id"] in registrar_ids
        d = destination["id"] in depot_ids
        c = routes[item] == destination["value"]
        correct += c
        registrar += r
        depot += d
        chains += r and d
        dispatch += r and d and c
    result = grade(cut)
    assert result == {
        "status": "complete",
        "reward": int(correct == 2),
        "correct_items": correct,
        "total_items": 2,
        "registrar_supported": registrar,
        "depot_supported": depot,
        "chains_supported": chains,
        "dispatch_supported": dispatch,
    }
    return result


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_role_local_authored_policy_and_world_authored_controls(seed, layout):
    case = make_case(seed, "coupling", layout=layout)
    gold = claims(case)
    sessions = [request(case, mode) for mode in MODES]
    check_view_pair(sessions, [])
    actions = [act(s.view("registrar"), "portfolio") for s in sessions]
    assert actions[0] == actions[1]
    action = actions[0]
    assert set(action) == {"op", "ids", "notes"}
    assert action["op"] == "file"
    assert len(action["ids"]) == 4
    assert sorted(action["notes"], key=lambda n: (n["epoch"], n["keys"])) == gold
    for session in sessions:
        session.apply("registrar", deepcopy(action))
    check_view_pair(sessions, gold)
    for mode in ("linked", "masked", "bundled"):
        world = request(case, mode)
        assert all("note_capacity" not in world.view(role) for role in ROLES)
        legacy = act(world.view("registrar"), "portfolio")
        assert set(legacy) == {"op", "ids"}
        assert legacy["ids"] == action["ids"]
        world.apply("registrar", legacy)
        expected = {
            "linked": [],
            "masked": [{**row, "weight": 0} for row in gold],
            "bundled": gold,
        }[mode]
        assert world.view("depot")["bundles"] == expected
        assert world.view("depot")["agenda"] == sessions[0].view("depot")["agenda"]
    for session in sessions:
        session.apply("depot", act(session.view("depot"), "portfolio"))
        session.apply("loader", act(session.view("loader"), "portfolio"))
        check_physical_grade(case, session)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("claim_kind", ("omit", "one", "wrong"))
def test_authored_delivery_is_not_truth_or_reward(seed, layout, claim_kind):
    case = make_case(seed, "coupling", layout=layout)
    gold = claims(case)
    selected = dict(gold[0])
    if claim_kind == "wrong":
        selected["weight"] = 4 if selected["weight"] == 1 else 1
        assert selected["weight"] != gold[0]["weight"]
        assert selected not in gold
    notes = [] if claim_kind == "omit" else [selected]
    sessions = [request(case, mode) for mode in MODES]
    registrar = act(sessions[0].view("registrar"), "portfolio")
    action = {**registrar, "notes": notes}
    for session in sessions:
        session.apply("registrar", deepcopy(action))
        assert session.snapshot()["events"][1]["action"]["notes"] == notes
        assert grade(session.snapshot())["status"] == "incomplete"
    check_view_pair(sessions, notes)

    # The same depot action in both arms does not depend on the received notes.
    # A filing event proves participation, not that the depot read the notes.
    depot_action = {"op": "file", "ids": []}
    for session in sessions:
        session.apply("depot", depot_action)
    check_view_pair(sessions, notes)
    brief = next(b for b in case["briefs"] if b["id"] == case["realized"])
    correct_bays = [card["value"] for card in destinations(case, brief)]
    routes = {
        item: next(bay for bay in case["bays"] if bay != correct)
        for item, correct in zip(brief["items"], correct_bays, strict=True)
    }
    for session in sessions:
        session.apply("loader", {"op": "commit", "routes": routes})
    check_view_pair(sessions, notes)
    a, b = [check_physical_grade(case, session) for session in sessions]
    assert a == b
    assert a["reward"] == a["correct_items"] == a["chains_supported"] == 0
    assert a["registrar_supported"] == 2

    # Hold all physical events fixed while varying the registrar annotation.
    # Test correct routes too, to separate physical reward from note accuracy.
    results = []
    for mode in MODES:
        for alternative in ([], [gold[0]], notes):
            session = request(case, mode)
            session.apply("registrar", {**action, "notes": alternative})
            session.apply("depot", depot_action)
            session.apply(
                "loader",
                {
                    "op": "commit",
                    "routes": dict(zip(brief["items"], correct_bays, strict=True)),
                },
            )
            results.append(check_physical_grade(case, session))
    assert all(result == results[0] for result in results)
    assert results[0]["reward"] == 1
    assert results[0]["dispatch_supported"] == 0


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("layout", LAYOUTS)
def test_masked_note_pair_selection_is_an_implicit_channel(seed, layout):
    case = make_case(seed, "coupling", layout=layout)
    first, second = claims(case)[:2]
    assert first["keys"] != second["keys"]
    sessions = [request(case, "author-masked") for _ in range(2)]
    action = act(sessions[0].view("registrar"), "portfolio")
    for session, selected in zip(sessions, (first, second), strict=True):
        session.apply("registrar", {**action, "notes": [selected]})
    left, right = [session.view("depot") for session in sessions]
    assert left["bundles"] == [{**first, "weight": 0}]
    assert right["bundles"] == [{**second, "weight": 0}]
    assert left["bundles"] != right["bundles"]
    assert {k: v for k, v in left.items() if k != "bundles"} == {
        k: v for k, v in right.items() if k != "bundles"
    }
    # Masking preserves the choice of which rows to send. It is not a
    # no-communication control, even with identical physical filings.
