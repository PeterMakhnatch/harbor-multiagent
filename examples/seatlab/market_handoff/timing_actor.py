import asyncio
import logging
import os
import re
import stat
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from .. import seat_agent
from ..capsule import view_hash
from . import decisions, timing_seat
from . import timing_messages as m

AGENT_NAME = "seatlab-price-timing-seat"
AGENT_VERSION = "1.0.0"
ROLES = ("sales", "finance_a", "finance_b")
PURPOSES = ("sales", "finance-price", "finance-receipt")
META_KEYS = frozenset(
    {
        "session_sha256",
        "prompt_sha256",
        "answer_sha256",
        "requested_model",
        "served_model",
        "client_tokens",
        "tool_steps",
        "credit_fallback",
    }
)
CONTROL_WORLD = 8
CONTROL_META = {"control": True}
CONTROL_SALES = '{"pick":1,"reason":"control"}'
CONTROL_PRICE = {"finance_a": '{"pick":2}', "finance_b": '{"pick":3}'}
TRIAL_SEAT = re.compile(r"price-w([0-9]{2})-r[0-9]+__(sales|finance_a|finance_b)")
TOKEN = re.compile(r"[0-9a-f]{12}")
_PHASE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_REPORT_KEYS = frozenset({"pick", "reason"})
_MAX_REPLY_BYTES = 8192
_MAX_MODEL_CHARS = 128
_VIEW_ATTEMPTS = 300
_VIEW_TIMEOUT_SEC = 330
_RETRY_SEC = 1
_WORLD_ERRORS = (RuntimeError, OSError, TypeError, ValueError, RecursionError)
_INPUT_ERRORS = (ValueError, TypeError, LookupError, RecursionError)


class _Stop(Exception):
    def __init__(self, phase: str) -> None:
        if not _PHASE.fullmatch(phase):
            raise ValueError("invalid phase name")
        super().__init__(phase)
        self.phase = phase


def _control_ack(token: str) -> str:
    if not TOKEN.fullmatch(token):
        raise ValueError("invalid receipt token")
    return '{"ack":"' + token + '"}'


def _object(body: str) -> dict[str, Any]:
    value = decisions.decode(body)
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return value


def _record(value: object, why: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(why)
    record: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(why)
        record[key] = item
    return record


def _checked_view(
    view: dict[str, Any], role: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if role not in ROLES:
        raise ValueError("unknown role")
    keys = {"observation"} if role == "sales" else {"observation", "report"}
    observation, report = view.get("observation"), view.get("report")
    if view.keys() != keys or not isinstance(observation, dict) or not observation:
        raise ValueError("malformed view")
    if report is not None and (not isinstance(report, dict) or not report):
        raise ValueError("malformed view report")
    return observation, report


def _checked_receipt(receipt: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    report, token = receipt.get("report"), receipt.get("token")
    if (
        receipt.keys() != {"report", "token"}
        or (report is not None and (not isinstance(report, dict) or not report))
        or not isinstance(token, str)
        or not TOKEN.fullmatch(token)
    ):
        raise ValueError("malformed receipt")
    return report, token


def _checked_report(raw: object) -> dict[str, Any]:
    report = _record(raw, "malformed sales report")
    if report.keys() != _REPORT_KEYS:
        raise ValueError("malformed sales report")
    pick, reason = report["pick"], report["reason"]
    if type(pick) is not int or pick not in decisions.PICKS:
        raise ValueError("malformed sales pick")
    if not isinstance(reason, str) or not reason:
        raise ValueError("malformed sales reason")
    return report


def _checked_pick(pick: object) -> int:
    if type(pick) is not int or pick not in decisions.PICKS:
        raise ValueError("malformed price pick")
    return pick


def _checked_answer(answer: object) -> str:
    if (
        not isinstance(answer, str)
        or not 0 < len(answer.encode("utf-8")) <= _MAX_REPLY_BYTES
    ):
        raise ValueError("invalid model reply")
    return answer


def _model_name(value: object, optional: bool) -> bool:
    if value is None:
        return optional
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_MODEL_CHARS
        and value.isprintable()
    )


def _checked_meta(
    raw: object, prompt: str, answer: str, sessions: set[str]
) -> dict[str, Any]:
    meta = _record(raw, "unexpected provenance fields")
    if meta.keys() != META_KEYS:
        raise ValueError("unexpected provenance fields")
    digests = (meta["session_sha256"], meta["prompt_sha256"], meta["answer_sha256"])
    if not all(isinstance(d, str) and decisions.HEX64.fullmatch(d) for d in digests):
        raise ValueError("invalid provenance digest")
    tokens = meta["client_tokens"]
    if (
        meta["prompt_sha256"] != decisions.sha(prompt)
        or meta["answer_sha256"] != decisions.sha(answer)
        or meta["session_sha256"] in sessions
        or not _model_name(meta["requested_model"], optional=False)
        or not _model_name(meta["served_model"], optional=True)
        or not (tokens is None or (type(tokens) is int and tokens > 0))
        or type(meta["tool_steps"]) is not int
        or meta["tool_steps"] != 0
        or meta["credit_fallback"] is not False
    ):
        raise ValueError("provenance does not bind this fresh tool-free turn")
    return meta


def _host_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and Path(value).is_absolute()
        and os.path.normpath(value) == value
    )


def _private_root(owner: Path) -> None:
    owner.mkdir(mode=0o700, exist_ok=True)
    info = owner.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("owner directory must be a private 0700 directory")


async def _get(environment: SeatEnvironment, path: str) -> tuple[int, dict[str, Any]]:
    status, body = await seat_agent._world_request(environment, path)
    return status, _object(body)


async def _view(environment: SeatEnvironment, role: str) -> dict[str, Any]:
    async with asyncio.timeout(_VIEW_TIMEOUT_SEC):
        for _ in range(_VIEW_ATTEMPTS):
            status, view = await _get(environment, "/view")
            if status == 200:
                return view
            pending = (
                status == 409
                and role != "sales"
                and view.keys() == {"error"}
                and isinstance(view["error"], str)
            )
            if not pending:
                raise ValueError("view rejected")
            await asyncio.sleep(_RETRY_SEC)
    raise TimeoutError("view wait exhausted")


class TimingSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        client: str | None = None,
        owner: str | None = None,
        control: bool | None = None,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if kwargs or extra_env or model_name is not None:
            raise ValueError("Price timing seat accepts only client, owner and control")
        if type(control) is not bool:
            raise ValueError("Price timing seat requires a boolean control flag")
        if control and client is not None:
            raise ValueError("Price timing control seat accepts no client")
        if not _host_path(owner) or not (control or _host_path(client)):
            raise ValueError(
                "Price timing seat requires absolute host client and owner paths"
            )
        assert isinstance(owner, str) and (control or isinstance(client, str))
        super().__init__(logs_dir=logs_dir, model_name=None, logger=logger)
        self._client = None if client is None else Path(client)
        self._owner = Path(owner)
        self._control = control
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
            or environment.service not in ROLES
        ):
            raise _Stop("invalid_seat")
        match = TRIAL_SEAT.fullmatch(self.session_id or "")
        if match is None or match[2] != environment.service:
            raise _Stop("invalid_seat")
        if (int(match[1]) == CONTROL_WORLD) != self._control:
            raise _Stop("control_world_mismatch")
        if not self._control:
            try:
                _private_root(self._owner)
            except (OSError, ValueError):
                raise _Stop("owner_unavailable") from None
        return environment

    async def _read_view(
        self, environment: SeatEnvironment, metadata: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        metadata["phase"] = "waiting_view"
        try:
            view = await _view(environment, environment.service)
        except TimeoutError:
            raise _Stop("view_timeout") from None
        except _WORLD_ERRORS:
            raise _Stop("view_unavailable") from None
        try:
            observation, report = _checked_view(view, environment.service)
            digest = view_hash(view)
        except _INPUT_ERRORS:
            raise _Stop("invalid_view") from None
        return observation, report, digest

    async def _ask(
        self,
        purpose: str,
        prompt: str,
        digest: str,
        control_reply: str,
        metadata: dict[str, Any],
    ) -> str:
        assert purpose in PURPOSES and decisions.HEX64.fullmatch(digest)
        slug = purpose.replace("-", "_")
        metadata["phase"] = f"{slug}_turn"
        if self._control:
            answer, meta = control_reply, dict(CONTROL_META)
        else:
            assert self._client is not None
            try:
                answer, raw = await asyncio.to_thread(
                    timing_seat.call,
                    self._client,
                    self._owner / purpose,
                    prompt,
                    purpose,
                )
            except timing_seat.SeatFailure as failure:
                category = failure.category
                if not isinstance(category, str) or not _PHASE.fullmatch(category):
                    category = "failed"
                raise _Stop(f"{slug}_{category}") from None
            try:
                answer = _checked_answer(answer)
                meta = _checked_meta(raw, prompt, answer, self._sessions)
            except _INPUT_ERRORS:
                raise _Stop(f"{slug}_provenance_rejected") from None
            self._sessions.add(meta["session_sha256"])
        metadata["turns"].append(
            {"purpose": purpose, "meta": meta, "view_sha256": digest, "reply": answer}
        )
        return answer

    async def _post(
        self,
        environment: SeatEnvironment,
        path: str,
        payload: dict[str, Any],
        digest: str,
        metadata: dict[str, Any],
        pending: str,
        rejected: str,
    ) -> None:
        metadata["phase"] = pending
        try:
            status, body = await seat_agent._world_request(
                environment, path, payload, view_sha256=digest
            )
            accepted = _object(body)
        except _WORLD_ERRORS:
            raise _Stop(rejected) from None
        if (
            status != 200
            or accepted.keys() != {"accepted"}
            or accepted["accepted"] is not True
        ):
            raise _Stop(rejected)

    async def _sales(
        self, environment: SeatEnvironment, metadata: dict[str, Any]
    ) -> None:
        observation, _, digest = await self._read_view(environment, metadata)
        try:
            prompt = m.sales_prompt(observation)
        except _INPUT_ERRORS:
            raise _Stop("invalid_view") from None
        answer = await self._ask("sales", prompt, digest, CONTROL_SALES, metadata)
        try:
            report = _checked_report(m.read_sales(answer))
        except _INPUT_ERRORS:
            raise _Stop("invalid_sales_reply") from None
        await self._post(
            environment,
            "/submit",
            report,
            digest,
            metadata,
            "submitting_report",
            "report_rejected",
        )

    async def _receipt(
        self, environment: SeatEnvironment, metadata: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, str]:
        metadata["phase"] = "reading_receipt"
        try:
            status, receipt = await _get(environment, "/receipt")
        except _WORLD_ERRORS:
            raise _Stop("receipt_unavailable") from None
        if status != 200:
            raise _Stop("receipt_rejected")
        try:
            report, token = _checked_receipt(receipt)
            return report, token, view_hash(receipt)
        except _INPUT_ERRORS:
            raise _Stop("invalid_receipt") from None

    async def _finance(
        self, environment: SeatEnvironment, metadata: dict[str, Any]
    ) -> None:
        observation, report, digest = await self._read_view(environment, metadata)
        try:
            price_prompt = m.price_prompt(observation, report)
        except _INPUT_ERRORS:
            raise _Stop("invalid_view") from None
        price_reply = await self._ask(
            "finance-price",
            price_prompt,
            digest,
            CONTROL_PRICE[environment.service],
            metadata,
        )
        try:
            pick = _checked_pick(m.read_price(price_reply))
        except _INPUT_ERRORS:
            raise _Stop("invalid_price_reply") from None
        await self._post(
            environment,
            "/submit",
            {"pick": pick},
            digest,
            metadata,
            "submitting_price",
            "price_rejected",
        )
        late_report, token, receipt_digest = await self._receipt(environment, metadata)
        try:
            receipt_prompt = m.receipt_prompt(
                price_prompt, price_reply, late_report, token
            )
        except _INPUT_ERRORS:
            raise _Stop("invalid_receipt") from None
        ack_reply = await self._ask(
            "finance-receipt",
            receipt_prompt,
            receipt_digest,
            _control_ack(token),
            metadata,
        )
        try:
            acknowledged = m.read_receipt(ack_reply, token)
        except _INPUT_ERRORS:
            acknowledged = False
        if acknowledged is not True:
            raise _Stop("invalid_receipt_reply")
        await self._post(
            environment,
            "/ack",
            {"ack": token},
            receipt_digest,
            metadata,
            "acknowledging",
            "ack_rejected",
        )

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, Any] = {"role": None, "phase": "starting", "turns": []}
        context.metadata = metadata
        try:
            if self._started:
                raise _Stop("already_started")
            self._started = True
            seat = self._bind(environment)
            metadata["role"] = seat.service
            if seat.service == "sales":
                await self._sales(seat, metadata)
            else:
                await self._finance(seat, metadata)
        except _Stop as stop:
            metadata["phase"] = stop.phase
            raise NonZeroAgentExitCodeError(
                f"Price timing seat: {stop.phase}"
            ) from None
        metadata["phase"] = "complete"
