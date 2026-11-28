import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Never, cast, override

SCHEMA = "seatlab.private-ballot-strategy/v1"
ROLES = ("east", "west")
MAX_OBSERVATIONS = 256
MAX_PACKET_BYTES = 8192
MAX_TABLE_BYTES = 512 * 1024


def _invalid() -> ValueError:
    return ValueError("invalid private ballot strategy or observation")


def _role(role: object) -> None:
    if type(role) is not str or role not in ROLES:
        raise _invalid()


def _label(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or not value.isprintable()
        or value.strip() != value
    ):
        raise _invalid()
    return value


def digest(packet: bytes) -> str:
    if type(packet) is not bytes or len(packet) > MAX_PACKET_BYTES:
        raise _invalid()
    return hashlib.sha256(packet).hexdigest()


def table(role: str, rules: dict[bytes, str]) -> bytes:
    _role(role)
    if type(rules) is not dict or not 1 <= len(rules) <= MAX_OBSERVATIONS:
        raise _invalid()
    entries: dict[str, str] = {}
    for packet, label in rules.items():
        key = digest(packet)
        if key in entries:
            raise _invalid()
        entries[key] = _label(label)
    return json.dumps(
        {"schema": SCHEMA, "role": role, "rules": entries},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _invalid()
    return result


def _reject(_: str) -> Never:
    raise _invalid()


def load(raw: bytes, role: str) -> dict[str, str]:
    _role(role)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_TABLE_BYTES:
        raise _invalid()
    try:
        parsed: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_reject
        )
    except (ValueError, RecursionError):
        raise _invalid() from None
    if type(parsed) is not dict or parsed.keys() != {"schema", "role", "rules"}:
        raise _invalid()
    fields = cast(dict[str, object], parsed)
    if fields["schema"] != SCHEMA or fields["role"] != role:
        raise _invalid()
    rules = fields["rules"]
    if type(rules) is not dict or not 1 <= len(rules) <= MAX_OBSERVATIONS:
        raise _invalid()
    result: dict[str, str] = {}
    for key, value in cast(dict[str, object], rules).items():
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise _invalid()
        result[key] = _label(value)
    return result


def choose(rules: dict[str, str], packet: bytes) -> str:
    key = digest(packet)
    if key not in rules:
        raise _invalid()
    return _label(rules[key])


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        raise _invalid()


def main() -> int:
    parser = _Parser(description="Select a scripted private ballot from a role table.")
    parser.add_argument("--role", choices=ROLES, required=True)
    parser.add_argument("--table", type=Path, required=True)
    parser.add_argument("--format", choices=("action", "slot"), default="action")
    try:
        args = parser.parse_args()
        with args.table.open("rb") as stream:
            rules = load(stream.read(MAX_TABLE_BYTES + 1), args.role)
        framed = args.format == "slot"
        packet = sys.stdin.buffer.read(MAX_PACKET_BYTES + 1 + int(framed))
        if framed:
            packet = packet.removesuffix(b"\n")
        vote = {args.format: choose(rules, packet)}
    except (ValueError, OSError):
        print("invalid private ballot strategy or observation", file=sys.stderr)
        return 1
    print(json.dumps(vote, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
