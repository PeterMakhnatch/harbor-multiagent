import json
import os
import secrets
import signal
import socket
import stat
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from exhibit_relay.cases import make_case
from exhibit_relay.engine import Session

ROLES = ("loader", "registrar", "depot")
MAX_BODY_BYTES = 64 * 1024
LISTEN_BACKLOG = 8
ACCEPT_TIMEOUT = 0.2
BODY_TIMEOUT = 3.0
REPLY_TIMEOUT = 1.0


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
    def __init__(self, root: Path, control: Path, session: Session) -> None:
        self.root = root
        self.control = control
        self.session = session
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.frozen = False
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
        assert stat.S_ISSOCK(identity.st_mode)
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

    def _dispatch(self, role: str | None, request: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if role is None:
                if request != {"op": "freeze"}:
                    raise ValueError("Invalid control request")
                self.frozen = True
                return self.session.snapshot()
            if request == {"op": "view"}:
                return self.session.view(role)
            if self.frozen:
                raise ValueError("Session frozen")
            return self.session.apply(role, request)

    def _serve(self, role: str | None, connection: socket.socket) -> None:
        try:
            request = read_request(connection, self.stop)
            data = self._dispatch(role, request)
            reply = {"ok": True, "data": data}
        except (ValueError, RecursionError, TimeoutError):
            reply = {"ok": False, "error": "invalid request"}
        except OSError:
            return
        try:
            message = json.dumps(reply, separators=(",", ":"), allow_nan=False)
            connection.settimeout(REPLY_TIMEOUT)
            connection.sendall(message.encode("utf-8") + b"\n")
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


def main() -> None:
    seed_setting = os.environ.get("EXHIBIT_SEED")
    seed = secrets.randbits(63) if not seed_setting else int(seed_setting)
    profile = os.environ.get("EXHIBIT_PROFILE", "fork")
    mode = os.environ.get("EXHIBIT_MODE", "linked")
    layout = os.environ.get("EXHIBIT_LAYOUT", "draw")
    session = Session(make_case(seed, profile, layout=layout), mode)
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with Family(Path("/run/relay"), Path("/run/relay-control"), session):
        stop.wait()


if __name__ == "__main__":
    main()
