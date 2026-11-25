from importlib import import_module
from itertools import combinations
from math import comb
from typing import Any

from examples.seatlab.exhibit_options import analyze

Session = import_module(
    "examples.tasks.exhibit-relay.environment.exhibit_relay.engine"
).Session


def history(prefix: str, keys: list[str], values: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}-{epoch}-{index}",
            "key": key,
            "value": values[(index + epoch) % len(values)],
            "start": epoch,
            "end": epoch,
        }
        for epoch in range(3)
        for index, key in enumerate(keys)
    ]


def coupling() -> dict[str, Any]:
    exhibits, crates, bays = (
        [f"{prefix}-{i}" for i in range(8)] for prefix in ("E", "C", "B")
    )
    return {
        "version": 1,
        "profile": "coupling",
        "quota": 2,
        "nominate": 4,
        "exhibits": exhibits,
        "crates": crates,
        "bays": bays,
        "briefs": [
            {
                "id": f"T-{i}",
                "epoch": 1,
                "items": [exhibits[a], exhibits[b]],
                "weight": w,
            }
            for i, (a, b, w) in enumerate(((0, 1, 4), (2, 3, 4), (0, 2, 1), (1, 3, 1)))
        ],
        "realized": "T-0",
        "registrar": history("R", exhibits, crates),
        "depot": history("D", crates, bays),
    }


def note(a: int, b: int, weight: int, *, epoch: int = 1) -> dict[str, Any]:
    return {"epoch": epoch, "keys": [f"C-{a}", f"C-{b}"], "weight": weight}


def prefix(
    registrar: list[str], notes: list[dict[str, Any]], *, mode: str = "authored"
) -> Any:
    case = coupling()
    session = Session(case, mode)
    session.apply(
        "loader", {"op": "request", "slips": [b["id"] for b in case["briefs"]]}
    )
    session.apply("registrar", {"op": "file", "ids": registrar, "notes": notes})
    return session


def actual(session: Any, ids: list[str]) -> dict[str, Any]:
    session.apply("depot", {"op": "file", "ids": ids})
    report = analyze(session.snapshot())
    assert report["actual"] is not None
    return report["actual"]


def assert_proxy(
    report: dict[str, Any], minimum: int, maximum: int, count: int
) -> None:
    proxy = report["proxy"]
    assert proxy is not None
    assert proxy["min_true_C_weight"] == minimum
    assert proxy["max_true_C_weight"] == maximum
    assert proxy["tied_file_count"] == count


def test_source_limited_tie_and_actual_choice() -> None:
    registrar = ["R-1-0", "R-1-1"]
    notes = [note(1, 2, 4), note(3, 4, 4)]
    supported = prefix(registrar, notes)
    report = analyze(supported.snapshot())
    assert report["total_weight"] == 10
    assert report["true_max_C_weight"] == 4
    assert report["legal_file_count"] == sum(comb(24, n) for n in range(3))
    assert report["actual"] is None
    assert_proxy(report, 0, 4, 2)
    good = actual(supported, ["D-1-1", "D-1-2"])
    bad = actual(prefix(registrar, notes), ["D-1-3", "D-1-4"])
    assert good["C_weight"] == 4
    assert good["P_item_weight"] == 10
    assert bad["C_weight"] == 0
    assert bad["P_item_weight"] == 0


def test_true_claims_without_registrar_cards_have_no_attainable_coverage() -> None:
    session = prefix([], [note(1, 2, 4), note(3, 4, 4), note(1, 3, 1), note(2, 4, 1)])
    report = analyze(session.snapshot())
    assert report["true_max_C_weight"] == 0
    assert_proxy(report, 0, 0, 2)
    selected = actual(session, ["D-1-1", "D-1-2"])
    assert selected["C_weight"] == selected["P_item_weight"] == 0


def test_no_delivered_positive_weights_preserves_actual_coverage() -> None:
    registrar = [f"R-1-{i}" for i in range(4)]
    for mode, notes in (
        ("authored", []),
        ("authored", [note(1, 3, 0)]),
        ("author-masked", [note(1, 3, 9)]),
    ):
        session = prefix(registrar, notes, mode=mode)
        report = analyze(session.snapshot())
        assert report["true_max_C_weight"] == 4
        assert report["proxy"] is None
        assert report["actual"] is None
        session.apply("depot", {"op": "file", "ids": ["D-1-1", "D-1-3"]})
        report = analyze(session.snapshot())
        assert report["proxy"] is None
        assert report["actual"]["C_weight"] == 1
        assert report["actual"]["P_item_weight"] == 10


def test_false_positive_pairs_tie_below_attainable_true_weight() -> None:
    session = prefix([f"R-1-{i}" for i in range(4)], [note(1, 4, 9), note(2, 3, 9)])
    report = analyze(session.snapshot())
    assert report["true_max_C_weight"] == 4
    assert_proxy(report, 0, 0, 2)
    selected = actual(session, ["D-1-1", "D-1-4"])
    assert selected["C_weight"] == 0
    assert selected["P_item_weight"] == 10


def brute_scores(
    cut: dict[str, Any], notes: list[dict[str, Any]]
) -> dict[frozenset[str], tuple[int, int, int]]:
    case = cut["case"]
    sources = set(cut["events"][1]["action"]["ids"])
    scores = {}
    for size in range(case["quota"] + 1):
        for cards in combinations(case["depot"], size):
            selected = frozenset(card["id"] for card in cards)
            visible = {
                (card["key"], epoch)
                for card in cards
                for epoch in range(card["start"], card["end"] + 1)
            }
            complete = items = 0
            for brief in case["briefs"]:
                epoch = brief["epoch"]
                hits = sum(
                    any(
                        card["id"] in sources
                        and card["key"] == exhibit
                        and card["start"] <= epoch <= card["end"]
                        and (card["value"], epoch) in visible
                        for card in case["registrar"]
                    )
                    for exhibit in brief["items"]
                )
                complete += brief["weight"] * (hits == 2)
                items += brief["weight"] * hits
            proxy = sum(
                row["weight"]
                for row in notes
                if all((key, row["epoch"]) in visible for key in row["keys"])
            )
            scores[selected] = (complete, items, proxy)
    return scores


def test_multi_era_decoys_against_independent_card_enumeration() -> None:
    notes = [
        note(1, 2, 5),
        note(1, 2, 5, epoch=0),
        note(1, 2, 5, epoch=2),
        note(3, 4, 2),
    ]
    session = prefix(["R-1-0", "R-1-1", "R-0-2", "R-2-3"], notes)
    ids = ["D-0-1", "D-1-2"]
    session.apply("depot", {"op": "file", "ids": ids})
    cut = session.snapshot()
    scores = brute_scores(cut, notes)
    best_proxy = max(score[2] for score in scores.values())
    tied = [score[0] for score in scores.values() if score[2] == best_proxy]
    assert sorted(tied) == [0, 0, 4]
    report = analyze(cut)
    assert report["legal_file_count"] == len(scores)
    assert report["total_weight"] == 10
    assert (
        report["true_max_C_weight"] == max(score[0] for score in scores.values()) == 4
    )
    assert_proxy(report, min(tied), max(tied), len(tied))
    expected = scores[frozenset(ids)]
    assert expected[:2] == (0, 5)
    assert report["actual"]["C_weight"] == expected[0]
    assert report["actual"]["P_item_weight"] == expected[1]
