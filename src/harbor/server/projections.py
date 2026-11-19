"""Small persisted read projections shared by REST and SQL.

Locks identify tasks; configured model strings are preserved (including prefixes).
Observed agent versions and model providers become available at result checkpoints.
Missing measurements stay null; cached input is already included in input totals.
"""

from typing import Any

from harbor.models.job.lock import TrialLock
from harbor.models.trial.config import AgentConfig, TrialConfig
from harbor.models.trial.result import SeatResult, TrialResult
from harbor.server.models import RolloutPhase


def current_phase(result: TrialResult | None) -> RolloutPhase | None:
    if result is None:
        return None
    targets = [result, *(result.step_results or []), *(result.seat_results or [])]
    for target in reversed(targets):
        for name in ("verifier", "agent_execution", "agent_setup", "environment_setup"):
            timing = getattr(target, name, None)
            if (
                timing is not None
                and timing.started_at is not None
                and timing.finished_at is None
            ):
                return name
    return None


def _primary_seat(result: TrialResult) -> SeatResult | None:
    seats = result.seat_results
    if not seats:
        return None
    return next((seat for seat in seats if seat.under_test), seats[0])


def _primary_agent_config(
    config: TrialConfig, result: TrialResult | None = None
) -> AgentConfig:
    if not config.participants:
        return config.agent
    seat = _primary_seat(result) if result is not None else None
    if seat is not None:
        for participant in config.participants:
            if participant.seat == seat.seat:
                return participant
    return config.participants[0]


def input_projection(config: TrialConfig, lock: TrialLock) -> dict[str, Any]:
    agent = _primary_agent_config(config)
    effort = agent.kwargs.get("reasoning_effort")
    return {
        "task_name": lock.task.name,
        "task_digest": lock.task.digest,
        "task_source": lock.task.source,
        "agent_name": agent.name or agent.import_path,
        "model_name": agent.model_name,
        "reasoning_effort": effort if isinstance(effort, str) else None,
        "environment_type": config.environment.type or config.environment.import_path,
    }


def result_projection(result: TrialResult) -> dict[str, Any]:
    errors = result.recorded_exceptions()
    n_input, n_cached, n_output, cost = result.compute_token_cost_totals()
    agent = _primary_agent_config(result.config, result)
    seat = _primary_seat(result)
    info = (
        seat.agent_info if seat is not None and seat.agent_info else result.agent_info
    )
    model = info.model_info
    return {
        "agent_name": agent.name or agent.import_path or info.name,
        "agent_version": info.version or None,
        "model_name": agent.model_name or (model.name if model else None),
        "model_provider": model.provider if model else None,
        "phase": current_phase(result),
        "error_type": errors[0].exception_type if errors else None,
        "exception_types": sorted({error.exception_type for error in errors}),
        "duration_seconds": (
            max((result.finished_at - result.started_at).total_seconds(), 0)
            if result.finished_at is not None and result.started_at is not None
            else None
        ),
        "n_errors": int(
            any(error.exception_type != "CancelledError" for error in errors)
        ),
        "reward_valid": result.reward_valid,
        "rewards": result.verifier_result.rewards if result.verifier_result else None,
        "n_input_tokens": n_input,
        "n_cached_input_tokens": n_cached,
        "n_output_tokens": n_output,
        "cost_usd": cost,
    }
