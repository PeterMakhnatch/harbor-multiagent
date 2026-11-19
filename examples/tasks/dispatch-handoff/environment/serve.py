"""Serve seat-scoped views and publish one completed world record."""

import json
import logging
import socket
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal, cast

from seatworld import NotReady, World

Role = Literal["inventory", "dispatch"]
ROLES: tuple[Role, ...] = ("inventory", "dispatch")
EVIDENCE = Path("/logs/world/handoff.json")
MAX_SUBMISSION_BYTES = 256 * 1024
PORT = 8080


def _publish(evidence: dict[str, object]) -> None:
    if evidence.get("complete") is not True:
        raise RuntimeError("Cannot publish an incomplete handoff")
    with TemporaryDirectory(prefix=".handoff-", dir=EVIDENCE.parent) as directory:
        staged = Path(directory) / EVIDENCE.name
        staged.write_text(
            json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        staged.chmod(0o444)
        EVIDENCE.hardlink_to(staged)  # Publish atomically and never replace a record.


class WorldServer(ThreadingHTTPServer):
    def __init__(self, world: World) -> None:
        self.world = world
        self.lock = Lock()
        self.published = False
        # None marks an accepted legacy submission, not an unsubmitted role.
        self._accepted_acks: dict[Role, str | None] = {}
        super().__init__(("0.0.0.0", PORT), Handler)

    def submit(self, role: Role, body: object, ack_sha256: str | None = None) -> object:
        with self.lock:
            if ack_sha256 is not None:
                canonical = json.dumps(
                    self.world.view(role),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("utf-8")
                if ack_sha256 != sha256(canonical).hexdigest():
                    raise ValueError("Acknowledgment does not match the current view")
            if role in self._accepted_acks and self._accepted_acks[role] != ack_sha256:
                raise ValueError("Cannot change an accepted acknowledgment")
            result = self.world.submit(role, body)
            self._accepted_acks.setdefault(role, ack_sha256)
            if role == "dispatch" and not self.published:
                evidence = self.world.evidence()
                evidence["observation_receipts"] = {
                    seat: {"ack_sha256": accepted}
                    for seat, accepted in self._accepted_acks.items()
                    if accepted is not None
                }
                _publish(evidence)
                self.published = True
            return result


class Handler(BaseHTTPRequestHandler):
    timeout = 5.0

    @property
    def _world_server(self) -> WorldServer:
        return cast(WorldServer, self.server)

    def _role(self) -> Role | None:
        # Resolve per request: seats start only after main is healthy.
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
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
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
            raise ValueError("Expected a length-delimited JSON body")
        length = int(lengths[0])
        if not 0 < length <= MAX_SUBMISSION_BYTES:
            raise ValueError("Body length is out of bounds")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete body")
        return json.loads(body.decode("utf-8"))

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
            server = self._world_server
            with server.lock:
                view = server.world.view(role)
        except NotReady:
            self._reply(409, {"error": "waiting for inventory"})
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
        acks = self.headers.get_all("X-SeatLab-View-SHA256", [])
        if len(acks) > 1:
            self._reply(400, {"error": "expected at most one view acknowledgment"})
            return
        try:
            body = self._submission()
        except (OSError, RecursionError, ValueError):
            self._reply(400, {"error": "invalid JSON body"})
            return
        try:
            result = self._world_server.submit(role, body, acks[0] if acks else None)
        except NotReady:
            self._reply(409, {"error": "waiting for inventory"})
        except ValueError:
            self._reply(400, {"error": "invalid seat submission"})
        except OSError:
            logging.exception("Could not publish the handoff")
            self._reply(500, {"error": "could not publish the handoff"})
        else:
            self._reply(200, result)


def main() -> None:
    settings = json.loads(Path(__file__).with_name("scenario.json").read_text("utf-8"))
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        raise FileExistsError(f"World record already exists: {EVIDENCE}")
    with WorldServer(World(settings)) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
