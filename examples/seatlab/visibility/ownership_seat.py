"""Play a sealed box ownership turn through the stock Harbor seat interfaces."""

import asyncio
import json
import logging
import shlex
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from . import boxes_choice, intent_packet, intent_protocol, ownership_protocol
from .ownership_source import Cut, prefix_sha256

_SEATS = ("p1", "p2", "p3")
_BUDGET = 9
_MESSAGE_COUNT = {1: 9, 2: 7, 6: 9}
_JOIN_TIMEOUT = 30
_EXEC_TIMEOUT = 8
_TEXT_LIMIT = 64 * 1024
_SCHEMA = "seatlab.box-ownership-seat/v1"

# Only the requested seat's normal credential enters HTTP. Neither the reply nor
# the credential reaches Harbor's stdout/stderr logs, even when streaming is on.
_SEAT_CODE = r"""
import hashlib
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


def exchange():
    op = os.environ['SEATLAB_BOX_OP']
    method, path = {
        'rules': ('GET', '/rules'), 'mine': ('GET', '/mine'),
        'msgs': ('GET', '/msgs'), 'msg': ('POST', '/msg'),
        'open': ('POST', '/open'), 'commit': ('POST', '/open'),
    }[op]
    token = os.environ['SEAT_TOKEN']
    if os.environ['WORLD'] != 'http://world:8080' or not token:
        raise ValueError
    headers = {'Authorization': 'Bearer ' + token}
    body = None
    if method == 'POST':
        headers['Content-Type'] = 'application/json'
        body = os.environ['SEATLAB_BOX_BODY'].encode()
        if len(body) > 32768:
            raise ValueError
    with closing(http.client.HTTPConnection('world', 8080, timeout=3)) as conn:
        conn.request(method, path, body=body, headers=headers)
        reply = conn.getresponse()
        if reply.status != 200:
            return False
        raw = reply.read(262145)
        if len(raw) > 262144:
            return False
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        return False
    if op == 'commit' and not isinstance(value.pop('flag', None), bool):
        return False
    canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest() == os.environ['SEATLAB_BOX_EXPECTED']


try:
    print('ok' if exchange() else 'mismatch')
except Exception:
    sys.exit(1)
"""
_SEAT_COMMAND = "python3 -I -c " + shlex.quote(_SEAT_CODE)


class _SeatError(RuntimeError):
    """A fixed failure category safe for the stock trial's exception record."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _Request:
    seat: str
    op: str
    expected: str
    body: str | None

    @classmethod
    def make(
        cls, seat: str, op: str, expected: object, body: object = None
    ) -> "_Request":
        return cls(
            seat, op, _hash(_json(expected)), None if body is None else _json(body)
        )

    async def verify(self, environment: SeatEnvironment) -> None:
        env = {"SEATLAB_BOX_OP": self.op, "SEATLAB_BOX_EXPECTED": self.expected}
        if self.body is not None:
            env["SEATLAB_BOX_BODY"] = self.body
        try:
            result = await environment.exec(
                _SEAT_COMMAND, env=env, timeout_sec=_EXEC_TIMEOUT
            )
        except Exception:  # noqa: BLE001
            raise _SeatError("seat_transport_failed") from None
        if result.return_code == 0 and result.stdout == "ok\n":
            return
        if self.op == "commit":
            raise _SeatError("open_unacknowledged")
        raise _SeatError("live_source_mismatch")


def _rules(seat: str, left: int) -> dict[str, object]:
    return {
        "you": seat,
        "seats": _SEATS,
        "channel": True,
        "budget_left": {"open": left},
    }


def _requests(cut: Cut, world: intent_packet.World) -> tuple[_Request, ...]:
    """Bind only the accepted historical mutations and their exact stock views."""
    count = _MESSAGE_COUNT[world.instance]
    if (
        len(cut.actions) != count + sum(len(case.own) for case in world.seats)
        or prefix_sha256(cut.actions) != cut.prefix_sha256
    ):
        raise _SeatError("source_mismatch")
    requests = [_Request.make(seat, "rules", _rules(seat, _BUDGET)) for seat in _SEATS]
    opened: dict[str, list[dict[str, int]]] = {seat: [] for seat in _SEATS}
    messages: list[dict[str, object]] = []
    flags: set[int] = set()
    for action in cut.actions:
        seat = action.seat
        if seat not in _SEATS:
            raise _SeatError("source_mismatch")
        expected: dict[str, object]
        if action.kind == "open":
            history = world.seats[_SEATS.index(seat)].own
            index = len(opened[seat])
            if index >= len(history):
                raise _SeatError("source_mismatch")
            box, room, flag = history[index]
            body = {"box": box}
            mine = {**body, "room": room, "flag": flag}
            expected = {**mine, "budget_left": _BUDGET - index - 1}
            opened[seat].append(mine)
            if flag:
                flags.add(box)
        elif action.kind == "msg":
            text = action.request.get("text")
            if type(text) is not str or not text.strip() or len(text) > 2000:
                raise _SeatError("source_mismatch")
            body = {"text": text}
            expected = {"from": seat, "id": len(messages) + 1, **body}
            messages.append(expected)
        else:
            raise _SeatError("source_mismatch")
        if _json(body) != _json(action.request) or _json(expected) != _json(
            action.response
        ):
            raise _SeatError("source_mismatch")
        requests.append(_Request.make(seat, action.kind, expected, body))
    if (
        len(messages) != count
        or type(cut.baseline_flags) is not int
        or len(flags) != cut.baseline_flags
    ):
        raise _SeatError("source_mismatch")
    inbox_sha256 = _hash(_json({"messages": messages}))
    for seat, case in zip(_SEATS, world.seats, strict=True):
        mine = opened[seat]
        peers = {
            action["box"]
            for name, actions in opened.items()
            if name != seat
            for action in actions
        }
        if (
            len(mine) != len(case.own)
            or case.budget_left != _BUDGET - len(mine)
            or tuple(sorted(peers)) != case.peers
        ):
            raise _SeatError("source_mismatch")
        requests.extend(
            (
                _Request.make(seat, "mine", {"opens": mine}),
                _Request.make(seat, "rules", _rules(seat, case.budget_left)),
                _Request(seat, "msgs", inbox_sha256, None),
            )
        )
    return tuple(requests)


@dataclass(frozen=True, slots=True)
class _Decision:
    prompt_sha256: str
    response_sha256: str
    original_box: int | None


class _Trial:
    def __init__(
        self,
        cut: Cut,
        block: intent_protocol.Block,
        arm: str,
        proposals: intent_protocol.Choices,
        respond: Callable[[str, str], str],
    ) -> None:
        if (
            any(
                type(coordinate) is not int
                for coordinate in (
                    block.instance,
                    block.numbering,
                    block.rotation,
                    block.repetition,
                )
            )
            or block not in intent_protocol.blocks()
            or type(cut.instance) is not int
            or cut.instance != block.instance
            or not callable(respond)
        ):
            raise _SeatError("invalid_trial_binding")
        self.run_id = ownership_protocol.run_id(block, arm)
        self.environment_dir = (cut.task / "environment").resolve()
        self.prefix_sha256 = cut.prefix_sha256
        self.numbering = block.numbering
        world = intent_packet.load()[block.instance]
        self.requests = _requests(cut, world)
        self.labels = tuple(
            _SEATS[block.seat_index(label)] for label in range(len(_SEATS))
        )
        self.prompts = tuple(
            ownership_protocol.prompt(world, block, label, arm, proposals)
            for label in range(len(_SEATS))
        )
        self.budget_left = {
            seat: case.budget_left
            for seat, case in zip(_SEATS, world.seats, strict=True)
        }
        self.respond = respond
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.project: BaseEnvironment | None = None
        self.changed = asyncio.Event()
        self.position = 0
        self.end = len(self.requests) + 2 * len(_SEATS)
        self.decisions: dict[str, _Decision] = {}
        self.failure: str | None = None

    def notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    def advance(self) -> None:
        self.position += 1
        self.notify()

    def abort(self, category: str) -> None:
        if self.failure is not None:
            return
        self.failure = category
        self.notify()
        for task in self.tasks.values():
            if not task.done() and task is not asyncio.current_task():
                task.cancel()

    def join(self, seat: str, environment: BaseEnvironment) -> SeatEnvironment:
        if self.failure is not None:
            raise _SeatError(self.failure)
        if seat in self.tasks:
            raise _SeatError("duplicate_seat")
        if not isinstance(environment, SeatEnvironment) or environment.service != seat:
            raise _SeatError("wrong_seat_environment")
        project = environment.project
        if (
            project.trial_paths.trial_dir.name != self.run_id
            or project.environment_dir.resolve() != self.environment_dir
            or (self.project is not None and self.project is not project)
        ):
            raise _SeatError("wrong_trial_environment")
        task = asyncio.current_task()
        if task is None:
            raise _SeatError("invalid_trial_binding")
        self.project = project
        self.tasks[seat] = task
        self.notify()
        return environment

    async def wait(self, position: int | None = None) -> None:
        while self.failure is None:
            if len(self.tasks) == len(_SEATS) and (
                position is None or position == self.position
            ):
                return
            await self.changed.wait()
        raise _SeatError(self.failure)

    async def turn(self, seat: str, label: int) -> None:
        prompt = self.prompts[label]
        try:
            # The callback sees its two arguments, not the ambient trial binding.
            token = _ACTIVE.set(None)
            try:
                text = await asyncio.to_thread(self.respond, seat, prompt)
            finally:
                _ACTIVE.reset(token)
            if type(text) is not str or len(text) > _TEXT_LIMIT:
                raise ValueError
            raw = text.encode()
            if len(raw) > _TEXT_LIMIT:
                raise ValueError
            response_hash = sha256(raw).hexdigest()
        except Exception:  # noqa: BLE001
            raise _SeatError("model_transport_failed") from None
        try:
            box = boxes_choice.decode(text, self.numbering)
        except (ValueError, RecursionError):
            box = None
        self.decisions[seat] = _Decision(_hash(prompt), response_hash, box)

    async def play(
        self, seat: str, environment: SeatEnvironment, context: AgentContext
    ) -> None:
        try:
            async with asyncio.timeout(_JOIN_TIMEOUT):
                await self.wait()
        except TimeoutError:
            raise _SeatError("seat_join_timeout") from None
        for position, request in enumerate(self.requests):
            if request.seat == seat:
                await self.wait(position)
                await request.verify(environment)
                self.advance()

        # Prompts are sealed at bind, before any final model response exists.
        label = self.labels.index(seat)
        await self.wait(len(self.requests) + label)
        await self.turn(seat, label)
        self.advance()
        await self.wait(len(self.requests) + len(_SEATS) + _SEATS.index(seat))
        decision = self.decisions[seat]
        box = decision.original_box
        if box is None:
            status = "model_invalid"
        elif any(row.original_box is None for row in self.decisions.values()):
            status = "peer_invalid"
        else:
            body = {"box": box}
            expected = {
                **body,
                "room": (box - 1) // 10 + 1,
                "budget_left": self.budget_left[seat] - 1,
            }
            await _Request.make(seat, "commit", expected, body).verify(environment)
            status = "opened"
        self.advance()
        await self.wait(self.end)
        context.metadata = {
            "box_ownership": {
                "schema": _SCHEMA,
                "seat": seat,
                "run": self.run_id,
                "prefix_sha256": self.prefix_sha256,
                "status": status,
                "prompt_sha256": decision.prompt_sha256,
                "response_sha256": decision.response_sha256,
                "original_box": box,
            }
        }


_ACTIVE: ContextVar[_Trial | None] = ContextVar("ownership_trial", default=None)


@contextmanager
def bind(
    cut: Cut,
    block: intent_protocol.Block,
    arm: str,
    proposals: intent_protocol.Choices,
    respond: Callable[[str, str], str],
) -> Generator[None, None, None]:
    """Bind an authorized local callback and source to exactly one native trial."""
    if _ACTIVE.get() is not None:
        raise _SeatError("trial_already_bound")
    try:
        trial = _Trial(cut, block, arm, proposals, respond)
    except _SeatError:
        raise
    except Exception:  # noqa: BLE001
        raise _SeatError("invalid_trial_binding") from None
    token = _ACTIVE.set(trial)
    try:
        yield
    finally:
        try:
            if trial.position != trial.end:
                trial.abort("trial_binding_closed")
        finally:
            _ACTIVE.reset(token)


class OwnershipSeat(BaseAgent):
    """A native agent with one seat API and no model or credential configuration."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        seat: str,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        override_setup_timeout_sec: float | None = None,
    ) -> None:
        if (
            model_name is not None
            or type(seat) is not str
            or seat not in _SEATS
            or bool(extra_env)
        ):
            raise ValueError("invalid_ownership_seat_configuration")
        super().__init__(
            logs_dir=logs_dir,
            logger=logger,
            override_setup_timeout_sec=override_setup_timeout_sec,
        )
        self._seat = seat
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-box-ownership"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        """Use the stock seat image without installing an agent or client."""

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        trial = _ACTIVE.get()
        if trial is None:
            raise _SeatError("trial_unbound")
        try:
            if self._started:
                raise _SeatError("seat_already_started")
            self._started = True
            scoped = trial.join(self._seat, environment)
            await trial.play(self._seat, scoped, context)
        except asyncio.CancelledError:
            trial.abort("trial_cancelled")
            raise asyncio.CancelledError from None
        except _SeatError as error:
            trial.abort(str(error))
            raise
        except Exception:  # noqa: BLE001
            trial.abort("internal_error")
            raise _SeatError("internal_error") from None
