import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.models.agent.context import AgentContext

from . import evidence_join, protocol
from .agent import DECLARED_MODEL, AttentionSeat

_CELLS = {cell["cell_id"]: cell for cell in evidence_join.grid()}


class JoinSeat(AttentionSeat):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        cell_id: str,
        transport: str,
        command_config: str | None = None,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(cell_id, str) or cell_id not in _CELLS:
            raise ValueError("Join seat requires a registered cell id")
        if extra_env or kwargs:
            raise ValueError("Join seat does not accept agent overrides")
        if transport == "direct":
            if model_name not in (None, DECLARED_MODEL) or command_config is not None:
                raise ValueError("Invalid direct join transport config")
            declared = DECLARED_MODEL
            config_path = None
        elif transport == "commands":
            if (
                model_name is not None
                or not isinstance(command_config, str)
                or not command_config
            ):
                raise ValueError(
                    "Command join transport requires a command config and no model"
                )
            declared = None
            config_path = Path(command_config)
        else:
            raise ValueError("Unknown join transport")
        BaseAgent.__init__(self, logs_dir=logs_dir, model_name=declared, logger=logger)
        self._cell = _CELLS[cell_id]
        self._command_config = config_path
        self._command: Callable[[str], str] | None = None
        self._started = False

    @override
    async def _decisions(
        self,
        cases: list[dict[str, Any]],
        role: str,
        metadata: dict[str, Any],
        context: AgentContext,
    ) -> dict[str, Any]:
        if role == "inventory":
            return await super()._decisions(cases, role, metadata, context)
        seed, rep = self._cell["seed"], self._cell["repetition"]
        prompt = protocol.select_prompt(cases, seed, rep, "named")
        text = await self._ask(prompt, "select", role, metadata, context)
        opened = protocol.select(text)
        metadata["opened"] = list(opened)
        prompt = evidence_join.project(
            protocol.decide_prompt(cases, seed, rep, opened), self._cell["arm"]
        )
        text = await self._ask(prompt, "decide", role, metadata, context)
        return protocol.decide(text, cases, seed, rep)
