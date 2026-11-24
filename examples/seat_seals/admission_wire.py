import json
import math
import re
from dataclasses import dataclass
from typing import Any

from ..native_study import DIGEST, as_choice, as_int, as_record, as_text, require
from ..seat_handoffs.core import reject_constant, unique_object
from ..shift_board.plan import TICKET
from ..ship_handover.study import INFLIGHT_TICKET
from ..ship_handover.study import TICKET as STOCK_TICKET
from .worker_layout import MAX_WORKERS

REQUEST_SCHEMA = "harbor.seat-seal.admission-request/v1"
REPLY_SCHEMA = "harbor.seat-seal.admission-reply/v1"
SHIPPING_REQUEST_SCHEMA = "harbor.shipping.admission-request/v1"
SHIPPING_REPLY_SCHEMA = "harbor.shipping.admission-reply/v1"
SHIPPING_TICKET = re.compile(
    f"(?:{STOCK_TICKET.pattern})|(?:{INFLIGHT_TICKET.pattern})"
)
GRANTED = "granted"
ALREADY_ISSUED = "already_issued"
STATUSES = (GRANTED, ALREADY_ISSUED)
NONCE = re.compile(r"[0-9a-f]{32}")
MAX_ENVELOPE_BYTES = 4096
BOUND_FIELDS = ("plan", "worker", "ticket", "nonce")
REQUEST_FIELDS = frozenset({"schema", *BOUND_FIELDS})
REPLY_FIELDS = frozenset({*REQUEST_FIELDS, "status"})

Message = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Namespace:
    request: str
    reply: str
    ticket: re.Pattern[str]


SEAT = Namespace(REQUEST_SCHEMA, REPLY_SCHEMA, TICKET)
SHIPPING = Namespace(SHIPPING_REQUEST_SCHEMA, SHIPPING_REPLY_SCHEMA, SHIPPING_TICKET)
NAMESPACES = (SEAT, SHIPPING)
assert len({schema for n in NAMESPACES for schema in (n.request, n.reply)}) == 4
assert all(
    SEAT.ticket.fullmatch(sample) is None
    for sample in ("sh-" + "0" * 16, "si-" + "0" * 16)
)
assert SHIPPING.ticket.fullmatch("sb-" + "0" * 16) is None


def _finite(text: str) -> float:
    value = float(text)
    require(math.isfinite(value), "JSON number is not finite")
    return value


def strict_object(text: str, label: str) -> Message:
    require(isinstance(text, str), f"{label} is not text")
    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=_finite,
        )
    except (ValueError, RecursionError) as error:
        raise ValueError(f"{label} is not strict JSON: {error}") from error
    return as_record(value, label)


def request_namespace(request: Message) -> Namespace:
    found = [space for space in NAMESPACES if request.get("schema") == space.request]
    require(len(found) == 1, "admission request schema differs")
    return found[0]


def _check_bound(record: Message, label: str, space: Namespace) -> None:
    as_text(record["plan"], f"{label} plan digest", DIGEST)
    as_int(record["worker"], f"{label} worker", 0, MAX_WORKERS - 1)
    as_text(record["ticket"], f"{label} ticket", space.ticket)
    as_text(record["nonce"], f"{label} nonce", NONCE)


def validate_request(value: object) -> Message:
    request = as_record(value, "admission request", REQUEST_FIELDS)
    _check_bound(request, "admission request", request_namespace(request))
    return request


def make_request(plan_digest: str, worker: int, ticket: str, nonce: str) -> Message:
    return validate_request(
        {
            "schema": _request_schema(ticket),
            "plan": plan_digest,
            "worker": worker,
            "ticket": ticket,
            "nonce": nonce,
        }
    )


def _request_schema(ticket: object) -> str:
    shipping = isinstance(ticket, str) and SHIPPING.ticket.fullmatch(ticket) is not None
    return SHIPPING.request if shipping else SEAT.request


def validate_reply(value: object, request: Message) -> str:
    request = validate_request(request)
    space = request_namespace(request)
    reply = as_record(value, "admission reply", REPLY_FIELDS)
    require(reply["schema"] == space.reply, "admission reply schema differs")
    _check_bound(reply, "admission reply", space)
    for field in BOUND_FIELDS:
        require(
            type(reply[field]) is type(request[field])
            and reply[field] == request[field],
            f"admission reply {field} differs from the request",
        )
    return as_choice(reply["status"], "admission reply status", STATUSES)


def make_reply(request: Message, status: str) -> Message:
    request = validate_request(request)
    as_choice(status, "admission status", STATUSES)
    reply = {field: request[field] for field in BOUND_FIELDS}
    reply.update(schema=request_namespace(request).reply, status=status)
    assert validate_reply(reply, request) == status
    return reply


def wire_bytes(message: Message) -> bytes:
    record = as_record(message, "admission envelope")
    require(
        any(record.get("schema") in (n.request, n.reply) for n in NAMESPACES),
        "admission envelope schema is unknown",
    )
    try:
        text = json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("admission envelope is not plain JSON") from error
    data = text.encode("ascii") + b"\n"
    require(
        len(data) <= MAX_ENVELOPE_BYTES,
        f"admission envelope exceeds {MAX_ENVELOPE_BYTES} bytes",
    )
    assert data.count(b"\n") == 1
    return data


def read_message(data: bytes) -> Message:
    require(isinstance(data, bytes), "admission envelope is not bytes")
    require(
        0 < len(data) <= MAX_ENVELOPE_BYTES,
        f"admission envelope is empty or exceeds {MAX_ENVELOPE_BYTES} bytes",
    )
    require(
        data.endswith(b"\n") and data.count(b"\n") == 1,
        "admission envelope is not one LF-terminated line",
    )
    try:
        text = data[:-1].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("admission envelope is not ASCII") from error
    return strict_object(text, "admission envelope")
