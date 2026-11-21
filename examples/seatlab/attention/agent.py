import asyncio
import logging
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, override

import seat_agent
from capsule import view_hash
from examples.seatlab.visibility import native_pairs, route_cli, route_run, seat_command

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from . import protocol

DECLARED_MODEL = native_pairs.DECLARED_MODEL
_EPISODES = 6
_ROLES = ("inventory", "dispatch")
_SCHEMA = "seatlab.native-attention-seat/v1"
_CELLS = {cell["cell_id"]: cell for cell in protocol.grid()}


def _record_usage(context: AgentContext, turns: list[dict[str, Any]]) -> None:
    known = [turn["usage"] for turn in turns if turn["usage"] is not None]
    if len(known) != len(turns):
        context.n_input_tokens = context.n_cache_tokens = context.n_output_tokens = None
        return
    context.n_input_tokens = sum(usage["input_tokens"] for usage in known)
    context.n_cache_tokens = sum(usage["cache_tokens"] for usage in known)
    context.n_output_tokens = sum(usage["output_tokens"] for usage in known)


class AttentionSeat(BaseAgent):
    _registered = _CELLS

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        cell_id: str,
        transport: str,
        command_config: str | None = None,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(cell_id, str) or cell_id not in self._registered:
            raise ValueError("Attention seat requires a registered cell id")
        if extra_env or kwargs:
            raise ValueError("Attention seat does not accept agent overrides")
        if transport == "direct":
            if model_name not in (None, DECLARED_MODEL) or command_config is not None:
                raise ValueError("Invalid direct attention transport config")
            declared = DECLARED_MODEL
            config_path = None
        elif transport == "commands":
            if (
                model_name is not None
                or not isinstance(command_config, str)
                or not command_config
            ):
                raise ValueError(
                    "Command attention transport requires a command config and no model"
                )
            declared = None
            config_path = Path(command_config)
        else:
            raise ValueError("Unknown attention transport")
        super().__init__(logs_dir=logs_dir, model_name=declared, logger=logger)
        self._cell = self._registered[cell_id]
        self._command_config = config_path
        self._command: Callable[[str], str] | None = None
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        provider, model = model_name.split("/", 1)
        return provider, model

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-revision-attention"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    def _respond(self, prompt: str, run: str, role: str) -> route_run.Answer:
        if self._command_config is None:
            return route_cli._direct(prompt, run, role)
        if self._command is None:
            reviewer, decider = seat_command.load_commands(self._command_config)
            self._command = reviewer if role == "inventory" else decider
        return route_run.Answer(self._command(prompt))

    async def _ask(
        self,
        prompt: str,
        stage: str,
        role: str,
        metadata: dict[str, Any],
        context: AgentContext,
    ) -> str:
        run = f"{self._cell['cell_id']}-{stage}"
        prompt_digest = sha256(prompt.encode("utf-8")).hexdigest()
        metadata["stage"] = f"{stage}_pending"
        context.n_input_tokens = context.n_cache_tokens = context.n_output_tokens = None
        try:
            answer = await asyncio.to_thread(self._respond, prompt, run, role)
            raw, usage = route_run._validate_answer(answer)
        except (Exception, SystemExit):
            text = response_digest = usage = None
        else:
            text = answer.text
            response_digest = sha256(raw).hexdigest()
        metadata["turns"].append(
            {
                "stage": stage,
                "run": run,
                "role": role,
                "status": "callback_error" if text is None else "received",
                "prompt_sha256": prompt_digest,
                "response_sha256": response_digest,
                "usage": usage,
                "text": text,
            }
        )
        _record_usage(context, metadata["turns"])
        if text is None:
            metadata["stage"] = "callback_error"
            raise ValueError("Callback returned no response")
        metadata["stage"] = "invalid_model_decisions"
        return text

    async def _decisions(
        self,
        cases: list[dict[str, Any]],
        role: str,
        metadata: dict[str, Any],
        context: AgentContext,
    ) -> dict[str, Any]:
        if role == "inventory":
            prompt = native_pairs._prompt(role, cases)
            text = await self._ask(prompt, "inventory", role, metadata, context)
            return seat_agent._json_object(text, "inventory response")
        seed, rep = self._cell["seed"], self._cell["repetition"]
        prompt = protocol.select_prompt(cases, seed, rep, self._cell["arm"])
        text = await self._ask(prompt, "select", role, metadata, context)
        opened = protocol.select(text)
        metadata["opened"] = list(opened)
        prompt = protocol.decide_prompt(cases, seed, rep, opened)
        text = await self._ask(prompt, "decide", role, metadata, context)
        return protocol.decide(text, cases, seed, rep)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Native attention seat already started")
        self._started = True
        metadata: dict[str, Any] = {
            "schema": _SCHEMA,
            "cell_id": self._cell["cell_id"],
            "role": None,
            "stage": "invalid_seat",
            "view_sha256": None,
            "decision_sha256": None,
            "opened": [],
            "turns": [],
        }
        context.metadata = metadata
        try:
            if (
                not isinstance(environment, SeatEnvironment)
                or environment.service not in _ROLES
            ):
                raise ValueError("Invalid seat")
            role = environment.service
            metadata.update(role=role, stage="invalid_view")
            view = await seat_agent._view(environment)
            cases = seat_agent._checked_view(view, role)
            if len(cases) != _EPISODES or view["condition"] != self._cell["condition"]:
                raise ValueError("Unregistered native scenario")
            digest = view_hash(view)
            metadata.update(view_sha256=digest, stage="invalid_model_decisions")
            submission = await self._decisions(cases, role, metadata, context)
            seat_agent._checked_submission(submission, role, _EPISODES)
            metadata["decision_sha256"] = native_pairs._decision_hash(
                role, submission["rows"]
            )
            metadata["stage"] = "submission_unacknowledged"
            status, body = await seat_agent._world_request(
                environment, "/submit", submission, view_sha256=digest
            )
            accepted = seat_agent._json_object(body, "world submission")
            if (
                status != 200
                or accepted.keys() != {"accepted"}
                or accepted["accepted"] is not True
            ):
                raise ValueError("World did not acknowledge the decision")
        except Exception:
            raise seat_agent._Unmeasured(
                f"Native attention seat: {metadata['stage']}"
            ) from None
        metadata["stage"] = "complete"
