from dataclasses import replace

import pytest

from examples.seatlab.coupled_retention.audit import score
from examples.seatlab.coupled_retention.records import Cell
from examples.seatlab.coupled_retention.strategy import digest

EAST = ("accept", "reject")
WEST = ("first", "second", "third")
E = b"\x00\xff"
W = b"\x80\x00"
VALID = Cell(3, E, W, ((0, 0, 1), (0, 0, 0)))


def test_weighted_native_payoff_of_nonoptimal_fixed_rules() -> None:
    rows = [
        VALID,
        Cell(7, E, W, ((0, 0, 0), (0, 1, 0))),
        Cell(2, b"", W, ((0, 0, 0), (0, 0, 1))),
    ]
    east = {digest(E): "accept", digest(b""): "reject"}
    west = {digest(W): "third"}
    expected = {"rows": 3, "total_weight": 12, "earned": 5, "reward": "5/12"}
    assert score(rows, EAST, WEST, east, west) == expected
    assert score(rows[::-1], EAST, WEST, east, west) == expected
    alternative = {digest(E): "reject", digest(b""): "reject"}
    assert score(rows, EAST, WEST, alternative, {digest(W): "second"}) == {
        "rows": 3,
        "total_weight": 12,
        "earned": 7,
        "reward": "7/12",
    }
    assert score(rows, EAST, WEST, alternative, west) == {
        "rows": 3,
        "total_weight": 12,
        "earned": 2,
        "reward": "1/6",
    }


def test_support_is_separate_even_for_identical_packet_bytes() -> None:
    rows = [Cell(1, E, E, ((0, 0, 1), (0, 0, 0)))]
    assert score(rows, EAST, WEST, {digest(E): "accept"}, {digest(E): "third"}) == {
        "rows": 1,
        "total_weight": 1,
        "earned": 1,
        "reward": "1",
    }


@pytest.mark.parametrize(
    ("east", "west"),
    [
        ({}, {digest(W): "third"}),
        ({digest(E): "accept"}, {}),
        ({digest(W): "accept"}, {digest(E): "third"}),
        ({digest(E): "accept", digest(b"extra"): "reject"}, {digest(W): "third"}),
        ({digest(E): "accept"}, {digest(W): "third", digest(b"extra"): "first"}),
        ({digest(E): "third"}, {digest(W): "third"}),
        ({digest(E): "accept"}, {digest(W): "accept"}),
        ({digest(E).upper(): "accept"}, {digest(W): "third"}),
    ],
)
def test_invalid_support_or_actions(east: dict[str, str], west: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        score([VALID], EAST, WEST, east, west)


@pytest.mark.parametrize(
    ("east", "west"),
    [
        ((), WEST),
        (EAST, ()),
        (("accept", "accept"), WEST),
        (EAST, ("first", "third", "third")),
        (("accept", ""), WEST),
        (("accept", " reject"), WEST),
        (("accept", "x" * 129), WEST),
        (EAST, ("first", "\x00", "third")),
        (EAST, ("third",)),
        (tuple(str(i) for i in range(17)), WEST),
    ],
)
def test_invalid_labels(east: tuple[str, ...], west: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        score([VALID], east, west, {digest(E): "accept"}, {digest(W): "third"})


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [VALID] * 257,
        [replace(VALID, weight=True)],
        [replace(VALID, weight=0)],
        [replace(VALID, weight=-1)],
        [replace(VALID, weight=1_000_001)],
        [replace(VALID, east=b"x" * 8193)],
        [replace(VALID, west=b"x" * 8193)],
        [replace(VALID, scores=())],
        [replace(VALID, scores=((0, 0, 1),))],
        [replace(VALID, scores=((0, 0, 1), (0, 0)))],
        [replace(VALID, scores=((0, 0, True), (0, 0, 0)))],
        [replace(VALID, scores=((0, 0, 2), (0, 0, 0)))],
        [replace(VALID, scores=((0, 0, -1), (0, 0, 0)))],
        [VALID, replace(VALID, scores=((1,),))],
    ],
)
def test_invalid_cells(rows: list[Cell]) -> None:
    with pytest.raises(ValueError):
        score(rows, EAST, WEST, {digest(E): "accept"}, {digest(W): "third"})


def test_maximum_weight_and_zero_reward() -> None:
    rows = [Cell(1_000_000, E, W, ((0, 0, 0), (0, 0, 0)))] * 256
    assert score(rows, EAST, WEST, {digest(E): "accept"}, {digest(W): "third"}) == {
        "rows": 256,
        "total_weight": 256_000_000,
        "earned": 0,
        "reward": "0",
    }
