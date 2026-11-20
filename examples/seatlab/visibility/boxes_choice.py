"""Render one cooperative search decision without the source's next move or hidden flags."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from . import boxes_packet

PACKET = (
    Path(__file__).resolve().parents[1] / "results" / "muster-box-receiver-packet.json"
)
PACKET_SHA256 = "dcd8806f26c170ee317b0ad0e0113739c4a1a87465c565487342221a40c84766"
_ARMS = ("own", "shared")


@dataclass(frozen=True, slots=True)
class Cell:
    instance: int
    numbering: int
    repetition: int
    arm: str

    @property
    def id(self) -> str:
        return (
            f"bx-i{self.instance:02d}-m{self.numbering}-r{self.repetition}-{self.arm}"
        )


def grid() -> tuple[Cell, ...]:
    return tuple(
        Cell(instance, numbering, repetition, arm)
        for repetition in range(2)
        for instance in range(1, 11)
        for numbering in range(2)
        for arm in (
            _ARMS if (repetition + instance - 1 + numbering) % 2 == 0 else _ARMS[::-1]
        )
    )


def map_box(box: int, numbering: int) -> int:
    if type(box) is not int or not 1 <= box <= 60:
        raise ValueError("box must be an integer from 1 to 60")
    if type(numbering) is not int or numbering not in (0, 1):
        raise ValueError("unregistered numbering")
    return 61 - box if numbering else box


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def prompt(case: boxes_packet.Case, cell: Cell) -> str:
    if case.instance != cell.instance or cell.arm not in _ARMS:
        raise ValueError("wrong receiver view")
    own = []
    for box, room, flag in case.own:
        if (box - 1) // 10 + 1 != room:
            raise ValueError("box outside its source room")
        display = map_box(box, cell.numbering)
        own.append({"box": display, "room": (display - 1) // 10 + 1, "flag": flag})
    peers = (
        "not supplied"
        if cell.arm == "own"
        else _json(sorted(map_box(box, cell.numbering) for box in case.peers))
    )
    return "\n".join(
        (
            "You are one of 3 teammates searching 60 boxes. The legal box numbers are 1 through 60.",
            "Room 1 contains boxes 1-10, room 2 contains 11-20, and so on through room 6.",
            "Exactly 2 rooms contain flags in every box. The other rooms contain no flags.",
            "Each teammate can open 9 boxes. The team score is its number of distinct flag boxes opened, divided by 20.",
            f"Your remaining opens: {case.budget_left}",
            "Your previous results, oldest first: " + _json(own),
            "Box numbers previously opened by teammates: " + peers,
            'Choose 1 box to open next. Return JSON with only the integer field "box".',
        )
    )


def decode(response_text: str, numbering: int) -> int:
    """Validate one legal action, then undo its number map without correcting it."""

    def unique(fields: list[tuple[str, object]]) -> dict[str, object]:
        result = dict(fields)
        if len(result) != len(fields):
            raise ValueError("duplicate response field")
        return result

    if not isinstance(response_text, str) or len(response_text) > 8192:
        raise ValueError("invalid response size")
    doc = json.loads(response_text, object_pairs_hook=unique)
    if not isinstance(doc, dict) or doc.keys() != {"box"}:
        raise ValueError("expected one box")
    return map_box(doc["box"], numbering)


def category(case: boxes_packet.Case, original_box: int) -> str:
    map_box(original_box, 0)
    if any(item[0] == original_box for item in case.own):
        return "own_repeat"
    return "peer_only_repeat" if original_box in case.peers else "globally_new"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    cells = {cell.id: cell for cell in grid()}
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="list the two numberings and paired trials")
    commands.add_parser(
        "prompt", help="write one receiver's exact user text"
    ).add_argument("cell", choices=cells)
    commands.add_parser(
        "classify", help="read a proposed action from stdin; do not open a box"
    ).add_argument("cell", choices=cells)
    args = parser.parse_args(argv)
    try:
        cases = boxes_packet.load(PACKET, PACKET_SHA256)
        if args.command == "plan":
            print(
                _json(
                    {
                        "packet_sha256": PACKET_SHA256,
                        "cells": [
                            {"id": c.id, "eligible": cases[c.instance] is not None}
                            for c in cells.values()
                        ],
                    }
                )
            )
            return 0
        cell = cells[args.cell]
        case = cases[cell.instance]
        if case is None:
            raise ValueError("ineligible source")
        if args.command == "prompt":
            sys.stdout.write(prompt(case, cell))
        else:
            text = sys.stdin.buffer.read(8193)
            if len(text) > 8192:
                raise ValueError("invalid response size")
            original = decode(text.decode("utf-8"), cell.numbering)
            print(_json({"category": category(case, original)}))
    except (ValueError, OSError, RecursionError):
        print("boxes: invalid packet, receiver or action", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
