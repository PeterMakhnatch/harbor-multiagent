import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage

from ..visibility import calendar_packet, calendar_protocol, deepseek, spend
from ..visibility.calendar_seat import (
    _receive,
    _request,
    _SeatError,
    _send,
    _table,
    _write_private,
)
from . import protocol as p


def _add_usage(
    context: AgentContext, usage: tuple[int, int, int], served: str | None
) -> None:
    total, cached, output = usage
    context.n_input_tokens = (context.n_input_tokens or 0) + total
    context.n_cache_tokens = (context.n_cache_tokens or 0) + cached
    context.n_output_tokens = (context.n_output_tokens or 0) + output
    if served is not None:
        if context.model_usage is None:
            context.model_usage = {}
        model = context.model_usage.setdefault(served, ModelUsage())
        model.n_input_tokens += total
        model.n_cache_tokens += cached
        model.n_output_tokens += output


class AnswerSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        world: str,
        arm: str,
        seat: str,
        control: bool,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
    ) -> None:
        self._cell = p.Cell(world, arm, control)
        declared = None if control else p.MODEL
        if seat not in p.SEATS or model_name != declared or extra_env:
            raise ValueError("invalid_native_answer_actor")
        super().__init__(
            logs_dir=logs_dir,
            model_name=declared,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )
        self._seat = seat
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        if model_name != p.MODEL:
            raise ValueError("invalid_direct_model")
        return "deepseek", deepseek.MODEL

    @staticmethod
    @override
    def name() -> str:
        return p.AGENT_NAME

    @override
    def version(self) -> str:
        return p.AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def _measure(
        self,
        phase: str,
        prompt: str,
        context: AgentContext,
        *,
        control_text: str | None = None,
    ) -> Any:
        if (control_text is not None) != self._cell.control or self.context_id is None:
            raise ValueError("invalid_turn_binding")
        role = p.LEDGER_ROLES[phase]
        receipt: dict[str, Any] = {
            "schema": p.TURN_SCHEMA,
            "trial_uuid": str(self.context_id),
            "run": self._cell.run,
            "seat": self._seat,
            "phase": phase,
            "ledger_role": None if self._cell.control else role,
            "prompt_sha256": p.digest(prompt),
            "response_text": None,
            "response_sha256": None,
            "served_model": None,
            "usage": None,
            "value": None,
            "status": "model_failed",
            "error": None,
        }
        try:
            if control_text is not None:
                text = control_text
            else:
                turn = await asyncio.to_thread(
                    deepseek.turn, prompt, run_id=self._cell.run, role=role
                )
                text = turn.text
                usage = (turn.input_tokens, turn.cache_tokens, turn.output_tokens)
                _add_usage(context, usage, turn.served_model)
                receipt.update(served_model=turn.served_model, usage=usage)
            receipt.update(response_text=text, response_sha256=p.digest(text))
            value = p.decode(text, phase)
            receipt.update(status="accepted", value=value)
            return value
        except deepseek.SeatUnavailable as exc:
            receipt["error"] = exc.category
            if exc.settled_usage is not None:
                receipt["usage"] = exc.settled_usage
                _add_usage(context, exc.settled_usage, None)
        except spend.SpendRefused:
            receipt["error"] = "ledger_settlement_failed"
        except ValueError:
            receipt["status"] = "model_invalid"
        finally:
            _write_private(self.logs_dir / f"{phase}.json", receipt)
        return None

    async def _peer(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        table: calendar_packet.Table,
        metadata: dict[str, Any],
    ) -> None:
        metadata["stage"] = "waiting_for_question"
        incoming = await _receive(environment, self._cell.receiver)
        metadata["received"] = incoming
        slots = p.decode(incoming["text"], "question")
        metadata["stage"] = "answer_turn"
        raw_bits = await self._measure(
            "answer",
            p.answer_prompt(table, incoming["text"]),
            context,
            control_text=(
                p.answer_wire(self._cell.draft_control_bits(table, slots))
                if self._cell.control
                else None
            ),
        )
        if raw_bits is None:
            metadata["stage"] = "answer_failed"
            return
        bits = self._cell.sent_bits(table, slots, raw_bits)
        metadata["sent"] = await _send(
            environment, "m2", self._cell.receiver, p.answer_wire(bits)
        )
        metadata["stage"] = "complete"

    async def _receiver(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        table: calendar_packet.Table,
        metadata: dict[str, Any],
    ) -> None:
        metadata["stage"] = "question_turn"
        slots = await self._measure(
            "question",
            p.question_prompt(table),
            context,
            control_text=p.control_reply("question", table, None, None)
            if self._cell.control
            else None,
        )
        if slots is None:
            metadata["stage"] = "question_failed"
            return
        sent = await _send(environment, "m1", self._cell.peer, p.question_wire(slots))
        metadata.update(sent=sent, stage="waiting_for_answer")
        incoming = await _receive(environment, self._cell.peer, sent=sent)
        metadata["received"] = incoming
        bits = p.decode(incoming["text"], "answer")
        supplied = bits if self._cell.shows_answer else None
        metadata["stage"] = "choice_turn"
        choice = await self._measure(
            "choice",
            p.choice_prompt(table, sent["text"], supplied),
            context,
            control_text=p.control_reply("choice", table, slots, supplied)
            if self._cell.control
            else None,
        )
        if choice is None:
            metadata["stage"] = "choice_failed"
            return
        reply = await _request(
            environment, "submit", 200, {"answer": calendar_protocol.answer(choice)}
        )
        if reply != {"accepted": True}:
            raise _SeatError("invalid_submission_receipt")
        metadata["stage"] = "complete"

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, Any] = {
            "schema": p.SEAT_SCHEMA,
            "run": self._cell.run,
            "seat": self._seat,
            "stage": "starting",
            "error": None,
            "calendar_sha256": None,
            "sent": None,
            "received": None,
        }
        context.metadata = {"native_calendar_answers": metadata}
        try:
            if (
                self._started
                or not isinstance(environment, SeatEnvironment)
                or environment.service != self._seat
            ):
                raise _SeatError("invalid_seat_binding")
            self._started = True
            table = await _table(environment, self._cell.index, self._seat)
            metadata["calendar_sha256"] = p.digest(p.wire(table))
            if self._seat == self._cell.receiver:
                await self._receiver(environment, context, table, metadata)
            else:
                await self._peer(environment, context, table, metadata)
        except _SeatError as exc:
            metadata["error"] = str(exc)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            metadata["error"] = "seat_failed"
            raise _SeatError("seat_failed") from None
