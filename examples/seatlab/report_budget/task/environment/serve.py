import json
import logging
import math
import os
import socket
from collections.abc import Callable
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import NoReturn, cast

from market_handoff.timing_messages import read_price, read_sales

SALES = "sales"
FINANCE = "finance"
SEATS = (SALES, FINANCE)
TARGETS = (100, 160)
STEPS = ("sales_view", "sales_resolved", "finance_view", "finance_resolved")
SCENARIO = Path("/world/scenario.json")
SCENARIO_SCHEMA = "seatlab.report-budget-scenario/v1"
SCENARIO_KEYS = frozenset(
    {"schema", "world", "target", "checkpoint_sha256", "sales", "finance"}
)
LOG = Path("/logs/world/budget.json")
LOG_SCHEMA = "seatlab.report-budget-native/v1"
DIGEST_HEADER = "X-SeatLab-View-SHA256"
HEX = frozenset("0123456789abcdef")
PARSE_ERRORS = (ValueError, TypeError, LookupError, RecursionError)
MAX_WORLD = 24
MAX_SCENARIO_BYTES = 1 << 22
MAX_SUBMISSION_BYTES = 1 << 20
MAX_REJECTED = 32
MAX_PATH_CHARS = 128
PORT = 8080


class Conflict(Exception):
    pass


class Forbidden(Exception):
    pass


class Broken(Exception):
    pass


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = {key for key, _ in pairs}
    return dict(pairs) if len(keys) == len(pairs) else _fail("duplicate key")


def _finite(text: str) -> float:
    value = float(text)
    return value if math.isfinite(value) else _fail("non-finite number")


def decode(data: bytes) -> object:
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique,
        parse_float=_finite,
        parse_constant=_fail,
    )


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(value: object) -> str:
    return sha256(canonical(value)).hexdigest()


def _object(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("expected a JSON object")
    items = cast(dict[object, object], value).items()
    result = {key: item for key, item in items if isinstance(key, str)}
    if len(result) != len(items) or set(result) != keys:
        _fail("JSON object keys are invalid")
    return result


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value) <= HEX


def load_scenario(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        data = handle.read(MAX_SCENARIO_BYTES + 1)
    if not 0 < len(data) <= MAX_SCENARIO_BYTES:
        _fail("scenario size is out of bounds")
    scenario = _object(decode(data), SCENARIO_KEYS)
    world, target = scenario["world"], scenario["target"]
    if scenario["schema"] != SCENARIO_SCHEMA:
        _fail("scenario schema is invalid")
    if type(world) is not int or not 0 <= world <= MAX_WORLD:
        _fail("scenario world is invalid")
    if type(target) is not int or target not in TARGETS:
        _fail("scenario target is invalid")
    if not _is_hex(scenario["checkpoint_sha256"], 64):
        _fail("scenario checkpoint digest is invalid")
    if not all(isinstance(scenario[seat], dict) and scenario[seat] for seat in SEATS):
        _fail("scenario views are invalid")
    if canonical(scenario) != data:
        _fail("scenario is not canonical")
    return scenario


def reply_text(body: object) -> str | None:
    reply = _object(body, frozenset({"reply"}))["reply"]
    if reply is not None and not isinstance(reply, str):
        _fail("reply must be a string or null")
    return reply


def sales_outcome(reply: str | None) -> tuple[str, dict[str, object] | None]:
    if reply is None:
        return "unanswered", None
    try:
        return "accepted", read_sales(reply)
    except PARSE_ERRORS:
        return "invalid", None


def finance_outcome(reply: str | None) -> tuple[str, int | None]:
    if reply is None:
        return "unanswered", None
    try:
        return "accepted", read_price(reply)
    except PARSE_ERRORS:
        return "invalid", None


def _header_digest(headers: list[str]) -> str:
    if len(headers) != 1 or not _is_hex(headers[0], 64):
        _fail("expected one lowercase SHA-256 view digest")
    return headers[0]


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_log(record: dict[str, object]) -> None:
    with NamedTemporaryFile(dir=LOG.parent, prefix=".budget-", delete=False) as staged:
        staged.write(canonical(record))
        staged.flush()
        os.fchmod(staged.fileno(), 0o444)
        os.fsync(staged.fileno())
    os.replace(staged.name, LOG)
    _fsync_directory(LOG.parent)


class World:
    def __init__(self, scenario: dict[str, object]) -> None:
        self.world = cast(int, scenario["world"])
        self.target = cast(int, scenario["target"])
        self.checkpoint = cast(str, scenario["checkpoint_sha256"])
        self.views = {seat: scenario[seat] for seat in SEATS}
        self.lock = Lock()
        self.broken = False
        self.report: dict[str, object] | None = None
        self.events: list[dict[str, object]] = []
        self.rejected: list[dict[str, object]] = []
        self.rejected_total = 0

    def log(self) -> dict[str, object]:
        return {
            "schema": LOG_SCHEMA,
            "world": self.world,
            "target": self.target,
            "checkpoint_sha256": self.checkpoint,
            "events": self.events,
            "rejected": self.rejected,
            "rejected_total": self.rejected_total,
        }

    def _write(
        self,
        events: list[dict[str, object]],
        rejected: list[dict[str, object]],
        rejected_total: int,
    ) -> None:
        if self.broken:
            raise Broken
        draft = self.log()
        draft.update(events=events, rejected=rejected, rejected_total=rejected_total)
        try:
            replace_log(draft)
        except OSError:
            self.broken = True
            raise

    def _steps(self) -> list[object]:
        return [event["step"] for event in self.events]

    def _record(self, row: dict[str, object]) -> None:
        step = row["step"]
        if self._steps() != list(STEPS[: STEPS.index(cast(str, step))]):
            raise AssertionError("event out of order")
        event = {"n": len(self.events) + 1, **row}
        self._write([*self.events, event], self.rejected, self.rejected_total)
        self.events.append(event)

    def _event(self, step: str) -> dict[str, object] | None:
        return next((event for event in self.events if event["step"] == step), None)

    def view(self, seat: str) -> dict[str, object]:
        if seat == SALES:
            return {"view": self.views[SALES]}
        if self._event("sales_resolved") is None:
            raise Conflict("waiting for sales")
        return {"view": self.views[FINANCE], "report": self.report}

    def read(self, seat: str) -> dict[str, object]:
        with self.lock:
            if self.broken:
                raise Broken
            body = self.view(seat)
            seen = self._event(f"{seat}_view")
            if seen is None:
                row: dict[str, object] = {
                    "step": f"{seat}_view",
                    "view_sha256": digest(body),
                }
                if seat == FINANCE:
                    row["report"] = self.report
                self._record(row)
            elif seen["view_sha256"] != digest(body):
                raise AssertionError("a delivered view changed")
            return body

    def resolve(self, seat: str, body: object, header: str) -> dict[str, object]:
        with self.lock:
            if self.broken:
                raise Broken
            if self._event(f"{seat}_resolved") is not None:
                raise Conflict("already resolved")
            seen = self._event(f"{seat}_view")
            if seen is None:
                raise Conflict("read the view first")
            if header != seen["view_sha256"]:
                _fail("digest does not match the view")
            reply = reply_text(body)
            if seat == SALES:
                status, report = sales_outcome(reply)
                self._record(
                    {
                        "step": "sales_resolved",
                        "reply": reply,
                        "status": status,
                        "report": report,
                    }
                )
                self.report = report
            else:
                status, pick = finance_outcome(reply)
                self._record(
                    {
                        "step": "finance_resolved",
                        "reply": reply,
                        "status": status,
                        "pick": pick,
                    }
                )
            return {"committed": True, "status": status}

    def reject(self, path: str, status: int, error: str) -> None:
        with self.lock:
            if self.broken:
                return
            row: dict[str, object] = {"path": path, "status": status, "error": error}
            rejected = self.rejected
            if len(rejected) < MAX_REJECTED:
                rejected = [*rejected, row]
            self._write(self.events, rejected, self.rejected_total + 1)
            self.rejected = rejected
            self.rejected_total += 1


class WorldServer(ThreadingHTTPServer):
    def __init__(self, world: World) -> None:
        self.world = world
        super().__init__(("0.0.0.0", PORT), Handler)


def seat_addresses() -> dict[str, str]:
    owners: dict[str, str] = {}
    for seat in SEATS:
        try:
            _, _, addresses = socket.gethostbyname_ex(seat)
        except (socket.gaierror, socket.herror, UnicodeError):
            continue
        if len(addresses) != 1:
            continue
        address = addresses[0]
        owners[address] = "" if address in owners else seat
    return {address: seat for address, seat in owners.items() if seat}


class Handler(BaseHTTPRequestHandler):
    timeout = 5.0

    @property
    def _world(self) -> World:
        return cast(WorldServer, self.server).world

    def _caller(self, role: str) -> str:
        seat = seat_addresses().get(self.client_address[0])
        if seat is None or seat != role:
            raise Forbidden("this seat cannot use that role")
        return seat

    def _reply(self, status: int, body: object) -> None:
        data = canonical(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _submission(self) -> tuple[object, str]:
        header = _header_digest(self.headers.get_all(DIGEST_HEADER, []))
        lengths = self.headers.get_all("Content-Length", [])
        if (
            len(lengths) != 1
            or self.headers.get("Transfer-Encoding") is not None
            or self.headers.get_content_type() != "application/json"
        ):
            _fail("expected a length-delimited JSON body")
        length = int(lengths[0])
        if not 0 < length <= MAX_SUBMISSION_BYTES:
            _fail("body length is out of bounds")
        try:
            body = self.rfile.read(length)
        except OSError:
            _fail("unreadable body")
        if len(body) != length:
            _fail("incomplete body")
        return decode(body), header

    def _refuse(self, status: int, error: str, log: bool) -> None:
        if log:
            try:
                self._world.reject(self.path[:MAX_PATH_CHARS], status, error)
            except (Broken, OSError):
                status, error = 500, "the world cannot record actions"
        self._reply(status, {"error": error})

    def _guarded(
        self, log_conflict: bool, call: Callable[[], dict[str, object]]
    ) -> None:
        try:
            result = call()
        except Conflict as error:
            self._refuse(409, str(error), log_conflict)
        except Forbidden as error:
            self._refuse(403, str(error), True)
        except (ValueError, RecursionError):
            self._refuse(400, "invalid request", True)
        except (Broken, OSError, AssertionError):
            self._world.broken = True
            logging.getLogger(__name__).exception("Budget world cannot record actions")
            self._reply(500, {"error": "the world cannot record actions"})
        else:
            self._reply(200, result)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"ok": True})
            return
        role = self.path.removeprefix("/view/")
        if role not in SEATS or self.path != f"/view/{role}":
            self._refuse(404, "not found", True)
            return
        self._guarded(False, lambda: self._world.read(self._caller(role)))

    def do_POST(self) -> None:
        role = self.path[1:]
        if role not in SEATS:
            self._refuse(404, "not found", True)
            return

        def act() -> dict[str, object]:
            seat = self._caller(role)
            body, header = self._submission()
            return self._world.resolve(seat, body, header)

        self._guarded(True, act)


def main() -> None:
    world = World(load_scenario(SCENARIO))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOG.exists():
        raise FileExistsError("Budget log already exists")
    replace_log(world.log())
    with WorldServer(world) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
