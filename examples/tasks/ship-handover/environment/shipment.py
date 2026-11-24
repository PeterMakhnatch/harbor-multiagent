"""Pure ship-handover domain: staged prior dispatch, optional handoff, one relief decision."""

import copy
import uuid
from dataclasses import dataclass
from typing import Literal, cast

Role = Literal["outgoing", "relief"]
Identity = Literal["original", "draft", "fresh"]
Kind = Literal["handoff", "inbox", "resolve"]
Reply = tuple[int, dict[str, object]]

SCHEMA = "harbor.ship-handover.cut/v1"
ROLES: tuple[Role, ...] = ("outgoing", "relief")
PRIORS = ("committed", "dropped")
CHANNELS = ("deliver", "withhold")
KINDS: tuple[Kind, ...] = ("handoff", "inbox", "resolve")
IDENTITIES: tuple[Identity, ...] = ("original", "draft", "fresh")
SERVED_CHOICES = ("original", "draft", "none")
RESOLVE_CHOICES = ("close", "fresh", "original", "draft")
ACTIONS = ("close", "fresh", "resume")
REF_LENGTH = 32
REF_ALPHABET = frozenset("0123456789abcdef")
RECEIPT = "unconfirmed"
ROUTE_ROLES: dict[tuple[str, str], tuple[Role, ...]] = {
    ("GET", "/brief"): ROLES,
    ("GET", "/status"): ROLES,
    ("GET", "/inbox"): ("relief",),
    ("POST", "/handoff"): ("outgoing",),
    ("POST", "/resolve"): ("relief",),
}
ROUTES = tuple(ROUTE_ROLES)
ERRORS = {
    400: ("bad request", "unknown reference"),
    403: ("forbidden",),
    404: ("not found",),
    409: ("frozen", "already handed off", "already resolved"),
}


def is_ref(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == REF_LENGTH
        and set(value) <= REF_ALPHABET
    )


def _error(status: int, reason: str) -> Reply:
    assert status in ERRORS and reason in ERRORS[status]
    return status, {"error": reason}


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    kind: Kind
    choice: str

    def __post_init__(self) -> None:
        assert 1 <= self.seq <= len(KINDS)
        assert self.kind in KINDS
        allowed = RESOLVE_CHOICES if self.kind == "resolve" else SERVED_CHOICES
        assert self.choice in allowed

    def record(self) -> dict[str, object]:
        return {"seq": self.seq, "kind": self.kind, "choice": self.choice}


class Ledger:
    def __init__(self, prior: str) -> None:
        assert prior in PRIORS
        self._entries: list[Identity] = ["original"] if prior == "committed" else []

    def accept(self, identity: Identity) -> None:
        assert identity in IDENTITIES
        if identity not in self._entries:
            self._entries.append(identity)
        assert len(set(self._entries)) == len(self._entries) <= len(IDENTITIES)

    def contains(self, identity: Identity) -> bool:
        return identity in self._entries

    def size(self) -> int:
        return len(self._entries)

    def snapshot(self) -> list[str]:
        return list(self._entries)


class Shipment:
    def __init__(
        self, prior: str, channel: str, run: str, request_ref: str, draft_ref: str
    ) -> None:
        if prior not in PRIORS:
            raise ValueError(f"prior must be one of {list(PRIORS)}")
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {list(CHANNELS)}")
        if not isinstance(run, str) or str(uuid.UUID(run)) != run:
            raise ValueError("run must be a canonical UUID string")
        if not is_ref(request_ref) or not is_ref(draft_ref):
            raise ValueError("references must be 32 lowercase hex characters")
        if request_ref == draft_ref:
            raise ValueError("references must be distinct")
        self.prior = prior
        self.channel = channel
        self.run = run
        self._refs: dict[str, Identity] = {request_ref: "original", draft_ref: "draft"}
        self._request_ref = request_ref
        self._draft_ref = draft_ref
        self._handoff: Identity | None = None
        self._handed_off = False
        self._inbox_recorded = False
        self._resolved = False
        self._ledger = Ledger(prior)
        self._trace: list[Event] = []
        self._cut: dict[str, object] | None = None

    @property
    def frozen(self) -> bool:
        return self._cut is not None

    def freeze(self) -> dict[str, object]:
        if self._cut is None:
            self._cut = {
                "schema": SCHEMA,
                "run": self.run,
                "prior": self.prior,
                "channel": self.channel,
                "events": [event.record() for event in self._trace],
                "effects": self._ledger.snapshot(),
            }
        self._check()
        return copy.deepcopy(self._cut)

    def handle(self, role: str, method: str, path: str, body: object) -> Reply:
        allowed = ROUTE_ROLES.get((method, path))
        if allowed is None:
            return _error(404, "not found")
        if role not in allowed:
            return _error(403, "forbidden")
        seat = cast(Role, role)
        if method == "GET":
            reply = self._read(seat, path, body)
        else:
            reply = self._write(path, body)
        self._check()
        return reply

    def _read(self, role: Role, path: str, body: object) -> Reply:
        if not isinstance(body, dict) or body:
            return _error(400, "bad request")
        if path == "/brief":
            if role == "relief":
                return 200, {"role": "relief", "receipt": RECEIPT}
            return 200, {
                "role": "outgoing",
                "receipt": RECEIPT,
                "request_ref": self._request_ref,
                "draft_ref": self._draft_ref,
            }
        if path == "/status":
            return 200, {
                "handoff_ready": self._handed_off,
                "relief_sealed": self._resolved,
            }
        assert path == "/inbox" and role == "relief"
        return self._inbox()

    def _inbox(self) -> Reply:
        if not self._handed_off:
            return 200, {"ready": False, "reference": None}
        served = self._handoff if self.channel == "deliver" else None
        if not self._inbox_recorded:
            if self.frozen:
                return _error(409, "frozen")
            self._inbox_recorded = True
            self._emit("inbox", served or "none")
        return 200, {"ready": True, "reference": self._ref_of(served)}

    def _write(self, path: str, body: object) -> Reply:
        if path == "/handoff":
            return self._post_handoff(body)
        assert path == "/resolve"
        return self._post_resolve(body)

    def _post_handoff(self, body: object) -> Reply:
        if not isinstance(body, dict) or set(body) != {"reference"}:
            return _error(400, "bad request")
        reference = cast(dict[str, object], body)["reference"]
        identity: Identity | None = None
        if reference is not None:
            if not is_ref(reference):
                return _error(400, "bad request")
            if reference not in self._refs:
                return _error(400, "unknown reference")
            identity = self._refs[cast(str, reference)]
        if self.frozen:
            return _error(409, "frozen")
        if self._handed_off:
            return _error(409, "already handed off")
        self._handed_off = True
        self._handoff = identity
        self._emit("handoff", identity or "none")
        return 200, {"ok": True}

    def _post_resolve(self, body: object) -> Reply:
        parsed = self._parse_resolve(body)
        if isinstance(parsed, tuple):
            return parsed
        if self.frozen:
            return _error(409, "frozen")
        if self._resolved:
            return _error(409, "already resolved")
        self._resolved = True
        if parsed in IDENTITIES:
            self._ledger.accept(cast(Identity, parsed))
        self._emit("resolve", parsed)
        return 200, {"ok": True}

    def _parse_resolve(self, body: object) -> str | Reply:
        if not isinstance(body, dict):
            return _error(400, "bad request")
        fields = cast(dict[str, object], body)
        action = fields.get("action")
        if action in ("close", "fresh") and set(fields) == {"action"}:
            return cast(str, action)
        if action != "resume" or set(fields) != {"action", "reference"}:
            return _error(400, "bad request")
        reference = fields["reference"]
        if not is_ref(reference):
            return _error(400, "bad request")
        if reference not in self._refs:
            return _error(400, "unknown reference")
        return self._refs[cast(str, reference)]

    def _ref_of(self, identity: Identity | None) -> str | None:
        if identity is None:
            return None
        assert identity in ("original", "draft")
        return self._request_ref if identity == "original" else self._draft_ref

    def _emit(self, kind: Kind, choice: str) -> None:
        assert not self.frozen
        assert all(event.kind != kind for event in self._trace)
        self._trace.append(Event(seq=len(self._trace) + 1, kind=kind, choice=choice))

    def _check(self) -> None:
        kinds = [event.kind for event in self._trace]
        assert len(kinds) == len(set(kinds)) <= len(KINDS)
        assert all(event.seq == index + 1 for index, event in enumerate(self._trace))
        assert ("handoff" in kinds) == self._handed_off
        assert ("inbox" in kinds) == self._inbox_recorded
        assert ("resolve" in kinds) == self._resolved
        assert not self._inbox_recorded or self._handed_off
        assert self._handed_off or self._handoff is None
        assert self._ledger.contains("original") or self.prior == "dropped"
        assert not self._ledger.contains("fresh") or self._resolved
        assert self._ledger.size() <= (1 if self.prior == "committed" else 0) + int(
            self._resolved
        )
        assert set(self._refs.values()) == {"original", "draft"}
        if self._cut is not None:
            assert len(cast(list[object], self._cut["events"])) == len(self._trace)
            assert cast(list[object], self._cut["effects"]) == self._ledger.snapshot()
