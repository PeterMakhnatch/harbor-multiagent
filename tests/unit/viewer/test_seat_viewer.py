"""The viewer shows seat trials by their participants and serves seat trajectories."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from harbor.models.trial.config import AgentConfig, TaskConfig, TrialConfig
from harbor.viewer.api_adapter import ViewerApiAdapter, register_api_adapter
from harbor.viewer.server import create_app
from harbor.viewer.trial_utils import (
    partial_trial_result_from_config,
    trial_summary_from_config,
)
from tests.unit.viewer.test_api_adapter import RemoteFixture
from tests.unit.viewer.test_sources import write_job

PARTICIPANTS = [
    {
        "seat": "chef0",
        "name": "terminus-2",
        "model_name": "anthropic/claude-sonnet-5-5",
        "kwargs": {"reasoning_effort": "high"},
    },
    {"seat": "chef1", "name": "codex", "model_name": "openai/gpt-6.1"},
]


def _write_seat_trajectories(trial_dir: Path) -> None:
    for seat in ("chef0", "chef1"):
        directory = trial_dir / "seats" / seat / "agent"
        directory.mkdir(parents=True)
        (directory / "trajectory.json").write_text(json.dumps({"seat": seat}))


def test_job_summary_extracts_participant_agents_and_models(tmp_path: Path) -> None:
    job_dir = tmp_path / "seat-job"
    job_dir.mkdir()
    (job_dir / "config.json").write_text(
        json.dumps(
            {
                "job_name": "seat-job",
                "participants": PARTICIPANTS,
                "agents": [{"name": "oracle"}],
            }
        )
    )

    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/jobs")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["agents"] == ["codex", "terminus-2"]
    assert item["providers"] == ["anthropic", "openai"]
    assert item["models"] == ["claude-sonnet-5-5", "gpt-6.1"]


def test_in_progress_seat_trial_uses_primary_participant(tmp_path: Path) -> None:
    config = TrialConfig(
        task=TaskConfig(path=tmp_path / "task"),
        participants=PARTICIPANTS,
    )
    assert config.agent.name == "oracle"
    assert config.agent.model_name is None
    config_path = tmp_path / "config.json"
    config_path.write_text(config.model_dump_json())

    summary = trial_summary_from_config("trial", config)
    result = partial_trial_result_from_config(
        job_name="job",
        trial_name="trial",
        trial_dir=tmp_path,
        config=config,
        config_path=config_path,
    )

    assert summary.agent_name == "terminus-2"
    assert summary.model_provider == "anthropic"
    assert summary.model_name == "claude-sonnet-5-5"
    assert summary.reasoning_effort == "high"
    assert result.agent_info.name == "terminus-2"
    assert result.agent_info.model_info is not None
    assert result.agent_info.model_info.provider == "anthropic"
    assert result.agent_info.model_info.name == "claude-sonnet-5-5"


def test_ordinary_in_progress_trial_is_unchanged(tmp_path: Path) -> None:
    config = TrialConfig(
        task=TaskConfig(path=tmp_path / "task"),
        agent=AgentConfig(
            name="codex",
            model_name="openai/gpt-6.1",
            kwargs={"reasoning_effort": "low"},
        ),
    )

    summary = trial_summary_from_config("trial", config)

    assert summary.agent_name == "codex"
    assert summary.model_provider == "openai"
    assert summary.model_name == "gpt-6.1"
    assert summary.reasoning_effort == "low"


def test_local_viewer_trajectory_supports_seat_and_rejects_traversal(
    tmp_path: Path,
) -> None:
    trial_dir = tmp_path / "job" / "trial"
    trial_dir.mkdir(parents=True)
    _write_seat_trajectories(trial_dir)
    url = "/api/jobs/job/trials/trial/trajectory"

    with TestClient(create_app(tmp_path)) as client:
        root = client.get(url)
        chef0 = client.get(url, params={"seat": "chef0"})
        chef1 = client.get(url, params={"seat": "chef1"})
        missing = client.get(url, params={"seat": "missing"})
        traversal = client.get(url, params={"seat": "../agent"})
        both = client.get(url, params={"step": "s1", "seat": "chef0"})

    assert root.status_code == 200 and root.json() is None
    assert chef0.status_code == 200 and chef0.json() == {"seat": "chef0"}
    assert chef1.status_code == 200 and chef1.json() == {"seat": "chef1"}
    assert missing.status_code == 404
    assert traversal.status_code == 400
    assert both.status_code == 400


def test_api_adapter_seat_prefix_and_trajectory_query(tmp_path: Path) -> None:
    assert ViewerApiAdapter.seat_prefix("chef0") == "seats/chef0/"
    for seat in ("../x", "a/b", ""):
        with pytest.raises(HTTPException) as error:
            ViewerApiAdapter.seat_prefix(seat)
        assert error.value.status_code == 400

    job, trial, trial_dir = write_job(tmp_path)
    _write_seat_trajectories(trial_dir)
    app = FastAPI()
    register_api_adapter(app, RemoteFixture(tmp_path))
    url = f"/api/jobs/{job.id}/trials/{trial.id}/trajectory"

    with TestClient(app) as client:
        seat = client.get(url, params={"seat": "chef0"})
        both = client.get(url, params={"step": "s1", "seat": "chef0"})

    assert seat.status_code == 200 and seat.json() == {"seat": "chef0"}
    assert both.status_code == 400
