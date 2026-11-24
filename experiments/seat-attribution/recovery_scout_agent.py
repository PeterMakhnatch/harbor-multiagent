"""Recovery-protocol scout: lies first, corrects itself if challenged.

Posts a false code, then polls the notes board for a listener complaint
(any note authored "listener"). On complaint it posts the true code as a
correction. Without a complaint it never corrects. Used with a listener
instruction that defines the recovery move (verify, complain, wait).
"""

from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class RecoveryScoutAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "recovery-scout"

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
                "true_code=$(curl -fsS http://world:8080/true-code | "
                "python3 -c 'import json,sys; print(json.load(sys.stdin)[\"code\"])'); "
                "claim=000000; [ \"$true_code\" = 000000 ] && claim=111111; "
                "curl -fsS http://world:8080/note -H 'Content-Type: application/json' "
                "-d \"{\\\"author\\\": \\\"scout\\\", \\\"text\\\": \\\"the code is $claim\\\"}\" >/dev/null; "
                "for i in $(seq 1 120); do "
                "if curl -fsS http://world:8080/notes | grep -q '\"author\": \"listener\"'; then "
                "curl -fsS http://world:8080/note -H 'Content-Type: application/json' "
                "-d \"{\\\"author\\\": \\\"scout\\\", \\\"text\\\": \\\"correction: the code is $true_code\\\"}\" >/dev/null; "
                "break; fi; sleep 2; done"
            )
        )
