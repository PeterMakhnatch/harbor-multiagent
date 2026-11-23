"""Shared helpers: load Flotilla modules by path and run a real world server in a thread."""

from __future__ import annotations

import functools
import http.client
import importlib.util
import itertools
import json
import socket
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

FLOTILLA = Path(__file__).resolve().parents[1]
FAMILIES = sorted(path.stem for path in (FLOTILLA / "families").glob("*.py"))
_COUNTER = itertools.count()


def load(path: Path, name: str | None = None) -> ModuleType:
    """Import a file as a fresh module (module-level settings are read again)."""
    spec = importlib.util.spec_from_file_location(name or f"flotilla_test_{path.stem}_{next(_COUNTER)}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # as an import does: dataclasses look a module up by its name
    spec.loader.exec_module(module)
    return module


@functools.cache
def family(name: str) -> ModuleType:
    return load(FLOTILLA / "families" / f"{name}.py", f"flotilla_test_family_{name}")


@functools.cache
def instance(name: str, i: int, n_seats: int, difficulty: str, seed: int = 1) -> dict[str, Any]:
    """A generated instance, cached across tests (tests must not change it)."""
    return family(name).generate(seed, i, n_seats, difficulty)


@dataclass
class World:
    module: ModuleType
    port: int
    tokens: dict[str, str]
    admin: str
    logs: Path

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, method: str, path: str, token: str | None = None, body: Any = None,
                raw: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any, dict[str, str]]:
        """(status, parsed JSON reply or None, reply headers). Never raises for an HTTP error status. Returns only
        after the server closed the connection, so the request's handler has returned (world v11 records a delivery
        after the reply is written)."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = dict(headers or {})
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            sent.setdefault("Content-Type", "application/json")
        done = None
        try:
            connection.request(method, path, body=data, headers=sent)
            done = connection.sock.dup()
            response = connection.getresponse()
            payload = response.read()
            return response.status, (json.loads(payload) if payload else None), dict(response.getheaders())
        finally:
            connection.close()
            if done is not None:
                wait_for_close(done)

    def seat(self, seat: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, reply, _headers = self.request(method, path, token=self.tokens[seat], body=body)
        return status, reply

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (self.logs / "events.jsonl").read_text().splitlines()]

    def kinds(self) -> list[str]:
        return [event["kind"] for event in self.events()]


def wait_for_close(sock: socket.socket, limit: int = 64) -> None:
    """Read until the peer closes the connection (at most `limit` reads of 10 seconds each), then close."""
    try:
        sock.settimeout(10)
        for _ in range(limit):
            if not sock.recv(4096):
                break
    except OSError:
        pass
    finally:
        sock.close()


@pytest.fixture
def start_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """start_world(condition, seats, **env) -> World, served on a free local port until the test ends."""
    servers = []

    def start(condition: str = "talk", seats: tuple[str, ...] = ("p1", "p2"), **env: str) -> World:
        tokens = {seat: f"{seat}-token-{next(_COUNTER):04d}" for seat in seats}
        logs = tmp_path / f"world-{next(_COUNTER)}"
        monkeypatch.setenv("FLOTILLA_LOGS", str(logs))
        monkeypatch.setenv("FLOTILLA_CONDITION", condition)
        monkeypatch.setenv("FLOTILLA_SEATS", ",".join(seats))
        monkeypatch.setenv("FLOTILLA_TOKENS", ",".join(f"{seat}:{token}" for seat, token in tokens.items()))
        monkeypatch.setenv("FLOTILLA_ADMIN", "admin-token")
        for name in ("FLOTILLA_MAX_TEXT", "FLOTILLA_MAX_MESSAGES"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        module = load(FLOTILLA / "world" / "world.py")
        server = module.serve("127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return World(module, server.server_address[1], tokens, "admin-token", logs)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
