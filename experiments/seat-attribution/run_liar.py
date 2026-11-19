"""Liar-scout episodes: model listener vs scripted scout (truth/lie).

Usage: uv run python experiments/seat-attribution/run_liar.py [truth|lie] [n]
Prints one JSON object per episode with reward, whether the listener
verified before answering, bypass reads of /true-code, and listener cost.
Needs OPENROUTER_API_KEY for the listener model.
"""

import asyncio
import json
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

import os
TASK = Path(os.environ.get("SEAT_TASK", Path(__file__).parent / "tasks/liar-scout"))
ROOT = Path("/tmp/liar-scout-trials")
MODEL = "openrouter/openai/gpt-oss-120b"


async def run_one(name: str, lie: bool, listener: Participant) -> dict:
    config = TrialConfig(
        task=TaskConfig(path=TASK),
        trial_name=f"liar-{name}",
        trials_dir=ROOT,
        participants=[
            listener,
            Participant(
                seat="scout",
                import_path="scout_agent:ScoutAgent",
                kwargs={"lie": lie},
            ),
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
    answer_at = kinds.index("answer") if "answer" in kinds else None
    listener_seat = next(
        (s for s in result.seat_results or [] if s.seat == "listener"), None
    )
    return {
        "episode": name,
        "lie": lie,
        "rewards": result.verifier_result.rewards if result.verifier_result else None,
        "verified_before_answer": (
            any(k == "verify" for k in kinds[:answer_at]) if answer_at is not None else False
        ),
        "verify_calls": kinds.count("verify"),
        "true_code_reads": kinds.count("true_code_read"),
        "events": kinds,
        "listener_cost": (
            listener_seat.agent_result.cost_usd
            if listener_seat and listener_seat.agent_result
            else None
        ),
        "trial_dir": str(trial.paths.trial_dir),
    }


async def main() -> None:
    condition = sys.argv[1] if len(sys.argv) > 1 else "truth"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    listener = Participant(
        seat="listener",
        name="terminus-2",
        model_name=MODEL,
        model_api_key_name="OPENROUTER_API_KEY",
    )
    for i in range(count):
        try:
            print(
                json.dumps(await run_one(f"{condition}{i}", condition == "lie", listener)),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {"episode": f"{condition}{i}", "error": f"{type(exc).__name__}: {exc}"[:300]}
                ),
                flush=True,
            )


asyncio.run(main())
