import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harbor.trial.hooks import TrialEvent, TrialHookEvent
from harbor.trial.seats import SeatsTrial

from ..native_study import write_exclusive
from .observe import HOST_DOCUMENT, role_halted, role_states

STAGES = {
    "planner": ("after_publish",),
    "dispatcher": ("before_decide", "after_decide"),
}
HELD = frozenset({"held-early", "held-late", "held-leave", "racing", "off"})


async def until(ready: Callable[[], bool]) -> None:
    async with asyncio.timeout(35):
        for _ in range(1401):
            if ready():
                return
            await asyncio.sleep(0.025)
    raise TimeoutError("qualification gate exceeded its poll budget")


class Qualification:
    SCHEDULES: frozenset[str] = HELD

    def __init__(self, trial: SeatsTrial, schedule: str) -> None:
        if schedule not in self.SCHEDULES:
            raise ValueError("unknown scripted gate schedule")
        self.trial = trial
        self.root = trial.paths.trial_dir
        self.schedule = schedule
        self.trace: list[str] = []
        self.job: asyncio.Task[None] | None = None
        trial.add_hook(TrialEvent.AGENT_START, self.start)

    async def start(self, _: TrialHookEvent) -> None:
        if self.job is not None:
            raise RuntimeError("duplicate native agent start")
        self.job = asyncio.create_task(self.play())

    def marker(self, role: str, prefix: str, stage: str) -> Path:
        assert (
            role in STAGES
            and stage in STAGES[role]
            and prefix in {"arrived", "release"}
        )
        return self.root / "seats" / role / "seat-cut-control" / f"{prefix}-{stage}"

    async def arrival(self, role: str, stage: str) -> None:
        await until(self.marker(role, "arrived", stage).is_file)
        self.trace.append(f"arrived:{role}:{stage}")

    def release(self, role: str, stage: str) -> None:
        assert self.marker(role, "arrived", stage).is_file()
        self.marker(role, "release", stage).touch(mode=0o600, exist_ok=False)
        self.trace.append(f"release:{role}:{stage}")

    def samples(self) -> list[dict[str, Any]]:
        return json.loads((self.root / HOST_DOCUMENT).read_bytes())["samples"]

    def native(self) -> dict[str, str]:
        results = self.trial.result.seat_results or []
        if role_halted(results, "planner", "dispatcher"):
            raise RuntimeError("a native seat will never return; gate aborted")
        return role_states(results, "planner", "dispatcher")

    async def witness(self, state: str) -> None:
        assert state in {"running", "clean"}
        old = {r["id"] for r in self.samples()} if state == "running" else set()
        snapshot = {"target": state, "peer": "running"}

        def witnessed() -> bool:
            self.native()
            rows = self.samples()
            if any(r["ack"] == "failed" for r in rows):
                raise RuntimeError(
                    "no receipt from the world; cannot qualify this schedule"
                )
            return any(
                r["id"] not in old
                and r["ack"] == "ok"
                and r["before"] == snapshot
                and r["after"] == snapshot
                for r in rows
            )

        await until(witnessed)
        self.trace.append(f"witness:{state}:running")

    async def play(self) -> None:
        planner, before, after = "after_publish", "before_decide", "after_decide"
        await self.arrival("planner", planner)
        await self.arrival("dispatcher", before)
        if self.schedule != "off":
            await self.witness("running")
        if self.schedule == "held-late":
            self.release("dispatcher", before)
            await self.arrival("dispatcher", after)
            await self.witness("running")
        self.release("planner", planner)
        if self.schedule == "racing":
            self.release("dispatcher", before)
        if self.schedule == "off":
            await until(lambda: self.native()["target"] == "clean")
            self.trace.append("native:planner:clean")
        else:
            await self.witness("clean")
        if self.schedule not in {"held-late", "racing"}:
            self.release("dispatcher", before)
        if self.schedule != "held-late":
            await self.arrival("dispatcher", after)
        self.release("dispatcher", after)

    async def finish(self) -> bool:
        status = "not_started"
        if self.job is not None:
            if not self.job.done():
                self.job.cancel()
            try:
                await self.job
                status = "complete"
            except asyncio.CancelledError:
                status = "interrupted"
            except (TimeoutError, RuntimeError, KeyError, ValueError, OSError) as error:
                status = type(error).__name__
        write_exclusive(
            self.root / "seat-cut-schedule.json",
            {
                "schema": "harbor.seat-cut.schedule/v1",
                "trial_id": str(self.trial.id),
                "ticket": self.trial.config.trial_name,
                "schedule": self.schedule,
                "status": status,
                "trace": self.trace,
            },
            0o600,
        )
        return status == "complete"
