import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, cast, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..live_answers.seat import _add_usage
from ..visibility import calendar_packet, calendar_protocol, deepseek
from ..visibility.calendar_seat import (
    _message,
    _read_local_table,
    _request,
    _SeatError,
    _send,
    _table,
    _write_private,
)
from . import protocol as p
from . import team_views

_INBOX_TIMEOUT_SEC = 180
_POLL_INTERVAL_SEC = 1
_DAY_COUNT = len(calendar_protocol._DAYS)
_CONTROL_DAYS = (0, 1)
_MISSING_CONTROL_SLOT = object()


async def _receive(
    environment: SeatEnvironment, peer: str, *prefix: dict[str, Any]
) -> dict[str, Any]:
    offset = len(prefix)
    if offset > 2:
        raise _SeatError("invalid_transport")
    try:
        async with asyncio.timeout(_INBOX_TIMEOUT_SEC):
            while True:
                reply = await _request(environment, "inbox", 200)
                inbox = reply.get("messages")
                if (
                    reply.keys() != {"messages"}
                    or not isinstance(inbox, list)
                    or not offset <= len(inbox) <= offset + 1
                    or any(
                        seen != prior or type(seen["t"]) is not type(prior["t"])
                        for seen, prior in zip(inbox[:offset], prefix, strict=True)
                    )
                ):
                    raise _SeatError("invalid_transport")
                if len(inbox) > offset:
                    message = _message(
                        inbox[offset], f"m{offset + 1}", peer, environment.service
                    )
                    if prefix and message["t"] < prefix[-1]["t"]:
                        raise _SeatError("invalid_transport")
                    return message
                await asyncio.sleep(_POLL_INTERVAL_SEC)
    except TimeoutError:
        raise _SeatError("inbox_timeout") from None


async def _send_next(
    environment: SeatEnvironment,
    mid: str,
    peer: str,
    text: str,
    *,
    after: dict[str, Any],
) -> dict[str, Any]:
    message = await _send(environment, mid, peer, text)
    if message["t"] < after["t"]:
        raise _SeatError("invalid_transport")
    return message


def _object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict) or text != p.wire(value):
        raise _SeatError("invalid_transport")
    return value


def _decode_index(text: str, arm: str) -> dict[str, Any]:
    index = _object(text)
    fields = {"initial", "count"}
    if arm == "named":
        fields.add("changed")
    count = index.get("count")
    if index.keys() != fields or type(count) is not int or not 0 <= count <= _DAY_COUNT:
        raise _SeatError("invalid_transport")
    p._starts(index["initial"])
    if arm == "named":
        changed = p._days(index["changed"])
        if len(changed) != count or changed != sorted(changed):
            raise _SeatError("invalid_transport")
    return index


def _decode_request(text: str) -> list[int]:
    days = cast(list[int], p.decode(text, "pick"))
    if text != p.request_wire(days):
        raise _SeatError("invalid_transport")
    return days


def _decode_opened(text: str, days: list[int]) -> dict[str, Any]:
    opened = _object(text)
    reports = opened.get("reports")
    if (
        opened.keys() != {"reports"}
        or not isinstance(reports, list)
        or len(reports) != len(days)
    ):
        raise _SeatError("invalid_transport")
    for day, raw in zip(days, reports, strict=True):
        report = calendar_packet._object(raw, "day", "starts")
        if type(report["day"]) is not int or report["day"] != day:
            raise _SeatError("invalid_transport")
        if any(p._day(slot) != day for slot in p._starts(report["starts"])):
            raise _SeatError("invalid_transport")
    return opened


def _control_choice(index: int, control: str) -> str:
    if control not in ("oracle", "wrong"):
        raise _SeatError("invalid_control")
    first, second = calendar_packet.load(
        calendar_packet.PACKET, calendar_packet.PACKET_SHA256
    )[index].tables
    shared = set(calendar_packet.starts(first)).intersection(
        calendar_packet.starts(second)
    )
    if len(shared) != 1:
        raise _SeatError("invalid_control")
    slot = shared.pop()
    if control == "wrong":
        slot = 1 if slot == 0 else 0
    return p.wire({"slot": slot})


class NoticeSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        world: str,
        arm: str,
        seat: str,
        control: str = "live",
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
    ) -> None:
        self._cell = self._make_cell(world, arm, control)
        declared = p.MODEL if control == "live" else None
        if seat not in p.SEATS or model_name != declared or extra_env:
            raise ValueError("invalid_calendar_notice_actor")
        super().__init__(
            logs_dir=logs_dir,
            model_name=declared,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )
        self._seat = seat
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        if model_name != p.MODEL:
            raise ValueError("invalid_direct_model")
        return "deepseek", deepseek.MODEL

    @staticmethod
    @override
    def name() -> str:
        return p.AGENT_NAME

    @override
    def version(self) -> str:
        return p.AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @staticmethod
    def _make_cell(world: str, arm: str, control: str) -> p.Cell | team_views.Cell:
        return p.Cell(world, arm, control)

    async def _own_table(
        self, environment: SeatEnvironment
    ) -> tuple[calendar_packet.Table, str]:
        table = await _table(environment, self._cell.index, self._seat)
        return table, p.digest(p.wire(table))

    @property
    def _notice_arm(self) -> str:
        return self._cell.arm

    def _initial_prompt(self, table: calendar_packet.Table) -> str:
        return p.initial_prompt(table)

    def _revision_prompt(self, table: calendar_packet.Table, old: object) -> str:
        return p.revision_prompt(table, old)

    def _pick_prompt(
        self, table: calendar_packet.Table, index: dict[str, object]
    ) -> str:
        return p.pick_prompt(table, index)

    def _choice_prompt(
        self, table: calendar_packet.Table, old: object, opened: dict[str, object]
    ) -> str:
        return p.choice_prompt(table, old, opened)

    def _choice_control_text(self) -> str:
        return _control_choice(self._cell.index, self._cell.control)

    async def _measure(
        self,
        phase: str,
        prompt: str,
        context: AgentContext,
        state: dict[str, Any],
        *,
        control_text: str | None,
    ) -> list[int] | int:
        live = self._cell.control == "live"
        if (control_text is None) != live or self.context_id is None:
            raise _SeatError("invalid_turn_binding")
        role = p.role(cast(p.Cell, self._cell), phase)
        state.update(phase=phase, stage="model_turn")
        receipt: dict[str, Any] = {
            "trial_uuid": str(self.context_id),
            "run": self._cell.run,
            "seat": self._seat,
            "phase": phase,
            "ledger_role": role if live else None,
            "prompt_sha256": p.digest(prompt),
            "response_text": None,
            "response_sha256": None,
            "served_model": None,
            "usage": None,
            "value": None,
            "status": "model_failed",
        }
        try:
            if control_text is None:
                turn = await asyncio.to_thread(
                    deepseek.turn, prompt, run_id=self._cell.run, role=role
                )
                usage = (turn.input_tokens, turn.cache_tokens, turn.output_tokens)
                receipt.update(served_model=turn.served_model, usage=usage)
                _add_usage(context, usage, turn.served_model)
                text = turn.text
            else:
                text = control_text
            receipt.update(response_text=text, response_sha256=p.digest(text))
            try:
                value = p.decode(text, phase)
            except (ValueError, RecursionError):
                receipt["status"] = "model_invalid"
                raise _SeatError("model_invalid") from None
            receipt.update(status="accepted", value=value)
            return value
        except deepseek.SeatUnavailable as exc:
            if exc.settled_usage is not None:
                receipt["usage"] = exc.settled_usage
                _add_usage(context, exc.settled_usage, None)
            raise _SeatError("model_failed") from None
        except _SeatError:
            raise
        except (
            OSError,
            sqlite3.Error,
            ValueError,
            LookupError,
            TypeError,
            RuntimeError,
        ):
            raise _SeatError("model_failed") from None
        finally:
            _write_private(self.logs_dir / f"{phase}.json", receipt)

    async def _report(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        table: calendar_packet.Table,
        state: dict[str, Any],
    ) -> None:
        live = self._cell.control == "live"
        peer = self._cell.receiver
        old = cast(
            list[int],
            await self._measure(
                "initial",
                self._initial_prompt(table),
                context,
                state,
                control_text=(
                    None
                    if live
                    else p.wire(
                        {
                            "starts": calendar_packet.starts(
                                (*table[:2], (0,) * _DAY_COUNT)
                            )
                        }
                    )
                ),
            ),
        )
        new = cast(
            list[int],
            await self._measure(
                "revision",
                self._revision_prompt(table, old),
                context,
                state,
                control_text=(
                    None if live else p.wire({"starts": calendar_packet.starts(table)})
                ),
            ),
        )
        state["stage"] = "sending_index"
        index = await _send(
            environment, "m1", peer, p.index_wire(old, new, self._notice_arm)
        )
        state["sent"].append(index)
        state["stage"] = "waiting_for_request"
        request = await _receive(environment, peer, index)
        days = _decode_request(request["text"])
        state["received"].append(request)
        state["stage"] = "sending_reports"
        opened = await _send_next(
            environment, "m3", peer, p.read_wire(new, days), after=request
        )
        state["sent"].append(opened)

    async def _schedule(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        table: calendar_packet.Table,
        state: dict[str, Any],
    ) -> None:
        live = self._cell.control == "live"
        peer = self._cell.sender
        state["stage"] = "waiting_for_index"
        index_message = await _receive(environment, peer)
        index = _decode_index(index_message["text"], self._notice_arm)
        state["received"].append(index_message)
        days = cast(
            list[int],
            await self._measure(
                "pick",
                self._pick_prompt(table, index),
                context,
                state,
                control_text=None if live else p.wire({"days": _CONTROL_DAYS}),
            ),
        )
        state["stage"] = "sending_request"
        request = await _send_next(
            environment, "m2", peer, p.request_wire(days), after=index_message
        )
        state["sent"].append(request)
        state["stage"] = "waiting_for_reports"
        report_message = await _receive(environment, peer, index_message, request)
        opened = _decode_opened(report_message["text"], days)
        state["received"].append(report_message)
        choice = cast(
            int,
            await self._measure(
                "choice",
                self._choice_prompt(table, index["initial"], opened),
                context,
                state,
                control_text=None if live else self._choice_control_text(),
            ),
        )
        state["stage"] = "submitting"
        reply = await _request(
            environment, "submit", 200, {"answer": calendar_protocol.answer(choice)}
        )
        if reply.keys() != {"accepted"} or reply["accepted"] is not True:
            raise _SeatError("invalid_submission_receipt")

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        state: dict[str, Any] = {
            "run": self._cell.run,
            "seat": self._seat,
            "stage": "starting",
            "phase": None,
            "error": None,
            "calendar_sha256": None,
            "sent": [],
            "received": [],
        }
        context.metadata = {"calendar_notice": state}
        try:
            if (
                self._started
                or not isinstance(environment, SeatEnvironment)
                or environment.service != self._seat
            ):
                raise _SeatError("invalid_seat_binding")
            self._started = True
            table, state["calendar_sha256"] = await self._own_table(environment)
            if self._seat == self._cell.sender:
                await self._report(environment, context, table, state)
            else:
                await self._schedule(environment, context, table, state)
            state["stage"] = "complete"
        except _SeatError as exc:
            state.update(stage=str(exc), error=str(exc))
        except (ValueError, RecursionError):
            state.update(stage="invalid_transport", error="invalid_transport")
        except asyncio.CancelledError:
            state.update(stage="cancelled", error="cancelled")
            raise
        except (OSError, LookupError, TypeError, RuntimeError):
            state.update(stage="seat_failed", error="seat_failed")


class PrivateViewSeat(NoticeSeat):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        world: str,
        arm: str,
        seat: str,
        calendar_sha256: str,
        control: str = "live",
        control_slot: object = _MISSING_CONTROL_SLOT,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
    ) -> None:
        self._calendar_sha256 = calendar_packet._check_digest(calendar_sha256)
        self._control_slot: int | None = None
        if control == "live":
            if control_slot is not _MISSING_CONTROL_SLOT:
                raise ValueError("invalid_control")
        elif control in ("oracle", "wrong"):
            self._control_slot = calendar_protocol._checked_slot(control_slot)
        else:
            raise ValueError("invalid_control")
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            world=world,
            arm=arm,
            seat=seat,
            control=control,
            logger=logger,
            extra_env=extra_env,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )

    @staticmethod
    @override
    def name() -> str:
        return team_views.AGENT_NAME

    @override
    def version(self) -> str:
        return team_views.AGENT_VERSION

    @staticmethod
    @override
    def _make_cell(world: str, arm: str, control: str) -> team_views.Cell:
        if arm in team_views.ROLE_ARMS:
            return team_views.RoleCell(world, arm, control)
        if arm in team_views.JOIN_ARMS:
            return team_views.JoinCell(world, arm, control)
        return team_views.Cell(world, arm, control)

    @override
    async def _own_table(
        self, environment: SeatEnvironment
    ) -> tuple[calendar_packet.Table, str]:
        table = await _read_local_table(environment, self._seat)
        digest = p.digest(p.wire(table))
        if digest != self._calendar_sha256:
            raise _SeatError("invalid_transport")
        return table, digest

    @property
    @override
    def _notice_arm(self) -> str:
        return "named"

    @override
    def _initial_prompt(self, table: calendar_packet.Table) -> str:
        return team_views.initial_prompt(table, self._cell.arm)

    @override
    def _revision_prompt(self, table: calendar_packet.Table, old: object) -> str:
        return team_views.revision_prompt(table, old, self._cell.arm)

    @override
    def _pick_prompt(
        self, table: calendar_packet.Table, index: dict[str, object]
    ) -> str:
        return team_views.pick_prompt(table, index, self._cell.arm)

    @override
    def _choice_prompt(
        self, table: calendar_packet.Table, old: object, opened: dict[str, object]
    ) -> str:
        return team_views.choice_prompt(table, old, opened, self._cell.arm)

    @override
    def _choice_control_text(self) -> str:
        if self._control_slot is None:
            raise _SeatError("invalid_control")
        return p.wire({"slot": self._control_slot})
