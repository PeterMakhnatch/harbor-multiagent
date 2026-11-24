from typing import Any

import pytest

from examples.seatlab.workbench.cohort import summarize
from examples.seatlab.workbench.input_evidence import Evidence, Final
from examples.seatlab.workbench.input_keys import Cell

LEFT = Cell(key="sha256:" + "a" * 64, view="hello-left")
RIGHT = Cell(key="sha256:" + "b" * 64, view="hello-right")
THIRD = Cell(key="sha256:" + "c" * 64, view="hello-third")
SHARED = Cell(key="sha256:" + "d" * 64, view="hello-left")
PAIR = (LEFT.key, RIGHT.key)


def evidence(
    registered: tuple[Cell, ...],
    finals: tuple[Final, ...],
    closed: bool | None = True,
) -> Evidence:
    return Evidence(
        job="job-1",
        registered=registered,
        closed=closed,
        retries=0,
        lock_digest="sha256:" + "e" * 64,
        finals=finals,
        lock_without_result=0,
    )


def final(
    execution: str, cell: Cell, grade: str = "one", agrees: bool | None = True
) -> Final:
    return Final(execution=execution, cell=cell, agrees=agrees, grade=grade)


def tally(**counts: int) -> dict[str, int]:
    return {
        tag: counts.get(tag, 0)
        for tag in ("one", "zero", "other", "unknown", "invalid")
    }


def group(summary: dict[str, Any], cell: Cell) -> dict[str, Any]:
    [found] = [row for row in summary["groups"] if row["input"] == cell.key]
    return found


def test_closed_matched_pair_is_covered_with_exact_pair() -> None:
    summary = summarize(
        evidence((LEFT, RIGHT), (final("x1", LEFT), final("x2", RIGHT, "zero"))),
        pair=PAIR,
    )
    assert summary["status"] == "covered"
    assert summary["pair"]["status"] == "exact"
    expected = {
        "input": LEFT.key,
        "registered": 1,
        "retained": 1,
        "missing_lock_copies": 0,
        "excess_lock_copies": 0,
        "grades": tally(one=1),
        "unattributed": 0,
        "shared_result_view": False,
    }
    assert {name: group(summary, LEFT)[name] for name in expected} == expected
    assert group(summary, RIGHT)["grades"] == tally(zero=1)
    unrequested = summarize(
        evidence((LEFT, RIGHT), (final("x1", LEFT), final("x2", RIGHT)))
    )
    assert unrequested["pair"] is None


def test_identical_pair_keys_are_an_input_error() -> None:
    with pytest.raises(ValueError):
        summarize(
            evidence((LEFT, RIGHT), (final("x1", LEFT), final("x2", RIGHT))),
            pair=(LEFT.key, LEFT.key),
        )


def test_two_full_keys_sharing_result_view_withhold_grades() -> None:
    summary = summarize(
        evidence((LEFT, SHARED), (final("x1", LEFT), final("x2", SHARED, "zero"))),
        pair=(LEFT.key, SHARED.key),
    )
    assert summary["status"] == "incomplete"
    assert summary["pair"]["status"] == "indistinguishable"
    for cell in (LEFT, SHARED):
        assert group(summary, cell)["shared_result_view"] is True
        assert group(summary, cell)["grades"] is None


def test_swapped_lock_evidence_is_not_rescued_by_result_views() -> None:
    finals = (final("x1", RIGHT, agrees=False), final("x2", LEFT, "zero", agrees=False))
    summary = summarize(evidence((LEFT, RIGHT), finals), pair=PAIR)
    assert summary["status"] == "inconsistent"
    for cell in (LEFT, RIGHT):
        assert group(summary, cell)["grades"] is None
        assert group(summary, cell)["unattributed"] == 1


def test_missing_copy_of_registered_multiplicity_is_incomplete() -> None:
    summary = summarize(
        evidence((LEFT, LEFT, RIGHT), (final("x1", LEFT), final("x2", RIGHT)))
    )
    assert summary["status"] == "incomplete"
    row = group(summary, LEFT)
    assert (row["registered"], row["retained"]) == (2, 1)
    assert (row["missing_lock_copies"], row["excess_lock_copies"]) == (1, 0)


def test_overrepresented_group_gets_no_grades() -> None:
    finals = (final("x1", LEFT), final("x2", LEFT, "zero"), final("x3", RIGHT))
    summary = summarize(evidence((LEFT, RIGHT), finals))
    assert summary["status"] == "inconsistent"
    row = group(summary, LEFT)
    assert (row["registered"], row["retained"], row["excess_lock_copies"]) == (1, 2, 1)
    assert row["grades"] is None
    assert group(summary, RIGHT)["grades"] == tally(one=1)


def test_unknown_grade_and_unknown_lock_relation_are_never_zero() -> None:
    finals = (final("x1", LEFT, "unknown"), final("x2", RIGHT, "zero", agrees=None))
    summary = summarize(evidence((LEFT, RIGHT), finals))
    assert summary["status"] == "incomplete"
    assert group(summary, LEFT)["grades"] == tally(unknown=1)
    assert group(summary, RIGHT)["grades"] is None
    assert group(summary, RIGHT)["unattributed"] == 1


@pytest.mark.parametrize("closed", [False, None])
def test_open_or_unknown_closure_is_incomplete(closed: bool | None) -> None:
    finals = (final("x1", LEFT), final("x2", RIGHT))
    assert (
        summarize(evidence((LEFT, RIGHT), finals, closed=closed))["status"]
        == "incomplete"
    )


def test_named_pair_cannot_select_two_of_three_planned() -> None:
    finals = (final("x1", LEFT), final("x2", RIGHT), final("x3", THIRD))
    summary = summarize(evidence((LEFT, RIGHT, THIRD), finals), pair=PAIR)
    assert summary["pair"]["status"] == "outside_pair"
    assert summary["status"] == "inconsistent"


def test_unregistered_named_key_is_rejected() -> None:
    summary = summarize(evidence((LEFT,), (final("x1", LEFT),)), pair=PAIR)
    assert summary["pair"]["status"] == "not_registered"
    assert summary["status"] == "inconsistent"


def test_identical_full_key_repeat_with_two_executions_is_two_retained() -> None:
    summary = summarize(
        evidence((LEFT, LEFT), (final("x1", LEFT), final("x2", LEFT, "zero")))
    )
    assert summary["status"] == "covered"
    row = group(summary, LEFT)
    assert (row["registered"], row["retained"]) == (2, 2)
    assert (row["missing_lock_copies"], row["excess_lock_copies"]) == (0, 0)
    assert row["grades"] == tally(one=1, zero=1)


def test_enumeration_order_does_not_change_output() -> None:
    registered = (LEFT, RIGHT, LEFT)
    finals = (
        final("x1", LEFT),
        final("x2", RIGHT, "unknown"),
        final("x3", LEFT, "zero"),
    )
    forward = summarize(evidence(registered, finals), pair=PAIR)
    backward = summarize(evidence(registered[::-1], finals[::-1]), pair=PAIR)
    assert forward == backward
