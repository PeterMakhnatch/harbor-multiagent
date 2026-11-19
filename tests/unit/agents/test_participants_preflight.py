"""A seat task runs its participants, so preflight checks each of them, and a
job checks that its roster fits every task before any trial starts.

The roster is the one that showed the gap: an oracle in ``p1`` and a
terminus-2 in ``p2`` whose model has no API key in the environment.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from harbor.agents.factory import AgentFactory
from harbor.agents.nop import NopAgent
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


class FailingPreflightAgent(NopAgent):
    """A harness whose own preflight fails, as one that is not installed."""

    error: BaseException = RuntimeError("the harness is not installed")

    @classmethod
    def preflight(cls, kwargs=None, env=None) -> None:
        raise cls.error


FAILING = f"{__name__}:FailingPreflightAgent"
# A seat trial never runs agent or user_agent; these would fail if checked.
UNUSED_AGENT = {"import_path": FAILING}
UNUSED_USER_AGENT = {"import_path": FAILING, "bridge": {"kind": "acp"}}


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
    return JobConfig.model_validate(
        {
            "job_name": "seats",
            "jobs_dir": tmp_path / "jobs",
            "tasks": [
                TaskConfig(path=_seat_task(tmp_path, "one")),
                TaskConfig(path=_seat_task(tmp_path, "two")),
            ],
            "participants": ROSTER,
            "n_attempts": 2,
            **kwargs,
        }
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


@pytest.fixture
def no_environment_preflight(monkeypatch):
    monkeypatch.setattr(
        "harbor.environments.factory.EnvironmentFactory.run_preflight", lambda **_: None
    )


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


def test_a_seat_that_fails_another_way_does_not_hide_the_others():
    roster = [Participant(seat="p1", import_path=FAILING), *_participants()[1:]]

    with pytest.raises(ValueError) as error:
        AgentFactory.run_participants_preflight(roster)

    lines = str(error.value).splitlines()
    assert lines[0] == (
        "Participant for seat 'p1': RuntimeError: the harness is not installed"
    )
    assert lines[1].startswith(NO_KEY)
    assert len(lines) == 2


@pytest.mark.parametrize(
    "interrupt", [KeyboardInterrupt, SystemExit, asyncio.CancelledError]
)
def test_an_interrupt_is_not_collected(monkeypatch, interrupt):
    monkeypatch.setattr(FailingPreflightAgent, "error", interrupt())
    roster = [Participant(seat="p1", import_path=FAILING), *_participants()[1:]]

    with pytest.raises(interrupt):
        AgentFactory.run_participants_preflight(roster)


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
    "unused",
    [{"agent": UNUSED_AGENT}, {"user_agent": UNUSED_USER_AGENT}],
    ids=["agent", "user_agent"],
)
def test_trial_start_checks_participants_in_place_of_agent(
    tmp_path: Path, monkeypatch, checked, unused
):
    result, created = _start_trial(tmp_path, monkeypatch, participants=ROSTER, **unused)

    assert result.exit_code == 1
    assert NO_KEY in str(result.exception.__cause__)
    assert [seat for seat, _, _ in checked] == ["p1", "p2"]
    assert created == []


@pytest.mark.parametrize(
    "bypass", [{}, {"skip_credential_checks": True}, {"install_only": True}]
)
def test_job_cli_preflight_checks_participants(no_environment_preflight, bypass):
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
    assert checked == [("p1", "oracle", False), ("p2", "terminus-2", False)]


def test_job_that_runs_no_agent_checks_no_participant(checked):
    config = JobConfig(participants=ROSTER)

    Job._preflight_agents(config, [])

    assert checked == []


@pytest.mark.parametrize(
    "unused",
    [{"agents": [UNUSED_AGENT]}, {"user_agent": UNUSED_USER_AGENT}],
    ids=["agents", "user_agent"],
)
async def test_jobs_check_participants_in_place_of_agents(
    tmp_path: Path, no_environment_preflight, checked, unused
):
    config = _seat_job(tmp_path, **unused)

    with pytest.raises(SystemExit):
        _run_preflight(config)
    with pytest.raises(ValueError, match=NO_KEY):
        await Job.create(config)
    with pytest.raises(ValueError, match=NO_KEY):
        await resolve_config(config)
    assert [seat for seat, _, _ in checked] == ["p1", "p2"] * 3


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

    assert [seat for seat, _, _ in checked] == ["p1", "p2"]

    # The server validates a queued trial again, alone, before it runs.
    checked.clear()
    await validate_trials(
        resolved.trials[:1], resolved.lock.trials[:1], skip_credential_checks=True
    )
    assert checked == [("p1", "oracle", True), ("p2", "terminus-2", True)]


@pytest.mark.parametrize(
    "change, problem",
    [
        ({"participants": []}, r"seats without a participant: \['p1', 'p2'\]"),
        ({"participants": ROSTER[1:]}, r"seats without a participant: \['p1'\]"),
        (
            {"participants": [*ROSTER, {"seat": "p3", "name": "nop"}]},
            r"participants for seats the task does not have: \['p3'\]",
        ),
        ({"agents": [{"name": "nop"}]}, "agent is set; a seat task takes participants"),
        (
            {"user_agent": {"name": "nop", "bridge": {"kind": "acp"}}},
            "user_agent is not supported for seat tasks",
        ),
    ],
    ids=["no-participants", "missing-seat", "unknown-seat", "agent", "user_agent"],
)
async def test_roster_that_does_not_fit_the_tasks_fails_before_any_trial(
    tmp_path: Path, api_key, change, problem
):
    config = _seat_job(tmp_path, **change)
    message = f"Task one cannot run: {problem}"

    with pytest.raises(ValueError, match=message):
        await Job.create(config)
    assert not (tmp_path / "jobs").exists()

    with pytest.raises(ValueError, match=message):
        await resolve_config(config)


async def test_participants_for_a_task_without_seats_fail_before_any_trial(
    tmp_path: Path, api_key
):
    config = _seat_job(tmp_path, tasks=[TaskConfig(path=_plain_task(tmp_path))])
    message = (
        r"Task plain has no \[agents.<service>\] seats, so it takes agent, "
        "not participants"
    )

    with pytest.raises(ValueError, match=message):
        await Job.create(config)
    assert not (tmp_path / "jobs").exists()

    with pytest.raises(ValueError, match=message):
        await resolve_config(config)


async def test_single_agent_job_with_a_seat_task_fails_before_any_trial(
    tmp_path: Path,
):
    config = JobConfig(
        job_name="mixed",
        jobs_dir=tmp_path / "jobs",
        tasks=[
            TaskConfig(path=_plain_task(tmp_path)),
            TaskConfig(path=_seat_task(tmp_path)),
        ],
        agents=[AgentConfig(name="nop")],
    )

    with pytest.raises(
        ValueError, match=r"Task hello cannot run: seats without a participant"
    ):
        await Job.create(config)
    assert not (tmp_path / "jobs").exists()


async def test_single_agent_job_leaves_a_task_that_does_not_load_to_its_trial(
    tmp_path: Path,
):
    task_dir = _plain_task(tmp_path)
    (task_dir / "task.toml").write_text("[agent\n")
    config = JobConfig(
        job_name="plain",
        jobs_dir=tmp_path / "jobs",
        tasks=[TaskConfig(path=task_dir)],
        agents=[AgentConfig(name="nop")],
    )

    job = await Job.create(config)
    job._close_logger_handlers()

    assert len(job) == 1


def test_configs_without_participants_check_only_their_agent(
    tmp_path: Path, monkeypatch, no_environment_preflight, checked
):
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
