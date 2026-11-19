"""What a seat leaves running when play ends, on real Docker with the oracle in
both seats; no model APIs are used.

p1, under test, ends at once and leaves a detached child behind. p2, a
supporting agent, is still running when play ends and has a background child of
its own. Each of the three posts to the world DELAY_SEC after p1 has ended, and
the verifier reads the world GRACE_SEC after that.
"""

import asyncio
import hashlib
import json
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

DELAY_SEC = 12
GRACE_SEC = 6
LATE = ("p1_child", "p2_child", "p2_harness")

WORLD = """\
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NOTES = Path("/logs/world/notes.jsonl")
POSTED = set()


class World(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path in POSTED else 404)
        self.end_headers()

    def do_POST(self):
        POSTED.add(self.path)
        with NOTES.open("a") as notes:
            note = {"note": self.path[1:], "at": time.time()}
            notes.write(json.dumps(note) + "\\n")
        self.send_response(201)
        self.end_headers()


NOTES.parent.mkdir(parents=True, exist_ok=True)
NOTES.touch()
ThreadingHTTPServer(("0.0.0.0", 8080), World).serve_forever()
"""

COMPOSE = """\
x-seat: &seat
  build: .
  user: player
  command: ["sleep", "infinity"]
  init: true

services:
  main:
    command: ["python3", "/world.py"]
  p1:
    <<: *seat
    environment: {SEAT: p1}
  p2:
    <<: *seat
    environment: {SEAT: p2}
"""

SOLVE = f"""\
#!/bin/bash
set -eu
post() {{ python3 -c "import urllib.request as u; u.urlopen('http://main:8080/$1', b'')"; }}
posted() {{ python3 -c "import urllib.request as u; u.urlopen('http://main:8080/$1')" 2>/dev/null; }}

echo "$SEAT started"
if [ "$SEAT" = p1 ]; then
  post p1_ended
  setsid nohup bash -c "$(declare -f post); sleep {DELAY_SEC}; post p1_child" >/dev/null 2>&1 &
  exit 0
fi
until posted p1_ended; do sleep 0.2; done
(sleep {DELAY_SEC}; post p2_child) &
sleep {DELAY_SEC}
post p2_harness
"""

TEST = f"""\
#!/bin/bash
python3 - <<'PY'
import json
import time
from pathlib import Path

notes = Path("/logs/world/notes.jsonl")


def posted():
    return {{n["note"]: n["at"] for n in map(json.loads, notes.read_text().splitlines())}}


time.sleep(max(0.0, posted()["p1_ended"] + {DELAY_SEC + GRACE_SEC} - time.time()))
seen = posted()
Path("/logs/verifier/notes.json").write_text(json.dumps(seen))
rewards = {{late: int(late in seen) for late in {LATE!r}}}
Path("/logs/verifier/reward.json").write_text(json.dumps(rewards))
PY
"""

TASK = """\
artifacts = [
    "/logs/world/notes.jsonl",
    { source = "/logs/agent/oracle.txt", service = "p2" },
]

[agent]
timeout_sec = 120

[verifier]
timeout_sec = 120

[environment.healthcheck]
command = "exec 3<>/dev/tcp/localhost/8080"
interval_sec = 1

[agents.p1]
%s
[agents.p2]
under_test = false
%s
"""


def _task(root: Path, seat_keys: str = "") -> Path:
    task = root / "late-seats"
    for directory in ("environment", "instructions", "solution", "tests"):
        (task / directory).mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text(
        "FROM python:3.13-slim\nRUN useradd --create-home player\n"
        "COPY world.py /world.py\n"
    )
    (task / "environment" / "world.py").write_text(WORLD)
    (task / "environment" / "docker-compose.yaml").write_text(COMPOSE)
    for seat in ("p1", "p2"):
        (task / "instructions" / f"{seat}.md").write_text(f"You are {seat}.\n")
    (task / "solution" / "solve.sh").write_text(SOLVE)
    (task / "tests" / "test.sh").write_text(TEST)
    (task / "task.toml").write_text(TASK % (seat_keys, seat_keys))
    return task


async def _run(task: Path, root: Path) -> tuple[Trial, TrialResult]:
    config = TrialConfig(
        task=TaskConfig(path=task),
        # Parallel tests share the Docker daemon's Compose project namespace.
        trial_name=f"late-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}",
        trials_dir=root / "trials",
        participants=[Participant(seat=seat, name="oracle") for seat in ("p1", "p2")],
        environment=EnvironmentConfig(type=EnvironmentType.DOCKER, delete=True),
    )
    async with asyncio.timeout(600):
        trial = await Trial.create(config)
        return trial, await trial.run()


def _collected_notes(trial: Trial) -> set[str]:
    notes = trial.paths.artifacts_dir / "logs" / "world" / "notes.jsonl"
    return {json.loads(line)["note"] for line in notes.read_text().splitlines()}


async def test_nothing_a_seat_left_running_acts_on_the_world_after_play(
    tmp_path: Path,
):
    trial, result = await _run(_task(tmp_path), tmp_path)

    assert result.exception_info is None, result.exception_info
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == dict.fromkeys(LATE, 0)
    assert result.agent_execution is not None
    assert result.agent_execution.finished_at is not None
    assert result.seat_results is not None
    for seat in result.seat_results:
        assert seat.freeze is not None, seat.seat
        assert seat.freeze.error is None, (seat.seat, seat.freeze.error)
        # The service's own `sleep infinity` and at least one leftover.
        assert seat.freeze.stopped is not None and seat.freeze.stopped >= 2
        assert seat.freeze.finished_at >= result.agent_execution.finished_at
    # A frozen seat is still collected from, by copy and by exec.
    p2_log = trial.paths.trial_dir / "seats" / "p2" / "agent" / "oracle.txt"
    assert p2_log.read_text() == "p2 started\n"
    p2_artifact = trial.paths.artifacts_dir / "logs" / "agent" / "oracle.txt"
    assert p2_artifact.read_text() == "p2 started\n"
    assert _collected_notes(trial) == {"p1_ended"}


async def test_a_seat_with_freeze_off_is_rewarded_for_what_it_does_after_play(
    tmp_path: Path,
):
    """Every seat trial ran this way before seats were frozen."""
    trial, result = await _run(_task(tmp_path, "freeze = false"), tmp_path)

    assert result.exception_info is None, result.exception_info
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == dict.fromkeys(LATE, 1)
    assert result.seat_results is not None
    assert [seat.freeze for seat in result.seat_results] == [None, None]
    seen = json.loads((trial.paths.verifier_dir / "notes.json").read_text())
    assert all(seen[late] - seen["p1_ended"] >= DELAY_SEC for late in LATE)
    # The artifacts were collected before the verifier ran, and lack them.
    assert _collected_notes(trial) == {"p1_ended"}
