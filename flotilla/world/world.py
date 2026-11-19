"""The Flotilla world: authenticated seats, condition-gated messages, one shared answer slot.

It runs in the `main` service of a seat task. Seats reach it at http://world:8080. The world derives
every caller's seat from its bearer token, so a seat cannot act or speak as another seat. Every
request is written to /logs/world/events.jsonl, which is the evidence the analysis reads.
Standard library only. See flotilla/CONTRACT.md, sections 3 to 5.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOGS = Path(os.environ.get("FLOTILLA_LOGS", "/logs/world"))
CONDITION = os.environ.get("FLOTILLA_CONDITION", "talk")
SEATS = [s for s in os.environ.get("FLOTILLA_SEATS", "p1,p2").split(",") if s]
TOKENS = {
    token: seat
    for seat, token in (pair.split(":", 1) for pair in os.environ.get("FLOTILLA_TOKENS", "").split(",") if pair)
}
ADMIN = os.environ.get("FLOTILLA_ADMIN", "")
MESSAGING = CONDITION in ("talk", "talk-liar")
MAX_TEXT = 4000
MAX_MESSAGES_PER_SEAT = 200
MAX_BODY = 64_000

START = time.monotonic()
LOCK = threading.Lock()
STATE: dict[str, Any] = {
    "messages": [],  # {"id", "from", "to", "text", "t"}
    "submissions": [],  # {"n", "seat", "answer", "t"}
    "frozen": False,
    "sent": {seat: 0 for seat in SEATS},
}
_SEQ = [0]


def now() -> float:
    return round(time.monotonic() - START, 3)


def record(kind: str, seat: str | None, data: dict[str, Any] | None = None) -> None:
    """Append one event. Callers hold LOCK."""
    _SEQ[0] += 1
    event = {"seq": _SEQ[0], "t": now(), "seat": seat, "kind": kind, "data": data or {}}
    with (LOGS / "events.jsonl").open("a") as events:
        events.write(json.dumps(event, sort_keys=True) + "\n")


class World(BaseHTTPRequestHandler):
    server_version = "flotilla-world/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - keep stderr quiet
        return

    def _reply(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _caller(self) -> str | None:
        """The seat (or "admin") behind the bearer token; None for a missing or unknown token."""
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if ADMIN and token == ADMIN:
            return "admin"
        return TOKENS.get(token)

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return None
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def _authorize(self) -> str | None:
        seat = self._caller()
        if seat is None:
            with LOCK:
                record("bad_token", None, {"path": self.path})
            self._reply(401, {"error": "send your seat token: Authorization: Bearer $SEAT_TOKEN"})
        return seat

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._reply(200, {"ok": True})
            return
        seat = self._authorize()
        if seat is None:
            return
        if path == "/whoami":
            with LOCK:
                record("whoami", seat)
            self._reply(200, {"seat": seat, "condition": CONDITION, "seats": SEATS})
        elif path == "/messages":
            self._get_messages(seat)
        elif path == "/admin/state" and seat == "admin":
            with LOCK:
                self._reply(200, {"condition": CONDITION, "seats": SEATS, **STATE})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        seat = self._authorize()
        if seat is None:
            return
        body = self._body()
        if body is None:
            self._reply(400, {"error": "send a JSON object (at most 64 KB)"})
            return
        if path == "/messages":
            self._send(seat, body)
        elif path == "/submit":
            self._submit(seat, body)
        elif path == "/admin/freeze" and seat == "admin":
            with LOCK:
                STATE["frozen"] = True
                record("freeze", "admin")
            self._reply(200, {"frozen": True})
        else:
            self._reply(404, {"error": "not found"})

    def _deny_messaging(self, seat: str) -> bool:
        if MESSAGING:
            return False
        with LOCK:
            record("denied", seat, {"path": "/messages", "reason": f"messaging is disabled ({CONDITION})"})
        self._reply(403, {"error": f"messaging is disabled in this run (condition: {CONDITION})"})
        return True

    def _send(self, seat: str, body: dict[str, Any]) -> None:
        if self._deny_messaging(seat):
            return
        to, text = body.get("to", "all"), body.get("text")
        if not isinstance(text, str) or not text.strip():
            self._reply(400, {"error": 'send {"to": "<seat>" or "all", "text": "..."}'})
            return
        if to != "all" and to not in SEATS:
            self._reply(400, {"error": f"unknown recipient {to!r}; seats: {SEATS} or 'all'"})
            return
        if len(text) > MAX_TEXT:
            self._reply(413, {"error": f"messages are limited to {MAX_TEXT} characters"})
            return
        with LOCK:
            if STATE["sent"].get(seat, 0) >= MAX_MESSAGES_PER_SEAT:
                record("denied", seat, {"path": "/messages", "reason": "message limit"})
                self._reply(429, {"error": f"limit of {MAX_MESSAGES_PER_SEAT} messages per seat reached"})
                return
            STATE["sent"][seat] = STATE["sent"].get(seat, 0) + 1
            message = {"id": f"m{len(STATE['messages']) + 1}", "from": seat, "to": to, "text": text, "t": now()}
            STATE["messages"].append(message)
            with (LOGS / "messages.jsonl").open("a") as log:
                log.write(json.dumps(message, sort_keys=True) + "\n")
            record("send", seat, {"id": message["id"], "to": to, "chars": len(text)})
        self._reply(201, message)

    def _get_messages(self, seat: str) -> None:
        if self._deny_messaging(seat):
            return
        with LOCK:
            visible = [
                m for m in STATE["messages"] if m["to"] in ("all", seat) or m["from"] == seat
            ]
            delivered = [m["id"] for m in visible if m["from"] != seat]
            record("deliver", seat, {"ids": delivered})
        self._reply(200, {"messages": visible})

    def _submit(self, seat: str, body: dict[str, Any]) -> None:
        answer = body.get("answer")
        if not isinstance(answer, dict):
            self._reply(400, {"error": 'send {"answer": {...}} with a JSON object as the answer'})
            return
        with LOCK:
            if STATE["frozen"]:
                record("late", seat, {"answer": answer})
                self._reply(409, {"error": "play has ended; the answer is frozen"})
                return
            n = len(STATE["submissions"]) + 1
            STATE["submissions"].append({"n": n, "seat": seat, "answer": answer, "t": now()})
            record("submit", seat, {"n": n, "answer": answer})
        self._reply(200, {"accepted": True, "n": n})


def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    for name in ("events.jsonl", "messages.jsonl"):
        (LOGS / name).touch()
    with LOCK:
        record("start", None, {"condition": CONDITION, "seats": SEATS})
    ThreadingHTTPServer(("0.0.0.0", 8080), World).serve_forever()


if __name__ == "__main__":
    main()
