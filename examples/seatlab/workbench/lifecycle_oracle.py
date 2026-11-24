import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

from harbor.agents.oracle import OracleAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import TrialPaths

SCHEMA = "seatlab.workbench.lifecycle-control/v1"
IMPORT = "examples.seatlab.workbench.lifecycle_oracle:LifecycleOracle"
WITNESS = "seat-control-{seat}.jsonl"
TASK_DIR = Path(__file__).resolve().parents[2] / "tasks" / "hello-seats"
TASK_NAME = "harbor/hello-seats"
BEHAVIORS = ("return", "delay", "hold", "cancel", "raise", "setup_refusal")
DELAY_SEC = 5.0
HOLD_LIMIT_SEC = 90.0
MARKS = (
    "setup_entered",
    "setup_refused",
    "setup_returned",
    "run_entered",
    "oracle_returned",
    "oracle_raised",
    "oracle_cancelled",
    "delay_started",
    "delay_elapsed",
    "hold_started",
    "hold_cancelled",
    "hold_limit_reached",
    "cancel_raised",
    "error_raised",
    "wrapper_returned",
    "wrapper_raised",
    "wrapper_cancelled",
)
RESERVED = frozenset({"task_dir", "trial_paths", "agent_timeout_sec"})
SEATS = ("p1", "p2")
MAX_LINE_BYTES = 512


class LifecycleControlError(RuntimeError):
    pass


class LifecycleSetupRefusal(RuntimeError):
    pass


class LifecycleHoldExpired(RuntimeError):
    pass


class LifecycleWitnessError(RuntimeError):
    pass


class _Witness:
    def __init__(self, path: Path, seat: str, behavior: str):
        if path.exists() or path.is_symlink():
            raise LifecycleWitnessError(f"witness already exists: {path.name}")
        self._path = path
        self._seat = seat
        self._behavior = behavior
        self._seen: list[str] = []

    def mark(self, mark: str) -> None:
        if mark not in MARKS:
            raise LifecycleWitnessError(f"unknown witness mark: {mark}")
        if mark in self._seen:
            raise LifecycleWitnessError(f"repeated witness mark: {mark}")
        record = {
            "schema": SCHEMA,
            "seq": len(self._seen),
            "seat": self._seat,
            "behavior": self._behavior,
            "mark": mark,
            "wall": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "monotonic_ns": time.monotonic_ns(),
        }
        line = (
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        if len(line) > MAX_LINE_BYTES:
            raise LifecycleWitnessError("witness line is too long")
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
        if not self._seen:
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(self._path, flags, 0o644)
        try:
            if os.write(fd, line) != len(line):
                raise LifecycleWitnessError("witness line was not fully written")
            os.fsync(fd)
        finally:
            os.close(fd)
        self._seen.append(mark)


class LifecycleOracle(OracleAgent):
    def __init__(
        self,
        logs_dir: Path,
        behavior: str,
        model_name: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ):
        if behavior not in BEHAVIORS:
            raise ValueError(f"behavior must be one of {BEHAVIORS}, got {behavior!r}")
        if reserved := sorted(RESERVED & kwargs.keys()):
            raise ValueError(f"LifecycleOracle sets these itself: {reserved}")
        if model_name is not None:
            raise ValueError("LifecycleOracle takes no model")
        logs_dir = Path(logs_dir)
        seat = logs_dir.parent.name
        if (
            logs_dir.name != "agent"
            or logs_dir.parent.parent.name != "seats"
            or seat not in SEATS
        ):
            raise ValueError("LifecycleOracle runs only as a native seat participant")
        super().__init__(
            logs_dir=logs_dir,
            task_dir=TASK_DIR,
            trial_paths=TrialPaths(trial_dir=logs_dir.parent),
            model_name=None,
            extra_env=extra_env,
            agent_timeout_sec=None,
            **kwargs,
        )
        if self._task.name != TASK_NAME:
            raise ValueError(f"LifecycleOracle runs only {TASK_NAME}")
        self.behavior = behavior
        trial_dir = logs_dir.parent.parent.parent
        self._witness = _Witness(trial_dir / WITNESS.format(seat=seat), seat, behavior)

    @staticmethod
    @override
    def name() -> str:
        return "lifecycle-oracle"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        self._witness.mark("setup_entered")
        if self.behavior == "setup_refusal":
            self._witness.mark("setup_refused")
            raise LifecycleSetupRefusal("lifecycle setup refusal")
        await super().setup(environment)
        self._witness.mark("setup_returned")

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        self._witness.mark("run_entered")
        try:
            if self.behavior == "setup_refusal":
                raise LifecycleWitnessError("run entered after a setup refusal")
            await self._oracle(instruction, environment, context)
            await self._act()
        except asyncio.CancelledError:
            self._witness.mark("wrapper_cancelled")
            raise
        except Exception:
            self._witness.mark("wrapper_raised")
            raise
        self._witness.mark("wrapper_returned")

    async def _oracle(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        try:
            await super().run(
                instruction=instruction, environment=environment, context=context
            )
        except asyncio.CancelledError:
            self._witness.mark("oracle_cancelled")
            raise
        except Exception:
            self._witness.mark("oracle_raised")
            raise
        self._witness.mark("oracle_returned")

    async def _act(self) -> None:
        match self.behavior:
            case "return":
                return
            case "delay":
                self._witness.mark("delay_started")
                await asyncio.sleep(DELAY_SEC)
                self._witness.mark("delay_elapsed")
            case "hold":
                await self._hold()
            case "cancel":
                self._witness.mark("cancel_raised")
                raise asyncio.CancelledError
            case "raise":
                self._witness.mark("error_raised")
                raise LifecycleControlError("lifecycle control raise")
            case _:
                raise LifecycleWitnessError(f"no play action for {self.behavior}")

    async def _hold(self) -> None:
        self._witness.mark("hold_started")
        try:
            await asyncio.sleep(HOLD_LIMIT_SEC)
        except asyncio.CancelledError:
            self._witness.mark("hold_cancelled")
            raise
        self._witness.mark("hold_limit_reached")
        raise LifecycleHoldExpired(f"lifecycle hold reached {HOLD_LIMIT_SEC} seconds")
