"""Offline plan, prompt, and response codec for the calendar feedback study.

Run ``python -m examples.seatlab.visibility.calendar_protocol`` from the repo root.
For ``prompt`` and ``decode``, supply each JSON argument inline or as ``@PATH``.
For a prompt, pass a local 3×5 array of busy masks with ``--table``.
Or select one table from the pinned packet with ``--world 0 --seat p1``.
"""

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from .calendar_packet import PACKET, PACKET_SHA256, Table, load

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_SEATS = ("p1", "p2")
_WORLD_COUNT = 12
_FIRST_HOUR = 9
_STARTS_PER_DAY = 7
_BUSY_HOURS = 8
_SLOT_COUNT = len(_DAYS) * _STARTS_PER_DAY
_READY = '{"ready":true}'
_LIST_RULE = (
    'Return only a JSON object with a single "starts" key. Use unique integer slot IDs '
    'in ascending order. Return {"starts":[]} if none fit.\n'
)


@dataclass(frozen=True, slots=True)
class Block:
    world: int
    direction: int
    repetition: int

    @property
    def id(self) -> str:
        return f"cf-w{self.world:02d}-d{self.direction}-r{self.repetition}"

    @property
    def sender(self) -> str:
        return _SEATS[self.direction]

    @property
    def receiver(self) -> str:
        return _SEATS[1 - self.direction]

    @property
    def arms(self) -> tuple[str, str]:
        if (self.world + self.direction + self.repetition) % 2 == 0:
            return "blind", "peer"
        return "peer", "blind"


def blocks() -> tuple[Block, ...]:
    return tuple(
        Block(w, d, r) for r in range(2) for w in range(_WORLD_COUNT) for d in range(2)
    )


def _slot_grid() -> str:
    header = "Day  " + "  ".join(
        f"{h:02d}:00" for h in range(_FIRST_HOUR, _FIRST_HOUR + _STARTS_PER_DAY)
    )
    rows = [header]
    for day, name in enumerate(_DAYS):
        first = day * _STARTS_PER_DAY
        rows.append(
            f"{name}  "
            + "  ".join(f"{slot:>5}" for slot in range(first, first + _STARTS_PER_DAY))
        )
    return "\n".join(rows)


_RULES = (
    "Schedule one 2-hour meeting for all attendees at both seats.\n"
    "A meeting needs two consecutive free 1-hour cells on the same weekday. "
    "A start fits your seat only if both cells are free for every attendee at your seat. "
    "A joint start must fit both seats.\n\n"
    f"Slot IDs by weekday and start time:\n{_slot_grid()}\n\n"
    "Your calendar: one anonymous attendee per group. "
    "Times mark the start of busy 1-hour cells. Cells start from 09:00 through 16:00. "
    "Unlisted cells are free; '-' means no busy cells.\n"
)


def _view(table: Table) -> str:
    groups = []
    for row in table:
        days = []
        for day, mask in zip(_DAYS, row, strict=True):
            busy = [
                f"{_FIRST_HOUR + bit:02d}:00"
                for bit in range(_BUSY_HOURS)
                if mask & (1 << bit)
            ]
            days.append(f"{day}: {', '.join(busy) or '-'}")
        groups.append("\n".join(days))
    return _RULES + "\n\n".join(groups) + "\n\nTask:\n"


def initial_prompt(table: Table) -> str:
    return _view(table) + "Find every start that fits your own calendar.\n" + _LIST_RULE


def recheck_prompt(
    table: Table, own: tuple[int, ...], peer: tuple[int, ...] | None
) -> str:
    return (
        _view(table) + f"Your initial candidate list: {_starts_wire(own)}\n"
        f"Receiver message: {feedback_wire(peer)}\n"
        "Recheck your own calendar. The message is either a fallible candidate list "
        "or a ready marker with no list. Use any list only as a hint. "
        "Revise and return every start that fits your seat, not just the overlap.\n"
        + _LIST_RULE
    )


def decision_prompt(table: Table, own: tuple[int, ...], sender: tuple[int, ...]) -> str:
    return (
        _view(table) + f"Your initial candidate list: {_starts_wire(own)}\n"
        f"Sender's revised candidate list: {_starts_wire(sender)}\n"
        "Choose one start that best fits both seats. Both lists can be wrong. "
        "Recheck your own calendar if needed. "
        "Choose one legal integer slot ID even if you are uncertain.\n"
        'Return only a JSON object with a single "slot" key and that integer value.\n'
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(pairs) != len(result):
        raise ValueError("duplicate JSON key")
    return result


def _json(text: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError("expected valid JSON without duplicate keys") from exc


def _checked_slot(value: object) -> int:
    if type(value) is not int or not 0 <= value < _SLOT_COUNT:
        raise ValueError("expected an integer slot ID from 0 to 34")
    return value


def _checked_starts(values: Sequence[object]) -> tuple[int, ...]:
    starts = tuple(_checked_slot(value) for value in values)
    if any(left >= right for left, right in pairwise(starts)):
        raise ValueError("expected unique starts in ascending order")
    return starts


def _decode_starts(value: object) -> tuple[int, ...]:
    if (
        not isinstance(value, dict)
        or value.keys() != {"starts"}
        or not isinstance(value["starts"], list)
    ):
        raise ValueError('expected a JSON object with only a "starts" array')
    return _checked_starts(value["starts"])


def decode_list(text: str) -> tuple[int, ...]:
    return _decode_starts(_json(text))


def decode_slot(text: str) -> int:
    value = _json(text)
    if not isinstance(value, dict) or value.keys() != {"slot"}:
        raise ValueError('expected a JSON object with only a "slot" key')
    return _checked_slot(value["slot"])


def _starts_wire(value: tuple[int, ...]) -> str:
    return json.dumps({"starts": _checked_starts(value)}, separators=(",", ":"))


def feedback_wire(value: tuple[int, ...] | None) -> str:
    return _READY if value is None else _starts_wire(value)


def answer(slot: int) -> dict[str, str]:
    day, offset = divmod(_checked_slot(slot), _STARTS_PER_DAY)
    return {"day": _DAYS[day], "start": f"{_FIRST_HOUR + offset:02d}:00"}


def _input(argument: str) -> str:
    if argument.startswith("@"):
        return Path(argument[1:]).read_text(encoding="utf-8")
    return argument


def _checked_mask(value: object) -> int:
    if type(value) is not int or not 0 <= value < 1 << _BUSY_HOURS:
        raise ValueError("expected integer busy masks from 0 to 255")
    return value


def _input_table(argument: str) -> Table:
    value = _json(_input(argument))
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("expected three attendee rows")
    rows = []
    for row in value:
        if not isinstance(row, list) or len(row) != len(_DAYS):
            raise ValueError("expected five weekday masks per attendee")
        rows.append(tuple(_checked_mask(mask) for mask in row))
    return tuple(rows)


def _input_feedback(argument: str) -> tuple[int, ...] | None:
    value = _json(_input(argument))
    if isinstance(value, dict) and value.keys() == {"ready"} and value["ready"] is True:
        return None
    return _decode_starts(value)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="list the registered blocks in execution order")
    phases = commands.add_parser("prompt").add_subparsers(dest="phase", required=True)
    for phase in ("initial", "recheck", "final"):
        options = phases.add_parser(phase)
        source = options.add_mutually_exclusive_group(required=True)
        source.add_argument("--table", metavar="JSON|@PATH")
        source.add_argument("--world", type=int, choices=range(_WORLD_COUNT))
        options.add_argument("--seat", choices=_SEATS, help="required with --world")
        if phase != "initial":
            options.add_argument("--own", required=True, metavar="JSON|@PATH")
        if phase == "recheck":
            options.add_argument("--peer", required=True, metavar="JSON|@PATH")
        if phase == "final":
            options.add_argument("--sender", required=True, metavar="JSON|@PATH")
    decode = commands.add_parser(
        "decode", help="decode a list or map a slot to native day and start"
    )
    decode.add_argument("kind", choices=("list", "slot"))
    decode.add_argument("response", metavar="JSON|@PATH")
    args = parser.parse_args(argv)

    try:
        if args.command == "plan":
            plan = [
                {
                    "id": block.id,
                    "sender": block.sender,
                    "receiver": block.receiver,
                    "arms": block.arms,
                }
                for block in blocks()
            ]
            print(json.dumps(plan, indent=2))
        elif args.command == "decode":
            response = _input(args.response)
            if args.kind == "list":
                print(_starts_wire(decode_list(response)))
            else:
                print(json.dumps(answer(decode_slot(response)), separators=(",", ":")))
        else:
            if args.world is None:
                if args.seat is not None:
                    raise ValueError("--seat requires --world")
                table = _input_table(args.table)
            else:
                if args.seat is None:
                    raise ValueError("--world requires --seat")
                world = load(PACKET, PACKET_SHA256)[args.world]
                table = world.tables[_SEATS.index(args.seat)]
            if args.phase == "initial":
                prompt = initial_prompt(table)
            else:
                own = decode_list(_input(args.own))
                if args.phase == "recheck":
                    prompt = recheck_prompt(table, own, _input_feedback(args.peer))
                else:
                    prompt = decision_prompt(
                        table, own, decode_list(_input(args.sender))
                    )
            print(prompt, end="")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
