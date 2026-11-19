"""examples/tasks/hello-seats on real Docker, with the oracle in both seats; no
model APIs are used. A token is read only inside its own seat's container, by
the scripts below, and nothing prints one."""

import asyncio
import hashlib
import json
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from harbor.models.environment_type import EnvironmentType
from harbor.models.trial.config import (
    EnvironmentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import TrialResult
from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.seats import SeatsTrial
from harbor.trial.trial import Trial

pytestmark = [pytest.mark.runtime, pytest.mark.integration, pytest.mark.asyncio]

TASK = Path(__file__).resolve().parents[2] / "examples" / "tasks" / "hello-seats"
SEATS = ("p1", "p2")

# Replaces solution/solve.sh. p1 takes no action in the world, and prints a
# line, as any agent leaves a log, so only the world's record can fail it. p2
# posts its own note, then sends p1's exact note, in reply to that note, with
# p2's token and p1's name, with no token, and with p2's token and no name.
# The last one differs from p1's note only in its author.
IMPERSONATION = r"""#!/bin/bash
if [ "$SEAT" = p1 ]; then echo "p1 takes no action"; exit 0; fi
token=$(cat /run/seat/token)
post() {  # POST $1 with the bearer token $2, and print the HTTP status
  curl -sS -o /dev/null -w '%{http_code}\n' http://world:8080/note \
    -H 'Content-Type: application/json' -H "Authorization: Bearer $2" -d "$1"
}
post '{"text": "hello from p2"}' "$token"
id=$(curl -fsS http://world:8080/notes | python3 -c '
import json, sys
print(json.load(sys.stdin)[0]["id"])')
p1_note="\"text\": \"hello from p1\", \"reply_to\": \"$id\""
post "{\"author\": \"p1\", $p1_note}" "$token"
post "{\"author\": \"p1\", $p1_note}" ""
post "{$p1_note}" "$token"
"""

# Runs in p1's container after the collect hook has saved the notes and before
# the verifier grades them: a second note with p1's token, again in reply to
# p2's note. It prints the HTTP status only.
LATE_NOTE = r"""
id=$(curl -fsS http://world:8080/notes | python3 -c '
import json, sys
print(next(n["id"] for n in json.load(sys.stdin) if n["author"] == "p2"))')
curl -sS -o /dev/null -w '%{http_code}' http://world:8080/note \
  -H "Authorization: Bearer $(cat /run/seat/token)" \
  -H 'Content-Type: application/json' \
  -d "{\"text\": \"hello from p1\", \"reply_to\": \"$id\"}"
"""

Mount = tuple[str, str | None, str, bool]  # type, volume name, path, writable


async def run_oracle_trial(
    task: Path,
    tmp_path: Path,
    before_grading: Callable[[SeatsTrial], Awaitable[None]] | None = None,
) -> tuple[SeatsTrial, TrialResult]:
    """Run the task with the oracle in both seats. before_grading runs when
    the verifier starts: after the collect hook has saved the notes, with
    every container still up."""
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
        assert isinstance(trial, SeatsTrial)
        if before_grading is not None:

            async def at_verification_start(_: TrialHookEvent) -> None:
                await before_grading(trial)

            trial.add_hook(TrialEvent.VERIFICATION_START, at_verification_start)
        result = await trial.run()
    return trial, result


def world_notes(trial: Trial) -> list[dict[str, Any]]:
    """The notes that Harbor saved, which are the notes that the verifier graded."""
    notes = trial.paths.artifacts_dir / "logs" / "world" / "notes.json"
    return json.loads(notes.read_text())


def compose_project(trial: Trial) -> str:
    """The trial's Compose project name, without depending on how Harbor derives it."""
    environment = trial.agent_environment
    name = getattr(
        environment, "compose_project_name", None
    )  # Harbor with neutral project names
    if isinstance(name, str):
        return name
    from harbor.environments.docker.docker import _sanitize_docker_compose_project_name

    return _sanitize_docker_compose_project_name(environment.session_id)


def service_mounts(project: str) -> dict[str, list[Mount]]:
    """Each running container of the Compose project, by service, with its
    mounts as `docker inspect` reports them. Mounts hold no file contents."""

    def docker(*args: str) -> str:
        return subprocess.run(
            ["docker", *args], check=True, capture_output=True, text=True
        ).stdout

    label = f"label=com.docker.compose.project={project}"
    ids = docker("ps", "--quiet", "--filter", label).split()
    return {
        container["Config"]["Labels"]["com.docker.compose.service"]: [
            (mount["Type"], mount.get("Name"), mount["Destination"], mount["RW"])
            for mount in container["Mounts"]
        ]
        for container in json.loads(docker("inspect", *ids))
    }


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


async def test_each_seat_mounts_its_own_token_and_nothing_else(tmp_path: Path):
    mounts: dict[str, list[Mount]] = {}

    async def read_mounts(trial: SeatsTrial) -> None:
        project = compose_project(trial)
        mounts.update(await asyncio.to_thread(service_mounts, project))

    trial, result = await run_oracle_trial(TASK, tmp_path, before_grading=read_mounts)

    assert result.exception_info is None, result.exception_info
    assert sorted(mounts) == ["main", "p1", "p2"]
    volume = {seat: f"{compose_project(trial)}_{seat}-token" for seat in SEATS}
    for seat in SEATS:
        # The other seat's token volume is at no path in this seat, and
        # nothing else from the host or another container is mounted.
        assert mounts[seat] == [("volume", volume[seat], "/run/seat", False)]
    token_mounts = [mount for mount in mounts["main"] if mount[1] in volume.values()]
    assert sorted(token_mounts) == [
        ("volume", volume["p1"], "/tokens/p1", True),
        ("volume", volume["p2"], "/tokens/p2", True),
    ]


async def test_a_note_after_the_save_is_neither_graded_nor_saved(tmp_path: Path):
    statuses: list[str] = []

    async def post_late_note(trial: SeatsTrial) -> None:
        p1 = next(seat for seat in trial.seats if seat.name == "p1")
        statuses.append(((await p1.environment.exec(LATE_NOTE)).stdout or "").strip())

    trial, result = await run_oracle_trial(
        TASK, tmp_path, before_grading=post_late_note
    )

    assert result.exception_info is None, result.exception_info
    # The world accepted p1's second note while the verifier ran. The live
    # world then held two notes by p1, which would earn 0.
    assert statuses == ["201"]
    # The verifier graded the saved notes, and Harbor kept that same record.
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == {"reward": 1.0}
    p2_note, p1_note = world_notes(trial)
    assert (p1_note["author"], p1_note["reply_to"]) == ("p1", p2_note["id"])


async def test_p2_sending_p1s_note_earns_reward_zero(tmp_path: Path):
    task = tmp_path / "hello-seats"
    shutil.copytree(TASK, task)
    (task / "solution" / "solve.sh").write_text(IMPERSONATION)
    # Put p2 under test too, so play lasts until p2's posts are done.
    task_toml = task / "task.toml"
    assert "under_test = false" in task_toml.read_text()
    task_toml.write_text(
        task_toml.read_text().replace("under_test = false", "under_test = true")
    )

    trial, result = await run_oracle_trial(task, tmp_path)

    assert result.exception_info is None, result.exception_info
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == {"reward": 0.0}
    p2_log = trial.paths.trial_dir / "seats" / "p2" / "agent" / "oracle.txt"
    assert p2_log.read_text().split() == ["201", "403", "401", "201"]
    # The record holds a note with p1's text in reply to p2's note, which
    # qualifies in every way except its author, so only the token decides.
    p2_note, copy = world_notes(trial)
    assert (p2_note["author"], p2_note["text"]) == ("p2", "hello from p2")
    assert (copy["author"], copy["text"]) == ("p2", "hello from p1")
    assert copy["reply_to"] == p2_note["id"]
