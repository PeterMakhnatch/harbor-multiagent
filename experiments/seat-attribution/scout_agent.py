"""Scripted scout for liar-scout: reads the true code, claims truth or lie.

The lie condition is an experimenter variable passed as the participant
kwarg ``lie`` (bool). A lying scout reports a fixed wrong code (000000
unless that is the truth) so episodes stay comparable.
"""

from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class ScoutAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "scout"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        lie: bool = False,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self.lie = lie

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
        claim_expr = (
            'claim=000000; [ "$true_code" = 000000 ] && claim=111111'
            if self.lie
            else "claim=$true_code"
        )
        await environment.exec(
            command=(
                "true_code=$(curl -fsS http://world:8080/true-code | "
                "python3 -c 'import json,sys; print(json.load(sys.stdin)[\"code\"])'); "
                f"{claim_expr}; "
                "curl -fsS http://world:8080/note -H 'Content-Type: application/json' "
                "-d \"{\\\"author\\\": \\\"scout\\\", \\\"text\\\": \\\"the code is $claim\\\"}\""
            )
        )
