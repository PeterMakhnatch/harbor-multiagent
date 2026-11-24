"""Cross-seat return labels from a stock Flotilla world event log.

Each adjacent pair of frozen writes gets one label. A label names what the
stock world prepared for the later writer between the two writes, never what
the writer read or thought. The event log carries no world identifier, so the
link to the frozen team is consistency of every consumed record with the
frozen prefixes, not a binding. Records are parsed as strict JSON (no
duplicate keys, finite numbers) but their byte layout is not compared with the
stock serializer: whitespace may be repacked, and an exact layout would not
authenticate a record anyway.

Only regular files reached without symbolic links are read, as a bounded
sequence of complete newline-terminated frames; a torn, oversized or missing
suffix leaves later labels unknown, never absent. Any complete record that the
stock world could not have written, or that disagrees with the frozen state,
makes every cross-seat label unknown. A freeze record requires every frozen
write to be accepted already; messages may still follow it.
"""

import errno
import json
import math
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from .core import MAX_LINE_BYTES, MAX_LOG_BYTES, reject_constant, unique_object
from .schedule_native import Team, answer_signature

EVENTS = Path("artifacts", "logs", "world", "events.jsonl")
NEW_RETURN = "new_return"
PREVIOUS_RETURN = "previous_return"
SENT_NOT_RETURNED = "sent_not_returned"
NOT_SENT = "not_sent"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"
LABELS = (
    NEW_RETURN,
    PREVIOUS_RETURN,
    SENT_NOT_RETURNED,
    NOT_SENT,
    UNKNOWN,
    NOT_APPLICABLE,
)
ADMIN = "admin"
EVENT_KEYS = frozenset({"seq", "t", "seat", "kind", "data"})
START_KEYS = {
    1: frozenset({"condition", "seats"}),
    2: frozenset({"condition", "seats", "world_version", "limits"}),
}
LIMIT_KEYS = frozenset({"max_text", "max_messages"})
PROBE_STATUSES = (404, 405)
INVALID_STATUSES = (400, 413)
MESSAGE_LIMIT = "message limit"
UNAVAILABLE = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EMLINK})
NO_FOLLOW = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _integer(value: object) -> bool:
    return type(value) is int


def _text(value: object) -> bool:
    return isinstance(value, str)


def _open_events(path: Path) -> BinaryIO | None:
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as error:
        if error.errno in UNAVAILABLE:
            return None
        raise
    try:
        for name in EVENTS.parts[:-1]:
            child = os.open(name, NO_FOLLOW | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        leaf = os.open(EVENTS.name, NO_FOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError as error:
        if error.errno in UNAVAILABLE:
            return None
        raise
    finally:
        os.close(directory)
    if not stat.S_ISREG(os.fstat(leaf).st_mode):
        os.close(leaf)
        return None
    return os.fdopen(leaf, "rb")


def _frames(handle: BinaryIO) -> Iterator[bytes]:
    consumed = 0
    while consumed < MAX_LOG_BYTES:
        limit = min(MAX_LINE_BYTES + 1, MAX_LOG_BYTES - consumed)
        frame = handle.readline(limit)
        if not frame.endswith(b"\n"):
            return
        consumed += len(frame)
        yield frame[:-1]


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _record(frame: bytes, seq: int) -> dict[str, Any]:
    try:
        text = frame.decode("utf-8")
        record = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=_finite,
        )
    except (ValueError, RecursionError) as error:
        raise ValueError(f"event {seq} is not a valid JSON record") from error
    if not isinstance(record, dict) or set(record) != EVENT_KEYS:
        raise ValueError(f"event {seq} is not a stock event record")
    if not _integer(record["seq"]) or record["seq"] != seq:
        raise ValueError(f"expected contiguous event seq {seq}")
    clock = record["t"]
    if type(clock) not in (int, float) or not math.isfinite(clock) or clock < 0:
        raise ValueError(f"event {seq} has an invalid world clock")
    if not _text(record["kind"]) or not isinstance(record["data"], dict):
        raise ValueError(f"event {seq} has an invalid kind or data")
    seat = record["seat"]
    if seat is not None and not _text(seat):
        raise ValueError(f"event {seq} has an invalid seat")
    return record


class _Tape:
    def __init__(self, team: Team) -> None:
        self.team = team
        self.actors: tuple[str, ...] = (*team.seats, ADMIN)
        self.messaging = team.condition.startswith("talk")
        self.max_text: int | None = None
        self.max_messages: int | None = None
        self.frozen = False
        self.sent = dict.fromkeys(team.seats, 0)
        self.senders: dict[str, str] = {}
        self.visible: dict[str, list[str]] = {actor: [] for actor in self.actors}
        self.seen = dict.fromkeys(self.actors, 0)
        self.routed: dict[tuple[str, str], int] = {}
        self.fresh: dict[tuple[str, str], int] = {}
        self.submits: list[int] = []
        self.labels: list[str] = []

    def apply(self, record: dict[str, Any]) -> None:
        seq, kind = record["seq"], record["kind"]
        seat, data = record["seat"], record["data"]
        if (seq == 1) != (kind == "start"):
            raise ValueError(f"event {seq} breaks the single leading start")
        if kind in ("start", "health", "bad_token"):
            if seat is not None:
                raise ValueError(f"event {seq} names a seat it cannot carry")
            self._anonymous(kind, data, seq)
        elif seat not in self.actors:
            raise ValueError(f"event {seq} names an unknown caller")
        elif kind in ("freeze", "state"):
            self._admin(kind, seat, data, seq)
        elif kind == "send":
            self._send(seat, data, seq)
        elif kind == "deliver":
            self._deliver(seat, data, seq)
        elif kind == "submit":
            self._submit(seat, data, seq)
        else:
            self._request(kind, seat, data, seq)

    def _anonymous(self, kind: str, data: dict[str, Any], seq: int) -> None:
        if kind == "start":
            self._start(data)
        elif kind == "health":
            if data:
                raise ValueError(f"event {seq} carries unexpected health data")
        elif set(data) != {"path"} or not _text(data["path"]):
            raise ValueError(f"event {seq} is not a stock bad_token record")

    def _start(self, data: dict[str, Any]) -> None:
        version = self.team.version
        if set(data) != START_KEYS[version]:
            raise ValueError("start record does not match the frozen world version")
        if data["condition"] != self.team.condition or data["seats"] != list(
            self.team.seats
        ):
            raise ValueError(
                "start record does not match the frozen condition or seats"
            )
        if version == 1:
            return
        limits = data["limits"]
        if (
            not _integer(data["world_version"])
            or data["world_version"] != 2
            or not isinstance(limits, dict)
            or set(limits) != LIMIT_KEYS
            or not all(_integer(value) for value in limits.values())
        ):
            raise ValueError("start record is not a stock version 2 start")
        self.max_text = limits["max_text"]
        self.max_messages = limits["max_messages"]

    def _admin(self, kind: str, seat: str, data: dict[str, Any], seq: int) -> None:
        if seat != ADMIN or data:
            raise ValueError(f"event {seq} is not a stock admin record")
        if kind != "freeze":
            return
        if len(self.submits) != len(self.team.writes):
            raise ValueError(f"event {seq} freezes before every frozen write")
        self.frozen = True

    def _request(self, kind: str, seat: str, data: dict[str, Any], seq: int) -> None:
        if kind == "whoami":
            valid = not data
        elif kind == "late":
            valid = (
                self.frozen
                and set(data) == {"answer"}
                and isinstance(data["answer"], dict)
            )
        elif kind == "denied":
            valid = self._denied(seat, data)
        elif kind == "probe":
            valid = (
                set(data) == {"method", "path", "status"}
                and _text(data["method"])
                and _text(data["path"])
                and _integer(data["status"])
                and data["status"] in PROBE_STATUSES
            )
        elif kind == "invalid":
            valid = (
                set(data) == {"method", "path", "status", "reason"}
                and _text(data["method"])
                and _text(data["path"])
                and _text(data["reason"])
                and _integer(data["status"])
                and data["status"] in INVALID_STATUSES
            )
        else:
            valid = False
        if not valid:
            raise ValueError(f"event {seq} is not a stock {kind} record")

    def _denied(self, seat: str, data: dict[str, Any]) -> bool:
        if (
            set(data) != {"path", "reason"}
            or data["path"] != "/messages"
            or not _text(data["reason"])
        ):
            return False
        if self.team.version == 1:
            return True
        if not self.messaging:
            return data["reason"] == f"messaging is disabled ({self.team.condition})"
        return (
            data["reason"] == MESSAGE_LIMIT
            and self.max_messages is not None
            and self.sent.get(seat, 0) >= self.max_messages
        )

    def _send(self, seat: str, data: dict[str, Any], seq: int) -> None:
        if not self.messaging or seat not in self.sent:
            raise ValueError(f"event {seq} sends where the stock world refuses")
        if set(data) != {"id", "to", "chars"}:
            raise ValueError(f"event {seq} is not a stock send record")
        to, chars = data["to"], data["chars"]
        if not _text(to) or (to != "all" and to not in self.sent):
            raise ValueError(f"event {seq} sends to an unknown recipient")
        if not _integer(chars) or chars < 1:
            raise ValueError(f"event {seq} records an impossible message length")
        if self.max_text is not None and chars > self.max_text:
            raise ValueError(f"event {seq} exceeds the recorded text limit")
        if self.max_messages is not None and self.sent[seat] >= self.max_messages:
            raise ValueError(f"event {seq} exceeds the recorded message limit")
        index = len(self.senders)
        message = (f"m{index + 1}", seat, to)
        if data["id"] != message[0]:
            raise ValueError(f"event {seq} breaks the stock message numbering")
        if index < len(self.team.messages):
            if self.team.messages[index] != message:
                raise ValueError(f"event {seq} disagrees with the frozen messages")
        elif not self.frozen:
            raise ValueError(f"event {seq} sends beyond the frozen messages")
        self.sent[seat] += 1
        self.senders[message[0]] = seat
        for actor in self.actors:
            if actor != seat and to in ("all", actor):
                self.visible[actor].append(message[0])
        for receiver in self.team.seats:
            if receiver != seat and to in ("all", receiver):
                pair = (seat, receiver)
                self.routed[pair] = self.routed.get(pair, 0) + 1

    def _deliver(self, seat: str, data: dict[str, Any], seq: int) -> None:
        if not self.messaging:
            raise ValueError(f"event {seq} delivers where the stock world refuses")
        visible = self.visible[seat]
        if set(data) != {"ids"} or data["ids"] != visible:
            raise ValueError(f"event {seq} does not return the addressed messages")
        if seat == ADMIN:
            return
        for message in visible[self.seen[seat] :]:
            self.fresh[(seat, self.senders[message])] = seq
        self.seen[seat] = len(visible)

    def _submit(self, seat: str, data: dict[str, Any], seq: int) -> None:
        if self.frozen or seat not in self.sent:
            raise ValueError(f"event {seq} accepts a write the stock world refuses")
        if set(data) != {"n", "answer"} or not isinstance(data["answer"], dict):
            raise ValueError(f"event {seq} is not a stock submit record")
        n = data["n"]
        writes = self.team.writes
        if not _integer(n) or n != len(self.submits) + 1 or n > len(writes):
            raise ValueError(f"event {seq} breaks the frozen write numbering")
        write = writes[n - 1]
        if (
            write.seat != seat
            or answer_signature(data["answer"]) != write.answer_digest
        ):
            raise ValueError(f"event {seq} disagrees with the frozen write")
        if n > 1:
            self.labels.append(self._label(writes[n - 2].seat, seat))
        self.submits.append(seq)

    def _label(self, previous: str, current: str) -> str:
        if previous == current:
            return NOT_APPLICABLE
        latest = self.fresh.get((current, previous))
        if latest is not None and latest > self.submits[-1]:
            return NEW_RETURN
        if latest is not None:
            return PREVIOUS_RETURN
        if self.routed.get((previous, current), 0) > 0:
            return SENT_NOT_RETURNED
        return NOT_SENT


def _baseline(team: Team) -> list[str]:
    writes = team.writes
    return [
        NOT_APPLICABLE if writes[index].seat == writes[index + 1].seat else UNKNOWN
        for index in range(len(writes) - 1)
    ]


def peer_returns(path: Path, team: Team) -> tuple[str, ...]:
    labels = _baseline(team)
    if UNKNOWN not in labels:
        return tuple(labels)
    handle = _open_events(path)
    if handle is None:
        return tuple(labels)
    tape = _Tape(team)
    with handle:
        try:
            for seq, frame in enumerate(_frames(handle), start=1):
                tape.apply(_record(frame, seq))
        except ValueError:
            return tuple(labels)
    labels[: len(tape.labels)] = tape.labels
    assert len(labels) == max(0, len(team.writes) - 1)
    assert all(label in LABELS for label in labels)
    return tuple(labels)
