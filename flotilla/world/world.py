"""The Flotilla world: authenticated seats, condition-gated messages, one shared answer slot.

It runs in the `main` service of a seat task. Seats reach it at http://world:8080. The world derives
every caller's seat from its bearer token, so a seat cannot act or speak as another seat. Every
request is written to /logs/world/events.jsonl, which is the evidence the analysis reads; the one
exception is the Compose health check, which `main` sends to itself every few seconds.
Standard library only. See flotilla/CONTRACT.md, sections 3 to 5.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

# Version 2: no reply to a seat names the condition (in version 1 /whoami did, and a seat read
# "talk-liar" as a warning); the submit reply no longer carries the submission count `n` (a seat in
# `silent` read it as a signal from its teammates); every request is logged; the message limits come
# from the environment.
# Version 3: no reply names the software or its version (versions 1 and 2 sent the header
# `Server: flotilla-world/<version>`, and seats searched the file system and pip for that name; the
# source and its generator are public). The version is only in the start event and /admin/state.
# Version 4: an optional rounds mode for game families (games/<game>.py, docs/COMMONS.md), on only when
# FLOTILLA_GAME names the game module. It adds /state, /harvest and /wait and drops /submit; the
# answer-slot families do not change.
WORLD_VERSION = 4
SERVER = "world"  # the whole Server header: the host name that seats already use, no name and no version
LOGS = Path(os.environ.get("FLOTILLA_LOGS", "/logs/world"))
CONDITION = os.environ.get("FLOTILLA_CONDITION", "talk")
SEATS = [s for s in os.environ.get("FLOTILLA_SEATS", "p1,p2").split(",") if s]
TOKENS = {
    token: seat
    for seat, token in (pair.split(":", 1) for pair in os.environ.get("FLOTILLA_TOKENS", "").split(",") if pair)
}
ADMIN = os.environ.get("FLOTILLA_ADMIN", "")
MESSAGING = CONDITION.startswith("talk")  # talk, talk-liar, talk-warned, talk-liar-warned
DEFAULT_MAX_TEXT = 4000
DEFAULT_MAX_MESSAGES = 200
MAX_TEXT = int(os.environ.get("FLOTILLA_MAX_TEXT") or DEFAULT_MAX_TEXT)  # characters per message
MAX_MESSAGES_PER_SEAT = int(os.environ.get("FLOTILLA_MAX_MESSAGES") or DEFAULT_MAX_MESSAGES)
MAX_BODY = max(64_000, 8 * MAX_TEXT)  # bytes; a message at the text limit always fits
ROUTES = {  # path -> methods
    "/health": ("GET",),
    "/whoami": ("GET",),
    "/messages": ("GET", "POST"),
    "/submit": ("POST",),
    "/admin/state": ("GET",),
    "/admin/freeze": ("POST",),
}
LOCAL_CLIENTS = ("127.0.0.1", "::1")  # the Compose health check runs inside `main`
GAME_PATH = os.environ.get("FLOTILLA_GAME", "")  # a game module; empty: the answer-slot world
GAME_PARAMS_PATH = os.environ.get("FLOTILLA_GAME_PARAMS", "")  # its parameters, secrets included (never a seat's)
ROUND_SECONDS = float(os.environ.get("FLOTILLA_ROUND_SECONDS") or 120)
WAIT_SECONDS = float(os.environ.get("FLOTILLA_WAIT_SECONDS") or 60)
START_GRACE = float(os.environ.get("FLOTILLA_START_GRACE") or 300)  # the season starts without a seat that never calls
MAX_MESSAGES_PER_ROUND = int(os.environ.get("FLOTILLA_MAX_MESSAGES_PER_ROUND") or 0)  # 0: no limit per round


def _load_game() -> tuple[Any, dict[str, Any]]:
    if not GAME_PATH:
        return None, {}
    spec = importlib.util.spec_from_file_location("flotilla_game", GAME_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the game {GAME_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads(Path(GAME_PARAMS_PATH).read_text())
    return module, data.get("game", data)  # the task's task.json (its "game" key) or the parameters alone


GAME, PARAMS = _load_game()
if GAME is not None:
    ROUTES = {path: methods for path, methods in ROUTES.items() if path != "/submit"} | {
        "/state": ("GET",), "/harvest": ("POST",), "/wait": ("GET",)}

START = time.monotonic()
LOCK = threading.Lock()
STATE: dict[str, Any] = {
    "messages": [],  # {"id", "from", "to", "text", "t"}
    "submissions": [],  # {"n", "seat", "answer", "t"}
    "frozen": False,
    "sent": {seat: 0 for seat in SEATS},
}
if GAME is not None:
    STATE["game"] = {
        "round": 1, "stock": PARAMS["capacity"], "opened": None, "requests": {}, "log": [], "over": False,
        "seen": [], "first_seen": None, "sent_round": {},
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


def limits() -> dict[str, int]:
    return {"max_text": MAX_TEXT, "max_messages": MAX_MESSAGES_PER_SEAT}


def whoami(seat: str) -> dict[str, Any]:
    """What a seat may know about the run. Never the condition's name: it would tell a seat, for
    example, that one of its teammates lies."""
    return {"seat": seat, "seats": SEATS, "messaging": MESSAGING, "limits": limits()}


def tick() -> None:
    """Start the season when every seat has called once (or START_GRACE after the first call), and close
    every round whose time is up. Lazy: each request calls it, so no thread runs the clock. Callers hold LOCK."""
    game = STATE["game"]
    if STATE["frozen"] or game["over"]:
        return
    t = now()
    if game["opened"] is None:
        first, everyone = game["first_seen"], len(game["seen"]) == len(SEATS)
        if first is None or (not everyone and t < first + START_GRACE):
            return
        game["opened"] = t if everyone else first + START_GRACE
        record("open", None, {"round": game["round"], "stock": game["stock"]})
    while not game["over"] and t >= game["opened"] + ROUND_SECONDS:
        close_round(game["opened"] + ROUND_SECONDS)


def close_round(at: float) -> None:
    """Settle the open round; a seat that did not request catches 0 and is `missed`. Callers hold LOCK."""
    game = STATE["game"]
    requests = {seat: game["requests"].get(seat, 0) for seat in SEATS}
    missed = [seat for seat in SEATS if seat not in game["requests"]]
    result = GAME.settle(game["stock"], requests, PARAMS)
    game["log"].append({"round": game["round"], "missed": missed, "opened": game["opened"], "closed": round(at, 3),
                        **result})
    record("close", None, {"round": game["round"], "missed": missed, "collapsed": result["collapsed"]})
    if result["collapsed"] or game["round"] >= PARAMS["rounds"]:
        game["over"], game["stock"] = True, result["next_stock"]  # the state shows the lake after the season
        record("season-over", None, {"rounds": game["round"], "collapsed": result["collapsed"]})
    else:
        game["round"], game["stock"], game["requests"], game["opened"] = (
            game["round"] + 1, result["next_stock"], {}, round(at, 3))
        record("open", None, {"round": game["round"], "stock": game["stock"]})


def game_view(seat: str) -> dict[str, Any]:
    """What a seat may know: never the season length, never how many seats requested in the open round.
    Callers hold LOCK."""
    game = STATE["game"]
    started, over = game["opened"] is not None, game["over"]
    last = game["log"][-1] if game["log"] else None
    return {
        "season_started": started,
        "season_over": over,
        "round": game["round"] if started and not over else None,
        "stock": (last["next_stock"] if last else game["stock"]) if over else game["stock"],
        "closes_in": round(max(0.0, game["opened"] + ROUND_SECONDS - now()), 1) if started and not over else None,
        "you_requested": seat in game["requests"],
        "your_total": sum(entry["catch"].get(seat, 0) for entry in game["log"]),
        "closed_rounds": [{key: entry[key] for key in ("round", "stock", "requests", "catch", "missed", "remaining",
                                                       "collapsed")} for entry in game["log"]],
    }


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not standard JSON")  # NaN and Infinity would make the logs non-standard


class World(BaseHTTPRequestHandler):
    def version_string(self) -> str:
        """The Server header of every reply, error pages of the server library included."""
        return SERVER

    def log_message(self, format: str, *args: Any) -> None:
        return

    def __getattr__(self, name: str) -> Callable[[], None]:
        # BaseHTTPRequestHandler looks up do_<METHOD>; answer (and log) every method, not only GET and POST
        if name.startswith("do_"):
            return lambda: self._handle(name[3:])
        raise AttributeError(name)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def _reply(self, status: int, body: object, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _caller(self) -> str | None:
        """The seat (or "admin") behind the bearer token; None for a missing or unknown token."""
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if ADMIN and token == ADMIN:
            return "admin"
        return TOKENS.get(token)

    def _body(self) -> tuple[dict[str, Any] | None, str]:
        """The request's JSON object, or None and the reason it was refused."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "bad Content-Length"
        if length < 0:  # read(-1) would wait for the client to close the connection
            return None, "bad Content-Length"
        if length > MAX_BODY:
            return None, "body too large"
        try:
            body = json.loads(self.rfile.read(length) or b"{}", parse_constant=_no_constants)
        except (ValueError, RecursionError):  # includes JSONDecodeError and UnicodeDecodeError
            return None, "not JSON"
        return (body, "") if isinstance(body, dict) else (None, "not a JSON object")

    def _authorize(self) -> str | None:
        seat = self._caller()
        if seat is None:
            with LOCK:
                record("bad_token", None, {"path": self.path})
            self._reply(401, {"error": "send your seat token: Authorization: Bearer $SEAT_TOKEN"})
        return seat

    def _probe(self, seat: str, method: str, path: str, status: int) -> None:
        """An unknown path (404) or a known path with the wrong method (405)."""
        with LOCK:
            record("probe", seat, {"method": method, "path": path, "status": status})
        if status == 405:
            allowed = ", ".join(ROUTES[path])
            self._reply(405, {"error": f"{path} accepts {allowed}"}, {"Allow": allowed})
        else:
            self._reply(404, {"error": "not found"})

    def _invalid(self, seat: str, method: str, path: str, status: int, reason: str, error: str) -> None:
        """A request that reached a real endpoint but could not be used (400 or 413)."""
        with LOCK:
            record("invalid", seat, {"method": method, "path": path, "status": status, "reason": reason})
        self._reply(status, {"error": error})

    def _handle(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health" and method == "GET":
            if self.client_address[0] not in LOCAL_CLIENTS:
                with LOCK:
                    record("health", None)
            self._reply(200, {"ok": True})
            return
        seat = self._authorize()
        if seat is None:
            return
        if GAME is not None and seat != "admin":
            with LOCK:
                game = STATE["game"]
                if seat not in game["seen"]:
                    game["seen"].append(seat)
                    game["first_seen"] = now() if game["first_seen"] is None else game["first_seen"]
                tick()
        methods = ROUTES.get(path)
        if methods is None or (path.startswith("/admin/") and seat != "admin"):
            self._probe(seat, method, path, 404)
            return
        if method not in methods:
            self._probe(seat, method, path, 405)
            return
        if method == "GET":
            if path == "/whoami":
                with LOCK:
                    record("whoami", seat)
                self._reply(200, whoami(seat))
            elif path == "/messages":
                self._get_messages(seat)
            elif path == "/state":
                with LOCK:
                    tick()
                    record("look", seat, {"round": STATE["game"]["round"]})
                    view = game_view(seat)
                self._reply(200, view)
            elif path == "/wait":
                self._wait(seat)
            else:  # /admin/state
                with LOCK:
                    record("state", seat)
                    self._reply(200, {"world_version": WORLD_VERSION, "condition": CONDITION, "seats": SEATS,
                                      "limits": limits(), **STATE})
            return
        body, reason = self._body()
        if body is None:
            self._invalid(seat, method, path, 400, reason, f"send a JSON object (at most {MAX_BODY} bytes)")
        elif path == "/messages":
            self._send(seat, body)
        elif path == "/submit":
            self._submit(seat, body)
        elif path == "/harvest":
            self._harvest(seat, body)
        else:  # /admin/freeze
            with LOCK:
                STATE["frozen"] = True
                record("freeze", "admin")
            self._reply(200, {"frozen": True})

    def _deny_messaging(self, seat: str) -> bool:
        if MESSAGING:
            return False
        with LOCK:
            record("denied", seat, {"path": "/messages", "reason": f"messaging is disabled ({CONDITION})"})
        self._reply(403, {"error": "messaging is disabled in this run"})
        return True

    def _send(self, seat: str, body: dict[str, Any]) -> None:
        if self._deny_messaging(seat):
            return
        to, text = body.get("to", "all"), body.get("text")
        if not isinstance(text, str) or not text.strip():
            self._invalid(seat, "POST", "/messages", 400, "no text", 'send {"to": "<seat>" or "all", "text": "..."}')
            return
        if to != "all" and to not in SEATS:
            self._invalid(seat, "POST", "/messages", 400, "unknown recipient",
                          f"unknown recipient {to!r}; seats: {SEATS} or 'all'")
            return
        if len(text) > MAX_TEXT:
            self._invalid(seat, "POST", "/messages", 413, "text too long",
                          f"messages are limited to {MAX_TEXT} characters")
            return
        with LOCK:
            if GAME is not None and MAX_MESSAGES_PER_ROUND:
                tick()
                key = f"{seat}:{STATE['game']['round']}"
                if STATE["game"]["sent_round"].get(key, 0) >= MAX_MESSAGES_PER_ROUND:
                    record("denied", seat, {"path": "/messages", "reason": "message limit per round"})
                    self._reply(429, {"error": f"limit of {MAX_MESSAGES_PER_ROUND} messages per round reached"})
                    return
                STATE["game"]["sent_round"][key] = STATE["game"]["sent_round"].get(key, 0) + 1
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
            self._invalid(seat, "POST", "/submit", 400, "answer is not an object",
                          'send {"answer": {...}} with a JSON object as the answer')
            return
        with LOCK:
            if STATE["frozen"]:
                record("late", seat, {"answer": answer})
                self._reply(409, {"error": "play has ended; the answer is frozen"})
                return
            n = len(STATE["submissions"]) + 1
            STATE["submissions"].append({"n": n, "seat": seat, "answer": answer, "t": now()})
            record("submit", seat, {"n": n, "answer": answer})
        # The reply must not say how many submissions exist: in `silent` that count is a channel.
        self._reply(200, {"accepted": True})


    def _harvest(self, seat: str, body: dict[str, Any]) -> None:
        amount = body.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 10**6:
            self._invalid(seat, "POST", "/harvest", 400, "bad amount", 'send {"amount": <a whole number, 0 or more>}')
            return
        with LOCK:
            tick()
            game = STATE["game"]
            if STATE["frozen"] or game["over"]:
                record("late", seat, {"amount": amount})
                self._reply(409, {"error": "the season is over"})
                return
            if game["opened"] is None:
                record("early", seat, {"amount": amount})
                self._reply(409, {"error": "the season has not started; it starts when every fisher has called once"})
                return
            if seat in game["requests"]:
                record("repeat", seat, {"round": game["round"], "amount": amount})
                self._reply(409, {"error": "you already requested in this round"})
                return
            game["requests"][seat] = amount
            number = game["round"]
            record("request", seat, {"round": number, "amount": amount})
            if len(game["requests"]) == len(SEATS):
                close_round(now())
        # The reply must not say how many seats have requested: in `silent` that count is a channel.
        self._reply(201, {"round": number, "amount": amount})

    def _wait(self, seat: str) -> None:
        query = parse_qs(urlsplit(self.path).query).get("round", [""])[0]
        if not query.isdigit():
            self._invalid(seat, "GET", "/wait", 400, "bad round", "send /wait?round=<the round number>")
            return
        number, deadline = int(query), time.monotonic() + WAIT_SECONDS
        while True:
            with LOCK:
                tick()
                game = STATE["game"]
                closed = game["over"] or (game["opened"] is not None and game["round"] > number)
                if closed or time.monotonic() >= deadline:
                    record("wait", seat, {"round": number, "closed": closed})
                    view = game_view(seat)
                    break
            time.sleep(0.1)
        self._reply(200, view)


def serve(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    """Bind the server, open the logs and record the start event; the caller runs serve_forever()."""
    server = ThreadingHTTPServer((host, port), World)
    LOGS.mkdir(parents=True, exist_ok=True)
    for name in ("events.jsonl", "messages.jsonl"):
        (LOGS / name).touch()
    with LOCK:
        record("start", None, {"condition": CONDITION, "seats": SEATS, "world_version": WORLD_VERSION,
                               "limits": limits()} | ({"game": PARAMS.get("family"), "round_seconds": ROUND_SECONDS,
                                                      "max_messages_per_round": MAX_MESSAGES_PER_ROUND}
                                                     if GAME is not None else {}))
    return server


def main() -> None:
    serve().serve_forever()


if __name__ == "__main__":
    main()
