from collections import Counter

from ..visibility import calendar_packet as packet
from ..visibility import calendar_protocol as calendar
from . import protocol

MAX_WORLDS_PER_COHORT = 48
_DAY_COUNT = len(calendar._DAYS)
_STARTS_PER_DAY = calendar._STARTS_PER_DAY
_SLOT_COUNT = calendar._SLOT_COUNT
_EMPTY_ROW = (0,) * _DAY_COUNT

_Tables = tuple[packet.Table, packet.Table]
_RoleShape = tuple[tuple[int, ...], ...]
_WorldShapes = frozenset[_RoleShape]


def _check_cohort(cohort: object) -> None:
    if type(cohort) is not dict or not 1 <= len(cohort) <= MAX_WORLDS_PER_COHORT:
        raise ValueError("invalid calendar cohort size")
    if any(type(world) is not str or not world for world in cohort):
        raise ValueError("invalid calendar world ID")


def _check_table(table: object) -> None:
    if type(table) is not tuple or len(table) != 3:
        raise ValueError("expected three calendar rows per seat")
    for row in table:
        if type(row) is not tuple or len(row) != _DAY_COUNT:
            raise ValueError("expected five weekday masks per attendee")
        for mask in row:
            calendar._checked_mask(mask)


def _role_shape(initial: set[int], revised: set[int], receiver: set[int]) -> _RoleShape:
    days = (
        tuple(
            sorted(
                int(slot in initial)
                | (int(slot in revised) << 1)
                | (int(slot in receiver) << 2)
                for slot in range(first, first + _STARTS_PER_DAY)
            )
        )
        for first in range(0, _SLOT_COUNT, _STARTS_PER_DAY)
    )
    return tuple(sorted(days))


def _starts(table: packet.Table) -> set[int]:
    return set(protocol._starts(packet.starts(table)))


def _world_shapes(tables: _Tables) -> _WorldShapes:
    if type(tables) is not tuple or len(tables) != 2:
        raise ValueError("expected two calendar seats")
    first, second = tables
    _check_table(first)
    _check_table(second)
    first_starts, second_starts = _starts(first), _starts(second)
    if min(len(first_starts), len(second_starts)) < 4:
        raise ValueError("expected at least four local starts per seat")
    if len(first_starts & second_starts) != 1:
        raise ValueError("expected exactly one shared start")
    return frozenset(
        (
            _role_shape(_starts((*first[:2], _EMPTY_ROW)), first_starts, second_starts),
            _role_shape(
                _starts((*second[:2], _EMPTY_ROW)), second_starts, first_starts
            ),
        )
    )


def panel(
    reference: dict[str, _Tables], candidate: dict[str, _Tables]
) -> dict[str, int | bool]:
    if (
        len(protocol.SEATS) != 2
        or _DAY_COUNT != 5
        or _STARTS_PER_DAY != 7
        or _SLOT_COUNT != 35
        or calendar._BUSY_HOURS != 8
    ):
        raise ValueError("unsupported calendar decision geometry")
    _check_cohort(reference)
    _check_cohort(candidate)
    if not reference.keys().isdisjoint(candidate):
        raise ValueError("calendar cohort world IDs overlap")

    reference_shapes = [_world_shapes(tables) for tables in reference.values()]
    candidate_shapes = [_world_shapes(tables) for tables in candidate.values()]
    reference_counts = Counter(shape for world in reference_shapes for shape in world)
    candidate_counts = Counter(shape for world in candidate_shapes for shape in world)
    reference_unique = sum(
        all(reference_counts[shape] == 1 for shape in world)
        for world in reference_shapes
    )
    across_count = within_count = either_count = 0
    for world in candidate_shapes:
        across = any(shape in reference_counts for shape in world)
        within = any(candidate_counts[shape] > 1 for shape in world)
        across_count += across
        within_count += within
        either_count += across or within

    return {
        "reference_worlds": len(reference),
        "candidate_worlds": len(candidate),
        "reference_distinct_role_shapes": len(reference_counts),
        "candidate_distinct_role_shapes": len(candidate_counts),
        "reference_unique_worlds": reference_unique,
        "candidate_unique_worlds": len(candidate) - within_count,
        "candidate_worlds_colliding_reference": across_count,
        "candidate_worlds_colliding_candidate": within_count,
        "candidate_worlds_colliding_either": either_count,
        "pass": either_count == 0,
    }
