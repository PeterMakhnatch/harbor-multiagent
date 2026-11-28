from fractions import Fraction
from itertools import product
from typing import Any

import pytest

from examples.seatlab.partner_native.repeat_math import Quartet, summarize

METRICS = (
    "cross_disagreement",
    "within_disagreement",
    "excess_disagreement",
    "replacement_minus_original_reward",
)


def _reference(full: tuple[int, ...]) -> tuple[Fraction, ...]:
    p = Fraction(full[0] + full[1], 2)
    q = Fraction(full[2] + full[3], 2)
    cross = p * (1 - q) + q * (1 - p)
    within = 2 * (p * (1 - p) + q * (1 - q))
    return cross, within, cross - within, q - p


def _possible(case: Quartet) -> list[tuple[Fraction, ...]]:
    return [
        _reference(full)
        for full in product((0, 1), repeat=4)
        if all(
            observed is None or observed == actual
            for observed, actual in zip(case, full, strict=True)
        )
    ]


def test_every_ternary_quartet() -> None:
    domain = (None, 0, 1)
    for case in product(domain, domain, domain, domain):
        report = summarize([case])
        complete = None not in case
        assert report["registered_cases"] == 1
        assert report["complete"] is complete
        assert report["complete_cases"] == int(complete)
        assert report["unknown_cases"] == int(not complete)
        assert report["stable_original_only"] == int(case == (1, 1, 0, 0))
        assert report["stable_replacement_only"] == int(case == (0, 0, 1, 1))
        for metric, possible in zip(
            METRICS, zip(*_possible(case), strict=True), strict=True
        ):
            assert report[metric] == {
                "value": str(possible[0]) if complete else None,
                "bounds": [str(min(possible)), str(max(possible))],
            }


def test_mixed_cohort_matches_joint_completion_enumeration() -> None:
    cases: list[Quartet] = [
        (1, 1, 0, 0),
        (0, 0, 1, 1),
        (0, 1, 0, 1),
        (None, 0, 1, None),
        (1, None, None, 1),
        (1, 1, None, 0),
    ]
    report = summarize(cases)
    assert report["registered_cases"] == 6
    assert report["complete_cases"] == 3
    assert report["unknown_cases"] == 3
    assert report["complete"] is False
    assert report["stable_original_only"] == 1
    assert report["stable_replacement_only"] == 1
    cohort = [
        tuple(sum(column) / len(cases) for column in zip(*completion, strict=True))
        for completion in product(*map(_possible, cases))
    ]
    for metric, possible in zip(METRICS, zip(*cohort, strict=True), strict=True):
        assert report[metric] == {
            "value": None,
            "bounds": [str(min(possible)), str(max(possible))],
        }


def test_negative_excess_and_registered_case_limit() -> None:
    mixed = summarize([(0, 1, 0, 1)] * 1024)
    assert mixed["excess_disagreement"] == {
        "value": "-1/2",
        "bounds": ["-1/2", "-1/2"],
    }
    assert mixed["complete_cases"] == 1024
    unknown = summarize([(None, None, None, None)] * 1024)
    assert unknown["excess_disagreement"] == {
        "value": None,
        "bounds": ["-1/2", "1"],
    }
    assert unknown["unknown_cases"] == 1024


@pytest.mark.parametrize(
    "cases",
    [
        [],
        [(0, 0, 0, 0)] * 1025,
        ((0, 0, 0, 0),),
        [[0, 0, 0, 0]],
        [(0, 0, 0)],
        [(0, 0, 0, 0, 0)],
        [(True, 0, 0, 0)],
        [(0, False, 0, 0)],
        [(0, 0, 1.0, 0)],
        [(0, 0, 0, "1")],
        [(0, 0, 0, 2)],
        [(0, 0, 0, -1)],
    ],
)
def test_reject_invalid_cases(cases: Any) -> None:
    with pytest.raises(ValueError):
        summarize(cases)
