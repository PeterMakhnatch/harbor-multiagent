import json
import sys
from typing import cast

SCHEMA = "seatlab.coupled-retention.role/v1"
INSTRUCTION = (
    "Both roles already endorsed the incumbent. The team wins if both sealed votes "
    "equal the incumbent when feasible, or the unique shared available alternative "
    "otherwise. '?' means omitted; '-' means an empty report. No private actions "
    'after this offer. Send {"slot":"A"} using one of the listed labels.'
)
ROLES = ("east", "west")
SLOTS = ("A", "B", "C", "D", "E", "F")
FIELDS = frozenset(
    ("schema", "role", "instruction", "incumbent", "options", "own", "peer")
)
OMITTED = ("?", "?")
PASSED = ("-", "-")
MAX_PACKET_BYTES = 1024


def _invalid() -> ValueError:
    return ValueError("invalid role packet")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _invalid()
    return result


def _reject_constant(_: str) -> object:
    raise _invalid()


def _labels(value: object, size: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != size:
        raise _invalid()
    if not all(isinstance(item, str) and item in SLOTS for item in value):
        raise _invalid()
    labels = tuple(cast(list[str], value))
    if len(set(labels)) != size:
        raise _invalid()
    return labels


def _available(value: object, incumbent: str) -> tuple[str, ...]:
    labels = _labels(value, 2)
    if labels != tuple(sorted(labels)) or incumbent in labels:
        raise _invalid()
    return labels


def _validate(data: object) -> str:
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise _invalid()
    fields = cast(dict[str, object], data)
    if (
        fields["schema"] != SCHEMA
        or fields["instruction"] != INSTRUCTION
        or fields["role"] not in ROLES
    ):
        raise _invalid()
    options = _labels(fields["options"], len(SLOTS))
    incumbent = fields["incumbent"]
    if not isinstance(incumbent, str) or incumbent not in options:
        raise _invalid()
    own = _available(fields["own"], incumbent)
    peer = fields["peer"]
    if peer == list(OMITTED) or peer == list(PASSED):
        return own[0]
    shared = set(own).intersection(_available(peer, incumbent))
    if len(shared) != 1:
        raise _invalid()
    return next(iter(shared))


def render(
    role: str,
    incumbent: str,
    options: tuple[str, ...],
    own: tuple[str, ...],
    peer: tuple[str, ...] | None,
) -> bytes:
    if peer in (OMITTED, PASSED):
        raise _invalid()
    observed = (
        OMITTED if peer is None else PASSED if peer == () else tuple(sorted(peer))
    )
    fields = {
        "schema": SCHEMA,
        "role": role,
        "instruction": INSTRUCTION,
        "incumbent": incumbent,
        "options": list(options),
        "own": sorted(own),
        "peer": list(observed),
    }
    _validate(fields)
    return json.dumps(fields, separators=(",", ":"), allow_nan=False).encode("utf-8")


def act(packet: bytes) -> dict[str, str]:
    if not isinstance(packet, bytes) or not 0 < len(packet) <= MAX_PACKET_BYTES:
        raise _invalid()
    try:
        data = json.loads(
            packet.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError):
        raise _invalid() from None
    return {"slot": _validate(data)}


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_PACKET_BYTES + 2)
    packet = raw.removesuffix(b"\n")
    try:
        if b"\n" in packet:
            raise _invalid()
        vote = act(packet)
    except ValueError:
        print("invalid role packet", file=sys.stderr)
        return 1
    print(json.dumps(vote, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
