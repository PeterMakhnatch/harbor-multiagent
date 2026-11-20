"""Support seat that posts its note after a fixed delay (token-auth world)."""

import os
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

DELAY = int(os.environ.get("SEAT_DELAY_SEC", "0"))


class DelayedPosterAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "delayed-poster"

    def __init__(self, logs_dir: Path, model_name: str | None = None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        await environment.exec(
            command=(
                f"sleep {DELAY}; "
                "curl -fsS http://world:8080/note -H 'Content-Type: application/json' "
                "-H \"Authorization: Bearer $(cat /run/seat/token)\" "
                "-d '{\"text\": \"hello from p2\"}'"
            )
        )
