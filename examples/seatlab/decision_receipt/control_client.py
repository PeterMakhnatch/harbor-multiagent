import json
import re
import socket
import sys
import time
from collections.abc import Callable
from typing import cast, override
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

BASE = "http://world:8080"
TOKEN_PATH = "/run/role/token"
ROUTES = frozenset(
    {
        ("GET", "/brief"),
        ("GET", "/status"),
        ("GET", "/inbox"),
        ("POST", "/resolve"),
    }
)
BRIEF = {"role": "relief", "receipt": "unconfirmed"}
STATUS_KEYS = frozenset({"handoff_ready", "relief_sealed"})
INBOX_KEYS = frozenset({"ready", "reference"})
REFERENCE = re.compile(r"[0-9a-f]{32}")
TOKEN = re.compile(r"[!-~]{1,256}")
ARGUMENTS = {"true": True, "false": False}
POLL_SEC = 2.0
WAIT_SEC = 300.0
MAX_POLLS = int(WAIT_SEC / POLL_SEC) + 1
REQUEST_SEC = 5.0
MAX_REPLY_BYTES = 4096
MAX_TOKEN_BYTES = 512
SEALED = "sealed"
FAILURES = frozenset(
    {
        "input_refused",
        "token_unreadable",
        "transport_failed",
        "brief_refused",
        "status_refused",
        "handoff_timeout",
        "inbox_refused",
        "resolve_unsent",
        "resolve_refused",
        "resolve_unconfirmed",
        "client_failed",
    }
)
LITERALS = frozenset({SEALED, *FAILURES})

assert SEALED not in FAILURES and len(LITERALS) == 12
assert POLL_SEC >= 2.0 and WAIT_SEC == 300.0 and MAX_POLLS == 151
assert all(method in ("GET", "POST") for method, _ in ROUTES) and len(ROUTES) == 4


class Unreachable(Exception):
    pass


class Unanswered(Exception):
    pass


class Failure(Exception):
    def __init__(self, literal: str) -> None:
        if literal not in FAILURES:
            raise ValueError("unknown failure literal")
        super().__init__(literal)
        self.literal = literal


Call = Callable[[str, str, bytes | None], tuple[int, bytes]]
Clock = Callable[[], float]
Sleep = Callable[[float], None]


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate JSON key")
    return record


def _constant(name: str) -> object:
    raise ValueError("non-finite JSON number")


def decode(raw: bytes) -> object:
    if type(raw) is not bytes or len(raw) > MAX_REPLY_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def read(call: Call, path: str, refused: str) -> object:
    status, raw = call("GET", path, None)
    if status != 200:
        raise Failure(refused)
    return decode(raw)


def contact(call: Call, clock: Clock, sleep: Sleep, deadline: float) -> None:
    for attempt in range(MAX_POLLS):
        if attempt:
            sleep(POLL_SEC)
        try:
            reply = read(call, "/brief", "brief_refused")
        except Unreachable:
            if clock() >= deadline:
                break
            continue
        if type(reply) is not dict or reply != BRIEF:
            raise Failure("brief_refused")
        return
    raise Failure("brief_refused")


def handoff_ready(reply: object) -> bool:
    if type(reply) is not dict or set(reply) != STATUS_KEYS:
        raise Failure("status_refused")
    fields = cast(dict[str, object], reply)
    ready, sealed = fields["handoff_ready"], fields["relief_sealed"]
    if type(ready) is not bool or sealed is not False:
        raise Failure("status_refused")
    return ready


def await_handoff(call: Call, clock: Clock, sleep: Sleep, deadline: float) -> None:
    for attempt in range(MAX_POLLS):
        if attempt:
            sleep(POLL_SEC)
        try:
            if handoff_ready(read(call, "/status", "status_refused")):
                return
        except Unreachable:
            pass
        if clock() >= deadline:
            break
    raise Failure("handoff_timeout")


def inbox(call: Call) -> str:
    try:
        reply = read(call, "/inbox", "inbox_refused")
    except Unreachable:
        raise Failure("transport_failed") from None
    if type(reply) is not dict or set(reply) != INBOX_KEYS:
        raise Failure("inbox_refused")
    fields = cast(dict[str, object], reply)
    reference = fields["reference"]
    if fields["ready"] is not True or type(reference) is not str:
        raise Failure("inbox_refused")
    if REFERENCE.fullmatch(reference) is None:
        raise Failure("inbox_refused")
    return reference


def resolution(cite: bool, reference: str) -> bytes:
    if type(cite) is not bool or REFERENCE.fullmatch(reference) is None:
        raise ValueError("resolution input refused")
    body = {"action": "close", "cite": reference} if cite else {"action": "close"}
    return json.dumps(body, separators=(",", ":")).encode("ascii")


def resolve(call: Call, data: bytes) -> None:
    try:
        status, raw = call("POST", "/resolve", data)
    except Unreachable:
        raise Failure("resolve_unsent") from None
    except Unanswered:
        raise Failure("resolve_unconfirmed") from None
    if status != 200:
        raise Failure("resolve_refused")
    reply = decode(raw)
    if (
        type(reply) is not dict
        or set(reply) != {"ok"}
        or cast(dict[str, object], reply)["ok"] is not True
    ):
        raise Failure("resolve_unconfirmed")


def run(cite: bool, call: Call, clock: Clock, sleep: Sleep) -> str:
    if type(cite) is not bool:
        raise Failure("input_refused")
    deadline = clock() + WAIT_SEC
    try:
        contact(call, clock, sleep, deadline)
        await_handoff(call, clock, sleep, deadline)
        reference = inbox(call)
    except Unanswered:
        raise Failure("transport_failed") from None
    resolve(call, resolution(cite, reference))
    return SEALED


def read_token() -> str:
    try:
        with open(TOKEN_PATH, "rb") as handle:
            raw = handle.read(MAX_TOKEN_BYTES + 1)
        text = raw.decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        raise Failure("token_unreadable") from None
    if len(raw) > MAX_TOKEN_BYTES or TOKEN.fullmatch(text) is None:
        raise Failure("token_unreadable")
    return text


class _Unredirected(HTTPRedirectHandler):
    @override
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(token: str) -> Call:
    if TOKEN.fullmatch(token) is None:
        raise Failure("token_unreadable")
    opener = build_opener(ProxyHandler({}), _Unredirected())

    def call(method: str, path: str, data: bytes | None) -> tuple[int, bytes]:
        if (method, path) not in ROUTES or (data is None) != (method == "GET"):
            raise ValueError("route refused")
        request = Request(BASE + path, data=data, method=method)
        request.add_unredirected_header("Authorization", "Bearer " + token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with opener.open(request, timeout=REQUEST_SEC) as response:
                return response.status, response.read(MAX_REPLY_BYTES + 1)
        except HTTPError as error:
            return error.code, error.read(MAX_REPLY_BYTES + 1)
        except URLError as error:
            if isinstance(error.reason, (ConnectionRefusedError, socket.gaierror)):
                raise Unreachable() from None
            raise Unanswered() from None
        except OSError:
            raise Unanswered() from None

    return call


def emit(literal: str) -> int:
    if literal not in LITERALS:
        literal = "client_failed"
    sys.stdout.write(literal + "\n")
    sys.stdout.flush()
    return 0 if literal == SEALED else 1


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ARGUMENTS:
        return emit("input_refused")
    try:
        literal = run(
            ARGUMENTS[argv[0]], transport(read_token()), time.monotonic, time.sleep
        )
    except Failure as failure:
        literal = failure.literal
    except Exception:  # noqa: BLE001
        literal = "client_failed"
    return emit(literal)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
