import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from ..native_owner_process import OWNER_ID, OwnerFailure, call_owner
from ..native_seat_http import SeatFailure, seat_request
from .owner_game import PUBLIC_INSTRUCTIONS, play
from .progress import record

METADATA_KEY = "joint_reopen_owner"
RECEIPT_SCHEMA = "harbor.joint-reopen.owner-receipt/v1"
REQUEST_SCHEMA = "harbor.joint-reopen.turn/v1"
REPLY_SCHEMA = "harbor.joint-reopen.choice/v1"
ROLES = ("east", "west")
OWNER_VARIABLES = {
    "east": "JOINT_REOPEN_EAST_OWNER",
    "west": "JOINT_REOPEN_WEST_OWNER",
}
ROUTES = frozenset(
    {
        ("GET", "/brief"),
        ("GET", "/status"),
        ("GET", "/card"),
        ("GET", "/inbox"),
        ("POST", "/endorse"),
        ("POST", "/report"),
        ("POST", "/vote"),
    }
)
READ_KINDS = frozenset({"card", "inbox"})
SLOT_KINDS = frozenset({"endorse", "vote"})
PHASES = ("endorse", "review", "vote", "complete")
WAIT_PHASES = frozenset({"review", "vote"})
OPTIONS = ("A", "B", "C", "D", "E", "F")
MAX_REPORT = 3
STAGE_KINDS = {
    "endorse": frozenset({"endorse", "stop"}),
    "report": frozenset({"card", "report", "stop"}),
    "vote": frozenset({"card", "inbox", "vote", "stop"}),
}
CHOICE = re.compile(r"endorse:[A-F]|card|report:(?:-|[A-F]{1,3})|inbox|vote:[A-F]|stop")
SUMMARY_KEYS = frozenset({"role", "terminal", "turns"})
TERMINAL_STATES = {"vote": "voted", "stop": "stopped"}
REASON = re.compile(r"[a-z_]{1,48}")
MAX_DECISIONS = 5
MAX_REQUESTS = 2 * len(READ_KINDS | SLOT_KINDS | {"report"})
MAX_SERIAL = 64
POLL_SECONDS = 2.0
WAIT_SECONDS = 300.0
TEAM_SECONDS = 540.0
MAX_POLLS = int(WAIT_SECONDS / POLL_SECONDS) + 1

assert 1 + MAX_DECISIONS + MAX_REQUESTS + len(WAIT_PHASES) <= MAX_SERIAL


class OwnerSeatFailure(RuntimeError):
    def __init__(self, reason: str) -> None:
        assert REASON.fullmatch(reason)
        super().__init__(f"joint reopen owner seat failed: {reason}")
        self.reason = reason


@dataclass
class Progress:
    logs_dir: Path
    role: str
    serial: int = 0
    confirmed: list[str] = field(default_factory=list)

    def publish(self, state: str, step: str | None) -> None:
        assert self.role in ROLES
        if self.serial >= MAX_SERIAL:
            raise OwnerSeatFailure("progress_bound_exceeded")
        self.serial += 1
        try:
            record(
                self.logs_dir,
                role=self.role,
                serial=self.serial,
                state=state,
                step=step,
                confirmed=list(self.confirmed),
            )
        except Exception:
            raise OwnerSeatFailure("progress_unwritable") from None

    def confirm(self, kind: str) -> None:
        if kind in self.confirmed or "vote" in self.confirmed:
            raise OwnerSeatFailure("confirmation_repeated")
        self.confirmed.append(kind)


@dataclass
class Trial:
    requested: str
    deadline: float
    receipt: dict[str, Any]
    live: Progress | None = None
    requests: int = 0
    refused: bool = False


def _receipt(requested: str) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "role": None,
        "requested": requested,
        "outcome": "incomplete",
        "confirmed": [],
        "decisions": [],
        "failure": None,
    }


def _slot(body: dict[str, object]) -> str:
    slot = body.get("slot")
    if set(body) != {"slot"} or type(slot) is not str or slot not in OPTIONS:
        raise OwnerSeatFailure("request_body_invalid")
    return json.dumps({"slot": slot}, separators=(",", ":"))


def _report(body: dict[str, object]) -> str:
    slots = body.get("slots")
    if set(body) != {"slots"} or type(slots) is not list or len(slots) > MAX_REPORT:
        raise OwnerSeatFailure("request_body_invalid")
    if not all(type(slot) is str and slot in OPTIONS for slot in slots):
        raise OwnerSeatFailure("request_body_invalid")
    if slots != sorted(set(slots)):
        raise OwnerSeatFailure("request_body_invalid")
    return json.dumps({"slots": slots}, separators=(",", ":"))


def _route(path: str, body: dict[str, object] | None) -> tuple[str, str, str | None]:
    if not path.startswith("/"):
        raise OwnerSeatFailure("request_path_refused")
    kind = path[1:]
    if kind in READ_KINDS:
        if body is not None:
            raise OwnerSeatFailure("request_body_invalid")
        return "GET", kind, None
    if body is None:
        raise OwnerSeatFailure("request_path_refused")
    if kind in SLOT_KINDS:
        return "POST", kind, _slot(body)
    if kind == "report":
        return "POST", kind, _report(body)
    raise OwnerSeatFailure("request_path_refused")


def _slots(value: object, minimum: int, maximum: int) -> bool:
    return (
        type(value) is list
        and minimum <= len(value) <= maximum
        and all(type(slot) is str and slot in OPTIONS for slot in value)
        and sorted(set(value)) == value
    )


def _packet(kind: str, reply: dict[str, object]) -> bool:
    if kind == "card":
        return set(reply) == {"slots"} and _slots(reply["slots"], 2, MAX_REPORT)
    if kind == "inbox":
        if set(reply) != {"delivered", "slots"} or type(reply["delivered"]) is not bool:
            return False
        if reply["delivered"]:
            return _slots(reply["slots"], 0, MAX_REPORT)
        return reply["slots"] is None
    return set(reply) == {"ok"} and reply["ok"] is True


def _phase(status: int, reply: dict[str, object]) -> int:
    if status != 200:
        raise OwnerSeatFailure("status_unavailable")
    phase = reply.get("phase")
    if set(reply) != {"phase"} or type(phase) is not str or phase not in PHASES:
        raise OwnerSeatFailure("status_malformed")
    return PHASES.index(phase)


def _turn(view: dict[str, object], role: str) -> tuple[str, int]:
    stage, turn = view.get("stage"), view.get("turn")
    if view.get("role") != role or not isinstance(stage, str):
        raise OwnerSeatFailure("view_invalid")
    if stage not in STAGE_KINDS:
        raise OwnerSeatFailure("view_invalid")
    if type(turn) is not int or not 1 <= turn <= MAX_DECISIONS:
        raise OwnerSeatFailure("view_invalid")
    return stage, turn


def _kind(choice: str, stage: str) -> str:
    if CHOICE.fullmatch(choice) is None:
        raise OwnerSeatFailure("choice_invalid")
    kind, _, slots = choice.partition(":")
    if kind == "report" and slots != "-" and list(slots) != sorted(set(slots)):
        raise OwnerSeatFailure("choice_invalid")
    if kind not in STAGE_KINDS[stage]:
        raise OwnerSeatFailure("choice_out_of_stage")
    return kind


def _terminal(summary: dict[str, object], trial: Trial) -> str:
    receipt = trial.receipt
    terminal, turns = summary.get("terminal"), summary.get("turns")
    if set(summary) != SUMMARY_KEYS or summary["role"] != receipt["role"]:
        raise OwnerSeatFailure("summary_malformed")
    if not isinstance(terminal, str) or terminal not in TERMINAL_STATES:
        raise OwnerSeatFailure("summary_malformed")
    if type(turns) is not int or turns != len(receipt["decisions"]):
        raise OwnerSeatFailure("summary_malformed")
    kinds = [decision["kind"] for decision in receipt["decisions"]]
    voted = receipt["confirmed"][-1:] == ["vote"]
    if not kinds or kinds[-1] != terminal or voted != (terminal == "vote"):
        raise OwnerSeatFailure("summary_inconsistent")
    return terminal


def _reason(exc: BaseException, trial: Trial) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, (OwnerSeatFailure, OwnerFailure, SeatFailure)):
        reason = getattr(exc, "reason", None)
        if type(reason) is str and REASON.fullmatch(reason):
            return reason
    return "world_refused" if trial.refused else "play_failed"


class Seat:
    def __init__(
        self,
        environment: BaseEnvironment,
        trial: Trial,
        live: Progress,
        save: Callable[[], None],
    ) -> None:
        assert live.role in ROLES and callable(save)
        self.environment = environment
        self.trial = trial
        self.live = live
        self.save = save

    async def request(
        self, path: str, body: dict[str, object] | None
    ) -> tuple[int, dict[str, object]]:
        method, kind, text = _route(path, body)
        if self.trial.requests >= MAX_REQUESTS or "vote" in self.live.confirmed:
            raise OwnerSeatFailure("request_bound_exceeded")
        self.trial.requests += 1
        self.live.publish("request", kind)
        status, reply = await seat_request(self.environment, ROUTES, method, path, text)
        if status != 200:
            self.trial.refused = True
        elif _packet(kind, reply):
            self.live.confirm(kind)
            self.trial.receipt["confirmed"] = list(self.live.confirmed)
            self.save()
        return status, reply

    async def decide(self, view: dict[str, object]) -> str:
        decisions = self.trial.receipt["decisions"]
        if len(decisions) >= MAX_DECISIONS:
            raise OwnerSeatFailure("decision_bound_exceeded")
        stage, turn = _turn(view, self.live.role)
        self.live.publish("owner_turn", stage)
        choice, served = await call_owner(
            role=self.live.role,
            variable=OWNER_VARIABLES[self.live.role],
            requested=self.trial.requested,
            request_schema=REQUEST_SCHEMA,
            reply_schema=REPLY_SCHEMA,
            instructions=PUBLIC_INSTRUCTIONS,
            view=view,
        )
        kind = _kind(choice, stage)
        decisions.append(
            {
                "turn": turn,
                "stage": stage,
                "kind": kind,
                "served_model": served,
            }
        )
        self.save()
        return choice

    async def wait(self, phase: str) -> None:
        if type(phase) is not str or phase not in WAIT_PHASES:
            raise OwnerSeatFailure("wait_phase_refused")
        target = PHASES.index(phase)
        deadline = min(time.monotonic() + WAIT_SECONDS, self.trial.deadline)
        announced = False
        for _ in range(MAX_POLLS):
            status, reply = await seat_request(
                self.environment, ROUTES, "GET", "/status", None
            )
            if _phase(status, reply) >= target:
                return
            if not announced:
                self.live.publish("barrier", phase)
                announced = True
            if time.monotonic() + POLL_SECONDS > deadline:
                break
            await asyncio.sleep(POLL_SECONDS)
        raise OwnerSeatFailure("wait_timeout")


class JointOwnerSeat(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return "joint-reopen-owner"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        skip_model_parsing: bool = False,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if type(model_name) is not str or OWNER_ID.fullmatch(model_name) is None:
            raise ValueError(
                "JointOwnerSeat needs a public owner identifier as model_name"
            )
        if "://" in model_name:
            raise ValueError("JointOwnerSeat model_name must not be a URL")
        if skip_model_parsing is not True:
            raise ValueError("JointOwnerSeat needs skip_model_parsing: true")
        if extra_env:
            raise ValueError("JointOwnerSeat forwards no environment into its seat")
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            skip_model_parsing=True,
            **kwargs,
        )
        self.requested = model_name

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        assert isinstance(instruction, str)
        trial = Trial(
            requested=self.requested,
            deadline=time.monotonic() + TEAM_SECONDS,
            receipt=_receipt(self.requested),
        )
        self._record(context, trial)
        try:
            terminal = await self._play(environment, context, trial)
        except BaseException as exc:
            reason = _reason(exc, trial)
            self._settle(trial, None, reason)
            self._record(context, trial)
            if not isinstance(exc, Exception):
                raise
            raise OwnerSeatFailure(reason) from None
        self._settle(trial, terminal, None)
        self._record(context, trial)
        if trial.receipt["failure"] is not None:
            raise OwnerSeatFailure(trial.receipt["failure"])

    async def _play(
        self, environment: BaseEnvironment, context: AgentContext, trial: Trial
    ) -> str:
        status, brief = await seat_request(environment, ROUTES, "GET", "/brief", None)
        role = brief.get("role")
        if status != 200 or type(role) is not str or role not in ROLES:
            raise OwnerSeatFailure("brief_unavailable")
        trial.receipt["role"] = role
        trial.live = Progress(self.logs_dir, role)
        self._record(context, trial)
        seat = Seat(
            environment, trial, trial.live, lambda: self._record(context, trial)
        )
        summary = await play(dict(brief), seat.request, seat.decide, seat.wait)
        return _terminal(summary, trial)

    def _settle(self, trial: Trial, terminal: str | None, reason: str | None) -> None:
        assert (terminal is None) != (reason is None)
        receipt = trial.receipt
        if receipt["confirmed"][-1:] == ["vote"]:
            state = "voted"
        elif terminal is not None:
            state = TERMINAL_STATES[terminal]
        else:
            state = "failed"
        receipt["outcome"] = state
        receipt["failure"] = reason
        if trial.live is None:
            return
        try:
            trial.live.publish(state, None)
        except OwnerSeatFailure as exc:
            receipt["failure"] = receipt["failure"] or exc.reason

    def _record(self, context: AgentContext, trial: Trial) -> None:
        snapshot = json.loads(json.dumps(trial.receipt, allow_nan=False))
        context.metadata = {**(context.metadata or {}), METADATA_KEY: snapshot}
