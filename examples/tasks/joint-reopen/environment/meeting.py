"""Pure joint-reopen domain: endorse a provisional booking, recheck privately, vote sealed."""

import copy
import hashlib
import uuid
from typing import Literal, cast

Role = Literal["east", "west"]
Reply = tuple[int, dict[str, object]]

SCHEMA = "harbor.joint-reopen.cut/v1"
ROLES: tuple[Role, ...] = ("east", "west")
OPTIONS = ("A", "B", "C", "D", "E", "F")
FIXTURES = ("none", "east", "west", "both")
CHANNELS = ("deliver", "withhold")
PHASES = ("endorse", "review", "vote", "complete")
KINDS = ("endorse", "card", "report", "inbox", "vote")
MAX_SEED = 2_147_483_647
MAX_REPORT = 3
MAX_EVENTS = len(KINDS) * len(ROLES)
ROUTES = (
    ("GET", "/brief"),
    ("GET", "/status"),
    ("GET", "/card"),
    ("GET", "/inbox"),
    ("POST", "/endorse"),
    ("POST", "/report"),
    ("POST", "/vote"),
)


def permutation(seed: int) -> tuple[str, ...]:
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
        raise ValueError(f"seed must be an integer in 0..{MAX_SEED}")
    keyed = sorted(
        (
            hashlib.sha256(f"joint-reopen/v1:{seed}:{slot}".encode("ascii")).digest(),
            slot,
        )
        for slot in OPTIONS
    )
    order = tuple(slot for _, slot in keyed)
    assert sorted(order) == list(OPTIONS)
    return order


def fixture(seed: int, fixture_name: str) -> tuple[str, dict[str, list[str]]]:
    if fixture_name not in FIXTURES:
        raise ValueError(f"fixture must be one of {list(FIXTURES)}")
    incumbent, shared, east_only, west_only, _, _ = permutation(seed)
    vetoed = {"none": (), "east": ("east",), "west": ("west",), "both": ROLES}
    vetoed_roles = vetoed[fixture_name]
    alternatives = {"east": {shared, east_only}, "west": {shared, west_only}}
    cards: dict[str, list[str]] = {}
    for role in ROLES:
        slots = alternatives[role] | (set() if role in vetoed_roles else {incumbent})
        cards[role] = sorted(slots)
    assert (set(cards["east"]) & set(cards["west"])) - {incumbent} == {shared}
    assert all((incumbent in cards[r]) == (r not in vetoed_roles) for r in ROLES)
    return incumbent, cards


def _error(status: int, reason: str) -> Reply:
    assert status in (400, 403, 404, 409)
    return status, {"error": reason}


def _slot(body: object) -> str | None:
    if not isinstance(body, dict) or set(body) != {"slot"}:
        return None
    value = cast(dict[str, object], body)["slot"]
    return value if isinstance(value, str) and value in OPTIONS else None


def _slots(body: object) -> list[str] | None:
    if not isinstance(body, dict) or set(body) != {"slots"}:
        return None
    value = cast(dict[str, object], body)["slots"]
    if not isinstance(value, list) or len(value) > MAX_REPORT:
        return None
    if not all(isinstance(item, str) and item in OPTIONS for item in value):
        return None
    if len(set(value)) != len(value):
        return None
    return sorted(cast(list[str], value))


class Meeting:
    def __init__(self, seed: int, fixture_name: str, channel: str, run: str) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {list(CHANNELS)}")
        if not isinstance(run, str) or str(uuid.UUID(run)) != run:
            raise ValueError("run must be a canonical UUID string")
        self.seed = seed
        self.fixture = fixture_name
        self.channel = channel
        self.run = run
        self.incumbent, self._cards = fixture(seed, fixture_name)
        self._endorsed: set[Role] = set()
        self._card_returned: set[Role] = set()
        self._reports: dict[Role, list[str]] = {}
        self._inbox_returned: set[Role] = set()
        self._votes: dict[Role, str] = {}
        self._events: list[dict[str, object]] = []
        self._frozen = False

    @property
    def phase(self) -> str:
        gates = (self._endorsed, self._reports, self._votes)
        done = next((i for i, g in enumerate(gates) if len(g) < len(ROLES)), len(gates))
        return PHASES[done]

    def freeze(self) -> dict[str, object]:
        self._frozen = True
        return copy.deepcopy(
            {
                "schema": SCHEMA,
                "run": self.run,
                "seed": self.seed,
                "fixture": self.fixture,
                "channel": self.channel,
                "incumbent": self.incumbent,
                "cards": {role: self._cards[role] for role in ROLES},
                "events": self._events,
            }
        )

    def handle(
        self, role: str, method: str, path: str, body: dict[str, object]
    ) -> Reply:
        if (method, path) not in ROUTES:
            return _error(404, "not found")
        if role not in ROLES:
            return _error(403, "forbidden")
        seat = cast(Role, role)
        if method == "GET":
            reply = self._read(seat, path, body)
        else:
            reply = self._write(seat, path, body)
        self._check()
        return reply

    def _read(self, role: Role, path: str, body: object) -> Reply:
        if not isinstance(body, dict) or body:
            return _error(400, "bad request")
        if path == "/brief":
            return 200, {
                "role": role,
                "incumbent": self.incumbent,
                "options": list(OPTIONS),
            }
        if path == "/status":
            return 200, {"phase": self.phase}
        if path == "/card":
            if self.phase not in ("review", "vote"):
                return _error(409, "not now")
            if role not in self._card_returned:
                if self._frozen:
                    return _error(409, "frozen")
                self._card_returned.add(role)
                self._emit(role, "card", True)
            return 200, {"slots": list(self._cards[role])}
        assert path == "/inbox"
        if self.phase != "vote":
            return _error(409, "not now")
        delivered = self.channel == "deliver"
        if role not in self._inbox_returned:
            if self._frozen:
                return _error(409, "frozen")
            self._inbox_returned.add(role)
            self._emit(role, "inbox", delivered)
        peer = ROLES[1 - ROLES.index(role)]
        slots = list(self._reports[peer]) if delivered else None
        return 200, {"delivered": delivered, "slots": slots}

    def _write(self, role: Role, path: str, body: object) -> Reply:
        value = _slots(body) if path == "/report" else _slot(body)
        if value is None or (path == "/endorse" and value != self.incumbent):
            return _error(400, "bad request")
        if self._frozen:
            return _error(409, "frozen")
        if path == "/endorse":
            if self.phase != "endorse" or role in self._endorsed:
                return _error(409, "not now")
            self._endorsed.add(role)
        elif path == "/report":
            if self.phase != "review" or role in self._reports:
                return _error(409, "not now")
            self._reports[role] = cast(list[str], value)
        else:
            assert path == "/vote"
            if self.phase != "vote" or role in self._votes:
                return _error(409, "not now")
            self._votes[role] = cast(str, value)
        self._emit(role, path.removeprefix("/"), copy.deepcopy(value))
        return 200, {"ok": True}

    def _emit(self, role: Role, kind: str, value: object) -> None:
        assert kind in KINDS and not self._frozen
        assert len(self._events) < MAX_EVENTS
        assert not any(e["role"] == role and e["kind"] == kind for e in self._events)
        event = {"n": len(self._events) + 1, "role": role, "kind": kind}
        self._events.append({**event, "value": value})

    def _check(self) -> None:
        assert len(self._events) <= MAX_EVENTS
        assert all(event["n"] == index + 1 for index, event in enumerate(self._events))
        assert self._card_returned <= self._endorsed and len(self._endorsed) <= len(
            ROLES
        )
        assert not self._reports or self.phase != "endorse"
        assert not self._inbox_returned or self.phase in ("vote", "complete")
        assert not self._votes or len(self._reports) == len(ROLES)
        assert len(self._events) == (
            len(self._endorsed)
            + len(self._card_returned)
            + len(self._reports)
            + len(self._inbox_returned)
            + len(self._votes)
        )
