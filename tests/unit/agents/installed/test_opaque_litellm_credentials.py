"""Explicitly selected credentials reach clients regardless of provider parsing."""

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from harbor.agents.installed.aider import Aider, _SNAPSHOT_UNTRACKED_WORKSPACE
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.mini_swe_agent import MiniSweAgent
from harbor.agents.installed.swe_agent import SweAgent
from harbor.models.agent.context import AgentContext


@pytest.mark.parametrize("agent_class", [Aider, MiniSweAgent, SweAgent])
@pytest.mark.parametrize(
    "model,skip,base_url",
    [
        ("openai/gpt-6", True, "https://proxy.example/v1"),
        ("unknown/nested/model:free", False, "https://proxy.example/v1"),
        ("openai/gpt-6", False, None),
        ("openrouter/anthropic/claude-sonnet-4-5", False, None),
        ("sambanova/nested/model", False, None),
    ],
)
@pytest.mark.parametrize("selected", ["selected-'\"$key=with spaces", ""])
async def test_selected_key_reaches_native_client(
    tmp_path, monkeypatch, agent_class, model, skip, base_url, selected
):
    monkeypatch.setenv("OPENAI_API_KEY", "lower-priority-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "lower-priority-key")
    monkeypatch.setenv("SAMBANOVA_API_KEY", "lower-priority-key")
    agent = agent_class(
        logs_dir=tmp_path,
        model_name=model,
        skip_model_parsing=skip,
        model_api_key_name="MY_KEY",
        base_url=base_url,
        extra_env={"MY_KEY": selected},
    )
    agent.exec_as_agent = AsyncMock()

    await agent.run("do the task", AsyncMock(), AgentContext())

    calls = [call.kwargs for call in agent.exec_as_agent.call_args_list]
    commands = [call["command"] for call in calls]
    if selected:
        assert all(selected not in command for command in commands)
    if agent_class is Aider:
        launch_call = next(call for call in calls if "aider --yes" in call["command"])
        settings = json.loads(launch_call["env"]["AIDER_API_KEY_SETTINGS"])
        assert settings[0]["extra_params"]["api_key"] == selected
        assert (
            settings[0]["extra_params"].get("api_base")
            == agent.model_connection.execution_base_url
        )
        launch = next(command for command in commands if "aider --yes" in command)
        assert '--model-settings-file="$settings_path"' in launch
        assert f"--model={model}" in shlex.split(launch)
    elif agent_class is MiniSweAgent:
        launch = next(
            command for command in commands if "mini-swe-agent --yolo" in command
        )
        tokens = shlex.split(launch)
        # mini-swe-agent prints every spec, so the key is not one of them.
        assert not any("api_key=" in token for token in tokens)
        assert "$key_config_dir/api-key.yaml" in tokens
        launch_env = next(call["env"] for call in calls if call["command"] == launch)
        assert yaml.safe_load(launch_env["MINI_SWE_AGENT_MODEL_API_KEY_CONFIG"]) == {
            "model": {"model_kwargs": {"api_key": selected}}
        }
        if base_url:
            assert f"model.model_kwargs.api_base={base_url}" in tokens
        assert f"--model={model}" in tokens
    else:
        launch = next(command for command in commands if "sweagent run" in command)
        tokens = shlex.split(launch)
        assert "--agent.model.api_key=$SWE_AGENT_MODEL_API_KEY" in tokens
        launch_env = next(call["env"] for call in calls if call["command"] == launch)
        assert launch_env["SWE_AGENT_MODEL_API_KEY"] == selected
        if base_url:
            assert f"--agent.model.api_base={base_url}" in tokens
        assert f"--agent.model.name={model}" in tokens


@pytest.mark.parametrize("agent_class", [Aider, MiniSweAgent, SweAgent])
@pytest.mark.parametrize("return_code", [0, 1])
@pytest.mark.parametrize("skip", [False, True])
async def test_selected_credentials_do_not_enter_command_logs_or_errors(
    tmp_path, caplog, agent_class, return_code, skip
):
    selected = "synthetic-selected-secret-3551"
    agent = agent_class(
        logs_dir=tmp_path,
        model_name="openai/private-model",
        skip_model_parsing=skip,
        model_api_key_name="MY_VALUE",
        extra_env={"MY_VALUE": selected},
        logger=logging.getLogger("harbor.test.selected-credentials"),
    )
    environment = AsyncMock()
    environment.exec.return_value = SimpleNamespace(
        return_code=return_code, stdout="", stderr=""
    )
    with caplog.at_level(logging.DEBUG):
        if return_code:
            with pytest.raises(NonZeroAgentExitCodeError) as error:
                await agent.run("do the task", environment, AgentContext())
            assert selected not in str(error.value)
        else:
            await agent.run("do the task", environment, AgentContext())
    assert selected not in caplog.text
    assert all(
        selected not in repr(getattr(record, "env", {})) for record in caplog.records
    )


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="requires a POSIX host and shell",
)
@pytest.mark.parametrize("return_code", [0, 7])
async def test_aider_settings_are_private_and_removed_after_launch(
    tmp_path, return_code
):
    selected = "selected-'\"$key=with spaces\nand a newline"
    agent = Aider(
        logs_dir=tmp_path,
        model_name="openai/private-model",
        skip_model_parsing=True,
        model_api_key_name="MY_KEY",
        extra_env={"MY_KEY": selected},
    )
    agent.exec_as_agent = AsyncMock()
    await agent.run("do the task", AsyncMock(), AgentContext())
    call = agent.exec_as_agent.call_args.kwargs
    setup = call["command"].split(_SNAPSHOT_UNTRACKED_WORKSPACE)[0]
    output = tmp_path / "observed.json"
    inspect_settings = (
        "import json,sys; from pathlib import Path; "
        "p=Path(sys.argv[1]); "
        "Path(sys.argv[2]).write_text(json.dumps({'path':str(p),"
        "'mode':p.stat().st_mode & 0o777,'settings':json.loads(p.read_text())})); "
        f"sys.exit({return_code})"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            setup
            + f'{shlex.quote(sys.executable)} -c {shlex.quote(inspect_settings)} "$settings_path" {shlex.quote(str(output))}',
        ],
        env={**os.environ, **call["env"], "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    observed = json.loads(output.read_text())
    assert result.returncode == return_code
    assert "harbor" not in Path(observed["path"]).name.lower()
    assert observed["mode"] == 0o600
    assert observed["settings"][0]["extra_params"]["api_key"] == selected
    assert not Path(observed["path"]).exists()


# Stands in for mini-swe-agent 2.x: it prints its -c spec list, then loads
# each file spec with yaml.safe_load (minisweagent.config.get_config_from_spec).
_MINI_SWE_AGENT_STUB = """
import json, os, sys
from pathlib import Path
import yaml

args = sys.argv[1:]
specs = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-c"]
print(f"Building agent config from specs: {specs}")
files = [Path(spec) for spec in specs if "=" not in spec and spec.endswith(".yaml")]
Path(os.environ["STUB_RECORD"]).write_text(json.dumps([
    {
        "path": str(path),
        "mode": path.parent.stat().st_mode & 0o777,
        "config": yaml.safe_load(path.read_text()),
    }
    for path in files
]))
sys.exit(int(os.environ["STUB_EXIT"]))
"""


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="requires a POSIX host and shell",
)
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize(
    "selected",
    [
        # As long as the OpenRouter key that mini-swe-agent's wrapped spec
        # list once printed into its log. Not a real key.
        "sk-or-v1-" + "0123456789abcdef" * 4,
        "selected-'\"$key=with spaces\nand a newline",
    ],
    ids=["provider key", "shell and YAML syntax"],
)
async def test_mini_swe_agent_reads_the_key_from_a_private_file_spec(
    tmp_path, exit_code, selected
):
    agent = MiniSweAgent(
        logs_dir=tmp_path,
        model_name="openrouter/z-ai/glm-5.3-flash",
        model_api_key_name="MY_KEY",
        extra_env={"MY_KEY": selected},
        reasoning_effort="low",
    )
    agent.exec_as_agent = AsyncMock()
    await agent.run("do the task", AsyncMock(), AgentContext())
    call = agent.exec_as_agent.call_args.kwargs
    logs = tmp_path / "logs"
    logs.mkdir()
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (tmp_path / "stub.py").write_text(_MINI_SWE_AGENT_STUB)
    stub = bin_dir / "mini-swe-agent"
    stub.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$HOME/stub.py" "$@"\n'
    )
    stub.chmod(0o755)
    record = tmp_path / "record.json"

    result = subprocess.run(
        ["bash", "-c", call["command"].replace("/logs/agent/", f"{logs}/")],
        env={
            "PATH": os.environ["PATH"],
            **call["env"],
            "HOME": str(tmp_path),
            "STUB_RECORD": str(record),
            "STUB_EXIT": str(exit_code),
        },
        capture_output=True,
        text=True,
    )

    assert record.exists(), result.stderr
    printed = (logs / "mini-swe-agent.txt").read_text()
    assert "Building agent config from specs:" in printed
    # Wrapping at any width cannot rebuild the key from what was printed.
    assert "".join(selected.split()) not in "".join(printed.split())
    (key_file,) = json.loads(record.read_text())
    path = Path(key_file["path"])
    # mini-swe-agent only takes a file spec that ends in .yaml.
    assert path.name == "api-key.yaml"
    assert "harbor" not in path.parent.name.lower()
    assert key_file["mode"] == 0o700
    assert key_file["config"] == {"model": {"model_kwargs": {"api_key": selected}}}
    assert not path.parent.exists()
