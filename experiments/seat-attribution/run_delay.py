"""Model p1 (under test, must reply to p2) vs scripted delayed p2, fixed task."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/seat_exp")

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.trial.trial import Trial

import os
TASK = Path(os.environ.get("SEAT_TASK", "/tmp/flotilla-task/examples/tasks/hello-seats"))
ROOT = Path("/tmp/seat-delay-trials")


async def run_one(name: str) -> dict:
    config = TrialConfig(
        task=TaskConfig(path=TASK),
        trial_name=f"seat-delay-{name}",
        trials_dir=ROOT,
        participants=[
            Participant(
                seat="p1",
                name="terminus-2",
                model_name="openrouter/openai/gpt-oss-120b",
                model_api_key_name="OPENROUTER_API_KEY",
            ),
            Participant(seat="p2", import_path="delay_agents:DelayedPosterAgent"),
        ],
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
        timeout_multiplier=0.5,
    )
    async with asyncio.timeout(600):
        trial = await Trial.create(config)
        result = await trial.run()
    notes = []
    for artifact in Path(trial.paths.trial_dir).rglob("notes.jsonl"):
        notes = [json.loads(line) for line in artifact.read_text().splitlines() if line.strip()]
    seats = {
        s.seat: {
            "exception": s.exception_info.exception_type if s.exception_info else None,
            "cost": s.agent_result.cost_usd if s.agent_result else None,
            "finished": str(s.agent_execution.finished_at) if s.agent_execution else None,
        }
        for s in result.seat_results or []
    }
    return {
        "arm": name,
        "rewards": result.verifier_result.rewards if result.verifier_result else None,
        "notes": notes,
        "seats": seats,
        "trial_dir": str(trial.paths.trial_dir),
    }


async def _main() -> None:
    print(json.dumps(await run_one(sys.argv[1])), flush=True)


asyncio.run(_main())
