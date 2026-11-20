"""SeatsTrial end to end against a fake Compose runner and scripted agents.

Order comes from events, never from sleeps. Timeout tests use real deadlines.
"""

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import (
    AgentAuthenticationError,
    NonZeroAgentExitCodeError,
)
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment, ExecResult, OutputCallback
from harbor.environments.docker.docker import DockerEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.result import JobStats
from harbor.models.trial.config import (
    AgentConfig,
    SourceTrialConfig,
    TaskConfig,
    TrialConfig,
    VerifierConfig,
)
from harbor.models.trial.result import TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.server.executor import TrialExecutor
from harbor.trial.hooks import LogEntry, TrialEvent, TrialHookEvent
from harbor.trial.seats import SeatsTrial
from harbor.trial.single_step import SingleStepTrial
from harbor.trial.trial import Trial

EVENTS: list[tuple[str, ...]] = []
WAITERS: dict[tuple[str, ...], asyncio.Event] = {}
RELEASE: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)


def note(*event: str) -> None:
    EVENTS.append(event)
    if event in WAITERS:
        WAITERS[event].set()


async def seen(*event: str) -> None:
    if event not in EVENTS:
        await WAITERS.setdefault(event, asyncio.Event()).wait()


def index(*event: str) -> int:
    return EVENTS.index(event)


class ScriptedAgent(BaseAgent):
    """Ends at once, or when released if ``hold``, or as ``fail`` says."""

    def __init__(self, *args, hold: bool = False, fail: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.hold = hold
        self.fail = fail

    @staticmethod
    def name() -> str:
        return "scripted"

    def version(self) -> str:
        return "1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        assert isinstance(environment, SeatEnvironment)
        note("setup", environment.service)
        await environment.exec(f"echo {environment.service} setup")
        if self.fail == "setup":
            await asyncio.Event().wait()

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        assert isinstance(environment, SeatEnvironment)
        seat = environment.service
        note("run", seat, instruction)
        self.logger.info("working")
        await environment.exec(f"echo {seat} is working")
        for key, value in self.extra_env.items():
            (self.logs_dir / "log.txt").write_text(f"{key}={value}\n")
        try:
            if self.fail == "exit":
                raise NonZeroAgentExitCodeError("exit 1")
            if self.fail == "auth":
                raise AgentAuthenticationError("401 invalid api key")
            if self.fail == "crash":
                raise RuntimeError(f"{seat} crashed")
            if self.hold:
                await RELEASE[seat].wait()
            context.cost_usd = 0.5
            note("ended", seat)
        except asyncio.CancelledError:
            note("stopped", seat)
            if self.fail == "crash-on-cancel":
                raise RuntimeError(f"{seat} crashed while stopping")
            raise


AGENT = f"{__name__}:ScriptedAgent"


def _task_dir(root: Path, timeout_sec: float = 30, extra: str = "") -> Path:
    task_dir = root / "hello"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "docker-compose.yaml").write_text(
        "services:\n  main: {}\n  maya: {}\n  raj: {}\n  lee: {}\n"
    )
    (task_dir / "instructions").mkdir()
    for seat in ("maya", "raj", "lee"):
        (task_dir / "instructions" / f"{seat}.md").write_text(f"You are {seat}.")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    (task_dir / "task.toml").write_text(
        f"[agent]\ntimeout_sec = {timeout_sec}\n{extra}\n"
        "[agents.maya]\n[agents.raj]\n[agents.lee]\nunder_test = false\n"
    )
    return task_dir


def _participant(seat: str, **kwargs: Any) -> dict[str, Any]:
    return {"seat": seat, "import_path": AGENT, "kwargs": kwargs}


def _config(task_dir: Path, *participants: dict[str, Any], **kwargs) -> TrialConfig:
    return TrialConfig(
        task=TaskConfig(path=task_dir),
        trials_dir=task_dir.parent / "trials",
        participants=list(participants),
        **kwargs,
    )


def _service_and_user(command: list[str]) -> tuple[str, str]:
    service = command[command.index("sh") - 1]
    user = command[command.index("-u") + 1] if "-u" in command else ""
    return service, user


async def compose(
    command: list[str], *, on_output: OutputCallback | None = None, **_: Any
) -> ExecResult:
    note("compose", *command)
    if command[0] != "exec":
        return ExecResult(stdout="", return_code=0)
    stdout = "1000\n" if command[-1] == "exec bash -c 'id -u'" else ""
    if on_output is not None:
        # Docker merges stderr into stdout in its Compose subprocess.
        await asyncio.sleep(0)
        await on_output(stdout or command[-1], "stdout")
    return ExecResult(stdout=stdout, return_code=0)


def _fake_compose(trial: Trial) -> None:
    project = trial.agent_environment
    assert isinstance(project, DockerEnvironment)
    project._run_docker_compose_command = AsyncMock(side_effect=compose)
    project.start = AsyncMock()
    project.stop = AsyncMock(side_effect=lambda delete: note("stop"))


async def _seats_trial(config: TrialConfig) -> SeatsTrial:
    EVENTS.clear()
    WAITERS.clear()
    RELEASE.clear()
    trial = await Trial.create(config)
    assert isinstance(trial, SeatsTrial)
    _fake_compose(trial)

    # Keep the real verifier phase and its log context, without running tests.
    async def verify(*_: Any) -> VerifierResult:
        note("verify")
        await trial.agent_environment.exec("echo verifying")
        return VerifierResult(rewards={"maya": 1})

    trial._verify_with_timeout = AsyncMock(side_effect=verify)
    return trial


def _verified(trial: SeatsTrial) -> bool:
    return ("verify",) in EVENTS


def _seat_results(result) -> dict[str, Any]:
    return {seat.seat: seat for seat in result.seat_results or []}


def _record_seat_hooks(trial: SeatsTrial) -> None:
    async def capture(event: TrialHookEvent) -> None:
        assert event.seat is not None
        note("hook", event.event.value, event.seat)

    trial.add_hook(TrialEvent.SEAT_START, capture)
    trial.add_hook(TrialEvent.SEAT_END, capture)


def _assert_seat_hooks(trial: SeatsTrial) -> None:
    hooks = [event[1:] for event in EVENTS if event[0] == "hook"]
    assert sorted(hooks) == sorted(
        (event, seat.name)
        for seat in trial.seats
        for event in ("seat-start", "seat-end")
    )
    for seat in trial.seats:
        start = index("hook", "seat-start", seat.name)
        end = index("hook", "seat-end", seat.name)
        assert start < index("run", seat.name, seat.instruction) < end
        for event in ("ended", "stopped"):
            if (event, seat.name) in EVENTS:
                assert index(event, seat.name) < end


async def test_trial_create_routes_seat_tasks(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
        )
    )

    assert [seat.name for seat in trial.seats] == ["maya", "raj", "lee"]
    assert trial.agent is trial.seats[0].agent
    assert trial.seats[2].under_test is False
    assert trial.seats[0].paths.agent_dir == trial.paths.trial_dir / "seats/maya/agent"


async def test_ordinary_trial_keeps_unattributed_hooks_and_logs(tmp_path: Path):
    task_dir = tmp_path / "plain"
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "environment" / "Dockerfile").write_text("FROM ubuntu\n")
    (task_dir / "instruction.md").write_text("Do it.")
    (task_dir / "task.toml").write_text("[agent]\ntimeout_sec = 5\n")
    (task_dir / "tests").mkdir()
    (task_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
    plain = TrialConfig(
        task=TaskConfig(path=task_dir),
        trials_dir=tmp_path / "trials",
        agent=AgentConfig(name="nop"),
        verifier=VerifierConfig(disable=True),
    )

    trial = await Trial.create(plain)
    assert type(trial) is SingleStepTrial
    _fake_compose(trial)
    entries: list[LogEntry] = []
    events: list[TrialHookEvent] = []

    async def log(entry: LogEntry) -> None:
        entries.append(entry)

    async def hook(event: TrialHookEvent) -> None:
        events.append(event)

    async def exec_in_agent(environment: BaseEnvironment, **_: Any) -> None:
        await environment.exec("echo ordinary trial")

    trial.agent.setup = AsyncMock(side_effect=exec_in_agent)
    trial.agent.run = AsyncMock(side_effect=exec_in_agent)
    trial.add_log_callback(log)
    for event in TrialEvent:
        trial.add_hook(event, hook)

    result = await trial.run()

    assert result.exception_info is None
    assert [(entry.phase, entry.seat) for entry in entries] == [
        ("agent_setup", None),
        ("agent", None),
    ]
    assert [(event.event, event.seat) for event in events] == [
        (TrialEvent.START, None),
        (TrialEvent.ENVIRONMENT_START, None),
        (TrialEvent.AGENT_START, None),
        (TrialEvent.AGENT_END, None),
        (TrialEvent.END, None),
    ]

    with pytest.raises(ValueError, match="takes agent, not participants"):
        await Trial.create(
            plain.model_copy(update={"participants": [_participant("maya")]})
        )


async def test_regrade_refuses_seat_tasks(tmp_path: Path):
    config = _config(
        _task_dir(tmp_path),
        _participant("maya"),
        _participant("raj"),
        _participant("lee"),
        source_trial=SourceTrialConfig(action="regrade", type="local", path=tmp_path),
    )

    with pytest.raises(ValueError, match="Regrade does not support seat tasks"):
        await Trial.create(config)


@pytest.mark.parametrize(
    "participants, kwargs, extra, message",
    [
        (["maya", "raj"], {}, "", r"seats without a participant: \['lee'\]"),
        (
            ["maya", "raj", "lee", "zed"],
            {},
            "",
            r"participants for seats the task does not have: \['zed'\]",
        ),
        (
            ["maya", "raj", "lee"],
            {"agent": AgentConfig(name="claude-code")},
            "",
            "agent is set; a seat task takes participants",
        ),
        (
            ["maya", "raj", "lee"],
            {},
            "network_mode = 'no-network'",
            "only public network_mode",
        ),
        (
            ["maya", "raj", "lee"],
            {},
            "[verifier]\nenvironment_mode = 'separate'",
            r"must run in main \(shared mode\)",
        ),
    ],
)
async def test_launch_refusals_come_before_any_environment(
    tmp_path: Path, participants, kwargs, extra, message
):
    config = _config(
        _task_dir(tmp_path, extra=extra),
        *(_participant(seat) for seat in participants),
        **kwargs,
    )

    with pytest.raises(ValueError, match=message):
        await Trial.create(config)


async def test_launch_refusals_are_one_error(tmp_path: Path):
    config = _config(
        _task_dir(tmp_path), _participant("maya"), agent=AgentConfig(name="nop")
    )

    with pytest.raises(ValueError) as error:
        await Trial.create(config)

    assert str(error.value).count(";") == 2


async def test_play_ends_when_agents_under_test_end_and_the_rest_are_stopped(
    tmp_path: Path,
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj", hold=True),
            _participant("lee", hold=True),
        )
    )
    _record_seat_hooks(trial)

    run = asyncio.create_task(trial.run())
    await seen("ended", "maya")
    assert ("stopped", "lee") not in EVENTS
    RELEASE["raj"].set()
    result = await run

    assert result.exception_info is None
    _assert_seat_hooks(trial)
    assert index("ended", "raj") < index("stopped", "lee")
    for seat in trial.seats:
        download = ("compose", "cp", f"{seat.name}:/logs/agent/.")
        download += (str(seat.paths.agent_dir),)
        assert index("stopped", "lee") < index(*download) < index("verify")
    upload = next(e for e in EVENTS if e[-1] == "main:/logs/seats")
    assert upload[2] == f"{trial.paths.trial_dir / 'seats'}/."
    assert index(*upload) < index("verify") < index("stop")
    assert result.verifier_result is not None
    assert result.verifier_result.rewards == {"maya": 1}
    seats = _seat_results(result)
    assert seats["lee"].exception_info is None
    assert seats["maya"].agent_result.cost_usd == 0.5
    assert result.compute_token_cost_totals()[3] == 1.0


async def test_a_cancelled_trial_stays_cancelled_over_seat_errors(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya", hold=True),
            _participant("raj"),
            _participant("lee", hold=True, fail="crash-on-cancel"),
        )
    )

    async def capture(event: TrialHookEvent) -> None:
        note(event.event.value)

    _record_seat_hooks(trial)
    for event in (TrialEvent.AGENT_END, TrialEvent.CANCEL, TrialEvent.END):
        trial.add_hook(event, capture)

    run = asyncio.create_task(trial.run())
    await seen("ended", "raj")
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    _assert_seat_hooks(trial)
    for seat in trial.seats:
        assert (
            index("agent-end")
            < index("cancel")
            < index("hook", "seat-end", seat.name)
            < index("end")
        )
    assert ("stopped", "lee") in EVENTS
    assert trial.result.exception_info is not None
    assert trial.result.exception_info.exception_type == "CancelledError"
    assert not _verified(trial)


async def test_every_seat_gets_its_own_instruction(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
            verifier=VerifierConfig(disable=True),
        )
    )

    await trial.run()

    runs = {e[1]: e[2] for e in EVENTS if e[0] == "run"}
    assert runs == {
        "maya": "You are maya.",
        "raj": "You are raj.",
        "lee": "You are lee.",
    }
    assert not any(e[-1] == "main:/logs/seats" for e in EVENTS)


async def test_seat_user_becomes_default_user_for_ownership_fixes(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
            verifier=VerifierConfig(disable=True),
        )
    )

    await trial.run()

    maya = trial.seats[0].environment
    assert maya.default_user == 1000
    id_command = next(e for e in EVENTS if e[-1].endswith("'id -u'") and "maya" in e)
    assert "-u" not in id_command
    work = next(e for e in EVENTS if "maya is working" in e[-1])
    assert _service_and_user(list(work[1:])) == ("maya", "")

    EVENTS.clear()
    codex = Codex(logs_dir=tmp_path / "codex", model_name="openai/gpt-5.5")
    await codex._upload_config_text(
        maya, content="{}", remote_path="/tmp/agent/config.json", filename="c.json"
    )

    execs = [e[1:] for e in EVENTS if e[:2] == ("compose", "exec")]
    fix = next(e for e in execs if "chown 1000 /tmp/agent/config.json" in e[-1])
    assert "chmod 600 /tmp/agent/config.json" in fix[-1]
    assert _service_and_user(list(fix)) == ("maya", "root")


async def test_agent_timeout_ends_everyone_still_running_and_still_grades(
    tmp_path: Path,
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path, timeout_sec=0.2),
            _participant("maya", hold=True),
            _participant("raj"),
            _participant("lee", hold=True),
        )
    )
    _record_seat_hooks(trial)

    result = await trial.run()

    _assert_seat_hooks(trial)
    seats = _seat_results(result)
    assert ("stopped", "maya") in EVENTS and ("stopped", "lee") in EVENTS
    assert seats["raj"].exception_info is None
    for seat in ("maya", "lee"):
        info = seats[seat].exception_info
        assert info is not None and info.exception_type == "AgentTimeoutError"
    assert result.exception_info is not None
    assert result.exception_info.exception_type == "AgentTimeoutError"
    assert result.exception_info.exception_message.startswith("Seat 'maya': ")
    assert _verified(trial) and result.verifier_result is not None


@pytest.mark.parametrize(
    "fail, error, graded",
    [
        ("exit", "NonZeroAgentExitCodeError", True),
        ("auth", "AgentAuthenticationError", False),
    ],
)
async def test_an_agent_failure_reaches_the_trial_and_job_stats_and_grades_by_policy(
    tmp_path: Path, fail: str, error: str, graded: bool
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya", fail=fail),
            _participant("raj"),
            _participant("lee", hold=True),
        )
    )
    _record_seat_hooks(trial)

    result = await trial.run()

    _assert_seat_hooks(trial)
    seats = _seat_results(result)
    assert seats["maya"].exception_info.exception_type == error
    assert result.exception_info is not None
    assert result.exception_info.exception_type == error
    assert result.exception_info.exception_message.startswith("Seat 'maya': ")
    assert "Seat 'maya'" in trial.paths.exception_message_path.read_text()
    assert ("ended", "raj") in EVENTS and ("stopped", "lee") in EVENTS
    assert _verified(trial) is graded
    stats = JobStats()
    stats.increment(result)
    assert stats.n_errored_trials == 1
    assert any(error in s.exception_stats for s in stats.evals.values())


async def test_a_seat_crash_fails_the_trial_and_ends_play(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya", hold=True),
            _participant("raj", hold=True),
            _participant("lee", fail="crash"),
        )
    )
    _record_seat_hooks(trial)

    result = await trial.run()

    _assert_seat_hooks(trial)
    assert result.exception_info is not None
    assert result.exception_info.exception_type == "RuntimeError"
    assert ("stopped", "maya") in EVENTS and ("stopped", "raj") in EVENTS
    assert not _verified(trial)
    assert EVENTS[-1] == ("stop",)


async def test_a_failed_log_download_warns_and_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
        )
    )
    raj = trial.seats[1]
    raj.environment.download_dir = AsyncMock(side_effect=RuntimeError("cp failed"))

    with caplog.at_level("ERROR"):
        result = await trial.run()

    assert result.exception_info is None
    assert f"Failed to download logs to {raj.paths.agent_dir}: cp failed" in (
        caplog.text
    )
    assert _verified(trial)


async def test_any_setup_failure_fails_the_trial_before_play(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            {**_participant("raj", fail="setup"), "override_setup_timeout_sec": 0.05},
            _participant("lee"),
        )
    )
    _record_seat_hooks(trial)

    result = await trial.run()

    assert result.exception_info is not None
    assert result.exception_info.exception_type == "AgentSetupTimeoutError"
    assert "raj" in result.exception_info.exception_message
    raj = _seat_results(result)["raj"].exception_info
    assert raj is not None and raj.exception_type == "AgentSetupTimeoutError"
    assert not any(e[0] == "run" for e in EVENTS)
    assert {e[1] for e in EVENTS if e[0] == "setup"} == {"maya", "raj", "lee"}
    assert not any(e[0] == "hook" for e in EVENTS)


async def test_install_only_sets_up_every_seat_and_plays_none(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
            install_only=True,
        )
    )
    _record_seat_hooks(trial)
    entries: list[LogEntry] = []

    async def capture(entry: LogEntry) -> None:
        entries.append(entry)

    trial.add_log_callback(capture)

    await trial.run()

    assert {e[1] for e in EVENTS if e[0] == "setup"} == {"maya", "raj", "lee"}
    assert not any(e[0] == "run" for e in EVENTS)
    assert not any(e[0] == "hook" for e in EVENTS)
    assert {(entry.phase, entry.seat) for entry in entries} == {
        ("agent_setup", "maya"),
        ("agent_setup", "raj"),
        ("agent_setup", "lee"),
    }


SCRUBBED = {seat: f"{seat}-value-to-scrub" for seat in ("maya", "raj")}


async def test_secrets_of_every_seat_are_scrubbed_in_each_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from harbor.trial import trial as trial_module

    original = trial_module.scrub_file_text
    calls: list[Path] = []

    def counting(path, text, secrets):
        calls.append(path)
        return original(path, text, secrets)

    monkeypatch.setattr(trial_module, "scrub_file_text", counting)
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            {**_participant("maya"), "env": {"MAYA_API_KEY": SCRUBBED["maya"]}},
            {**_participant("raj"), "env": {"RAJ_API_KEY": SCRUBBED["raj"]}},
            _participant("lee"),
            verifier=VerifierConfig(disable=True),
        )
    )

    await trial.run()

    for seat, secret in SCRUBBED.items():
        log = trial.paths.trial_dir / "seats" / seat / "agent" / "log.txt"
        assert secret not in log.read_text()
        assert calls.count(log) == 2


async def test_result_json_records_every_seat(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
            verifier=VerifierConfig(disable=True),
        )
    )

    await trial.run()

    data = json.loads(trial.paths.result_path.read_text())
    assert data["agent_info"]["name"] == "scripted"
    assert [(s["seat"], s["under_test"]) for s in data["seat_results"]] == [
        ("maya", True),
        ("raj", True),
        ("lee", False),
    ]
    for seat in data["seat_results"]:
        assert seat["agent_info"]["name"] == "scripted"
        assert seat["agent_setup"]["finished_at"] is not None
        assert seat["agent_execution"]["finished_at"] is not None
    config = json.loads(trial.paths.config_path.read_text())
    assert [p["seat"] for p in config["participants"]] == ["maya", "raj", "lee"]
    assert "agent" not in config
    reloaded = TrialResult.model_validate_json(trial.paths.result_path.read_text())
    assert [p.seat for p in reloaded.config.participants] == ["maya", "raj", "lee"]


async def test_each_seat_logs_under_its_own_name(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
            verifier=VerifierConfig(disable=True),
        )
    )
    await trial.run()

    for seat in trial.seats:
        assert seat.agent.logger.name.startswith(
            f"{trial.logger.name}.seats.{seat.name}."
        )
        assert seat.environment.logger.name.startswith(
            f"{trial.logger.name}.seats.{seat.name}."
        )


async def test_log_streaming_attributes_concurrent_seat_output(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya"),
            _participant("raj"),
            _participant("lee"),
        )
    )
    entries: list[LogEntry] = []

    async def capture(entry: LogEntry) -> None:
        entries.append(entry)

    trial.add_log_callback(capture)

    result = await trial.run()

    assert result.exception_info is None
    assert result.verifier_result is not None
    assert all(entry.trial_id == trial.id for entry in entries)
    assert {entry.stream for entry in entries} == {"stdout"}
    for seat in ("maya", "raj", "lee"):
        setup = [
            entry.text
            for entry in entries
            if entry.phase == "agent_setup" and entry.seat == seat
        ]
        assert "1000\n" in setup
        assert any("mkdir -p /logs/agent" in text for text in setup)
        assert any(f"{seat} setup" in text for text in setup)
    assert {
        (entry.seat, entry.text) for entry in entries if entry.phase == "agent"
    } == {
        (seat, f"exec bash -c 'echo {seat} is working'")
        for seat in ("maya", "raj", "lee")
    }
    assert [entry.seat for entry in entries if entry.phase == "verification"] == [None]


async def test_unsupported_seat_streaming_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            *(_participant(seat) for seat in ("maya", "raj", "lee")),
        )
    )
    monkeypatch.setattr(trial.agent_environment, "type", lambda: EnvironmentType.MODAL)
    capture = AsyncMock()
    trial.add_log_callback(capture)

    result = await trial.run()

    assert result.exception_info is None
    assert _verified(trial)
    assert [entry.args[0].phase for entry in capture.await_args_list] == [
        "verification"
    ]
    assert caplog.text.count("Live seat logs require the docker environment") == 1


@pytest.mark.parametrize("observer_error", [RuntimeError, asyncio.CancelledError])
async def test_raising_seat_observers_preserve_agent_outcome(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    observer_error: type[BaseException],
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya", fail="exit"),
            _participant("raj"),
            _participant("lee"),
        )
    )

    async def disconnected(_event: TrialHookEvent | LogEntry) -> None:
        raise observer_error("observer disconnected")

    trial.add_hook(TrialEvent.SEAT_START, disconnected)
    trial.add_hook(TrialEvent.SEAT_END, disconnected)
    trial.add_log_callback(disconnected)
    survivor = AsyncMock()
    trial.add_log_callback(survivor)
    _record_seat_hooks(trial)

    result = await trial.run()

    _assert_seat_hooks(trial)
    assert result.exception_info is not None
    assert result.exception_info.exception_type == "NonZeroAgentExitCodeError"
    assert _verified(trial)
    seats = _seat_results(result)
    assert seats["maya"].exception_info.exception_type == "NonZeroAgentExitCodeError"
    assert seats["raj"].exception_info is seats["lee"].exception_info is None
    assert any(entry.args[0].phase == "agent" for entry in survivor.await_args_list)
    assert "seat-start hook failed" in caplog.text
    assert "seat-end hook failed" in caplog.text
    assert "Trial log callback failed" in caplog.text


async def test_seat_hook_duration_is_outside_the_agent_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path, timeout_sec=0.2),
            *(_participant(seat) for seat in ("maya", "raj", "lee")),
        )
    )
    release = asyncio.Event()
    pending_hooks: list[asyncio.Task[None]] = []

    async def slow_start(event: TrialHookEvent) -> None:
        await asyncio.sleep(0.25)
        note("start-observed", str(event.seat))

    async def hanging_end(_event: TrialHookEvent) -> None:
        task = asyncio.current_task()
        assert task is not None
        pending_hooks.append(task)
        try:
            await release.wait()
        except asyncio.CancelledError:
            # A subscriber may take an arbitrary time to clean up.
            await release.wait()

    trial.add_hook(TrialEvent.SEAT_START, slow_start)
    trial.add_hook(TrialEvent.SEAT_END, hanging_end)
    _record_seat_hooks(trial)
    try:
        result = await asyncio.wait_for(trial.run(), timeout=3)

        assert result.exception_info is None
        assert _verified(trial)
        _assert_seat_hooks(trial)
        for seat in trial.seats:
            assert index("start-observed", seat.name) < index(
                "run", seat.name, seat.instruction
            )
        assert len(pending_hooks) == 3
        assert all(not task.done() for task in pending_hooks)
        assert caplog.text.count("seat-end hook timed out") == 3
    finally:
        release.set()
        for task in pending_hooks:
            await task


async def test_cancelling_trial_interrupts_seat_hook_wait(tmp_path: Path):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            *(_participant(seat) for seat in ("maya", "raj", "lee")),
        )
    )
    entered = asyncio.Event()

    async def hanging_end(_event: TrialHookEvent) -> None:
        entered.set()
        await asyncio.Event().wait()

    trial.add_hook(TrialEvent.SEAT_END, hanging_end)
    run = asyncio.create_task(trial.run())
    await asyncio.wait_for(entered.wait(), timeout=3)
    run.cancel()
    await asyncio.sleep(0)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(run), timeout=1)

    assert trial.result.exception_info is not None
    assert trial.result.exception_info.exception_type == "CancelledError"
    assert ("stop",) in EVENTS


async def test_seat_end_snapshot_includes_collected_context_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            _participant("maya", fail="exit"),
            _participant("raj"),
            _participant("lee"),
        )
    )
    maya = trial.seats[0]
    usage_file = maya.paths.agent_dir / "usage.txt"
    events: list[TrialHookEvent] = []

    async def download(**_kwargs: Any) -> None:
        usage_file.write_text("0.75")

    def populate(context: AgentContext) -> None:
        context.cost_usd = float(usage_file.read_text())

    async def capture(event: TrialHookEvent) -> None:
        events.append(event)

    monkeypatch.setattr(maya.environment, "download_dir", download)
    monkeypatch.setattr(maya.agent, "populate_context_post_run", populate)
    trial.add_hook(TrialEvent.SEAT_START, capture)
    trial.add_hook(TrialEvent.SEAT_END, capture)

    result = await trial.run()

    assert len(events) == 6
    assert len({id(event.result) for event in events}) == 6
    seats = _seat_results(result)
    for event in events:
        assert event.seat is not None
        assert event.seat_result is not None
        assert event.seat_result.seat == event.seat
        assert event.result is not result
        assert event.seat_result is not seats[event.seat]
        assert event.seat_result is _seat_results(event.result)[event.seat]
        assert event.seat_result.agent_execution is not None
        if event.event == TrialEvent.SEAT_START:
            assert event.seat_result.agent_execution.finished_at is None
            assert event.seat_result.exception_info is None
        else:
            assert event.seat_result.agent_execution.finished_at is not None
    end = next(
        event
        for event in events
        if event.event == TrialEvent.SEAT_END and event.seat == "maya"
    )
    assert end.seat_result is not None
    assert end.seat_result.agent_result is not None
    assert end.seat_result.agent_result.cost_usd == 0.75
    assert end.seat_result.exception_info is not None
    assert end.seat_result.exception_info.exception_type == "NonZeroAgentExitCodeError"
    assert end.result.exception_info is not None
    assert end.result.exception_info.exception_message.startswith("Seat 'maya': ")


async def test_server_reports_trial_events_not_seat_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    trial = await _seats_trial(
        _config(
            _task_dir(tmp_path),
            *(_participant(seat) for seat in ("maya", "raj", "lee")),
        )
    )
    monkeypatch.setattr(Trial, "create", AsyncMock(return_value=trial))
    _record_seat_hooks(trial)
    report = AsyncMock()

    result = await TrialExecutor().run(trial.id, trial.config, report)

    _assert_seat_hooks(trial)
    assert report.await_count == 7  # 6 trial-level events and the finalized result.
    assert all(call.args[0] is result for call in report.await_args_list)


def test_trial_start_names_each_participant(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from harbor.cli.main import app

    async def run(self):
        return TrialResult.model_construct(
            trial_name=self.config.trial_name,
            task_name="hello",
            started_at=None,
            finished_at=None,
            exception_info=None,
            verifier_result=None,
        )

    monkeypatch.setattr(SeatsTrial, "run", run)
    launch = _config(
        _task_dir(tmp_path),
        {**_participant("maya"), "model_name": "openai/gpt-6-luna"},
        _participant("raj"),
        _participant("lee"),
    )
    config_path = tmp_path / "launch.json"
    config_path.write_text(launch.model_dump_json())

    result = CliRunner().invoke(app, ["trial", "start", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "Agent: oracle" not in result.output
    lines = result.output.splitlines()
    start = lines.index("Participants:")
    assert lines[start + 1 : start + 4] == [
        f"  maya: {AGENT} openai/gpt-6-luna (under test)",
        f"  raj: {AGENT} (under test)",
        f"  lee: {AGENT}",
    ]
