from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Self

from .generation_receipts import (
    MAX_LINE_BYTES,
    MAX_OWNER_BYTES,
    MAX_ROWS,
    _owner_row,
    owner,
)
from .seat_stream import MIB, Malformed, Record, _require

OWNERS_FILE = "owners.jsonl"
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
LOCK_ATTEMPTS = 2000
LOCK_POLL_SECONDS = 0.005
READ_CHUNK = MIB
MAX_DEPTH = 256
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
WALK_FLAGS = (
    getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
FILE_FLAGS = os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _encode(row: Record) -> bytes:
    text = json.dumps(
        row, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return text.encode("ascii") + b"\n"


MAX_ROW_BYTES = len(
    _encode(owner("00000000-0000-0000-0000-000000000000", "a" * 128, "a"))
)
_require(MAX_ROW_BYTES <= MAX_LINE_BYTES, "owner row bound exceeds the reader bound")


@contextlib.contextmanager
def _os(step: str) -> Iterator[None]:
    try:
        yield
    except Malformed:
        raise
    except (OSError, ValueError) as error:
        code = getattr(error, "errno", None)
        name = errno.errorcode.get(code, "invalid") if type(code) is int else "invalid"
        raise Malformed(f"owner journal {step} failed ({name})") from None


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _private(info: os.stat_result, kind: Callable[[int], bool], mode: int) -> bool:
    return (
        kind(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == mode
    )


def _private_directory(info: os.stat_result) -> os.stat_result:
    _require(
        _private(info, stat.S_ISDIR, DIRECTORY_MODE),
        "owner journal directory is not an owner-only 0700 directory",
    )
    return info


def _components(directory: Path) -> tuple[str, ...]:
    _require(
        isinstance(directory, Path) and directory.is_absolute(),
        "owner journal directory must be an absolute Path",
    )
    parts = directory.parts[1:]
    _require(
        0 < len(parts) <= MAX_DEPTH and ".." not in parts,
        "owner journal directory must be a plain absolute path",
    )
    return parts


def _walk(parts: tuple[str, ...]) -> int:
    fd = os.open("/", WALK_FLAGS)
    try:
        for index, name in enumerate(parts):
            flags = DIR_FLAGS if index == len(parts) - 1 else WALK_FLAGS
            fd, parent = os.open(name, flags, dir_fd=fd), fd
            os.close(parent)
            _require(
                stat.S_ISDIR(os.fstat(fd).st_mode),
                "owner journal directory path has a non-directory component",
            )
    except BaseException:
        os.close(fd)
        raise
    return fd


def _check_directory(parts: tuple[str, ...], dir_fd: int, pin: tuple[int, int]) -> None:
    with _os("check"):
        walked = _walk(parts)
        try:
            named = _private_directory(os.fstat(walked))
        finally:
            os.close(walked)
        held = _private_directory(os.fstat(dir_fd))
    _require(
        _identity(named) == _identity(held) == pin,
        "owner journal directory was moved or replaced",
    )


def _open_file(dir_fd: int) -> int:
    for _ in range(2):
        try:
            info = os.stat(OWNERS_FILE, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            flags = FILE_FLAGS | os.O_CREAT | os.O_EXCL
            try:
                fd = os.open(OWNERS_FILE, flags, FILE_MODE, dir_fd=dir_fd)
            except FileExistsError:
                continue
            try:
                os.fchmod(fd, FILE_MODE)
                os.fsync(dir_fd)
            except BaseException:
                os.close(fd)
                raise
            return fd
        _require(stat.S_ISREG(info.st_mode), "owner journal file is not regular")
        return os.open(OWNERS_FILE, FILE_FLAGS, dir_fd=dir_fd)
    raise Malformed("owner journal file changed while opening")


@contextlib.contextmanager
def _flocked(fd: int) -> Iterator[None]:
    with _os("lock"):
        for _ in range(LOCK_ATTEMPTS):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(LOCK_POLL_SECONDS)
        else:
            raise Malformed("owner journal lock is busy")
    try:
        yield
    finally:
        with _os("unlock"):
            fcntl.flock(fd, fcntl.LOCK_UN)


def _pread(fd: int, offset: int, length: int) -> bytes:
    data = bytearray()
    with _os("read"):
        for _ in range(length // READ_CHUNK + 2):
            if len(data) == length:
                break
            chunk = os.pread(
                fd, min(READ_CHUNK, length - len(data)), offset + len(data)
            )
            if not chunk:
                break
            data += chunk
    _require(len(data) == length, "owner journal file changed while reading")
    return bytes(data)


def _count(data: bytes, rows: int) -> int:
    if not data:
        return rows
    _require(data.endswith(b"\n"), "owner journal file has a torn tail")
    _require(b"\r" not in data, "owner journal file has carriage returns")
    lines = data[:-1].split(b"\n")
    _require(rows + len(lines) <= MAX_ROWS, "owner journal file has too many rows")
    for index, raw in enumerate(lines, start=rows):
        _require(
            0 < len(raw) <= MAX_LINE_BYTES,
            f"owner journal row {index} is empty or too long",
        )
        _owner_row(raw, index)
    return rows + len(lines)


class OwnerJournal:
    def __init__(
        self, parts: tuple[str, ...], dir_fd: int, dir_pin: tuple[int, int], fd: int
    ) -> None:
        with _os("bind"):
            self._file_pin = _identity(os.fstat(fd))
        self._parts = parts
        self._dir_fd = dir_fd
        self._dir_pin = dir_pin
        self._fd = fd
        self._size = 0
        self._rows = 0
        self._failed = False
        self._lock = threading.Lock()

    @classmethod
    def bind(cls, directory: Path) -> OwnerJournal:
        parts = _components(directory)
        with contextlib.ExitStack() as cleanup:
            with _os("bind"):
                dir_fd = _walk(parts)
                cleanup.callback(os.close, dir_fd)
                dir_pin = _identity(_private_directory(os.fstat(dir_fd)))
                fd = _open_file(dir_fd)
                cleanup.callback(os.close, fd)
            journal = cls(parts, dir_fd, dir_pin, fd)
            with _flocked(fd):
                journal._advance()
            cleanup.pop_all()
            return journal

    def _verify(self) -> int:
        _check_directory(self._parts, self._dir_fd, self._dir_pin)
        with _os("check"):
            named = os.stat(OWNERS_FILE, dir_fd=self._dir_fd, follow_symlinks=False)
            held = os.fstat(self._fd)
        _require(
            _identity(named) == _identity(held) == self._file_pin,
            "owner journal file was moved or replaced",
        )
        _require(
            _private(held, stat.S_ISREG, FILE_MODE) and held.st_nlink == 1,
            "owner journal file is not an owner-only 0600 unlinked regular file",
        )
        _require(
            held.st_size <= MAX_OWNER_BYTES, "owner journal file exceeds its bound"
        )
        return held.st_size

    def _advance(self) -> None:
        size = self._verify()
        _require(size >= self._size, "owner journal file shrank")
        self._rows = _count(_pread(self._fd, self._size, size - self._size), self._rows)
        self._size = size

    def append(self, trial: str, seat: str, generation_id: str) -> None:
        row = _encode(owner(trial, seat, generation_id))
        _require(len(row) <= MAX_ROW_BYTES, "owner journal row exceeds its bound")
        with self._lock:
            _require(
                self._fd >= 0 and not self._failed, "owner journal is closed or failed"
            )
            with _flocked(self._fd):
                self._advance()
                _require(
                    self._size + len(row) <= MAX_OWNER_BYTES and self._rows < MAX_ROWS,
                    "owner journal file would exceed its bound",
                )
                self._failed = True
                with _os("write"):
                    written = os.write(self._fd, row)
                    os.fsync(self._fd)
                    size = os.fstat(self._fd).st_size
                _require(
                    written == len(row) and size == self._size + len(row),
                    "owner journal write was partial or raced",
                )
                self._size, self._rows, self._failed = size, self._rows + 1, False

    def close(self) -> None:
        with self._lock:
            fds = (self._fd, self._dir_fd)
            self._fd = self._dir_fd = -1
        for fd in fds:
            if fd >= 0:
                os.close(fd)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
