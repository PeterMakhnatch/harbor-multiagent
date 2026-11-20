"""Scripted seat agents for attribution experiments. No model calls.

SleeperAgent writes the record file a naive verifier looks for, then idles.
ImpersonatorAgent posts notes as p1 and as p2, whatever its own seat is.
"""

from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class _Scripted(BaseAgent):
    def __init__(self, logs_dir: Path, model_name: str | None = None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)

    def version(self) -> str:
        return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None


class SleeperAgent(_Scripted):
    """Leaves a record file, then sleeps. Does no task work."""

    @staticmethod
    def name() -> str:
        return "sleeper"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        (self.logs_dir / "oracle.txt").write_text("sleeper left a record\n")
        await environment.exec(command="sleep 8")


class ImpersonatorAgent(_Scripted):
    """Posts the p1 note and the p2 note itself, with forged author fields."""

    @staticmethod
    def name() -> str:
        return "impersonator"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        (self.logs_dir / "oracle.txt").write_text("impersonator ran\n")
        await environment.exec(
            command=(
                "curl -fsS http://world:8080/note -H 'Content-Type: application/json' "
                "-d '{\"author\": \"p1\", \"text\": \"hello from p1\"}'; "
                "curl -fsS http://world:8080/note -H 'Content-Type: application/json' "
                "-d '{\"author\": \"p2\", \"text\": \"hello from p2\"}'"
            )
        )
