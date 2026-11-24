"""Serve the revised-dispatch world and record its ordered handoff log and state."""

import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import dataclass, replace
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast

Role = Literal["reporter", "booker"]
Slot = Literal["A", "B", "C"]
Message = Literal["initial", "revision"]
Variant = Literal["replace", "confirm"]
Reply = tuple[int, dict[str, object]]

ROLES: tuple[Role, ...] = ("reporter", "booker")
SLOT_ORDER: tuple[Slot, ...] = ("A", "B", "C")
SLOTS: dict[str, Slot] = {slot: slot for slot in SLOT_ORDER}
MESSAGES: dict[str, Message] = {"initial": "initial", "revision": "revision"}
VARIANTS: dict[str, Variant] = {"replace": "replace", "confirm": "confirm"}
ROUTES: dict[tuple[str, str], Role] = {
    ("GET", "/brief"): "reporter",
    ("POST", "/report"): "reporter",
    ("GET", "/inbox"): "booker",
    ("POST", "/draft"): "booker",
    ("POST", "/commit"): "booker",
    ("POST", "/ack"): "booker",
}
SCHEMA = "harbor.handoffs/v1"
EDGES: tuple[dict[str, str], ...] = (
    {"message": "initial", "sender": "reporter", "receiver": "booker"},
    {"message": "revision", "sender": "reporter", "receiver": "booker"},
)
LOG_NAME = "handoffs.jsonl"
STATE_NAME = "state.json"
LOG_DIR = Path("/logs/world")
TOKEN_ROOT = Path("/run")
PORT = 8080
MAX_BODY_BYTES = 1024
MAX_SEED = 1_000_000

logger = logging.getLogger(__name__)


class Refusal(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Progress:
    reports: tuple[tuple[Message, Slot], ...] = ()
    returned: int = 0
    draft: Slot | None = None
    commit: Slot | None = None
    acknowledged: tuple[Message, ...] = ()
    frozen: bool = False
    seq: int = 1


def fixture(variant: Variant, seed: int) -> tuple[Slot, Slot]:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant: {variant!r}")
    if not 0 <= seed <= MAX_SEED:
        raise ValueError(f"Seed must be in [0, {MAX_SEED}]")
    digest = sha256(f"revised-dispatch/{seed}".encode("ascii")).digest()
    first = digest[0] % len(SLOT_ORDER)
    if variant == "confirm":
        return SLOT_ORDER[first], SLOT_ORDER[first]
    return SLOT_ORDER[first], SLOT_ORDER[(first + 1 + digest[1] % 2) % len(SLOT_ORDER)]


def _encode_line(event: dict[str, object]) -> bytes:
    return (
        json.dumps(event, separators=(",", ":"), allow_nan=False).encode("ascii")
        + b"\n"
    )


def _append(fd: int, events: list[dict[str, object]]) -> None:
    data = b"".join(_encode_line(event) for event in events)
    if not data:
        raise RuntimeError("Refusing an empty audit append")
    if os.write(fd, data) != len(data):
        raise OSError("Short write to the handoff log")
    os.fsync(fd)


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_state(path: Path, state: dict[str, object]) -> None:
    staged = path.with_name(f".{path.name}.tmp")
    with staged.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    staged.chmod(0o600)
    os.replace(staged, path)
    _fsync_directory(path.parent)


def _choice[T: str](value: object, choices: dict[str, T], name: str) -> T:
    if not isinstance(value, str) or value not in choices:
        raise Refusal(400, f"{name} must be one of {sorted(choices)}")
    return choices[value]


def _request(body: bytes | None, keys: tuple[str, ...]) -> dict[str, object]:
    if body is None or not 0 < len(body) <= MAX_BODY_BYTES:
        raise Refusal(400, "expected a small JSON object body")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refusal(400, "invalid JSON body") from None
    if not isinstance(value, dict) or set(value) != set(keys):
        raise Refusal(400, f"expected a JSON object with exactly {list(keys)}")
    return cast(dict[str, object], value)


class World:
    def __init__(self, log_dir: Path, variant: Variant, seed: int) -> None:
        self.variant = variant
        self.seed = seed
        self.initial, self.final = fixture(variant, seed)
        self.run = str(uuid.uuid4())
        self.tokens: dict[Role, str] = {
            role: secrets.token_urlsafe(32) for role in ROLES
        }
        self._receipts: dict[Message, str] = {
            message: secrets.token_urlsafe(24) for message in MESSAGES.values()
        }
        secrets_issued = [*self.tokens.values(), *self._receipts.values()]
        if len(set(secrets_issued)) != len(secrets_issued):
            raise RuntimeError("Seat tokens and receipts must be distinct")
        self.log_path = log_dir / LOG_NAME
        self.state_path = log_dir / STATE_NAME
        self._lock = threading.Lock()
        self._progress = Progress()
        self._fault: str | None = None
        log_dir.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            raise FileExistsError(f"World state already exists: {self.state_path}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
        self._log_fd = os.open(self.log_path, flags, 0o644)
        header: dict[str, object] = {
            "seq": 0,
            "type": "start",
            "schema": SCHEMA,
            "run": self.run,
            "edges": [dict(edge) for edge in EDGES],
            "acknowledgements": True,
        }
        try:
            _append(self._log_fd, [header])
            _write_state(self.state_path, self._state(self._progress))
        except BaseException:
            os.close(self._log_fd)
            raise

    def close(self) -> None:
        with self._lock:
            os.close(self._log_fd)

    def healthy(self) -> bool:
        with self._lock:
            return self._fault is None

    def freeze(self) -> dict[str, object]:
        """End play: persist frozen state, then refuse every seat route; idempotent."""
        with self._lock:
            if self._fault is not None:
                raise Refusal(503, "world unavailable")
            if not self._progress.frozen:
                self._persist(replace(self._progress, frozen=True), [])
            return {"run": self.run, "events": self._progress.seq}

    def handle(
        self, method: str, path: str, authorization: str | None, body: bytes | None
    ) -> Reply:
        route_role = ROUTES.get((method, path))
        if route_role is None:
            return 404, {"error": "not found"}
        role = self._authenticate(authorization)
        if role is None:
            return 401, {"error": "send Authorization: Bearer <your seat token>"}
        if role != route_role:
            return 403, {"error": "this endpoint belongs to the other seat"}
        try:
            if method == "GET" and body is not None:
                raise Refusal(400, "GET takes no body")
            with self._lock:
                if self._fault is not None:
                    raise Refusal(503, "world unavailable")
                if self._progress.frozen:
                    raise Refusal(409, "play is over")
                return 200, self._dispatch(path, body)
        except Refusal as refusal:
            return refusal.status, {"error": refusal.reason}

    def _authenticate(self, authorization: str | None) -> Role | None:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            return None
        presented = authorization[len(prefix) :].encode("utf-8")
        matches = [
            role
            for role, token in self.tokens.items()
            if hmac.compare_digest(presented, token.encode("ascii"))
        ]
        return matches[0] if len(matches) == 1 else None

    def _dispatch(self, path: str, body: bytes | None) -> dict[str, object]:
        if path == "/brief":
            phase, slot = self._phase(self._progress)
            return {"phase": phase, "slot": slot}
        if path == "/inbox":
            return self._inbox()
        if path == "/report":
            return self._report(_request(body, ("phase", "slot")))
        if path == "/draft":
            return self._draft(
                _choice(_request(body, ("slot",))["slot"], SLOTS, "slot")
            )
        if path == "/commit":
            return self._commit(
                _choice(_request(body, ("slot",))["slot"], SLOTS, "slot")
            )
        if path == "/ack":
            return self._ack(_request(body, ("phase", "receipt")))
        raise RuntimeError(f"Unrouted path: {path}")

    def _phase(self, progress: Progress) -> tuple[str, Slot | None]:
        sent = len(progress.reports)
        if sent == 0:
            return "initial", self.initial
        if sent == 1 and progress.draft is None:
            return "waiting", None
        if sent == 1:
            return "revision", self.final
        if sent == 2:
            return "done", None
        raise RuntimeError(f"Impossible report count: {sent}")

    def _report(self, request: dict[str, object]) -> dict[str, object]:
        phase = _choice(request["phase"], MESSAGES, "phase")
        slot = _choice(request["slot"], SLOTS, "slot")
        progress = self._progress
        if self._phase(progress)[0] != phase:
            raise Refusal(409, f"the {phase} report is not open now")
        self._persist(
            replace(progress, reports=(*progress.reports, (phase, slot))),
            [{"type": "sent", "message": phase, "sender": "reporter"}],
        )
        return {"ok": True}

    def _inbox(self) -> dict[str, object]:
        progress = self._progress
        unseen = progress.reports[progress.returned :]
        if unseen:
            self._persist(
                replace(progress, returned=len(progress.reports)),
                [
                    {"type": "returned", "message": message, "receiver": "booker"}
                    for message, _ in unseen
                ],
            )
        return {
            "reports": [
                {"phase": message, "slot": slot, "receipt": self._receipts[message]}
                for message, slot in progress.reports
            ]
        }

    def _draft(self, slot: Slot) -> dict[str, object]:
        progress = self._progress
        if progress.commit is not None:
            raise Refusal(409, "already committed")
        if progress.draft is not None:
            raise Refusal(409, "already drafted")
        self._persist(replace(progress, draft=slot), [])
        return {"ok": True}

    def _commit(self, slot: Slot) -> dict[str, object]:
        progress = self._progress
        if progress.commit is not None:
            raise Refusal(409, "already committed")
        self._persist(
            replace(progress, commit=slot),
            [{"type": "commit", "receiver": "booker"}],
        )
        return {"ok": True}

    def _ack(self, request: dict[str, object]) -> dict[str, object]:
        phase = _choice(request["phase"], MESSAGES, "phase")
        receipt = request["receipt"]
        if not isinstance(receipt, str) or not 0 < len(receipt) <= 64:
            raise Refusal(400, "receipt must be the string returned by /inbox")
        progress = self._progress
        returned = [message for message, _ in progress.reports[: progress.returned]]
        if phase not in returned:
            raise Refusal(409, f"the {phase} report has not been returned to you")
        if not hmac.compare_digest(
            receipt.encode("utf-8"), self._receipts[phase].encode("ascii")
        ):
            raise Refusal(409, "receipt does not match")
        if phase not in progress.acknowledged:
            self._persist(
                replace(progress, acknowledged=(*progress.acknowledged, phase)),
                [{"type": "acknowledged", "message": phase, "receiver": "booker"}],
            )
        return {"ok": True}

    def _persist(self, progress: Progress, events: list[dict[str, object]]) -> None:
        start = self._progress.seq
        staged = replace(progress, seq=start + len(events))
        if events:
            try:
                _append(
                    self._log_fd,
                    [
                        {"seq": start + offset, **event}
                        for offset, event in enumerate(events)
                    ],
                )
            except OSError:
                self._fail("audit")
                raise Refusal(500, "world record failed") from None
        try:
            _write_state(self.state_path, self._state(staged))
        except OSError:
            self._fail("state")
            raise Refusal(500, "world record failed") from None
        self._progress = staged

    def _fail(self, fault: str) -> None:
        logger.exception("World %s write failed; refusing further play", fault)
        self._fault = fault
        try:
            _write_state(self.state_path, self._state(self._progress))
        except OSError:
            logger.exception("Could not record the world fault in state")

    def _state(self, progress: Progress) -> dict[str, object]:
        return {
            "run": self.run,
            "variant": self.variant,
            "seed": self.seed,
            "initial": self.initial,
            "final": self.final,
            "draft": progress.draft,
            "commit": progress.commit,
            "reported_revision": next(
                (slot for message, slot in progress.reports if message == "revision"),
                None,
            ),
            "events": progress.seq,
            "frozen": progress.frozen,
            "fault": self._fault,
        }


class WorldServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, world: World, address: tuple[str, int]) -> None:
        self.world = world
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    timeout = 5.0

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
            raise Refusal(411, "send one Content-Length and no Transfer-Encoding")
        try:
            length = int(lengths[0])
        except ValueError:
            raise Refusal(400, "invalid Content-Length") from None
        if not 0 < length <= MAX_BODY_BYTES:
            raise Refusal(413, f"body must be 1 to {MAX_BODY_BYTES} bytes")
        body = self.rfile.read(length)
        if len(body) != length:
            raise Refusal(400, "incomplete body")
        return body

    def _serve(self, method: str) -> None:
        world = cast(WorldServer, self.server).world
        if method == "GET" and self.path == "/health":
            healthy = world.healthy()
            self._reply(200 if healthy else 503, {"ok": healthy})
            return
        if method == "POST" and self.path == "/freeze":
            self._freeze(world)
            return
        authorizations = self.headers.get_all("Authorization", [])
        if len(authorizations) > 1:
            self._reply(401, {"error": "send exactly one Authorization header"})
            return
        try:
            body = self._body() if method == "POST" else None
        except Refusal as refusal:
            self._reply(refusal.status, {"error": refusal.reason})
            return
        authorization = authorizations[0] if authorizations else None
        self._reply(*world.handle(method, self.path, authorization, body))

    def _freeze(self, world: World) -> None:
        if not ipaddress.ip_address(self.client_address[0]).is_loopback:
            self._reply(403, {"error": "forbidden"})
            return
        try:
            if self.headers.get_all("Content-Length", []):
                self._body()
            self._reply(200, world.freeze())
        except Refusal as refusal:
            self._reply(refusal.status, {"error": refusal.reason})

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


def settings(environ: dict[str, str]) -> tuple[Variant, int]:
    variant = environ.get("HANDOFF_VARIANT", "replace")
    if variant not in VARIANTS:
        raise ValueError(f"HANDOFF_VARIANT must be one of {sorted(VARIANTS)}")
    seed = environ.get("HANDOFF_SEED", "0")
    if not (seed.isascii() and seed.isdigit() and len(seed) <= 7):
        raise ValueError("HANDOFF_SEED must be a small nonnegative integer")
    return VARIANTS[variant], int(seed)


def main() -> None:
    variant, seed = settings(dict(os.environ))
    world = World(LOG_DIR, variant, seed)
    publish_tokens(world.tokens, TOKEN_ROOT)
    with WorldServer(world, ("0.0.0.0", PORT)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
