import asyncio
import logging
import os
import shlex
import socket
import stat
import time
from pathlib import Path
from typing import Any, override

from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp
from examples.seatlab.visibility import local_seat
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_PHASES = {
    "loader": ("request", "dispatch"),
    "registrar": ("registrar",),
    "depot": ("depot",),
}
_NEXT = {
    "request": "registrar",
    "registrar": "depot",
    "depot": "dispatch",
    "dispatch": "done",
}
_VIEW_KEYS = {
    "role",
    "phase",
    "turn",
    "quota",
    "nominate",
    "exhibits",
    "crates",
    "bays",
    "briefs",
    "cards",
    "agenda",
    "registrar_file",
    "depot_file",
    "order",
}
_LIMIT = 65536


def _identity(path: str) -> tuple[int, int]:
    status = os.lstat(path)
    if (
        not stat.S_ISSOCK(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o600
    ):
        raise ValueError("Invalid owner socket")
    return status.st_dev, status.st_ino


def _check_pin(path: str, identity: tuple[int, int], *, consumed: bool) -> None:
    try:
        current = _identity(path)
    except FileNotFoundError:
        if consumed:
            return
        raise
    if current != identity:
        raise ValueError("Owner socket changed")


def _exchange(path: str, identity: tuple[int, int], request: bytes) -> dict[str, Any]:
    local_seat._check_request(request)
    _check_pin(path, identity, consumed=False)
    deadline = time.monotonic() + 86
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(local_seat._remaining(deadline))
        conn.connect(path)
        _check_pin(path, identity, consumed=True)
        before = os.fstat(conn.fileno())
        conn.settimeout(local_seat._remaining(deadline))
        conn.sendall(request)

        def receive(size: int) -> bytes:
            conn.settimeout(local_seat._remaining(deadline))
            return conn.recv(size)

        reply = local_seat._read_bounded(receive, local_seat._MAX_REPLY_BYTES)
        after = os.fstat(conn.fileno())
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("Owner connection changed")
        _check_pin(path, identity, consumed=True)
    local_seat._check_reply(reply)
    text = local_seat._load_line(reply, local_seat._MAX_REPLY_BYTES)["text"]
    if not isinstance(text, str):
        raise TypeError("Invalid owner reply")
    return local_seat._load_line(
        text.strip().encode() + b"\n", local_seat._MAX_TEXT_BYTES + 1
    )


async def _seat_command(
    environment: SeatEnvironment, arguments: list[str], timeout: int
) -> dict[str, Any]:
    command = "python3 /app/relay.py " + " ".join(shlex.quote(arg) for arg in arguments)
    result = await environment.exec(command, timeout_sec=timeout)
    if result.return_code != 0 or result.stdout is None:
        raise RuntimeError("Relay seat command failed")
    return local_seat._load_line(result.stdout.strip().encode() + b"\n", _LIMIT)


def _check_view(view: dict[str, Any], role: str, phase: str) -> None:
    if (
        view.keys() not in (_VIEW_KEYS, _VIEW_KEYS | {"bundles"})
        or view["role"] != role
        or view["phase"] != phase
    ):
        raise ValueError("Invalid role view or phase")


def _safe_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and not value.startswith("-")
        and all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value)
    )


def _listing(
    action: dict[str, Any], op: str, field: str, allowed: list[str], cap: int
) -> list[str]:
    values = action.get(field)
    if (
        action.keys() != {"op", field}
        or action["op"] != op
        or type(cap) is not int
        or not 0 <= cap <= 40
        or not isinstance(values, list)
        or len(values) > cap
        or any(not _safe_id(value) or value not in allowed for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError("Invalid owner listing")
    return [op, *values]


def _arguments(action: dict[str, Any], view: dict[str, Any], phase: str) -> list[str]:
    if phase == "request":
        return _listing(
            action,
            "request",
            "slips",
            [brief["id"] for brief in view["briefs"]],
            view["nominate"],
        )
    if phase in ("registrar", "depot"):
        return _listing(
            action, "file", "ids", [card["id"] for card in view["cards"]], view["quota"]
        )
    routes = action.get("routes")
    if (
        action.keys() != {"op", "routes"}
        or action["op"] != "commit"
        or not isinstance(routes, dict)
        or len(routes) != 2
        or routes.keys() != set(view["order"]["items"])
        or any(
            not _safe_id(item) or not _safe_id(bay) or bay not in view["bays"]
            for item, bay in routes.items()
        )
    ):
        raise ValueError("Invalid owner routes")
    return ["commit", *(f"{item}={bay}" for item, bay in sorted(routes.items()))]


class RelayOwner(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        owner_sockets: list[str],
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if model_name is not None or extra_env or kwargs:
            raise ValueError(
                "RelayOwner accepts only explicit owner sockets, not model or environment overrides"
            )
        if (
            role not in _PHASES
            or not isinstance(owner_sockets, list)
            or len(owner_sockets) != len(_PHASES[role])
        ):
            raise ValueError("Expected one explicit owner socket per role turn")
        if any(not isinstance(path, str) for path in owner_sockets):
            raise ValueError("Owner sockets must be absolute path strings")
        paths = tuple(local_seat._socket_path(path) for path in owner_sockets)
        pins = tuple(_identity(path) for path in paths)
        if len(set(paths)) != len(paths) or len(set(pins)) != len(pins):
            raise ValueError("Owner sockets must be distinct per turn")
        super().__init__(logs_dir=logs_dir, logger=logger)
        self._role = role
        self._endpoints = tuple(zip(paths, pins, strict=True))
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-exhibit-relay-owner"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service != self._role
        ):
            raise ValueError("Owner bound to wrong seat")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if (
            self._started
            or not isinstance(environment, SeatEnvironment)
            or environment.service != self._role
        ):
            raise ValueError("Invalid or repeated owner run")
        self._started = True
        metadata: dict[str, Any] = {
            "role": self._role,
            "accepted_actions": [],
            "accepted_requests": [],
            "phase": None,
        }
        context.metadata = metadata
        previous = None
        for phase, (path, identity) in zip(
            _PHASES[self._role], self._endpoints, strict=True
        ):
            metadata["phase"] = phase
            view = await _seat_command(environment, ["view"], 10)
            if view["phase"] != phase:
                view = await _seat_command(environment, ["wait", phase], 300)
            _check_view(view, self._role, phase)
            if view["turn"] != self._role:
                raise ValueError("Seat turn not ready")
            request = encode_request(render_turn(instruction, view, previous))
            action = await asyncio.to_thread(_exchange, path, identity, request)
            args = _arguments(action, view, phase)
            accepted = await _seat_command(environment, args, 10)
            _check_view(accepted, self._role, _NEXT[phase])
            metadata["accepted_actions"].append(action)
            metadata["accepted_requests"].append(request_stamp(request))
            previous = action
        metadata["phase"] = "finished"
