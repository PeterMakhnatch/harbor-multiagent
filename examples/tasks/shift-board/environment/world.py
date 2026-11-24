"""Serve the shift-board world: seat authentication and bounded HTTP around the board."""

import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast, override

from board import Board

Role = Literal["planner", "dispatcher"]
Reply = tuple[int, bytes]

ROLES: tuple[Role, ...] = ("planner", "dispatcher")
ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/brief"),
        ("GET", "/status"),
        ("GET", "/board"),
        ("POST", "/prepare"),
        ("POST", "/publish"),
        ("POST", "/leave"),
    }
)
FIXTURES: tuple[str, ...] = ("planner-fit", "dispatcher-fit")
RULES: tuple[str, ...] = ("observe", "guard")
TOKEN_ROOT = Path("/run")
ID_PATH = Path("/logs/world/shift-board-id.json")
ID_SCHEMA = "harbor.shift-board.world/v1"
TICKET = re.compile(r"sb-[0-9a-f]{16}")
PORT = 8080
MAX_BODY_BYTES = 1024
MAX_SEED = 2_147_483_647
MAX_HANDLERS = 16
LISTEN_BACKLOG = 16
SOCKET_TIMEOUT_SEC = 5.0

logger = logging.getLogger(__name__)


class Refusal(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")


UNAVAILABLE: Reply = (503, encode({"error": "world unavailable"}))
FAILED: Reply = (500, encode({"error": "world unavailable"}))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise ValueError(f"unsupported JSON constant {name}")


def parse_object(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refusal(400, "body must be one JSON object") from None
    if not isinstance(value, dict):
        raise Refusal(400, "body must be one JSON object")
    return cast(dict[str, object], value)


class World:
    def __init__(self, board: Board, tokens: dict[Role, str]) -> None:
        assert set(tokens) == set(ROLES)
        assert all(tokens.values())
        assert len(set(tokens.values())) == len(tokens)
        self.tokens = tokens
        self._board = board
        self._lock = threading.Lock()
        self._fault = False

    def healthy(self) -> bool:
        with self._lock:
            return not self._fault

    def authenticate(self, authorizations: list[str]) -> Role | None:
        prefix = "Bearer "
        if len(authorizations) != 1 or not authorizations[0].startswith(prefix):
            return None
        presented = authorizations[0][len(prefix) :].encode("utf-8")
        matches = [
            role
            for role, token in self.tokens.items()
            if hmac.compare_digest(presented, token.encode("ascii"))
        ]
        return matches[0] if len(matches) == 1 else None

    def handle(
        self, seat: Role, method: str, path: str, body: dict[str, object]
    ) -> Reply:
        assert seat in ROLES
        assert (method, path) in ROUTES
        with self._lock:
            if self._fault:
                return UNAVAILABLE
            try:
                status, payload = self._board.request(seat, method, path, body)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise TypeError(f"board status is not an int: {status!r}")
                if not 200 <= status <= 599 or not isinstance(payload, dict):
                    raise TypeError(f"board reply is malformed: {status!r}")
                return status, encode(payload)
            except Exception:
                logger.exception("Board request failed; refusing further play")
                self._fault = True
                return FAILED

    def freeze(self) -> Reply:
        with self._lock:
            if self._fault:
                return UNAVAILABLE
            try:
                cut = self._board.freeze()
                if not isinstance(cut, dict):
                    raise TypeError("board cut is not an object")
                return 200, encode(cut)
            except Exception:
                logger.exception("Board freeze failed; refusing further play")
                self._fault = True
                return FAILED


class WorldServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = LISTEN_BACKLOG

    def __init__(self, world: World, address: tuple[str, int]) -> None:
        self.world = world
        self._handlers = threading.BoundedSemaphore(MAX_HANDLERS)
        super().__init__(address, Handler)

    @override
    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> None:
        assert isinstance(request, socket.socket)
        if not self._handlers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._handlers.release()
            raise

    @override
    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> None:
        try:
            assert isinstance(request, socket.socket)
            super().process_request_thread(request, client_address)
        finally:
            self._handlers.release()


class Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT_SEC

    def _send(self, status: int, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, refusal: Refusal) -> None:
        self._send(refusal.status, encode({"error": refusal.reason}))

    def _raw_body(self, *, minimum: int) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
            raise Refusal(411, "send one Content-Length and no Transfer-Encoding")
        text = lengths[0].strip()
        if not (text.isascii() and text.isdigit() and len(text) <= 5):
            raise Refusal(400, "invalid Content-Length")
        length = int(text)
        if not minimum <= length <= MAX_BODY_BYTES:
            raise Refusal(413, f"body must be {minimum} to {MAX_BODY_BYTES} bytes")
        body = self.rfile.read(length)
        if len(body) != length:
            raise Refusal(400, "incomplete body")
        return body

    def _seat_body(self, method: str) -> dict[str, object]:
        if method == "POST":
            return parse_object(self._raw_body(minimum=1))
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or any(
            length.strip() != "0" for length in lengths
        ):
            raise Refusal(400, "GET takes no body")
        return {}

    def _serve(self, method: str) -> None:
        world = cast(WorldServer, self.server).world
        if method == "GET" and self.path == "/health":
            healthy = world.healthy()
            self._send(200 if healthy else 503, encode({"ok": healthy}))
            return
        if method == "POST" and self.path == "/freeze":
            self._freeze(world)
            return
        if (method, self.path) not in ROUTES:
            self._send(404, encode({"error": "not found"}))
            return
        seat = world.authenticate(self.headers.get_all("Authorization", []))
        if seat is None:
            self._send(401, encode({"error": "send Authorization: Bearer <token>"}))
            return
        try:
            body = self._seat_body(method)
        except Refusal as refusal:
            self._refuse(refusal)
            return
        self._send(*world.handle(seat, method, self.path, body))

    def _freeze(self, world: World) -> None:
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            self._send(403, encode({"error": "forbidden"}))
            return
        try:
            if (
                self.headers.get_all("Content-Length", [])
                or self.headers.get("Transfer-Encoding") is not None
            ):
                body = self._raw_body(minimum=0)
                if body and parse_object(body) != {}:
                    raise Refusal(400, "freeze takes an empty JSON object")
        except Refusal as refusal:
            self._refuse(refusal)
            return
        self._send(*world.freeze())

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")


def publish_tokens(tokens: dict[Role, str], root: Path) -> None:
    for role, token in tokens.items():
        directory = root / role
        directory.mkdir(parents=True, exist_ok=True)
        staged = directory / ".token.tmp"
        staged.write_text(token, encoding="ascii")
        staged.chmod(0o444)
        os.replace(staged, directory / "token")


def ticket_setting(environ: dict[str, str]) -> str | None:
    ticket = environ.get("BOARD_TICKET", "")
    if not ticket:
        return None
    if TICKET.fullmatch(ticket) is None:
        raise ValueError("BOARD_TICKET must be empty or match sb-[0-9a-f]{16}")
    return ticket


def write_identity(path: Path, run: str, ticket: str | None) -> None:
    assert str(uuid.UUID(run)) == run
    assert ticket is None or TICKET.fullmatch(ticket) is not None
    record: dict[str, object] = {"schema": ID_SCHEMA, "run": run, "ticket": ticket}
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"World identity already exists: {path}")
    staged = path.with_name(f".{path.name}.tmp")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        data = encode(record) + b"\n"
        if os.write(fd, data) != len(data):
            raise OSError("Short write to the world identity")
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(staged, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def settings(environ: dict[str, str]) -> tuple[int, str, str]:
    seed = environ.get("BOARD_SEED", "0")
    decimal = seed.isascii() and seed.isdigit() and len(seed) <= 10
    if not (decimal and seed == str(int(seed)) and int(seed) <= MAX_SEED):
        raise ValueError(f"BOARD_SEED must be a decimal integer in [0, {MAX_SEED}]")
    fixture = environ.get("BOARD_FIXTURE", "planner-fit")
    if fixture not in FIXTURES:
        raise ValueError(f"BOARD_FIXTURE must be one of {list(FIXTURES)}")
    rule = environ.get("BOARD_RULE", "guard")
    if rule not in RULES:
        raise ValueError(f"BOARD_RULE must be one of {list(RULES)}")
    return int(seed), fixture, rule


def main() -> None:
    environ = dict(os.environ)
    seed, fixture, rule = settings(environ)
    ticket = ticket_setting(environ)
    run = str(uuid.uuid4())
    assert str(uuid.UUID(run)) == run
    board = Board(seed=seed, fixture=fixture, rule=rule, run=run)
    write_identity(ID_PATH, run, ticket)
    world = World(board, {role: secrets.token_urlsafe(32) for role in ROLES})
    publish_tokens(world.tokens, TOKEN_ROOT)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
