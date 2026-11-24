"""Deterministic hello-seats attribution arms. No model calls.

Arms (task selected with SEAT_TASK, default examples/tasks/hello-seats):
  honest       oracle + oracle
  impersonate  sleeper(p1, under test, no work) + impersonator(p2 forges p1)
  lazy-support oracle(p1) + nop(p2) at 0.05x timeout

Run from the repository root with Docker up:
  uv run python experiments/seat-attribution/run_arms.py
Prints one JSON object per arm. Trial dirs land in /tmp/seat-exp-trials.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.trial.trial import Trial

TASK = Path(
    os.environ.get(
        "SEAT_TASK",
        str(Path(__file__).resolve().parents[2] / "examples/tasks/hello-seats"),
    )
)
ROOT = Path("/tmp/seat-exp-trials")


async def run_arm(name: str, participants: list[Participant], mult: float = 1.0) -> dict:
    config = TrialConfig(
        task=TaskConfig(path=TASK),
        trial_name=f"seat-exp-{name}",
        trials_dir=ROOT,
        participants=participants,
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
        timeout_multiplier=mult,
    )
    async with asyncio.timeout(600):
        trial = await Trial.create(config)
        result = await trial.run()
    seats = {}
    for seat in result.seat_results or []:
        exe = seat.agent_execution
        seats[seat.seat] = {
            "under_test": seat.under_test,
            "exception": seat.exception_info.exception_type if seat.exception_info else None,
            "started": str(exe.started_at) if exe else None,
            "finished": str(exe.finished_at) if exe else None,
        }
    return {
        "arm": name,
        "task": str(TASK),
        "rewards": result.verifier_result.rewards if result.verifier_result else None,
        "trial_exception": result.exception_info.exception_type if result.exception_info else None,
        "seats": seats,
        "trial_dir": str(trial.paths.trial_dir),
    }


ARMS = [
    (
        "honest",
        [Participant(seat="p1", name="oracle"), Participant(seat="p2", name="oracle")],
        1.0,
    ),
    (
        "impersonate",
        [
            Participant(seat="p1", import_path="agents:SleeperAgent"),
            Participant(seat="p2", import_path="agents:ImpersonatorAgent"),
        ],
        1.0,
    ),
    (
        "lazy-support",
        [Participant(seat="p1", name="oracle"), Participant(seat="p2", name="nop")],
        0.05,
    ),
]


async def main() -> None:
    for name, participants, mult in ARMS:
        try:
            print(json.dumps(await run_arm(name, participants, mult)), flush=True)
        except Exception as exc:
            print(
                json.dumps({"arm": name, "error": f"{type(exc).__name__}: {exc}"[:300]}),
                flush=True,
            )


asyncio.run(main())
