import random
from typing import Any

import pytest

from examples.seatlab.workbench.team_timing_math import (
    DURATIONS,
    MAX_WINDOWS,
    Geometry,
    Window,
    measure,
)

WALL = 1_764_000_000_000_000_000


def expect(
    span: int,
    occupied: int,
    shared: int,
    together: int,
    seat: int,
    alone: int,
    last: str | None,
) -> Geometry:
    return {
        "span_ns": span,
        "occupied_ns": occupied,
        "shared_ns": shared,
        "all_ns": together,
        "seat_ns": seat,
        "last_alone_ns": alone,
        "last_seat": last,
    }


def test_three_seats_tied_at_finish_name_no_last_seat() -> None:
    result = measure([("a", 0, 10), ("b", 2, 10), ("c", 5, 10)])
    assert result == expect(10, 10, 8, 5, 23, 0, None)


def test_tied_latest_end_is_found_in_any_position() -> None:
    late_tie = [("a", 0, 10), ("b", 1, 3), ("c", 2, 4), ("d", 9, 10)]
    assert measure(late_tie)["last_seat"] is None
    assert measure(late_tie[::-1])["last_alone_ns"] == 0
    assert measure([("a", 0, 10), ("b", 10, 10)]) == expect(10, 10, 0, 0, 10, 0, None)
    assert measure([("a", 0, 5), ("b", 0, 5), ("c", 0, 8)])["last_alone_ns"] == 3
    assert measure([("a", 0, 8), ("b", 0, 5), ("c", 0, 5)])["last_seat"] == "a"


def test_idle_gap_between_disjoint_seats_is_not_solo_time() -> None:
    assert measure([("a", 0, 10), ("b", 20, 30)]) == expect(30, 20, 0, 0, 20, 10, "b")


def test_touching_endpoints_are_half_open() -> None:
    assert measure([("a", 0, 10), ("b", 10, 20)]) == expect(20, 20, 0, 0, 20, 10, "b")


def test_zero_duration_seat_occupies_nothing() -> None:
    assert measure([("a", 0, 10), ("b", 5, 5)]) == expect(10, 10, 0, 0, 10, 5, "a")
    assert measure([("a", 0, 10), ("b", 12, 12)]) == expect(12, 10, 0, 0, 10, 0, "b")
    assert measure([("a", 3, 3), ("b", 3, 3)]) == expect(0, 0, 0, 0, 0, 0, None)


def test_same_seat_sum_serial_versus_concurrent() -> None:
    serial = measure([("a", 0, 10), ("b", 10, 20), ("c", 20, 30)])
    concurrent = measure([("a", 0, 10), ("b", 0, 10), ("c", 0, 10)])
    assert serial == expect(30, 30, 0, 0, 30, 10, "c")
    assert concurrent == expect(10, 10, 10, 10, 30, 0, None)
    assert serial["seat_ns"] == concurrent["seat_ns"]


def test_pairwise_overlap_without_common_overlap() -> None:
    assert measure([("a", 0, 10), ("b", 5, 15), ("c", 12, 20)]) == expect(
        20, 20, 8, 0, 28, 5, "c"
    )


def test_short_later_tail() -> None:
    assert measure([("a", 0, 100), ("b", 0, 101)]) == expect(
        101, 101, 100, 100, 201, 1, "b"
    )


def test_contained_seat_does_not_hide_a_longer_tail() -> None:
    assert measure([("outer", 0, 50), ("inner", 10, 20), ("late", 5, 30)]) == expect(
        50, 50, 25, 10, 85, 20, "outer"
    )


def test_order_and_clock_origin_do_not_change_integers() -> None:
    windows = [("a", 0, 10), ("b", 5, 15), ("c", 12, 20)]
    moved = [(name, start + WALL, end + WALL) for name, start, end in windows]
    base = measure(windows)
    assert measure(list(reversed(moved))) == base
    assert measure(tuple(moved)) == base
    assert measure([(n, s - WALL, e - WALL) for n, s, e in windows]) == base


@pytest.mark.parametrize(
    "windows",
    [
        [],
        [("a", 0, 1)],
        [("a", 0, 1), ("a", 1, 2)],
        [("a", 0, 1), ("", 1, 2)],
        [("a", 2, 1), ("b", 0, 1)],
        [("a", 0, 1), ("b", True, 2)],
        [("a", 0, 1.0), ("b", 0, 2)],
        [("a", 0, 1), ["b", 0, 2]],
        [("a", 0, 1), ("b", 0)],
        [("a", 0, 1), (7, 0, 2)],
        [("a", 0, 1), ("b", None, 2)],
    ],
)
def test_malformed_windows_are_rejected(windows: Any) -> None:
    with pytest.raises(ValueError):
        measure(windows)


@pytest.mark.parametrize(
    "windows", ["ab", b"ab", {("a", 0, 1), ("b", 0, 2)}, iter([("a", 0, 1)])]
)
def test_non_sequence_windows_are_type_errors(windows: Any) -> None:
    with pytest.raises(TypeError):
        measure(windows)


def test_selected_window_count_is_bounded() -> None:
    full = [(f"seat{index}", index, index + 1) for index in range(MAX_WINDOWS)]
    assert measure(full) == expect(
        MAX_WINDOWS, MAX_WINDOWS, 0, 0, MAX_WINDOWS, 1, f"seat{MAX_WINDOWS - 1}"
    )
    with pytest.raises(ValueError):
        measure([*full, ("extra", 0, 1)])


def reference(windows: list[Window]) -> Geometry:
    first = min(start for _, start, _ in windows)
    final = max(end for _, _, end in windows)
    counts = [
        sum(start <= moment < end for _, start, end in windows)
        for moment in range(first, final)
    ]
    ends = sorted((end, name) for name, _, end in windows)
    last = ends[-1][1] if ends[-1][0] != ends[-2][0] else None
    alone = 0
    if last is not None:
        name, start, end = next(w for w in windows if w[0] == last)
        others = max(e for n, _, e in windows if n != name)
        alone = sum(moment >= others for moment in range(start, end))
    return expect(
        final - first,
        sum(count >= 1 for count in counts),
        sum(count >= 2 for count in counts),
        sum(count == len(windows) for count in counts),
        sum(counts),
        alone,
        last,
    )


@pytest.mark.parametrize("seed", range(40))
def test_bounded_lattice_matches_unit_step_reference(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(50):
        seats = rng.randint(2, 5)
        windows: list[Window] = []
        for index in range(seats):
            start = rng.randint(0, 12)
            end = rng.randint(start, 12)
            windows.append((f"seat{index}", start, end))
        result = measure(windows)
        assert result == reference(windows), windows
        assert all(type(result[field]) is int for field in DURATIONS)
        assert result["last_alone_ns"] <= result["occupied_ns"] - result["shared_ns"]
