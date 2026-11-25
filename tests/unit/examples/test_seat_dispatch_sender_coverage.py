import json
from itertools import combinations

import pytest

from examples.seatlab.physical_dispatch.certificate import Session, make_case
from examples.seatlab.physical_dispatch.stage_labels import summarize_handoffs


def _facts(view):
    if view["role"] == "registrar":
        return {
            (item, brief["epoch"])
            for brief in view["briefs"]
            for item in brief["items"]
        }
    return {(row["key"], row["epoch"]) for row in view["agenda"]}


def _covered(cards, facts):
    return {
        (key, epoch)
        for key, epoch in facts
        if any(c["key"] == key and c["start"] <= epoch <= c["end"] for c in cards)
    }


def _needed(view):
    facts = _facts(view)
    return [card["id"] for card in view["cards"] if _covered([card], facts)]


def _assert_filing(summary, view, ids):
    facts = _facts(view)
    cards = view["cards"]
    selected = [card for card in cards if card["id"] in ids]
    quota = view["quota"]
    bound = max(
        len(_covered(combo, facts))
        for combo in combinations(cards, min(quota, len(cards)))
    )
    expected = {
        "target_facts": len(facts),
        "records_needed": len(_needed(view)),
        "capacity": quota,
        "records_filed": len(ids),
        "target_records_filed": len(set(ids) & set(_needed(view))),
        "target_facts_filed": len(_covered(selected, facts)),
        "max_target_facts_at_capacity": bound,
    }
    assert {key: summary[key] for key in expected} == expected


def _senders(session):
    cut = session.snapshot()
    _, senders = summarize_handoffs(cut)
    case = cut["case"]
    identifiers = set(case["exhibits"] + case["crates"] + case["bays"])
    identifiers.update(
        row["id"] for layer in ("briefs", "registrar", "depot") for row in case[layer]
    )
    encoded = json.dumps(senders)
    assert not any(identifier in encoded for identifier in identifiers)
    return senders


def _request(session, slips):
    view = session.view("loader")
    session.apply("loader", {"op": "request", "slips": slips})
    senders = _senders(session)
    nomination = senders["nomination"]
    assert nomination["available_weight"] == sum(b["weight"] for b in view["briefs"])
    assert nomination["requested_weight"] == sum(
        b["weight"] for b in view["briefs"] if b["id"] in slips
    )
    assert nomination["briefs_requested"] == len(slips)
    assert nomination["briefs_available"] == len(view["briefs"])
    assert nomination["capacity"] == view["nominate"]
    assert nomination["realized_brief_requested"] is None
    assert senders["registrar"] is None
    assert senders["depot"] is None


def _file(session, role, ids):
    view = session.view(role)
    session.apply(role, {"op": "file", "ids": ids})
    senders = _senders(session)
    _assert_filing(senders[role], view, ids)
    if role == "registrar":
        assert senders["depot"] is None
        assert senders["nomination"]["realized_brief_requested"] is None
    return senders


def test_missed_nomination_with_complete_local_filings():
    for seed in range(65):
        case = make_case(seed, "fork")
        nominated = [b for b in case["briefs"] if b["id"] != case["realized"]]
        if nominated[0]["epoch"] == nominated[1]["epoch"]:
            break
    else:
        pytest.fail(
            "no generated fork with a realized brief outside the overlapping pair"
        )

    session = Session(case, "linked")
    _request(session, [b["id"] for b in nominated])
    registrar = session.view("registrar")
    assert len(_facts(registrar)) == len(_needed(registrar)) == 3
    registered = _file(session, "registrar", _needed(registrar))
    depot = session.view("depot")
    assert len(_facts(depot)) == len(_needed(depot)) == 3
    senders = _file(session, "depot", _needed(depot))
    assert senders["registrar"] == registered["registrar"]
    assert senders["nomination"]["realized_brief_requested"] is False
    for role in ("registrar", "depot"):
        filing = senders[role]
        assert (
            filing["target_facts_filed"] == filing["max_target_facts_at_capacity"] == 3
        )

    order = session.view("loader")["order"]
    routes = dict(zip(order["items"], case["bays"][:2], strict=True))
    session.apply("loader", {"op": "commit", "routes": routes})
    committed = _senders(session)
    for role in ("nomination", "registrar", "depot"):
        assert committed[role] == senders[role]


def test_disjoint_requests_exceed_capacity_even_with_current_cards():
    for seed in range(65):
        case = make_case(seed, "fork")
        first, second = next(
            (a, b)
            for a, b in combinations(case["briefs"], 2)
            if a["epoch"] != b["epoch"]
        )
        facts = {
            (item, brief["epoch"])
            for brief in (first, second)
            for item in brief["items"]
        }
        stale = next(
            (
                c
                for c in case["registrar"]
                if c["key"] in {key for key, _ in facts} and not _covered([c], facts)
            ),
            None,
        )
        if stale is not None:
            break
    else:
        pytest.fail("no generated fork with a wrong-date requested key")

    for use_stale in (False, True):
        session = Session(case, "linked")
        _request(session, [first["id"], second["id"]])
        needed = _needed(session.view("registrar"))
        assert len(needed) == 4 and stale["id"] not in needed
        ids = needed[:2] + [stale["id"] if use_stale else needed[2]]
        filing = _file(session, "registrar", ids)["registrar"]
        assert filing["capacity"] == filing["max_target_facts_at_capacity"] == 3
        assert filing["target_facts_filed"] == (2 if use_stale else 3)
        depot = session.view("depot")
        assert len(_facts(depot)) == filing["target_facts_filed"]
        senders = _file(session, "depot", _needed(depot))
        assert senders["registrar"] == filing
        assert senders["depot"]["target_facts_filed"] == len(_facts(depot))
        requested = case["realized"] in {first["id"], second["id"]}
        assert senders["nomination"]["realized_brief_requested"] is requested


def test_unobserved_is_not_an_accepted_empty_action():
    session = Session(make_case(0, "fork"), "linked")
    senders = _senders(session)
    assert all(senders[role] is None for role in ("nomination", "registrar", "depot"))
    _request(session, [])
    _file(session, "registrar", [])
    senders = _file(session, "depot", [])
    assert senders["nomination"]["realized_brief_requested"] is False

    session = Session(make_case(0, "fork"), "linked")
    _request(session, [session.view("loader")["briefs"][0]["id"]])
    filing = _file(session, "registrar", [])["registrar"]
    assert filing["target_facts"] == filing["max_target_facts_at_capacity"] == 2
    assert filing["records_filed"] == filing["target_facts_filed"] == 0
    _file(session, "depot", [])
