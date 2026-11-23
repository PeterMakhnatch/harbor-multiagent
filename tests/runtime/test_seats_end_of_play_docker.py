"""What a seat leaves running when play ends, on real Docker with the oracle in
both seats; no model APIs are used. Both seats run as a non-root user, player.

p1, under test, ends as soon as p2 has started, and leaves a detached child
(a double fork with setsid nohup) behind: its agent has ended, and the child's
parent is PID 1. p2, a supporting agent, is still running when play ends; it
has a detached child of its own, also under PID 1, and an ordinary background
child. Each of the four posts to
the world DELAY_SEC after p1 has ended, and the verifier reads the world
GRACE_SEC after that and pays 1 for each late post it sees.
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
# How long after play ends the kill may finish; about a second on local Docker.
WINDOW_SEC = 5
BEFORE = {"p2_started", "p1_ended"}
LATE = ("p1_detached", "p2_detached", "p2_child", "p2_harness")
# Harbor before kill_leftovers: a seat still running at the end of play has
# its exec-started processes killed; a seat that ended, and a child that
# detached to PID 1, are spared.
KILLED_ONLY_IF_STOPPED = {
    "p1_detached": 1,
    "p2_detached": 1,
    "p2_child": 0,
    "p2_harness": 0,
}

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
later() {{ (setsid nohup bash -c "$(declare -f post); sleep {DELAY_SEC}; post $1" >/dev/null 2>&1 &) }}

echo "$SEAT started as $(id -un)"
if [ "$SEAT" = p1 ]; then
  until posted p2_started; do sleep 0.2; done
  post p1_ended
  later p1_detached
  exit 0
fi
post p2_started
until posted p1_ended; do sleep 0.2; done
later p2_detached
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
start_period_sec = 30
start_interval_sec = 0.5

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
    assert result.reward_valid
    assert result.seat_results is not None
    p1 = next(seat for seat in result.seat_results if seat.seat == "p1")
    # Play ended when p1, the only agent under test, ended.
    assert p1.agent_execution is not None and p1.agent_execution.finished_at
    play_ended = p1.agent_execution.finished_at
    for seat in result.seat_results:
        end = seat.end_of_play
        assert end is not None, seat.seat
        assert end.exception_info is None, (seat.seat, end.exception_info)
        # At least the detached child, in p1 whose agent had already ended.
        assert end.killed is not None and end.killed >= 1, seat.seat
        assert end.left == [], seat.seat
        window = (end.finished_at - play_ended).total_seconds()
        assert 0 <= window < WINDOW_SEC, (seat.seat, window)
    # A seat is still collected from after the kill.
    p2_log = trial.paths.trial_dir / "seats" / "p2" / "agent" / "oracle.txt"
    assert p2_log.read_text() == "p2 started as player\n"
    p2_artifact = trial.paths.artifacts_dir / "logs" / "agent" / "oracle.txt"
    assert p2_artifact.read_text() == "p2 started as player\n"
    assert _collected_notes(trial) == BEFORE


async def test_seats_that_keep_their_leftovers_are_paid_for_late_detached_posts(
    tmp_path: Path,
):
    """kill_leftovers = false gives the kill of Harbor before this change."""
    trial, result = await _run(_task(tmp_path, "kill_leftovers = false"), tmp_path)

    assert result.exception_info is None, result.exception_info
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == KILLED_ONLY_IF_STOPPED
    assert result.reward_valid
    assert result.seat_results is not None
    ends = {seat.seat: seat.end_of_play for seat in result.seat_results}
    assert ends["p1"] is None
    p2 = ends["p2"]
    assert p2 is not None and p2.exception_info is None
    assert (p2.killed, p2.left) == (None, None)
    seen = json.loads((trial.paths.verifier_dir / "notes.json").read_text())
    assert seen["p1_detached"] - seen["p1_ended"] >= DELAY_SEC
    assert seen["p2_detached"] - seen["p1_ended"] >= DELAY_SEC
    assert _collected_notes(trial) == BEFORE
