from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict

Window = tuple[str, int, int]
Duration = Literal[
    "span_ns",
    "occupied_ns",
    "shared_ns",
    "all_ns",
    "seat_ns",
    "last_alone_ns",
]
DURATIONS: tuple[Duration, ...] = (
    "span_ns",
    "occupied_ns",
    "shared_ns",
    "all_ns",
    "seat_ns",
    "last_alone_ns",
)
MAX_WINDOWS = 256


class Geometry(TypedDict):
    span_ns: int
    occupied_ns: int
    shared_ns: int
    all_ns: int
    seat_ns: int
    last_alone_ns: int
    last_seat: str | None


def _checked(windows: Sequence[Window]) -> None:
    if isinstance(windows, (str, bytes)) or not isinstance(windows, Sequence):
        raise TypeError("windows must be a sequence")
    if not 2 <= len(windows) <= MAX_WINDOWS:
        raise ValueError(f"between 2 and {MAX_WINDOWS} windows are required")
    names: set[str] = set()
    for window in windows:
        if not isinstance(window, tuple) or len(window) != 3:
            raise ValueError("each window must be a (name, start, end) tuple")
        name, start, end = window
        if type(name) is not str or not name or name in names:
            raise ValueError("window names must be unique non-empty strings")
        if type(start) is not int or type(end) is not int or start > end:
            raise ValueError("window bounds must be integers with start <= end")
        names.add(name)
    assert len(names) == len(windows)


def _sweep(windows: Sequence[Window]) -> tuple[int, int, int, int]:
    deltas: dict[int, int] = {}
    for _, start, end in windows:
        deltas[start] = deltas.get(start, 0) + 1
        deltas[end] = deltas.get(end, 0) - 1
    seats = len(windows)
    occupied = shared = together = integral = active = 0
    previous: int | None = None
    for moment in sorted(deltas):
        if previous is not None:
            width = moment - previous
            assert width > 0
            occupied += width if active >= 1 else 0
            shared += width if active >= 2 else 0
            together += width if active == seats else 0
            integral += width * active
        active += deltas[moment]
        assert 0 <= active <= seats
        previous = moment
    assert active == 0
    return occupied, shared, together, integral


def _last(windows: Sequence[Window]) -> tuple[str | None, int]:
    first, second = windows[0], windows[1]
    leader, runner = (first, second[2]) if first[2] >= second[2] else (second, first[2])
    for index in range(2, len(windows)):
        window = windows[index]
        if window[2] > leader[2]:
            leader, runner = window, leader[2]
        elif window[2] > runner:
            runner = window[2]
    name, start, end = leader
    assert runner <= end
    if runner == end:
        return None, 0
    alone = end - max(start, runner)
    assert 0 <= alone <= end - start
    return name, alone


def measure(windows: Sequence[Window]) -> Geometry:
    _checked(windows)
    span = max(end for _, _, end in windows) - min(start for _, start, _ in windows)
    seat = sum(end - start for _, start, end in windows)
    occupied, shared, together, integral = _sweep(windows)
    last_seat, last_alone = _last(windows)
    assert 0 <= together <= shared <= occupied <= span
    assert integral == seat
    assert occupied <= seat
    assert 0 <= last_alone <= occupied - shared
    assert last_seat is not None or last_alone == 0
    return {
        "span_ns": span,
        "occupied_ns": occupied,
        "shared_ns": shared,
        "all_ns": together,
        "seat_ns": seat,
        "last_alone_ns": last_alone,
        "last_seat": last_seat,
    }
