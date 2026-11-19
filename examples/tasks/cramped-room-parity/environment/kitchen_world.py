"""Serve the fixed-length Overcooked cramped_room replay on the task network."""

import json
import re
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

from overcooked_ai_py.mdp.actions import Action
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld

HORIZON = 38
LOG_PATH = Path("/logs/world/kitchen.jsonl")
ACK_LOG_PATH = Path("/logs/world/seatlab-acks.jsonl")
ACTION_SEATS = {"/action/chef0": 0, "/action/chef1": 1}
MAX_BODY_BYTES = 256
SOCKET_TIMEOUT_SECONDS = 3

NativeAction = tuple[int, int] | str


def _native_action(value: object) -> NativeAction:
    if isinstance(value, str) and value == Action.INTERACT:
        return value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        dx, dy = value
        if type(dx) is int and type(dy) is int and (dx, dy) in Action.MOTION_ACTIONS:
            return (dx, dy)
    raise ValueError("action must be a native movement, stay, or 'interact'")


def canonical(row: dict[str, object]) -> str:
    """Serialize a replay row exactly as the native rollout does, without a newline."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate JSON object member")
    return obj


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".jsonl.tmp")
    try:
        temporary.write_bytes("".join(lines).encode("utf-8"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Kitchen:
    """Replay without HTTP: pass each recorded joint_action to step in order."""

    def __init__(self) -> None:
        self._mdp = OvercookedGridworld.from_layout_name("cramped_room")
        self._state = self._mdp.get_standard_start_state()

    @property
    def tick(self) -> int:
        return self._state.timestep

    def observe(self) -> dict[str, object]:
        return {"tick": self.tick, "state": self._state.to_dict()}

    def step(self, joint_action: object) -> dict[str, object]:
        """Return one native post-transition row from a chef0, chef1 action pair."""
        if self.tick >= HORIZON:
            raise ValueError("replay is already complete")
        if not isinstance(joint_action, (list, tuple)) or len(joint_action) != 2:
            raise ValueError("joint_action must contain exactly two actions")
        actions = (_native_action(joint_action[0]), _native_action(joint_action[1]))
        next_tick = self.tick + 1
        state, info = self._mdp.get_state_transition(self._state, actions)
        if state.timestep != next_tick:
            raise RuntimeError(f"native timestep mismatch at tick {next_tick}")
        self._state = state
        sparse = info["sparse_reward_by_agent"]
        return {
            "tick": state.timestep,
            "joint_action": actions,
            "state": state.to_dict(),
            "reward": sum(sparse),
            "sparse_reward_by_agent": sparse,
            "shaped_reward_by_agent": info["shaped_reward_by_agent"],
            "soup_delivery_by_agent": info["event_infos"]["soup_delivery"],
        }


class KitchenServer(HTTPServer):
    def __init__(self) -> None:
        self.kitchen = Kitchen()
        self._pending: list[tuple[NativeAction, str] | None] = [None, None]
        self._view: tuple[dict[str, object], str] | None = None
        self._lines: list[str] = []
        self._ack_lines: list[str] = []
        super().__init__(("0.0.0.0", 8080), KitchenHandler)

    def current_view(self) -> tuple[dict[str, object], str]:
        if self._view is None:
            view = self.kitchen.observe()
            self._view = (view, sha256(canonical(view).encode("ascii")).hexdigest())
        return self._view

    def submit(
        self, seat: int, tick: int, action: NativeAction, view_sha256: str
    ) -> bool:
        if (
            tick != self.kitchen.tick
            or tick >= HORIZON
            or self._pending[seat] is not None
        ):
            return False
        view, expected_sha256 = self.current_view()
        if view_sha256 != expected_sha256:
            return False

        accepted = (action, view_sha256)
        peer = self._pending[1 - seat]
        if peer is None:
            self._pending[seat] = accepted
            return True

        chef0, chef1 = (accepted, peer) if seat == 0 else (peer, accepted)
        # Serialize the pre-action view before the native transition.
        receipt = canonical(
            {
                "tick": tick,
                "view": view,
                "ack_sha256": {"chef0": chef0[1], "chef1": chef1[1]},
            }
        )
        row = self.kitchen.step((chef0[0], chef1[0]))
        self._pending[1 - seat] = None
        self._view = None
        self._lines.append(canonical(row) + "\n")
        self._ack_lines.append(receipt + "\n")
        if self.kitchen.tick == HORIZON:
            _write_log(LOG_PATH, self._lines)
            _write_log(ACK_LOG_PATH, self._ack_lines)
        return True


class KitchenHandler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT_SECONDS

    def _reply(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply(HTTPStatus.OK, {"ok": True})
        elif self.path == "/state":
            server = cast(KitchenServer, self.server)
            view, _ = server.current_view()
            self._reply(HTTPStatus.OK, view)
        else:
            self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown route"})

    def do_POST(self) -> None:
        seat = ACTION_SEATS.get(self.path)
        if seat is None:
            self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return

        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or "Transfer-Encoding" in self.headers:
                raise ValueError("expected one Content-Length and no Transfer-Encoding")
            length = int(lengths[0])
            if not 1 <= length <= MAX_BODY_BYTES:
                raise ValueError(f"body size must be 1 to {MAX_BODY_BYTES} bytes")
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("incomplete request body")
            payload = json.loads(body, object_pairs_hook=_unique_object)
            if not isinstance(payload, dict) or payload.keys() != {
                "tick",
                "action",
                "view_sha256",
            }:
                raise ValueError(
                    "expected a JSON object with tick, action, and view_sha256"
                )
            tick = payload["tick"]
            if type(tick) is not int:
                raise ValueError("tick must be an integer")
            action = _native_action(payload["action"])
            view_sha256 = payload["view_sha256"]
            if not isinstance(view_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", view_sha256
            ):
                raise ValueError(
                    "view_sha256 must be 64 lowercase hexadecimal characters"
                )
        except ValueError as exc:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return

        server = cast(KitchenServer, self.server)
        accepted = server.submit(seat, tick, action, view_sha256)
        status = HTTPStatus.OK if accepted else HTTPStatus.CONFLICT
        self._reply(status, {"tick": server.kitchen.tick})


if __name__ == "__main__":
    with KitchenServer() as server:
        server.serve_forever()
