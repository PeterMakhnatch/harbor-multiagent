import asyncio
import logging
from pathlib import Path
from typing import Any, override

from examples.seatlab.exhibit_owner import (
    _NEXT,
    _PHASES,
    _arguments,
    _check_view,
    _exchange,
    _identity,
    _seat_command,
)
from examples.seatlab.exhibit_wire import encode_request, render_turn, request_stamp
from examples.seatlab.physical_dispatch.policies import depot, nominate, registrar
from examples.seatlab.visibility import local_seat
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext


class DispatchReceiver(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        owner_socket: str | None = None,
        filing: str | None = None,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if model_name is not None or extra_env or kwargs:
            raise ValueError("DispatchReceiver rejects model and environment overrides")
        if role not in _PHASES:
            raise ValueError("Invalid receiver role")
        self._endpoint: tuple[str, tuple[int, int]] | None = None
        if role == "loader":
            if not isinstance(owner_socket, str) or filing is not None:
                raise ValueError("Loader requires one owner socket and no filing")
            path = local_seat._socket_path(owner_socket)
            self._endpoint = (path, _identity(path))
        elif owner_socket is not None:
            raise ValueError("Only loader may have an owner socket")
        elif role == "depot":
            if filing not in ("other", "relevant"):
                raise ValueError("Depot requires an explicit filing policy")
        elif filing is not None:
            raise ValueError("Registrar cannot have a filing policy")
        super().__init__(logs_dir=logs_dir, logger=logger)
        self._role = role
        self._filing = filing
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-physical-dispatch-receiver"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service != self._role
        ):
            raise ValueError("Receiver bound to wrong seat")

    async def _action(
        self, phase: str, view: dict[str, Any], request: bytes
    ) -> dict[str, Any]:
        if phase == "request":
            return nominate(view)
        if phase == "registrar":
            return registrar(view)
        if phase == "depot":
            if self._filing not in ("other", "relevant"):
                raise ValueError("Missing depot filing policy")
            return depot(view, self._filing)
        if phase != "dispatch" or self._role != "loader" or self._endpoint is None:
            raise ValueError("Final dispatch requires the loader’s unused owner socket")
        path, identity = self._endpoint
        self._endpoint = None
        return await asyncio.to_thread(_exchange, path, identity, request)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if (
            self._started
            or not isinstance(environment, SeatEnvironment)
            or environment.service != self._role
        ):
            raise ValueError("Invalid or repeated receiver run")
        self._started = True
        metadata: dict[str, Any] = {
            "role": self._role,
            "accepted_actions": [],
            "accepted_requests": [],
            "phase": None,
        }
        context.metadata = metadata
        previous: dict[str, Any] | None = None
        for phase in _PHASES[self._role]:
            metadata["phase"] = phase
            view = await _seat_command(environment, ["view"], 10)
            if view["phase"] != phase:
                view = await _seat_command(environment, ["wait", phase], 300)
            _check_view(view, self._role, phase)
            if view["turn"] != self._role:
                raise ValueError("Seat turn not ready")
            request = encode_request(render_turn(instruction, view, previous))
            action = await self._action(phase, view, request)
            args = _arguments(action, view, phase)
            accepted = await _seat_command(environment, args, 10)
            _check_view(accepted, self._role, _NEXT[phase])
            metadata["accepted_actions"].append(action)
            metadata["accepted_requests"].append(request_stamp(request))
            previous = action
        metadata["phase"] = "finished"
