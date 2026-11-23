"""The Flotilla world: authenticated seats, condition-gated messages, one shared answer slot.

It runs in the `main` service of a seat task. Seats reach it at http://world:8080. The world derives
every caller's seat from its bearer token, so a seat cannot act or speak as another seat. Every
request is written to /logs/world/events.jsonl, which is the evidence the analysis reads; the one
exception is the Compose health check, which `main` sends to itself every few seconds.
Standard library only. See flotilla/CONTRACT.md, sections 3 to 5.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import secrets
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
# Version 5 (review of fork PR #18, world half): the world freezes itself FLOTILLA_PLAY_SECONDS after the
# first seat call (a seat process that outlives play cannot act); messages after a freeze get 409; the admin
# token works on /admin/ only; JSON numbers must be finite; the world refuses a config that lets a request
# act as the wrong seat; message ids are random (a counter showed the number of hidden direct messages).
# Version 6: bound submission history and sample unattributed errors; record HTTP parser refusals.
# Version 7: rounds games can allow a pass as an explicit action, separate from a missed round.
# Version 8: an optional round protocol (extra POST paths, /state fields, close-round log fields).
# Version 9: standing offers. Optional hooks, called only if the protocol defines them:
# note_look(seat, time) on GET /state, settle_standing(next_stock, ended, round) before the round
# log line, and drain_events() for offer fates. A round still closes when every seat has acted
# or the round's time ends.
# Version 10: a pending proposal holds the round open until its timer ends.
# Version 11: the `gated` condition (talk with a read gate): a game action by a seat with messages that no reply
# has shown it is not applied; the world shows those messages in a 409 (with the round of the refused action) and
# the seat sends the action again. A message counts as delivered (GET /messages or the 409) only after its reply was
# written, outside LOCK; v10 recorded GET delivery before the write. Every action event carries `unread`. A round
# protocol may define check() (a refusal without side effects), run before the gate. In split, POST /decline takes
# an optional `counter` proposal, applied in the same locked step.
WORLD_VERSION = 11
SERVER = "world"  # the whole Server header: the host name that seats already use, no name and no version
LOGS = Path(os.environ.get("FLOTILLA_LOGS", "/logs/world"))
CONDITION = os.environ.get("FLOTILLA_CONDITION", "talk")
SEATS = [s for s in os.environ.get("FLOTILLA_SEATS", "p1,p2").split(",") if s]
_TOKEN_PAIRS = [pair.split(":", 1) for pair in os.environ.get("FLOTILLA_TOKENS", "").split(",") if pair]
TOKENS = {pair[1]: pair[0] for pair in _TOKEN_PAIRS if len(pair) == 2}
PLAY_SECONDS = float(os.environ.get("FLOTILLA_PLAY_SECONDS") or 0)  # 0: no deadline of the world's own
ADMIN = os.environ.get("FLOTILLA_ADMIN", "")
READ_GATE = CONDITION == "gated"  # talk, and the world holds a game action until the seat has read its messages
MESSAGING = CONDITION.startswith("talk") or READ_GATE  # talk, talk-liar, talk-warned, talk-liar-warned, gated
REPLY_WRITE_SECONDS = 5.0  # a reply that shows messages must be written within this time, or nothing is delivered
DEFAULT_MAX_TEXT = 4000
DEFAULT_MAX_MESSAGES = 200
MAX_TEXT = int(os.environ.get("FLOTILLA_MAX_TEXT") or DEFAULT_MAX_TEXT)  # characters per message
MAX_MESSAGES_PER_SEAT = int(os.environ.get("FLOTILLA_MAX_MESSAGES") or DEFAULT_MAX_MESSAGES)
MAX_BODY = max(64_000, 8 * MAX_TEXT)  # bytes; a message at the text limit always fits
MAX_SUBMIT_ATTEMPTS = 100
ANSWER_EVENT_BYTES = 1024
UNATTRIBUTED_EVENT_LIMIT = 8
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
GAME_ACTION = getattr(GAME, "ACTION_PATH", "/harvest")  # a game names its action: /harvest, /claim
GAME_ALLOWS_PASS = getattr(GAME, "ALLOW_PASS", False)
_protocol_factory = getattr(GAME, "round_protocol", None)
ROUND_PROTOCOL = _protocol_factory(CONDITION, SEATS) if _protocol_factory is not None else None
ROUND_KEYS = getattr(GAME, "PUBLIC_ROUND_KEYS",  # what a seat sees of each closed round
                     ("round", "stock", "requests", "catch", "missed", "remaining", "collapsed"))
if GAME is not None:
    ROUTES = {path: methods for path, methods in ROUTES.items() if path != "/submit"} | {
        "/state": ("GET",), GAME_ACTION: ("POST",), "/wait": ("GET",)}
if ROUND_PROTOCOL is not None:
    ROUTES.update(dict.fromkeys(ROUND_PROTOCOL.paths, ("POST",)))

START = time.monotonic()
LOCK = threading.Lock()
_PROTOCOL_ACTORS: set[str] = set()
STATE: dict[str, Any] = {
    "messages": [],  # {"id", "from", "to", "text", "t"}
    "submissions": [],  # {"n", "seat", "answer", "t"}
    "frozen": False,
    "sent": {seat: 0 for seat in SEATS},
    "submit_attempts": dict.fromkeys(SEATS, 0),
    "bad_token_requests": 0,
    "invalid_http_requests": 0,
    "first_call": None,  # world time of the first authorized seat request
    "delivered": {seat: {} for seat in SEATS},  # message id -> world time a reply first showed it to the seat
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


def record_unattributed(kind: str, data: dict[str, Any]) -> None:
    """Count every request but write only a fixed sample of each kind."""
    with LOCK:
        counter = f"{kind}_requests"
        STATE[counter] += 1
        if STATE[counter] <= UNATTRIBUTED_EVENT_LIMIT:
            record(kind, None, data)


def answer_evidence(answer: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    data = {"answer_sha256": hashlib.sha256(encoded).hexdigest(), "answer_bytes": len(encoded)}
    if len(encoded) <= ANSWER_EVENT_BYTES:
        return {**data, "answer": answer}
    return {**data, "answer_prefix": encoded[:ANSWER_EVENT_BYTES].decode("ascii")}


def limits() -> dict[str, int]:
    return {"max_text": MAX_TEXT, "max_messages": MAX_MESSAGES_PER_SEAT}


def whoami(seat: str) -> dict[str, Any]:
    """What a seat may know about the run. Never the condition's name: it would tell a seat, for
    example, that one of its teammates lies."""
    return {"seat": seat, "seats": SEATS, "messaging": MESSAGING, "limits": limits()}


def config_problems() -> list[str]:
    """Settings that would let a request act as the wrong seat (review of fork PR #18, M5)."""
    tokens = [pair[1] for pair in _TOKEN_PAIRS if len(pair) == 2]
    problems = [
        "a seat is named admin" if "admin" in SEATS else "",
        "a FLOTILLA_TOKENS entry is not seat:token" if any(len(pair) != 2 for pair in _TOKEN_PAIRS) else "",
        "a seat token is empty" if any(not token for token in tokens) else "",
        "two seats share a token" if len(set(tokens)) != len(tokens) else "",
        "no admin token" if not ADMIN else "",
        "the admin token is also a seat token" if ADMIN and ADMIN in tokens else "",
        "a token names a seat that is not in FLOTILLA_SEATS" if set(TOKENS.values()) - set(SEATS) else "",
    ]
    return [problem for problem in problems if problem]


def expire() -> None:
    """Freeze the world PLAY_SECONDS after the first seat call, so that a seat process that outlives play
    cannot change the result before the verifier freezes it (review of fork PR #18, S1). Callers hold LOCK."""
    first = STATE["first_call"]
    if PLAY_SECONDS and not STATE["frozen"] and first is not None and now() >= first + PLAY_SECONDS:
        STATE["frozen"] = True
        record("freeze", None, {"reason": "play time is over", "after_s": PLAY_SECONDS})


def tick(at: float | None = None) -> None:
    """Start the season when every seat has called once (or START_GRACE after the first call), and close
    every round whose time is up. Lazy: each request calls it, so no thread runs the clock. Callers hold LOCK."""
    game = STATE["game"]
    if STATE["frozen"] or game["over"]:
        return
    t = now() if at is None else at
    if game["opened"] is None:
        first, everyone = game["first_seen"], len(game["seen"]) == len(SEATS)
        if first is None or (not everyone and t < first + START_GRACE):
            return
        game["opened"] = t if everyone else first + START_GRACE
        record("open", None, {"round": game["round"], "stock": game["stock"]})
    while not game["over"] and t >= game["opened"] + ROUND_SECONDS:
        close_round(game["opened"] + ROUND_SECONDS)


def _drain_protocol(seat: str | None) -> None:
    """Write offer events the protocol queued. Callers hold LOCK. No-op when the hook is absent."""
    if ROUND_PROTOCOL is None:
        return
    drain = getattr(ROUND_PROTOCOL, "drain_events", None)
    if drain is None:
        return
    for item in drain():
        record(item["kind"], seat, item["data"])


def _note_look(seat: str) -> None:
    """The first GET /state by another seat after a proposal was posted. Callers hold LOCK."""
    if ROUND_PROTOCOL is None:
        return
    note = getattr(ROUND_PROTOCOL, "note_look", None)
    if note is not None:
        note(seat, now())
    _drain_protocol(seat)


def _unread(seat: str) -> list[dict[str, Any]]:
    """Messages from another seat, to this seat or to all, that no reply has shown this seat. Callers hold LOCK."""
    delivered = STATE["delivered"][seat]
    return [m for m in STATE["messages"]
            if m["from"] != seat and m["to"] in ("all", seat) and m["id"] not in delivered]


def _deliver(seat: str, ids: list[str], via: str = "") -> None:
    """Record the delivery of messages whose reply was written, then mark them as shown to the seat. The event is
    appended first: if the log write raises, as in every record() call, the request fails and the messages stay
    unread, so the in-memory state never holds a delivery that the log lacks. Callers hold LOCK."""
    at = now()
    record("deliver", seat, {"ids": ids} | ({"via": via} if via else {}))
    delivered = STATE["delivered"][seat]
    for message_id in ids:
        delivered.setdefault(message_id, at)


def gate_error(number: int) -> str:
    return (f"not recorded in round {number}: messages reached you that you have not read; they are below; read "
            "them and send your action again")


def _gate(seat: str, path: str, action: dict[str, Any]) -> dict[str, Any] | None:
    """The 409 reply that holds a game action in the gated condition, or None: apply the action. The caller holds
    LOCK from this check through the action, so no message arrives between them. The messages stay unread until
    the caller has written this reply (World._deliver_reply)."""
    if not READ_GATE:
        return None
    pending = _unread(seat)
    if not pending:
        return None
    number = STATE["game"]["round"]
    ids = [m["id"] for m in pending]
    record("gated", seat, {"round": number, "path": path, "ids": ids, "action": action, "unread": len(ids)})
    return {"error": gate_error(number), "round": number, "messages": pending}


def _close_if_ready(at: float | None = None) -> None:
    """Close the round when every seat has acted and no proposal holds it. Callers hold LOCK."""
    if len(STATE["game"]["requests"]) != len(SEATS):
        return
    if ROUND_PROTOCOL is not None and ROUND_PROTOCOL.blocks_early_close():
        return
    close_round(now() if at is None else at)


def _record_round_action(path: str, seat: str, number: int, reply: dict[str, Any]) -> None:
    """A decline with a counter is two events: `decline` names the counter, `propose` names the declined ID."""
    unread = len(_unread(seat))
    counter = reply.get("counter") if path == "/decline" else None
    if counter is None:
        record(path.removeprefix("/"), seat, {"round": number, **reply, "unread": unread})
        return
    record("decline", seat, {"round": number, **reply, "counter": counter["id"], "unread": unread})
    record("propose", seat, {"round": number, **counter, "counter_to": reply["id"], "unread": unread})


def _round_step(path: str, seat: str, body: dict[str, Any], at: float) -> tuple[int, dict[str, Any], bool]:
    """One round-protocol action in an open round: the protocol's refusal, then the gate, then the action.
    Returns (status, reply, held by the gate). Callers hold LOCK."""
    assert ROUND_PROTOCOL is not None
    game = STATE["game"]
    number, stock = game["round"], game["stock"]
    check = getattr(ROUND_PROTOCOL, "check", None)
    refusal = check(path, seat, body, stock, number) if check is not None else None
    held = _gate(seat, path, body) if refusal is None else None
    if held is not None:
        return 409, held, True
    status, reply, agreement = refusal or ROUND_PROTOCOL.act(path, seat, body, stock, number, at)
    if status == 201:
        _PROTOCOL_ACTORS.add(seat)
        _record_round_action(path, seat, number, reply)
    else:
        record("invalid" if status == 400 else "denied", seat,
               {"method": "POST", "path": path, "status": status, "reason": reply["error"]})
    _drain_protocol(seat)
    if agreement is not None:
        close_round(at, agreement=agreement)
    elif status == 201:
        _close_if_ready(at)
    return status, reply, False


def close_round(at: float, *, agreement: dict[str, int] | None = None) -> None:
    """Settle the open round; a seat that did not act catches 0 and is `missed`. Callers hold LOCK."""
    game = STATE["game"]
    actions = game["requests"] if agreement is None else agreement
    requests = {seat: actions.get(seat) or 0 for seat in SEATS}
    missed = [seat for seat in SEATS if seat not in actions]
    inactive = [seat for seat in SEATS if seat not in game["requests"] and seat not in _PROTOCOL_ACTORS]
    _PROTOCOL_ACTORS.clear()
    pass_data = {}
    if GAME_ALLOWS_PASS:
        pass_data["passed"] = [seat for seat in SEATS if seat in actions and actions[seat] is None]
    result = GAME.settle(game["stock"], requests, PARAMS, missed=missed, **pass_data)
    ended = bool(result["collapsed"] or result.get("ends") or game["round"] >= PARAMS["rounds"])
    extra: dict[str, Any] = {}
    if ROUND_PROTOCOL is not None:
        settle = getattr(ROUND_PROTOCOL, "settle_standing", None)
        if settle is not None:
            settle(result["next_stock"], ended, game["round"])
        _drain_protocol(None)
        extra = ROUND_PROTOCOL.close_round()
    game["log"].append({"round": game["round"], "missed": missed, "inactive": inactive,
                        "opened": game["opened"], "closed": round(at, 3), **pass_data, **result, **extra})
    record("close", None, {"round": game["round"], "missed": missed, "inactive": inactive,
                           "collapsed": result["collapsed"], **pass_data, **extra})
    if ended:  # a deal, a collapse, or the deadline
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
        "closed_rounds": [{key: entry[key] for key in ROUND_KEYS} for entry in game["log"]],
        **(ROUND_PROTOCOL.view() if ROUND_PROTOCOL is not None else {}),
    }


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # 1e999 parses as inf; the logs must stay standard JSON
        raise ValueError(f"{text} is not a finite number")
    return value


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not standard JSON")  # NaN and Infinity would make the logs non-standard


class World(BaseHTTPRequestHandler):
    def version_string(self) -> str:
        """The Server header of every reply, error pages of the server library included."""
        return SERVER

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # The HTTP parser can reject a request before it has headers or an authenticated seat.
        record_unattributed("invalid_http", {"status": code})
        super().send_error(code, message, explain)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except ConnectionError:
            self.close_connection = True

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

    def _deliver_reply(self, status: int, body: dict[str, Any], seat: str, via: str = "") -> None:
        """Write a reply that shows messages, outside LOCK and within REPLY_WRITE_SECONDS; the messages from other
        seats count as delivered only once the write succeeded. Until then they stay unread, so an action that
        arrives meanwhile is held again, which is harmless. On a failed write nothing is recorded."""
        ids = [m["id"] for m in body["messages"] if m["from"] != seat]
        connection = self.connection
        previous = connection.gettimeout()
        try:
            connection.settimeout(REPLY_WRITE_SECONDS)
            self._reply(status, body)
        except OSError:  # a reset, a broken pipe or the timeout
            self.close_connection = True
            return
        finally:
            try:
                connection.settimeout(previous)
            except OSError:
                self.close_connection = True
        with LOCK:
            _deliver(seat, ids, via)

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
            raw = self.rfile.read(length)
        except (ConnectionError, TimeoutError):
            return None, "incomplete body"
        if len(raw) != length:
            return None, "incomplete body"
        try:
            body = json.loads(raw or b"{}", parse_constant=_no_constants, parse_float=_finite)
        except (ValueError, RecursionError):  # includes JSONDecodeError and UnicodeDecodeError
            return None, "not JSON"
        return (body, "") if isinstance(body, dict) else (None, "not a JSON object")

    def _authorize(self) -> str | None:
        seat = self._caller()
        if seat is None:
            record_unattributed("bad_token", {"path": self.path[:128]})
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
        if seat == "admin" and not path.startswith("/admin/"):  # the verifier's token is not a seat
            with LOCK:
                record("denied", "admin", {"path": path, "reason": "the admin token is for /admin/ only"})
            self._reply(403, {"error": "the admin token is for /admin/ only"})
            return
        if seat != "admin":
            with LOCK:
                STATE["first_call"] = now() if STATE["first_call"] is None else STATE["first_call"]
                expire()
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
                    _note_look(seat)
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
        if path == "/submit" and not self._claim_submit(seat):
            return
        body, reason = self._body()
        if body is None:
            self._invalid(seat, method, path, 400, reason, f"send a JSON object (at most {MAX_BODY} bytes)")
        elif path == "/messages":
            self._send(seat, body)
        elif path == "/submit":
            self._submit(seat, body)
        elif path == GAME_ACTION:
            self._harvest(seat, body)
        elif ROUND_PROTOCOL is not None and path in ROUND_PROTOCOL.paths:
            self._round_action(path, seat, body)
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
            if STATE["frozen"]:  # review of fork PR #18, M2: a frozen world takes no more evidence
                record("late", seat, {"path": "/messages", "chars": len(text)})
                self._reply(409, {"error": "play has ended; the world is frozen"})
                return
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
            # A random id: a global counter told a third seat how many direct messages it did not see
            # (review of fork PR #18, M1). The order is in "t" and in the logs.
            message = {"id": f"m-{secrets.token_hex(6)}", "from": seat, "to": to, "text": text, "t": now()}
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
        self._deliver_reply(200, {"messages": visible}, seat)

    def _claim_submit(self, seat: str) -> bool:
        with LOCK:
            STATE["submit_attempts"][seat] += 1
            count = STATE["submit_attempts"][seat]
            if count <= MAX_SUBMIT_ATTEMPTS:
                return True
            frozen = STATE["frozen"]
            if count == MAX_SUBMIT_ATTEMPTS + 1:
                record("denied", seat, {"path": "/submit", "reason": "submit limit"})
        if frozen:
            self._reply(409, {"error": "play has ended; the answer is frozen"})
        else:
            self._reply(429, {"error": f"limit of {MAX_SUBMIT_ATTEMPTS} submit attempts per seat reached"})
        return False

    def _submit(self, seat: str, body: dict[str, Any]) -> None:
        answer = body.get("answer")
        if not isinstance(answer, dict):
            self._invalid(seat, "POST", "/submit", 400, "answer is not an object",
                          'send {"answer": {...}} with a JSON object as the answer')
            return
        evidence = answer_evidence(answer)
        with LOCK:
            if STATE["frozen"]:
                record("late", seat, {"path": "/submit", **evidence})
                self._reply(409, {"error": "play has ended; the answer is frozen"})
                return
            n = len(STATE["submissions"]) + 1
            STATE["submissions"].append({"n": n, "seat": seat, "answer": answer, "t": now()})
            record("submit", seat, {"n": n, **evidence})
        # The reply must not say how many submissions exist: in `silent` that count is a channel.
        self._reply(200, {"accepted": True})

    def _harvest(self, seat: str, body: dict[str, Any]) -> None:
        passing = GAME_ALLOWS_PASS and "pass" in body
        amount = body.get("amount")
        if passing:
            if body["pass"] is not True or len(body) != 1:
                self._invalid(seat, "POST", GAME_ACTION, 400, "bad pass", 'send {"pass": true} by itself')
                return
        elif isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 10**6:
            self._invalid(seat, "POST", GAME_ACTION, 400, "bad amount",
                          'send {"amount": <a whole number from 0 to 1000000>}')
            return
        action = {"pass": True} if passing else {"amount": amount}
        with LOCK:
            tick()
            game = STATE["game"]
            if STATE["frozen"] or game["over"]:
                record("late", seat, action)
                self._reply(409, {"error": "the season is over"})
                return
            if game["opened"] is None:
                record("early", seat, action)
                self._reply(409, {"error": "the season has not started; it starts when every fisher has called once"})
                return
            if seat in game["requests"]:
                record("repeat", seat, {"round": game["round"], **action})
                self._reply(409, {"error": "you already requested in this round"})
                return
            held = _gate(seat, GAME_ACTION, action)
            if held is None:
                game["requests"][seat] = None if passing else amount
                number = game["round"]
                record("pass" if passing else "request", seat,
                       {"round": number, **action, "unread": len(_unread(seat))})
                _close_if_ready()
        if held is not None:
            self._deliver_reply(409, held, seat, "gate")
            return
        # The reply must not say how many seats have requested: in `silent` that count is a channel.
        self._reply(201, {"round": number, **action})

    def _round_action(self, path: str, seat: str, body: dict[str, Any]) -> None:
        assert ROUND_PROTOCOL is not None
        with LOCK:
            expire()
            at = now()
            tick(at)
            game = STATE["game"]
            if STATE["frozen"] or game["over"]:
                record("late", seat, {"path": path})
                self._reply(409, {"error": "the season is over"})
                return
            if game["opened"] is None:
                record("early", seat, {"path": path})
                self._reply(409, {"error": "the season has not started; it starts when every player has called once"})
                return
            status, reply, held = _round_step(path, seat, body, at)
        if held:
            self._deliver_reply(status, reply, seat, "gate")
        else:
            self._reply(status, reply)

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
                    _note_look(seat)
                    view = game_view(seat)
                    break
            time.sleep(0.1)
        self._reply(200, view)


def serve(host: str = "0.0.0.0", port: int = 8080) -> ThreadingHTTPServer:
    """Bind the server, open the logs and record the start event; the caller runs serve_forever()."""
    if problems := config_problems():
        raise SystemExit(f"world config refused: {'; '.join(problems)}")
    server = ThreadingHTTPServer((host, port), World)
    LOGS.mkdir(parents=True, exist_ok=True)
    for name in ("events.jsonl", "messages.jsonl"):
        (LOGS / name).touch()
    with LOCK:
        record("start", None, {"condition": CONDITION, "seats": SEATS, "world_version": WORLD_VERSION,
                               "limits": limits()} | ({"game": PARAMS.get("family"), "round_seconds": ROUND_SECONDS,
                                                      "max_messages_per_round": MAX_MESSAGES_PER_ROUND,
                                                      "read_gate": READ_GATE}
                                                     if GAME is not None else {}))
    return server


def main() -> None:
    serve().serve_forever()


if __name__ == "__main__":
    main()
