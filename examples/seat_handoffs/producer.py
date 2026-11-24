"""Append-only harbor.handoffs/v1 world journal for task authors.

Wiring: create one Journal per world instance with the declared edges, enter it
before serving seats, and call sent/returned/acknowledged/commit while holding
the world's own state lock, before replying or persisting the matching state.
Each call returns True only when it appended and fsynced a first occurrence;
a repeat of the same legal action returns False and writes nothing. If the
world's state write fails after a journal call, call fail() and stop play: the
journal and the state have no shared transaction. freeze() ends play and
returns the cut {"run", "events"}, where events counts every frame including
the start record and equals the seq the verifier gives its closed frame.

The verifier reads the raw journal bytes and passes them to seal() with the
cut and its own reward. seal() returns a closed copy and never writes the
world journal. "returned" means the world prepared the reply, not that it was
delivered. Validation does not authenticate the producer.
"""

import json
import os
import stat
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast

from .core import (
    MAX_EDGES,
    MAX_FRAMES,
    MAX_LINE_BYTES,
    MAX_LOG_BYTES,
    SCHEMA,
    Edge,
    Replay,
    canonical_uuid,
    check_identifier,
    frames_from_bytes,
    parse_header,
    replay_frames,
)

CUT_KEYS = frozenset({"run", "events"})
FILE_MODE = 0o600
OPEN_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | os.O_APPEND
    | os.O_NOFOLLOW
    | getattr(os, "O_CLOEXEC", 0)
)


def encode_frame(record: dict[str, Any]) -> bytes:
    line = json.dumps(record, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(line) > MAX_LINE_BYTES:
        raise ValueError(f"frame exceeds {MAX_LINE_BYTES} bytes")
    return line + b"\n"


def fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def header_record(
    edges: tuple[Edge, ...], *, acknowledgements: bool, run: str
) -> dict[str, Any]:
    if not isinstance(edges, tuple) or not 1 <= len(edges) <= MAX_EDGES:
        raise ValueError(f"edges must be a tuple of 1 to {MAX_EDGES} core.Edge")
    if not all(type(e) is Edge for e in edges):
        raise TypeError("edges must be a tuple of core.Edge")
    if type(acknowledgements) is not bool:
        raise TypeError("acknowledgements must be a bool")
    record: dict[str, Any] = {
        "seq": 0,
        "type": "start",
        "schema": SCHEMA,
        "run": run,
        "edges": [
            {"message": e.message, "sender": e.sender, "receiver": e.receiver}
            for e in edges
        ],
    }
    if acknowledgements:
        record["acknowledgements"] = True
    return record


def edge_key(message: str, receiver: str) -> tuple[str, str]:
    return (
        check_identifier(message, "message"),
        check_identifier(receiver, "receiver"),
    )


class Journal:
    def __init__(
        self,
        path: Path,
        edges: tuple[Edge, ...],
        *,
        acknowledgements: bool,
        run: str | None = None,
    ) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        chosen = str(uuid.uuid4()) if run is None else run
        if not canonical_uuid(chosen):
            raise ValueError("run must be a canonical UUID")
        self._header = header_record(
            edges, acknowledgements=acknowledgements, run=chosen
        )
        self._replay = Replay(*parse_header(self._header))
        self._path = path
        self._fd: int | None = None
        self._used = False
        self._seq = 0
        self._frozen = False
        self._fault: str | None = None

    @property
    def run(self) -> str:
        return self._replay.run

    @property
    def path(self) -> Path:
        return self._path

    def __enter__(self) -> Self:
        if self._used:
            raise RuntimeError("a journal opens only once")
        self._used = True
        fd = os.open(self._path, OPEN_FLAGS, FILE_MODE)
        self._fd = fd
        try:
            os.fchmod(fd, FILE_MODE)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("journal is not a regular file")
            self._append(self._header)
            fsync_directory(self._path.parent)
        except BaseException:
            self._fault = self._fault or "start"
            self.close()
            raise
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def fail(self) -> None:
        self._fault = self._fault or "caller"

    def sent(self, message: str, sender: str) -> bool:
        self._playing()
        check_identifier(message, "message")
        check_identifier(sender, "sender")
        if message in self._replay.sent and any(
            (e.message, e.sender) == (message, sender) for e in self._replay.edges
        ):
            return False
        return self._record({"type": "sent", "message": message, "sender": sender})

    def returned(self, message: str, receiver: str) -> bool:
        self._playing()
        if edge_key(message, receiver) in self._replay.returned:
            return False
        return self._record(
            {"type": "returned", "message": message, "receiver": receiver}
        )

    def acknowledged(self, message: str, receiver: str) -> bool:
        self._playing()
        if edge_key(message, receiver) in self._replay.acknowledged:
            return False
        return self._record(
            {"type": "acknowledged", "message": message, "receiver": receiver}
        )

    def commit(self, receiver: str) -> bool:
        self._playing()
        if check_identifier(receiver, "receiver") in self._replay.commits:
            return False
        return self._record({"type": "commit", "receiver": receiver})

    def freeze(self) -> dict[str, Any]:
        self._open()
        self._frozen = True
        return {"run": self._replay.run, "events": self._seq}

    def _open(self) -> None:
        if self._fd is None:
            raise RuntimeError("journal is not open")
        if self._fault is not None:
            raise RuntimeError(f"journal refused after a {self._fault} fault")

    def _playing(self) -> None:
        self._open()
        if self._frozen:
            raise RuntimeError("play is frozen")

    def _record(self, event: dict[str, Any]) -> bool:
        record = {"seq": self._seq, **event}
        if self._seq + 1 >= MAX_FRAMES:
            raise RuntimeError("journal reached its frame limit")
        self._replay.apply(record, self._seq)
        try:
            self._append(record)
        except BaseException:
            self._fault = self._fault or "write"
            raise
        return True

    def _append(self, record: dict[str, Any]) -> None:
        assert self._fd is not None and record["seq"] == self._seq
        data = encode_frame(record)
        if os.write(self._fd, data) != len(data):
            raise OSError("short write to the handoff journal")
        os.fsync(self._fd)
        self._seq += 1


def frame_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def check_cut(cut: object) -> tuple[str, int]:
    if not isinstance(cut, dict) or set(cut) != CUT_KEYS:
        raise ValueError("cut must be exactly {run, events}")
    fields = cast(dict[str, object], cut)
    run, events = fields["run"], frame_count(fields["events"])
    if not isinstance(run, str) or not canonical_uuid(run):
        raise ValueError("cut run is not a canonical UUID")
    if events is None or not 1 <= events < MAX_FRAMES:
        raise ValueError("cut events is not a frame count within the limit")
    return run, events


def seal(world_bytes: bytes, cut: dict[str, Any], reward: float | None) -> bytes:
    if type(world_bytes) is not bytes:
        raise TypeError("world_bytes must be bytes")
    run, events = check_cut(cut)
    frames, torn = frames_from_bytes(world_bytes)
    if torn:
        raise ValueError("world journal ends with a torn frame")
    replay = replay_frames(frames)
    if replay.run != run or len(frames) != events:
        raise ValueError("cut does not match the world journal")
    closed: dict[str, Any] = {"seq": events, "type": "closed", "reward": reward}
    replay.apply(closed, events)
    line = encode_frame(closed)
    if len(world_bytes) + len(line) > MAX_LOG_BYTES:
        raise ValueError(f"sealed journal exceeds {MAX_LOG_BYTES} bytes")
    return world_bytes + line
