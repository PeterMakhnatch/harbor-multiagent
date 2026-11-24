import json
import re
import shlex
from typing import Any, cast

from harbor.environments.base import BaseEnvironment

REASON = re.compile(r"[a-z_]{1,48}")
METHODS = frozenset({"GET", "POST"})
SEAT_PATH = re.compile(r"/[a-z]{1,32}")
ADMIN_PATHS = frozenset({"/health", "/freeze"})
MAX_ROUTES = 16
MIN_BODY_BYTES = 2
MAX_BODY_BYTES = 1024
EXEC_TIMEOUT_SEC = 60
MAX_STDOUT_BYTES = 16 << 10

PROGRAM = r"""
import json, re, socket, sys, time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SEAT_PATH = re.compile(r"/[a-z]{1,32}")
ADMIN_PATHS = {"/health", "/freeze"}
MAX_ROUTES = 16
LIMIT = 4096
READY_SEC = 20.0
ATTEMPTS = 40


class Refuse(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def seat_routes(text):
    try:
        items = json.loads(text)
    except (ValueError, RecursionError):
        sys.exit(3)
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ROUTES:
        sys.exit(3)
    routes = set()
    for item in items:
        if not isinstance(item, list) or len(item) != 2:
            sys.exit(3)
        method, path = item
        if method not in ("GET", "POST") or not isinstance(path, str):
            sys.exit(3)
        if SEAT_PATH.fullmatch(path) is None or path in ADMIN_PATHS:
            sys.exit(3)
        routes.add((method, path))
    if len(routes) != len(items):
        sys.exit(3)
    return routes


if len(sys.argv) not in (4, 5):
    sys.exit(3)
method, path = sys.argv[2], sys.argv[3]
if (method, path) not in seat_routes(sys.argv[1]):
    sys.exit(3)
if (len(sys.argv) == 5) != (method == "POST"):
    sys.exit(3)
if method == "POST" and not sys.argv[4].isascii():
    sys.exit(3)
data = sys.argv[4].encode("ascii") if method == "POST" else None
if data is not None and not 2 <= len(data) <= 1024:
    sys.exit(3)
try:
    with open("/run/role/token", encoding="ascii") as handle:
        token = handle.read(256).strip()
except (OSError, UnicodeDecodeError):
    sys.exit(4)
if not token:
    sys.exit(4)
opener = build_opener(ProxyHandler({}), Refuse())
deadline = time.monotonic() + READY_SEC
for _ in range(ATTEMPTS):
    request = Request("http://world:8080" + path, data=data, method=method)
    request.add_unredirected_header("Authorization", "Bearer " + token)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with opener.open(request, timeout=5) as response:
            status, raw = response.status, response.read(LIMIT + 1)
        break
    except HTTPError as error:
        status, raw = error.code, error.read(LIMIT + 1)
        break
    except URLError as error:
        ready = isinstance(error.reason, (ConnectionRefusedError, socket.gaierror))
        if not ready or time.monotonic() >= deadline:
            sys.exit(5)
    except OSError:
        sys.exit(5)
    time.sleep(0.5)
else:
    sys.exit(5)
if len(raw) > LIMIT:
    sys.exit(6)
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    sys.exit(7)
sys.stdout.write(json.dumps({"status": status, "body": text}) + "\n")
"""


class SeatFailure(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        assert REASON.fullmatch(reason)
        super().__init__(f"{reason}{': ' + detail if detail else ''}")
        self.reason = reason


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate JSON key")
    return record


def _constant(name: str) -> None:
    raise ValueError("non-finite JSON number")


def _strict(text: str) -> object:
    return json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)


def _seat_route(route: object) -> bool:
    if type(route) is not tuple or len(route) != 2:
        return False
    method, path = route
    return (
        type(method) is str
        and method in METHODS
        and type(path) is str
        and SEAT_PATH.fullmatch(path) is not None
        and path not in ADMIN_PATHS
    )


def _routes_argument(routes: frozenset[tuple[str, str]]) -> str:
    if type(routes) is not frozenset or not 1 <= len(routes) <= MAX_ROUTES:
        raise ValueError("seat routes must be a bounded frozenset")
    if not all(_seat_route(route) for route in routes):
        raise ValueError("seat routes must name seat endpoints only")
    pairs = sorted([method, path] for method, path in routes)
    return json.dumps(pairs, separators=(",", ":"))


def _check_call(
    routes: frozenset[tuple[str, str]], method: str, path: str, body: str | None
) -> None:
    if (method, path) not in routes:
        raise ValueError("seat request route is not in the world route set")
    if (body is None) != (method == "GET"):
        raise ValueError("seat request body must accompany POST only")
    if body is not None and (
        type(body) is not str
        or not body.isascii()
        or not MIN_BODY_BYTES <= len(body) <= MAX_BODY_BYTES
    ):
        raise ValueError("seat request body is not bounded ASCII")


def _reply(stdout: str | None) -> tuple[int, dict[str, object]]:
    text = stdout or ""
    if len(text.encode("utf-8")) > MAX_STDOUT_BYTES or text.count("\n") != 1:
        raise SeatFailure("seat_reply_malformed")
    try:
        outer = _strict(text)
        if not isinstance(outer, dict) or set(outer) != {"status", "body"}:
            raise ValueError("outer reply fields differ")
        fields = cast(dict[str, object], outer)
        status, body = fields["status"], fields["body"]
        if isinstance(status, bool) or not isinstance(status, int):
            raise ValueError("status is not an integer")
        if not 100 <= status <= 599:
            raise ValueError("status out of range")
        if not isinstance(body, str):
            raise ValueError("body is not text")
        payload = _strict(body)
    except (ValueError, RecursionError):
        raise SeatFailure("seat_reply_malformed") from None
    if not isinstance(payload, dict):
        raise SeatFailure("seat_reply_malformed")
    return status, cast(dict[str, object], payload)


async def seat_request(
    environment: BaseEnvironment,
    routes: frozenset[tuple[str, str]],
    method: str,
    path: str,
    body: str | None,
) -> tuple[int, dict[str, object]]:
    allowed = _routes_argument(routes)
    _check_call(routes, method, path, body)
    argv = ["python3", "-I", "-c", PROGRAM, allowed, method, path]
    if body is not None:
        argv.append(body)
    command = shlex.join(argv)
    try:
        result = await environment.exec(command, timeout_sec=EXEC_TIMEOUT_SEC)
    except Exception as exc:
        raise SeatFailure("seat_exec_failed", type(exc).__name__) from None
    if result.return_code != 0:
        raise SeatFailure("seat_request_failed", f"exit {result.return_code}")
    return _reply(result.stdout)
