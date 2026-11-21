import json
from pathlib import Path
from typing import override

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext


class ConflictAgent(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return "agentsnet-coloring-conflict"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if not isinstance(environment, SeatEnvironment):
            raise ValueError("ConflictAgent requires a native seat")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        script = Path(__file__).resolve().parent / "solution/solve.sh"
        remote_script = "/tmp/agentsnet-conflict-solve.sh"
        await environment.upload_file(source_path=script, target_path=remote_script)
        result = await environment.exec(
            command=f"bash {remote_script} conflict", timeout_sec=120
        )
        (self.logs_dir / "conflict.json").write_text(
            json.dumps(
                {
                    "return_code": result.return_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        context.metadata = {"control": "conflict"}
        if result.return_code != 0:
            raise NonZeroAgentExitCodeError("AgentsNet conflict solution failed")
