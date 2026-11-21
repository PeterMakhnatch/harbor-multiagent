from pathlib import Path
from secrets import token_hex
from typing import Any, ClassVar, override

import seat_agent
from capsule import view_hash

from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..attention import protocol as attention
from ..attention.agent import AttentionSeat
from ..briefing import channel
from ..briefing.agent import _submit
from ..visibility import native_pairs
from . import protocol


class DispatchAnswersSeat(AttentionSeat):
    _registered: ClassVar[dict[str, dict[str, Any]]] = {
        cell["cell_id"]: cell for cell in protocol.grid()
    }

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        channel_id: str,
        cell_id: str,
        transport: str,
        command_config: str | None = None,
        **kwargs: Any,
    ) -> None:
        if transport != "direct" or command_config is not None:
            raise ValueError("Dispatch answers requires host direct DeepSeek")
        super().__init__(
            logs_dir, model_name, cell_id=cell_id, transport="direct", **kwargs
        )
        self._channel_id = channel_id

    @staticmethod
    @override
    def name() -> str:
        return protocol.AGENT_NAME

    async def _inventory(
        self,
        env: SeatEnvironment,
        cases: list[dict[str, Any]],
        port: channel._Port,
        receipt: dict[str, Any],
        context: AgentContext,
    ) -> None:
        role = "inventory"
        prompt = native_pairs._prompt(role, cases)
        text = await self._ask(prompt, "inventory", role, receipt, context)
        submitted = seat_agent._json_object(text, "inventory response")
        await _submit(env, submitted, receipt)
        receipt["stage"] = "missing_question"
        question = protocol.question_from_wire(await port.receive("brief"))
        receipt["question_sha256"] = view_hash(question)
        prompt = protocol.answer_prompt(submitted["rows"], self._cell["seed"], question)
        text = await self._ask(prompt, "answer", role, receipt, context)
        answers = protocol.parse_answers(text)
        receipt["answer_sha256"] = view_hash((question, answers))
        wire = protocol.answers_to_wire(answers, self._cell["arm"] == "visible")
        await port.send("push", wire)

    async def _dispatch(
        self,
        env: SeatEnvironment,
        cases: list[dict[str, Any]],
        port: channel._Port,
        receipt: dict[str, Any],
        context: AgentContext,
    ) -> None:
        role = "dispatch"
        seed = self._cell["seed"]
        prompt = protocol.question_prompt(cases, seed)
        text = await self._ask(prompt, "question", role, receipt, context)
        question = protocol.parse_question(text)
        receipt.update(question_sha256=view_hash(question), stage="missing_answer")
        await port.send("brief", protocol.question_to_wire(question))
        wire = await port.receive("push")
        answers = protocol.answers_from_wire(wire, self._cell["arm"] == "visible")
        receipt["delivered_answer_sha256"] = (
            None if answers is None else view_hash((question, answers))
        )
        prompt = protocol.decide_prompt(cases, seed, question, answers)
        text = await self._ask(prompt, "decide", role, receipt, context)
        await _submit(env, attention.decide(text, cases, seed, 0), receipt)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Native Dispatch answers seat already started")
        self._started = True
        receipt: dict[str, Any] = {
            "schema": protocol.SEAT_SCHEMA,
            "cell_id": self._cell["cell_id"],
            "stage": "invalid_seat",
            "turns": [],
        }
        context.metadata = receipt
        try:
            if not isinstance(environment, SeatEnvironment):
                raise TypeError("Expected a native seat")
            role = environment.service
            if (
                role not in ("inventory", "dispatch")
                or self.session_id != f"{self._channel_id}__{role}"
            ):
                raise ValueError("Expected the registered trial seat")
            receipt["role"] = role
            if role == "dispatch":
                receipt["public_receipt_nonce"] = token_hex(32)
            async with channel.open_port(self._channel_id, role) as port:
                receipt["stage"] = "invalid_view"
                view = await seat_agent._view(environment)
                cases = seat_agent._checked_view(view, role)
                if (
                    len(cases) != protocol.EPISODES
                    or view["condition"] != protocol.CONDITION
                ):
                    raise ValueError("Expected the registered native world")
                receipt["view_sha256"] = view_hash(view)
                play = self._inventory if role == "inventory" else self._dispatch
                await play(environment, cases, port, receipt, context)
        except (OSError, RuntimeError, TypeError, ValueError):
            raise seat_agent._Unmeasured(
                f"Native Dispatch answers seat: {receipt['stage']}"
            ) from None
        receipt["stage"] = "complete"
