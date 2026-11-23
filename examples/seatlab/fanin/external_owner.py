"""Relay one native seat turn to an operator-owned SeatLab listener that this driver never starts.

admit: check p1.sock..p4.sock under one private directory without connecting.
relay: python -IS external_owner.py --socket ABS --device INT --inode INT --record ABS
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import NoReturn, override


def _sibling(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("missing SeatLab sibling module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __package__:
    from ..visibility import local_seat
    from . import protocol
else:
    _HERE = Path(__file__).resolve().parent
    local_seat = _sibling(
        "seatlab_local_seat", _HERE.parent / "visibility" / "local_seat.py"
    )
    protocol = _sibling("seatlab_fanin_protocol", _HERE / "protocol.py")

SCHEMA = local_seat._SCHEMA
SEATS: tuple[str, ...] = tuple(protocol.SEATS)
REQUEST = "request.json"
REPLY = "reply.json"
MAX_REQUEST_BYTES = local_seat._MAX_REQUEST_BYTES
MAX_REPLY_BYTES = local_seat._MAX_REPLY_BYTES
MAX_SOCKET_PATH_BYTES = local_seat._MAX_SOCKET_PATH_BYTES
RELAY_SECONDS = local_seat._OWNER_SECONDS - 6.0
RELAY = Path(__file__).resolve().parent.parent / "visibility" / "local_seat.py"
PRIVATE = 0o700
SOCKET_MODE = 0o600
READ_ONLY = 0o400
REFUSED = 2
NO_REPLY = 3
_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_FAILURES = (OSError, ValueError, TypeError, RecursionError)


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> NoReturn:
        raise ValueError("invalid external owner arguments")


def envelope(prompt: str) -> bytes:
    if type(prompt) is not str:
        raise ValueError("prompt must be text")
    try:
        text = json.dumps({"schema": SCHEMA, "prompt": prompt}, ensure_ascii=False)
        return (text + "\n").encode("utf-8")
    except UnicodeError:
        raise ValueError("prompt is not UTF-8 text") from None


def request_prompt(raw: bytes) -> str:
    if type(raw) is not bytes:
        raise ValueError("request must be bytes")
    try:
        local_seat._check_request(raw)
        frame = local_seat._load_line(raw, MAX_REQUEST_BYTES)
    except (local_seat._Fail, TypeError, ValueError, RecursionError):
        raise ValueError("request is not a seat turn") from None
    prompt = frame["prompt"]
    if type(prompt) is not str or envelope(prompt) != raw:
        raise ValueError("request is not canonical")
    return prompt


def reply_text(raw: bytes) -> str:
    if type(raw) is not bytes or len(raw) > MAX_REPLY_BYTES:
        raise ValueError("reply must be bounded bytes")
    try:
        local_seat._check_reply(raw)
        frame = local_seat._load_line(raw, MAX_REPLY_BYTES)
    except (local_seat._Fail, TypeError, ValueError, RecursionError):
        raise ValueError("reply is not a seat answer") from None
    text = frame["text"]
    if type(text) is not str:
        raise ValueError("reply is not a seat answer")
    return text


def staging_name(record: Path) -> Path:
    if not isinstance(record, Path) or record.name in ("", ".", ".."):
        raise ValueError("record must name a directory")
    return record.parent / f".{record.name}.staging"


def _posix() -> None:
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        raise ValueError("external seats require POSIX AF_UNIX sockets")


def _physical(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("path must be absolute")
    if os.path.realpath(path) != str(path):
        raise ValueError("path must be physical, without symlinks")
    return path


def _private_dir(path: Path) -> tuple[int, int]:
    info = os.lstat(_physical(path))
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != PRIVATE
    ):
        raise ValueError("directory must be yours with mode 0700")
    return info.st_dev, info.st_ino


def _disjoint(a: Path, b: Path) -> None:
    if a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError("directories must be disjoint")


def _listener(path: Path) -> tuple[int, int]:
    if len(os.fsencode(path)) > MAX_SOCKET_PATH_BYTES:
        raise ValueError("socket path exceeds the Unix bound")
    try:
        canonical = local_seat._socket_path(str(path))
    except local_seat._Fail:
        raise ValueError("socket path is unsafe") from None
    if canonical != str(path):
        raise ValueError("socket path is not canonical")
    info = os.lstat(path)
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != SOCKET_MODE
        or info.st_nlink != 1
    ):
        raise ValueError("listener node is unsafe")
    return info.st_dev, info.st_ino


def admit(directory: Path, *, out: Path) -> dict[str, tuple[int, int]]:
    _posix()
    directory = _physical(directory)
    if not isinstance(out, Path) or not out.is_absolute():
        raise ValueError("output must be absolute")
    for candidate in (out, Path(os.path.realpath(out))):
        _disjoint(directory, candidate)
    before = _private_dir(directory)
    if set(os.listdir(directory)) != {f"{seat}.sock" for seat in SEATS}:
        raise ValueError("expected only the four seat listeners")
    identities = {seat: _listener(directory / f"{seat}.sock") for seat in SEATS}
    if _private_dir(directory) != before:
        raise ValueError("socket directory changed during admission")
    if tuple(identities) != SEATS or len(set(identities.values())) != len(SEATS):
        raise ValueError("listeners must be distinct nodes, one per seat")
    return identities


def _arguments(argv: list[str] | None) -> tuple[Path, tuple[int, int], Path]:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--inode", type=int, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    listener: Path = args.socket
    if listener.name not in {f"{seat}.sock" for seat in SEATS}:
        raise ValueError("listener must be a seat socket")
    _private_dir(listener.parent)
    if args.device < 0 or args.inode <= 0:
        raise ValueError("listener identity is invalid")
    record: Path = args.record
    staging_name(record)
    _private_dir(record.parent)
    _disjoint(record, listener.parent)
    return listener, (args.device, args.inode), record


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, _CREATE, READ_ONLY)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _record_request(record: Path, raw: bytes) -> tuple[int, int]:
    staging = staging_name(record)
    if os.path.lexists(record):
        raise ValueError("record already exists")
    os.mkdir(staging, PRIVATE)
    _private_dir(staging)
    _write_new(staging / REQUEST, raw)
    _sync_dir(staging)
    if os.path.lexists(record):
        raise ValueError("record already exists")
    os.rename(staging, record)
    _sync_dir(record.parent)
    return _private_dir(record)


def _record_reply(record: Path, kept: tuple[int, int], reply: bytes) -> None:
    if _private_dir(record) != kept or os.listdir(record) != [REQUEST]:
        raise ValueError("record changed during the turn")
    staging = staging_name(record)
    os.mkdir(staging, PRIVATE)
    _private_dir(staging)
    _write_new(staging / REPLY, reply)
    os.link(staging / REPLY, record / REPLY)
    os.unlink(staging / REPLY)
    os.rmdir(staging)
    _sync_dir(record)
    _sync_dir(record.parent)


def _turn(listener: Path, raw: bytes, deadline: float) -> bytes | None:
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not os.path.isabs(sys.executable):
        return None
    try:
        done = subprocess.run(
            [sys.executable, "-IS", str(RELAY), "turn", str(listener)],
            input=raw,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            cwd="/",
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        reply_text(done.stdout)
    except ValueError:
        return None
    return done.stdout


def _emit(data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(1, view) :]


def _say(message: str) -> None:
    try:
        os.write(2, f"external owner: {message}\n".encode())
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    deadline = time.monotonic() + RELAY_SECONDS
    try:
        _posix()
        listener, identity, record = _arguments(argv)
        raw = local_seat._read_bounded(lambda size: os.read(0, size), MAX_REQUEST_BYTES)
        request_prompt(raw)
        kept = _record_request(record, raw)
        if _listener(listener) != identity:
            raise ValueError("listener is not the admitted node")
    except _FAILURES:
        _say("refused this turn")
        return REFUSED
    reply = _turn(listener, raw, deadline)
    if reply is None:
        _say("no reply")
        return NO_REPLY
    try:
        _record_reply(record, kept, reply)
        _emit(reply)
    except _FAILURES:
        _say("refused this reply")
        return REFUSED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
