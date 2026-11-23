import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..calendar_notices.seat import _receive, _send_next
from ..visibility import calendar_packet, calendar_protocol, local_seat, seat_command
from ..visibility.calendar_seat import _read_local_table, _request, _SeatError, _send
from . import protocol as p

AGENT_NAME = "seatlab-wait-peer-seat"
AGENT_VERSION = "1.0.0"
PHASES = ("report", "open", "book")
_SEATS = ("p1", "p2")
_COMMAND_TIMEOUT_SEC = 90.0
_COMMANDS: dict[str, seat_command.CommandSeat] = {}
_BOUND = False


def bind(socket_dir: Path) -> None:
    global _BOUND
    if _BOUND:
        raise RuntimeError("wait-peer seat commands already bound")
    if not isinstance(socket_dir, Path) or not socket_dir.is_absolute():
        raise ValueError("wait-peer socket directory must be an absolute path")
    _BOUND = True
    relay = str(Path(local_seat.__file__).resolve())
    commands = {
        phase: seat_command.CommandSeat(
            [sys.executable, "-IS", relay, "turn", str(socket_dir / f"{phase}.sock")],
            environ=None,
            timeout=_COMMAND_TIMEOUT_SEC,
        )
        for phase in PHASES
    }
    _COMMANDS.update(commands)
    if tuple(_COMMANDS) != PHASES:
        raise RuntimeError("wait-peer seat commands are not one per phase")


async def _turn(phase: str, prompt: str, metadata: dict[str, Any]) -> str:
    if phase not in PHASES or phase in metadata["turns"]:
        raise _SeatError("invalid_turn_binding")
    command = _COMMANDS.get(phase)
    if command is None:
        raise _SeatError("command_unbound")
    metadata["phase"] = phase
    try:
        text = await asyncio.to_thread(command, prompt)
    except TimeoutError:
        raise _SeatError(f"{phase}_timeout") from None
    except ValueError:
        raise _SeatError(f"{phase}_failed") from None
    if not isinstance(text, str):
        raise _SeatError(f"{phase}_failed")
    metadata["turns"][phase] = {
        "prompt_sha256": p.sha(prompt),
        "reply_sha256": p.sha(text),
    }
    return text


def _decoded(phase: str, text: str) -> list[int] | bool | int:
    try:
        return p.decode(phase, text)
    except (ValueError, TypeError, LookupError, RecursionError):
        raise _SeatError(f"{phase}_invalid") from None


def _report_value(text: str) -> list[int]:
    value = _decoded("report", text)
    if not isinstance(value, list) or any(type(slot) is not int for slot in value):
        raise _SeatError("report_invalid")
    return value


def _open_value(text: str) -> bool:
    value = _decoded("open", text)
    if not isinstance(value, bool):
        raise _SeatError("open_invalid")
    return value


def _book_value(text: str) -> int:
    value = _decoded("book", text)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _SeatError("book_invalid")
    return value


def _open_flag(text: str) -> bool:
    for flag in (False, True):
        if text == p.wire({"open": flag}):
            return flag
    raise _SeatError("invalid_transport")


def _released(text: str) -> list[int] | None:
    if text == p.wire({"opened": False}):
        return None
    value = json.loads(text)
    if (
        not isinstance(value, dict)
        or value.keys() != {"starts"}
        or not isinstance(value["starts"], list)
        or any(type(slot) is not int for slot in value["starts"])
        or text != p.wire(value)
    ):
        raise _SeatError("invalid_transport")
    return value["starts"]


class PeerSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        world: int,
        arm: str,
        seat: str,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
    ) -> None:
        if (
            type(world) is not int
            or seat not in _SEATS
            or model_name is not None
            or extra_env
        ):
            raise ValueError("invalid_wait_peer_seat")
        sender, receiver = p.role_ids(world)
        if (sender, receiver) not in (
            _SEATS,
            _SEATS[::-1],
        ) or arm not in p.ordered_arms(world):
            raise ValueError("invalid_wait_peer_seat")
        super().__init__(
            logs_dir=logs_dir,
            model_name=None,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )
        self._arm = arm
        self._seat = seat
        self._role = "sender" if seat == sender else "receiver"
        self._peer = receiver if seat == sender else sender
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

    async def _report(
        self, environment: SeatEnvironment, starts: list[int], metadata: dict[str, Any]
    ) -> None:
        text = await _turn("report", p.render("report", starts, self._arm), metadata)
        report = _report_value(text)
        metadata["actions"]["report"] = report
        metadata["phase"] = "sending_ready"
        ready = await _send(environment, "m1", self._peer, p.wire({"ready": True}))
        metadata["messages"].append(ready)
        metadata["phase"] = "waiting_open"
        request = await _receive(environment, self._peer, ready)
        metadata["messages"].append(request)
        requested = _open_flag(request["text"])
        release = (
            p.wire({"starts": report})
            if p.allowed(self._arm, requested)
            else p.wire({"opened": False})
        )
        metadata["phase"] = "releasing"
        sent = await _send_next(environment, "m3", self._peer, release, after=request)
        metadata["messages"].append(sent)

    async def _book(
        self, environment: SeatEnvironment, starts: list[int], metadata: dict[str, Any]
    ) -> None:
        metadata["phase"] = "waiting_ready"
        ready = await _receive(environment, self._peer)
        metadata["messages"].append(ready)
        if ready["text"] != p.wire({"ready": True}):
            raise _SeatError("invalid_transport")
        text = await _turn("open", p.render("open", starts, self._arm), metadata)
        requested = _open_value(text)
        metadata["actions"]["open"] = requested
        metadata["phase"] = "sending_open"
        request = await _send_next(
            environment, "m2", self._peer, p.wire({"open": requested}), after=ready
        )
        metadata["messages"].append(request)
        metadata["phase"] = "waiting_release"
        release = await _receive(environment, self._peer, ready, request)
        metadata["messages"].append(release)
        report = _released(release["text"])
        if (report is not None) != p.allowed(self._arm, requested):
            raise _SeatError("invalid_transport")
        text = await _turn(
            "book", p.render("book", starts, self._arm, report), metadata
        )
        slot = _book_value(text)
        try:
            answer = calendar_protocol.answer(slot)
        except ValueError:
            raise _SeatError("book_invalid") from None
        metadata["phase"] = "submitting"
        try:
            receipt = await _request(environment, "submit", 200, {"answer": answer})
        except _SeatError:
            raise _SeatError("submit_rejected") from None
        if receipt.keys() != {"accepted"} or receipt["accepted"] is not True:
            raise _SeatError("submit_rejected")
        metadata["receipt"] = receipt
        metadata["actions"]["book"] = slot

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, Any] = {
            "role": None,
            "phase": "starting",
            "error": None,
            "starts_sha256": None,
            "turns": {},
            "actions": {},
            "messages": [],
            "receipt": None,
        }
        context.metadata = metadata
        try:
            if (
                self._started
                or not isinstance(environment, SeatEnvironment)
                or environment.service != self._seat
            ):
                raise _SeatError("invalid_seat_binding")
            self._started = True
            metadata["role"] = self._role
            metadata["phase"] = "reading_calendar"
            table = await _read_local_table(environment, self._seat)
            starts = list(calendar_packet.starts(table))
            if starts != sorted(set(starts)):
                raise _SeatError("invalid_transport")
            metadata["starts_sha256"] = p.sha(p.wire(starts))
            if self._role == "sender":
                await self._report(environment, starts, metadata)
            else:
                await self._book(environment, starts, metadata)
            metadata["phase"] = "done"
        except _SeatError as exc:
            metadata["error"] = str(exc)
        except (ValueError, RecursionError):
            metadata["error"] = "invalid_transport"
        except asyncio.CancelledError:
            metadata["error"] = "cancelled"
            raise
        except (OSError, LookupError, TypeError, RuntimeError):
            metadata["error"] = "seat_failed"
