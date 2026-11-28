import argparse
import json
import math
import os
import random
import re
import secrets
import signal
import socket
import stat
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any, Self

ROLES = ("loader", "depot")
TRANSITIONS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")
DRAW = "draw"
SEED_PATTERN = re.compile(r"[0-9]{1,19}")
SEED_LIMIT = 2**63
SOCKET_ROOT = "/run/recall"
CONTROL_DIR = "/run/recall-control"
MAX_BODY_BYTES = 64 * 1024
LISTEN_BACKLOG = 8
ACCEPT_TIMEOUT = 0.2
BODY_TIMEOUT = 3.0
REPLY_TIMEOUT = 1.0
ERROR_LINE = b'{"ok":false,"error":"invalid request"}\n'


def encode_reply(data: object) -> bytes:
    envelope = {"ok": True, "data": data}
    message = json.dumps(envelope, separators=(",", ":"), allow_nan=False)
    return message.encode("utf-8") + b"\n"


def encode_error() -> bytes:
    return ERROR_LINE


def settings(environ: Mapping[str, str]) -> tuple[int, str, str]:
    raw_seed = environ.get("RECALL_SEED", "")
    if raw_seed == "":
        seed = secrets.randbits(63)
    elif SEED_PATTERN.fullmatch(raw_seed) and int(raw_seed) < SEED_LIMIT:
        seed = int(raw_seed)
    else:
        raise ValueError(f"RECALL_SEED must be empty or an int below {SEED_LIMIT}")
    transition = environ.get("RECALL_TRANSITION", "") or DRAW
    if transition == DRAW:
        transition = random.Random(f"dispatch-recall/transition/{seed}").choice(
            TRANSITIONS
        )
    elif transition not in TRANSITIONS:
        raise ValueError(f"RECALL_TRANSITION must be one of {[*TRANSITIONS, DRAW]}")
    timing = environ.get("RECALL_TIMING", "") or TIMINGS[0]
    if timing not in TIMINGS:
        raise ValueError(f"RECALL_TIMING must be one of {list(TIMINGS)}")
    return seed, transition, timing


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def reject_constant(value: str) -> object:
    raise ValueError("Invalid constant")


def read_request(connection: socket.socket, stop: threading.Event) -> dict[str, Any]:
    deadline = time.monotonic() + BODY_TIMEOUT
    body = bytearray()
    for _ in range(MAX_BODY_BYTES + 1):
        if stop.is_set():
            raise ValueError("Server stopping")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Request timeout")
        connection.settimeout(remaining)
        chunk = connection.recv(min(4096, MAX_BODY_BYTES + 1 - len(body)))
        if not chunk:
            raise ValueError("Missing newline")
        end = chunk.find(b"\n")
        if end >= 0:
            body.extend(chunk[:end])
            request = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
            if not isinstance(request, dict):
                raise ValueError("Expected object")
            return request
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise ValueError("Request too large")
    raise ValueError("Missing newline")


class Family:
    def __init__(self, root: Path, control: Path, session: Any) -> None:
        self.root = root
        self.control = control
        self.session = session
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.cut: object = None
        self.listeners: list[tuple[str | None, socket.socket]] = []
        self.paths: list[tuple[Path, int, int]] = []
        self.threads: list[threading.Thread] = []

    def _bind(self, directory: Path, role: str | None) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "rpc.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listeners.append((role, listener))
        listener.bind(str(path))
        identity = path.lstat()
        if not stat.S_ISSOCK(identity.st_mode):
            raise RuntimeError(f"Bound path is not a socket: {path}")
        self.paths.append((path, identity.st_dev, identity.st_ino))
        path.chmod(0o600 if role is None else 0o666)
        listener.listen(LISTEN_BACKLOG)
        listener.settimeout(ACCEPT_TIMEOUT)

    def __enter__(self) -> Self:
        if self.listeners or self.stop.is_set():
            raise RuntimeError("Family cannot be restarted")
        try:
            for role in ROLES:
                self._bind(self.root / role, role)
            self._bind(self.control, None)
            for role, listener in self.listeners:
                thread = threading.Thread(target=self._listen, args=(role, listener))
                thread.start()
                self.threads.append(thread)
        except BaseException:
            self._close()
            raise
        return self

    def _close(self) -> None:
        self.stop.set()
        for thread in self.threads:
            thread.join()
        for _, listener in self.listeners:
            listener.close()
        for path, device, inode in reversed(self.paths):
            try:
                identity = path.lstat()
                if (identity.st_dev, identity.st_ino) == (
                    device,
                    inode,
                ) and stat.S_ISSOCK(identity.st_mode):
                    path.unlink()
            except FileNotFoundError:
                pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()

    def _dispatch(self, role: str | None, request: dict[str, Any]) -> object:
        with self.lock:
            if role is None:
                if request != {"op": "freeze"}:
                    raise ValueError("Invalid control request")
                if self.cut is None:
                    self.cut = self.session.snapshot()
                return self.cut
            if self.cut is not None:
                raise ValueError("Session frozen")
            if request == {"op": "view"}:
                return self.session.view(role)
            return self.session.apply(role, request)

    def _serve(self, role: str | None, connection: socket.socket) -> None:
        try:
            request = read_request(connection, self.stop)
            line = encode_reply(self._dispatch(role, request))
        except (ValueError, RecursionError, TimeoutError):
            line = encode_error()
        except OSError:
            return
        try:
            connection.settimeout(REPLY_TIMEOUT)
            connection.sendall(line)
        except OSError:
            pass

    def _listen(self, role: str | None, listener: socket.socket) -> None:
        while not self.stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                self._serve(role, connection)


def lifetime(argv: list[str] | None) -> float | None:
    parser = argparse.ArgumentParser(prog="recall_server.py")
    parser.add_argument("--lifetime-seconds", type=float)
    seconds = parser.parse_args(argv).lifetime_seconds
    if seconds is not None and not (math.isfinite(seconds) and seconds > 0):
        raise ValueError("--lifetime-seconds must be a positive finite number")
    return seconds


def main(argv: list[str] | None = None) -> None:
    from recall_world import Session

    seconds = lifetime(argv)
    seed, transition, timing = settings(os.environ)
    session = Session(seed, transition, timing)
    root = Path(os.environ.get("RECALL_SOCKET_ROOT", "") or SOCKET_ROOT)
    control = Path(os.environ.get("RECALL_CONTROL_DIR", "") or CONTROL_DIR)
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with Family(root, control, session):
        stop.wait(seconds)


if __name__ == "__main__":
    main()
