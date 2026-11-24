import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast, override

from shipment import CHANNELS, PRIORS, REF_LENGTH, ROLES, ROUTES, SCHEMA, Shipment

Reply = tuple[int, bytes]

TOKEN_ROOT = Path("/run")
PORT = 8080
MAX_BODY_BYTES = 512
MAX_HANDLERS = 8
LISTEN_BACKLOG = 8
SOCKET_TIMEOUT_SEC = 5.0
REASONS = {
    400: "bad request",
    401: "unauthorized",
    403: "forbidden",
    404: "not found",
    411: "length required",
    413: "too large",
    503: "world unavailable",
}

logger = logging.getLogger(__name__)


class Refusal(Exception):
    def __init__(self, status: int) -> None:
        assert status in REASONS and status != 503
        super().__init__(REASONS[status])
        self.status = status


def encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")


def symbol(status: int) -> Reply:
    assert status in REASONS
    return status, encode({"error": REASONS[status]})


UNAVAILABLE = symbol(503)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name: str) -> object:
    raise ValueError("unsupported JSON constant")


def parse_object(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refusal(400) from None
    if not isinstance(value, dict):
        raise Refusal(400)
    return cast(dict[str, object], value)


class World:
    def __init__(
        self, shipment: Shipment, tokens: dict[str, str], refs: tuple[str, str]
    ) -> None:
        assert tuple(tokens) == ROLES
        assert all(token.isascii() and len(token) >= 32 for token in tokens.values())
        assert len(set(tokens.values())) == len(tokens)
        assert len(set(refs)) == len(refs) == 2
        self.tokens = tokens
        self._tokens = tuple(token.encode("ascii") for token in tokens.values())
        self._private = (*self._tokens, *(ref.encode("ascii") for ref in refs))
        self._shipment = shipment
        self._lock = threading.Lock()
        self._fault = False

    def healthy(self) -> bool:
        with self._lock:
            return not self._fault

    def authenticate(self, authorizations: list[str]) -> str | None:
        prefix = "Bearer "
        if len(authorizations) != 1 or not authorizations[0].startswith(prefix):
            return None
        presented = authorizations[0][len(prefix) :].encode("utf-8", "replace")
        matches = [
            role
            for role, token in zip(ROLES, self._tokens, strict=True)
            if hmac.compare_digest(presented, token)
        ]
        return matches[0] if len(matches) == 1 else None

    def _guarded(
        self, produce: Callable[[], tuple[object, object]], forbidden: tuple[bytes, ...]
    ) -> Reply:
        with self._lock:
            if self._fault:
                return UNAVAILABLE
            try:
                status, payload = produce()
                if type(status) is not int or not 200 <= status <= 499:
                    raise TypeError("shipment status is malformed")
                if not isinstance(payload, dict):
                    raise TypeError("shipment payload is malformed")
                data = encode(cast(dict[str, object], payload))
                if any(secret in data for secret in forbidden):
                    raise ValueError("shipment reply leaks a private value")
                return status, data
            except Exception as error:
                logger.error("Shipment failed closed: %s", type(error).__name__)
                self._fault = True
                return UNAVAILABLE

    def handle(
        self, role: str, method: str, path: str, body: dict[str, object]
    ) -> Reply:
        assert role in ROLES and (method, path) in ROUTES
        return self._guarded(
            lambda: self._shipment.handle(role, method, path, body), self._tokens
        )

    def freeze(self) -> Reply:
        def cut() -> tuple[object, object]:
            frozen = self._shipment.freeze()
            if not isinstance(frozen, dict) or frozen.get("schema") != SCHEMA:
                raise TypeError("shipment cut is malformed")
            return 200, frozen

        return self._guarded(cut, self._private)


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
            super().process_request_thread(request, client_address)
        finally:
            self._handlers.release()

    @override
    def handle_error(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> None:
        logger.warning("Request aborted")


class Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT_SEC

    @override
    def log_message(self, format: str, *args: object) -> None:
        return

    @override
    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        self.close_connection = True
        self._send(*symbol(400))

    def _send(self, status: int, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self, *, minimum: int) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) > 1:
            raise Refusal(411)
        if not lengths:
            if minimum > 0:
                raise Refusal(411)
            return b""
        text = lengths[0].strip()
        if not (text.isascii() and text.isdigit() and len(text) <= 4):
            raise Refusal(400)
        length = int(text)
        if not minimum <= length <= MAX_BODY_BYTES:
            raise Refusal(413)
        body = self.rfile.read(length)
        if len(body) != length:
            raise Refusal(400)
        return body

    def _serve(self, method: str) -> None:
        world = cast(WorldServer, self.server).world
        try:
            if method == "GET" and self.path == "/health":
                healthy = world.healthy()
                self._send(200 if healthy else 503, encode({"ok": healthy}))
            elif method == "POST" and self.path == "/freeze":
                self._freeze(world)
            elif (method, self.path) not in ROUTES:
                self._send(*symbol(404))
            else:
                role = world.authenticate(self.headers.get_all("Authorization", []))
                if role is None:
                    raise Refusal(401)
                if method == "POST":
                    body = parse_object(self._body(minimum=1))
                elif self._body(minimum=0):
                    raise Refusal(400)
                else:
                    body = {}
                self._send(*world.handle(role, method, self.path, body))
        except Refusal as refusal:
            self.close_connection = True
            self._send(*symbol(refusal.status))

    def _freeze(self, world: World) -> None:
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            raise Refusal(403)
        body = self._body(minimum=0)
        if body and parse_object(body) != {}:
            raise Refusal(400)
        self._send(*world.freeze())

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")


def publish_tokens(tokens: dict[str, str], root: Path) -> None:
    assert tuple(tokens) == ROLES
    for role, token in tokens.items():
        directory = root / role
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        staged = directory / ".token.tmp"
        staged.unlink(missing_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(staged, flags, 0o444)
        try:
            data = token.encode("ascii")
            if os.write(fd, data) != len(data):
                raise OSError("short token write")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(staged, directory / "token")


def settings(environ: dict[str, str]) -> tuple[str, str]:
    prior = environ.get("SH_PRIOR")
    if prior not in PRIORS:
        raise ValueError(f"SH_PRIOR must be one of {list(PRIORS)}")
    channel = environ.get("SH_CHANNEL")
    if channel not in CHANNELS:
        raise ValueError(f"SH_CHANNEL must be one of {list(CHANNELS)}")
    return prior, channel


def fresh_refs() -> tuple[str, str]:
    request_ref = secrets.token_hex(REF_LENGTH // 2)
    draft_ref = secrets.token_hex(REF_LENGTH // 2)
    if draft_ref == request_ref:
        raise RuntimeError("reference generation repeated")
    assert len(request_ref) == len(draft_ref) == REF_LENGTH
    return request_ref, draft_ref


def main() -> None:
    prior, channel = settings(dict(os.environ))
    run = str(uuid.uuid4())
    assert str(uuid.UUID(run)) == run
    request_ref, draft_ref = fresh_refs()
    shipment = Shipment(
        prior=prior,
        channel=channel,
        run=run,
        request_ref=request_ref,
        draft_ref=draft_ref,
    )
    tokens: dict[str, str] = {role: secrets.token_urlsafe(32) for role in ROLES}
    world = World(shipment, tokens, (request_ref, draft_ref))
    publish_tokens(world.tokens, TOKEN_ROOT)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
