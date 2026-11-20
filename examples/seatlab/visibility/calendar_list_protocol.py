"""Plan, render, and score the conditional calendar decision replay offline."""

import argparse
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from . import calendar_packet, calendar_protocol
from .calendar_list_packet import Case
from .calendar_list_packet import load as load_cases

_CONDITIONS = ("rr", "er", "re", "ee")
_ORDERS = (
    ("rr", "er", "ee", "re"),
    ("er", "re", "rr", "ee"),
    ("re", "ee", "er", "rr"),
    ("ee", "rr", "re", "er"),
)


@dataclass(frozen=True, slots=True)
class Cell:
    case: Case
    draw: int
    condition: str

    @property
    def id(self) -> str:
        return f"cl-{self.case.id.removeprefix('rx-')}-k{self.draw}-{self.condition}"


@cache
def _worlds() -> tuple[calendar_packet.World, ...]:
    return calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)


def prompt(case: Case, condition: str) -> str:
    """Render the original decision view with receiver, then sender, substitutions."""
    if condition not in _CONDITIONS:
        raise ValueError("unknown list condition")
    tables = _worlds()[case.world].tables
    # Direction names the sender's table index in the native block.
    receiver_table = tables[1 - case.direction]
    own = (
        calendar_packet.starts(receiver_table) if condition[0] == "e" else case.receiver
    )
    peer = (
        calendar_packet.starts(tables[case.direction])
        if condition[1] == "e"
        else case.sender
    )
    return calendar_protocol.decision_prompt(receiver_table, own, peer)


def cells(cases: Iterable[Case]) -> tuple[Cell, ...]:
    """Register two draws, preserving each case's index when reversing the second."""
    indexed = tuple(enumerate(sorted(cases, key=lambda case: case.id)))
    return tuple(
        Cell(case, draw, condition)
        for draw in range(2)
        for index, case in (reversed(indexed) if draw else indexed)
        for condition in _ORDERS[(index + draw) % len(_ORDERS)]
    )


def _correct(case: Case, slot: int) -> int:
    p1, p2 = _worlds()[case.world].tables
    return int(
        slot in calendar_packet.starts(p1) and slot in calendar_packet.starts(p2)
    )


def grade(case: Case, text: str) -> int:
    """Grade only the selected slot; raise ValueError for invalid decision JSON."""
    return _correct(case, calendar_protocol.decode_slot(text))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="list cell IDs in execution order")
    render = commands.add_parser("prompt", help="render one receiver view")
    render.add_argument("case_id")
    render.add_argument("condition", choices=_CONDITIONS)
    score = commands.add_parser("score", help="report grammar and correctness")
    score.add_argument("case_id")
    score.add_argument("response", metavar="JSON|@FILE")
    args = parser.parse_args(argv)

    try:
        registered = load_cases()
        if args.command == "plan":
            print(json.dumps([cell.id for cell in cells(registered)]))
            return
        case = next((case for case in registered if case.id == args.case_id), None)
        if case is None:
            parser.error("unknown case ID")
        if args.command == "prompt":
            print(prompt(case, args.condition), end="")
            return

        text = args.response
        if text.startswith("@"):
            text = Path(text[1:]).read_text(encoding="utf-8")
        try:
            slot = calendar_protocol.decode_slot(text)
        except ValueError:
            result = {"grammar": False, "correct": None}
        else:
            result = {"grammar": True, "correct": _correct(case, slot)}
        print(json.dumps(result, separators=(",", ":")))
    except (OSError, UnicodeError):
        parser.error("cannot read input")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
