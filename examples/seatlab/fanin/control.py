"""Deterministic fan-in control seat. It reads only the delivered role prompt, never a stock task."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

if __package__:
    from . import protocol
else:
    _spec = importlib.util.spec_from_file_location(
        "seatlab_fanin_protocol", Path(__file__).resolve().with_name("protocol.py")
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("missing sibling protocol.py")
    protocol = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(protocol)

SCHEMA = "seatlab.seat-turn/v1"
MODES = ("correct", "inexact", "wrong")
MAX_TURN = 65536


def line(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        + "\n"
    ).encode()


def _turn(raw: bytes) -> str:
    if not raw.endswith(b"\n") or len(raw) > MAX_TURN or raw.count(b"\n") != 1:
        raise ValueError("invalid turn framing")
    packet = json.loads(raw)
    if (
        type(packet) is not dict
        or set(packet) != {"schema", "prompt"}
        or packet["schema"] != SCHEMA
        or type(packet["prompt"]) is not str
    ):
        raise ValueError("invalid turn")
    return packet["prompt"]


def _view(prompt: str) -> dict[str, Any]:
    head, sep, tail = prompt.rpartition("\n" + protocol.INPUT)
    if not sep or not head or "\n" in tail:
        raise ValueError("missing final input line")
    view = json.loads(tail)
    if type(view) is not dict:
        raise ValueError("input is not an object")
    return view


def _reporter(mode: str, first: bool, prompt: str) -> dict[str, Any]:
    view = _view(prompt)
    if set(view) != {"own"} or protocol.report_prompt(view["own"]) != prompt:
        raise ValueError("not a known report prompt")
    own = list(view["own"])
    return {"starts": own[:-1] if mode == "inexact" and first else own}


def _booker(mode: str, prompt: str) -> dict[str, Any]:
    view = _view(prompt)
    if (
        set(view) != {"own", "reports"}
        or protocol.book_prompt(view["own"], view["reports"]) != prompt
    ):
        raise ValueError("not a known booking prompt")
    remaining = set(view["own"])
    for report in view["reports"]:
        if report["starts"] is not None:
            remaining.intersection_update(report["starts"])
    slot = min(remaining) if remaining else min(view["own"])
    if mode == "wrong":
        slot = min(set(range(protocol.SLOTS)).difference({slot}))
    return {"slot": slot}


def answer(mode: str, world: int, seat: str, prompt: str) -> str:
    roles = protocol.order(world)
    if mode not in MODES or seat not in roles:
        raise ValueError("unknown mode or seat")
    if seat == roles[0]:
        reply = _booker(mode, prompt)
    else:
        reply = _reporter(mode, seat == roles[1], prompt)
    return json.dumps(reply, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--world", type=int, required=True)
    parser.add_argument("--seat", choices=protocol.SEATS, required=True)
    parser.add_argument(
        "--record", type=Path, required=True, help="New directory for this turn"
    )
    args = parser.parse_args()
    try:
        prompt = _turn(sys.stdin.buffer.read(MAX_TURN + 1))
        text = answer(args.mode, args.world, args.seat, prompt)
        args.record.mkdir(mode=0o700, parents=False, exist_ok=False)
        (args.record / "request.json").write_bytes(
            line({"schema": SCHEMA, "prompt": prompt})
        )
        (args.record / "reply.json").write_bytes(line({"text": text}))
        sys.stdout.buffer.write(line({"text": text}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("control seat did not accept this turn", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
