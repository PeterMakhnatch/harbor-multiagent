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

SALES = "sales"
FINANCE = ("finance_a", "finance_b")
SEATS = (SALES, *FINANCE)
PICKS = (1, 2, 3)
SCENARIO = Path("/world/scenario.json")
SCENARIO_SCHEMA = "seatlab.price-timing-scenario/v1"
SCENARIO_KEYS = frozenset({"schema", "early", "checkpoint_sha256", "sales", "finance"})
EVIDENCE = Path("/logs/world/timing.json")
JOURNAL = Path("/logs/world/timing-journal.json")
EVIDENCE_SCHEMA = "seatlab.price-timing-native/v1"
JOURNAL_SCHEMA = "seatlab.price-timing-journal/v1"
TOKEN_DOMAIN = "seatlab.price-timing-receipt/v1"
DIGEST_HEADER = "X-SeatLab-View-SHA256"
HEX = frozenset("0123456789abcdef")
TOKEN_CHARS = 12
REASON_MAX_CHARS = 160
MAX_SCENARIO_BYTES = 1 << 22
MAX_SUBMISSION_BYTES = 4096
MAX_EVENTS = 10
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
    if scenario["schema"] != SCENARIO_SCHEMA or scenario["early"] not in FINANCE:
        _fail("scenario schema or early seat is invalid")
    if not _is_hex(scenario["checkpoint_sha256"], 64):
        _fail("scenario checkpoint digest is invalid")
    if not all(
        isinstance(scenario[key], dict) and scenario[key]
        for key in ("sales", "finance")
    ):
        _fail("scenario observations are invalid")
    if canonical(scenario) != data:
        _fail("scenario is not canonical")
    return scenario


def _pick(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in PICKS:
        _fail("pick is out of range")
    return value


def _report(body: object) -> dict[str, object]:
    record = _object(body, frozenset({"pick", "reason"}))
    reason = record["reason"]
    if not isinstance(reason, str) or not 0 < len(reason) <= REASON_MAX_CHARS:
        _fail("reason length is out of range")
    if not reason.isprintable():
        _fail("reason has unprintable characters")
    return {"pick": _pick(record["pick"]), "reason": reason}


def _price(body: object) -> int:
    return _pick(_object(body, frozenset({"pick"}))["pick"])


def _ack(body: object) -> str:
    token = _object(body, frozenset({"ack"}))["ack"]
    return (
        cast(str, token)
        if _is_hex(token, TOKEN_CHARS)
        else _fail("ack token is invalid")
    )


def _header_digest(headers: list[str]) -> str:
    if len(headers) != 1 or not _is_hex(headers[0], 64):
        _fail("expected one lowercase SHA-256 view digest")
    return headers[0]


def token(
    checkpoint: str, seat: str, pick: int, view_sha256: str, report: object
) -> str:
    if seat not in FINANCE or pick not in PICKS or not _is_hex(view_sha256, 64):
        raise AssertionError("token inputs are invalid")
    return digest(
        {
            "domain": TOKEN_DOMAIN,
            "checkpoint_sha256": checkpoint,
            "seat": seat,
            "pick": pick,
            "view_sha256": view_sha256,
            "report": report,
        }
    )[:TOKEN_CHARS]


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_journal(record: dict[str, object]) -> None:
    with NamedTemporaryFile(
        dir=JOURNAL.parent, prefix=".journal-", delete=False
    ) as staged:
        staged.write(canonical(record))
        staged.flush()
        os.fchmod(staged.fileno(), 0o444)
        os.fsync(staged.fileno())
    os.replace(staged.name, JOURNAL)
    _fsync_directory(JOURNAL.parent)


def publish_evidence(record: dict[str, object]) -> None:
    with NamedTemporaryFile(
        dir=EVIDENCE.parent, prefix=".timing-", delete=False
    ) as staged:
        staged.write(canonical(record))
        staged.flush()
        os.fchmod(staged.fileno(), 0o444)
        os.fsync(staged.fileno())
    try:
        os.link(staged.name, EVIDENCE)
    finally:
        os.unlink(staged.name)
    _fsync_directory(EVIDENCE.parent)


class World:
    def __init__(self, scenario: dict[str, object]) -> None:
        self.checkpoint = cast(str, scenario["checkpoint_sha256"])
        self.early = cast(str, scenario["early"])
        self.observations = {SALES: scenario["sales"], "finance": scenario["finance"]}
        self.lock = Lock()
        self.broken = False
        self.report: dict[str, object] | None = None
        self.events: list[dict[str, object]] = []
        self.seen: dict[tuple[str, str], str] = {}
        self.picks: dict[str, int] = {}
        self.acknowledged: list[str] = []

    def journal(self) -> dict[str, object]:
        return {
            "schema": JOURNAL_SCHEMA,
            "checkpoint_sha256": self.checkpoint,
            "early": self.early,
            "report": self.report,
            "events": self.events,
            "picks": self.picks,
            "acknowledged": sorted(self.acknowledged),
        }

    def evidence(self) -> dict[str, object]:
        if sorted(self.acknowledged) != list(FINANCE) or self.report is None:
            raise AssertionError("evidence requires both acknowledgements")
        return {
            "schema": EVIDENCE_SCHEMA,
            "early": self.early,
            "checkpoint_sha256": self.checkpoint,
            "report": self.report,
            "events": self.events,
            "picks": {seat: self.picks[seat] for seat in FINANCE},
            "acknowledged": list(FINANCE),
        }

    def _record(
        self,
        seat: str,
        event: str,
        view_sha256: str,
        apply: dict[str, object] | None = None,
    ) -> None:
        if self.broken:
            raise Broken
        if len(self.events) >= MAX_EVENTS or (seat, event) in self.seen:
            raise AssertionError("event table is full or repeated")
        row: dict[str, object] = {
            "n": len(self.events) + 1,
            "seat": seat,
            "event": event,
            "view_sha256": view_sha256,
        }
        draft = self.journal()
        draft["events"] = [*self.events, row]
        draft.update(apply or {})
        try:
            replace_journal(draft)
        except OSError:
            self.broken = True
            raise
        self.events.append(row)
        self.seen[(seat, event)] = view_sha256

    def view(self, seat: str) -> dict[str, object]:
        if seat == SALES:
            return {"observation": self.observations[SALES]}
        if self.report is None:
            raise Conflict("waiting for sales")
        slot = self.report if seat == self.early else None
        return {"observation": self.observations["finance"], "report": slot}

    def receipt(self, seat: str) -> dict[str, object]:
        if seat not in FINANCE:
            raise Forbidden("finance only")
        if seat not in self.picks:
            raise Conflict("price not committed")
        commit = self.seen[(seat, "commit")]
        slot = None if seat == self.early else self.report
        return {
            "report": slot,
            "token": token(
                self.checkpoint, seat, self.picks[seat], commit, self.report
            ),
        }

    def read(self, seat: str, event: str) -> dict[str, object]:
        with self.lock:
            if self.broken:
                raise Broken
            body = self.view(seat) if event == "view" else self.receipt(seat)
            if (seat, event) not in self.seen:
                self._record(seat, event, digest(body))
            elif self.seen[(seat, event)] != digest(body):
                raise AssertionError("a materialized body changed")
            return body

    def submit(self, seat: str, body: object, header: str) -> None:
        with self.lock:
            if self.broken:
                raise Broken
            if (seat, "commit") in self.seen:
                raise Conflict("already submitted")
            if (seat, "view") not in self.seen:
                raise Conflict("read the view first")
            if header != self.seen[(seat, "view")]:
                _fail("digest does not match the view")
            if seat == SALES:
                report = _report(body)
                self._record(seat, "commit", header, {"report": report})
                self.report = report
                return
            pick = _price(body)
            self._record(seat, "commit", header, {"picks": {**self.picks, seat: pick}})
            self.picks[seat] = pick

    def ack(self, seat: str, body: object, header: str) -> None:
        with self.lock:
            if self.broken:
                raise Broken
            if seat not in FINANCE:
                raise Forbidden("finance only")
            if (seat, "ack") in self.seen:
                raise Conflict("already acknowledged")
            if (seat, "receipt") not in self.seen:
                raise Conflict("read the receipt first")
            if header != self.seen[(seat, "receipt")]:
                _fail("digest does not match the receipt")
            if _ack(body) != self.receipt(seat)["token"]:
                _fail("ack token does not match the receipt")
            done = sorted([*self.acknowledged, seat])
            self._record(seat, "ack", header, {"acknowledged": done})
            self.acknowledged.append(seat)
            if done == list(FINANCE):
                try:
                    publish_evidence(self.evidence())
                except OSError:
                    self.broken = True
                    raise


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

    def _seat(self) -> str | None:
        return seat_addresses().get(self.client_address[0])

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
        body = self.rfile.read(length)
        if len(body) != length:
            _fail("incomplete body")
        return decode(body), header

    def _guarded(self, action: str, call: Callable[[], object]) -> None:
        try:
            result = call()
        except Conflict as error:
            self._reply(409, {"error": str(error)})
        except Forbidden as error:
            self._reply(403, {"error": str(error)})
        except (ValueError, RecursionError):
            self._reply(400, {"error": f"invalid {action}"})
        except (Broken, OSError, AssertionError):
            self._world.broken = True
            logging.getLogger(__name__).exception(
                "Timing world cannot record %s", action
            )
            self._reply(500, {"error": "the world cannot record actions"})
        else:
            self._reply(200, result if isinstance(result, dict) else {"accepted": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"ok": True})
            return
        if self.path not in ("/view", "/receipt"):
            self._reply(404, {"error": "not found"})
            return
        seat = self._seat()
        if seat is None:
            self._reply(403, {"error": "seat required"})
            return
        event = "view" if self.path == "/view" else "receipt"
        self._guarded(event, lambda: self._world.read(seat, event))

    def do_POST(self) -> None:
        if self.path not in ("/submit", "/ack"):
            self._reply(404, {"error": "not found"})
            return
        seat = self._seat()
        if seat is None:
            self._reply(403, {"error": "seat required"})
            return
        world = self._world

        def act() -> None:
            try:
                body, header = self._submission()
            except OSError:
                _fail("unreadable body")
            if self.path == "/submit":
                world.submit(seat, body, header)
            else:
                world.ack(seat, body, header)

        self._guarded(self.path[1:], act)


def main() -> None:
    world = World(load_scenario(SCENARIO))
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists() or JOURNAL.exists():
        raise FileExistsError("Timing records already exist")
    replace_journal(world.journal())
    with WorldServer(world) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
