"""Private, synchronous JSONL exchange with an external turn dispatcher."""

import json
import os
import re
import select
from collections.abc import Generator
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from stat import S_IMODE
from threading import Lock
from time import monotonic
from typing import BinaryIO

TURN_SCHEMA = "seatlab.relay-turn/v1"
MAX_TEXT_BYTES = 64 * 1024
MAX_FRAME_BYTES = 6 * MAX_TEXT_BYTES + 512
_TURN_TIMEOUT_SEC = 120.0
_RUN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,63}")
_LABEL = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")


def _frame(value: dict[str, str]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate relay field")
    return result


def _encode_text(text: str) -> bytes:
    if not isinstance(text, str) or len(text) > MAX_TEXT_BYTES:
        raise ValueError("Invalid relay text")
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError("Invalid relay text")
    return data


def _wait(fd: int, deadline: float, *, write: bool = False) -> None:
    timeout = deadline - monotonic()
    if timeout > 0:
        reads, writes = ([], [fd]) if write else ([fd], [])
        ready_read, ready_write, _ = select.select(reads, writes, [], timeout)
        if ready_read or ready_write:
            return
    raise TimeoutError("Relay turn timed out")


def _write_frame(output: BinaryIO, frame: bytes, deadline: float) -> None:
    if isinstance(output, BytesIO):
        output.write(frame)
        return
    fd = output.fileno()
    data = memoryview(frame)
    while data:
        _wait(fd, deadline, write=True)
        # A writable pipe accepts PIPE_BUF bytes without blocking.
        size = os.write(fd, data[: select.PIPE_BUF])
        if not size:
            raise OSError("Relay output closed")
        data = data[size:]


def read_frame(input: BinaryIO, deadline: float) -> bytes:
    """Read one bounded LF-terminated frame before a monotonic deadline."""
    if isinstance(input, BytesIO):
        line = input.read(MAX_FRAME_BYTES + 1)
    else:
        fd = input.fileno()
        data = bytearray()
        while len(data) <= MAX_FRAME_BYTES:
            _wait(fd, deadline)
            chunk = os.read(fd, min(8192, MAX_FRAME_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        line = bytes(data)
    if (
        len(line) > MAX_FRAME_BYTES
        or not line.endswith(b"\n")
        or line.endswith(b"\r\n")
        or line.count(b"\n") != 1
    ):
        raise ValueError("Invalid relay framing")
    return line


class Exchange:
    """Own the protocol streams; allow only one outstanding turn and no retries."""

    def __init__(self, input: BinaryIO, output: BinaryIO, turn_directory: Path) -> None:
        self._input = input
        self._output = output
        self._turn_directory = turn_directory
        self._lock = Lock()
        self._attempted: set[str] = set()
        self._failed = False

    def ask(self, run: str, role: str, seat: str, prompt: str) -> str:
        if not all(isinstance(value, str) for value in (run, role, seat)) or not (
            _RUN.fullmatch(run) and _LABEL.fullmatch(role) and _LABEL.fullmatch(seat)
        ):
            raise ValueError("Invalid relay identity")
        if not self._lock.acquire(blocking=False):
            raise ValueError("A relay turn is already outstanding")
        try:
            turn_id = f"{run}:{role}"
            if self._failed or turn_id in self._attempted:
                raise ValueError("Relay turn cannot be retried")
            self._attempted.add(turn_id)
            try:
                return self._ask(run, role, seat, prompt, turn_id)
            except (ValueError, RecursionError):
                self._failed = True
                raise ValueError("Invalid relay turn") from None
            except OSError as error:
                self._failed = True
                if isinstance(error, TimeoutError):
                    raise TimeoutError("Relay turn timed out") from None
                raise OSError("Relay turn I/O failed") from None
        finally:
            self._lock.release()

    def _ask(self, run: str, role: str, seat: str, prompt: str, turn_id: str) -> str:
        prompt_sha256 = sha256(_encode_text(prompt)).hexdigest()
        name = f"{run}_{role}.json"
        directory = os.open(
            self._turn_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            info = os.fstat(directory)
            if S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
                raise ValueError("Invalid relay receipt directory")
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("Relay receipt already exists")

            deadline = monotonic() + _TURN_TIMEOUT_SEC
            request = _frame({"type": "turn", "id": turn_id, "prompt": prompt})
            if (
                not isinstance(self._input, BytesIO)
                and select.select([self._input.fileno()], [], [], 0)[0]
            ):
                raise ValueError("Relay input was readable before its request")
            _write_frame(self._output, request, deadline)
            reply = json.loads(
                read_frame(self._input, deadline).decode("utf-8"),
                object_pairs_hook=_unique_object,
            )
            if (
                not isinstance(reply, dict)
                or reply.keys() != {"id", "text"}
                or reply["id"] != turn_id
                or not isinstance(reply["text"], str)
            ):
                raise ValueError("Invalid relay reply")
            text = reply["text"]
            receipt = _frame(
                {
                    "schema": TURN_SCHEMA,
                    "run": run,
                    "role": role,
                    "seat": seat,
                    "prompt_sha256": prompt_sha256,
                    "response_sha256": sha256(_encode_text(text)).hexdigest(),
                    "response_text": text,
                }
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            with os.fdopen(os.open(name, flags, 0o600, dir_fd=directory), "wb") as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(receipt)
            return text
        finally:
            os.close(directory)


_binding_lock = Lock()
_bound: Exchange | None = None


@contextmanager
def bind(exchange: Exchange) -> Generator[None, None, None]:
    global _bound
    with _binding_lock:
        if _bound is not None:
            raise ValueError("A relay exchange is already bound")
        _bound = exchange
    try:
        yield
    finally:
        with _binding_lock:
            _bound = None


def current() -> Exchange:
    with _binding_lock:
        if _bound is None:
            raise ValueError("No relay exchange is bound")
        return _bound
