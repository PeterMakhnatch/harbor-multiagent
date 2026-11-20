"""Restore the public cut in a native seat and stop before fresh actions."""

import logging
from pathlib import Path
from typing import override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from . import prefix_packet, prefix_probe, prefix_seat, schedule


class PrefixProbeSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        map_index: int,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        if (
            model_name is not None
            or bool(extra_env)
            or role not in schedule.ROLES
            or type(map_index) is not int
            or map_index not in range(6)
        ):
            raise ValueError("Invalid prefix probe seat configuration")
        super().__init__(logs_dir=logs_dir, logger=logger)
        self._role = role
        self._map_index = map_index

    @staticmethod
    @override
    def name() -> str:
        return prefix_probe._AGENT_NAME

    @override
    def version(self) -> str:
        return prefix_probe._AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        prefix_seat._check_environment(environment, self._role)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        seat = prefix_seat._check_environment(environment, self._role)
        cut = prefix_probe._load_cut(self._map_index)
        receipt = prefix_probe._receipt(self._role, cut)
        run_id = prefix_probe._run_id(self._map_index)
        async with prefix_seat.restored(seat, self._role, run_id, cut) as (
            _,
            cut_status,
        ):
            if prefix_packet._canonical(cut_status) != prefix_packet._canonical(
                receipt["cut_status"]
            ):
                raise ValueError("Unexpected native prefix cut status")
        # Commit only after restoration has finished. Return without sending
        # /done or asking the world for another view of the board.
        context.metadata = receipt
