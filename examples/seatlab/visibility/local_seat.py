"""Relay one seatlab.seat-turn/v1 turn to a trusted owner program over a private POSIX socket.

serve: python -IS /abs/local_seat.py serve /private/dir/role.sock -- /abs/owner [arg...]
turn:  python -IS /abs/local_seat.py turn /private/dir/role.sock

Not a sandbox. The owner program sees only HOME, PATH, TMPDIR and LANG.
"""

from __future__ import annotations

import errno
import json
import os
import re
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from types import FrameType

_SCHEMA = "seatlab.seat-turn/v1"
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_TEXT_BYTES = 8192
_MAX_REPLY_BYTES = 6 * _MAX_TEXT_BYTES + 256
_MAX_SOCKET_PATH_BYTES = 103
_ACCEPT_SECONDS = 600.0
_TURN_SECONDS = 82.0
_OWNER_SECONDS = 70.0
_NATIVE_SECONDS = 86
_CONNECT_ATTEMPTS = 100
_CONNECT_PAUSE_SECONDS = 0.1
_OWNER_ENV = ("HOME", "PATH", "TMPDIR", "LANG")
_SOCKET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

_INTERNAL = 1
_USAGE = 2
_NO_NATIVE = 3
_BAD_REQUEST = 4
_OWNER_FAILED = 5
_TIMED_OUT = 6
_NATIVE_LOST = 7
_INTERRUPTED = 8
_MESSAGES = {
    _INTERNAL: "internal error",
    _USAGE: "usage: local_seat.py serve SOCKET -- /abs/owner [arg...] | turn SOCKET",
    _NO_NATIVE: "no native turn arrived within 10 minutes",
    _BAD_REQUEST: "native request was invalid",
    _OWNER_FAILED: "owner program failed or replied invalidly",
    _TIMED_OUT: "turn timed out",
    _NATIVE_LOST: "native connection failed",
    _INTERRUPTED: "interrupted",
}


class _Fail(Exception):
    def __init__(self, code: int, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message or _MESSAGES[code]


def _say(message: str) -> None:
    try:
        os.write(2, f"local_seat: {message}\n".encode())
    except OSError:
        pass


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise _Fail(_INTERRUPTED)


def _expire(signum: int, frame: FrameType | None) -> None:
    raise _Fail(_TIMED_OUT)


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise _Fail(_TIMED_OUT)
    return seconds


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON constant")


def _load_line(line: bytes, limit: int) -> dict[str, object]:
    if len(line) > limit or not line.endswith(b"\n") or line.count(b"\n") != 1:
        raise ValueError("expected one JSON line")
    frame: object = json.loads(
        line.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(frame, dict):
        raise TypeError("expected a JSON object")
    fields: dict[str, object] = {}
    for key, value in frame.items():
        if not isinstance(key, str):
            raise TypeError("expected string JSON keys")
        fields[key] = value
    return fields


def _check_request(line: bytes) -> None:
    try:
        frame = _load_line(line, _MAX_REQUEST_BYTES)
        prompt = frame.get("prompt")
        if (
            set(frame) != {"schema", "prompt"}
            or frame["schema"] != _SCHEMA
            or not isinstance(prompt, str)
        ):
            raise ValueError("invalid request")
        prompt.encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise _Fail(_BAD_REQUEST) from None


def _check_reply(line: bytes) -> None:
    try:
        frame = _load_line(line, _MAX_REPLY_BYTES)
        text = frame.get("text")
        if (
            set(frame) != {"text"}
            or not isinstance(text, str)
            or len(text.encode("utf-8")) > _MAX_TEXT_BYTES
        ):
            raise ValueError("invalid reply")
    except (TypeError, ValueError, RecursionError):
        raise _Fail(_OWNER_FAILED) from None


def _read_bounded(read: Callable[[int], bytes], limit: int) -> bytes:
    data = bytearray()
    while len(data) <= limit:
        chunk = read(limit + 1 - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _socket_path(raw: str) -> str:
    if not os.path.isabs(raw):
        raise _Fail(_USAGE, "socket path must be absolute")
    parent, name = os.path.split(raw)
    if _SOCKET_NAME.fullmatch(name) is None:
        raise _Fail(_USAGE, "socket name must be 1-64 of A-Z a-z 0-9 . _ -")
    try:
        shown = os.lstat(parent)
        canonical = os.path.realpath(parent)
        real = os.lstat(canonical)
    except (OSError, ValueError):
        raise _Fail(_USAGE, "socket directory must already exist") from None
    if not stat.S_ISDIR(shown.st_mode) or (shown.st_dev, shown.st_ino) != (
        real.st_dev,
        real.st_ino,
    ):
        raise _Fail(_USAGE, "socket directory must be a real directory, not a symlink")
    if real.st_uid != os.geteuid() or stat.S_IMODE(real.st_mode) != 0o700:
        raise _Fail(_USAGE, "socket directory must be yours with mode 0700 (chmod 700)")
    path = os.path.join(canonical, name)
    if len(os.fsencode(path)) > _MAX_SOCKET_PATH_BYTES:
        raise _Fail(
            _USAGE,
            "socket path exceeds 103 bytes; use a short directory "
            "such as one from mktemp -d /tmp/seat.XXXXXX",
        )
    return path


def _owner_argv(argv: list[str]) -> list[str]:
    if not argv or not os.path.isabs(argv[0]):
        raise _Fail(_USAGE, "owner program must be an absolute path")
    try:
        status = os.stat(argv[0])
    except (OSError, ValueError):
        raise _Fail(_USAGE, "owner program must be an executable file") from None
    if not stat.S_ISREG(status.st_mode) or not os.access(argv[0], os.X_OK):
        raise _Fail(_USAGE, "owner program must be an executable file")
    return argv


def _bind(listener: socket.socket, path: str) -> tuple[int, int]:
    previous = os.umask(0o177)
    try:
        listener.bind(path)
    except OSError as error:
        if error.errno in (errno.EADDRINUSE, errno.EEXIST):
            raise _Fail(
                _USAGE, "socket path already exists; remove the stale socket and retry"
            ) from None
        raise _Fail(_USAGE, "could not create the socket") from None
    finally:
        os.umask(previous)
    status = os.lstat(path)
    if not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.geteuid():
        raise _Fail(_USAGE, "socket was replaced during creation")
    if stat.S_IMODE(status.st_mode) != 0o600:
        os.unlink(path)
        raise _Fail(_USAGE, "socket was not created with mode 0600")
    return status.st_dev, status.st_ino


def _remove_own(path: str, identity: tuple[int, int]) -> None:
    try:
        status = os.lstat(path)
        if stat.S_ISSOCK(status.st_mode) and (status.st_dev, status.st_ino) == identity:
            os.unlink(path)
    except OSError:
        pass


def _read_request(conn: socket.socket, deadline: float) -> bytes:
    data = bytearray()
    while b"\n" not in data and len(data) <= _MAX_REQUEST_BYTES:
        conn.settimeout(_remaining(deadline))
        chunk = conn.recv(_MAX_REQUEST_BYTES + 1 - len(data))
        if not chunk:
            break
        data.extend(chunk)
    request = bytes(data)
    _check_request(request)
    return request


def _native_gone(conn: socket.socket) -> None:
    try:
        data = conn.recv(1)
    except BlockingIOError:
        return
    except OSError:
        raise _Fail(_NATIVE_LOST) from None
    raise _Fail(_BAD_REQUEST if data else _NATIVE_LOST)


def _feed(fd: int, payload: memoryview) -> int:
    try:
        return os.write(fd, payload)
    except BlockingIOError:
        return 0
    except OSError:
        raise _Fail(_OWNER_FAILED) from None


def _drain(fd: int, size: int) -> bytes | None:
    try:
        return os.read(fd, size)
    except BlockingIOError:
        return None
    except OSError:
        raise _Fail(_OWNER_FAILED) from None


def _exchange(
    process: subprocess.Popen[bytes],
    request: bytes,
    conn: socket.socket,
    deadline: float,
) -> bytes:
    if process.stdin is None or process.stdout is None:
        raise _Fail(_OWNER_FAILED)
    stdin, stdout = process.stdin.fileno(), process.stdout.fileno()
    os.set_blocking(stdin, False)
    os.set_blocking(stdout, False)
    conn.setblocking(False)
    response = bytearray()
    sent = 0
    with memoryview(request) as payload, selectors.DefaultSelector() as ready:
        ready.register(stdin, selectors.EVENT_WRITE)
        ready.register(stdout, selectors.EVENT_READ)
        ready.register(conn, selectors.EVENT_READ)
        while stdout in ready.get_map():
            for key, _ in ready.select(_remaining(deadline)):
                if key.fileobj is conn:
                    _native_gone(conn)
                elif key.fd == stdin:
                    sent += _feed(stdin, payload[sent:])
                    if sent == len(request):
                        ready.unregister(stdin)
                        process.stdin.close()
                else:
                    chunk = _drain(stdout, _MAX_REPLY_BYTES + 1 - len(response))
                    if chunk == b"":
                        ready.unregister(stdout)
                    response.extend(chunk or b"")
                    if len(response) > _MAX_REPLY_BYTES:
                        raise _Fail(_OWNER_FAILED)
    return bytes(response)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    process.poll()
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            process.kill()


def _run_owner(
    argv: list[str], request: bytes, conn: socket.socket, deadline: float
) -> bytes:
    environ = {name: os.environ[name] for name in _OWNER_ENV if name in os.environ}
    try:
        process = subprocess.Popen(
            argv,
            env=environ,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        raise _Fail(_OWNER_FAILED) from None
    with process:
        try:
            reply = _exchange(process, request, conn, deadline)
            try:
                status = process.wait(timeout=_remaining(deadline))
            except subprocess.TimeoutExpired:
                raise _Fail(_TIMED_OUT) from None
            if status != 0:
                raise _Fail(_OWNER_FAILED)
        finally:
            _kill_group(process)
    _check_reply(reply)
    return reply


def _serve(raw_path: str, owner: list[str]) -> None:
    argv = _owner_argv(owner)
    path = _socket_path(raw_path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    identity: tuple[int, int] | None = None
    try:
        identity = _bind(listener, path)
        listener.listen(1)
        listener.settimeout(_ACCEPT_SECONDS)
        _say("ready")
        try:
            conn, _ = listener.accept()
        except TimeoutError:
            raise _Fail(_NO_NATIVE) from None
        with conn:
            listener.close()
            _remove_own(path, identity)
            identity = None
            deadline = time.monotonic() + _TURN_SECONDS
            request = _read_request(conn, deadline)
            owner_deadline = min(deadline, time.monotonic() + _OWNER_SECONDS)
            reply = _run_owner(argv, request, conn, owner_deadline)
            conn.settimeout(_remaining(deadline))
            conn.sendall(reply)
    finally:
        listener.close()
        if identity is not None:
            _remove_own(path, identity)


def _serve_reporting(raw_path: str, owner: list[str]) -> int:
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        _say("requires a POSIX system with AF_UNIX sockets")
        return _USAGE
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, _interrupt)
    try:
        _serve(raw_path, owner)
        return 0
    except _Fail as failure:
        code, message = failure.code, failure.message
    except (TimeoutError, subprocess.TimeoutExpired):
        code, message = _TIMED_OUT, _MESSAGES[_TIMED_OUT]
    except OSError:
        code, message = _NATIVE_LOST, _MESSAGES[_NATIVE_LOST]
    except (ValueError, RuntimeError):
        code, message = _INTERNAL, _MESSAGES[_INTERNAL]
    _say(message)
    return code


def _connect(path: str) -> socket.socket:
    for _ in range(_CONNECT_ATTEMPTS):
        try:
            status = os.lstat(path)
        except FileNotFoundError:
            time.sleep(_CONNECT_PAUSE_SECONDS)
            continue
        if not stat.S_ISSOCK(status.st_mode) or status.st_uid != os.geteuid():
            raise _Fail(_NATIVE_LOST)
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            conn.connect(path)
            return conn
        except (FileNotFoundError, ConnectionRefusedError):
            conn.close()
            time.sleep(_CONNECT_PAUSE_SECONDS)
        except BaseException:
            conn.close()
            raise
    raise _Fail(_NATIVE_LOST)


def _turn(raw_path: str) -> bytes:
    path = _socket_path(raw_path)
    request = _read_bounded(lambda size: os.read(0, size), _MAX_REQUEST_BYTES)
    _check_request(request)
    with _connect(path) as conn:
        conn.sendall(request)
        reply = _read_bounded(conn.recv, _MAX_REPLY_BYTES)
    _check_reply(reply)
    return reply


def _turn_quietly(raw_path: str) -> int:
    try:
        quiet = os.open(os.devnull, os.O_WRONLY)
        os.dup2(quiet, 2)
        os.close(quiet)
        signal.signal(signal.SIGALRM, _expire)
        signal.alarm(_NATIVE_SECONDS)
        view = memoryview(_turn(raw_path))
        while view:
            view = view[os.write(1, view) :]
        signal.alarm(0)
        return 0
    except _Fail as failure:
        return failure.code
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt):
        return _INTERNAL


def main(args: list[str]) -> int:
    if len(args) == 2 and args[0] == "turn":
        return _turn_quietly(args[1])
    if len(args) >= 4 and args[0] == "serve" and args[2] == "--":
        return _serve_reporting(args[1], args[3:])
    _say(_MESSAGES[_USAGE])
    return _USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
