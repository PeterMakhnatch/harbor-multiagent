import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socket import socket
from typing import cast, override

from agentsnet_protocol import COLORS, Observation, ReceivedMessage, is_message_map

SEATS = ("n0", "n1", "n2", "n3")
MESSAGE_ROUNDS = 4
FINAL_ROUND = MESSAGE_ROUNDS + 1
FIXTURE_SHA256 = "381d38758384e2e49c1f788d567faad89ecd8cdd8238b5e3e032fe1476cfd138"
FIXTURE_PATH = Path(__file__).with_name("ba4-row.json")
STATE_PATH = Path("/logs/world/agentsnet.json")
AUDIT_PATH = STATE_PATH.with_name("agentsnet-audit.jsonl")
MAX_BODY = 32 * 1024
MAX_STATE = 2 * 1024 * 1024
REQUEST_TIMEOUT = 3.0
AUDIT_LIMIT = 16
PRIVATE_ROUTES = {"GET": {"observation"}, "POST": {"messages", "answer"}}
CROSS_ROLE_FIELDS = {"seat", "name", "role"}
CAPABILITY_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def released_graph() -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    fixture = FIXTURE_PATH.read_bytes()
    if hashlib.sha256(fixture).hexdigest() != FIXTURE_SHA256:
        raise ValueError("Released row fixture digest mismatch")
    graph = json.loads(json.loads(fixture)["row"]["graph"])["graph"]
    names = tuple(node["name"] for node in graph["nodes"])
    edges = tuple((link["source"], link["target"]) for link in graph["links"])
    return names, edges


def seat_capabilities() -> dict[str, str]:
    tokens = {
        os.environ.get(f"AGENTSNET_{seat.upper()}_CAPABILITY", ""): seat
        for seat in SEATS
    }
    if len(tokens) != len(SEATS) or any(
        not CAPABILITY_PATTERN.fullmatch(token) for token in tokens
    ):
        raise ValueError("World requires four distinct task capabilities")
    return tokens


class ColoringWorld:
    def __init__(self) -> None:
        self.capabilities = seat_capabilities()
        names, edges = released_graph()
        self.names = dict(zip(SEATS, names, strict=True))
        self.neighbors: dict[str, list[str]] = {seat: [] for seat in SEATS}
        for first, second in edges:
            self.neighbors[SEATS[first]].append(SEATS[second])
            self.neighbors[SEATS[second]].append(SEATS[first])
        self.messages: list[dict[str, dict[str, str]]] = [
            {} for _ in range(MESSAGE_ROUNDS)
        ]
        self.answers: dict[str, str | None] = {}
        self.round = 1
        self.audit_counts: dict[tuple[str, str], int] = {}
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text("", encoding="utf-8")
        self.persist()

    @property
    def phase(self) -> str:
        if self.round < FINAL_ROUND:
            return "messages"
        return "done" if len(self.answers) == len(SEATS) else "final"

    def persist(self) -> None:
        snapshot = {
            "fixture_sha256": FIXTURE_SHA256,
            "round": self.round,
            "phase": self.phase,
            "messages": self.messages,
            "answers": self.answers,
        }
        temp = STATE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        temp.replace(STATE_PATH)

    def observation(self, seat: str) -> Observation:
        name = self.names[seat]
        received: list[ReceivedMessage] = []
        if self.round > 1:
            previous = self.messages[self.round - 2]
            received = [
                ReceivedMessage(
                    name=self.names[neighbor], message=previous[neighbor].get(name)
                )
                for neighbor in self.neighbors[seat]
            ]
        submissions = (
            self.answers if self.round == FINAL_ROUND else self.messages[self.round - 1]
        )
        return {
            "seat": seat,
            "name": name,
            "neighbors": [self.names[neighbor] for neighbor in self.neighbors[seat]],
            "num_agents": len(SEATS),
            "num_rounds": MESSAGE_ROUNDS,
            "num_colors": len(COLORS),
            "round": self.round,
            "phase": self.phase,
            "submitted": seat in submissions,
            "received": received,
        }

    def submit(
        self, route: str, seat: str, payload: dict[str, object]
    ) -> tuple[int, str] | None:
        field = "messages" if route == "messages" else "answer"
        if payload.keys() != {"round", field} or type(payload["round"]) is not int:
            return 400, "invalid_payload"
        if payload["round"] != self.round:
            return 409, "stale_round"
        if route != ("answer" if self.round == FINAL_ROUND else "messages"):
            return 409, "wrong_phase"
        value = payload[field]
        if route == "messages":
            outboxes = self.messages[self.round - 1]
            if seat in outboxes:
                return 409, "duplicate_submission"
            if not is_message_map(value):
                return 400, "invalid_messages"
            outboxes[seat] = value
            if len(outboxes) == len(SEATS):
                self.round += 1
        else:
            if seat in self.answers:
                return 409, "duplicate_submission"
            if value is not None and not isinstance(value, str):
                return 400, "invalid_answer"
            self.answers[seat] = value
        self.persist()
        return None

    def audit(self, route: str, seat: str | None, status: int, reason: str) -> None:
        key = (route, reason)
        count = self.audit_counts.get(key, 0)
        if count == AUDIT_LIMIT:
            return
        self.audit_counts[key] = count + 1
        record = {
            "operation": route,
            "seat": seat,
            "status": status,
            "reason": reason,
            "round": self.round,
            "phase": self.phase,
        }
        with AUDIT_PATH.open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(record) + "\n")


class WorldHandler(BaseHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def reply(self, status: int, body: Mapping[str, object]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode(
            "utf-8", errors="backslashreplace"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_payload(self) -> tuple[dict[str, object] | None, tuple[int, str] | None]:
        lengths = self.headers.get_all("Content-Length") or []
        if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1:
            return None, (400, "invalid_length")
        length = lengths[0]
        if not length.isascii() or not length.isdecimal() or len(length) > 10:
            return None, (400, "invalid_length")
        size = int(length)
        if size > MAX_BODY:
            return None, (413, "body_too_large")
        deadline = time.monotonic() + REQUEST_TIMEOUT
        data = bytearray()
        try:
            while len(data) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, (408, "body_timeout")
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(size - len(data))
                if not chunk:
                    return None, (400, "invalid_length")
                data.extend(chunk)
            payload: object = json.loads(data.decode("utf-8"))
        except TimeoutError:
            return None, (408, "body_timeout")
        except (ValueError, RecursionError):
            return None, (400, "invalid_json")
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) for key in payload
        ):
            return None, (400, "invalid_payload")
        return cast(dict[str, object], payload), None

    def dispatch(self, method: str) -> None:
        path, query_delimiter, _ = self.path.partition("?")
        route, path_delimiter, _ = path.removeprefix("/").partition("/")
        if method == "GET" and self.path == "/health":
            self.reply(200, {"ok": True})
            return
        if not path.startswith("/") or route not in PRIVATE_ROUTES[method]:
            self.reply(404, {"error": "not_found"})
            return
        server = self.server
        if not isinstance(server, WorldServer):
            raise TypeError("World requests must use WorldServer")
        world = server.world
        auth = self.headers.get_all("Authorization") or []
        seat = None
        if len(auth) == 1 and auth[0].startswith("Bearer "):
            seat = world.capabilities.get(auth[0][len("Bearer ") :])
        if seat is None:
            denial = (401, "unauthorized")
        elif path_delimiter or query_delimiter:
            denial = (403, "cross_role")
        elif method == "GET":
            self.reply(200, world.observation(seat))
            return
        elif world.phase == "done":
            denial = (409, "terminal")
        else:
            payload, denial = self.read_payload()
            if payload is not None:
                if CROSS_ROLE_FIELDS.intersection(payload):
                    denial = (403, "cross_role")
                else:
                    denial = world.submit(route, seat, payload)
            if denial is None:
                self.reply(200, world.observation(seat))
                return
        status, reason = denial
        world.audit(route, seat, status, reason)
        self.reply(status, {"error": reason})


class WorldServer(HTTPServer):
    request_queue_size = 16

    def __init__(self, world: ColoringWorld) -> None:
        self.world = world
        super().__init__(("0.0.0.0", 8080), WorldHandler)

    @override
    def get_request(self) -> tuple[socket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.settimeout(REQUEST_TIMEOUT)
        return connection, address


if __name__ == "__main__":
    with WorldServer(ColoringWorld()) as server:
        server.serve_forever()
