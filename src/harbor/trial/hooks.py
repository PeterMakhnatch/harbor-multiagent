from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from harbor.environments.base import OutputStream
from harbor.models.job.lock import TrialLock
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import SeatResult, TrialResult

LogPhase = Literal["agent_setup", "agent", "verification"]


class TrialEvent(Enum):
    """Events in a trial's lifecycle."""

    START = "start"
    ENVIRONMENT_START = "environment-start"
    AGENT_START = "agent-start"
    AGENT_END = "agent-end"
    SEAT_START = "seat-start"  # After setup, before agent execution.
    SEAT_END = "seat-end"  # After seat log collection and error recording.
    VERIFICATION_START = "verification-start"
    END = "end"
    CANCEL = "cancel"


class TrialHookEvent(BaseModel):
    """
    Unified event object passed to all trial lifecycle hooks.

    Provides context about the trial at the time of the event.
    The `result` field is initialized before START and fully populated by END.
    `trial_name` and `trial_id` are derived from `config` and `result`, so
    callers do not pass them when constructing hook events.
    For seat events, `seat` identifies the participant, `seat_result` is
    its point-in-time snapshot, and `result` is also a point-in-time copy.
    Other events keep the live trial result and leave both seat fields unset.
    """

    model_config = {"arbitrary_types_allowed": True}

    event: TrialEvent
    task_name: str
    config: TrialConfig
    result: TrialResult
    lock: TrialLock
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    seat: str | None = None
    seat_result: SeatResult | None = None

    @computed_field
    @property
    def trial_name(self) -> str:
        return self.config.trial_name

    @computed_field
    @property
    def trial_id(self) -> UUID:
        return self.result.id


HookCallback = Callable[["TrialHookEvent"], Awaitable[None]]


class LogEntry(BaseModel):
    """A structured log chunk emitted during trial execution.

    Pydantic (like its neighbor ``TrialHookEvent``) to follow the project's
    model convention; ``timestamp`` is a ``datetime`` to match it as well.
    `seat` identifies a participant's setup or execution output, if applicable.
    Shared verifier output never has a seat, even when the verifier invokes a seat.
    """

    trial_id: UUID
    phase: LogPhase
    stream: OutputStream
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    step_name: str | None = None
    seat: str | None = None


LogCallback = Callable[[LogEntry], Awaitable[None]]
