import json
import subprocess
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TypedDict

from .. import calendar_case
from ..visibility import seat_command
from . import route_booking

_FALLBACK_TIMEOUT_SECONDS = 90.0
_FALLBACK_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"


class WorkerEnvelope(TypedDict):
    id: str
    prompt_sha256: str
    reply: str


def _route_request(request_bytes: bytes) -> dict[str, object]:
    if (
        not isinstance(request_bytes, bytes)
        or not 0 < len(request_bytes) <= calendar_case._MAX_LINE_BYTES
    ):
        raise ValueError("expected one bounded checked calendar request")
    try:
        end = len(request_bytes)
        if request_bytes.endswith(b"\r\n"):
            end -= 2
        elif request_bytes.endswith(b"\n"):
            end -= 1
        if (
            request_bytes.find(b"\n", 0, end) != -1
            or request_bytes.find(b"\r", 0, end) != -1
        ):
            raise ValueError("expected one request row")
        return route_booking._route(request_bytes)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid checked calendar request") from None


def _fallback_reply(
    request_bytes: bytes,
    fallback_callable: Callable[[bytes], bytes],
    case_id: str,
    commitment: str,
) -> str:
    frame = fallback_callable(request_bytes)
    if not isinstance(frame, bytes):
        raise TypeError("expected fallback row in bytes")
    if (
        not 0 < len(frame) <= calendar_case._MAX_LINE_BYTES
        or frame.find(b"\n") != len(frame) - 1
    ):
        raise ValueError("expected one bounded fallback row")
    try:
        observed_id, observed_commitment, reply = calendar_case._worker_line(frame)
        too_large = (
            len(reply) > calendar_case._MAX_REPLY_BYTES
            or len(reply.encode("utf-8")) > calendar_case._MAX_REPLY_BYTES
        )
    except (ValueError, RecursionError):
        raise ValueError("invalid fallback worker envelope") from None
    if observed_id != case_id or observed_commitment != commitment:
        raise ValueError("fallback binding mismatch")
    if too_large:
        raise ValueError("fallback reply exceeds the byte limit")
    return reply


def resolve(
    request_bytes: bytes, fallback_callable: Callable[[bytes], bytes]
) -> WorkerEnvelope:
    decision = _route_request(request_bytes)
    case_id = decision.get("id")
    commitment = decision.get("prompt_sha256")
    if not isinstance(case_id, str) or not isinstance(commitment, str):
        raise TypeError("invalid booking route binding")
    if decision["action"] == "book":
        reply = json.dumps(decision["booking"], separators=(",", ":"), allow_nan=False)
    elif decision["action"] == "defer":
        reply = _fallback_reply(request_bytes, fallback_callable, case_id, commitment)
    else:
        raise ValueError("invalid booking route action")
    return {"id": case_id, "prompt_sha256": commitment, "reply": reply}


def _external(executable: str, request: bytes) -> bytes:
    with seat_command._command_line(
        (executable,),
        request,
        deadline=time.monotonic() + _FALLBACK_TIMEOUT_SECONDS,
        max_reply_bytes=calendar_case._MAX_LINE_BYTES,
        search_path=_FALLBACK_PATH,
        environ={},
    ) as response:
        return bytes(response)


def main() -> int:
    parser = calendar_case._Parser(allow_abbrev=False)
    parser.add_argument("--fallback", required=True, metavar="ABSOLUTE_EXECUTABLE")
    args = parser.parse_args()
    try:
        if not Path(args.fallback).is_absolute():
            raise ValueError("expected an absolute fallback executable")
        request = sys.stdin.buffer.read(calendar_case._MAX_LINE_BYTES + 1)
        envelope = resolve(request, partial(_external, args.fallback))
        sys.stdout.buffer.write(calendar_case._wire(envelope))
    except (OSError, RecursionError, subprocess.SubprocessError, TypeError, ValueError):
        print("Cannot resolve checked calendar booking request.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
