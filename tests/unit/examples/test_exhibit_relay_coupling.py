import importlib
import json
from copy import deepcopy
from itertools import combinations

import pytest

PACKAGE = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = importlib.import_module(f"{PACKAGE}.cases").make_case
validate_case = importlib.import_module(f"{PACKAGE}.cases").validate_case
Session = importlib.import_module(f"{PACKAGE}.engine").Session
grade = importlib.import_module(f"{PACKAGE}.grading").grade
act = importlib.import_module("examples.tasks.exhibit-relay.solution.policies").act
analyze_frontier = importlib.import_module(f"{PACKAGE}.frontier").analyze_frontier
certify = importlib.import_module("examples.seatlab.exhibit_twins").certify
ROLES = ("loader", "registrar", "depot")
SEEDS = (0, 17, 983, 2**63 - 1)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def record(cards, key, epoch):
    matches = [c for c in cards if c["key"] == key and c["start"] <= epoch <= c["end"]]
    assert len(matches) == 1
    return matches[0]


def requirements(case, brief):
    epoch = brief["epoch"]
    sources = [record(case["registrar"], item, epoch) for item in brief["items"]]
    destinations = [record(case["depot"], source["value"], epoch) for source in sources]
    return sources, destinations


def ids(cards):
    return {card["id"] for card in cards}


def early(case, mode):
    session = Session(case, mode)
    for role in ROLES[:2]:
        view = session.view(role)
        session.apply(role, act(view, "portfolio"))
    return session


def file_action(selected):
    return {"op": "file", "ids": sorted(selected)}


def expected_bundles(case, sources, nominated):
    bundles = []
    for brief in case["briefs"]:
        required, _ = requirements(case, brief)
        if brief["id"] in nominated and ids(required) <= sources:
            bundles.append(
                {
                    "epoch": brief["epoch"],
                    "keys": sorted(c["value"] for c in required),
                    "weight": brief["weight"],
                }
            )
    return sorted(bundles, key=lambda b: (b["epoch"], b["keys"]))


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_twins_change_only_joint_weights(seed):
    a, b = [make_case(seed, "coupling", layout=layout) for layout in ("a", "b")]
    assert {k: v for k, v in a.items() if k not in ("briefs", "realized")} == {
        k: v for k, v in b.items() if k not in ("briefs", "realized")
    }
    assert len(a["briefs"]) == len(b["briefs"]) == 4
    for left, right in zip(a["briefs"], b["briefs"], strict=True):
        assert {k: v for k, v in left.items() if k != "weight"} == {
            k: v for k, v in right.items() if k != "weight"
        }
        assert {left["weight"], right["weight"]} == {1, 4}
    views = {}
    for layout, case in (("a", a), ("b", b)):
        for mode in ("linked", "bundled"):
            session = early(case, mode)
            events = session.snapshot()["events"]
            nominated = {brief["id"] for brief in case["briefs"]}
            sources = set().union(
                *(ids(requirements(case, brief)[0]) for brief in case["briefs"])
            )
            assert set(events[0]["action"]["slips"]) == nominated
            assert set(events[1]["action"]["ids"]) == sources
            assert len(sources) == 4
            view = session.view("depot")
            assert len(view["agenda"]) == 4
            assert [entry["weight"] for entry in view["agenda"]] == [5] * 4
            assert view["bundles"] == (
                expected_bundles(case, sources, nominated) if mode == "bundled" else []
            )
            assert (
                session.view("registrar")["bundles"]
                == session.view("loader")["bundles"]
                == []
            )
            views[layout, mode] = view
        assert canonical(views[layout, "linked"]) == canonical(
            {**views[layout, "bundled"], "bundles": []}
        )
    assert canonical(views["a", "linked"]) == canonical(views["b", "linked"])
    assert views["a", "bundled"]["bundles"] != views["b", "bundled"]["bundles"]


@pytest.mark.parametrize("mode", ("linked", "bundled", "independent", "silent", "open"))
def test_future_draw_cannot_change_any_pre_dispatch_input(mode):
    case = make_case(17, "coupling", layout="a")
    sessions = [
        Session({**case, "realized": brief["id"]}, mode) for brief in case["briefs"]
    ]
    for turn in ROLES:
        for role in ROLES:
            views = [session.view(role) for session in sessions]
            assert all(canonical(view) == canonical(views[0]) for view in views)
            assert views[0]["order"] is None
        action = act(sessions[0].view(turn), "portfolio")
        for session in sessions:
            session.apply(turn, action)
    assert {session.view("loader")["order"]["id"] for session in sessions} == {
        b["id"] for b in case["briefs"]
    }


def test_partial_filing_does_not_invent_bundles():
    case = make_case(17, "coupling", layout="b")
    brief = case["briefs"][0]
    sources, _ = requirements(case, brief)
    session = Session(case, "bundled")
    session.apply("loader", {"op": "request", "slips": [brief["id"]]})
    session.apply("registrar", file_action(ids(sources[:1])))
    view = session.view("depot")
    assert view["bundles"] == []
    assert view["agenda"] == [
        {"epoch": brief["epoch"], "key": sources[0]["value"], "weight": brief["weight"]}
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_certificate_against_full_archive_combinatorics(seed):
    cases = [make_case(seed, "coupling", layout=layout) for layout in ("a", "b")]
    archive = sorted(ids(cases[0]["depot"]))
    assert len(archive) >= 8
    candidates = [
        set(pair) for size in range(3) for pair in combinations(archive, size)
    ]
    weights = [
        [
            sum(
                b["weight"]
                for b in case["briefs"]
                if ids(requirements(case, b)[1]) <= candidate
            )
            for candidate in candidates
        ]
        for case in cases
    ]
    common = max(sum(pair) for pair in zip(*weights, strict=True))
    grouped = sum(max(row) for row in weights)
    total = sum(b["weight"] for case in cases for b in case["briefs"])
    assert (common, grouped, total) == (5, 8, 20)
    certificate = certify(seed)
    assert certificate["flat_common_weight"] == common
    assert certificate["grouped_role_weight"] == grouped
    assert certificate["total_weight"] == total
    for case in cases:
        for mode in ("linked", "bundled"):
            frontier = analyze_frontier(case, mode)
            assert (frontier["ceiling_weight"], frontier["weight"]) == (4, 10)
        session = early(case, "bundled")
        action = act(session.view("depot"), "portfolio")
        assert (
            sum(
                b["weight"]
                for b in case["briefs"]
                if ids(requirements(case, b)[1]) <= set(action["ids"])
            )
            == 4
        )


@pytest.mark.parametrize("layout", ("a", "b"))
@pytest.mark.parametrize("selection", ("heavy", "light", "portfolio"))
def test_physical_grading_for_every_realized_alternative(layout, selection):
    case = make_case(983, "coupling", layout=layout)
    target = next(
        b for b in case["briefs"] if b["weight"] == (1 if selection == "light" else 4)
    )
    selected = ids(requirements(case, target)[1])
    if selection == "portfolio":
        session = early(case, "bundled")
        selected = set(act(session.view("depot"), "portfolio")["ids"])
    covered_weight = 0
    for brief in case["briefs"]:
        session = early({**case, "realized": brief["id"]}, "bundled")
        session.apply("depot", file_action(selected))
        _, destinations = requirements(case, brief)
        expected = {
            item: card["value"]
            for item, card in zip(brief["items"], destinations, strict=True)
        }
        supported = len(ids(destinations) & selected)
        session.apply("loader", act(session.view("loader"), "portfolio"))
        cut = session.snapshot()
        routes = cut["events"][-1]["action"]["routes"]
        result = grade(cut)
        correct = sum(routes[item] == bay for item, bay in expected.items())
        assert result["correct_items"] == correct
        assert result["reward"] == int(correct == 2)
        assert result["registrar_supported"] == 2
        assert result["depot_supported"] == result["chains_supported"] == supported
        if supported == 2:
            assert result["reward"] == 1
            covered_weight += brief["weight"]
    assert covered_weight == (1 if selection == "light" else 4)


@pytest.mark.parametrize("mode", ("silent", "open"))
def test_silent_and_open_physical_answer_is_separate_from_support(mode):
    case = make_case(0, "coupling")
    session = Session(case, mode)
    session.apply(
        "loader", {"op": "request", "slips": sorted(b["id"] for b in case["briefs"])}
    )
    registrar = session.view("registrar")
    assert registrar["quota"] == (40 if mode == "open" else 4)
    assert bool(registrar["briefs"]) == (mode == "open")
    brief = next(b for b in case["briefs"] if b["id"] == case["realized"])
    sources, destinations = requirements(case, brief)
    session.apply(
        "registrar", file_action(ids(case["registrar"] if mode == "open" else sources))
    )
    depot = session.view("depot")
    assert depot["quota"] == (40 if mode == "open" else 2)
    assert bool(depot["agenda"]) == (mode == "open")
    assert depot["bundles"] == []
    session.apply(
        "depot", file_action(ids(case["depot"] if mode == "open" else destinations))
    )
    view = session.view("loader")
    assert bool(view["registrar_file"]) == bool(view["depot_file"]) == (mode == "open")
    routes = {
        item: card["value"]
        for item, card in zip(brief["items"], destinations, strict=True)
    }
    session.apply("loader", {"op": "commit", "routes": routes})
    result = grade(session.snapshot())
    assert result["reward"] == 1
    assert (
        result["chains_supported"]
        == result["dispatch_supported"]
        == (2 if mode == "open" else 0)
    )


@pytest.mark.parametrize("role,cap", (("registrar", 4), ("depot", 2)))
def test_actual_role_cap_enforced_by_engine_and_grader(role, cap):
    case = make_case(17, "coupling")
    session = Session(case, "bundled")
    session.apply("loader", act(session.view("loader")))
    if role == "depot":
        session.apply("registrar", act(session.view("registrar")))
    assert session.view(role)["quota"] == cap
    before = session.snapshot()
    action = file_action(sorted(ids(case[role]))[: cap + 1])
    with pytest.raises(ValueError):
        session.apply(role, action)
    assert session.snapshot() == before
    invalid = deepcopy(before)
    invalid["events"].append(
        {"seq": len(before["events"]) + 1, "role": role, "action": action}
    )
    with pytest.raises(ValueError):
        grade(invalid)
    session.apply(role, file_action(sorted(ids(case[role]))[:cap]))
    assert grade(session.snapshot())["status"] == "incomplete"


@pytest.mark.parametrize(
    "corruption", ("history_gap", "history_overlap", "wrong_weight", "mixed_epoch")
)
def test_invalid_coupling_case_rejected(corruption):
    case = make_case(17, "coupling")
    if corruption == "history_gap":
        case["depot"].pop()
    elif corruption == "history_overlap":
        case["registrar"].append({**case["registrar"][0], "id": "extra-card"})
    elif corruption == "wrong_weight":
        case["briefs"][0]["weight"] = 3
    else:
        brief = case["briefs"][0]
        brief["epoch"] = (brief["epoch"] + 1) % 3
    with pytest.raises(ValueError):
        validate_case(case)
    with pytest.raises(ValueError):
        Session(case, "bundled")
    with pytest.raises(ValueError):
        grade({"version": 1, "case": case, "mode": "bundled", "events": []})


@pytest.mark.parametrize(
    "profile,mode", (("fork", "bundled"), ("mesh", "bundled"), ("coupling", "draw"))
)
def test_invalid_profile_mode_combination(profile, mode):
    case = make_case(17, profile)
    with pytest.raises(ValueError):
        Session(case, mode)
    with pytest.raises(ValueError):
        grade({"version": 1, "case": case, "mode": mode, "events": []})
    with pytest.raises(ValueError):
        analyze_frontier(case, mode)
