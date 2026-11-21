import asyncio
import logging
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from .. import native_model_agent, seat_agent
from . import dispatch_mix_plan as plan
from . import native_pairs

_CELLS = {cell["cell_id"]: cell for cell in plan.grid()}
_SYSTEM = "Answer only with JSON."


class MixedSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str,
        *,
        run_id: str,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(run_id, str) or run_id not in _CELLS:
            raise ValueError("Mixed seat requires a registered cell id")
        if model_name not in (plan.CLAUDE, plan.DIRECT) or bool(extra_env) or kwargs:
            raise ValueError("Mixed seat requires a declared model without overrides")
        super().__init__(logs_dir=logs_dir, model_name=model_name, logger=logger)
        self._cell = _CELLS[run_id]
        self._declared_model = model_name
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        provider, name = model_name.split("/", 1)
        return provider, name

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-mixed-model"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Mixed seat already started")
        self._started = True
        metadata: dict[str, Any] = {
            "schema": native_pairs._SEAT_SCHEMA,
            "run_id": self._cell["cell_id"],
            "role": None,
            "requested_model": self._declared_model.split("/", 1)[1],
            "served_model": None,
            "stage": "waiting_view",
            "model_turns": 0,
            "view_sha256": None,
            "prompt_sha256": None,
            "decision_sha256": None,
        }
        context.metadata = metadata
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service not in plan.ROLES
        ):
            native_model_agent._fail(metadata, "invalid_seat")
        role = environment.service
        metadata["role"] = role
        if plan.model_for(self._cell, role) != self._declared_model:
            native_model_agent._fail(metadata, "invalid_roster")
        digest, prompt = await native_pairs._prepare_view(
            environment, role, plan.EPISODES, (self._cell["condition"],), metadata
        )
        if self._declared_model == plan.DIRECT:
            response = await native_pairs._ask_once(prompt, role, metadata, context)
        else:
            await native_model_agent._verify_route(self.context_id, metadata)
            metadata["stage"] = "generation"
            response = await asyncio.to_thread(
                native_model_agent._generate_text,
                _SYSTEM,
                prompt.encode("utf-8"),
                context,
                metadata,
            )
        await native_pairs._submit_response(
            environment, role, plan.EPISODES, digest, response, metadata
        )
