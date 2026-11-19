"""CooperBench messages between seats, served from main.

Each CooperBench agent is a Harbor seat in its own container, and this
service is the only channel between them. It keeps the semantics of
CooperBench's messaging for CLI agents (upstream
``cooperbench.agents._coop.coop_msg``): one FIFO inbox per agent, a receive
drains the caller's inbox, and a broadcast is one message to each other
agent. It differs in two ways:

- Authentication. At start the service writes a random token to
  ``<tokens dir>/<seat>/token`` for each seat (a restart keeps the tokens).
  Compose mounts each of those directories in main and in that one seat only,
  so a seat can read its own token and no other. Every request except
  ``GET /health`` must send ``Authorization: Bearer <token>``, and the service
  takes the sender from the token: a seat cannot send as another seat or read
  another seat's inbox. Upstream's Redis inbox trusts a ``from`` field that
  the sender writes.
- Evidence. Each message is written once to ``<log dir>/messages.jsonl``.
  Each send, receive, peek, refusal and bad token is an event in
  ``<log dir>/events.jsonl``. The verifier and the analysis read both.

Limits, so that a seat cannot exhaust main, where the verifier runs: a
message is at most 64 KiB of UTF-8; a seat sends at most 2,000 messages and
8 MiB in all; events.jsonl stops at 100,000 events; a request body is at most
1 MiB and each read must arrive within 30 seconds; at most 32 connections are
served at once, and at most 8 from one address (each seat has its own).

Standard library only. Configured by environment variables:

  COOP_AGENTS      comma-separated seat names (required), e.g. agent1,agent2
  COOP_PORT        port to listen on (default 8080)
  COOP_TOKENS_DIR  directory with one mounted subdirectory per seat
                   (default /run/cooperbench/tokens)
  COOP_LOG_DIR     where the logs go (default /logs/cooperbench)
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY_BYTES = 1_048_576
MAX_MESSAGE_BYTES = 65_536
MAX_MESSAGES_PER_SEAT = 2_000
MAX_BYTES_PER_SEAT = 8_388_608
MAX_EVENTS = 100_000
REQUEST_TIMEOUT_SEC = 30
MAX_CONNECTIONS = 32
MAX_CONNECTIONS_PER_CLIENT = 8


class Refused(Exception):
    """A request the service turns down, with its HTTP status."""

    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status = status
        self.error = error


class Board:
    """The inboxes, the tokens and the logs, behind one lock."""

    def __init__(
        self,
        agents: list[str],
        tokens: dict[str, str],
        log_dir: Path,
        *,
        max_messages: int = MAX_MESSAGES_PER_SEAT,
        max_bytes: int = MAX_BYTES_PER_SEAT,
        max_events: int = MAX_EVENTS,
    ):
        self.agents = agents
        self.tokens = tokens
        self.max_messages = max_messages
        self.max_bytes = max_bytes
        self.max_events = max_events
        self.inboxes: dict[str, list[dict[str, Any]]] = {agent: [] for agent in agents}
        self.sent_by: dict[str, int] = {agent: 0 for agent in agents}
        self.bytes_by: dict[str, int] = {agent: 0 for agent in agents}
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.seq = 0
        self.events_written = 0
        self.next_id = 1
        log_dir.mkdir(parents=True, exist_ok=True)
        self.messages_path = log_dir / "messages.jsonl"
        self.events_path = log_dir / "events.jsonl"
        self.messages_path.touch()
        self.events_path.touch()

    def seat_of(self, authorization: str) -> str | None:
        """The seat whose token an ``Authorization: Bearer <token>`` header holds."""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        token_bytes = token.strip().encode()
        for seat, seat_token in self.tokens.items():
            if secrets.compare_digest(token_bytes, seat_token.encode()):
                return seat
        return None

    def event(self, seat: str | None, kind: str, data: dict[str, Any]) -> None:
        """Append one event; the caller holds the lock. The last event the
        cap allows says that the log is full."""
        if self.events_written >= self.max_events:
            return
        self.events_written += 1
        self.seq += 1
        if self.events_written == self.max_events:
            seat, kind, data = None, "events_capped", {"max_events": self.max_events}
        record = {
            "seq": self.seq,
            "t": round(time.monotonic() - self.started, 3),
            "seat": seat,
            "kind": kind,
            "data": data,
        }
        with self.events_path.open("a") as events:
            events.write(json.dumps(record) + "\n")

    def send(
        self, sender: str, recipient: str, content: str, size: int
    ) -> dict[str, Any]:
        """Queue one message of ``size`` UTF-8 bytes, or raise Refused when the
        sender has used up its messages or bytes."""
        now = time.time()
        with self.lock:
            if self.sent_by[sender] >= self.max_messages:
                raise Refused(429, f"a seat sends at most {self.max_messages} messages")
            if self.bytes_by[sender] + size > self.max_bytes:
                raise Refused(
                    429, f"a seat sends at most {self.max_bytes} bytes of messages"
                )
            message = {
                "id": f"m{self.next_id}",
                "from": sender,
                "to": recipient,
                "content": content,
                "timestamp": now,
                "timestamp_iso": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            }
            self.next_id += 1
            self.sent_by[sender] += 1
            self.bytes_by[sender] += size
            self.inboxes[recipient].append(message)
            with self.messages_path.open("a", encoding="utf-8") as messages:
                messages.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.event(
                sender, "send", {"id": message["id"], "to": recipient, "bytes": size}
            )
        return message

    def receive(self, seat: str) -> list[dict[str, Any]]:
        with self.lock:
            delivered, self.inboxes[seat] = self.inboxes[seat], []
            self.event(seat, "recv", {"ids": [m["id"] for m in delivered]})
        return delivered

    def peek(self, seat: str) -> int:
        with self.lock:
            unread = len(self.inboxes[seat])
            self.event(seat, "peek", {"unread": unread})
        return unread


def make_handler(board: Board) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_SEC  # per socket read, so a stalled client is dropped

        def log_message(self, format: str, *args: object) -> None:
            pass  # events.jsonl is the record

        def _reply(self, status: int, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self, seat: str | None, status: int, error: str) -> None:
            with board.lock:
                board.event(seat, "denied", {"path": self.path, "error": error})
            self._reply(status, {"error": error})

        def _caller(self) -> str | None:
            seat = board.seat_of(self.headers.get("Authorization", ""))
            if seat is None:
                with board.lock:
                    board.event(None, "bad_token", {"path": self.path})
                self._reply(401, {"error": "send Authorization: Bearer <your token>"})
            return seat

        def _body(self, seat: str) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._refuse(
                    seat, 413, f"the body must be at most {MAX_BODY_BYTES} bytes"
                )
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                self._refuse(seat, 400, "send a JSON object")
                return None
            return body

        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, {"ok": True})
                return
            if self.path not in ("/agents", "/peek"):
                self._reply(404, {"error": "not found"})
                return
            seat = self._caller()
            if seat is None:
                return
            if self.path == "/agents":
                self._reply(200, {"agents": board.agents, "you": seat})
            else:
                self._reply(200, {"unread": board.peek(seat)})

        def do_POST(self) -> None:
            if self.path not in ("/send", "/recv"):
                self._reply(404, {"error": "not found"})
                return
            seat = self._caller()
            if seat is None:
                return
            body = self._body(seat)
            if body is None:
                return
            if self.path == "/recv":
                self._reply(200, {"messages": board.receive(seat)})
                return
            recipient, content = body.get("to"), body.get("content")
            if "from" in body and body["from"] != seat:
                self._refuse(seat, 403, f"this token is {seat}'s")
                return
            if not isinstance(recipient, str) or recipient not in board.inboxes:
                self._refuse(seat, 400, f"'to' must be one of {board.agents}")
                return
            if not isinstance(content, str):
                self._refuse(seat, 400, "'content' must be a string")
                return
            try:
                size = len(content.encode("utf-8"))
            except UnicodeEncodeError:
                self._refuse(seat, 400, "'content' must be valid Unicode")
                return
            if size > MAX_MESSAGE_BYTES:
                self._refuse(
                    seat, 413, f"a message is at most {MAX_MESSAGE_BYTES} bytes"
                )
                return
            try:
                message = board.send(seat, recipient, content, size)
            except Refused as refused:
                self._refuse(seat, refused.status, refused.error)
                return
            self._reply(201, message)

    return Handler


class Server(ThreadingHTTPServer):
    """A threading HTTP server that serves at most ``max_connections`` at once,
    and at most ``max_per_client`` from one client address, so one seat
    cannot take every slot. It closes any connection beyond that."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        max_connections: int = MAX_CONNECTIONS,
        max_per_client: int = MAX_CONNECTIONS_PER_CLIENT,
    ):
        super().__init__(address, handler)
        self.max_connections = max_connections
        self.max_per_client = max_per_client
        self.open_by_client: dict[str, int] = {}
        self.slots_lock = threading.Lock()

    def _claim(self, client: str) -> bool:
        with self.slots_lock:
            mine = self.open_by_client.get(client, 0)
            if (
                sum(self.open_by_client.values()) >= self.max_connections
                or mine >= self.max_per_client
            ):
                return False
            self.open_by_client[client] = mine + 1
            return True

    def _release(self, client: str) -> None:
        with self.slots_lock:
            left = self.open_by_client.get(client, 1) - 1
            if left > 0:
                self.open_by_client[client] = left
            else:
                self.open_by_client.pop(client, None)

    def process_request(self, request: Any, client_address: Any) -> None:
        client = str(client_address[0])
        if not self._claim(client):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release(client)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release(str(client_address[0]))


def issue_tokens(agents: list[str], tokens_dir: Path) -> dict[str, str]:
    """Write a token into each seat's token directory and return them. A
    token already there (the service restarted) is kept, so the seats'
    tokens stay valid."""
    tokens = {}
    for agent in agents:
        seat_dir = tokens_dir / agent
        if not seat_dir.is_dir():
            raise SystemExit(
                f"coop_server: no token directory for {agent} at {seat_dir}"
            )
        token_path = seat_dir / "token"
        try:
            existing = token_path.read_text().strip()
        except OSError:
            existing = ""
        if len(existing) == 32 and all(c in "0123456789abcdef" for c in existing):
            tokens[agent] = existing
            continue
        tokens[agent] = secrets.token_hex(16)
        partial = seat_dir / ".token.partial"
        partial.write_text(tokens[agent] + "\n")
        partial.chmod(0o644)
        os.replace(partial, token_path)
    return tokens


def make_server(
    agents: list[str],
    tokens_dir: Path,
    log_dir: Path,
    host: str = "0.0.0.0",
    port: int = 8080,
    **limits: int,
) -> Server:
    """The service, not yet serving. ``limits`` may set max_messages,
    max_bytes, max_events, max_connections and max_per_client (the tests make
    them small)."""
    max_connections = limits.pop("max_connections", MAX_CONNECTIONS)
    max_per_client = limits.pop("max_per_client", MAX_CONNECTIONS_PER_CLIENT)
    board = Board(agents, issue_tokens(agents, tokens_dir), log_dir, **limits)
    return Server((host, port), make_handler(board), max_connections, max_per_client)


def main() -> None:
    agents = [
        a.strip() for a in os.environ.get("COOP_AGENTS", "").split(",") if a.strip()
    ]
    if len(agents) < 2:
        raise SystemExit("coop_server: set COOP_AGENTS to at least two seat names")
    server = make_server(
        agents,
        Path(os.environ.get("COOP_TOKENS_DIR", "/run/cooperbench/tokens")),
        Path(os.environ.get("COOP_LOG_DIR", "/logs/cooperbench")),
        port=int(os.environ.get("COOP_PORT", "8080")),
    )
    print(
        f"coop_server: serving {agents} on port {server.server_address[1]}",
        file=sys.stderr,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
