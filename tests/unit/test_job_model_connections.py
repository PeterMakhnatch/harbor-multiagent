from pathlib import Path
from unittest.mock import Mock

import pytest

from harbor.agents.agent_auth_resolution import MissingModelCredentialError
from harbor.agents.oracle import OracleAgent
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import (
    AgentConfig,
    Participant,
    SourceTrialConfig,
    TaskConfig,
    TrialConfig,
)


@pytest.fixture
def seat_job_config(tmp_path: Path) -> JobConfig:
    seats = ("north", "south", "east", "west")
    task_dir = tmp_path / "task"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "docker-compose.yaml").write_text(
        "services:\n  main: {}\n" + "".join(f"  {seat}: {{}}\n" for seat in seats)
    )
    (task_dir / "instructions").mkdir()
    for seat in seats:
        (task_dir / "instructions" / f"{seat}.md").write_text("Play your seat.")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    task_toml = "".join(f"[agents.{seat}]\n" for seat in seats)
    (task_dir / "task.toml").write_text(task_toml)
    return JobConfig(
        jobs_dir=tmp_path / "jobs",
        tasks=[TaskConfig(path=task_dir)],
        agents=[AgentConfig()],
        participants=[
            Participant(seat="north", name="nop"),
            Participant(seat="south", name="nop"),
            Participant(seat="east", name="nop"),
            Participant(
                seat="west",
                name="mini-swe-agent",
                model_name="openai/gpt-6",
                model_api_key_name="SEAT_JOB_TEST_API_KEY",
            ),
        ],
    )


@pytest.mark.unit
async def test_job_create_plans_one_trial_for_four_participants(
    seat_job_config: JobConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEAT_JOB_TEST_API_KEY", "test-only-key")
    monkeypatch.setattr(
        OracleAgent,
        "preflight",
        Mock(side_effect=ValueError("The unused placeholder cannot be preflighted.")),
    )

    job = await Job.create(seat_job_config)
    try:
        assert len(job) == 1
        trial_config = job._trial_configs[0]
        assert trial_config.agent == AgentConfig()
        assert trial_config.participants == seat_job_config.participants
    finally:
        job._close_logger_handlers()


@pytest.mark.unit
async def test_job_create_rejects_missing_participant_credentials(
    seat_job_config: JobConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SEAT_JOB_TEST_API_KEY", raising=False)

    with pytest.raises(MissingModelCredentialError, match="SEAT_JOB_TEST_API_KEY"):
        await Job.create(seat_job_config)


@pytest.mark.unit
async def test_job_create_rejects_invalid_participant_agent(
    seat_job_config: JobConfig,
) -> None:
    name = "invalid-seat-agent"
    seat_job_config.participants[-1] = Participant(seat="west", name=name)

    with pytest.raises(ValueError, match=f"Agent name {name} is not valid"):
        await Job.create(seat_job_config)


@pytest.mark.parametrize("include_rerun", [False, True])
def test_reporting_skips_unused_agent_credentials_for_regrades(
    tmp_path, monkeypatch, include_rerun
):
    monkeypatch.delenv("UNUSED_AGENT_KEY", raising=False)
    regrade = TrialConfig(
        task=TaskConfig(path=tmp_path),
        agent=AgentConfig(
            name="codex",
            model_name="openai/gpt-6",
            model_api_key_name="UNUSED_AGENT_KEY",
        ),
        source_trial=SourceTrialConfig(
            action="regrade", type="local", path=tmp_path / "source"
        ),
    )
    job = Job.__new__(Job)
    job.config = JobConfig()
    job._logger = Mock()
    job._remaining_trial_configs = [regrade]
    if include_rerun:
        job._remaining_trial_configs.append(
            TrialConfig(
                task=TaskConfig(path=tmp_path),
                agent=AgentConfig(
                    name="codex",
                    model_name="openai/gpt-6",
                    model_api_key_name="RERUN_KEY",
                    env={"RERUN_KEY": "selected-key"},
                ),
            )
        )
    job._report_model_connections()
    if include_rerun:
        assert job._logger.debug.call_args_list[0].args[1:] == (1, 1)
        assert job._logger.debug.call_args_list[1].args[4] == "RERUN_KEY"
    else:
        job._logger.debug.assert_not_called()


def test_reporting_still_checks_credentials_for_inference_trials(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_AGENT_KEY", raising=False)
    job = Job.__new__(Job)
    job.config = JobConfig()
    job._logger = Mock()
    job._remaining_trial_configs = [
        TrialConfig(
            task=TaskConfig(path=tmp_path),
            agent=AgentConfig(
                name="codex",
                model_name="openai/gpt-6",
                model_api_key_name="MISSING_AGENT_KEY",
            ),
        )
    ]
    with pytest.raises(ValueError, match="MISSING_AGENT_KEY"):
        job._report_model_connections()


@pytest.mark.parametrize("action", ["diff", "regrade"])
def test_cli_defers_derived_agent_preflight_until_trial_plan(
    tmp_path, monkeypatch, action
):
    from harbor.cli.jobs import _run_preflight
    from harbor.models.job.config import SourceJobConfig

    monkeypatch.delenv("UNUSED_AGENT_KEY", raising=False)
    environment_preflight = Mock()
    monkeypatch.setattr(
        "harbor.environments.factory.EnvironmentFactory.run_preflight",
        environment_preflight,
    )
    config = JobConfig(
        agents=[
            AgentConfig(
                name="codex",
                model_name="openai/gpt-6",
                model_api_key_name="UNUSED_AGENT_KEY",
            )
        ],
        source_jobs=[SourceJobConfig(action=action, type="local", path=tmp_path)],
    )
    _run_preflight(config)
    environment_preflight.assert_called_once()
