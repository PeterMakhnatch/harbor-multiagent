"""Recovery arms for liar-scout: can the listener repair a false claim?

The scout lies first and corrects itself only if the listener posts a
complaint note. Listener instruction comes from the task copy named by
SEAT_TASK (recovery wording) so the base task stays untouched.

Usage: SEAT_TASK=/path/to/liar-scout-recovery uv run python \
  experiments/seat-attribution/run_recovery.py [n]
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

TASK = Path(os.environ.get("SEAT_TASK", Path(__file__).parent / "tasks/liar-scout"))
ROOT = Path("/tmp/liar-scout-trials")


async def run_one(name: str) -> dict:
    config = TrialConfig(
        task=TaskConfig(path=TASK),
        trial_name=f"liar-{name}",
        trials_dir=ROOT,
        participants=[
            Participant(
                seat="listener",
                name="terminus-2",
                model_name="openrouter/openai/gpt-oss-120b",
                model_api_key_name="OPENROUTER_API_KEY",
            ),
            Participant(seat="scout", import_path="recovery_scout_agent:RecoveryScoutAgent"),
        ],
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
        timeout_multiplier=0.5,
    )
    async with asyncio.timeout(600):
        trial = await Trial.create(config)
        result = await trial.run()
    events = []
    for artifact in Path(trial.paths.trial_dir).rglob("events.jsonl"):
        events = [
            json.loads(line) for line in artifact.read_text().splitlines() if line.strip()
        ]
    kinds = [e.get("event") for e in events]
    notes = [e for e in events if e.get("event") == "note"]
    listener_seat = next(
        (s for s in result.seat_results or [] if s.seat == "listener"), None
    )
    return {
        "episode": name,
        "rewards": result.verifier_result.rewards if result.verifier_result else None,
        "notes": [(n.get("author"), n.get("text")) for n in notes],
        "verify_calls": kinds.count("verify"),
        "answered": "answer" in kinds,
        "listener_cost": (
            listener_seat.agent_result.cost_usd
            if listener_seat and listener_seat.agent_result
            else None
        ),
        "trial_dir": str(trial.paths.trial_dir),
    }


async def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    for i in range(count):
        try:
            print(json.dumps(await run_one(f"recovery{i}")), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {"episode": f"recovery{i}", "error": f"{type(exc).__name__}: {exc}"[:300]}
                ),
                flush=True,
            )


asyncio.run(main())
