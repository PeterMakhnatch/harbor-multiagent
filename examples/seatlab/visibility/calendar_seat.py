"""Run one scoped calendar actor on its original Flotilla seat."""

import asyncio
import json
import logging
import os
import shlex
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, cast, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from . import calendar_packet, calendar_protocol

AGENT_NAME = "seatlab-calendar-feedback"
AGENT_VERSION = "1.0.0"
DECLARED_MODEL = "deepseek/deepseek-flash"
SEAT_SCHEMA = "seatlab.calendar-seat/v1"
CONTROL_SCHEMA = "seatlab.calendar-control/v1"
TURN_FILE = "calendar-turn.json"
CONTROL_FILE = "calendar-control.json"
INVALID_WIRE = '{"model_invalid":true}'
_INBOX_TIMEOUT_SEC = 120
_POLL_INTERVAL_SEC = 1
_EXEC_TIMEOUT_SEC = 8
_SEATS = ("p1", "p2")

# Requests use only this seat's ordinary credential. The briefing branch keeps
# the original attendee identifiers and raw environment text inside the seat.
_SEAT_CODE = r"""
import http.client
import json
import os
import sys
from contextlib import closing


def unique(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError
    return value


def table():
    raw = os.environ['SEAT_BRIEFING']
    if len(raw) > 16384:
        raise ValueError
    briefing = json.loads(raw, object_pairs_hook=unique)
    people = briefing['calendars']
    days = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri')
    if (
        briefing.keys() != {'calendars'}
        or not isinstance(people, dict)
        or len(people) != 3
        or any(not name.strip() for name in people)
    ):
        raise ValueError
    result = []
    for _, calendar in sorted(people.items()):
        if not isinstance(calendar, dict) or calendar.keys() != set(days):
            raise ValueError
        row = []
        for day in days:
            busy = calendar[day]
            if (not isinstance(busy, list) or len(busy) > 8
                or any(type(hour) is not int or not 9 <= hour <= 16 for hour in busy)
                or len(set(busy)) != len(busy)):
                raise ValueError
            row.append(sum(1 << (hour - 9) for hour in busy))
        result.append(row)
    return result


def request(op):
    if os.environ['WORLD_URL'] != 'http://world:8080':
        raise ValueError
    token = os.environ['SEAT_TOKEN']
    if not token:
        raise ValueError
    method, path = {
        'whoami': ('GET', '/whoami'),
        'inbox': ('GET', '/messages'),
        'send': ('POST', '/messages'),
        'submit': ('POST', '/submit'),
    }[op]
    headers = {'Authorization': 'Bearer ' + token}
    body = None
    if method == 'POST':
        headers['Content-Type'] = 'application/json'
        body = os.environ['SEATLAB_CALENDAR_BODY'].encode()
    with closing(http.client.HTTPConnection('world', 8080, timeout=3)) as conn:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read(8193)
        if len(raw) > 8192:
            raise ValueError
        return [response.status, json.loads(raw, object_pairs_hook=unique)]


try:
    op = os.environ['SEATLAB_CALENDAR_OP']
    value = table() if op == 'table' else request(op)
    print(json.dumps(value, allow_nan=False, separators=(',', ':')))
except Exception:
    sys.exit(1)
"""
_SEAT_COMMAND = "python3 -I -c " + shlex.quote(_SEAT_CODE)


class _SeatError(RuntimeError):
    """A fixed stage that does not expose the private briefing or credential."""


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _write_private(path: Path, value: Mapping[str, object]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8") as file:
        file.write(_json(value) + "\n")


async def _exec(
    environment: SeatEnvironment, op: str, body: dict[str, Any] | None = None
) -> Any:
    env = {"SEATLAB_CALENDAR_OP": op}
    if body is not None:
        env["SEATLAB_CALENDAR_BODY"] = _json(body)
    try:
        result = await environment.exec(
            _SEAT_COMMAND, env=env, timeout_sec=_EXEC_TIMEOUT_SEC
        )
    except TimeoutError:
        raise _SeatError("invalid_transport") from None
    if result.return_code != 0 or result.stdout is None or len(result.stdout) > 8192:
        raise _SeatError("invalid_transport")
    return json.loads(result.stdout)


async def _request(
    environment: SeatEnvironment,
    op: str,
    status: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = await _exec(environment, op, body)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or type(value[0]) is not int
        or value[0] != status
        or not isinstance(value[1], dict)
    ):
        raise _SeatError("invalid_transport")
    return value[1]


def _expected_table(world: int, seat: str) -> calendar_packet.Table:
    return calendar_packet.load(calendar_packet.PACKET, calendar_packet.PACKET_SHA256)[
        world
    ].tables[_SEATS.index(seat)]


async def _table(
    environment: SeatEnvironment, world: int, seat: str
) -> calendar_packet.Table:
    identity = await _request(environment, "whoami", 200)
    limits = identity.get("limits")
    if (
        identity.keys() != {"seat", "seats", "messaging", "limits"}
        or identity["seat"] != seat
        or identity["seats"] != list(_SEATS)
        or identity["messaging"] is not True
        or not isinstance(limits, dict)
        or limits.keys() != {"max_text", "max_messages"}
        or any(type(value) is not int or value <= 0 for value in limits.values())
    ):
        raise _SeatError("invalid_transport")
    raw = await _exec(environment, "table")
    if not isinstance(raw, list) or len(raw) != 3:
        raise _SeatError("invalid_transport")
    for row in raw:
        if (
            not isinstance(row, list)
            or len(row) != 5
            or any(type(mask) is not int or not 0 <= mask <= 255 for mask in row)
        ):
            raise _SeatError("invalid_transport")
    table = tuple(tuple(row) for row in raw)
    if table != _expected_table(world, seat):
        raise _SeatError("invalid_transport")
    return cast(calendar_packet.Table, table)


def _message(value: object, mid: str, sender: str, receiver: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.keys() != {"id", "from", "to", "text", "t"}
        or (value["id"], value["from"], value["to"]) != (mid, sender, receiver)
        or not isinstance(value["text"], str)
        or type(value["t"]) not in (int, float)
        or not 0 <= value["t"] < float("inf")
    ):
        raise _SeatError("invalid_transport")
    return value


async def _send(
    environment: SeatEnvironment, mid: str, to: str, wire: str
) -> dict[str, Any]:
    receipt = _message(
        await _request(environment, "send", 201, {"to": to, "text": wire}),
        mid,
        environment.service,
        to,
    )
    if receipt["text"] != wire:
        raise _SeatError("invalid_transport")
    return receipt


async def _receive(
    environment: SeatEnvironment, peer: str, sent: dict[str, Any] | None = None
) -> dict[str, Any]:
    offset = int(sent is not None)
    try:
        async with asyncio.timeout(_INBOX_TIMEOUT_SEC):
            while True:
                reply = await _request(environment, "inbox", 200)
                inbox = reply.get("messages")
                if (
                    reply.keys() != {"messages"}
                    or not isinstance(inbox, list)
                    or not offset <= len(inbox) <= offset + 1
                    or (sent is not None and inbox[0] != sent)
                ):
                    raise _SeatError("invalid_transport")
                if len(inbox) > offset:
                    message = _message(
                        inbox[offset], f"m{offset + 1}", peer, environment.service
                    )
                    if sent is not None and message["t"] < sent["t"]:
                        raise _SeatError("invalid_transport")
                    return message
                await asyncio.sleep(_POLL_INTERVAL_SEC)
    except TimeoutError:
        raise _SeatError("inbox_timeout") from None


def _decode_wire(wire: str, *, ready: bool) -> tuple[int, ...] | None:
    if ready:
        if wire != calendar_protocol.feedback_wire(None):
            raise _SeatError("invalid_transport")
        return None
    value = calendar_protocol.decode_list(wire)
    if wire != calendar_protocol.feedback_wire(value):
        raise _SeatError("invalid_transport")
    return value


class CalendarSeat(BaseAgent):
    """One host actor, with only its own scoped Flotilla API and local view."""

    _declared_model: str | None = DECLARED_MODEL
    _run_prefix = "cf-"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        world: int,
        direction: int,
        repetition: int,
        arm: str,
        seat: str,
        own_starts: list[int],
        control: bool,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
    ) -> None:
        if (
            any(type(value) is not int for value in (world, direction, repetition))
            or not 0 <= world < 12
            or direction not in (0, 1)
            or repetition not in (0, 1)
            or arm not in ("blind", "peer")
            or seat not in _SEATS
            or type(control) is not bool
            or type(own_starts) is not list
            or (control and own_starts)
            or bool(extra_env)
        ):
            raise ValueError("Invalid calendar seat configuration")
        declared = None if control else self._declared_model
        if model_name not in (None, declared):
            raise ValueError("Invalid calendar model declaration")
        self._own = calendar_protocol.decode_list(_json({"starts": own_starts}))
        super().__init__(
            logs_dir=logs_dir,
            model_name=declared,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )
        self._block = calendar_protocol.Block(world, direction, repetition)
        self._run = f"{self._run_prefix}{self._block.id.removeprefix('cf-')}-{arm}"
        self._arm = arm
        self._seat = seat
        self._is_control = control
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        # Identifies the fixed host adapter, not a LiteLLM route.
        provider, name = model_name.split("/", 1)
        return provider, name

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

    async def _measure(
        self, prompt: str, role: str, context: AgentContext, metadata: dict[str, Any]
    ) -> tuple[int, ...] | int | None:
        from . import calendar_turns

        metadata["own_prompt_sha256"] = _hash(prompt)
        try:
            receipt = await asyncio.to_thread(
                calendar_turns.measure, prompt, self._run, role
            )
        except Exception:  # noqa: BLE001 - Do not expose provider exception text.
            raise _SeatError("model_failed") from None
        _write_private(self.logs_dir / TURN_FILE, receipt)
        context.n_input_tokens = receipt["input_tokens"]
        context.n_cache_tokens = receipt["cache_tokens"]
        context.n_output_tokens = receipt["output_tokens"]
        if receipt["status"] == "invalid" and isinstance(receipt["response_text"], str):
            metadata["stage"] = "model_invalid"
            return None
        if receipt["status"] != "accepted":
            raise _SeatError("model_failed")
        value = receipt["value"]
        if isinstance(value, list):
            return tuple(value)
        if type(value) is int:
            return value
        raise _SeatError("model_failed")

    def _control(
        self, own: tuple[int, ...], revised: tuple[int, ...] | None = None
    ) -> tuple[int, ...] | int:
        value: tuple[int, ...] | int = own
        if revised is not None:
            common = next((slot for slot in own if slot in revised), None)
            if common is None:
                raise _SeatError("invalid_transport")
            value = common
        _write_private(
            self.logs_dir / CONTROL_FILE,
            {
                "schema": CONTROL_SCHEMA,
                "run": self._run,
                "seat": self._seat,
                "value": value,
            },
        )
        return value

    async def _play(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        metadata: dict[str, Any],
    ) -> None:
        table = await _table(environment, self._block.world, self._seat)
        metadata["own_table_sha256"] = _hash(_json(table))
        own = calendar_packet.starts(table) if self._is_control else self._own
        sender, receiver = self._block.sender, self._block.receiver
        if self._seat == sender:
            incoming = await _receive(environment, receiver)
            metadata["incoming_id"] = incoming["id"]
            peer = _decode_wire(incoming["text"], ready=self._arm == "blind")
            if self._is_control:
                value = self._control(own)
            else:
                value = await self._measure(
                    calendar_protocol.recheck_prompt(table, own, peer),
                    "cal_recheck",
                    context,
                    metadata,
                )
            if metadata["stage"] == "model_invalid":
                wire = INVALID_WIRE
            elif isinstance(value, tuple):
                wire = calendar_protocol.feedback_wire(value)
            else:
                raise _SeatError("model_failed")
            outgoing = await _send(environment, "m2", receiver, wire)
            metadata["outgoing_id"] = outgoing["id"]
        else:
            wire = calendar_protocol.feedback_wire(own if self._arm == "peer" else None)
            outgoing = await _send(environment, "m1", sender, wire)
            metadata["outgoing_id"] = outgoing["id"]
            incoming = await _receive(environment, sender, sent=outgoing)
            metadata["incoming_id"] = incoming["id"]
            if not self._is_control and incoming["text"] == INVALID_WIRE:
                metadata["stage"] = "peer_model_invalid"
                return
            revised = _decode_wire(incoming["text"], ready=False)
            if revised is None:
                raise _SeatError("invalid_transport")
            if self._is_control:
                value = self._control(own, revised)
            else:
                value = await self._measure(
                    calendar_protocol.decision_prompt(table, own, revised),
                    "cal_decide",
                    context,
                    metadata,
                )
            if metadata["stage"] == "model_invalid":
                return
            if type(value) is not int:
                raise _SeatError("model_failed")
            reply = await _request(
                environment, "submit", 200, {"answer": calendar_protocol.answer(value)}
            )
            if reply.keys() != {"accepted"} or reply["accepted"] is not True:
                raise _SeatError("invalid_transport")
        if metadata["stage"] != "model_invalid":
            metadata["stage"] = "complete"

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        metadata: dict[str, Any] = {
            "schema": SEAT_SCHEMA,
            "run": self._run,
            "seat": self._seat,
            "control": self._is_control,
            "stage": "starting",
            "own_table_sha256": None,
            "own_prompt_sha256": None,
            "incoming_id": None,
            "outgoing_id": None,
        }
        context.metadata = {"calendar": metadata}
        try:
            if (
                self._started
                or not isinstance(environment, SeatEnvironment)
                or environment.service != self._seat
            ):
                raise _SeatError("invalid_transport")
            self._started = True
            await self._play(environment, context, metadata)
        except _SeatError as error:
            metadata["stage"] = str(error)
            raise
        except Exception:  # noqa: BLE001 - Do not expose raw seat or transport data.
            metadata["stage"] = "invalid_transport"
            raise _SeatError("invalid_transport") from None
