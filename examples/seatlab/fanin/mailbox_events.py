import hashlib
from dataclasses import dataclass, field
from typing import Any, cast

from ..visibility import calendar_packet as packet
from ..wait_peer.receipt import HEX64, Bad, moment, need, same
from . import evidence, protocol

SCHEMA = "seatlab.fanin-mailbox-cut/v1"
SEATS = evidence.SEATS
REPORTERS = len(SEATS) - 1
REQUEST_CHARS = len(protocol.wire({"action": evidence.ACTIONS[0]}))
MAX_BYTES = 1 << 20
MAX_LINE = 4096
MAX_EVENTS = 8192
MAX_CLOCK = 253_402_300_800
EVENT_KEYS = frozenset(("seq", "kind", "seat", "t", "data"))
ADMIN = "admin"
ADMIN_KINDS = ("freeze", "state")
STEPS = ("request", "request_inbox", "reply", "reply_inbox")
Row = dict[str, Any]
Moment = tuple[int, int | float]


@dataclass
class Mailbox:
    booker: str
    reporters: tuple[str, ...]
    identified: set[str] = field(default_factory=set)
    inbox: dict[str, list[str]] = field(
        default_factory=lambda: {seat: [] for seat in SEATS}
    )
    seen: dict[str, set[str]] = field(
        default_factory=lambda: {seat: set() for seat in SEATS}
    )
    peer: dict[str, str] = field(default_factory=dict)
    edges: dict[str, dict[str, Moment | None]] = field(init=False)
    sent: int = 0
    requests: int = 0
    origin: int | float | None = None
    submit: Moment | None = None
    last_event: Moment | None = None
    frozen: bool = False

    def __post_init__(self) -> None:
        self.edges = {seat: dict.fromkeys(STEPS) for seat in self.reporters}


def roles(booker: object, reporters: object) -> tuple[str, tuple[str, ...]]:
    need(
        isinstance(booker, str)
        and type(reporters) is tuple
        and len(reporters) == REPORTERS
        and all(isinstance(seat, str) for seat in reporters)
        and sorted((booker, *reporters)) == sorted(SEATS),
        "mailbox_roles",
    )
    assert isinstance(booker, str) and isinstance(reporters, tuple)
    return booker, cast(tuple[str, ...], reporters)


def records(raw: bytes) -> list[Row]:
    need(raw and b"\r" not in raw, "events_framing")
    body = raw[:-1] if raw.endswith(b"\n") else raw
    parts = body.split(b"\n")
    need(len(parts) <= MAX_EVENTS, "events_count")
    events = []
    for part in parts:
        need(
            0 < len(part) <= MAX_LINE and part[:1] == b"{" and part[-1:] == b"}",
            "events_framing",
        )
        try:
            value = packet._json(part.decode("utf-8"))
        except (RecursionError, ValueError):
            raise Bad("events_json") from None
        need(isinstance(value, dict), "events_json")
        events.append(value)
    assert 0 < len(events) <= MAX_EVENTS
    return events


def envelope(event: Row, seq: int, previous: float) -> None:
    need(
        event.keys() == EVENT_KEYS
        and type(event["seq"]) is int
        and event["seq"] == seq
        and isinstance(event["kind"], str)
        and (event["seat"] is None or isinstance(event["seat"], str))
        and type(event["t"]) in (float, int)
        and 0 <= event["t"] < MAX_CLOCK
        and moment(event["t"])
        and event["t"] >= previous
        and isinstance(event["data"], dict),
        "world_event_sequence",
    )


def walk(events: list[Row], box: Mailbox) -> None:
    previous: int | float = 0
    for seq, event in enumerate(events, 1):
        envelope(event, seq, previous)
        kind, seat, t, data = event["kind"], event["seat"], event["t"], event["data"]
        if seq == 1:
            need(
                kind == "start" and seat is None and same(data, evidence.WORLD),
                "world_start",
            )
        elif kind == "health":
            need(seat is None and data == {}, "world_health")
        elif kind == "whoami":
            need(
                seat in SEATS and seat not in box.identified and data == {},
                "world_identity",
            )
            box.identified.add(seat)
        elif seat == ADMIN:
            need(kind in ADMIN_KINDS and data == {}, "world_admin_event")
            box.frozen = True
        else:
            need(seat in box.identified, "world_event_actor")
            action(box, kind, seat, (seq, t), data)
        box.last_event = (seq, t)
        previous = t


def action(box: Mailbox, kind: str, seat: str, at: Moment, data: Row) -> None:
    if kind == "send":
        send(box, seat, at, data)
    elif kind == "deliver":
        deliver(box, seat, at, data)
    elif kind == "submit":
        submit(box, seat, at, data)
    else:
        raise Bad("unexpected_world_event")


def send(box: Mailbox, seat: str, at: Moment, data: Row) -> None:
    need(box.submit is None and not box.frozen, "late_mutation")
    chars = data.get("chars")
    need(
        data.keys() == {"id", "to", "chars"}
        and data["id"] == f"m{box.sent + 1}"
        and type(chars) is int
        and 0 < chars <= evidence.MAX_TEXT,
        "send_binding",
    )
    to = data["to"]
    if seat == box.booker:
        need(
            box.requests < REPORTERS
            and to == box.reporters[box.requests]
            and chars == REQUEST_CHARS,
            "request_binding",
        )
        reporter, step = to, "request"
        box.requests += 1
        if box.origin is None:
            box.origin = at[1]
    else:
        need(seat in box.edges and to == box.booker, "reply_binding")
        reporter, step = seat, "reply"
        need(box.edges[seat]["request_inbox"] is not None, "reply_before_request")
        need(box.edges[seat]["reply"] is None, "duplicate_reply")
    assert box.edges[reporter][step] is None and box.origin is not None
    box.edges[reporter][step] = at
    box.inbox[to].append(data["id"])
    box.peer[data["id"]] = reporter
    box.sent += 1
    assert box.sent <= 2 * REPORTERS


def deliver(box: Mailbox, seat: str, at: Moment, data: Row) -> None:
    need(
        data.keys() == {"ids"}
        and type(data["ids"]) is list
        and data["ids"] == box.inbox[seat],
        "deliver_binding",
    )
    step = "reply_inbox" if seat == box.booker else "request_inbox"
    for mid in box.inbox[seat]:
        if mid in box.seen[seat]:
            continue
        box.seen[seat].add(mid)
        edge = box.edges[box.peer[mid]]
        assert edge[step] is None
        assert step == "reply_inbox" or box.peer[mid] == seat
        edge[step] = at


def submit(box: Mailbox, seat: str, at: Moment, data: Row) -> None:
    need(box.submit is None and not box.frozen, "late_mutation")
    need(
        seat == box.booker
        and all(edge["reply_inbox"] is not None for edge in box.edges.values()),
        "submit_before_gather",
    )
    answer = data.get("answer")
    need(
        data.keys() == {"n", "answer"}
        and type(data["n"]) is int
        and data["n"] == 1
        and isinstance(answer, dict)
        and answer in evidence.ANSWERS,
        "submit_event_binding",
    )
    box.submit = at


def point(at: Moment | None, origin: int | None) -> list[int] | None:
    if at is None:
        return None
    assert origin is not None
    elapsed = evidence.millis(at[1]) - origin
    need(0 <= elapsed <= 1 << 40, "timing_order")
    assert type(at[0]) is int and at[0] >= 1
    return [at[0], elapsed]


def cut(box: Mailbox, digest: str) -> Row:
    origin = None if box.origin is None else evidence.millis(box.origin)
    edges = []
    for seat in box.reporters:
        moments = [box.edges[seat][step] for step in STEPS]
        known = sum(m is not None for m in moments)
        assert all(m is not None for m in moments[:known])
        assert all(m is None for m in moments[known:])
        edge: Row = {"seat": seat}
        edge.update((step, point(m, origin)) for step, m in zip(STEPS, moments))
        edges.append(edge)
    return {
        "schema": SCHEMA,
        "events_sha256": digest,
        "booker": box.booker,
        "reporters": list(box.reporters),
        "edges": edges,
        "submit": point(box.submit, origin),
        "through": point(box.last_event, origin) if origin is not None else None,
    }


def project(
    raw: bytes, *, expected_sha256: str, booker: str, reporters: tuple[str, ...]
) -> Row:
    need(
        type(raw) is bytes
        and isinstance(expected_sha256, str)
        and HEX64.fullmatch(expected_sha256) is not None,
        "mailbox_arguments",
    )
    seat, order = roles(booker, reporters)
    need(len(raw) <= MAX_BYTES, "events_size")
    digest = hashlib.sha256(raw).hexdigest()
    need(digest == expected_sha256, "events_digest")
    box = Mailbox(booker=seat, reporters=order)
    walk(records(raw), box)
    result = cut(box, digest)
    assert len(result["edges"]) == REPORTERS
    return result
