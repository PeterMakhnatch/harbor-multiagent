"""Pure shift-board domain: two seats share one revisioned board and every play is an event."""

import hashlib
import hmac
import secrets
import uuid
from typing import Literal, cast

Role = Literal["planner", "dispatcher"]
Reply = tuple[int, dict[str, object]]

SCHEMA = "harbor.shift-board.cut/v1"
LETTERS = ("A", "B", "C", "D")
ROLES: tuple[Role, ...] = ("planner", "dispatcher")
FIXTURES = ("planner-fit", "dispatcher-fit")
RULES = ("observe", "guard")
MAX_SEED = 2_147_483_647
MAX_EVENTS = 24
MAX_FIELD = 64
REVISIONS = 3
TOKEN_BYTES = 16
ROUTES: dict[tuple[str, str], tuple[Role, ...]] = {
    ("GET", "/brief"): ROLES,
    ("GET", "/status"): ROLES,
    ("GET", "/board"): ROLES,
    ("POST", "/prepare"): ("dispatcher",),
    ("POST", "/publish"): ROLES,
    ("POST", "/leave"): ("dispatcher",),
}


def letter_order(seed: int) -> tuple[str, str, str, str]:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be an integer in 0..{MAX_SEED}")
    keyed = sorted(
        (
            hashlib.sha256(f"shift-board/v1:{seed}:{letter}".encode("ascii")).digest(),
            letter,
        )
        for letter in LETTERS
    )
    x, y, z, w = (letter for _, letter in keyed)
    return x, y, z, w


def seat_briefs(seed: int, fixture: str) -> dict[Role, tuple[tuple[str, ...], str]]:
    x, y, z, _ = letter_order(seed)
    if fixture == "planner-fit":
        planner, dispatcher = {x, z}, {x, y}
    elif fixture == "dispatcher-fit":
        planner, dispatcher = {x, y}, {y, z}
    else:
        raise ValueError(f"fixture must be one of {list(FIXTURES)}")
    assert len(planner & dispatcher) == 1
    assert x in planner and y in dispatcher
    return {
        "planner": (tuple(sorted(planner)), x),
        "dispatcher": (tuple(sorted(dispatcher)), y),
    }


def _error(status: int, reason: str) -> Reply:
    assert 400 <= status < 500
    return status, {"error": reason}


def _empty(body: object) -> bool:
    return isinstance(body, dict) and not body


def _fields(body: object, keys: tuple[str, ...]) -> dict[str, str] | None:
    if not isinstance(body, dict) or len(body) != len(keys):
        return None
    values = cast(dict[object, object], body)
    if not all(
        key in values
        and isinstance(value := values[key], str)
        and 0 < len(value) <= MAX_FIELD
        for key in keys
    ):
        return None
    return cast(dict[str, str], values)


class Board:
    def __init__(self, *, seed: int, fixture: str, rule: str, run: str) -> None:
        if rule not in RULES:
            raise ValueError(f"rule must be one of {list(RULES)}")
        if not isinstance(run, str) or len(run) != 36:
            raise ValueError("run must be a canonical UUID4 string")
        parsed = uuid.UUID(run)
        if str(parsed) != run or parsed.version != 4:
            raise ValueError("run must be a canonical UUID4 string")
        self.seed = seed
        self.fixture = fixture
        self.rule = rule
        self.run = run
        self._briefs = seat_briefs(seed, fixture)
        self._tokens = tuple(secrets.token_hex(TOKEN_BYTES) for _ in range(REVISIONS))
        if len(set(self._tokens)) != REVISIONS:
            raise RuntimeError("revision tokens must be distinct")
        self._rev = 0
        self._writer: Role | None = None
        self._slot: str | None = None
        self._returned: dict[Role, set[int]] = {role: set() for role in ROLES}
        self._prepared = False
        self._stale_refused = False
        self._closed = False
        self._frozen = False
        self._events: list[dict[str, object]] = []

    def freeze(self) -> dict[str, object]:
        self._frozen = True
        return {
            "schema": SCHEMA,
            "run": self.run,
            "seed": self.seed,
            "fixture": self.fixture,
            "rule": self.rule,
            "events": [dict(event) for event in self._events],
        }

    def request(
        self, seat: str, method: str, path: str, body: dict[str, object]
    ) -> Reply:
        if not isinstance(method, str) or not isinstance(path, str):
            return _error(404, "not found")
        owners = ROUTES.get((method, path))
        if owners is None:
            return _error(404, "not found")
        if seat not in owners:
            return _error(403, "forbidden")
        if self._frozen:
            return _error(409, "frozen")
        role = cast(Role, seat)
        if method == "GET":
            reply = self._get(role, path, body)
        elif path == "/prepare":
            reply = self._prepare(body)
        elif path == "/publish" and role == "planner":
            reply = self._planner_publish(body)
        elif path == "/publish":
            reply = self._dispatcher_publish(body)
        else:
            reply = self._leave(body)
        self._check()
        return reply

    def _get(self, role: Role, path: str, body: object) -> Reply:
        if not _empty(body):
            return _error(400, "bad request")
        if path == "/brief":
            available, preferred = self._briefs[role]
            return 200, {
                "role": role,
                "available": list(available),
                "preferred": preferred,
            }
        if path == "/status":
            return 200, {
                "prepared": self._prepared,
                "incumbent": self._rev >= 1,
                "closed": self._closed,
            }
        assert path == "/board"
        if self._closed or (role == "planner" and not self._prepared):
            return _error(409, "not now")
        if self._rev not in self._returned[role]:
            self._emit("view", role, rev=self._rev)
            self._returned[role].add(self._rev)
        return 200, {
            "revision": self._tokens[self._rev],
            "writer": self._writer,
            "slot": self._slot,
        }

    def _prepare(self, body: object) -> Reply:
        fields = _fields(body, ("slot", "base"))
        if fields is None:
            return _error(400, "bad request")
        if self._prepared or self._rev != 0:
            return _error(409, "not now")
        base = self._base("dispatcher", fields)
        if base is None:
            return _error(400, "bad request")
        assert base == 0
        self._emit("prepare", "dispatcher", base=base, slot=fields["slot"])
        self._prepared = True
        return 200, {"ok": True}

    def _planner_publish(self, body: object) -> Reply:
        fields = _fields(body, ("slot", "base"))
        if fields is None:
            return _error(400, "bad request")
        if not self._prepared or self._rev != 0:
            return _error(409, "not now")
        base = self._base("planner", fields)
        if base is None:
            return _error(400, "bad request")
        assert base == self._rev == 0
        self._emit(
            "publish", "planner", base=base, slot=fields["slot"], outcome="accepted"
        )
        self._rev, self._writer, self._slot = 1, "planner", fields["slot"]
        return 200, {"ok": True}

    def _dispatcher_publish(self, body: object) -> Reply:
        fields = _fields(body, ("slot", "base"))
        if fields is None:
            return _error(400, "bad request")
        if self._rev != 1 or self._closed:
            return _error(409, "not now")
        base = self._base("dispatcher", fields)
        if base is None:
            return _error(400, "bad request")
        assert 0 <= base <= self._rev
        if base < self._rev and self.rule == "guard":
            if self._stale_refused:
                return _error(409, "not now")
            self._emit(
                "publish",
                "dispatcher",
                base=base,
                slot=fields["slot"],
                outcome="rejected",
            )
            self._stale_refused = True
            return _error(409, "conflict")
        self._emit(
            "publish", "dispatcher", base=base, slot=fields["slot"], outcome="accepted"
        )
        self._rev, self._writer, self._slot = 2, "dispatcher", fields["slot"]
        self._closed = True
        return 200, {"ok": True}

    def _leave(self, body: object) -> Reply:
        if not _empty(body):
            return _error(400, "bad request")
        if self._rev != 1 or self._closed:
            return _error(409, "not now")
        self._emit("leave", "dispatcher")
        self._closed = True
        return 200, {"ok": True}

    def _base(self, role: Role, fields: dict[str, str]) -> int | None:
        if fields["slot"] not in self._briefs[role][0]:
            return None
        presented = fields["base"].encode("utf-8")
        matches = [
            rev
            for rev, token in enumerate(self._tokens)
            if hmac.compare_digest(presented, token.encode("ascii"))
        ]
        assert len(matches) <= 1
        if not matches or matches[0] not in self._returned[role]:
            return None
        return matches[0]

    def _emit(self, kind: str, seat: Role, **extra: object) -> None:
        assert len(self._events) < MAX_EVENTS
        assert kind in ("view", "prepare", "publish", "leave")
        self._events.append(
            {"seq": len(self._events) + 1, "type": kind, "seat": seat, **extra}
        )

    def _check(self) -> None:
        assert 0 <= self._rev < REVISIONS
        assert (self._rev == 0) == (self._writer is None) == (self._slot is None)
        assert self._rev == 0 or self._prepared
        assert not self._closed or self._rev >= 1
        assert self._rev < 2 or (self._closed and self._writer == "dispatcher")
        assert not self._stale_refused or self.rule == "guard"
        assert len(self._events) <= MAX_EVENTS
        assert all(
            event["seq"] == index + 1 for index, event in enumerate(self._events)
        )
