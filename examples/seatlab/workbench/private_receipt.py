from __future__ import annotations

import contextlib
import os
import re
import secrets
import stat

from .attempt_keeper import Skipped, _open_private
from .seat_stream import MIB, _require

MAX_BYTES = MIB
MAX_NAME = 200
MAX_DIRECTORY = 4096
NAME = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_NAME - 1}}}")
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
TEMP_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


class Conflict(Skipped):
    pass


def _linkable() -> bool:
    return os.link in os.supports_dir_fd and os.link in os.supports_follow_symlinks


def _private_file(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_size <= MAX_BYTES
    )


def _existing(dir_fd: int, name: str) -> bytes:
    fd = os.open(name, FILE_FLAGS, dir_fd=dir_fd)
    try:
        if not _private_file(os.fstat(fd)):
            raise Conflict("existing file is not a private bounded file")
        chunks: list[bytes] = []
        total = 0
        for _ in range(MAX_BYTES + 1):
            chunk = os.read(fd, MAX_BYTES + 1 - total)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise Conflict("existing file is not a private bounded file")
        raise Conflict("existing file is not a private bounded file")
    finally:
        os.close(fd)


def _stage(fd: int, data: bytes) -> tuple[int, int]:
    os.fchmod(fd, 0o600)
    view = memoryview(data)
    for _ in range(len(data)):
        written = os.write(fd, view)
        if written <= 0:
            raise Skipped("short private write")
        view = view[written:]
        if not view:
            os.fsync(fd)
            staged = os.fstat(fd)
            return staged.st_dev, staged.st_ino
    raise Skipped("short private write")


def _publish(dir_fd: int, name: str, data: bytes) -> None:
    temp = f".{name}.{secrets.token_hex(16)}.tmp"
    fd = os.open(temp, TEMP_FLAGS, 0o600, dir_fd=dir_fd)
    staged: tuple[int, int] | None = None
    try:
        try:
            staged = _stage(fd, data)
        finally:
            os.close(fd)
        os.link(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    except FileExistsError:
        staged = None
        if _existing(dir_fd, name) != data:
            raise Conflict("a different file already holds this name") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=dir_fd)
        os.fsync(dir_fd)
    kept = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not _private_file(kept) or kept.st_size != len(data):
        raise Conflict("published file changed")
    if staged is not None and (kept.st_dev, kept.st_ino) != staged:
        raise Conflict("published file changed")


def put_once(
    directory: str,
    name: str,
    data: bytes,
    *,
    pin: tuple[int, int] | None = None,
) -> None:
    _require(
        type(directory) is str
        and 0 < len(directory) <= MAX_DIRECTORY
        and os.path.isabs(directory),
        "private directory is not an absolute path",
    )
    _require(
        type(name) is str and NAME.fullmatch(name) is not None,
        "private file name is not a safe flat name",
    )
    _require(
        type(data) is bytes and 0 < len(data) <= MAX_BYTES,
        "private file is empty or exceeds 1 MiB",
    )
    _require(
        pin is None
        or (
            type(pin) is tuple
            and len(pin) == 2
            and all(type(part) is int for part in pin)
        ),
        "private directory pin is malformed",
    )
    if not _linkable():
        raise Skipped("linkat unsupported")
    dir_fd, _ = _open_private(directory, pin)
    try:
        _publish(dir_fd, name, data)
    finally:
        os.close(dir_fd)
