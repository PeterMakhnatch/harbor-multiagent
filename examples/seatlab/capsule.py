"""Seal and verify caller-approved JSON observations offline.

The digest identifies the canonical observation bytes, not the role metadata,
HTTP transport bytes, authorship, or what an actor noticed. A bound capsule
requires a matching acknowledgment from a producer the caller already trusts.
This tool does not redact its input or authenticate the acknowledgment.
"""

import argparse
import json
import sys
from hashlib import sha256
from typing import Literal, NotRequired, TypedDict

_SCHEMA = "seatlab.capsule/v1"
_FIELDS = {"schema", "role", "basis", "sha256", "view"}
_ACK_FIELD = "acknowledged_sha256"
Basis = Literal["reconstructed", "bound"]


class Capsule(TypedDict):
    schema: str
    role: str
    basis: Basis
    sha256: str
    view: object
    acknowledged_sha256: NotRequired[str]


def encode_view(view: object) -> bytes:
    """Encode a JSON value as sorted-key, ASCII-escaped UTF-8 without a newline.

    Object key order is immaterial. Array order is part of the observation.
    These are representation bytes, not a capture of the original input bytes.
    """
    return json.dumps(
        view, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True
    ).encode("utf-8")


def view_hash(view: object) -> str:
    """Return the lower-case SHA-256 hex digest of encode_view(view)."""
    return sha256(encode_view(view)).hexdigest()


def _check_metadata(role: object, basis: object) -> None:
    if not isinstance(role, str) or not role.strip():
        raise ValueError("a capsule requires a nonblank role")
    if not isinstance(basis, str) or basis not in ("reconstructed", "bound"):
        raise ValueError("unknown capsule basis")


def _check_ack(acknowledged_sha256: object, digest: str) -> str:
    if not isinstance(acknowledged_sha256, str) or acknowledged_sha256 != digest:
        raise ValueError("acknowledgment does not match the canonical view hash")
    return acknowledged_sha256


def make_capsule(
    role: str,
    view: object,
    *,
    basis: Basis = "reconstructed",
    acknowledged_sha256: str | None = None,
) -> Capsule:
    """Snapshot one approved view; never infer an acknowledgment from its bytes."""
    _check_metadata(role, basis)
    body = encode_view(view)
    digest = sha256(body).hexdigest()
    capsule: Capsule = {
        "schema": _SCHEMA,
        "role": role,
        "basis": basis,
        "sha256": digest,
        "view": json.loads(body),
    }
    if basis == "bound":
        capsule["acknowledged_sha256"] = _check_ack(acknowledged_sha256, digest)
    elif acknowledged_sha256 is not None:
        raise ValueError("a reconstructed capsule cannot contain an acknowledgment")
    return capsule


def extract_view(capsule: object) -> object:
    """Verify the envelope and return only its observation, without metadata."""
    if not isinstance(capsule, dict) or capsule.get("schema") != _SCHEMA:
        raise ValueError("unsupported capsule schema")
    basis = capsule.get("basis")
    _check_metadata(capsule.get("role"), basis)
    fields = _FIELDS | {_ACK_FIELD} if basis == "bound" else _FIELDS
    if capsule.keys() != fields:
        raise ValueError("invalid fields for the capsule basis")
    view = capsule.get("view")
    digest = view_hash(view)
    if capsule.get("sha256") != digest:
        raise ValueError("capsule view hash does not match")
    if basis == "bound":
        _check_ack(capsule.get(_ACK_FIELD), digest)
    return view


def verify_capsule(capsule: object) -> None:
    """Raise ValueError for an invalid envelope or a digest mismatch."""
    extract_view(capsule)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON number")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser("seal", help="seal one approved JSON view from stdin")
    seal.add_argument("--role", required=True)
    commands.add_parser("verify", help="verify and write a capsule from stdin")
    commands.add_parser("view", help="write only the verified canonical view bytes")
    args = parser.parse_args()
    try:
        data = json.loads(
            sys.stdin.buffer.read().decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if args.command == "seal":
            output = encode_view(make_capsule(args.role, data)) + b"\n"
        elif args.command == "verify":
            verify_capsule(data)
            output = encode_view(data) + b"\n"
        else:
            output = encode_view(extract_view(data))
    except (TypeError, ValueError, RecursionError):
        parser.exit(1, "invalid capsule or JSON view\n")
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
