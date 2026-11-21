from dataclasses import dataclass
from typing import override

from ..visibility import calendar_packet, calendar_protocol
from . import protocol as p

WORLDS = tuple(f"w{i:02d}" for i in range(12, 24))
ARMS = ("raw", "exact")
ROLE_ARMS = ("sender-exact", "receiver-exact")
JOIN_WORLDS = tuple(f"w{i:02d}" for i in range(24, 48))
JOIN_ARMS = ("lists", "checked")
SEATS = p.SEATS
PHASES = p.PHASES
MODEL = p.MODEL
AGENT_IMPORT = "examples.seatlab.calendar_notices.seat:PrivateViewSeat"
AGENT_NAME = "seatlab-calendar-private-view"
AGENT_VERSION = "1.0.0"
SCHEMA = "seatlab.native-calendar-private-views/v1"
ROLE_SCHEMA = "seatlab.native-calendar-role-views/v1"
JOIN_SCHEMA = "seatlab.native-calendar-visible-check/v1"
REPEAT_SCHEMA = "seatlab.native-calendar-visible-repeat/v1"

_JOIN_LISTS_FIRST = frozenset(
    world
    for offset in range(2)
    for world in sorted(
        JOIN_WORLDS[offset::2],
        key=lambda world: p.digest(f"seatlab-visible-intersection-v1:{world}"),
    )[:6]
)

_VIEW_PREFIX = calendar_protocol._RULES[
    : calendar_protocol._RULES.index("Your calendar: ")
]


@dataclass(frozen=True, slots=True)
class Cell:
    world: str
    arm: str
    control: str = "live"

    def __post_init__(self) -> None:
        if (
            self.world not in WORLDS
            or self.arm not in ARMS
            or self.control not in ("live", "oracle", "wrong")
        ):
            raise ValueError("invalid_cell")

    @property
    def index(self) -> int:
        return int(self.world[1:])

    @property
    def receiver(self) -> str:
        return SEATS[self.index % 2]

    @property
    def sender(self) -> str:
        return SEATS[1 - self.index % 2]

    @property
    def first(self) -> str:
        return "raw" if self.index % 4 in (0, 3) else "exact"

    @property
    def run(self) -> str:
        mode = "" if self.control == "live" else f"ctl-{self.control}-"
        return f"cpv-{mode}{self.world}-{self.arm}"

    @property
    def task_name(self) -> str:
        return f"schedule-{self.index:03d}-talk"


@dataclass(frozen=True, slots=True)
class RoleCell(Cell):
    @override
    def __post_init__(self) -> None:
        if (
            self.world not in WORLDS
            or self.arm not in ROLE_ARMS
            or self.control not in ("live", "oracle", "wrong")
        ):
            raise ValueError("invalid_cell")

    @property
    @override
    def first(self) -> str:
        return "sender-exact" if self.index % 4 in (0, 3) else "receiver-exact"

    @property
    @override
    def run(self) -> str:
        mode = "" if self.control == "live" else f"ctl-{self.control}-"
        return f"crv-{mode}{self.world}-{self.arm}"


@dataclass(frozen=True, slots=True)
class JoinCell(Cell):
    @override
    def __post_init__(self) -> None:
        if (
            self.world not in JOIN_WORLDS
            or self.arm not in JOIN_ARMS
            or self.control not in ("live", "oracle", "wrong")
        ):
            raise ValueError("invalid_cell")

    @property
    @override
    def first(self) -> str:
        return "lists" if self.world in _JOIN_LISTS_FIRST else "checked"

    @property
    @override
    def run(self) -> str:
        mode = "" if self.control == "live" else f"ctl-{self.control}-"
        return f"cvj-{mode}{self.world}-{self.arm}"


@dataclass(frozen=True, slots=True)
class RepeatJoinCell(JoinCell):
    repetition: int = 1

    @override
    def __post_init__(self) -> None:
        JoinCell.__post_init__(self)
        if (
            not isinstance(self.repetition, int)
            or isinstance(self.repetition, bool)
            or not 1 <= self.repetition <= 32
        ):
            raise ValueError("invalid_repetition")

    @property
    @override
    def first(self) -> str:
        first = super(RepeatJoinCell, self).first
        if self.repetition % 2 == 0:
            return first
        return "checked" if first == "lists" else "lists"

    @property
    @override
    def run(self) -> str:
        mode = "" if self.control == "live" else f"ctl-{self.control}-"
        return f"cvr-r{self.repetition:02d}-{mode}{self.world}-{self.arm}"


def _view_arm(arm: str, exact_arm: str) -> str:
    if arm in ROLE_ARMS:
        return "exact" if arm == exact_arm else "raw"
    if arm in JOIN_ARMS:
        return "exact"
    return arm


def _view(table: calendar_packet.Table, arm: str, *, first_two: bool = False) -> str:
    if len(table) != 3:
        raise ValueError("expected_three_attendees")
    if arm == "raw":
        return p._view(table, first_two=first_two)
    if arm != "exact":
        raise ValueError("invalid_arm")
    visible = (*table[:2], (0,) * len(calendar_protocol._DAYS)) if first_two else table
    starts = p.wire(calendar_packet.starts(visible))
    return (
        _VIEW_PREFIX
        + f"Your calendar: 2-hour start IDs that fit all attendees in this view: {starts}."
        + "\n\nTask:\n"
        + p._DAY_IDS
    )


def initial_prompt(table: calendar_packet.Table, arm: str) -> str:
    return (
        _view(table, _view_arm(arm, "sender-exact"), first_two=True)
        + "Find every start that fits both attendees.\n"
        + p._LIST_RULE
    )


def revision_prompt(table: calendar_packet.Table, old: object, arm: str) -> str:
    return (
        _view(table, _view_arm(arm, "sender-exact"))
        + f"Your initial report: {p.wire({'starts': p._starts(old)})}\n"
        + "Check the initial report against all 3 attendees. Return the full "
        "revised list of starts that fit all 3 attendees, not just the changes.\n"
        + p._LIST_RULE
    )


def pick_prompt(
    table: calendar_packet.Table, index: dict[str, object], arm: str
) -> str:
    notice = calendar_packet._object(index, "initial", "count", "changed")
    p._starts(calendar_packet._list(notice["initial"]))
    count = notice["count"]
    if type(count) is not int or not 0 <= count <= len(calendar_protocol._DAYS):
        raise ValueError("invalid_change_count")
    days = p._days(calendar_packet._list(notice["changed"]))
    if len(days) != count or days != sorted(days):
        raise ValueError("invalid_changed_days")
    return (
        _view(table, _view_arm(arm, "receiver-exact"))
        + f"Notice from the other calendar: {p.wire(notice)}\n"
        + "The notice contains the full initial list, the number of weekdays "
        "whose list changed, and the IDs of those weekdays. A weekday changed if any "
        "start was added or removed. Use the notice and your calendar to request the "
        '2 weekday reports most useful for finding a joint start. Return only JSON: {"days":[a,b]}. '
        "Replace a and b with 2 distinct day IDs."
    )


def _opened_starts(opened: dict[str, object]) -> dict[int, tuple[int, ...]]:
    reports = calendar_packet._list(
        calendar_packet._object(opened, "reports")["reports"]
    )
    if len(reports) != 2:
        raise ValueError("expected_two_opened_reports")
    items = [calendar_packet._object(report, "day", "starts") for report in reports]
    days = p._days([item["day"] for item in items])
    parsed = {}
    for day, item in zip(days, items, strict=True):
        starts = p._starts(calendar_packet._list(item["starts"]))
        if any(p._day(slot) != day for slot in starts):
            raise ValueError("opened_report_slot_on_wrong_day")
        parsed[day] = starts
    return parsed


def _visible_sets(
    table: calendar_packet.Table,
    initial: tuple[int, ...],
    reports: dict[int, tuple[int, ...]],
) -> dict[str, list[int]]:
    if len(table) != 3 or any(
        len(row) != len(calendar_protocol._DAYS) for row in table
    ):
        raise ValueError("invalid_calendar_shape")
    for row in table:
        for mask in row:
            calendar_protocol._checked_mask(mask)
    own = set(calendar_packet.starts(table))
    return {
        "opened": sorted(
            own.intersection(slot for starts in reports.values() for slot in starts)
        ),
        "unopened_initial": [
            slot for slot in initial if p._day(slot) not in reports and slot in own
        ],
    }


def visible_sets(
    table: calendar_packet.Table, old: object, opened: dict[str, object]
) -> dict[str, list[int]]:
    return _visible_sets(table, p._starts(old), _opened_starts(opened))


def choice_prompt(
    table: calendar_packet.Table, old: object, opened: dict[str, object], arm: str
) -> str:
    reports = _opened_starts(opened)
    initial = p._starts(old)
    visible = _visible_sets(table, initial, reports) if arm == "checked" else None
    baseline = (
        _view(table, _view_arm(arm, "receiver-exact"))
        + f"Initial report from the other calendar: {p.wire(initial)}\n"
        + f"Opened weekday reports: {p.wire(opened)}\n"
        + "Each opened report replaces the initial list for that weekday. An empty "
        "report means the sender listed no start for that weekday; it is not proof none exists. "
        "For every other weekday, the initial list is your only information about the "
        'other calendar. Select one joint start. Return only JSON: {"slot":n}. '
        "Replace n with one slot ID."
    )
    if visible is None:
        return baseline
    return (
        baseline
        + f"\nVisible intersections: {p.wire(visible)}\n"
        + "Both sets depend on the peer's reports. A listed start can be invalid "
        "(a false positive), and a valid start can be absent (a false negative). "
        "You may select any slot ID, even if it is not listed in either set."
    )
