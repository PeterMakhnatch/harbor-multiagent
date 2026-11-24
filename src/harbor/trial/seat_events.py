"""An opt-in, in-memory stream of a seat trial's native play lifecycle.

``SeatsTrial.seat_events()`` attaches a ``SeatEventRecorder`` before the trial
runs. The trial calls it synchronously at each native transition; it only puts
a new dict on a bounded queue for the one consumer. Rows carry the trial UUID,
seat names, monotonic nanoseconds since the stream opened and the seat's
native disposition, never prompts, outputs, errors or models.
"""

import asyncio
import re
import time
from collections.abc import Sequence
from typing import Final, Literal, Self, TypedDict
from uuid import UUID

from harbor.models.trial.result import SeatExecutionDisposition

SEAT_EVENTS_SCHEMA: Final = "harbor.seat-events/v1"
MAX_SEATS: Final = 256

_SEAT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", re.ASCII)


class SeatEventSeat(TypedDict):
    name: str
    under_test: bool


class SeatEventsOpen(TypedDict):
    schema: Literal["harbor.seat-events/v1"]
    kind: Literal["open"]
    seq: int
    trial: str
    seats: list[SeatEventSeat]


class SeatRunEnter(TypedDict):
    kind: Literal["run_enter"]
    seq: int
    elapsed_ns: int
    seat: str


class SeatPlayEnd(TypedDict):
    kind: Literal["play_end"]
    seq: int
    elapsed_ns: int
    seat: str
    disposition: SeatExecutionDisposition | None


class SeatEventsClosed(TypedDict):
    kind: Literal["closed"]
    seq: int
    elapsed_ns: int


SeatEventRow = SeatEventsOpen | SeatRunEnter | SeatPlayEnd | SeatEventsClosed


def _check_seats(seats: Sequence[tuple[str, bool]]) -> None:
    if not 1 <= len(seats) <= MAX_SEATS:
        raise ValueError(f"seat events need 1 to {MAX_SEATS} seats, got {len(seats)}")
    for name, under_test in seats:
        if not isinstance(name, str) or _SEAT_NAME.fullmatch(name) is None:
            raise ValueError(f"seat name {name!r} is not a safe ASCII seat name")
        if type(under_test) is not bool:
            raise ValueError(f"seat {name!r} under_test must be a bool")
    names = [name for name, _ in seats]
    if len(set(names)) != len(names):
        raise ValueError("seat events need unique seat names")
    if not any(under_test for _, under_test in seats):
        raise ValueError("seat events need at least one seat under test")


class SeatEventRecorder:
    """The producer side: one per opened stream, called on the trial's loop.
    It observes one play: begin_play refuses a second one before any seat
    starts, so the stream carries at most open, one run_enter and one
    play_end per seat, and closed. Its queue holds that many rows, so a full
    queue raises QueueFull rather than dropping a row."""

    __slots__ = ("_origin_ns", "_played", "_queue", "_seq")

    def __init__(self, trial: UUID, seats: Sequence[tuple[str, bool]]) -> None:
        if not isinstance(trial, UUID):
            raise TypeError("seat events need the trial's UUID")
        _check_seats(seats)
        self._queue: asyncio.Queue[SeatEventRow] = asyncio.Queue(
            maxsize=2 * len(seats) + 2
        )
        self._played = False
        self._seq = 0
        self._origin_ns = time.monotonic_ns()
        self._put(
            SeatEventsOpen(
                schema=SEAT_EVENTS_SCHEMA,
                kind="open",
                seq=0,
                trial=str(trial),
                seats=[
                    SeatEventSeat(name=name, under_test=under_test)
                    for name, under_test in seats
                ],
            )
        )

    def begin_play(self) -> None:
        if self._played:
            raise RuntimeError("seat events observe one play per opened stream")
        self._played = True

    def run_enter(self, seat: str) -> None:
        self._put(
            SeatRunEnter(
                kind="run_enter",
                seq=self._seq,
                elapsed_ns=self._elapsed_ns(),
                seat=seat,
            )
        )

    def play_end(self, seat: str, disposition: SeatExecutionDisposition | None) -> None:
        self._put(
            SeatPlayEnd(
                kind="play_end",
                seq=self._seq,
                elapsed_ns=self._elapsed_ns(),
                seat=seat,
                disposition=disposition,
            )
        )

    def close(self) -> None:
        self._put(
            SeatEventsClosed(
                kind="closed", seq=self._seq, elapsed_ns=self._elapsed_ns()
            )
        )

    async def next_row(self) -> SeatEventRow:
        return await self._queue.get()

    def _elapsed_ns(self) -> int:
        return time.monotonic_ns() - self._origin_ns

    def _put(self, row: SeatEventRow) -> None:
        self._queue.put_nowait(row)
        self._seq += 1


class SeatEventStream:
    """The consumer side: one async iterator for one consumer. It yields the
    open row at once, then each row in order, and ends after the closed row,
    which arrives when the ``seat_events()`` context exits."""

    __slots__ = ("_claimed", "_closed", "_recorder", "_waiting")

    def __init__(self, recorder: SeatEventRecorder) -> None:
        self._recorder = recorder
        self._claimed = False
        self._waiting = False
        self._closed = False

    def __aiter__(self) -> Self:
        if self._claimed:
            raise RuntimeError("seat events allow a single consumer")
        self._claimed = True
        return self

    async def __anext__(self) -> SeatEventRow:
        if self._closed:
            raise StopAsyncIteration
        if self._waiting:
            raise RuntimeError("seat events allow a single waiting consumer")
        self._waiting = True
        try:
            row = await self._recorder.next_row()
        finally:
            self._waiting = False
        if row["kind"] == "closed":
            self._closed = True
        return row
