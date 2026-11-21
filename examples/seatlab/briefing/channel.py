import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, overload
from weakref import WeakKeyDictionary

WAIT_TIMEOUT_SECONDS = 120.0
_MAX_CLAIMS_PER_LOOP = 1024
_CHANNEL_ID = re.compile(r"seatlab-team-[0-9a-f]{24}")
_BRIEF = re.compile(r"[A-D]{6}")
_ALIASES = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6")
_ORDER = {
    "inventory": (("receive", "brief"), ("send", "push")),
    "dispatch": (("send", "brief"), ("receive", "push")),
}


@dataclass(slots=True)
class _Exchange:
    brief: str | None = None
    push: tuple[str, str] | None = None
    brief_ready: asyncio.Event = field(default_factory=asyncio.Event)
    push_ready: asyncio.Event = field(default_factory=asyncio.Event)
    joined: set[str] = field(default_factory=set)
    completed: int = 0
    aborted: bool = False

    def abort(self) -> None:
        self.aborted = True
        self.brief_ready.set()
        self.push_ready.set()


@dataclass(slots=True)
class _LoopChannels:
    active: dict[str, _Exchange] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)

    def join(self, channel_id: str, role: str) -> _Exchange:
        exchange = self.active.get(channel_id)
        if exchange is None:
            if channel_id in self.claimed:
                raise RuntimeError("Relay exchange already closed")
            if len(self.claimed) >= _MAX_CLAIMS_PER_LOOP:
                raise RuntimeError("Relay trial limit reached for this event loop")
            exchange = _Exchange()
            self.active[channel_id] = exchange
            self.claimed.add(channel_id)
        if exchange.aborted:
            raise RuntimeError("Relay exchange aborted")
        if role in exchange.joined:
            raise RuntimeError("Relay role already joined")
        exchange.joined.add(role)
        return exchange


_loops: WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopChannels] = (
    WeakKeyDictionary()
)


class _Port:
    def __init__(self, exchange: _Exchange, role: str) -> None:
        self._exchange = exchange
        self._order = _ORDER[role]
        self._step = 0
        self._waiting = False
        self._closed = False

    def _check(self, operation: str, slot: str) -> None:
        if self._closed:
            raise RuntimeError("Relay port closed")
        if self._exchange.aborted:
            raise RuntimeError("Relay exchange aborted")
        if (
            type(slot) is not str
            or self._waiting
            or self._step == len(self._order)
            or self._order[self._step] != (operation, slot)
        ):
            raise RuntimeError("Relay operation violates role sequence")

    async def send(self, slot: str, value: str | tuple[str, str] | None) -> None:
        self._check("send", slot)
        exchange = self._exchange
        if slot == "brief":
            if value is not None and (
                type(value) is not str or _BRIEF.fullmatch(value) is None
            ):
                raise ValueError("Relay brief must be six A-D letters or None")
            exchange.brief = value
            exchange.brief_ready.set()
        else:
            if (
                type(value) is not tuple
                or len(value) != 2
                or any(
                    type(alias) is not str or alias not in _ALIASES for alias in value
                )
                or value[0] == value[1]
            ):
                raise ValueError("Relay push must be two distinct shipment aliases")
            exchange.push = value
            exchange.push_ready.set()
        self._step += 1

    @overload
    async def receive(self, slot: Literal["brief"]) -> str | None: ...

    @overload
    async def receive(self, slot: Literal["push"]) -> tuple[str, str]: ...

    async def receive(self, slot: str) -> str | tuple[str, str] | None:
        self._check("receive", slot)
        exchange = self._exchange
        ready = exchange.brief_ready if slot == "brief" else exchange.push_ready
        self._waiting = True
        try:
            async with asyncio.timeout(WAIT_TIMEOUT_SECONDS):
                await ready.wait()
        except (TimeoutError, asyncio.CancelledError):
            exchange.abort()
            raise
        finally:
            self._waiting = False
        if exchange.aborted:
            raise RuntimeError("Relay exchange aborted")
        self._step += 1
        if slot == "brief":
            return exchange.brief
        assert exchange.push is not None
        return exchange.push


@asynccontextmanager
async def open_port(channel_id: str, role: str) -> AsyncIterator[_Port]:
    if type(channel_id) is not str or _CHANNEL_ID.fullmatch(channel_id) is None:
        raise ValueError("Invalid relay channel ID")
    if type(role) is not str or role not in _ORDER:
        raise ValueError("Invalid relay role")
    loop = asyncio.get_running_loop()
    channels = _loops.get(loop)
    if channels is None:
        channels = _LoopChannels()
        _loops[loop] = channels
    exchange = channels.join(channel_id, role)
    port = _Port(exchange, role)
    try:
        yield port
    except BaseException:
        exchange.abort()
        raise
    finally:
        port._closed = True
        if port._step != len(port._order):
            exchange.abort()
        if not exchange.aborted:
            exchange.completed += 1
        if exchange.aborted or exchange.completed == len(_ORDER):
            channels.active.pop(channel_id, None)
