"""Held-model booker endpoint. It reads only the delivered booking prompt and never calls a model."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
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
JOIN_SCHEMA = "seatlab.held-join/v1"
MODES = ("abstain", "reduce")
REASONS = ("held", "reduced", "insufficient", "malformed")
MAX_TURN = 65536
_DECLINED = 3
_FAILED = 2
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON constant")


def _reporters(booker: str) -> tuple[str, str, str]:
    if booker not in protocol.SEATS:
        raise ValueError("unknown booker seat")
    roles = protocol.order(protocol.FIRST + protocol.SEATS.index(booker))
    if roles[0] != booker or booker in roles[1:]:
        raise ValueError("booker cycle mismatch")
    return roles[1], roles[2], roles[3]


def _view(prompt: object) -> dict[str, Any] | None:
    if type(prompt) is not str or len(prompt) > MAX_TURN:
        return None
    _, sep, tail = prompt.rpartition("\n" + protocol.INPUT)
    if not sep or "\n" in tail:
        return None
    try:
        view = json.loads(
            tail, object_pairs_hook=protocol._object, parse_constant=_reject_constant
        )
        if type(view) is not dict or set(view) != {"own", "reports"}:
            return None
        if protocol.book_prompt(view["own"], view["reports"]) != prompt:
            return None
    except (ValueError, TypeError, KeyError, RecursionError):
        return None
    return view


def decision(mode: str, booker: str, prompt: str) -> tuple[str, int | None]:
    if mode not in MODES:
        raise ValueError("unknown mode")
    expected = _reporters(booker)
    view = _view(prompt)
    if view is None:
        return "malformed", None
    reports: list[dict[str, Any]] = view["reports"]
    seats = tuple(report["seat"] for report in reports)
    if seats != expected[: len(seats)]:
        return "malformed", None
    if mode == "abstain":
        return "held", None
    if len(reports) != len(expected) or not all(
        report["exact"] is True for report in reports
    ):
        return "insufficient", None
    remaining = set(view["own"])
    for report in reports:
        remaining.intersection_update(report["starts"])
    if len(remaining) != 1:
        return "insufficient", None
    slot = remaining.pop()
    if type(slot) is not int or not 0 <= slot < protocol.SLOTS:
        raise ValueError("reduced slot out of range")
    return "reduced", slot


def envelope(prompt: str) -> bytes:
    if type(prompt) is not str:
        raise ValueError("prompt must be text")
    packet = {"schema": SCHEMA, "prompt": prompt}
    return (json.dumps(packet, ensure_ascii=False) + "\n").encode("utf-8")


def _prompt(raw: bytes) -> str:
    if len(raw) > MAX_TURN or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("invalid turn framing")
    packet = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=protocol._object,
        parse_constant=_reject_constant,
    )
    if type(packet) is not dict or set(packet) != {"schema", "prompt"}:
        raise ValueError("invalid turn fields")
    if packet["schema"] != SCHEMA or type(packet["prompt"]) is not str:
        raise ValueError("invalid turn schema")
    if envelope(packet["prompt"]) != raw:
        raise ValueError("turn is not canonical")
    return packet["prompt"]


def join_line(mode: str, booker: str, prompt: str) -> bytes:
    reason, slot = decision(mode, booker, prompt)
    if reason not in REASONS or (slot is None) != (reason != "reduced"):
        raise ValueError("inconsistent join decision")
    record = {
        "schema": JOIN_SCHEMA,
        "mode": mode,
        "booker": booker,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "reason": reason,
        "slot": slot,
    }
    return (protocol.wire(record) + "\n").encode("utf-8")


def reply_line(slot: int) -> bytes:
    if type(slot) is not int or not 0 <= slot < protocol.SLOTS:
        raise ValueError("slot out of range")
    text = protocol.wire({"slot": slot})
    if protocol.parse_book(text) != slot:
        raise ValueError("reply does not parse as the booking")
    return (protocol.wire({"text": text}) + "\n").encode("utf-8")


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, _FILE_FLAGS, 0o400)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def _record(directory: Path, raw: bytes, join: bytes, reply: bytes | None) -> None:
    if not directory.is_absolute():
        raise ValueError("record directory must be absolute")
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    _write_new(directory / "request.json", raw)
    _write_new(directory / "join.json", join)
    if reply is not None:
        _write_new(directory / "reply.json", reply)
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--booker", choices=protocol.SEATS, required=True)
    parser.add_argument(
        "--record",
        type=Path,
        required=True,
        help="New absolute directory for this turn",
    )
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_TURN + 1)
        prompt = _prompt(raw)
        reason, slot = decision(args.mode, args.booker, prompt)
        join = join_line(args.mode, args.booker, prompt)
        reply = reply_line(slot) if reason == "reduced" and slot is not None else None
        _record(args.record, raw, join, reply)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("join owner rejected this turn", file=sys.stderr)
        return _FAILED
    if reply is None:
        print(f"join owner declined: {reason}", file=sys.stderr)
        return _DECLINED
    sys.stdout.buffer.write(reply)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
