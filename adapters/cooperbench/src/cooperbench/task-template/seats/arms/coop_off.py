"""The message service of a CooperBench seat task with messaging off.

The ``silent`` and ``solo`` arms run this in main in place of coop_server.py.
The seats keep their tokens and their coop-* commands, but every request
except ``GET /health`` gets HTTP 403 "messaging is off in this task", which
the commands print. The service still checks the token, so each refused
request is a ``denied`` event in events.jsonl that names its seat (a bad token
is a ``bad_token`` event): the analysis can count the attempts. Tokens, logs
and connection limits come from coop_server.py, which main's image holds next
to this file.

Configured as coop_server.py is (COOP_AGENTS, COOP_PORT, COOP_TOKENS_DIR,
COOP_LOG_DIR); COOP_AGENTS may name a single seat.
"""

from __future__ import annotations

import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coop_server as service  # noqa: E402

OFF = "messaging is off in this task"


def make_off_handler(board: service.Board) -> type[BaseHTTPRequestHandler]:
    base = service.make_handler(board)

    class Off(base):
        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, {"ok": True})
                return
            self._off()

        def do_POST(self) -> None:
            self._off()

        def _off(self) -> None:
            try:  # read the body first, so the client gets the reply, not a reset
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            self.rfile.read(max(0, min(length, service.MAX_BODY_BYTES)))
            seat = self._caller()
            if seat is not None:
                self._refuse(seat, 403, OFF)

    return Off


def make_server(
    agents: list[str],
    tokens_dir: Path,
    log_dir: Path,
    host: str = "0.0.0.0",
    port: int = 8080,
    **limits: int,
) -> service.Server:
    """The service with messaging off, not yet serving; ``limits`` as in
    coop_server.make_server."""
    max_connections = limits.pop("max_connections", service.MAX_CONNECTIONS)
    max_per_client = limits.pop("max_per_client", service.MAX_CONNECTIONS_PER_CLIENT)
    board = service.Board(
        agents, service.issue_tokens(agents, tokens_dir), log_dir, **limits
    )
    return service.Server(
        (host, port), make_off_handler(board), max_connections, max_per_client
    )


def main() -> None:
    agents = [a.strip() for a in os.environ.get("COOP_AGENTS", "").split(",") if a.strip()]
    if not agents:
        raise SystemExit("coop_off: set COOP_AGENTS to the seat names")
    server = make_server(
        agents,
        Path(os.environ.get("COOP_TOKENS_DIR", "/run/cooperbench/tokens")),
        Path(os.environ.get("COOP_LOG_DIR", "/logs/cooperbench")),
        port=int(os.environ.get("COOP_PORT", "8080")),
    )
    print(f"coop_off: messaging off for {agents}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
