"""Authoritative HTTP world server for two-seat OpenSpiel Hanabi cross-play."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from engine import Game

CONFIG_PATH = Path(os.environ.get("HANABI_CONFIG", "/world/config.json"))
LOG_PATH = Path(os.environ.get("HANABI_LOG", "/logs/world/hanabi.json"))
TOKENS_DIR = Path(os.environ.get("HANABI_TOKENS_DIR", "/run/hanabi-tokens"))
PORT = int(os.environ.get("HANABI_PORT", "8080"))
SEATS = {"p0": 0, "p1": 1}
PLAYER_SEAT = {0: "p0", 1: "p1"}


def canonical_digest(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(data.encode("ascii")).hexdigest()


class WorldState:
    def __init__(self, deck_id: int, log_path: Path) -> None:
        self.deck_id, self.log_path = int(deck_id), log_path
        self.game = Game(self.deck_id)
        self.turns: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self._final: dict[str, Any] | None = None
        self._turn: int | None = None
        self._seat: str | None = None
        self._view: dict[str, Any] | None = None
        self._digest: str | None = None
        self._sync()

    def _sync(self) -> None:
        if self.game.player is None:
            self._turn = self._seat = self._view = self._digest = None
            if self._final is None:
                payload = {
                    "schema": "seatlab.hanabi-native/v1",
                    "deck_id": self.deck_id,
                    "deck_sha256": self.game.deck_sha256,
                    "turns": list(self.turns),
                    "scores": [int(s) for s in self.game.scores()],
                }
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.log_path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
                    + "\n",
                    encoding="utf-8",
                )
                tmp.replace(self.log_path)
                self._final = payload
            return
        self._turn, self._seat = self.game.turn, PLAYER_SEAT[self.game.player]
        self._view = self.game.view()
        self._digest = canonical_digest(self._view)

    def view_for(self, seat: str) -> dict[str, Any]:
        with self.lock:
            if self._final is not None:
                return {"status": "done", "scores": list(self._final["scores"])}
            if seat != self._seat:
                return {"status": "pending", "turn": self._turn}
            return {
                "status": "turn",
                "turn": self._turn,
                "seat": seat,
                "view": self._view,
                "view_sha256": self._digest,
            }

    def submit_action(
        self, seat: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        turn, action, digest = (
            body.get("turn"),
            body.get("action"),
            body.get("view_sha256"),
        )
        if (
            type(turn) is not int
            or type(action) is not int
            or not isinstance(digest, str)
        ):
            return 400, {"error": "require int turn, int action, str view_sha256"}
        with self.lock:
            if self._final is not None:
                return 409, {"error": "game finished"}
            if seat != self._seat:
                return 409, {"error": "not your turn", "turn": self._turn}
            if turn != self._turn:
                return 409, {"error": "turn mismatch", "expected_turn": self._turn}
            if digest != self._digest:
                return 409, {"error": "view_sha256 mismatch"}
            if self._view is None or action not in {
                int(item["id"]) for item in self._view["legal"]
            }:
                return 400, {"error": "illegal action"}
            committed_turn, committed_view = self._turn, self._view
            try:
                self.game.apply(action)
            except (ValueError, RuntimeError) as exc:
                return 400, {"error": str(exc)}
            self.turns.append(
                {
                    "turn": committed_turn,
                    "seat": seat,
                    "view": committed_view,
                    "view_sha256": digest,
                    "action": action,
                }
            )
            self._sync()
            return 200, {
                "ok": True,
                "turn": committed_turn,
                "done": self._final is not None,
            }


def write_seat_tokens(tokens_dir: Path) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for seat in SEATS:
        token = secrets.token_hex(32)
        seat_dir = tokens_dir / seat
        seat_dir.mkdir(parents=True, exist_ok=True)
        token_path = seat_dir / "token"
        tmp = seat_dir / ".token.tmp"
        tmp.write_text(token + "\n", encoding="ascii")
        os.chmod(tmp, 0o444)
        tmp.replace(token_path)
        tokens[seat] = token
    return tokens


def make_handler(
    state: WorldState, tokens: dict[str, str]
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode(
                "ascii"
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, seat: str) -> bool:
            expected = tokens.get(seat)
            header = self.headers.get("Authorization")
            if expected is None or header is None or not header.startswith("Bearer "):
                self._reply(401, {"error": "unauthorized"})
                return False
            provided = header[len("Bearer ") :]
            if not hmac.compare_digest(provided, expected):
                self._reply(401, {"error": "unauthorized"})
                return False
            return True

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._reply(200, {"ok": True})
            elif path.startswith("/view/") and path[6:] in SEATS:
                seat = path[6:]
                if self._authorized(seat):
                    self._reply(200, state.view_for(seat))
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if not (path.startswith("/action/") and path[8:] in SEATS):
                self._reply(404, {"error": "not found"})
                return
            seat = path[8:]
            if not self._authorized(seat):
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 < length <= 65536:
                    raise ValueError
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError
            except (TypeError, ValueError, UnicodeDecodeError):
                self._reply(400, {"error": "invalid JSON body"})
                return
            self._reply(*state.submit_action(seat, body))

    return Handler


def main() -> None:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if type(cfg.get("deck_id")) is not int:
        raise ValueError(f"{CONFIG_PATH} must contain integer 'deck_id'")
    state = WorldState(cfg["deck_id"], LOG_PATH)
    tokens = write_seat_tokens(TOKENS_DIR)
    ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(state, tokens)).serve_forever()


if __name__ == "__main__":
    main()
