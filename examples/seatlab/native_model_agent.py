"""A native Dispatch Handoff seat with one restricted Claude Max model turn."""

import asyncio
import logging
import subprocess
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Never, override
from uuid import UUID, uuid4

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage

if __package__:
    from . import claude_replay, seat_agent
    from .capsule import encode_view
else:
    import claude_replay
    import seat_agent
    from capsule import encode_view

_MODEL = claude_replay._MODEL
_DECLARED_MODEL = f"anthropic/{_MODEL}"
_PREFLIGHT_LOCK = Lock()


@lru_cache(maxsize=32)
def _trial_preflight(trial_id: UUID) -> str:
    return claude_replay._preflight(claude_replay._invoke)


def _preflight(trial_id: UUID | None) -> str:
    if trial_id is None:
        return claude_replay._preflight(claude_replay._invoke)
    # Harbor assigns both seats the same trial ID. Serialize the cache miss
    # without sharing an authentication result between trials.
    with _PREFLIGHT_LOCK:
        return _trial_preflight(trial_id)


def _fail(metadata: dict[str, Any], stage: str) -> Never:
    metadata["stage"] = stage
    # Harbor keeps this exception. Never attach raw CLI or world response text.
    raise seat_agent._Unmeasured(f"Native model seat: {stage}") from None


async def _verify_route(trial_id: UUID | None, metadata: dict[str, Any]) -> None:
    metadata["stage"] = "route_unverified"
    try:
        route = await asyncio.to_thread(_preflight, trial_id)
    except Exception:
        _fail(metadata, "route_unverified")
    if route != "verified":
        _fail(metadata, "route_unverified")


def _record_usage(
    context: AgentContext, telemetry: dict[str, int | float | None], attested: bool
) -> None:
    input_tokens = claude_replay._count(telemetry["input_tokens"])
    cache_read = claude_replay._count(telemetry["cache_read_input_tokens"])
    cache_creation = claude_replay._count(telemetry["cache_creation_input_tokens"])
    output_tokens = claude_replay._count(telemetry["output_tokens"])
    # Harbor's total includes both cache categories; cached tokens means reads.
    total = (
        input_tokens + cache_read + cache_creation
        if input_tokens is not None
        and cache_read is not None
        and cache_creation is not None
        else None
    )
    context.n_input_tokens = total
    context.n_cache_tokens = cache_read
    context.n_output_tokens = output_tokens
    context.cost_usd = telemetry["estimated_cost_usd"]
    if (
        attested
        and total is not None
        and cache_read is not None
        and output_tokens is not None
    ):
        context.model_usage = {
            _DECLARED_MODEL: ModelUsage(
                n_input_tokens=total,
                n_cache_tokens=cache_read,
                n_output_tokens=output_tokens,
                cost_usd=context.cost_usd,
            )
        }


def _generate_text(
    system: str,
    user: bytes,
    context: AgentContext,
    metadata: dict[str, Any],
) -> str:
    session = str(uuid4())
    args = claude_replay._args(system, session)
    # Set this in the worker, not while the command is still queued.
    metadata["model_turns"] = 1
    try:
        process = claude_replay._invoke(args, user, claude_replay._GENERATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        _fail(metadata, "generation_timeout")
    except OSError:
        _fail(metadata, "cli_launch_failed")
    except Exception:
        _fail(metadata, "generation_failed")
    try:
        response = claude_replay._json_object(process.stdout)
    except (ValueError, RecursionError):
        response = {}

    usage = response.get("modelUsage")
    model_attested = (
        isinstance(usage, dict)
        and usage.keys() == {_MODEL}
        and isinstance(usage[_MODEL], dict)
    )
    session_attested = (
        response.get("session_id") == session
        and claude_replay._count(response.get("num_turns")) == 1
        and response.get("permission_denials", []) == []
    )
    attested = model_attested and session_attested
    if attested:
        metadata["served_model"] = _MODEL
    telemetry = claude_replay._telemetry(response)
    metadata["telemetry"] = telemetry
    _record_usage(context, telemetry, attested)
    failure = claude_replay._failure(process, response)
    if failure is not None:
        _fail(metadata, failure)
    if not model_attested:
        _fail(metadata, "model_unattested")
    if not session_attested:
        _fail(metadata, "session_unattested")
    cost = telemetry["estimated_cost_usd"]
    if cost is not None and cost > float(claude_replay._BUDGET_USD):
        _fail(metadata, "budget_estimate_exceeded")
    if any(value is None for value in telemetry.values()):
        _fail(metadata, "telemetry_invalid")

    text = response.get("result")
    if not isinstance(text, str) or len(text) > 8192:
        _fail(metadata, "invalid_or_refused_decisions")
    return text


def _generate(
    role: str,
    view_bytes: bytes,
    episodes: int,
    context: AgentContext,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    system = seat_agent._RULES[role] + (
        " Output only a JSON object. Do not include explanations."
    )
    text = _generate_text(system, view_bytes, context, metadata)
    try:
        submission = claude_replay._json_object(text)
        seat_agent._checked_submission(submission, role, episodes)
    except (ValueError, RecursionError, seat_agent._Unmeasured):
        _fail(metadata, "invalid_or_refused_decisions")
    return submission


class NativeModelAgent(BaseAgent):
    """Use the host subscription without exposing the host client to the seat."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if model_name not in (None, _DECLARED_MODEL) or extra_env or kwargs:
            raise ValueError("Native model seat does not accept overrides")
        super().__init__(logs_dir=logs_dir, model_name=_DECLARED_MODEL, logger=logger)
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-first-party"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Native model seat already started")
        self._started = True
        metadata: dict[str, Any] = {
            "schema": "seatlab.native-model-seat/v1",
            "role": None,
            "model_turns": 0,
            "requested_model": _MODEL,
            "served_model": None,
            "stage": "waiting_view",
            "view_sha256": None,
            "telemetry": claude_replay._telemetry({}),
        }
        context.metadata = metadata
        if not isinstance(environment, SeatEnvironment):
            _fail(metadata, "invalid_seat")
        role = environment.service
        if role not in ("inventory", "dispatch"):
            _fail(metadata, "invalid_seat")
        metadata["role"] = role
        try:
            view = await seat_agent._view(environment)
            cases = seat_agent._checked_view(view, role)
            body = encode_view(view)
        except Exception:
            _fail(metadata, "invalid_view")
        view_sha256 = sha256(body).hexdigest()

        await _verify_route(self.context_id, metadata)

        metadata["stage"] = "generation"
        submission = await asyncio.to_thread(
            _generate, role, body, len(cases), context, metadata
        )
        metadata["stage"] = "submission_unacknowledged"
        try:
            status, result = await seat_agent._world_request(
                environment, "/submit", submission, view_sha256=view_sha256
            )
            acknowledgment = seat_agent._json_object(result, "world submission")
        except Exception:
            _fail(metadata, "submission_unacknowledged")
        if (
            status != 200
            or acknowledgment.keys() != {"accepted"}
            or acknowledgment["accepted"] is not True
        ):
            _fail(metadata, "submission_unacknowledged")
        metadata["view_sha256"] = view_sha256
        metadata["stage"] = "complete"
