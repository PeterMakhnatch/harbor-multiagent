"""Regrade real hello-seats records without restoring the live world or running a model."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from harbor.models.environment_type import EnvironmentType
from harbor.models.task.config import VerifierEnvironmentMode
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    SourceTrialConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.trial.trial import Trial

pytestmark = [pytest.mark.runtime, pytest.mark.integration, pytest.mark.asyncio]

TASK = Path(__file__).resolve().parents[2] / "examples" / "tasks" / "hello-seats"

# Use the fixture's HTTP protocol without the player image's apt dependencies.
SOLUTION = """#!/bin/bash
python3 - <<'PY'
import json
import os
import time
from urllib.request import urlopen

seat = os.environ["SEAT"]
other = "p2" if seat == "p1" else "p1"
print(f"{seat} posts its note", flush=True)
note = json.dumps({"author": seat, "text": f"hello from {seat}"}).encode()
with urlopen("http://world:8080/note", data=note, timeout=5) as response:
    assert response.status == 201
while True:
    with urlopen("http://world:8080/notes", timeout=5) as response:
        if any(note["author"] == other for note in json.load(response)):
            break
    time.sleep(0.2)
PY
"""

RECORD_VERIFIER = """#!/bin/bash
python3 - <<'PY'
import json
from pathlib import Path
from urllib.request import urlopen

# Declared artifacts are replayed at their source path. The live API reads
# the world's in-memory notes, not this file.
notes = Path("/logs/world/notes.jsonl").read_text().splitlines()
posts = sorted((note["author"], note["text"]) for note in map(json.loads, notes))
assert posts == [("p1", "hello from p1"), ("p2", "hello from p2")], posts
for seat in ("p1", "p2"):
    log = Path(f"/logs/seats/{seat}/agent/oracle.txt").read_text()
    assert f"{seat} posts its note" in log, log
with urlopen("http://localhost:8080/notes", timeout=5) as response:
    live_notes = json.load(response)
assert live_notes == [], live_notes
Path("/logs/verifier/reward.txt").write_text("0.75\\n")
PY
"""


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


async def test_regrade_saved_seats_in_fresh_world(tmp_path: Path):
    task = shutil.copytree(TASK, tmp_path / TASK.name)
    (task / "environment/player/Dockerfile").write_text(
        "FROM python:3.13-slim\nRUN useradd --create-home --shell /bin/bash player\n"
    )
    solve = task / "solution/solve.sh"
    solve.write_text(SOLUTION)
    # Parallel tests share the Docker daemon's Compose project namespace.
    suffix = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:12]
    config = TrialConfig(
        task=TaskConfig(path=task),
        trial_name=f"seat-source-{suffix}",
        trials_dir=tmp_path / "trials",
        participants=[Participant(seat=seat, name="oracle") for seat in ("p1", "p2")],
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
    )
    async with asyncio.timeout(600):
        source = await Trial.create(config)
        recorded = await source.run()
    assert recorded.exception_info is None, recorded.exception_info
    assert recorded.verifier_environment_mode == VerifierEnvironmentMode.SHARED
    assert recorded.reward_valid and recorded.verifier_result is not None
    assert recorded.verifier_result.rewards == {"reward": 1.0}
    assert recorded.task_checksum == source.task.checksum
    assert recorded.seat_results is not None
    assert {seat.seat for seat in recorded.seat_results} == {"p1", "p2"}
    assert all(seat.agent_execution is not None for seat in recorded.seat_results)
    assert source._is_agent_environment_stopped
    source_dir = source.paths.trial_dir
    manifest = json.loads((source.paths.artifacts_dir / "manifest.json").read_text())
    assert any(
        entry["source"] == "/logs/world/notes.jsonl" and entry["status"] == "ok"
        for entry in manifest
    )
    before = _snapshot(source_dir)

    # Regrade must not execute the oracle solution again.
    solve.write_text("#!/bin/bash\nexit 97\n")
    (task / "tests/test.sh").write_text(RECORD_VERIFIER)
    task_toml = task / "task.toml"
    task_toml.write_text(
        task_toml.read_text().replace(
            "[verifier]\n",
            '[verifier]\nenvironment_mode = "separate"\n'
            "regrade_from_seat_record = true\n",
        )
    )
    async with asyncio.timeout(600):
        replay = await Trial.create(
            config.model_copy(
                update={
                    "trial_name": f"seat-regrade-{suffix}",
                    "source_trial": SourceTrialConfig(
                        action="regrade", type="local", path=source_dir
                    ),
                }
            )
        )
        corrected = await replay.run()
    assert corrected.exception_info is None, corrected.exception_info
    assert corrected.verifier_environment_mode == VerifierEnvironmentMode.SEPARATE
    assert corrected.reward_valid and corrected.verifier_result is not None
    assert corrected.verifier_result.rewards == {"reward": 0.75}
    assert corrected.source_task_checksum == recorded.task_checksum
    assert corrected.task_checksum == replay.task.checksum != recorded.task_checksum
    assert corrected.agent_execution is None
    assert corrected.environment_setup is None
    assert corrected.seat_results == recorded.seat_results
    assert _snapshot(source_dir) == before
