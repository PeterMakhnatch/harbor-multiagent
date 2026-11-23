import asyncio
import hashlib
import json
import logging
import re
import shlex
import sys
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..calendar_notices.seat import _send_next
from ..visibility import calendar_protocol, local_seat, seat_command
from ..visibility.calendar_seat import _message, _request, _SeatError, _send
from . import protocol as p

AGENT_NAME = "seatlab-fanin-seat"
AGENT_VERSION = "1.0.0"
ROLES = ("booker", "reporter")
METADATA_KEYS = (
    "seat",
    "role",
    "stage",
    "error",
    "started_turn",
    "prompt_sha256",
    "reply_sha256",
    "commands",
    "sent",
    "polls",
)
ACTIONS = ("report", "skip")
ATTENDEES = {"p1": 2, "p2": 2, "p3": 1, "p4": 1}
_COMMAND_TIMEOUT_SEC = 90.0
_EXEC_TIMEOUT_SEC = 8
_POLL_INTERVAL_SEC = 1
_REPORT_WAIT_SEC = 150
_COMMAND_WAIT_SEC = 330
_MAX_COMMAND_ID = 2 * len(p.SEATS) - 3
_COMMAND_ID = re.compile(r"m[1-9][0-9]?")
_COMMANDS: dict[str, seat_command.CommandSeat] = {}
_BOUND = False

_STARTS_CODE = r"""
import json
import os
import sys


def unique(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError
    return value


try:
    raw = os.environ['SEAT_BRIEFING']
    people = int(os.environ['SEATLAB_FANIN_PEOPLE'])
    if len(raw) > 16384 or people not in (1, 2):
        raise ValueError
    briefing = json.loads(raw, object_pairs_hook=unique)
    days = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')
    if type(briefing) is not dict or briefing.keys() != {'calendars'}:
        raise ValueError
    calendars = briefing['calendars']
    if type(calendars) is not dict or len(calendars) != people:
        raise ValueError
    busy = [0] * len(days)
    for calendar in calendars.values():
        if type(calendar) is not dict or calendar.keys() != set(days):
            raise ValueError
        for index, day in enumerate(days):
            hours = calendar[day]
            if (type(hours) is not list or len(hours) > 8
                or any(type(hour) is not int or not 9 <= hour <= 16 for hour in hours)
                or len(set(hours)) != len(hours)):
                raise ValueError
            for hour in hours:
                busy[index] |= 1 << (hour - 9)
    starts = [
        index * 7 + offset
        for index in range(len(days))
        for offset in range(7)
        if not busy[index] & (3 << offset)
    ]
    print(json.dumps(starts, separators=(',', ':')))
except Exception:
    sys.exit(1)
"""
_STARTS_COMMAND = "python3 -I -c " + shlex.quote(_STARTS_CODE)


def bind(socket_dir: Path) -> None:
    global _BOUND
    if _BOUND:
        raise RuntimeError("fan-in seat commands already bound")
    if not isinstance(socket_dir, Path) or not socket_dir.is_absolute():
        raise ValueError("fan-in socket directory must be an absolute path")
    _BOUND = True
    relay = str(Path(local_seat.__file__).resolve())
    commands = {
        seat: seat_command.CommandSeat(
            [sys.executable, "-IS", relay, "turn", str(socket_dir / f"{seat}.sock")],
            environ=None,
            timeout=_COMMAND_TIMEOUT_SEC,
        )
        for seat in p.SEATS
    }
    _COMMANDS.update(commands)
    if tuple(_COMMANDS) != p.SEATS:
        raise RuntimeError("fan-in seat commands are not one per seat")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _turn(seat: str, prompt: str, stage: str, metadata: dict[str, Any]) -> str:
    if metadata["started_turn"]:
        raise _SeatError("invalid_seat_binding")
    command = _COMMANDS.get(seat)
    if command is None:
        raise _SeatError("command_unbound")
    metadata["stage"] = stage
    metadata["started_turn"] = True
    metadata["prompt_sha256"] = _sha(prompt)
    try:
        text = await asyncio.to_thread(command, prompt)
    except TimeoutError:
        raise _SeatError("turn_timeout") from None
    except ValueError:
        raise _SeatError("turn_failed") from None
    if not isinstance(text, str):
        raise _SeatError("turn_failed")
    metadata["reply_sha256"] = _sha(text)
    return text


async def _check_identity(environment: SeatEnvironment, seat: str) -> None:
    identity = await _request(environment, "whoami", 200)
    limits = identity.get("limits")
    if (
        identity.keys() != {"seat", "seats", "messaging", "limits"}
        or identity["seat"] != seat
        or identity["seats"] != list(p.SEATS)
        or identity["messaging"] is not True
        or not isinstance(limits, dict)
        or limits.keys() != {"max_text", "max_messages"}
        or any(type(value) is not int or value <= 0 for value in limits.values())
    ):
        raise _SeatError("invalid_transport")


async def _read_starts(environment: SeatEnvironment, seat: str) -> list[int]:
    try:
        result = await environment.exec(
            _STARTS_COMMAND,
            env={"SEATLAB_FANIN_PEOPLE": str(ATTENDEES[seat])},
            timeout_sec=_EXEC_TIMEOUT_SEC,
        )
    except TimeoutError:
        raise _SeatError("calendar_invalid") from None
    if result.return_code != 0 or result.stdout is None or len(result.stdout) > 1024:
        raise _SeatError("calendar_invalid")
    try:
        starts = json.loads(result.stdout)
    except ValueError:
        raise _SeatError("calendar_invalid") from None
    if (
        type(starts) is not list
        or not starts
        or any(type(slot) is not int or not 0 <= slot < p.SLOTS for slot in starts)
        or starts != sorted(set(starts))
    ):
        raise _SeatError("calendar_invalid")
    return starts


def _command_id(raw: object) -> str:
    mid = raw.get("id") if isinstance(raw, dict) else None
    if (
        not isinstance(mid, str)
        or _COMMAND_ID.fullmatch(mid) is None
        or int(mid[1:]) > _MAX_COMMAND_ID
    ):
        raise _SeatError("invalid_transport")
    return mid


async def _await_message(
    environment: SeatEnvironment,
    prefix: list[dict[str, Any]],
    sender: str,
    mid: str | None,
    wait_sec: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    offset = len(prefix)
    try:
        async with asyncio.timeout(wait_sec):
            for _ in range(wait_sec // _POLL_INTERVAL_SEC + 1):
                reply = await _request(environment, "inbox", 200)
                metadata["polls"] += 1
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
                    raw = inbox[offset]
                    expected = _command_id(raw) if mid is None else mid
                    message = _message(raw, expected, sender, environment.service)
                    if prefix and message["t"] < prefix[-1]["t"]:
                        raise _SeatError("invalid_transport")
                    return message
                await asyncio.sleep(_POLL_INTERVAL_SEC)
    except TimeoutError:
        raise _SeatError("inbox_timeout") from None
    raise _SeatError("inbox_timeout")


def _canonical(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict) or text != p.wire(value):
        raise _SeatError("invalid_transport")
    return value


def _action(text: str) -> str:
    value = _canonical(text)
    if value.keys() != {"action"} or value["action"] not in ACTIONS:
        raise _SeatError("invalid_transport")
    return value["action"]


def _delivered(seat: str, text: str) -> dict[str, Any]:
    value = _canonical(text)
    exact, starts = value.get("exact"), value.get("starts")
    if (
        value.keys() != {"exact", "starts"}
        or type(exact) is not bool
        or (starts is None and exact)
        or (starts is not None and type(starts) is not list)
    ):
        raise _SeatError("invalid_transport")
    return {"seat": seat, "exact": exact, "starts": starts}


async def _post(
    environment: SeatEnvironment,
    log: list[dict[str, Any]],
    to: str,
    text: str,
    metadata: dict[str, Any],
) -> None:
    mid = f"m{len(log) + 1}"
    if log:
        message = await _send_next(environment, mid, to, text, after=log[-1])
    else:
        message = await _send(environment, mid, to, text)
    metadata["sent"] += 1
    log.append(message)


async def _submit(environment: SeatEnvironment, slot: int) -> None:
    try:
        answer = calendar_protocol.answer(slot)
    except ValueError:
        raise _SeatError("book_invalid") from None
    try:
        receipt = await _request(environment, "submit", 200, {"answer": answer})
    except _SeatError:
        raise _SeatError("submit_rejected") from None
    if receipt.keys() != {"accepted"} or receipt["accepted"] is not True:
        raise _SeatError("submit_rejected")


class FanInSeat(BaseAgent):
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
            or not p.admitted(world, arm)
            or seat not in p.SEATS
            or model_name is not None
            or extra_env
        ):
            raise ValueError("invalid_fanin_seat")
        order = p.order(world)
        if sorted(order) != list(p.SEATS):
            raise ValueError("invalid_fanin_seat")
        super().__init__(
            logs_dir=logs_dir,
            model_name=None,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )
        self._arm = arm
        self._seat = seat
        self._booker = order[0]
        self._reporters = order[1:]
        self._role = ROLES[0] if seat == self._booker else ROLES[1]
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

    async def _book(
        self, environment: SeatEnvironment, own: list[int], metadata: dict[str, Any]
    ) -> None:
        log: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        asking = True
        for reporter in self._reporters:
            asking = asking and p.ask_next(self._arm, own, reports)
            action = ACTIONS[0] if asking else ACTIONS[1]
            metadata["stage"] = "requesting" if asking else "skipping"
            await _post(
                environment, log, reporter, p.wire({"action": action}), metadata
            )
            metadata["commands"].append(action)
            if not asking:
                continue
            metadata["stage"] = "waiting_report"
            reply = await _await_message(
                environment,
                log,
                reporter,
                f"m{len(log) + 1}",
                _REPORT_WAIT_SEC,
                metadata,
            )
            log.append(reply)
            reports.append(_delivered(reporter, reply["text"]))
        text = await _turn(self._seat, p.book_prompt(own, reports), "booking", metadata)
        slot = p.parse_book(text)
        if slot is None:
            raise _SeatError("book_invalid")
        metadata["stage"] = "submitting"
        await _submit(environment, slot)

    async def _report(
        self, environment: SeatEnvironment, own: list[int], metadata: dict[str, Any]
    ) -> None:
        metadata["stage"] = "waiting_command"
        command = await _await_message(
            environment, [], self._booker, None, _COMMAND_WAIT_SEC, metadata
        )
        action = _action(command["text"])
        metadata["commands"].append(action)
        if action == ACTIONS[1]:
            return
        text = await _turn(self._seat, p.report_prompt(own), "reporting", metadata)
        metadata["stage"] = "replying"
        mid = f"m{int(command['id'][1:]) + 1}"
        reply = p.wire(p.outgoing(own, text))
        await _send_next(environment, mid, self._booker, reply, after=command)
        metadata["sent"] += 1

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, Any] = {
            "seat": self._seat,
            "role": self._role,
            "stage": "starting",
            "error": None,
            "started_turn": False,
            "prompt_sha256": None,
            "reply_sha256": None,
            "commands": [],
            "sent": 0,
            "polls": 0,
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
            metadata["stage"] = "identity"
            await _check_identity(environment, self._seat)
            metadata["stage"] = "calendar"
            own = await _read_starts(environment, self._seat)
            if self._role == ROLES[0]:
                await self._book(environment, own, metadata)
            else:
                await self._report(environment, own, metadata)
            metadata["stage"] = "done"
        except _SeatError as exc:
            metadata["error"] = str(exc)
        except (ValueError, RecursionError):
            metadata["error"] = "invalid_transport"
        except asyncio.CancelledError:
            metadata["error"] = "cancelled"
            raise
        except (OSError, LookupError, TypeError, RuntimeError):
            metadata["error"] = "seat_failed"
