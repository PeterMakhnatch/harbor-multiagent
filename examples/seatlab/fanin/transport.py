import asyncio
import re
from typing import Any, cast

from harbor.environments.seat import SeatEnvironment

from ..visibility.calendar_seat import _message, _request, _SeatError
from . import protocol as p

MAX_MESSAGE_ID = 2 * (len(p.SEATS) - 1)
_MESSAGE_ID = re.compile(r"m[1-9][0-9]?")


def message_number(raw: object) -> int:
    mid = raw.get("id") if isinstance(raw, dict) else None
    if (
        not isinstance(mid, str)
        or _MESSAGE_ID.fullmatch(mid) is None
        or int(mid[1:]) > MAX_MESSAGE_ID
    ):
        raise _SeatError("invalid_transport")
    return int(mid[1:])


def _follows(message: dict[str, Any], prior: dict[str, Any] | None) -> None:
    if not message["t"] > 0:
        raise _SeatError("invalid_transport")
    if prior is not None and (
        message_number(message) <= message_number(prior) or message["t"] < prior["t"]
    ):
        raise _SeatError("invalid_transport")


async def send_fresh(
    environment: SeatEnvironment,
    to: str,
    text: str,
    *,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = await _request(environment, "send", 201, {"to": to, "text": text})
    message_number(raw)
    message = _message(raw, raw["id"], environment.service, to)
    if message["text"] != text:
        raise _SeatError("invalid_transport")
    _follows(message, after)
    return message


async def _inbox(
    environment: SeatEnvironment,
    log: list[dict[str, Any]],
    total: int,
    metadata: dict[str, Any],
) -> list[object]:
    reply = await _request(environment, "inbox", 200)
    metadata["polls"] += 1
    inbox = reply.get("messages")
    if (
        reply.keys() != {"messages"}
        or not isinstance(inbox, list)
        or not len(log) <= len(inbox) <= total
        or any(
            seen != prior or type(seen["t"]) is not type(prior["t"])
            for seen, prior in zip(inbox[: len(log)], log, strict=True)
        )
    ):
        raise _SeatError("invalid_transport")
    return inbox


def _accept(
    raw: object,
    log: list[dict[str, Any]],
    sent: list[dict[str, Any]],
    replies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    number = message_number(raw)
    value = cast(dict[str, Any], raw)
    booker = sent[0]["from"]
    ours = sum(1 for message in log if message["from"] == booker)
    sender = value.get("from")
    if sender == booker:
        if (
            ours == len(sent)
            or value != sent[ours]
            or type(value["t"]) is not type(sent[ours]["t"])
        ):
            raise _SeatError("invalid_transport")
        message = sent[ours]
    else:
        command = next((m for m in sent[:ours] if m["to"] == sender), None)
        if not isinstance(sender, str) or command is None or sender in replies:
            raise _SeatError("invalid_transport")
        message = _message(value, f"m{number}", sender, booker)
        _follows(message, command)
        replies[sender] = message
    _follows(message, log[-1] if log else None)
    return message


async def gather(
    environment: SeatEnvironment,
    sent: list[dict[str, Any]],
    *,
    wait_sec: int,
    poll_sec: int,
    metadata: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    asked = [message["to"] for message in sent]
    if (
        not sent
        or len(sent) > len(p.SEATS) - 1
        or len(set(asked)) != len(asked)
        or any(message["from"] != environment.service for message in sent)
        or environment.service in asked
        or wait_sec <= 0
        or poll_sec <= 0
    ):
        raise _SeatError("invalid_transport")
    total = 2 * len(sent)
    log: list[dict[str, Any]] = []
    replies: dict[str, dict[str, Any]] = {}
    try:
        async with asyncio.timeout(wait_sec):
            for _ in range(wait_sec // poll_sec + 1):
                inbox = await _inbox(environment, log, total, metadata)
                for raw in inbox[len(log) :]:
                    log.append(_accept(raw, log, sent, replies))
                if len(log) != len(sent) + len(replies):
                    raise _SeatError("invalid_transport")
                if len(replies) == len(sent):
                    return replies
                await asyncio.sleep(poll_sec)
    except TimeoutError:
        raise _SeatError("inbox_timeout") from None
    raise _SeatError("inbox_timeout")
