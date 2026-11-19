"""Native handoff seat: a last-arrival script or one tool-free first-party Claude turn.

The seat sees only its own scoped world view. The model path adds no prompt
beyond the existing role rules, no tools, no API credentials, and no retry or
fallback. Metadata holds digests and estimates only: no text, errors or account.
"""

import asyncio
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any, override
from uuid import uuid4

import claude_replay
import seat_agent
from capsule import encode_view
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_POLICIES = ("script", "first-party")
_DECLARED_MODEL = f"anthropic/{claude_replay._MODEL}"
_MAX_RESULT_CHARS = 8192


class _Failure(RuntimeError):
    """Carries only a safe error code."""


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _canonical_submission(role: str, submission: dict[str, Any]) -> bytes:
    rows = sorted(submission["rows"], key=lambda row: row["id"])
    if role == "inventory":
        rows = [
            {
                "id": row["id"],
                "reports": [
                    {
                        "revision": report["revision"],
                        "available": sorted(report["available"]),
                    }
                    for report in sorted(row["reports"], key=lambda r: r["revision"])
                ],
            }
            for row in rows
        ]
    return encode_view({"rows": rows})


def _generate(role: str, body: bytes, metadata: dict[str, Any]) -> dict[str, Any]:
    """Run exactly one strict CLI turn; return the checked submission."""
    model, budget = claude_replay._MODEL, float(claude_replay._BUDGET_USD)
    system = seat_agent._RULES[role] + (
        " Output only a JSON object. Do not include explanations."
    )
    if claude_replay._preflight(claude_replay._invoke) != "verified":
        raise _Failure("subscription_unverified")
    session = str(uuid4())
    metadata["model_turns"] = 1
    metadata["stage"] = "generating"
    try:
        process = claude_replay._invoke(
            claude_replay._args(system, session),
            body,
            claude_replay._GENERATION_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise _Failure("generation_timeout") from None
    except OSError:
        raise _Failure("cli_launch_failed") from None
    try:
        response = claude_replay._json_object(process.stdout)
    except (ValueError, RecursionError):
        response = {}
    metadata["telemetry"] = telemetry = claude_replay._telemetry(response)
    failure = claude_replay._failure(process, response)
    if failure:
        raise _Failure(failure)
    usage = response.get("modelUsage")
    if (
        not isinstance(usage, dict)
        or usage.keys() != {model}
        or not isinstance(usage[model], dict)
    ):
        raise _Failure("model_unattested")
    if (
        response.get("session_id") != session
        or claude_replay._count(response.get("num_turns")) != 1
        or response.get("permission_denials", []) != []
    ):
        raise _Failure("session_unattested")
    metadata["served_model"] = model
    metadata["model_view_sha256"] = _digest(body)
    cost = telemetry["estimated_cost_usd"]
    if cost is not None and cost > budget:
        raise _Failure("budget_estimate_exceeded")
    text = response.get("result")
    if not isinstance(text, str) or len(text) > _MAX_RESULT_CHARS:
        raise _Failure("invalid_decisions")
    try:
        return claude_replay._json_object(text)
    except (ValueError, RecursionError):
        raise _Failure("invalid_decisions") from None


class NativeHandoffAgent(BaseAgent):
    """Host adapter limited to one native seat's scoped view and submit endpoints."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        policy: str,
        **kwargs: Any,
    ) -> None:
        if policy not in _POLICIES:
            raise ValueError("Unknown NativeHandoffAgent policy")
        if kwargs.get("extra_env") or kwargs.pop("skip_model_parsing", False):
            raise ValueError("NativeHandoffAgent accepts no participant overrides")
        declared = _DECLARED_MODEL if policy == "first-party" else None
        if model_name not in (None, declared):
            raise ValueError("NativeHandoffAgent cannot override the declared model")
        self._policy = policy
        super().__init__(logs_dir=logs_dir, model_name=declared, **kwargs)

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-native-handoff"

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
        metadata: dict[str, Any] = {
            "schema": "seatlab.native-handoff-seat/v1",
            "role": None,
            "policy": self._policy,
            "stage": "start",
            "view_sha256": None,
            "view_bytes": None,
            "model_view_sha256": None,
            "accepted_submission_sha256": None,
            "served_model": None,
            "model_turns": 0,
            "telemetry": None,
        }
        context.metadata = metadata
        metadata = context.metadata
        try:
            await self._seat(environment, context, metadata)
        except _Failure as exc:
            code = str(exc)
        except seat_agent._Unmeasured:
            code = "unmeasured_seat"
        except Exception:
            code = "unexpected_error"
        else:
            metadata["stage"] = "complete"
            return
        metadata["stage"] = code
        raise RuntimeError(code) from None

    async def _seat(
        self,
        environment: BaseEnvironment,
        context: AgentContext,
        metadata: dict[str, Any],
    ) -> None:
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service not in seat_agent._RULES
        ):
            raise _Failure("not_native_seat")
        role = environment.service
        metadata["role"] = role
        view = await seat_agent._view(environment)
        cases = seat_agent._checked_view(view, role)
        body = encode_view(view)
        metadata["view_sha256"] = _digest(body)
        metadata["view_bytes"] = len(body)
        if self._policy == "script":
            context.n_input_tokens = context.n_cache_tokens = 0
            context.n_output_tokens = 0
            context.cost_usd = 0.0
            metadata["telemetry"] = None
            submission = seat_agent._arrival(role, cases)
        else:
            metadata["telemetry"] = claude_replay._telemetry({})
            submission = await asyncio.to_thread(_generate, role, body, metadata)
        metadata["stage"] = "submitting"
        try:
            seat_agent._checked_submission(submission, role, len(cases))
        except seat_agent._Unmeasured:
            raise _Failure("invalid_decisions") from None
        status, text = await seat_agent._world_request(
            environment, "/submit", submission
        )
        if status != 200:
            raise _Failure("submission_rejected")
        acknowledgment = seat_agent._json_object(text, "world submission")
        if (
            not seat_agent._keys(acknowledgment, "accepted")
            or acknowledgment["accepted"] is not True
        ):
            raise _Failure("submission_rejected")
        metadata["accepted_submission_sha256"] = _digest(
            _canonical_submission(role, submission)
        )
