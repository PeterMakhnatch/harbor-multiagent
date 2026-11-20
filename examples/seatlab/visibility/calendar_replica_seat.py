"""Run the ordinary calendar actor with turns supplied by an external harness."""

import asyncio
from typing import Any, override

from harbor.models.agent.context import AgentContext

from . import calendar_protocol, replica_io
from .calendar_seat import CalendarSeat, _hash, _SeatError


class RelayCalendarSeat(CalendarSeat):
    _declared_model: str | None = None
    _run_prefix = "rx-"

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-calendar-external"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def _measure(
        self, prompt: str, role: str, context: AgentContext, metadata: dict[str, Any]
    ) -> tuple[int, ...] | int | None:
        metadata["own_prompt_sha256"] = _hash(prompt)
        if role not in ("cal_recheck", "cal_decide"):
            raise _SeatError("model_failed")
        try:
            text = await asyncio.to_thread(
                replica_io.current().ask, self._run, role, self._seat, prompt
            )
        except (ValueError, OSError):
            raise _SeatError("model_failed") from None
        try:
            if role == "cal_recheck":
                return calendar_protocol.decode_list(text)
            return calendar_protocol.decode_slot(text)
        except ValueError:
            metadata["stage"] = "model_invalid"
            return None
