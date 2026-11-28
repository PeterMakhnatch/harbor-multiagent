from fractions import Fraction
from itertools import permutations, product

import pytest

from examples.seatlab.coupled_retention import bounds
from examples.seatlab.coupled_retention.records import Cell

FIRST = ((1, 0), (0, 0))
SECOND = ((0, 0), (0, 1))


def _earned(rows: list[Cell], east: dict[bytes, int], west: dict[bytes, int]) -> int:
    return sum(
        cell.weight * cell.scores[east[cell.east]][west[cell.west]] for cell in rows
    )


def _reference(rows: list[Cell]) -> dict[str, str]:
    east_packets = sorted({cell.east for cell in rows})
    west_packets = sorted({cell.west for cell in rows})
    height, width = len(rows[0].scores), len(rows[0].scores[0])
    total = sum(cell.weight for cell in rows)
    decentralized = max(
        _earned(
            rows,
            dict(zip(east_packets, left, strict=True)),
            dict(zip(west_packets, right, strict=True)),
        )
        for left in product(range(height), repeat=len(east_packets))
        for right in product(range(width), repeat=len(west_packets))
    )
    pooled = sum(
        max(
            sum(
                cell.weight * cell.scores[i][j]
                for cell in rows
                if (cell.east, cell.west) == pair
            )
            for i in range(height)
            for j in range(width)
        )
        for pair in {(cell.east, cell.west) for cell in rows}
    )
    oracle = sum(cell.weight * max(map(max, cell.scores)) for cell in rows)
    return {
        "decentralized": str(Fraction(decentralized, total)),
        "pooled": str(Fraction(pooled, total)),
        "oracle": str(Fraction(oracle, total)),
    }


def _check(
    rows: list[Cell],
) -> tuple[dict[str, str], dict[bytes, int], dict[bytes, int]]:
    values, east, west = bounds.optimize(rows)
    assert set(east) == {cell.east for cell in rows}
    assert set(west) == {cell.west for cell in rows}
    assert all(0 <= action < len(rows[0].scores) for action in east.values())
    assert all(0 <= action < len(rows[0].scores[0]) for action in west.values())
    total = sum(cell.weight for cell in rows)
    assert Fraction(_earned(rows, east, west), total) == Fraction(
        values["decentralized"]
    )
    assert values == _reference(rows) == bounds.evaluate(rows)
    assert list(east) == sorted(east)
    assert list(west) == sorted(west)
    return values, east, west


def test_weighted_conflicting_worlds_and_row_permutations() -> None:
    rows = [
        Cell(2, b"a", b"x", FIRST),
        Cell(3, b"a", b"x", SECOND),
        Cell(4, b"a", b"y", FIRST),
        Cell(5, b"b", b"y", SECOND),
    ]
    expected = _check(rows)
    assert expected[0] == {"decentralized": "4/7", "pooled": "6/7", "oracle": "1"}
    for order in permutations(rows):
        assert _check(list(order)) == expected


def test_nonzero_tied_optimum_uses_canonical_packet_and_action_order() -> None:
    same = ((1, 0), (0, 1))
    opposite = ((0, 1), (1, 0))
    rows = [
        Cell(1, b"b", b"y", same),
        Cell(1, b"a", b"y", opposite),
        Cell(1, b"b", b"x", opposite),
        Cell(1, b"a", b"x", same),
    ]
    expected = (
        {"decentralized": "1", "pooled": "1", "oracle": "1"},
        {b"a": 0, b"b": 1},
        {b"x": 0, b"y": 1},
    )
    for order in permutations(rows):
        assert _check(list(order)) == expected


def test_zero_reward_still_has_complete_canonical_policies() -> None:
    zero = ((0, 0, 0), (0, 0, 0))
    rows = [Cell(2, b"b", b"x", zero), Cell(1, b"a", b"y", zero)]
    expected = _check(rows)
    assert expected == (
        {"decentralized": "0", "pooled": "0", "oracle": "0"},
        {b"a": 0, b"b": 0},
        {b"x": 0, b"y": 0},
    )
    assert _check(rows[::-1]) == expected


def test_rectangular_west_first_with_dominated_and_duplicate_actions() -> None:
    rows = [
        Cell(2, b"b", b"w", ((1, 0), (0, 1), (1, 0))),
        Cell(3, b"a", b"w", ((0, 1), (1, 0), (0, 0))),
        Cell(5, b"c", b"w", ((0, 0), (0, 1), (0, 1))),
        Cell(1, b"c", b"w", ((0, 0), (1, 0), (1, 0))),
    ]
    expected = _check(rows)
    assert expected == (
        {"decentralized": "10/11", "pooled": "10/11", "oracle": "1"},
        {b"a": 0, b"b": 1, b"c": 1},
        {b"w": 1},
    )
    for order in permutations(rows):
        assert _check(list(order)) == expected
    transposed = [
        Cell(cell.weight, cell.west, cell.east, tuple(zip(*cell.scores, strict=True)))
        for cell in rows
    ]
    values, east, west = _check(transposed)
    assert (values, east, west) == (expected[0], expected[2], expected[1])


def test_more_policies_can_mean_less_response_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        Cell(1, b"a", b"w", ((1, 0, 0), (0, 1, 0))),
        Cell(1, b"b", b"w", ((0, 0, 1), (0, 0, 1))),
        Cell(1, b"c", b"w", ((0, 0, 1), (0, 0, 1))),
    ]
    monkeypatch.setattr(bounds, "MAX_SEARCH_SCORE_OPERATIONS", 12)
    assert _check(rows)[0]["decentralized"] == "2/3"
    monkeypatch.setattr(bounds, "MAX_SEARCH_SCORE_OPERATIONS", 11)
    with pytest.raises(ValueError, match="search exceeds its budget"):
        bounds.optimize(rows)
    with pytest.raises(ValueError, match="search exceeds its budget"):
        bounds.evaluate(rows)
