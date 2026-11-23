"""Certified local booker for one four-seat fan-in booking turn. It never calls a model.

Run: python -IS certified_booker.py --arm all|stop --seat p1|p2|p3|p4 < one turn line

Input is one canonical seatlab.seat-turn/v1 line on stdin. Only the default received-order
booking prompt is accepted. The booker uses only its own starts, the delivered reports and
--seat. The expected reporters are the three seats after --seat in cyclic order.

all: all three expected reporters are present, in cyclic order and exact, and their starts
meet own in exactly one start.
stop: the reports are an exact cyclic prefix whose starts meet own in exactly one start,
and no shorter prefix did so already. The empty prefix is accepted only when own holds
one start. A prefix that continued after a certified singleton is refused.

Limited proof: an exact report equals its sender's own calendar check, and own is this
seat's own check. The stock task guarantees one start that fits all six attendees, so that
start is in own and in every exact report. A singleton intersection is therefore that start.
An omitted reporter casts no vote and cannot remove it. The proof holds only if the exact
bits are honest and the stock guarantee holds. It says nothing about any other task.

Success writes one {"text": "{\\"slot\\":N}"} line and exits 0. A refusal writes nothing to
stdout, keeps no file and exits nonzero.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

if __package__:
    from . import join_owner
else:
    _spec = importlib.util.spec_from_file_location(
        "seatlab_fanin_join_owner", Path(__file__).resolve().with_name("join_owner.py")
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("missing sibling join_owner.py")
    join_owner = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(join_owner)

protocol = join_owner.protocol
ARMS: tuple[str, ...] = tuple(protocol.ARMS)
REFUSED = 3
FAILED = 2
assert ARMS == ("all", "stop")


def _prefix_survivor(own: list[int], reports: list[dict[str, Any]]) -> int | None:
    remaining = set(own)
    for report in reports:
        if report["exact"] is not True or report["starts"] is None:
            return None
        if len(remaining) == 1:
            return None
        remaining.intersection_update(report["starts"])
    if len(remaining) != 1:
        return None
    return remaining.pop()


def certify(arm: str, seat: str, prompt: str) -> int | None:
    if arm not in ARMS:
        raise ValueError("unknown arm")
    if arm == "all":
        return join_owner.decision("reduce", seat, prompt)[1]
    expected = join_owner._reporters(seat)
    view = join_owner._view(prompt)
    if view is None:
        return None
    own: list[int] = view["own"]
    reports: list[dict[str, Any]] = view["reports"]
    seats = tuple(report["seat"] for report in reports)
    if seat in seats or seats != expected[: len(seats)]:
        return None
    return _prefix_survivor(own, reports)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--seat", choices=protocol.SEATS, required=True)
    args = parser.parse_args()
    try:
        prompt = join_owner._prompt(sys.stdin.buffer.read(join_owner.MAX_TURN + 1))
        slot = certify(args.arm, args.seat, prompt)
        reply = None if slot is None else join_owner.reply_line(slot)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("certified booker rejected this turn", file=sys.stderr)
        return FAILED
    if reply is None:
        print("certified booker refused: not certified", file=sys.stderr)
        return REFUSED
    sys.stdout.buffer.write(reply)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
