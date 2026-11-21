from dataclasses import dataclass
from typing import Protocol

from ..live_answers import protocol as _common
from ..visibility import calendar_packet, calendar_protocol

digest = _common.digest
wire = _common.wire

WORLDS = tuple(f"w{i:02d}" for i in range(12))
ARMS = ("count", "named")
SEATS = ("p1", "p2")
PHASES = ("initial", "revision", "pick", "choice")
MODEL = "deepseek/deepseek-flash"
AGENT_NAME = "seatlab-calendar-notice"
AGENT_VERSION = "1.0.0"
AGENT_IMPORT = "examples.seatlab.calendar_notices.seat:NoticeSeat"

_DAY_IDS = (
    "Day IDs: "
    + ", ".join(f"{i} = {name}" for i, name in enumerate(calendar_protocol._DAYS))
    + ".\n"
)
_LIST_RULE = (
    'Return only JSON: {"starts":[...]}. List each valid slot ID once, '
    "in ascending order. Use an empty list if no start fits."
)


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
        return WORLDS.index(self.world)

    @property
    def receiver(self) -> str:
        return SEATS[self.index % 2]

    @property
    def sender(self) -> str:
        return SEATS[1 - self.index % 2]

    @property
    def first(self) -> str:
        return "count" if self.index % 4 in (0, 3) else "named"

    @property
    def run(self) -> str:
        mode = "" if self.control == "live" else f"ctl-{self.control}-"
        return f"cdn-{mode}{self.world}-{self.arm}"

    @property
    def task_name(self) -> str:
        return f"schedule-{self.index:03d}-talk"


class _HasSender(Protocol):
    @property
    def sender(self) -> str: ...


def role(cell: _HasSender, phase: str) -> str:
    if phase == "initial":
        return f"cal_initial_{cell.sender}"
    if phase == "revision":
        return "cal_recheck"
    if phase == "pick":
        return "cal_tool_pick"
    if phase == "choice":
        return "cal_decide"
    raise ValueError("invalid_phase")


def _starts(values: object) -> tuple[int, ...]:
    limit = calendar_protocol._SLOT_COUNT
    if not isinstance(values, (list, tuple)) or len(values) > limit:
        raise ValueError("invalid_starts")
    return calendar_protocol._checked_starts(values)


def _day(slot: int) -> int:
    return slot // calendar_protocol._STARTS_PER_DAY


def _days(values: object) -> list[int]:
    limit = len(calendar_protocol._DAYS)
    if not isinstance(values, (list, tuple)) or len(values) > limit:
        raise ValueError("invalid_days")
    days = []
    for value in values:
        if type(value) is not int or not 0 <= value < limit or value in days:
            raise ValueError("invalid_day")
        days.append(value)
    return days


def _picked_days(values: object) -> list[int]:
    days = _days(values)
    if len(days) != 2:
        raise ValueError("expected_two_distinct_days")
    return days


def decode(text: str, phase: str) -> list[int] | int:
    if not isinstance(text, str):
        raise TypeError("invalid_response")
    value = calendar_packet._json(text)
    if phase in ("initial", "revision"):
        starts = calendar_packet._object(value, "starts")["starts"]
        return list(_starts(calendar_packet._list(starts)))
    if phase == "pick":
        days = calendar_packet._object(value, "days")["days"]
        return _picked_days(calendar_packet._list(days))
    if phase == "choice":
        slot = calendar_packet._object(value, "slot")["slot"]
        return calendar_protocol._checked_slot(slot)
    raise ValueError("invalid_phase")


def changed_days(old: object, new: object) -> list[int]:
    changed = set(_starts(old)).symmetric_difference(_starts(new))
    return sorted({_day(slot) for slot in changed})


def index_wire(old: object, new: object, arm: str) -> str:
    days = changed_days(old, new)
    notice = {"initial": old, "count": len(days)}
    if arm == "named":
        notice["changed"] = days
    elif arm != "count":
        raise ValueError("invalid_arm")
    return wire(notice)


def request_wire(days: object) -> str:
    return wire({"days": _picked_days(days)})


def read_wire(new: object, days: object) -> str:
    requested = _picked_days(days)
    starts = _starts(new)
    return wire(
        {
            "reports": [
                {"day": day, "starts": [slot for slot in starts if _day(slot) == day]}
                for day in requested
            ]
        }
    )


def _view(table: calendar_packet.Table, *, first_two: bool = False) -> str:
    if len(table) != 3:
        raise ValueError("expected_three_attendees")
    return calendar_protocol._view(table[:2] if first_two else table) + _DAY_IDS


def initial_prompt(table: calendar_packet.Table) -> str:
    return (
        _view(table, first_two=True)
        + "Using only the calendars shown, find every start that fits both attendees.\n"
        + _LIST_RULE
    )


def revision_prompt(table: calendar_packet.Table, old: object) -> str:
    return (
        _view(table)
        + f"Your initial report: {wire({'starts': _starts(old)})}\n"
        + "Check the initial report against all 3 calendars shown. Return the full "
        "revised list of starts that fit all 3 attendees, not just the changes.\n"
        + _LIST_RULE
    )


def pick_prompt(table: calendar_packet.Table, index: dict[str, object]) -> str:
    notice = calendar_packet._object(index)
    if notice.keys() not in ({"initial", "count"}, {"initial", "count", "changed"}):
        raise ValueError("invalid_notice")
    _starts(calendar_packet._list(notice["initial"]))
    count = notice["count"]
    if type(count) is not int or not 0 <= count <= len(calendar_protocol._DAYS):
        raise ValueError("invalid_change_count")
    if "changed" in notice:
        days = _days(calendar_packet._list(notice["changed"]))
        if len(days) != count or days != sorted(days):
            raise ValueError("invalid_changed_days")
    return (
        _view(table)
        + f"Notice from the other calendar: {wire(notice)}\n"
        + "The notice contains the full initial list and the number of weekdays "
        "whose list changed. A weekday changed if any start was added or removed. "
        "Use the notice and your calendar to request the 2 weekday reports most "
        'useful for finding a joint start. Return only JSON: {"days":[a,b]}. '
        "Replace a and b with 2 distinct day IDs."
    )


def choice_prompt(
    table: calendar_packet.Table, old: object, opened: dict[str, object]
) -> str:
    reports = calendar_packet._list(
        calendar_packet._object(opened, "reports")["reports"]
    )
    if len(reports) != 2:
        raise ValueError("expected_two_opened_reports")
    items = [calendar_packet._object(report, "day", "starts") for report in reports]
    days = _days([item["day"] for item in items])
    for day, item in zip(days, items, strict=True):
        starts = _starts(calendar_packet._list(item["starts"]))
        if any(_day(slot) != day for slot in starts):
            raise ValueError("opened_report_slot_on_wrong_day")
    return (
        _view(table)
        + f"Initial report from the other calendar: {wire(_starts(old))}\n"
        + f"Opened weekday reports: {wire(opened)}\n"
        + "Each opened report replaces the initial list for that weekday. An empty "
        "report means no start fits that weekday. For every other weekday, the "
        "initial list is your only information about the other calendar. Select "
        'one joint start. Return only JSON: {"slot":n}. Replace n with one slot ID.'
    )
