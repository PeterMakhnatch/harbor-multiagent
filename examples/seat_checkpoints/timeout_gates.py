from typing import override

from .gates import Qualification, until
from .timeout_contract import TIMEOUTS


class Deadline(Qualification):
    SCHEDULES = frozenset(TIMEOUTS)

    async def native_clean(self, role: str) -> None:
        assert role in {"planner", "dispatcher"}
        other = "dispatcher" if role == "planner" else "planner"
        wanted = {role: "clean", other: "running"}

        def reached() -> bool:
            states = self.native()
            return {"planner": states["target"], "dispatcher": states["peer"]} == wanted

        await until(reached)
        self.trace.append(f"native:{role}:clean")

    @override
    async def play(self) -> None:
        planner, before, after = "after_publish", "before_decide", "after_decide"
        await self.arrival("planner", planner)
        await self.arrival("dispatcher", before)
        if self.schedule == "timeout-off":
            self.release("planner", planner)
            await self.native_clean("planner")
            self.release("dispatcher", before)
            await self.arrival("dispatcher", after)
            return
        await self.witness("running")
        if self.schedule == "timeout-both":
            return
        if self.schedule == "timeout-peer-after":
            self.release("planner", planner)
            await self.witness("clean")
            self.release("dispatcher", before)
            await self.arrival("dispatcher", after)
            return
        self.release("dispatcher", before)
        await self.arrival("dispatcher", after)
        if self.schedule == "timeout-target":
            self.release("dispatcher", after)
            await self.native_clean("dispatcher")
            return
        assert self.schedule == "timeout-peer-before"
        await self.witness("running")
        self.release("planner", planner)
        await self.witness("clean")
