"""A seat task runs its participants, so preflight checks each of them.

The roster is the one that showed the gap: an oracle in ``p1`` and a
terminus-2 in ``p2`` whose model has no API key in the environment.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from harbor.agents.factory import AgentFactory
from harbor.cli.jobs import _run_preflight
from harbor.cli.main import app
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import (
    AgentConfig,
    Participant,
    TaskConfig,
    TrialConfig,
)
from harbor.server.resolution import resolve_config, validate_trials

# Native logins live in the home directory; keep the checks to the environment.
pytestmark = pytest.mark.usefixtures("isolated_home")

ROSTER = [
    {"seat": "p1", "name": "oracle"},
    {"seat": "p2", "name": "terminus-2", "model_name": "deepseek/deepseek-flash"},
]
NO_KEY = "Participant for seat 'p2': terminus-2: no nonempty model API key selected"


def _participants() -> list[Participant]:
    return [Participant.model_validate(participant) for participant in ROSTER]


def _seat_task(root: Path, name: str = "hello") -> Path:
    task_dir = root / name
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "docker-compose.yaml").write_text(
        "services:\n  main: {}\n  p1: {}\n  p2: {}\n"
    )
    (task_dir / "instructions").mkdir()
    for seat in ("p1", "p2"):
        (task_dir / "instructions" / f"{seat}.md").write_text(f"You are {seat}.")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    (task_dir / "task.toml").write_text(
        "[agent]\ntimeout_sec = 30\n[agents.p1]\n[agents.p2]\nunder_test = false\n"
    )
    return task_dir


def _plain_task(root: Path) -> Path:
    task_dir = root / "plain"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "Dockerfile").write_text("FROM ubuntu:24.04\n")
    (task_dir / "instruction.md").write_text("Do nothing.")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    (task_dir / "task.toml").write_text("[agent]\ntimeout_sec = 10\n")
    return task_dir


def _seat_job(tmp_path: Path, **kwargs) -> JobConfig:
    return JobConfig(
        job_name="seats",
        jobs_dir=tmp_path / "jobs",
        tasks=[
            TaskConfig(path=_seat_task(tmp_path, "one")),
            TaskConfig(path=_seat_task(tmp_path, "two")),
        ],
        participants=ROSTER,
        n_attempts=2,
        **kwargs,
    )


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")


@pytest.fixture
def checked(monkeypatch) -> list[tuple[str | None, str | None, bool]]:
    """Every config AgentFactory.run_preflight sees, as (seat, agent, skip)."""
    calls = []
    run_preflight = AgentFactory.run_preflight

    def record(config, *, bridge=None, skip_credential_checks=False):
        calls.append(
            (getattr(config, "seat", None), config.name, skip_credential_checks)
        )
        run_preflight(
            config, bridge=bridge, skip_credential_checks=skip_credential_checks
        )

    monkeypatch.setattr(AgentFactory, "run_preflight", record)
    return calls


def _start_trial(tmp_path: Path, monkeypatch, **config):
    """Run ``harbor trial start`` with Trial.create replaced by a recorder."""
    created = []

    async def create(trial_config: TrialConfig):
        created.append(trial_config)

        async def run():
            return SimpleNamespace(
                trial_name=trial_config.trial_name,
                task_name="task",
                started_at=None,
                finished_at=None,
                exception_info=None,
                verifier_result=None,
            )

        return SimpleNamespace(run=run)

    monkeypatch.setattr("harbor.trial.trial.Trial.create", create)
    config_path = tmp_path / "trial.json"
    config_path.write_text(json.dumps({"task": {"path": str(tmp_path)}, **config}))
    result = CliRunner().invoke(app, ["trial", "start", "--config", str(config_path)])
    return result, created


def test_missing_model_credential_names_the_seat():
    with pytest.raises(ValueError, match=NO_KEY) as error:
        AgentFactory.run_participants_preflight(_participants())

    assert "DEEPSEEK_API_KEY" in str(error.value)
    assert "p1" not in str(error.value)


def test_every_failing_seat_is_named_in_one_error():
    roster = [
        Participant(seat="p1", import_path="os:path"),
        Participant(seat="ok", name="nop"),
        *_participants()[1:],
    ]

    with pytest.raises(ValueError) as error:
        AgentFactory.run_participants_preflight(roster)

    lines = str(error.value).splitlines()
    assert lines[0] == (
        "Participant for seat 'p1': Imported agent 'os:path' must be a class"
    )
    assert lines[1].startswith(NO_KEY)
    assert len(lines) == 2


def test_skip_credential_checks_defers_only_the_credential_check():
    AgentFactory.run_participants_preflight(
        _participants(), skip_credential_checks=True
    )

    bad_format = Participant(
        seat="p2",
        name="codex",
        model_name="openai/gpt-6",
        model_api_format="anthropic_messages",
    )
    with pytest.raises(ValueError, match="seat 'p2': Unsupported API format"):
        AgentFactory.run_participants_preflight(
            [bad_format], skip_credential_checks=True
        )


def test_roster_with_credentials_passes(api_key, checked):
    AgentFactory.run_participants_preflight(_participants())

    assert checked == [("p1", "oracle", False), ("p2", "terminus-2", False)]


def test_empty_roster_checks_nothing(checked):
    AgentFactory.run_participants_preflight([])

    assert checked == []


@pytest.mark.parametrize("install_only", [False, True])
def test_trial_start_checks_participants_before_creating_the_trial(
    tmp_path: Path, monkeypatch, install_only
):
    result, created = _start_trial(
        tmp_path, monkeypatch, participants=ROSTER, install_only=install_only
    )

    if install_only:
        assert result.exit_code == 0, result.exception
        assert len(created) == 1
    else:
        assert result.exit_code == 1
        assert NO_KEY in str(result.exception.__cause__)
        assert created == []


def test_trial_start_creates_the_trial_for_a_roster_with_credentials(
    tmp_path: Path, monkeypatch, api_key
):
    result, created = _start_trial(tmp_path, monkeypatch, participants=ROSTER)

    assert result.exit_code == 0, result.exception
    assert [p.seat for p in created[0].participants] == ["p1", "p2"]


@pytest.mark.parametrize(
    "bypass", [{}, {"skip_credential_checks": True}, {"install_only": True}]
)
def test_job_cli_preflight_checks_participants(monkeypatch, bypass):
    monkeypatch.setattr(
        "harbor.environments.factory.EnvironmentFactory.run_preflight", lambda **_: None
    )
    config = JobConfig(participants=ROSTER, **bypass)

    if bypass:
        _run_preflight(config)
    else:
        with pytest.raises(SystemExit) as error:
            _run_preflight(config)
        assert error.value.code == 1


@pytest.mark.parametrize(
    "bypass", [{}, {"skip_credential_checks": True}, {"install_only": True}]
)
async def test_job_create_checks_participants_before_any_trial(tmp_path: Path, bypass):
    config = _seat_job(tmp_path, **bypass)

    if not bypass:
        with pytest.raises(ValueError, match=NO_KEY):
            await Job.create(config)
        assert not (tmp_path / "jobs").exists()
        return

    job = await Job.create(config)
    job._close_logger_handlers()
    assert len(job) == 4


async def test_job_checks_the_roster_once_for_all_its_trials(
    tmp_path: Path, api_key, checked
):
    job = await Job.create(_seat_job(tmp_path))
    job._close_logger_handlers()

    assert len(job) == 4
    assert checked == [
        (None, "oracle", False),
        ("p1", "oracle", False),
        ("p2", "terminus-2", False),
    ]


def test_job_that_runs_no_agent_checks_no_participant(checked):
    config = JobConfig(participants=ROSTER)

    Job._preflight_agents(config, [])

    assert checked == []


async def test_server_resolution_checks_participants(tmp_path: Path):
    config = _seat_job(tmp_path)

    with pytest.raises(ValueError, match=NO_KEY):
        await resolve_config(config)

    resolved = await resolve_config(config, skip_credential_checks=True)
    assert len(resolved.trials) == 4


async def test_server_checks_the_roster_once_for_all_its_trials(
    tmp_path: Path, api_key, checked
):
    resolved = await resolve_config(_seat_job(tmp_path))

    assert [seat for seat, _, _ in checked if seat] == ["p1", "p2"]

    # The server validates a queued trial again, alone, before it runs.
    checked.clear()
    await validate_trials(
        resolved.trials[:1], resolved.lock.trials[:1], skip_credential_checks=True
    )
    assert checked == [
        (None, "oracle", True),
        ("p1", "oracle", True),
        ("p2", "terminus-2", True),
    ]


def test_configs_without_participants_check_only_their_agent(
    tmp_path: Path, monkeypatch, checked
):
    monkeypatch.setattr(
        "harbor.environments.factory.EnvironmentFactory.run_preflight", lambda **_: None
    )
    config = JobConfig(agents=[AgentConfig(name="nop")])
    only_the_agent = [(None, "nop", False)]

    _run_preflight(config)
    assert checked == only_the_agent

    checked.clear()
    Job._preflight_agents(config, config.agents)
    assert checked == only_the_agent

    checked.clear()
    result, created = _start_trial(tmp_path, monkeypatch, agent={"name": "nop"})
    assert result.exit_code == 0, result.exception
    assert checked == only_the_agent
    assert len(created) == 1


async def test_server_checks_only_the_agent_of_a_config_without_participants(
    tmp_path: Path, checked
):
    config = JobConfig(
        tasks=[TaskConfig(path=_plain_task(tmp_path))],
        agents=[AgentConfig(name="nop")],
        n_attempts=2,
    )

    await resolve_config(config)

    assert checked == [(None, "nop", False)] * 2
