import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, Mock

import pytest

from harbor.environments.ack import ACKEnvironment
from harbor.models.task.config import EnvironmentConfig


@pytest.mark.parametrize(
    "name",
    [
        "node_selector",
        "sandbox_env_vars",
        "sandbox_labels",
        "sandbox_annotations",
        "pod_overrides",
        "pod_annotations",
        "pod_labels",
    ],
)
def test_legacy_json_options(name):
    value = {"team": "evals"}
    options = ACKEnvironment.parse_options(
        {"namespace": "test", name: json.dumps(value)}
    )
    assert getattr(options, name) == value


@pytest.mark.asyncio
async def test_exec_forwards_environment_over_stdin(monkeypatch):
    env = object.__new__(ACKEnvironment)
    env._ensure_client = AsyncMock()
    env._merge_env = Mock(side_effect=lambda values: values)
    env._core_api = Mock()
    env._exec_api = Mock()
    env._read_exec_output = Mock(return_value=("", ""))
    env.default_user = None
    env.task_env_config = EnvironmentConfig()
    env.pod_name = "test-pod"
    env.namespace = "test-ns"
    response = Mock(returncode=0)
    stream = Mock(return_value=response)
    monkeypatch.setattr("harbor.environments.ack.stream", stream)
    value = "synthetic='value' $(not-a-command)\n"

    await env.exec(
        """printf '%s=%s' "$TOKEN" "$EMPTY" """,
        env={"TOKEN": value, "EMPTY": ""},
    )

    request = stream.call_args.kwargs
    assert request["stdin"] is True
    assert request["command"] == ["sh", "-s"]
    script = response.write_stdin.call_args.args[0]
    process = await asyncio.create_subprocess_exec(
        *request["command"],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(script.encode())
    assert process.returncode == 0, stderr
    assert stdout.decode() == value + "="


@pytest.mark.asyncio
async def test_image_exists_crane_removes_temp_docker_config(monkeypatch):
    """The crane image-existence check writes a temp Docker config that holds
    registry credentials; it must be removed afterward (regression: it leaked a
    credential-bearing directory on disk on every check)."""
    env = object.__new__(ACKEnvironment)
    env.registry = "myregistry.example.com"

    async def fake_docker_available() -> bool:
        return False  # force the crane fallback path

    async def fake_registry_auth():
        return ("user", "pass")  # so the temp DOCKER_CONFIG dir is created

    env._is_docker_available = fake_docker_available  # type: ignore[method-assign]
    env._get_registry_auth = fake_registry_auth  # type: ignore[method-assign]

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/crane")

    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr("tempfile.mkdtemp", spy_mkdtemp)

    class FakeProc:
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    await env._image_exists("myregistry.example.com/img:tag")

    assert created, "expected the crane path to create a temp Docker config dir"
    assert not os.path.exists(created[0]), (
        "temp Docker config dir (with registry credentials) was leaked"
    )
