"""Helpers for in-progress trials (config.json present, result.json absent)."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from harbor.models.task.id import GitTaskId, LocalTaskId, PackageTaskId
from harbor.models.trial.config import AgentConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, ModelInfo, TrialResult
from harbor.viewer.models import TrialSummary


def model_info_from_model_name(model_name: str | None) -> ModelInfo | None:
    if not model_name:
        return None
    if "/" in model_name:
        provider, name = model_name.split("/", maxsplit=1)
        return ModelInfo(name=name, provider=provider)
    return ModelInfo(name=model_name, provider=None)


def task_name_from_config(config: TrialConfig) -> str:
    if config.task.name:
        return config.task.name
    return config.task.get_task_id().get_name()


def task_id_from_config(config: TrialConfig) -> LocalTaskId | GitTaskId | PackageTaskId:
    if config.task.name and "/" not in config.task.name:
        return LocalTaskId(path=Path(config.task.name))
    return config.task.get_task_id()


def primary_agent_config(config: TrialConfig) -> AgentConfig:
    """The agent a trial's config is labelled by while it has no result.

    A multi-agent trial never runs ``config.agent`` (it holds the default,
    oracle), so its label is its first participant. The task, not the config,
    says which seats are under test; once the trial has a result,
    ``result_agent_config`` labels it by the seat its ``agent_info`` names.
    """
    return config.participants[0] if config.participants else config.agent


def result_agent_config(result: TrialResult) -> AgentConfig:
    """The config of the agent that ``result.agent_info`` describes.

    A multi-agent trial's ``agent_info`` is its first agent under test, the
    first seat in ``seat_results`` with ``under_test``. Its config is the
    participant in that seat, wherever ``config.participants`` lists it.
    """
    seats = result.seat_results or []
    seat = next((seat.seat for seat in seats if seat.under_test), None)
    for participant in result.config.participants:
        if participant.seat == seat:
            return participant
    return primary_agent_config(result.config)


def _agent_label(agent: AgentConfig) -> str:
    return agent.name or agent.import_path or "unknown"


def agent_name_from_config(config: TrialConfig) -> str:
    return _agent_label(primary_agent_config(config))


def agent_name_from_result(result: TrialResult) -> str:
    agent = result_agent_config(result)
    if agent.import_path:
        return _agent_label(agent)
    return result.agent_info.name


def _reasoning_effort(agent: AgentConfig) -> str | None:
    value = agent.kwargs.get("reasoning_effort")
    return value if isinstance(value, str) and value else None


def reasoning_effort_from_config(config: TrialConfig) -> str | None:
    return _reasoning_effort(primary_agent_config(config))


def reasoning_effort_from_result(result: TrialResult) -> str | None:
    """The reasoning effort of the agent that ``result.agent_info`` describes,
    so a trial's effort, agent and model labels name one agent."""
    return _reasoning_effort(result_agent_config(result))


def trial_summary_from_config(name: str, config: TrialConfig) -> TrialSummary:
    model_info = model_info_from_model_name(primary_agent_config(config).model_name)
    return TrialSummary(
        name=name,
        task_name=task_name_from_config(config),
        id=config.job_id,
        source=config.task.source,
        agent_name=agent_name_from_config(config),
        reasoning_effort=reasoning_effort_from_config(config),
        model_provider=model_info.provider if model_info else None,
        model_name=model_info.name if model_info else None,
        reward=None,
        error_type=None,
        started_at=None,
        finished_at=None,
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        cost_usd=None,
    )


def partial_trial_result_from_config(
    *,
    job_name: str,
    trial_name: str,
    trial_dir: Path,
    config: TrialConfig,
    config_path: Path,
) -> TrialResult:
    model_info = model_info_from_model_name(primary_agent_config(config).model_name)
    started_at = datetime.fromtimestamp(config_path.stat().st_mtime, tz=timezone.utc)
    return TrialResult(
        id=config.job_id or uuid4(),
        task_name=task_name_from_config(config),
        trial_name=config.trial_name or trial_name,
        trial_uri=trial_dir.resolve().as_uri(),
        task_id=task_id_from_config(config),
        source=config.task.source,
        task_checksum="",
        config=config,
        agent_info=AgentInfo(
            name=agent_name_from_config(config),
            version="",
            model_info=model_info,
        ),
        started_at=started_at,
        finished_at=None,
    )
