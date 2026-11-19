"""On real Docker, a container cannot read the task name from a peer's name.

Docker's DNS answers a reverse lookup of a peer's IP with its container and
network names, which Compose builds from the project name. This test runs a
two-service project whose session id carries a condition in the task name
("silent") and does that lookup from one service, as a seat could.
"""

import asyncio
import shlex
import subprocess
from pathlib import Path

import pytest
from shortuuid import ShortUUID

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

pytestmark = [pytest.mark.runtime, pytest.mark.integration, pytest.mark.asyncio]

TASK_NAME = "leak-probe-silent"
IMAGE = "python:3.13-slim"
COMPOSE = f"""\
services:
  main:
    networks: [play]
  peer:
    image: {IMAGE}
    command: ["sleep", "infinity"]
    init: true
    networks: [play]
networks:
  play: {{}}
"""
REVERSE_LOOKUP = (
    "import socket; ip = socket.gethostbyname('main'); "
    "print(socket.gethostbyaddr(ip)[0])"
)


def _require_docker() -> None:
    result = subprocess.run(
        ["docker", "info"], capture_output=True, timeout=30, check=False
    )
    if result.returncode != 0:
        pytest.skip("Docker daemon is not available")


def _project_objects(project: str) -> str:
    """IDs of every container, network and volume Compose labels as project's."""
    label = f"label=com.docker.compose.project={project}"
    ids = []
    for kind in ("container", "network", "volume"):
        listing = ["ls", "-q"] if kind != "container" else ["ls", "-aq"]
        result = subprocess.run(
            ["docker", kind, *listing, "--filter", label],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        ids.append(result.stdout.strip())
    return "".join(ids)


async def test_peer_cannot_read_the_task_name_by_reverse_dns(tmp_path: Path):
    _require_docker()
    env_dir = tmp_path / TASK_NAME / "environment"
    env_dir.mkdir(parents=True)
    (env_dir / "docker-compose.yaml").write_text(COMPOSE)
    trial_paths = TrialPaths(trial_dir=tmp_path / "trial")
    trial_paths.mkdir()
    # As TrialConfig names trials; unique, since parallel runs share a daemon.
    trial_suffix = ShortUUID().random(length=7)
    env = DockerEnvironment(
        environment_dir=env_dir,
        environment_name=TASK_NAME,
        session_id=f"{TASK_NAME}__{trial_suffix}__env",
        trial_paths=trial_paths,
        task_env_config=EnvironmentConfig(docker_image=IMAGE),
    )
    project = env.compose_project_name

    try:
        async with asyncio.timeout(300):
            await env.start(force_build=False)
            lookup = await env.service_exec(
                f"python3 -c {shlex.quote(REVERSE_LOOKUP)}", service="peer"
            )
    finally:
        await env.stop(delete=True)

    assert lookup.return_code == 0, lookup.stdout
    name = (lookup.stdout or "").strip()
    # The answer came from Docker's DNS: main's container on the play network.
    assert name == f"{project}-main-1.{project}_play", name
    assert TASK_NAME not in name
    assert "silent" not in name
    assert trial_suffix not in name
    # Stop reached the same project: nothing of it is left behind.
    assert _project_objects(project) == ""
