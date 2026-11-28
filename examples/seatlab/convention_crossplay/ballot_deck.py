import json
import re
import secrets

from ..coupled_retention import author, strategy
from .loader import digest

SCHEMA = "seatlab.role-ballot-deck/v1"
MAX_MANIFEST_BYTES = 32 << 10
MAX_REQUESTS_BYTES = 16 << 20


def _invalid() -> ValueError:
    return ValueError("invalid role ballot deck or pin")


def _settings(
    role: object, actions: object, field: object
) -> tuple[str, tuple[str, ...], str]:
    if (
        type(role) is not str
        or role not in strategy.ROLES
        or type(field) is not str
        or field not in ("slot", "action")
        or type(actions) is not tuple
        or not 1 <= len(actions) <= 16
    ):
        raise _invalid()
    try:
        labels = tuple(strategy._label(label) for label in actions)
    except ValueError:
        raise _invalid() from None
    if len(set(labels)) != len(labels):
        raise _invalid()
    return role, labels, field


def _json(value: object) -> bytes:
    return (
        json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def make(
    role: str, actions: tuple[str, ...], packets: set[bytes], field: str
) -> tuple[bytes, bytes]:
    role, actions, field = _settings(role, actions, field)
    if type(packets) is not set or not 1 <= len(packets) <= strategy.MAX_OBSERVATIONS:
        raise _invalid()
    observations: list[tuple[str, str]] = []
    try:
        for packet in packets:
            observations.append((strategy.digest(packet), packet.decode("utf-8")))
    except (ValueError, AttributeError):
        raise _invalid() from None
    secrets.SystemRandom().shuffle(observations)
    ids = [secrets.token_hex(16) for _ in observations]
    if len(set(ids)) != len(ids):
        raise _invalid()
    requests = b"".join(
        _json({"id": request_id, "prompt_sha256": pin, "prompt": text})
        for request_id, (pin, text) in zip(ids, observations, strict=True)
    )
    manifest = _json(
        {
            "schema": SCHEMA,
            "role": role,
            "actions": actions,
            "response_field": field,
            "requests_sha256": digest(requests),
            "count": len(observations),
        }
    )
    if len(manifest) > MAX_MANIFEST_BYTES or len(requests) > MAX_REQUESTS_BYTES:
        raise _invalid()
    return manifest, requests


def _roster(requests: bytes, count: int) -> dict[str, tuple[str, bytes]]:
    lines = requests.split(b"\n", count)
    if len(lines) != count + 1 or lines[-1] != b"":
        raise _invalid()
    roster: dict[str, tuple[str, bytes]] = {}
    seen: set[bytes] = set()
    for line in lines[:-1]:
        if b"\r" in line:
            raise _invalid()
        entry = author.document(line)
        if entry.keys() != {"id", "prompt_sha256", "prompt"}:
            raise _invalid()
        request_id, pin, prompt = entry["id"], entry["prompt_sha256"], entry["prompt"]
        if (
            type(request_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", request_id) is None
            or request_id in roster
            or type(pin) is not str
            or type(prompt) is not str
        ):
            raise _invalid()
        packet = prompt.encode("utf-8")
        if strategy.digest(packet) != pin or packet in seen:
            raise _invalid()
        roster[request_id] = (pin, packet)
        seen.add(packet)
    return roster


def parse(
    manifest: bytes, requests: bytes, expected_pin: str
) -> tuple[str, tuple[str, ...], str, dict[str, tuple[str, bytes]]]:
    if (
        type(manifest) is not bytes
        or not 0 < len(manifest) <= MAX_MANIFEST_BYTES
        or type(requests) is not bytes
        or not 0 < len(requests) <= MAX_REQUESTS_BYTES
        or type(expected_pin) is not str
        or digest(manifest) != expected_pin
    ):
        raise _invalid()
    try:
        data = author.document(manifest)
        if (
            data.keys()
            != {
                "schema",
                "role",
                "actions",
                "response_field",
                "requests_sha256",
                "count",
            }
            or data["schema"] != SCHEMA
        ):
            raise _invalid()
        if data["requests_sha256"] != digest(requests):
            raise _invalid()
        count = data["count"]
        if type(count) is not int or not 1 <= count <= strategy.MAX_OBSERVATIONS:
            raise _invalid()
        if type(data["actions"]) is not list:
            raise _invalid()
        role, actions, field = _settings(
            data["role"], tuple(data["actions"]), data["response_field"]
        )
        return role, actions, field, _roster(requests, count)
    except (ValueError, RecursionError):
        raise _invalid() from None
