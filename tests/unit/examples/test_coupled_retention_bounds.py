from dataclasses import replace
from fractions import Fraction
from itertools import product
from random import Random

import pytest

from examples.seatlab.coupled_retention.bounds import evaluate
from examples.seatlab.coupled_retention.records import Cell

ZERO = ((0, 0), (0, 0))
FIRST = ((1, 0), (0, 0))
SECOND = ((0, 0), (0, 1))


def _naive(cells: list[Cell]) -> dict[str, str]:
    east_packets = sorted({cell.east for cell in cells})
    west_packets = sorted({cell.west for cell in cells})
    east_actions = range(len(cells[0].scores))
    west_actions = range(len(cells[0].scores[0]))
    decentralized = 0
    for east in product(east_actions, repeat=len(east_packets)):
        for west in product(west_actions, repeat=len(west_packets)):
            reward = sum(
                cell.weight
                * cell.scores[east[east_packets.index(cell.east)]][
                    west[west_packets.index(cell.west)]
                ]
                for cell in cells
            )
            decentralized = max(decentralized, reward)
    pooled = sum(
        max(
            sum(
                cell.weight * cell.scores[i][j]
                for cell in cells
                if cell.east == east and cell.west == west
            )
            for i in east_actions
            for j in west_actions
        )
        for east, west in {(cell.east, cell.west) for cell in cells}
    )
    oracle = sum(cell.weight * max(map(max, cell.scores)) for cell in cells)
    total = sum(cell.weight for cell in cells)
    return {
        "decentralized": str(Fraction(decentralized, total)),
        "pooled": str(Fraction(pooled, total)),
        "oracle": str(Fraction(oracle, total)),
    }


def _transpose(cells: list[Cell]) -> list[Cell]:
    return [
        Cell(cell.weight, cell.west, cell.east, tuple(zip(*cell.scores, strict=True)))
        for cell in cells
    ]


def test_exact_against_both_policy_enumeration_for_every_binary_matrix_pair() -> None:
    matrices = [((a, b), (c, d)) for a, b, c, d in product(range(2), repeat=4)]
    for first, second in product(matrices, repeat=2):
        cells = [
            Cell(2, b"e", b"w1", first),
            Cell(3, b"e", b"w2", second),
            Cell(1, b"e", b"w1", SECOND),
        ]
        expected = _naive(cells)
        assert evaluate(cells) == expected
        assert evaluate(_transpose(cells)) == expected
        assert evaluate(cells[::-1]) == expected


def test_rectangular_matrices_and_independent_rewards() -> None:
    rng = Random(12)
    packets = (b"1", b"2", b"3")
    for _ in range(40):
        cells = [
            Cell(
                rng.randint(1, 4),
                rng.choice(packets),
                rng.choice(packets),
                tuple(tuple(rng.randint(0, 1) for _ in range(3)) for _ in range(2)),
            )
            for _ in range(7)
        ]
        expect = _naive(cells)
        assert evaluate(cells) == expect
        assert evaluate(_transpose(cells)) == expect
        shuffled = cells[:]
        rng.shuffle(shuffled)
        assert evaluate(shuffled) == expect


def test_strict_local_and_pooled_gaps_with_hidden_worlds() -> None:
    cells = [
        Cell(2, b"e", b"w1", FIRST),
        Cell(2, b"e", b"w2", SECOND),
        Cell(1, b"e", b"w1", SECOND),
    ]
    assert evaluate(cells) == {
        "decentralized": "3/5",
        "pooled": "4/5",
        "oracle": "1",
    }
    assert evaluate([Cell(1, b"e", b"w", ZERO)]) == {
        "decentralized": "0",
        "pooled": "0",
        "oracle": "0",
    }


VALID = Cell(1, b"e", b"w", FIRST)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [VALID] * 257,
        [replace(VALID, weight=True)],
        [replace(VALID, weight=0)],
        [replace(VALID, weight=-1)],
        [replace(VALID, weight=1.0)],
        [replace(VALID, weight=1_000_001)],
        [replace(VALID, east=b"")],
        [replace(VALID, west=b"x" * 8193)],
        [replace(VALID, east="e")],
        [replace(VALID, scores=())],
        [replace(VALID, scores=((),))],
        [replace(VALID, scores=((0,), (0, 1)))],
        [replace(VALID, scores=((True,),))],
        [replace(VALID, scores=((1.0,),))],
        [replace(VALID, scores=((2,),))],
        [replace(VALID, scores=((-1,),))],
        [replace(VALID, scores=((1,) * 17,))],
        [replace(VALID, scores=((1,),) * 17)],
        [VALID, replace(VALID, scores=((1,),))],
    ],
)
def test_invalid_cells_rejected(rows: list[Cell]) -> None:
    with pytest.raises(ValueError):
        evaluate(rows)


def _identity(size: int) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(int(i == j) for j in range(size)) for i in range(size))


def test_pruning_and_smaller_side_avoid_large_policy_spaces() -> None:
    cells = [Cell(1, bytes([i]), b"one", _identity(6)) for i in range(20)]
    assert evaluate(cells)["decentralized"] == "1"
    assert evaluate(_transpose(cells))["decentralized"] == "1"

    cells = [Cell(1, bytes([i]), bytes([i]), ((0, 0), (0, 1))) for i in range(20)]
    assert evaluate(cells)["decentralized"] == "1"


def test_search_refuses_excess_policies_or_score_operations() -> None:
    policy_expensive = [Cell(1, bytes([i]), bytes([i]), _identity(6)) for i in range(7)]
    with pytest.raises(ValueError, match="search exceeds its budget"):
        evaluate(policy_expensive)

    operations_expensive = [
        Cell(1, bytes([i]), bytes([j]), _identity(4))
        for i in range(8)
        for j in range(8)
    ]
    with pytest.raises(ValueError, match="search exceeds its budget"):
        evaluate(operations_expensive)
