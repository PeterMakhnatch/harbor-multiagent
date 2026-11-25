from typing import Any

from examples.seatlab.exhibit_options import analyze
from tests.unit.examples.test_exhibit_relay_options import (
    Session,
    coupling,
    note,
    prefix,
)


def ranking(score: int, minimum: int, maximum: int, count: int) -> dict[str, int]:
    return {
        "max_score": score,
        "min_true_C_weight": minimum,
        "max_true_C_weight": maximum,
        "tied_file_count": count,
    }


def agenda_keys(session: Any) -> set[tuple[str, int]]:
    return {(row["key"], row["epoch"]) for row in session.view("depot")["agenda"]}


def test_visible_agenda_resolves_note_only_tie() -> None:
    session = prefix(["R-1-0", "R-1-1"], [note(1, 2, 4), note(3, 4, 4)])
    assert agenda_keys(session) == {("C-1", 1), ("C-2", 1)}
    report = analyze(session.snapshot())
    assert report["true_max_C_weight"] == 4
    assert report["proxy"] == ranking(4, 0, 4, 2)
    assert report["agenda_backed_proxy"] == ranking(4, 4, 4, 1)
    assert report["actual"] is None

    session.apply("depot", {"op": "file", "ids": ["D-1-3", "D-1-4"]})
    report = analyze(session.snapshot())
    assert report["proxy"] == ranking(4, 0, 4, 2)
    assert report["agenda_backed_proxy"] == ranking(4, 4, 4, 1)
    actual = report["actual"]
    assert actual["proxy_score"] == 4
    assert actual["attains_proxy_optimum"] is True
    assert actual["C_weight"] == actual["P_item_weight"] == 0
    assert actual["C_weight"] < report["agenda_backed_proxy"]["min_true_C_weight"]


def test_individually_supported_cross_pairs_still_miss_true_briefs() -> None:
    session = prefix(
        [f"R-1-{i}" for i in range(4)],
        [note(1, 4, 9), note(2, 3, 9)],
    )
    assert agenda_keys(session) == {(f"C-{i}", 1) for i in range(1, 5)}
    report = analyze(session.snapshot())
    assert report["true_max_C_weight"] == 4
    assert report["proxy"] == ranking(9, 0, 0, 2)
    assert report["agenda_backed_proxy"] == ranking(9, 0, 0, 2)

    session.apply("depot", {"op": "file", "ids": ["D-1-1", "D-1-4"]})
    report = analyze(session.snapshot())
    assert report["actual"]["C_weight"] == 0
    assert report["actual"]["P_item_weight"] == 10
    assert report["actual"]["attains_proxy_optimum"] is True


def test_agenda_respects_nomination_without_changing_case_wide_truth() -> None:
    session = Session(coupling(), "authored")
    session.apply("loader", {"op": "request", "slips": ["T-0"]})
    session.apply(
        "registrar",
        {
            "op": "file",
            "ids": [f"R-1-{i}" for i in range(4)],
            "notes": [note(1, 2, 4), note(3, 4, 9)],
        },
    )
    assert agenda_keys(session) == {("C-1", 1), ("C-2", 1)}
    report = analyze(session.snapshot())
    assert report["total_weight"] == 10
    assert report["total_item_weight"] == 20
    assert report["true_max_C_weight"] == 4
    assert report["proxy"] == ranking(9, 4, 4, 1)
    assert report["agenda_backed_proxy"] == ranking(4, 4, 4, 1)

    session.apply("depot", {"op": "file", "ids": ["D-1-3", "D-1-4"]})
    report = analyze(session.snapshot())
    assert report["actual"]["C_weight"] == 4
    assert report["actual"]["P_item_weight"] == 10


def test_no_admissible_agenda_backed_ranking() -> None:
    registrar = [f"R-1-{i}" for i in range(4)]
    for mode, notes, expected_proxy in (
        ("authored", [note(1, 2, 9, epoch=0)], ranking(9, 0, 0, 1)),
        ("authored", [note(1, 2, 0)], None),
        ("author-masked", [note(1, 2, 9)], None),
        ("authored", [], None),
    ):
        session = prefix(registrar, notes, mode=mode)
        assert agenda_keys(session) == {(f"C-{i}", 1) for i in range(1, 5)}
        report = analyze(session.snapshot())
        assert report["true_max_C_weight"] == 4
        assert report["proxy"] == expected_proxy
        assert report["agenda_backed_proxy"] is None

        session.apply("depot", {"op": "file", "ids": ["D-1-1", "D-1-2"]})
        report = analyze(session.snapshot())
        assert report["proxy"] == expected_proxy
        assert report["agenda_backed_proxy"] is None
        assert report["actual"]["C_weight"] == 4
        assert report["actual"]["P_item_weight"] == 10
