import asyncio
import logging
import re
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from .. import seat_agent
from ..capsule import view_hash
from ..market_handoff import decisions, timing_seat
from ..market_handoff.timing_actor import _checked_meta, _host_path, _private_root
from . import domain

AGENT_NAME = "seatlab-report-budget-seat"
AGENT_VERSION = "1.0.0"
SEATS = ("sales", "finance")
PURPOSES = {"sales": "sales", "finance": "finance-price"}
VIEW_KEYS = {"sales": frozenset({"view"}), "finance": frozenset({"view", "report"})}
STATUSES = frozenset({"accepted", "invalid", "unanswered"})
CONTROL_META = {"control": True}
TRIAL_SEAT = re.compile(r"budget-w([0-9]{2})-t([0-9]{3})__(sales|finance)")
_PHASE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MAX_REPLY_BYTES = timing_seat.MAX_STDOUT
_VIEW_ATTEMPTS = 300
_VIEW_TIMEOUT_SEC = 330
_RETRY_SEC = 1
_WORLD_ERRORS = (RuntimeError, OSError, TypeError, ValueError, RecursionError)
_INPUT_ERRORS = (ValueError, TypeError, LookupError, RecursionError)

assert set(PURPOSES) == set(SEATS) == set(VIEW_KEYS)
assert set(PURPOSES.values()) <= set(timing_seat.ROLES)


class _Stop(Exception):
    def __init__(self, phase: str) -> None:
        if not _PHASE.fullmatch(phase):
            raise ValueError("invalid phase name")
        super().__init__(phase)
        self.phase = phase


def _object(body: str) -> dict[str, Any]:
    value = decisions.decode(body)
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise TypeError("expected a JSON object")
    return value


def _checked_view(
    body: dict[str, Any], seat: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    view, report = body.get("view"), body.get("report")
    if body.keys() != VIEW_KEYS[seat] or not isinstance(view, dict) or not view:
        raise ValueError("malformed view")
    if report is not None and (not isinstance(report, dict) or not report):
        raise ValueError("malformed view report")
    return view, report


def _checked_reply(reply: object) -> str:
    if (
        not isinstance(reply, str)
        or len(reply.encode("utf-8", "surrogatepass")) > _MAX_REPLY_BYTES
    ):
        raise ValueError("reply outside the seat frame")
    return reply


def _pending(status: int, body: dict[str, Any], seat: str) -> bool:
    return (
        status == 409
        and seat == "finance"
        and body.keys() == {"error"}
        and isinstance(body["error"], str)
    )


async def _get(environment: SeatEnvironment, path: str) -> tuple[int, dict[str, Any]]:
    status, body = await seat_agent._world_request(environment, path)
    return status, _object(body)


async def _view(environment: SeatEnvironment, seat: str) -> dict[str, Any]:
    async with asyncio.timeout(_VIEW_TIMEOUT_SEC):
        for _ in range(_VIEW_ATTEMPTS):
            status, body = await _get(environment, f"/view/{seat}")
            if status == 200:
                return body
            if not _pending(status, body, seat):
                raise ValueError("view rejected")
            await asyncio.sleep(_RETRY_SEC)
    raise TimeoutError("view wait exhausted")


class BudgetSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        client: str | None = None,
        owner: str | None = None,
        control: bool | None = None,
        target: int | None = None,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if kwargs or extra_env or model_name is not None:
            raise ValueError("Budget seat accepts only client, owner, control, target")
        if type(control) is not bool:
            raise ValueError("Budget seat requires a boolean control flag")
        if type(target) is not int or target not in domain.TARGETS:
            raise ValueError("Budget seat requires a registered target")
        if control and client is not None:
            raise ValueError("Budget control seat accepts no client")
        if not _host_path(owner) or not (control or _host_path(client)):
            raise ValueError("Budget seat requires absolute host client and owner")
        assert isinstance(owner, str) and (control or isinstance(client, str))
        super().__init__(logs_dir=logs_dir, model_name=None, logger=logger)
        self._client = None if client is None else Path(client)
        self._owner = Path(owner)
        self._control = control
        self._target = target
        self._sessions: set[str] = set()
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return AGENT_NAME

    @override
    def version(self) -> str:
        return AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    def _bind(self, environment: BaseEnvironment) -> SeatEnvironment:
        if (
            not isinstance(environment, SeatEnvironment)
            or environment.service not in SEATS
        ):
            raise _Stop("invalid_seat")
        match = TRIAL_SEAT.fullmatch(self.session_id or "")
        if match is None or match[3] != environment.service:
            raise _Stop("invalid_seat")
        if int(match[2]) != self._target:
            raise _Stop("target_mismatch")
        if (int(match[1]) == domain.CONTROL_WORLD) != self._control:
            raise _Stop("control_world_mismatch")
        if not self._control:
            try:
                _private_root(self._owner)
            except (OSError, ValueError):
                raise _Stop("owner_unavailable") from None
        return environment

    async def _read_view(
        self, environment: SeatEnvironment, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        metadata["phase"] = "waiting_view"
        try:
            body = await _view(environment, environment.service)
            metadata["view_sha256"] = view_hash(body)
        except TimeoutError:
            raise _Stop("view_timeout") from None
        except _WORLD_ERRORS:
            raise _Stop("view_unavailable") from None
        return body

    def _prompt(self, seat: str, body: dict[str, Any]) -> tuple[str, Any]:
        view, report = _checked_view(body, seat)
        if seat == "sales":
            return domain.sales_prompt(view, self._target), None
        return domain.finance_prompt(view, report), report

    async def _ask(
        self, seat: str, prompt: str, report: Any, metadata: dict[str, Any]
    ) -> str | None:
        purpose = PURPOSES[seat]
        turn: dict[str, Any] = {
            "purpose": purpose,
            "reply": None,
            "meta": None,
            "failure": None,
            "sent": None,
        }
        metadata["turn"] = turn
        metadata["phase"] = f"{purpose.replace('-', '_')}_turn"
        if self._control:
            try:
                answer = _checked_reply(
                    domain.control_reply(seat, self._target, report)
                )
            except _INPUT_ERRORS:
                turn.update(failure="control_invalid", sent=False)
                return None
            turn.update(reply=answer, meta=dict(CONTROL_META))
            return answer
        assert self._client is not None
        try:
            answer, raw = await asyncio.to_thread(
                timing_seat.call, self._client, self._owner / purpose, prompt, purpose
            )
        except timing_seat.SeatFailure as failure:
            turn.update(failure=failure.category, sent=failure.sent)
            return None
        try:
            answer = _checked_reply(answer)
            meta = _checked_meta(raw, prompt, answer, self._sessions)
        except _INPUT_ERRORS:
            turn.update(failure="provenance_rejected", sent=True)
            return None
        self._sessions.add(meta["session_sha256"])
        turn.update(reply=answer, meta=meta)
        return answer

    async def _post(
        self, environment: SeatEnvironment, reply: str | None, metadata: dict[str, Any]
    ) -> None:
        digest = metadata["view_sha256"]
        assert isinstance(digest, str) and decisions.HEX64.fullmatch(digest)
        assert reply is None or isinstance(reply, str)
        metadata["phase"] = "posting_reply"
        try:
            status, body = await seat_agent._world_request(
                environment,
                f"/{environment.service}",
                {"reply": reply},
                view_sha256=digest,
            )
            committed = _object(body)
        except _WORLD_ERRORS:
            raise _Stop("post_unavailable") from None
        resolution = committed.get("status")
        if (
            status != 200
            or committed.get("committed") is not True
            or resolution not in STATUSES
        ):
            raise _Stop("post_rejected")
        metadata["resolution"] = resolution

    async def _play(self, seat: SeatEnvironment, metadata: dict[str, Any]) -> None:
        body = await self._read_view(seat, metadata)
        try:
            prompt, report = self._prompt(seat.service, body)
        except _INPUT_ERRORS:
            await self._post(seat, None, metadata)
            raise _Stop("invalid_view") from None
        reply = await self._ask(seat.service, prompt, report, metadata)
        await self._post(seat, reply, metadata)
        failure = metadata["turn"]["failure"]
        if failure is not None:
            raise _Stop(f"{PURPOSES[seat.service].replace('-', '_')}_{failure}")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, Any] = {
            "role": None,
            "target": self._target,
            "control": self._control,
            "phase": "starting",
            "view_sha256": None,
            "turn": None,
            "resolution": None,
        }
        context.metadata = metadata
        try:
            if self._started:
                raise _Stop("already_started")
            self._started = True
            seat = self._bind(environment)
            metadata["role"] = seat.service
            await self._play(seat, metadata)
        except _Stop as stop:
            metadata["phase"] = stop.phase
            raise NonZeroAgentExitCodeError(f"Budget seat: {stop.phase}") from None
        metadata["phase"] = "complete"
