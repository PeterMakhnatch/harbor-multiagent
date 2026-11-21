import re
from pathlib import Path
from typing import Any, override

import seat_agent
from capsule import view_hash
from examples.seatlab.attention import protocol as attention
from examples.seatlab.attention.agent import AttentionSeat
from examples.seatlab.visibility import native_pairs

from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from . import channel, protocol

_SCHEMA = "seatlab.native-briefing-seat/v1"
_EPISODES = 6
_ROLES = ("inventory", "dispatch")
_CHANNEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


async def _submit(
    environment: SeatEnvironment,
    submission: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    role = environment.service
    seat_agent._checked_submission(submission, role, _EPISODES)
    metadata["decision_sha256"] = native_pairs._decision_hash(role, submission["rows"])
    metadata["stage"] = "submission_unacknowledged"
    status, body = await seat_agent._world_request(
        environment, "/submit", submission, view_sha256=metadata["view_sha256"]
    )
    if status != 200:
        raise ValueError("World did not acknowledge the decision")
    accepted = seat_agent._json_object(body, "world submission")
    if accepted.keys() != {"accepted"} or accepted["accepted"] is not True:
        raise ValueError("World did not acknowledge the decision")


class BriefingSeat(AttentionSeat):
    _registered = {cell["cell_id"]: cell for cell in protocol.grid()}

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        channel_id: str,
        **kwargs: Any,
    ) -> None:
        if not isinstance(channel_id, str) or not _CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("Briefing seat requires a native trial channel ID")
        super().__init__(logs_dir, model_name, **kwargs)
        self._channel_id = channel_id

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-dispatch-briefing"

    async def _inventory(
        self,
        environment: SeatEnvironment,
        cases: list[dict[str, Any]],
        port: Any,
        metadata: dict[str, Any],
        context: AgentContext,
    ) -> None:
        role = "inventory"
        prompt = native_pairs._prompt(role, cases)
        text = await self._ask(prompt, "inventory", role, metadata, context)
        submission = seat_agent._json_object(text, "inventory response")
        await _submit(environment, submission, metadata)

        metadata["stage"] = "missing_peer"
        card = await port.receive("brief")
        metadata.update(brief_seen=card, stage="invalid_relay")
        seed, rep = self._cell["seed"], self._cell["repetition"]
        prompt = protocol.push_prompt(submission["rows"], seed, rep, card)
        text = await self._ask(prompt, "push", role, metadata, context)
        opened = protocol.parse_push(text)
        metadata.update(opened=list(opened), stage="missing_peer")
        await port.send("push", opened)

    async def _dispatch(
        self,
        environment: SeatEnvironment,
        cases: list[dict[str, Any]],
        port: Any,
        metadata: dict[str, Any],
        context: AgentContext,
    ) -> None:
        role = "dispatch"
        seed, rep = self._cell["seed"], self._cell["repetition"]
        prompt = protocol.brief_prompt(cases, seed, rep)
        text = await self._ask(prompt, "brief", role, metadata, context)
        card = protocol.parse_brief(text)
        metadata.update(brief=card, stage="missing_peer")
        await port.send("brief", card if self._cell["arm"] == "brief" else None)
        opened = await port.receive("push")
        metadata.update(opened=list(opened), stage="invalid_relay")

        prompt = attention.decide_prompt(cases, seed, rep, opened)
        text = await self._ask(prompt, "decide", role, metadata, context)
        await _submit(environment, attention.decide(text, cases, seed, rep), metadata)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Native briefing seat already started")
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
            if self.session_id not in (None, f"{self._channel_id}__{role}"):
                raise ValueError("Channel ID does not match the native trial")
            metadata.update(role=role, stage="missing_peer")
            metadata["brief_seen" if role == "inventory" else "brief"] = None
            async with channel.open_port(self._channel_id, role) as port:
                metadata["stage"] = "invalid_view"
                view = await seat_agent._view(environment)
                cases = seat_agent._checked_view(view, role)
                if (
                    len(cases) != _EPISODES
                    or view["condition"] != self._cell["condition"]
                ):
                    raise ValueError("Unregistered native scenario")
                metadata.update(
                    view_sha256=view_hash(view), stage="invalid_model_decisions"
                )
                play = self._inventory if role == "inventory" else self._dispatch
                await play(environment, cases, port, metadata, context)
        except Exception:
            raise seat_agent._Unmeasured(
                f"Native briefing seat: {metadata['stage']}"
            ) from None
        metadata["stage"] = "complete"
