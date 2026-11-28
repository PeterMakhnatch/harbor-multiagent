from fractions import Fraction
from itertools import product
from typing import Any

import pytest

from examples.seatlab.partner_native.anchors import contrasts

SEEDS = (17, 0, 99, 81, 5, 2_147_483_647)
NAMES = ["zeta", "alpha", "mu"]
Arm = int | None
Panels = dict[tuple[int, int], dict[int, Arm]]


def _panel(*grades: Arm) -> dict[int, Arm]:
    return dict(zip(SEEDS, grades, strict=True))


def _grid(count: int) -> Panels:
    return {(i, j): _panel(*([0] * 6)) for i in range(count) for j in range(count)}


def test_three_team_directional_witness_and_order() -> None:
    wins = ((2, 0, 1), (6, 3, 2), (4, 5, 4))
    panels = {
        (i, j): _panel(*(1 if k < n else 0 for k in range(6)))
        for i, row in enumerate(wins)
        for j, n in enumerate(row)
    }
    report = contrasts(NAMES, panels)
    assert [
        (
            row["anchor"],
            row["role"],
            row["new_partner"],
            row["original_minus_replacement_bounds"],
        )
        for row in report
    ] == [
        ("zeta", "east", "alpha", ["1/3", "1/3"]),
        ("zeta", "east", "mu", ["1/6", "1/6"]),
        ("zeta", "west", "alpha", ["-2/3", "-2/3"]),
        ("zeta", "west", "mu", ["-1/3", "-1/3"]),
        ("alpha", "east", "zeta", ["-1/2", "-1/2"]),
        ("alpha", "east", "mu", ["1/6", "1/6"]),
        ("alpha", "west", "zeta", ["1/2", "1/2"]),
        ("alpha", "west", "mu", ["-1/3", "-1/3"]),
        ("mu", "east", "zeta", ["0", "0"]),
        ("mu", "east", "alpha", ["-1/6", "-1/6"]),
        ("mu", "west", "zeta", ["1/2", "1/2"]),
        ("mu", "west", "alpha", ["1/3", "1/3"]),
    ]
    permuted = {
        pair: dict(reversed(list(panel.items()))) if pair[0] != pair[1] else panel
        for pair, panel in reversed(list(panels.items()))
    }
    assert contrasts(NAMES, permuted) == report


def test_unchanged_marginal_can_hide_replaced_wins() -> None:
    panels = _grid(2)
    panels[0, 0] = _panel(1, 1, 1, 0, 0, 0)
    panels[0, 1] = _panel(0, 1, 0, 1, 0, 1)
    assert contrasts(NAMES[:2], panels)[0] == {
        "anchor": "zeta",
        "role": "east",
        "new_partner": "alpha",
        "registered_cases": 6,
        "complete_pairs": 6,
        "both_win": 1,
        "both_lose": 1,
        "original_only": 2,
        "replacement_only": 2,
        "unknown_pairs": 0,
        "original_only_bounds": [2, 2],
        "replacement_only_bounds": [2, 2],
        "original_minus_replacement_bounds": ["0", "0"],
    }


@pytest.mark.parametrize(
    ("mixed", "home"),
    [
        ((0, 1, None, 1, None, None), (1, 0, 0, None, 0, None)),
        ((0, 1, None, 0, None, None), (1, 0, 1, None, 1, None)),
        ((None, None, None, None, None, None), (1, 0, 1, 0, 1, 0)),
        ((1, 0, 1, 0, 0, 1), (None, None, None, None, None, None)),
    ],
)
def test_bounds_match_all_binary_completions(
    mixed: tuple[Arm, ...], home: tuple[Arm, ...]
) -> None:
    panels = _grid(2)
    panels[0, 0] = _panel(*home)
    panels[0, 1] = _panel(*mixed)
    row = contrasts(NAMES[:2], panels)[0]
    arms = list(zip(mixed, home, strict=True))
    assert row["registered_cases"] == 6
    for key, pair in (
        ("both_win", (1, 1)),
        ("both_lose", (0, 0)),
        ("original_only", (0, 1)),
        ("replacement_only", (1, 0)),
    ):
        assert row[key] == arms.count(pair)
    complete = sum(None not in pair for pair in arms)
    assert row["complete_pairs"] == complete
    assert row["unknown_pairs"] == 6 - complete

    options = [(0, 1) if grade is None else (grade,) for grade in mixed + home]
    assert sum(len(option) == 2 for option in options) <= 6
    outcomes = []
    for completion in product(*options):
        pairs = list(zip(completion[:6], completion[6:], strict=True))
        original = pairs.count((0, 1))
        replacement = pairs.count((1, 0))
        outcomes.append((original, replacement, original - replacement))
    original_counts, replacement_counts, nets = zip(*outcomes, strict=True)
    assert row["original_only_bounds"] == [min(original_counts), max(original_counts)]
    assert row["replacement_only_bounds"] == [
        min(replacement_counts),
        max(replacement_counts),
    ]
    assert row["original_minus_replacement_bounds"] == [
        str(Fraction(min(nets), 6)),
        str(Fraction(max(nets), 6)),
    ]


@pytest.mark.parametrize("names", [[], ["a"], ["a"] * 2, list("abcdef"), ["a", ""]])
def test_reject_invalid_enrollment(names: list[str]) -> None:
    with pytest.raises(ValueError):
        contrasts(names, _grid(len(names)))


@pytest.mark.parametrize("bad_grade", [True, False, -1, 2, 1.0, "1"])
def test_reject_non_binary_grades(bad_grade: Any) -> None:
    panels = _grid(2)
    panels[1, 0][0] = bad_grade
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)


@pytest.mark.parametrize("bad_seed", [False, 0.0, -1, 2_147_483_648, "0", 123])
def test_reject_seed_type_range_and_cohort_drift(bad_seed: Any) -> None:
    panels = _grid(2)
    panels[0, 0][bad_seed] = panels[0, 0].pop(0)
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)


@pytest.mark.parametrize("bad_pair", [(2, 0), (0, 2), (False, 0), (0.0, 0), (0,)])
def test_reject_pair_grid_drift(bad_pair: Any) -> None:
    panels = _grid(2)
    panels[bad_pair] = panels.pop((0, 0))
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)


def test_reject_missing_or_extra_pairs() -> None:
    panels = _grid(2)
    removed = panels.pop((0, 1))
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)
    panels[0, 1] = removed
    panels[0, 2] = removed
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)


def test_reject_missing_or_extra_cases() -> None:
    panels = _grid(2)
    removed = panels[0, 0].pop(0)
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)
    panels[0, 0][0] = removed
    panels[0, 0][123] = 1
    with pytest.raises(ValueError):
        contrasts(NAMES[:2], panels)
