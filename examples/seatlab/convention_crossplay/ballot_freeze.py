import io
import json
import re
from typing import Never, cast

from ..coupled_retention import strategy

MAX_REPLY_FILES = 32
MAX_REPLY_BYTES = 16 * 1024 * 1024
MAX_REPLY_ROWS = 512


def _invalid() -> ValueError:
    return ValueError("invalid ballot reply files")


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _invalid()
    return result


def _reject(_: str) -> Never:
    raise _invalid()


def _parse(raw: str) -> object:
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject)


def _envelope(line: bytes) -> tuple[str, str, str]:
    try:
        value = _parse(line.decode("utf-8"))
        if type(value) is not dict or value.keys() != {"id", "prompt_sha256", "reply"}:
            raise _invalid()
        fields = cast(dict[str, object], value)
        request_id, pin, reply = fields["id"], fields["prompt_sha256"], fields["reply"]
        if (
            type(request_id) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", request_id) is None
            or type(pin) is not str
            or re.fullmatch(r"[0-9a-f]{64}", pin) is None
            or type(reply) is not str
        ):
            raise _invalid()
        return request_id, pin, reply
    except (ValueError, RecursionError):
        raise _invalid() from None


def _choice(reply: str, field: str, actions: tuple[str, ...]) -> str | None:
    try:
        value = _parse(reply)
    except (ValueError, RecursionError):
        return None
    if type(value) is not dict or value.keys() != {field}:
        return None
    choice = cast(dict[str, object], value)[field]
    if type(choice) is not str or choice not in actions:
        return None
    return choice


def freeze(
    role: str,
    actions: tuple[str, ...],
    field: str,
    requests: dict[str, tuple[str, bytes]],
    replies: list[bytes],
) -> tuple[dict[str, int], bytes | None]:
    if not 1 <= len(replies) <= MAX_REPLY_FILES or any(
        type(raw) is not bytes for raw in replies
    ):
        raise _invalid()
    if sum(len(raw) for raw in replies) > MAX_REPLY_BYTES:
        raise _invalid()
    rows = 0
    unknown = 0
    seen: set[str] = set()
    duplicate: set[str] = set()
    stale: set[str] = set()
    invalid: set[str] = set()
    choices: dict[str, str] = {}
    for raw in replies:
        for line in io.BytesIO(raw):
            rows += 1
            if rows > MAX_REPLY_ROWS:
                raise _invalid()
            request_id, pin, reply = _envelope(line)
            if request_id not in requests:
                unknown += 1
                continue
            if request_id in seen:
                duplicate.add(request_id)
            seen.add(request_id)
            if pin != requests[request_id][0]:
                stale.add(request_id)
            choice = _choice(reply, field, actions)
            if choice is None:
                invalid.add(request_id)
            else:
                choices[request_id] = choice
    valid = seen - duplicate - stale - invalid
    report = {
        "expected": len(requests),
        "represented": len(seen),
        "valid": len(valid),
        "missing": len(requests) - len(seen),
        "duplicate_ids": len(duplicate),
        "stale_ids": len(stale),
        "invalid_ids": len(invalid),
        "unknown_rows": unknown,
    }
    if len(valid) != len(requests) or unknown:
        return report, None
    return report, strategy.table(
        role,
        {packet: choices[request_id] for request_id, (_, packet) in requests.items()},
    )
