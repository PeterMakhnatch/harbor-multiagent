"""LogEntry.seat attribution: seat phases are tagged, everything else is not."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.verifier.result import VerifierResult
from harbor.trial.hooks import LogEntry
from harbor.trial.seats import SeatsTrial
from harbor.trial.trial import Trial


class ChattyAgent(BaseAgent):
    """Emits one line of output in setup and one in run, through its seat."""

    @staticmethod
    def name() -> str:
        return "chatty"

    def version(self) -> str:
        return "1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        await environment.exec("echo setup")

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        await environment.exec("echo run")


def _task_dir(root: Path) -> Path:
    task_dir = root / "hello"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "docker-compose.yaml").write_text(
        "services:\n  main: {}\n  p1: {}\n  p2: {}\n"
    )
    (task_dir / "instructions").mkdir()
    for seat in ("p1", "p2"):
        (task_dir / "instructions" / f"{seat}.md").write_text(f"You are {seat}.")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    (task_dir / "task.toml").write_text(
        "[agent]\ntimeout_sec = 30\n[agents.p1]\n[agents.p2]\n"
    )
    return task_dir


def test_log_entry_seat_defaults_to_none_and_serializes():
    entry = LogEntry(trial_id=uuid4(), phase="agent", stream="stdout", text="hi")
    assert entry.seat is None

    tagged = entry.model_copy(update={"seat": "chef0"})
    restored = LogEntry.model_validate_json(tagged.model_dump_json())
    assert restored.seat == "chef0"
    assert restored == tagged


async def test_seats_trial_tags_setup_and_agent_logs_with_seat_name(tmp_path: Path):
    task_dir = _task_dir(tmp_path)
    config = TrialConfig(
        task=TaskConfig(path=task_dir),
        trials_dir=tmp_path / "trials",
        participants=[
            {"seat": seat, "import_path": f"{__name__}:ChattyAgent"}
            for seat in ("p1", "p2")
        ],
    )
    trial = await Trial.create(config)
    assert isinstance(trial, SeatsTrial)

    project = trial.agent_environment

    async def service_exec(*_: Any, **__: Any) -> ExecResult:
        emit = project._output_callback()
        if emit is not None:
            await emit("line\n", "stdout")
        return ExecResult(stdout="1000\n", return_code=0)

    async def compose(*_: Any, **__: Any) -> ExecResult:
        return ExecResult(stdout="", return_code=0)

    project.service_exec = AsyncMock(side_effect=service_exec)  # type: ignore[method-assign]
    project._run_docker_compose_command = AsyncMock(side_effect=compose)  # type: ignore[method-assign]
    project.start = AsyncMock()  # type: ignore[method-assign]
    project.stop = AsyncMock()  # type: ignore[method-assign]
    trial._run_shared_verifier = AsyncMock(  # type: ignore[method-assign]
        return_value=VerifierResult(rewards={"p1": 1})
    )

    async def run_verifier() -> None:
        with trial._log_context("verification", project):
            emit = project._output_callback()
            assert emit is not None
            await emit("verified\n", "stdout")

    trial._run_verifier = run_verifier  # type: ignore[method-assign]

    entries: list[LogEntry] = []

    async def collect(entry: LogEntry) -> None:
        entries.append(entry)

    trial.add_log_callback(collect)
    await trial.run()

    for phase in ("agent_setup", "agent"):
        seats = {e.seat for e in entries if e.phase == phase}
        assert seats == {"p1", "p2"}, phase
    verification = [e for e in entries if e.phase == "verification"]
    assert verification
    assert all(e.seat is None for e in verification)


async def test_single_step_log_context_leaves_seat_none(tmp_path: Path):
    task_dir = tmp_path / "plain"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "Dockerfile").write_text("FROM ubuntu\n")
    (task_dir / "instruction.md").write_text("Do it.")
    (task_dir / "task.toml").write_text("[agent]\ntimeout_sec = 5\n")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    trial = await Trial.create(TrialConfig(task=TaskConfig(path=task_dir)))

    entries: list[LogEntry] = []

    async def collect(entry: LogEntry) -> None:
        entries.append(entry)

    trial.add_log_callback(collect)
    env = trial.agent_environment
    with trial._log_context("agent", env):
        emit = env._output_callback()
        assert emit is not None
        await emit("hello\n", "stdout")

    assert len(entries) == 1
    assert entries[0].phase == "agent"
    assert entries[0].seat is None
