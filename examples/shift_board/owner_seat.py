import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, cast, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from ..native_owner_process import OWNER_ID, OwnerFailure
from ..native_seat_http import SeatFailure, seat_request
from .owner_game import MAX_DISPATCHER_TURNS, play
from .owner_io import (
    METADATA_KEY,
    RECEIPT_SCHEMA,
    REPLY_SCHEMA,
    REQUEST_SCHEMA,
    choose,
)

RECEIPT_NAME = "owner-receipt.json"
ROLES = ("planner", "dispatcher")
ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/brief"),
        ("GET", "/board"),
        ("GET", "/status"),
        ("POST", "/prepare"),
        ("POST", "/publish"),
        ("POST", "/leave"),
    }
)
GAME_PATHS = frozenset({"/board", "/prepare", "/publish", "/leave"})
WAIT_FLAGS = frozenset({"prepared", "incumbent"})
STATUS_KEYS = frozenset({"prepared", "incumbent", "closed"})
SUMMARY_KEYS = frozenset({"role", "turns", "terminal", "reads", "conflicts"})
TERMINALS = frozenset({"publish", "leave", "stop"})
SLOT = re.compile(r"[A-D]")
REVISION = re.compile(r"[0-9a-f]{32}")
POLL_SECONDS = 2.0
MAX_POLLS = 300
WAIT_SECONDS = 600.0
MAX_DECISIONS = MAX_DISPATCHER_TURNS


def _body(path: str, body: dict[str, object] | None) -> str | None:
    if body is None:
        return None
    if type(body) is not dict:
        raise SeatFailure("request_body_invalid")
    if path == "/leave":
        if body:
            raise SeatFailure("request_body_invalid")
        return "{}"
    slot, base = body.get("slot"), body.get("base")
    if (
        set(body) != {"slot", "base"}
        or type(slot) is not str
        or SLOT.fullmatch(slot) is None
        or type(base) is not str
        or REVISION.fullmatch(base) is None
    ):
        raise SeatFailure("request_body_invalid")
    return json.dumps({"slot": slot, "base": base}, separators=(",", ":"))


def _receipt_reason(exc: BaseException) -> str:
    if isinstance(exc, (SeatFailure, OwnerFailure)):
        return exc.reason
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "play_failed"


def _checked_summary(summary: object, role: str) -> dict[str, object]:
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS:
        raise SeatFailure("summary_malformed")
    checked = cast(dict[str, object], summary)
    counts = [checked[key] for key in ("turns", "reads", "conflicts")]
    if (
        checked["role"] != role
        or checked["terminal"] not in TERMINALS
        or not all(
            not isinstance(count, bool)
            and isinstance(count, int)
            and 0 <= count <= MAX_DISPATCHER_TURNS
            for count in counts
        )
    ):
        raise SeatFailure("summary_malformed")
    return dict(checked)


class OwnerSeat(BaseAgent):
    @staticmethod
    @override
    def name() -> str:
        return "shift-board-owner"

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
            raise ValueError("OwnerSeat needs a public owner identifier as model_name")
        if "://" in model_name:
            raise ValueError("OwnerSeat model_name must not be a URL")
        if skip_model_parsing is not True:
            raise ValueError("OwnerSeat needs skip_model_parsing: true")
        if extra_env:
            raise ValueError("OwnerSeat forwards no environment into its seat")
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
        receipt: dict[str, Any] = {
            "schema": RECEIPT_SCHEMA,
            "role": None,
            "requested": self.requested,
            "request_schema": REQUEST_SCHEMA,
            "reply_schema": REPLY_SCHEMA,
            "decisions": [],
            "summary": None,
            "outcome": "incomplete",
            "failure": None,
        }
        self._record(context, receipt)
        try:
            await self._play(environment, context, receipt)
        except BaseException as exc:
            receipt["outcome"] = "failed"
            receipt["failure"] = _receipt_reason(exc)
            raise
        else:
            receipt["outcome"] = "complete"
        finally:
            self._record(context, receipt)
            self._write_receipt(receipt)

    async def _play(
        self,
        environment: BaseEnvironment,
        context: AgentContext,
        receipt: dict[str, Any],
    ) -> None:
        status, brief = await seat_request(environment, ROUTES, "GET", "/brief", None)
        role = brief.get("role")
        if status != 200 or not isinstance(role, str) or role not in ROLES:
            raise SeatFailure("brief_unavailable", f"HTTP {status}")
        receipt["role"] = role
        self._record(context, receipt)

        async def request(
            path: str, body: dict[str, object] | None
        ) -> tuple[int, dict[str, object]]:
            if path not in GAME_PATHS:
                raise SeatFailure("request_path_refused")
            method = "GET" if body is None else "POST"
            if (method, path) not in ROUTES:
                raise SeatFailure("request_method_refused")
            return await seat_request(
                environment, ROUTES, method, path, _body(path, body)
            )

        async def decide(view: dict[str, object]) -> str:
            if len(receipt["decisions"]) >= MAX_DECISIONS:
                raise SeatFailure("decision_bound_exceeded")
            choice, decision = await choose(view, self.requested)
            receipt["decisions"].append(decision)
            self._record(context, receipt)
            return choice

        async def wait_for(flag: str) -> None:
            await self._wait_for(environment, flag)

        summary = await play(dict(brief), request, decide, wait_for)
        receipt["summary"] = _checked_summary(summary, role)

    async def _wait_for(self, environment: BaseEnvironment, flag: str) -> None:
        if flag not in WAIT_FLAGS:
            raise SeatFailure("wait_flag_refused")
        deadline = time.monotonic() + WAIT_SECONDS
        for _ in range(MAX_POLLS):
            status, reply = await seat_request(
                environment, ROUTES, "GET", "/status", None
            )
            if status != 200 or set(reply) != STATUS_KEYS:
                raise SeatFailure("status_unavailable", f"HTTP {status}")
            if not all(type(reply[key]) is bool for key in STATUS_KEYS):
                raise SeatFailure("status_malformed")
            if reply[flag]:
                return
            if reply["closed"]:
                raise SeatFailure("world_closed", flag)
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(POLL_SECONDS)
        raise SeatFailure("wait_timeout", flag)

    def _record(self, context: AgentContext, receipt: dict[str, Any]) -> None:
        snapshot = json.loads(json.dumps(receipt, allow_nan=False))
        context.metadata = {**(context.metadata or {}), METADATA_KEY: snapshot}

    def _write_receipt(self, receipt: dict[str, Any]) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        target = self.logs_dir / RECEIPT_NAME
        staged = self.logs_dir / f".{RECEIPT_NAME}.tmp"
        data = json.dumps(receipt, allow_nan=False, indent=2) + "\n"
        staged.write_text(data, encoding="utf-8")
        os.replace(staged, target)
