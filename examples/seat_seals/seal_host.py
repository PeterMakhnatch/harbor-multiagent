import asyncio
import logging
from typing import override

from harbor.trial.seats import SeatsTrial

from ..seat_checkpoints.control_seat import ControlSeat
from ..seat_checkpoints.gates import Qualification, until
from ..seat_checkpoints.observe import (
    MARK_COMMAND,
    TERMINAL_STATES,
    attach,
    role_states,
)

SEAL_COMMAND = "python3 /app/seal_mark.py"
SEAL_SCHEDULES = frozenset({"held-early", "held-late", "held-window", "held-leave"})
WINDOW = "held-window"
ROLES = frozenset({"planner", "dispatcher"})
DECIDING = {"target": "clean", "peer": "running"}
DECISION_PERMIT_SECONDS = 45.0

assert WINDOW in SEAL_SCHEDULES and SEAL_COMMAND != MARK_COMMAND

logger = logging.getLogger(__name__)


def _permit(permit: object) -> asyncio.Event | None:
    if permit is None:
        return None
    if type(permit) is not asyncio.Event or permit.is_set():
        raise ValueError("decision_permit must be an unset asyncio.Event")
    return permit


def _scripted(trial: SeatsTrial) -> bool:
    return {seat.name for seat in trial.seats} == ROLES and all(
        isinstance(seat.agent, ControlSeat) and seat.agent.role == seat.name
        for seat in trial.seats
    )


class _Decision:
    def __init__(self, permit: asyncio.Event | None) -> None:
        self._permit = permit
        self._requested = False

    async def select(self, before: dict[str, str]) -> str | None:
        if self._requested or before["target"] != "clean":
            return MARK_COMMAND
        self._requested = True
        if self._permit is None:
            return SEAL_COMMAND
        try:
            async with asyncio.timeout(DECISION_PERMIT_SECONDS):
                await self._permit.wait()
        except TimeoutError:
            logger.warning("seat-seal decision permit never arrived; not transported")
            return None
        return SEAL_COMMAND


def attach_seal(
    trial: SeatsTrial,
    *,
    target: str,
    peer: str,
    interval_seconds: float,
    max_samples: int,
    timeout_seconds: float,
    decision_permit: asyncio.Event | None = None,
) -> None:
    permit = _permit(decision_permit)
    if not isinstance(trial, SeatsTrial):
        raise TypeError("seat-seal observation requires a native SeatsTrial")
    if permit is not None and not _scripted(trial):
        raise ValueError("a decision permit is reserved for scripted control seats")
    attach(
        trial,
        target=target,
        peer=peer,
        interval_seconds=interval_seconds,
        max_samples=max_samples,
        timeout_seconds=timeout_seconds,
        select=_Decision(permit).select,
    )


class SealQualification(Qualification):
    SCHEDULES = SEAL_SCHEDULES

    def __init__(
        self,
        trial: SeatsTrial,
        schedule: str,
        *,
        decision_permit: asyncio.Event | None = None,
    ) -> None:
        permit = _permit(decision_permit)
        if (schedule == WINDOW) != (permit is not None):
            raise ValueError("exactly the held-window schedule carries a permit")
        if not isinstance(trial, SeatsTrial) or not _scripted(trial):
            raise ValueError("seal gates drive scripted control seats only")
        super().__init__(trial, schedule)
        self.permit = permit

    @override
    async def play(self) -> None:
        if self.schedule != WINDOW:
            await super().play()
            return
        assert self.permit is not None and not self.permit.is_set()
        planner, before, after = "after_publish", "before_decide", "after_decide"
        await self.arrival("planner", planner)
        await self.arrival("dispatcher", before)
        await self.witness("running")
        self.release("planner", planner)
        await self.returned()
        self.release("dispatcher", before)
        await self.arrival("dispatcher", after)
        self.permit.set()
        self.trace.append("permit:decision")
        await self.witness("clean")
        self.release("dispatcher", after)

    async def returned(self) -> None:
        def native() -> bool:
            states = role_states(
                self.trial.result.seat_results or [], "planner", "dispatcher"
            )
            if states["target"] == "error" or states["peer"] in TERMINAL_STATES:
                raise RuntimeError("held-window lost its native return order")
            return states["target"] == "clean"

        await until(native)
        self.trace.append("native:planner:clean")

        def held() -> bool:
            rows = self.samples()
            if any(r["ack"] == "failed" for r in rows):
                raise RuntimeError("no receipt from the world; cannot hold a decision")
            if any(r["ack"] == "ok" and r["before"] == DECIDING for r in rows):
                raise RuntimeError("a decision was transported before its permit")
            return any(r["ack"] == "pending" and r["before"] == DECIDING for r in rows)

        await until(held)
        self.trace.append("pending:clean:running")
