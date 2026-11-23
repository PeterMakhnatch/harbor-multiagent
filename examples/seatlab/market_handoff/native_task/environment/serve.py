"""Serve one guarded SeatLab market world to its sales and finance seats."""

import json
import logging
import math
import socket
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal, NoReturn, cast

Role = Literal["sales", "finance"]
ROLES: tuple[Role, ...] = ("sales", "finance")
PICKS = (1, 2, 3)
SCENARIO = Path("/world/scenario.json")
SCENARIO_SCHEMA = "seatlab.market-native-scenario/v1"
SCENARIO_KEYS = frozenset({"schema", "completed_days", "sales", "finance"})
EVIDENCE = Path("/logs/world/market.json")
EVIDENCE_SCHEMA = "seatlab.market-native-evidence/v1"
DIGEST_HEADER = "X-SeatLab-View-SHA256"
HEX = frozenset("0123456789abcdef")
MAX_SCENARIO_BYTES = 1 << 22
MAX_SUBMISSION_BYTES = 1024
MAX_DAYS = 90
PORT = 8080


class NotReady(Exception):
    pass


class Repeated(Exception):
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


def _object(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail("expected a JSON object")
    items = cast(dict[object, object], value).items()
    result = {key: item for key, item in items if isinstance(key, str)}
    if len(result) != len(items) or set(result) != keys:
        _fail("JSON object keys are invalid")
    return result


def load_scenario(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        data = handle.read(MAX_SCENARIO_BYTES + 1)
    if not 0 < len(data) <= MAX_SCENARIO_BYTES:
        _fail("scenario size is out of bounds")
    scenario = _object(decode(data), SCENARIO_KEYS)
    if scenario["schema"] != SCENARIO_SCHEMA:
        _fail("scenario schema is invalid")
    days = _object(scenario["completed_days"], frozenset(ROLES))
    for count in days.values():
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_DAYS:
            _fail("scenario completed_days is out of range")
    if not all(isinstance(scenario[role], dict) for role in ROLES):
        _fail("scenario observations are invalid")
    canonical(scenario)
    return scenario


def _pick(body: object) -> int:
    pick = _object(body, frozenset({"pick"}))["pick"]
    if isinstance(pick, bool) or not isinstance(pick, int) or pick not in PICKS:
        _fail("pick is out of range")
    return pick


def _digest(headers: list[str]) -> str:
    if len(headers) != 1 or len(headers[0]) != 64 or not set(headers[0]) <= HEX:
        _fail("expected one lowercase SHA-256 view digest")
    return headers[0]


def _publish(evidence: dict[str, object]) -> None:
    if set(evidence) != {"schema", "sales", "finance"}:
        raise RuntimeError("Cannot publish an incomplete market record")
    with TemporaryDirectory(prefix=".market-", dir=EVIDENCE.parent) as directory:
        staged = Path(directory) / EVIDENCE.name
        staged.write_bytes(canonical(evidence))
        staged.chmod(0o444)
        EVIDENCE.hardlink_to(staged)


class World:
    def __init__(self, scenario: dict[str, object]) -> None:
        self.scenario = scenario
        self.lock = Lock()
        self.accepted: dict[Role, dict[str, object]] = {}

    def view(self, role: Role) -> dict[str, object]:
        days = cast(dict[Role, int], self.scenario["completed_days"])
        view: dict[str, object] = {
            "role": role,
            "completed_days": days[role],
            "observation": self.scenario[role],
        }
        if role == "finance":
            sales = self.accepted.get("sales")
            if sales is None:
                raise NotReady
            view["advice"] = sales["pick"]
        return view

    def submit(self, role: Role, body: object, digest: str) -> None:
        with self.lock:
            if role in self.accepted:
                raise Repeated
            view_sha256 = sha256(canonical(self.view(role))).hexdigest()
            pick = _pick(body)
            if digest != view_sha256:
                _fail("digest does not match the current view")
            receipt: dict[str, object] = {"pick": pick, "view_sha256": view_sha256}
            if role == "finance":
                _publish({"schema": EVIDENCE_SCHEMA, "sales": self.accepted["sales"], "finance": receipt})
            self.accepted[role] = receipt


class WorldServer(ThreadingHTTPServer):
    def __init__(self, world: World) -> None:
        self.world = world
        super().__init__(("0.0.0.0", PORT), Handler)


class Handler(BaseHTTPRequestHandler):
    timeout = 5.0

    @property
    def _world(self) -> World:
        return cast(WorldServer, self.server).world

    def _role(self) -> Role | None:
        matches: list[Role] = []
        for role in ROLES:
            try:
                addresses = socket.gethostbyname_ex(role)[2]
            except socket.gaierror:
                continue
            if self.client_address[0] in addresses:
                matches.append(role)
        return matches[0] if len(matches) == 1 else None

    def _reply(self, status: int, body: object) -> None:
        data = canonical(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _submission(self) -> object:
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
        return decode(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(200, {"ok": True})
            return
        if self.path != "/view":
            self._reply(404, {"error": "not found"})
            return
        role = self._role()
        if role is None:
            self._reply(403, {"error": "seat required"})
            return
        try:
            with self._world.lock:
                view = self._world.view(role)
        except NotReady:
            self._reply(409, {"error": "waiting for sales"})
        else:
            self._reply(200, view)

    def do_POST(self) -> None:
        if self.path != "/submit":
            self._reply(404, {"error": "not found"})
            return
        role = self._role()
        if role is None:
            self._reply(403, {"error": "seat required"})
            return
        try:
            digest = _digest(self.headers.get_all(DIGEST_HEADER, []))
            body = self._submission()
        except (OSError, RecursionError, ValueError):
            self._reply(400, {"error": "invalid submission"})
            return
        try:
            self._world.submit(role, body, digest)
        except Repeated:
            self._reply(409, {"error": "already submitted"})
        except NotReady:
            self._reply(409, {"error": "waiting for sales"})
        except ValueError:
            self._reply(400, {"error": "invalid submission"})
        except OSError:
            logging.getLogger(__name__).exception("Could not publish the market record")
            self._reply(500, {"error": "could not publish the market record"})
        else:
            self._reply(200, {"accepted": True})


def main() -> None:
    world = World(load_scenario(SCENARIO))
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        raise FileExistsError(f"Market record already exists: {EVIDENCE}")
    with WorldServer(world) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
