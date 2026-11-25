from examples.seatlab.physical_dispatch.certificate import Session, grade, make_case
from examples.seatlab.physical_dispatch.stage_labels import summarize_versions

MATCH = "choice_matches_expired_registrar_current_depot"


def _public_cases():
    for seed in range(256):
        case = make_case(seed, "fork")
        session = Session(case, "linked")
        session.apply("loader", {"op": "request", "slips": []})
        registrar = session.view("registrar")["cards"]
        session.apply("registrar", {"op": "file", "ids": []})
        depot = session.view("depot")["cards"]
        session.apply("depot", {"op": "file", "ids": []})
        yield case, session.view("loader")["order"], registrar, depot


def _current(cards, key, epoch):
    return next(
        card
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    )


def _correct_routes(order, registrar, depot):
    epoch = order["epoch"]
    return {
        item: _current(depot, _current(registrar, item, epoch)["value"], epoch)["value"]
        for item in order["items"]
    }


def _submit(case, registrar, depot, routes):
    session = Session(case, "linked")
    for role, action in (
        ("loader", {"op": "request", "slips": []}),
        ("registrar", {"op": "file", "ids": [card["id"] for card in registrar]}),
        ("depot", {"op": "file", "ids": [card["id"] for card in depot]}),
    ):
        assert summarize_versions(session.snapshot())["items"] is None
        session.apply(role, action)
    prefix = session.snapshot()
    labels = summarize_versions(prefix)
    assert labels["committed"] is False
    assert all(row[MATCH] is None for row in labels["items"])
    assert grade(prefix)["correct_items"] is None
    session.apply("loader", {"op": "commit", "routes": routes})
    cut = session.snapshot()
    rows = summarize_versions(cut)["items"]
    result = grade(cut)
    assert rows[1][MATCH] is None
    assert sum(row["physically_correct"] for row in rows) == result["correct_items"]
    return rows[0], (result["reward"], result["correct_items"])


def test_expired_registrar_uses_realized_epoch_and_delivered_records():
    for case, order, registrar, depot in _public_cases():
        if order["epoch"] != 1:
            continue
        item = order["items"][0]
        expired = _current(registrar, item, 0)
        future = _current(registrar, item, 2)
        if expired["end"] != 0 or future["start"] != 2:
            continue
        crate = expired["value"]
        historical = _current(depot, crate, 0)
        current = _current(depot, crate, 1)
        if historical["end"] == 0:
            break
    else:
        raise AssertionError("no distinct historical depot path in bounded draws")

    correct = _correct_routes(order, registrar, depot)
    stale_choice = correct | {item: current["value"]}
    assert stale_choice[item] != correct[item]
    row, physical = _submit(case, [expired], [current], stale_choice)
    assert row[MATCH] is True
    assert row["physically_correct"] is False
    assert physical == (0, 1)

    for mismatch in (correct, correct | {item: historical["value"]}):
        row, _ = _submit(case, [expired], [current], mismatch)
        assert row[MATCH] is False

    future_depot = _current(depot, future["value"], 1)
    live_crate = _current(registrar, item, 1)["value"]
    unrelated = _current(depot, live_crate, 1)
    for reg_file, depot_file in (
        ([], [current]),
        ([expired], []),
        ([expired], [historical]),
        ([expired], [unrelated]),
        ([future], [future_depot]),
    ):
        row, unchanged = _submit(case, reg_file, depot_file, stale_choice)
        assert row[MATCH] is None
        assert unchanged == physical

    row, physical = _submit(case, [expired], [current], correct)
    assert row[MATCH] is False
    assert row["physically_correct"] is True
    assert physical == (1, 2)
    row, unchanged = _submit(case, [], [], correct)
    assert row[MATCH] is None
    assert unchanged == physical


def test_recurrent_registrar_path_can_match_a_correct_dispatch():
    for case, order, registrar, depot in _public_cases():
        if order["epoch"] != 2:
            continue
        item = order["items"][0]
        early, middle, live = (_current(registrar, item, epoch) for epoch in range(3))
        if early["end"] == 0 and early["value"] == live["value"]:
            break
    else:
        raise AssertionError("no recurring registrar path in bounded draws")

    assert middle["end"] == 1 and middle["value"] != early["value"]
    first = _current(depot, early["value"], 2)
    second = _current(depot, middle["value"], 2)
    correct = _correct_routes(order, registrar, depot)
    for choice, expected in ((first, (1, 2)), (second, (0, 1))):
        routes = correct | {item: choice["value"]}
        row, physical = _submit(case, [early, middle], [first, second], routes)
        assert row[MATCH] is True
        assert physical == expected
    row, physical = _submit(case, [early], [first], correct)
    assert row[MATCH] is True
    assert row["physically_correct"] is True
    assert physical == (1, 2)
