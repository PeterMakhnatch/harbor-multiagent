import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .. import calendar_case
from ..visibility import calendar_packet, calendar_protocol
from . import protocol as p
from . import route_booking, team_views

_SENDER_SCHEMA = "seatlab.calendar-clarification-sender/v1"
_MAX_SENDER_BYTES = 1024
_MAX_TRANSFER_BYTES = 256
_REPORT_RULES, _SLOT_REPLY = team_views._CHOICE_RULES.split("Select one joint start.")


@dataclass(frozen=True, slots=True)
class _Receiver:
    trial_uuid: str
    prompt: str
    own: tuple[int, ...]


def _receiver(data: bytes) -> _Receiver:
    if not isinstance(data, bytes):
        raise TypeError("expected a checked request as bytes")
    request = route_booking._checked_request(data)
    prompt = request["messages"][1]["content"]
    if route_booking._candidates(prompt):
        raise ValueError("expected an empty visible union")
    match = route_booking._CHECKED_PROMPT.fullmatch(prompt)
    if match is None:
        raise ValueError("expected a checked choice prompt")
    own = route_booking._starts(route_booking._prompt_field(match["own"]))
    if len(own) < 2:
        raise ValueError("expected at least two locally feasible starts")
    return _Receiver(request["id"].partition("/")[0], prompt, own)


def _reply_array(raw: str, key: str) -> list[object]:
    if not isinstance(raw, str):
        raise TypeError("expected reply text")
    if (
        len(raw) > _MAX_TRANSFER_BYTES
        or not 0 < len(raw.encode("utf-8")) <= _MAX_TRANSFER_BYTES
    ):
        raise ValueError("expected a bounded reply")
    reply = calendar_packet._object(calendar_packet._json(raw), key)
    return calendar_packet._list(reply[key])


def _proposals(slots: object) -> tuple[int, int]:
    if not isinstance(slots, tuple) or len(slots) != 2:
        raise ValueError("expected two proposed slot IDs")
    left, right = calendar_protocol._checked_starts(slots)
    return left, right


def _nomination_reply(raw: str) -> tuple[int, int]:
    return _proposals(tuple(_reply_array(raw, "proposals")))


def _local_proposals(
    receiver: _Receiver, proposed: tuple[int, int]
) -> tuple[int, int]:
    proposed = _proposals(proposed)
    if any(slot not in receiver.own for slot in proposed):
        raise ValueError("proposed start is not locally feasible")
    return proposed


def _fits(proposed: tuple[int, int], slots: object) -> tuple[int, ...]:
    if not isinstance(slots, tuple) or len(slots) > 2:
        raise ValueError("expected at most two verified slot IDs")
    fits = calendar_protocol._checked_starts(slots)
    if any(slot not in proposed for slot in fits):
        raise ValueError("verified start was not nominated")
    return fits


def _nomination(receiver: _Receiver) -> str:
    prefix, _, tail = receiver.prompt.partition(team_views._CHOICE_RULES)
    visible, _, _ = tail.partition(team_views._CHECK_LIMITS)
    return (
        prefix
        + _REPORT_RULES.rstrip()
        + visible
        + "There is no joint start in the visible intersections. Use the visible "
        "reports to choose exactly 2 distinct starts from your own calendar list "
        'for the other calendar to check. Return only JSON: {"proposals":[n,m]}. '
        "Replace n and m with the 2 slot IDs in ascending order."
    )


def nomination(checked: bytes) -> str:
    return _nomination(_receiver(checked))


def nominees(checked: bytes, raw: str) -> tuple[int, int]:
    return _local_proposals(_receiver(checked), _nomination_reply(raw))


def verification(sender_table: calendar_packet.Table, proposed: tuple[int, int]) -> str:
    proposed = _proposals(proposed)
    if not isinstance(sender_table, (list, tuple)) or len(sender_table) != 3:
        raise ValueError("expected three attendee rows")
    for row in sender_table:
        if not isinstance(row, (list, tuple)) or len(row) != len(calendar_protocol._DAYS):
            raise ValueError("expected five weekday masks per attendee")
        for mask in row:
            calendar_protocol._checked_mask(mask)
    return (
        team_views._view(sender_table, "raw")
        + f"Nominated slot IDs: {p.wire(proposed)}\n"
        + "Check only the 2 nominated starts against all 3 attendees in your own "
        'calendar. Return only JSON: {"fits":[...]}. List each nominated slot ID '
        "that fits once, in ascending order. Use an empty list if neither fits."
    )


def confirmation(proposed: tuple[int, int], raw: str) -> tuple[int, ...]:
    return _fits(_proposals(proposed), tuple(_reply_array(raw, "fits")))


def _booking(
    receiver: _Receiver, proposed: tuple[int, int], confirmed: tuple[int, ...] | None
) -> str:
    proposed = _local_proposals(receiver, proposed)
    if confirmed is None:
        reply = "No verification reply received."
    else:
        fits = _fits(proposed, confirmed)
        reply = f"Verification reply from the other calendar: {p.wire({'fits': fits})}"
    return (
        f"{receiver.prompt}\n\nClarification:\nNominated slot IDs: {p.wire(proposed)}\n"
        f"{reply}\n"
        "A verification reply concerns only the 2 nominated starts. Select one joint "
        "start from your own calendar list. You may choose any start in that list, "
        f"not only a nominated start.{_SLOT_REPLY}"
    )


def booking(
    checked: bytes, proposed: tuple[int, int], confirmed: tuple[int, ...] | None
) -> str:
    return _booking(_receiver(checked), proposed, confirmed)


def _sender(data: bytes) -> tuple[str, calendar_packet.Table]:
    sender = calendar_packet._object(
        calendar_case._json(data), "schema", "trial_uuid", "table"
    )
    if sender["schema"] != _SENDER_SCHEMA:
        raise ValueError("unexpected sender schema")
    trial_uuid = sender["trial_uuid"]
    if (
        not isinstance(trial_uuid, str)
        or len(trial_uuid) != 36
        or str(UUID(trial_uuid)) != trial_uuid
    ):
        raise ValueError("expected a canonical sender trial UUID")
    return trial_uuid, calendar_packet._table(sender["table"])


def _read_reply(path: Path) -> str:
    return calendar_packet._read(path, _MAX_TRANSFER_BYTES).decode("utf-8")


def _request(args: argparse.Namespace) -> calendar_case._Request:
    if args.phase == "propose":
        receiver = _receiver(
            calendar_packet._read(args.checked, calendar_case._MAX_LINE_BYTES)
        )
        return calendar_case._request(
            f"{receiver.trial_uuid}/receiver-nomination", _nomination(receiver)
        )
    proposed = _nomination_reply(_read_reply(args.nomination))
    if args.phase == "ask":
        trial_uuid, table = _sender(calendar_packet._read(args.sender, _MAX_SENDER_BYTES))
        return calendar_case._request(
            f"{trial_uuid}/sender-verification", verification(table, proposed)
        )
    receiver = _receiver(
        calendar_packet._read(args.checked, calendar_case._MAX_LINE_BYTES)
    )
    confirmed = (
        None
        if args.confirmation is None
        else confirmation(proposed, _read_reply(args.confirmation))
    )
    return calendar_case._request(
        f"{receiver.trial_uuid}/receiver-booking", _booking(receiver, proposed, confirmed)
    )


def main(argv: list[str] | None = None) -> int:
    parser = calendar_case._Parser(
        allow_abbrev=False,
        description=(
            "Prepare a calendar clarification request or admit a nomination. "
            "No model calls."
        ),
    )
    phases = parser.add_subparsers(dest="phase", required=True)
    propose = phases.add_parser(
        "propose", allow_abbrev=False, help="Nominate 2 local starts."
    )
    checked_help = "One checked case request."
    propose.add_argument("--checked", type=Path, required=True, help=checked_help)
    admit = phases.add_parser(
        "admit", allow_abbrev=False, help="Validate 2 nominees on the receiver host."
    )
    nomination_help = 'Raw JSON containing only {"proposals":[n,m]}.'
    admit.add_argument("--checked", type=Path, required=True, help=checked_help)
    admit.add_argument("--nomination", type=Path, required=True, help=nomination_help)
    ask = phases.add_parser(
        "ask", allow_abbrev=False, help="Check only the 2 nominations."
    )
    ask.add_argument(
        "--sender", type=Path, required=True, help=f"{_SENDER_SCHEMA} JSON file."
    )
    ask.add_argument("--nomination", type=Path, required=True, help=nomination_help)
    book = phases.add_parser("book", allow_abbrev=False, help="Choose one local start.")
    book.add_argument("--checked", type=Path, required=True, help=checked_help)
    book.add_argument("--nomination", type=Path, required=True, help=nomination_help)
    book.add_argument(
        "--confirmation",
        type=Path,
        help='Raw JSON containing only {"fits":[...]}. Omit if absent.',
    )
    args = parser.parse_args(argv)
    try:
        if args.phase == "admit":
            checked = calendar_packet._read(args.checked, calendar_case._MAX_LINE_BYTES)
            proposed = nominees(checked, _read_reply(args.nomination))
            result: object = {"proposals": proposed}
        else:
            result = _request(args)
        sys.stdout.buffer.write(calendar_case._wire(result))
    except (OSError, RecursionError, TypeError, ValueError):
        print("Cannot prepare calendar clarification request.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
