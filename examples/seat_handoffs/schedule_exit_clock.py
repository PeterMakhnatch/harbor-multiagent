import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

TRIGGERS = frozenset({"observed", "absent", "unknown"})
ALIGNMENTS = frozenset(
    {"unanchored", "fresh_interval", "clock_unknown", "inapplicable"}
)
ABSENT_REASONS = frozenset({"no_clean_seat", "finish_tie", "no_strict_clean_exit"})
UNKNOWN_REASONS = frozenset(
    {"seat_status_unknown", "seat_timing_unknown", "host_clock_inconsistent"}
)
CLOCK_REASONS = frozenset(
    {
        "host_envelope_missing",
        "host_envelope_inconsistent",
        "world_time_invalid",
        "world_clock_inconsistent",
        "world_source_unverified",
    }
)
OBSERVED_REASONS = CLOCK_REASONS | {"unanchored_prefixes", "fresh_interval_bounds"}
EXIT_REASONS = ABSENT_REASONS | UNKNOWN_REASONS | OBSERVED_REASONS
STOCK_PAIR = ("p1", "p2")
TIMEOUT = "AgentTimeoutError"
CLEAN, TIMED_OUT, UNKNOWN = "clean", "timed_out", "unknown"
MAX_WRITES = 1024
MAX_DRIFT_MS = 60_000
MAX_WORLD_MS = 10**12
HOST_QUANTUM_US = 1
WORLD_ERROR_US = 1000
STOCK_WORLD_VERSION = 2
STAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)",
    flags=re.ASCII,
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
ONE_US = timedelta(microseconds=1)
PLAY = (
    (None, "started_at"),
    ("agent_execution", "started_at"),
    ("agent_execution", "finished_at"),
)
TAIL = (("verifier", "started_at"), ("verifier", "finished_at"), (None, "finished_at"))

Interval = tuple[int, int]


@dataclass(frozen=True)
class ExitCut:
    trigger: str
    reason: str
    alignment: str
    first_seat: str | None
    clean_seats: int | None
    lo: int
    hi: int


def _check_drift(drift_ms: object) -> None:
    if drift_ms is None:
        return
    if type(drift_ms) is not int or not 0 <= drift_ms <= MAX_DRIFT_MS:
        raise ValueError("exit drift must be an integer from 0 to 60000 milliseconds")


@dataclass(frozen=True)
class ExitPolicy:
    drift_ms: int | None = None

    def __post_init__(self) -> None:
        _check_drift(self.drift_ms)


@dataclass(frozen=True)
class _Seat:
    name: str
    status: str
    interval: Interval | None


class _Unsafe(Exception):
    pass


def _micros(value: object) -> int:
    if type(value) is not str:
        raise _Unsafe
    match = STAMP.fullmatch(value)
    if match is None:
        raise _Unsafe
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        moment = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=UTC,
        )
    except ValueError as error:
        raise _Unsafe from error
    total = (moment - EPOCH) // ONE_US + int((fraction or "").ljust(6, "0"))
    if total < 0:
        raise _Unsafe
    return total


def _field(record: dict[str, Any], key: str | None, name: str) -> int | None:
    container = record if key is None else record.get(key)
    if container is None:
        return None
    if type(container) is not dict:
        raise _Unsafe
    value = container.get(name)
    return None if value is None else _micros(value)


def _points(
    record: dict[str, Any], specs: tuple[tuple[str | None, str], ...]
) -> tuple[int | None, ...] | None:
    try:
        return tuple(_field(record, key, name) for key, name in specs)
    except _Unsafe:
        return None


def _ordered(points: tuple[int | None, ...]) -> bool:
    present = [point for point in points if point is not None]
    return all(earlier <= later for earlier, later in pairwise(present))


def _status(row: dict[str, Any]) -> str:
    if "exception_info" not in row:
        return UNKNOWN
    info = row["exception_info"]
    if info is None:
        return CLEAN
    if type(info) is dict and info.get("exception_type") == TIMEOUT:
        return TIMED_OUT
    return UNKNOWN


def _interval(row: dict[str, Any]) -> Interval | None:
    found = _points(row, PLAY[1:])
    if found is None:
        return None
    start, finish = found
    if start is None or finish is None or finish < start:
        return None
    return start, finish


def _seats(result: dict[str, Any]) -> tuple[_Seat, _Seat]:
    rows = result.get("seat_results")
    if type(rows) is not list or len(rows) != len(STOCK_PAIR):
        raise ValueError("seat exit needs exactly two stock seat results")
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        if type(row) is not dict or row.get("under_test") is not True:
            raise ValueError("seat exit needs two under-test stock seats")
        seat = row.get("seat")
        if seat not in STOCK_PAIR or seat in found:
            raise ValueError("seat exit needs two distinct stock seats")
        found[seat] = row
    first, second = (
        _Seat(name, _status(found[name]), _interval(found[name])) for name in STOCK_PAIR
    )
    return first, second


def _writes(state: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if state.get("frozen") is not True or state.get("seats") != list(STOCK_PAIR):
        raise ValueError("seat exit needs a frozen stock two-seat world")
    rows = state.get("submissions")
    if type(rows) is not list or len(rows) > MAX_WRITES:
        raise ValueError("seat exit needs a bounded submission list")
    if not all(type(row) is dict for row in rows):
        raise ValueError("seat exit submission is not an object")
    return tuple(rows)


def _clean_count(pair: tuple[_Seat, _Seat]) -> int | None:
    if any(seat.status == UNKNOWN for seat in pair):
        return None
    return sum(seat.status == CLEAN for seat in pair)


def _relation(seat: _Seat, peer: _Seat) -> str:
    if seat.interval is None or peer.interval is None:
        return "undetermined"
    _, finish = seat.interval
    peer_start, peer_finish = peer.interval
    if finish == peer_finish:
        return "tie"
    if peer_start < finish < peer_finish:
        return "inside"
    return "outside"


def _host_safe(result: dict[str, Any], pair: tuple[_Seat, _Seat]) -> bool:
    play = _points(result, PLAY)
    if play is None or not _ordered(play):
        return False
    trial_start, play_start, play_finish = play
    for seat in pair:
        if seat.interval is None:
            continue
        start, finish = seat.interval
        if not _ordered((trial_start, play_start, start, finish, play_finish)):
            return False
    return True


def _trigger(
    result: dict[str, Any], pair: tuple[_Seat, _Seat]
) -> tuple[str, str, _Seat | None]:
    first, second = pair
    candidates = [
        (seat, _relation(seat, peer))
        for seat, peer in ((first, second), (second, first))
        if seat.status != TIMED_OUT
    ]
    if not candidates:
        return "absent", "no_clean_seat", None
    if not _host_safe(result, pair):
        return "unknown", "host_clock_inconsistent", None
    for seat, relation in candidates:
        if relation == "inside" and seat.status == CLEAN:
            return "observed", "unanchored_prefixes", seat
    relations = {relation for _, relation in candidates}
    if "inside" in relations:
        return "unknown", "seat_status_unknown", None
    if "undetermined" in relations:
        return "unknown", "seat_timing_unknown", None
    if "tie" in relations:
        return "absent", "finish_tie", None
    return "absent", "no_strict_clean_exit", None


def _world_us(value: object) -> int:
    if type(value) not in (int, float):
        raise _Unsafe
    try:
        millis = Decimal(repr(value)) * 1000
    except InvalidOperation as error:
        raise _Unsafe from error
    if not millis.is_finite() or millis != millis.to_integral_value():
        raise _Unsafe
    if not 0 <= millis <= MAX_WORLD_MS:
        raise _Unsafe
    return int(millis) * 1000


def _world_times(writes: tuple[dict[str, Any], ...]) -> tuple[int, ...]:
    times = tuple(_world_us(row.get("t")) for row in writes)
    if any(earlier > later for earlier, later in pairwise(times)):
        raise _Unsafe
    return times


def _envelope(result: dict[str, Any]) -> tuple[int, int] | str:
    play, tail = _points(result, PLAY), _points(result, TAIL)
    if play is None or tail is None:
        return "host_envelope_inconsistent"
    points = play + tail
    if any(point is None for point in points):
        return "host_envelope_missing"
    if not _ordered(points):
        return "host_envelope_inconsistent"
    trial_start, verify_finish = play[0], tail[1]
    assert trial_start is not None and verify_finish is not None
    return trial_start, verify_finish


def _fresh(
    result: dict[str, Any], state: dict[str, Any], cut: int, drift_ms: int
) -> tuple[str, int, int]:
    writes = _writes(state)
    count = len(writes)
    version = state.get("world_version")
    if type(version) is not int or version != STOCK_WORLD_VERSION:
        return "world_source_unverified", 0, count
    envelope = _envelope(result)
    if isinstance(envelope, str):
        return envelope, 0, count
    trial_start, verify_finish = envelope
    if not trial_start <= cut <= verify_finish:
        return "host_envelope_inconsistent", 0, count
    try:
        times = _world_times(writes)
    except _Unsafe:
        return "world_time_invalid", 0, count
    if not times:
        return "fresh_interval_bounds", 0, 0
    slack = drift_ms * 1000 + WORLD_ERROR_US
    if trial_start + times[-1] - slack >= verify_finish + HOST_QUANTUM_US:
        return "world_clock_inconsistent", 0, count
    latest = verify_finish + HOST_QUANTUM_US - times[-1] + WORLD_ERROR_US + slack
    lo = sum(latest + witness <= cut for witness in times[1:])
    later = sum(
        trial_start + moment - slack >= cut + HOST_QUANTUM_US for moment in times
    )
    hi = count - later
    assert 0 <= lo <= hi <= count
    return "fresh_interval_bounds", lo, hi


def _made(
    trigger: str,
    reason: str,
    alignment: str,
    first: _Seat | None,
    clean: int | None,
    bounds: tuple[int, int, int],
) -> ExitCut:
    lo, hi, count = bounds
    assert trigger in TRIGGERS and reason in EXIT_REASONS and alignment in ALIGNMENTS
    assert (first is None) == (trigger != "observed")
    assert clean is None or 0 <= clean <= len(STOCK_PAIR)
    assert 0 <= lo <= hi <= count <= MAX_WRITES
    assert trigger != "absent" or (lo == hi == count and reason in ABSENT_REASONS)
    assert trigger != "unknown" or (lo == 0 and hi == count)
    assert alignment != "clock_unknown" or (lo == 0 and hi == count)
    seat = None if first is None else first.name
    assert seat is None or seat in STOCK_PAIR
    return ExitCut(trigger, reason, alignment, seat, clean, lo, hi)


def cut_at_exit(
    result: dict[str, Any],
    state: dict[str, Any],
    seats: tuple[str, ...],
    *,
    drift_ms: int | None,
) -> ExitCut:
    _check_drift(drift_ms)
    if type(seats) is not tuple or seats != STOCK_PAIR:
        raise ValueError("seat exit needs exactly the two stock seats")
    if type(result) is not dict or type(state) is not dict:
        raise ValueError("seat exit records are not objects")
    pair = _seats(result)
    writes = _writes(state)
    count, clean = len(writes), _clean_count(pair)
    trigger, reason, first = _trigger(result, pair)
    if trigger == "absent":
        return _made(
            trigger, reason, "inapplicable", None, clean, (count, count, count)
        )
    if trigger == "unknown":
        return _made(trigger, reason, "inapplicable", None, clean, (0, count, count))
    assert first is not None and first.interval is not None
    if drift_ms is None:
        return _made(trigger, reason, "unanchored", first, clean, (0, count, count))
    reason, lo, hi = _fresh(result, state, first.interval[1], drift_ms)
    alignment = (
        "fresh_interval" if reason == "fresh_interval_bounds" else "clock_unknown"
    )
    return _made(trigger, reason, alignment, first, clean, (lo, hi, count))
