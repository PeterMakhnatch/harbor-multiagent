"""examples/tasks/hello-seats on real Docker, with the oracle in both seats; no
model APIs are used."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import TrialResult
from harbor.trial.trial import Trial

pytestmark = [pytest.mark.runtime, pytest.mark.integration, pytest.mark.asyncio]

TASK = Path(__file__).resolve().parents[2] / "examples" / "tasks" / "hello-seats"
SEATS = ("p1", "p2")

# Replaces solution/solve.sh: p1 takes no action in the world, and p2 posts
# p1's note as p1, once with its own token and once with no token. p1 prints a
# line, as any agent leaves a log, so only the world's record can fail it.
IMPERSONATION = r"""#!/bin/bash
if [ "$SEAT" = p1 ]; then echo "p1 takes no action"; sleep 10; exit 0; fi
for auth in "Authorization: Bearer $(cat /run/seat/token)" "Authorization: Bearer"; do
  curl -sS -o /dev/null -w '%{http_code}\n' http://world:8080/note -H "$auth" \
    -H 'Content-Type: application/json' -d '{"author": "p1", "text": "hello from p1"}'
done
"""


async def run_oracle_trial(task: Path, tmp_path: Path) -> tuple[Trial, TrialResult]:
    config = TrialConfig(
        task=TaskConfig(path=task),
        # Parallel tests share the Docker daemon's Compose project namespace.
        trial_name=f"seats-{hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]}",
        trials_dir=tmp_path / "trials",
        participants=[Participant(seat=seat, name="oracle") for seat in SEATS],
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
    )
    async with asyncio.timeout(600):
        trial = await Trial.create(config)
        result = await trial.run()
    return trial, result


def world_notes(trial: Trial) -> list[dict]:
    notes = trial.paths.artifacts_dir / "logs" / "world" / "notes.jsonl"
    return [json.loads(line) for line in notes.read_text().splitlines()]


async def test_oracle_in_both_seats_earns_reward_one(tmp_path: Path):
    trial, result = await run_oracle_trial(TASK, tmp_path)

    assert result.exception_info is None, result.exception_info
    assert result.finished_at is not None
    assert result.seat_results is not None
    assert {seat.seat: seat.under_test for seat in result.seat_results} == {
        "p1": True,
        "p2": False,
    }
    for seat in result.seat_results:
        assert seat.exception_info is None, (seat.seat, seat.exception_info)
        assert seat.agent_info is not None
        assert seat.agent_info.name == "oracle"
        assert seat.agent_execution is not None
        assert seat.agent_execution.finished_at is not None
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == {"reward": 1.0}
    for seat in SEATS:
        oracle_log = trial.paths.trial_dir / "seats" / seat / "agent" / "oracle.txt"
        assert f"{seat} posts its note" in oracle_log.read_text()
    p2_note, p1_note = world_notes(trial)
    assert (p2_note["author"], p2_note["text"]) == ("p2", "hello from p2")
    assert (p1_note["author"], p1_note["text"]) == ("p1", "hello from p1")
    assert p1_note["reply_to"] == p2_note["id"]


async def test_p2_posting_as_p1_earns_reward_zero(tmp_path: Path):
    task = tmp_path / "hello-seats"
    shutil.copytree(TASK, task)
    (task / "solution" / "solve.sh").write_text(IMPERSONATION)

    trial, result = await run_oracle_trial(task, tmp_path)

    assert result.exception_info is None, result.exception_info
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == {"reward": 0.0}
    p2_log = trial.paths.trial_dir / "seats" / "p2" / "agent" / "oracle.txt"
    assert p2_log.read_text().split() == ["403", "401"]
    assert world_notes(trial) == []
