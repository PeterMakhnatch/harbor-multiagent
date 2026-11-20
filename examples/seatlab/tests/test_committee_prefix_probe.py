import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from stat import S_IMODE
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from examples.seatlab.committee import (
    driver,
    prefix_packet,
    prefix_probe,
    prefix_schedule,
    schedule,
)

_STAMP = "2026-01-01T00:00:00Z"
_AGENT_IMPORT = "examples.seatlab.committee.prefix_probe_seat:PrefixProbeSeat"


@pytest.fixture
def cut():
    point = prefix_packet.load(prefix_schedule.PACKET, prefix_schedule.PACKET_SHA256)[0]
    assert point is not None
    return point


def _native_trial(config, cut, change):
    trial = Path(config["trials_dir"]) / config["trial_name"]
    trial.mkdir()
    seats: list[dict[str, Any]] = [
        {
            "seat": role,
            "under_test": True,
            "exception_info": None,
            "agent_execution": {"finished_at": _STAMP},
            "agent_info": {
                "name": "seatlab-prefix-cut-check",
                "version": "1.0.0",
                "model_info": None,
            },
            "agent_result": {
                "metadata": {
                    "schema": "seatlab.native-prefix-probe-seat/v1",
                    "status": "complete",
                    "role": role,
                    "map_index": 0,
                    "run_id": "cut-check-m0",
                    "cut_status": {
                        "message_count": 9,
                        "votes_submitted": 0,
                        "done": [],
                    },
                    "packet_sha256": prefix_schedule.PACKET_SHA256,
                    "prefix_sha256": cut.prefix_sha256,
                    "event_count": len(cut.events),
                }
            },
        }
        for role in schedule.ROLES
    ]
    result = {
        "id": str(uuid4()),
        "schema_version": 1,
        "trial_name": config["trial_name"],
        "trial_uri": trial.as_uri(),
        "task_name": "crucible/epistemic-crucible-hidden_profile_unshared",
        "task_checksum": schedule.TASK_CHECKSUM,
        "task_id": config["task"],
        "finished_at": _STAMP,
        "exception_info": None,
        "verifier_result": None,
        "reward_valid": False,
        "config": config,
        "seat_results": seats,
    }
    if change == "missing_receipt":
        del seats[-1]["agent_result"]["metadata"]
    elif change == "wrong_receipt":
        seats[-1]["agent_result"]["metadata"]["prefix_sha256"] = "0" * 64
    elif change == "wrong_trial":
        result["task_checksum"] = "0" * 64
    elif change == "verifier_result":
        result["verifier_result"] = {"rewards": {"reward": 0}}
    (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")

    if change == "missing_artifact":
        return
    rows = [event | {"ts": _STAMP} for event in cut.events]
    read = next(row for row in rows if row["event_type"] == "dossier_read")
    if change == "vote":
        role = read["actor"]
        # Keep the event count unchanged, to exercise the content check.
        read.update(
            event_type="vote",
            payload={"seat": role, "choice": "Option_A", "cited_facts": ["F_PRIV_1"]},
        )
    elif change == "wrong_prefix":
        read["actor"] = next(role for role in schedule.ROLES if role != read["actor"])
    elif change == "extra_done":
        role = read["actor"]
        rows.append(
            read
            | {
                "seq": len(rows) + 1,
                "event_type": "seat_done",
                "payload": {"seat": role},
            }
        )
    elif change == "truncated_prefix":
        rows.pop()
    world = trial / "artifacts" / "logs" / "world" / "events.jsonl"
    world.parent.mkdir(parents=True)
    world.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = [
        {
            "source": "/logs/world/events.jsonl",
            "destination": "artifacts/logs/world/events.jsonl",
            "type": "file",
            "status": "ok",
            "service": "main",
        }
    ]
    (trial / "artifacts" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (None, None),
        ("missing_receipt", "native_trial_incomplete"),
        ("wrong_receipt", "native_trial_incomplete"),
        ("wrong_trial", "native_trial_incomplete"),
        ("verifier_result", "native_trial_incomplete"),
        ("missing_artifact", "native_prefix_mismatch"),
        ("vote", "native_prefix_mismatch"),
        ("wrong_prefix", "native_prefix_mismatch"),
        ("extra_done", "native_prefix_mismatch"),
        ("truncated_prefix", "native_prefix_mismatch"),
    ],
)
def test_probe_uses_native_evidence_not_the_exit_code(
    tmp_path, monkeypatch, capsys, cut, change, error
):
    tmp_path = tmp_path.resolve()
    task = tmp_path / driver._TASK_STEM
    task.mkdir()
    harbor = tmp_path / "harbor"
    out = tmp_path / "fresh"
    plugins = tmp_path / "system-plugins"
    plugins.mkdir()
    monkeypatch.setattr(driver, "_DOCKER_DESKTOP_PLUGINS", plugins)
    inherited = {
        "PATH": os.pathsep.join(("/usr/bin", ".", "", "relative/bin")),
        "DOCKER_HOST": "unix:///docker.sock",
        "DEEPSEEK_API_KEY": "do-not-forward",
        "OPENAI_API_KEY": "do-not-forward",
        "SEATLAB_SPEND_DIR": "do-not-forward",
        "OPENAI_BASE_URL": "do-not-forward",
        "DOCKER_CONFIG": "do-not-forward",
        "HARBOR_CONFIG_FILE": "do-not-forward",
        "PYTHONPATH": "do-not-forward",
        "HOME": "do-not-forward",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)
    preflight = Mock(return_value=(harbor, harbor.with_name("python"), inherited))
    monkeypatch.setattr(driver, "_preflight", preflight)

    def run(command, *, cwd, env, stdout, **kwargs):
        assert command == [
            str(harbor),
            "trial",
            "start",
            "-c",
            str(out / "config.json"),
        ]
        assert cwd == out
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["umask"] == 0o077
        assert S_IMODE(out.stat().st_mode) == 0o700
        assert S_IMODE(os.fstat(stdout.fileno()).st_mode) == 0o600
        sanitized = driver._native_env(
            {"PATH": "/usr/bin", "DOCKER_HOST": "unix:///docker.sock"}
        )
        assert env == sanitized | {"HOME": env["HOME"]}
        assert env["COMPOSE_DISABLE_ENV_FILE"] == env["PYTHON_DOTENV_DISABLED"] == "1"
        home = Path(env["HOME"])
        assert home.parent == out
        assert S_IMODE(home.stat().st_mode) == 0o700
        assert json.loads((home / ".docker" / "config.json").read_text()) == {
            "cliPluginsExtraDirs": [str(plugins)]
        }
        assert sorted(p.relative_to(home).as_posix() for p in home.rglob("*")) == [
            ".docker",
            ".docker/config.json",
        ]
        config = json.loads((cwd / "config.json").read_text())
        assert config == {
            "schema_version": 1,
            "task": {"path": str(task)},
            "trial_name": config["trial_name"],
            "trials_dir": str(out / "trials"),
            "verifier": {"disable": True},
            "environment": {
                "type": "docker",
                "force_build": True,
                "delete": True,
                "stream": False,
            },
            "participants": [
                {
                    "seat": role,
                    "import_path": _AGENT_IMPORT,
                    "kwargs": {"role": role, "map_index": 0},
                }
                for role in schedule.ROLES
            ],
        }
        assert not any(
            secret in json.dumps(config)
            for secret in ("do-not-forward", "deepseek", "model_name")
        )
        _native_trial(config, cut, change)
        stdout.write(b"private native logs\n")
        return subprocess.CompletedProcess(command, 0)

    child = Mock(side_effect=run)
    monkeypatch.setattr(prefix_probe.subprocess, "run", child)
    args = [
        "--map",
        "0",
        "--task",
        str(task),
        "--harbor",
        str(harbor),
        "--out",
        str(out),
    ]
    assert prefix_probe.main(args) == (0 if error is None else 1)
    output = capsys.readouterr()
    if error is None:
        assert not output.err
        assert json.loads(output.out) == {
            "schema": "seatlab.native-prefix-probe/v1",
            "status": "accepted",
            "map_index": 0,
            "complete_seats": 4,
            "packet_sha256": prefix_schedule.PACKET_SHA256,
            "source_event_count": len(cut.events),
            "native_event_count": len(cut.events),
            "source_prefix_sha256": cut.prefix_sha256,
            "native_prefix_sha256": cut.prefix_sha256,
        }
        assert (out / "native.log").read_text() == "private native logs\n"
    else:
        assert output.out == ""
        assert output.err == f"prefix probe: {error}\n"

    # No second invocation can read this trial or create another in its place.
    assert prefix_probe.main(args) == 1
    assert capsys.readouterr().err == "prefix probe: private_output_already_exists\n"
    preflight.assert_called_once_with(harbor, task)
    child.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", schedule.ROLES)
async def test_adapter_returns_only_after_restored_without_followup_actions(
    tmp_path, monkeypatch, cut, role
):
    from examples.seatlab.committee import prefix_probe_seat, prefix_seat
    from harbor.environments.seat import SeatEnvironment
    from harbor.models.agent.context import AgentContext

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    environment = Mock(spec=SeatEnvironment)
    environment.service = role
    context = AgentContext()
    calls = []

    @asynccontextmanager
    async def restored(seat, restore_role, run_id, checkpoint):
        assert seat is environment
        assert restore_role == role
        assert run_id == "cut-check-m0"
        assert checkpoint == cut
        calls.append("restore")
        yield (
            {"private": "do not retain"},
            {
                "message_count": 9,
                "votes_submitted": 0,
                "done": [],
            },
        )
        assert context.metadata is None
        calls.append("exit")

    monkeypatch.setattr(prefix_seat, "restored", restored)
    adapter = prefix_probe_seat.PrefixProbeSeat(tmp_path, role=role, map_index=0)
    await adapter.setup(environment)
    await adapter.run("unused", environment, context)
    assert calls == ["restore", "exit"]
    assert context.metadata == prefix_probe._receipt(role, cut)
    assert adapter.to_agent_info().model_info is None
    assert environment.mock_calls == []


@pytest.mark.parametrize(
    ("model_name", "extra_env"),
    [("paid/provider", None), (None, {"API_KEY": "secret"})],
)
def test_adapter_rejects_paid_options(tmp_path, model_name, extra_env):
    from examples.seatlab.committee import prefix_probe_seat

    with pytest.raises(ValueError, match="Invalid prefix probe seat configuration"):
        prefix_probe_seat.PrefixProbeSeat(
            tmp_path,
            role=schedule.ROLES[0],
            map_index=0,
            model_name=model_name,
            extra_env=extra_env,
        )
