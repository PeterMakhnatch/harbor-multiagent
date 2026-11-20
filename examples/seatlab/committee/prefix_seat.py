"""Restore one stock committee prefix, then cast a fresh native vote per seat."""

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import ExitStack, asynccontextmanager
from contextvars import Context
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, NotRequired, TextIO, TypedDict, cast, override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..visibility import deepseek, spend
from . import contract, prefix_packet, prefix_schedule, recommendation, schedule
from .seat import (
    _ATTESTED_MODELS,
    _BARRIER_TIMEOUT_SEC,
    _MODEL,
    _SAFE_PROVIDER_FAILURES,
    _accumulate_usage,
    _canonical_json,
    _digest,
    _SeatFailure,
    _world,
    _WorldAction,
)

_SCHEMA = "seatlab.prefix-seat/v1"
_WORLD_AUDIT = "/logs/world/events.jsonl"
_CUT = {
    "mode": "hidden_profile",
    "report_counts": {},
    "decisions_submitted": [],
    "message_count": 9,
    "votes_submitted": 0,
    "done": [],
}
_ROLES = frozenset(schedule.ROLES)


class _PrefixAction(Enum):
    STATUS = ("GET", "/status", "200")


class _CutStatus(TypedDict):
    message_count: int
    votes_submitted: int
    done: list[str]


class _Turn(TypedDict):
    status: str
    requested_model: str
    served_model: str | None
    prompt_sha256: str | None
    response_sha256: str | None
    action_sha256: str | None


class _Metadata(TypedDict):
    schema: str
    role: str
    run_id: str
    arm: str
    map_index: int
    status: str
    prefix_sha256: str | None
    cut_status: _CutStatus | None
    read_msg_seqs: list[int] | None
    turn: _Turn
    failure: NotRequired[str]


@dataclass(slots=True)
class _Receipt:
    metadata: _Metadata
    action: dict[str, Any] | None = None

    def write(self, stream: TextIO) -> None:
        try:
            with stream:
                stream.write(
                    _canonical_json({**self.metadata, "action": self.action}) + "\n"
                )
        except OSError:
            self.metadata["status"] = "failed"
            self.metadata["failure"] = "receipt_unavailable"
            raise _SeatFailure("receipt_unavailable") from None


def _exact(actual: dict[str, Any], expected: dict[str, Any] | None) -> bool:
    try:
        return expected is not None and _canonical_json(actual) == _canonical_json(
            expected
        )
    except (TypeError, ValueError, UnicodeError):
        return False


async def _verify_audit(
    project: BaseEnvironment, checkpoint: prefix_packet.Checkpoint
) -> None:
    # This is host-only evidence collection from the stock main service, not a
    # seat API call. The world and its audit files remain unchanged.
    try:
        with TemporaryDirectory(prefix="seatlab-prefix-") as directory:
            path = Path(directory) / "events.jsonl"
            await project.download_file(_WORLD_AUDIT, path)
            lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, RuntimeError, UnicodeError):
        raise _SeatFailure("prefix_audit_unavailable") from None
    try:
        for line, expected in zip(lines, checkpoint.events, strict=True):
            event = prefix_packet.normalize_event(
                json.loads(line, object_pairs_hook=contract._json_pairs)
            )
            if not _exact(event, expected):
                raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise _SeatFailure("prefix_audit_mismatch") from None


class _Trial:
    def __init__(
        self,
        project: BaseEnvironment,
        checkpoint: prefix_packet.Checkpoint,
        *,
        require_votes: bool,
    ) -> None:
        self.project = project
        self.checkpoint = checkpoint
        self.require_votes = require_votes
        self.roles: set[str] = set()
        self.restored: set[str] = set()
        self.bound: set[str] = set()
        self.active = 0
        self.step = 0
        self.cut: _CutStatus | None = None
        self.failed = False
        self.changed = asyncio.Event()
        self.pending: set[asyncio.Task[Any]] = set()

    def notify(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    def require_live(self) -> None:
        if self.failed:
            raise _SeatFailure("prefix_peer_failed")

    async def until(self, ready: Callable[[], bool]) -> None:
        while True:
            self.require_live()
            if ready():
                return
            await self.changed.wait()

    def abort(self) -> None:
        if not self.failed:
            self.failed = True
            for task in self.pending:
                task.cancel()
            self.notify()

    async def _request[T](self, operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
        self.require_live()
        # Do not stream private world responses to inherited Harbor subscribers.
        task = asyncio.create_task(operation(), context=Context())
        self.pending.add(task)
        try:
            result = await task
            self.require_live()
            return result
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if self.failed and caller is not None and not caller.cancelling():
                raise _SeatFailure("prefix_peer_failed") from None
            raise
        finally:
            self.pending.remove(task)

    async def exchange(
        self,
        environment: SeatEnvironment,
        action: _WorldAction | _PrefixAction,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Both enums supply the same fixed method/path/status tuple.
        return await self._request(
            lambda: _world(environment, cast(_WorldAction, action), body)
        )

    async def replay(self, role: str, environment: SeatEnvironment) -> dict[str, Any]:
        await self.until(lambda: self.roles == _ROLES)
        steps = self.checkpoint.steps
        dossier = None
        while self.step < len(steps):
            await self.until(
                lambda: self.step == len(steps) or steps[self.step].role == role
            )
            if self.step == len(steps):
                break
            step = steps[self.step]
            if step.kind == "dossier":
                dossier = await self.exchange(environment, _WorldAction.DOSSIER)
                try:
                    prefix_packet.check_dossier(role, dossier)
                except ValueError:
                    raise _SeatFailure("prefix_dossier_mismatch") from None
            elif step.kind == "read":
                reply = await self.exchange(environment, _WorldAction.READ_CHAT)
                if not _exact(reply, step.expected):
                    raise _SeatFailure("prefix_chat_read_mismatch")
            elif step.kind == "post":
                reply = await self.exchange(environment, _WorldAction.SHARE, step.body)
                if not _exact(reply, step.expected):
                    raise _SeatFailure("prefix_chat_post_mismatch")
            else:
                raise _SeatFailure("prefix_checkpoint_invalid")
            self.step += 1
            self.notify()
        self.require_live()
        if dossier is None:
            raise _SeatFailure("prefix_dossier_missing")
        return dossier

    async def restore(
        self, role: str, environment: SeatEnvironment
    ) -> tuple[dict[str, Any], _CutStatus]:
        dossier = await self.replay(role, environment)
        self.restored.add(role)
        self.notify()
        await self.until(lambda: self.restored == _ROLES)
        if role == self.checkpoint.chair:
            status = await self.exchange(environment, _PrefixAction.STATUS)
            if not _exact(status, _CUT):
                raise _SeatFailure("prefix_cut_status_mismatch")
            await self._request(lambda: _verify_audit(self.project, self.checkpoint))
            self.cut = {
                "message_count": status["message_count"],
                "votes_submitted": status["votes_submitted"],
                "done": status["done"],
            }
            self.notify()
        await self.until(lambda: self.cut is not None)
        if self.cut is None:
            raise _SeatFailure("prefix_cut_status_mismatch")
        return dossier, {**self.cut, "done": list(self.cut["done"])}


_ACTIVE: dict[str, _Trial] = {}
# These registered identities do not retry. Keep identities, not environments,
# packets, or waiters, after success, cancellation, or failure.
_STARTED: set[str] = set()


def _join(
    project: BaseEnvironment,
    run_id: str,
    map_index: int,
    role: str,
    *,
    cut: prefix_packet.Checkpoint | None = None,
) -> _Trial:
    trial = _ACTIVE.get(run_id)
    if trial is None:
        if run_id in _STARTED:
            raise _SeatFailure("duplicate_cell")
        if any(active.project is project for active in _ACTIVE.values()):
            raise _SeatFailure("backend_cell_conflict")
        _STARTED.add(run_id)
        checkpoint = cut
        if checkpoint is None:
            try:
                checkpoint = prefix_packet.load(
                    prefix_schedule.PACKET, prefix_schedule.PACKET_SHA256
                )[map_index]
            except (KeyError, OSError, ValueError, RecursionError):
                raise _SeatFailure("prefix_checkpoint_invalid") from None
        if checkpoint is None:
            raise _SeatFailure("prefix_checkpoint_ineligible")
        if checkpoint.map_index != map_index or checkpoint.chair != schedule.chair(
            map_index, 0
        ):
            raise _SeatFailure("prefix_checkpoint_invalid")
        trial = _ACTIVE[run_id] = _Trial(project, checkpoint, require_votes=cut is None)
    elif trial.project is not project:
        raise _SeatFailure("duplicate_cell")
    if (cut is not None and trial.checkpoint != cut) or trial.require_votes != (
        cut is None
    ):
        raise _SeatFailure("prefix_checkpoint_conflict")
    if role in trial.roles:
        raise _SeatFailure("duplicate_seat")
    trial.roles.add(role)
    trial.active += 1
    trial.notify()
    return trial


def _leave(run_id: str, trial: _Trial) -> None:
    # Once all views are bound, each seat owns its vote even if another fails.
    if trial.cut is None or (trial.require_votes and trial.bound != _ROLES):
        trial.abort()
    trial.active -= 1
    if not trial.active:
        del _ACTIVE[run_id]


def _check_environment(environment: BaseEnvironment, role: str) -> SeatEnvironment:
    if not isinstance(environment, SeatEnvironment) or environment.service != role:
        raise _SeatFailure("wrong_environment")
    if "DEEPSEEK_API_KEY" in os.environ:
        raise _SeatFailure("unbound_host_key")
    return environment


@asynccontextmanager
async def restored(
    environment: SeatEnvironment, role: str, run_id: str, cut: prefix_packet.Checkpoint
) -> AsyncGenerator[tuple[dict[str, Any], _CutStatus]]:
    """Verify a stock prefix without a fresh chat read, model call, or vote."""
    map_index = cut.map_index
    if (
        not isinstance(role, str)
        or role not in _ROLES
        or type(map_index) is not int
        or not 0 <= map_index < 6
    ):
        raise _SeatFailure("prefix_checkpoint_invalid")
    if run_id != f"cut-check-m{map_index}" and not any(
        (run_id, map_index) == (cell["cell_id"], cell["map_index"])
        for cell in prefix_schedule.grid()
    ):
        raise _SeatFailure("prefix_checkpoint_invalid")
    environment = _check_environment(environment, role)
    trial = _join(environment.project, run_id, map_index, role, cut=cut)
    try:
        try:
            async with asyncio.timeout(_BARRIER_TIMEOUT_SEC):
                dossier, cut_status = await trial.restore(role, environment)
        except TimeoutError:
            raise _SeatFailure("prefix_barrier_timeout") from None
        yield dossier, cut_status
    finally:
        _leave(run_id, trial)


class PrefixSeat(BaseAgent):
    """A stock Harbor agent with one paid vote after exact prefix restoration."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        run_id: str,
        arm: str,
        map_index: int,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        if model_name not in (None, _MODEL) or bool(extra_env):
            raise ValueError("Prefix seat does not accept overrides")
        if (
            role not in schedule.ROLES
            or type(map_index) is not int
            or not any(
                (run_id, arm, map_index)
                == (cell["cell_id"], cell["arm"], cell["map_index"])
                for cell in prefix_schedule.grid()
            )
        ):
            raise ValueError("Invalid prefix seat configuration")
        super().__init__(logs_dir=logs_dir, model_name=_MODEL, logger=logger)
        self._role = role
        self._run_id = run_id
        self._arm = arm
        self._map_index = map_index
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-prefix-voter"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def _prepare_vote(
        self, trial: _Trial, environment: SeatEnvironment, metadata: _Metadata
    ) -> tuple[str, dict[str, Any], recommendation.Board]:
        checkpoint = trial.checkpoint
        role = self._role
        dossier, metadata["cut_status"] = await trial.restore(role, environment)
        chat = await trial.exchange(environment, _WorldAction.READ_CHAT)
        if not _exact(chat, checkpoint.board):
            raise _SeatFailure("prefix_final_board_mismatch")
        try:
            board = recommendation.board(chat, self._map_index, checkpoint.chair)
            prompt = recommendation.turn_prompt(
                "vote",
                dossier,
                board,
                self._map_index,
                role,
                checkpoint.chair,
                self._arm,
            )
        except ValueError:
            raise _SeatFailure("invalid_view") from None
        metadata["read_msg_seqs"] = [message["seq"] for message in chat["messages"]]
        metadata["turn"]["prompt_sha256"] = _digest(prompt)
        trial.bound.add(role)
        trial.notify()
        await trial.until(lambda: trial.bound == _ROLES)
        return prompt, dossier, board

    async def _vote_action(
        self,
        prompt: str,
        dossier: dict[str, Any],
        board: recommendation.Board,
        context: AgentContext,
        turn: _Turn,
    ) -> dict[str, Any]:
        turn["status"] = "requesting"
        try:
            result = await asyncio.to_thread(
                deepseek.turn, prompt, run_id=self._run_id, role=self._role
            )
        except deepseek.SeatUnavailable as exc:
            turn["status"] = "unavailable"
            if exc.settled_usage is not None and not _accumulate_usage(
                context, exc.settled_usage
            ):
                raise _SeatFailure("usage_unverified") from None
            category = exc.category
            if category not in _SAFE_PROVIDER_FAILURES:
                category = "model_unavailable"
            raise _SeatFailure(category) from None
        except (OSError, sqlite3.Error, spend.SpendRefused):
            turn["status"] = "unavailable"
            raise _SeatFailure("budget_guard") from None
        turn["status"] = "rejected"
        if not _accumulate_usage(
            context, (result.input_tokens, result.cache_tokens, result.output_tokens)
        ):
            raise _SeatFailure("usage_unverified")
        served = result.served_model
        if not isinstance(served, str) or served.casefold() not in _ATTESTED_MODELS:
            raise _SeatFailure("served_model_unverified")
        turn["served_model"] = served
        try:
            turn["response_sha256"] = _digest(result.text)
        except (AttributeError, UnicodeError):
            raise _SeatFailure("provider_response_invalid") from None
        try:
            action = contract.vote_action(
                result.text, dossier, board.facts, self._map_index
            )
            turn["action_sha256"] = _digest(_canonical_json(action))
        except ValueError:
            raise _SeatFailure("invalid_model_action") from None
        return action

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise _SeatFailure("seat_already_started")
        self._started = True
        turn: _Turn = {
            "status": "not_started",
            "requested_model": deepseek.MODEL,
            "served_model": None,
            "prompt_sha256": None,
            "response_sha256": None,
            "action_sha256": None,
        }
        metadata: _Metadata = {
            "schema": _SCHEMA,
            "role": self._role,
            "run_id": self._run_id,
            "arm": self._arm,
            "map_index": self._map_index,
            "status": "running",
            "prefix_sha256": None,
            "cut_status": None,
            "read_msg_seqs": None,
            "turn": turn,
        }
        context.metadata = cast(dict[str, Any], metadata)
        context.n_input_tokens = context.n_cache_tokens = context.n_output_tokens = None
        record = _Receipt(metadata)
        with ExitStack() as cleanup:
            try:
                environment = _check_environment(environment, self._role)
                trial = _join(
                    environment.project, self._run_id, self._map_index, self._role
                )
                cleanup.callback(_leave, self._run_id, trial)
                metadata["prefix_sha256"] = trial.checkpoint.prefix_sha256
                try:
                    fd = os.open(
                        self.logs_dir / "prefix-vote.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                    )
                    stream = os.fdopen(fd, "w", encoding="utf-8")
                except OSError:
                    raise _SeatFailure("receipt_unavailable") from None
                cleanup.callback(record.write, stream)
                try:
                    async with asyncio.timeout(_BARRIER_TIMEOUT_SEC):
                        prompt, dossier, board = await self._prepare_vote(
                            trial, environment, metadata
                        )
                except TimeoutError:
                    raise _SeatFailure("prefix_barrier_timeout") from None
                vote = await self._vote_action(prompt, dossier, board, context, turn)
                turn["status"] = "submitting"
                try:
                    ack = await trial.exchange(environment, _WorldAction.VOTE, vote)
                    if not _exact(ack, {"seat": self._role, **vote}):
                        raise _SeatFailure("vote_unacknowledged")
                except (_SeatFailure, asyncio.CancelledError):
                    turn["status"] = "unacknowledged"
                    raise
                record.action = vote
                turn["status"] = "submitted"
                done = await trial.exchange(environment, _WorldAction.DONE, {})
                if not _exact(done, {"seat": self._role, "ok": True}):
                    raise _SeatFailure("done_unacknowledged")
                metadata["status"] = "complete"
            except _SeatFailure as exc:
                metadata["status"] = "failed"
                metadata["failure"] = exc.category
                raise
            except asyncio.CancelledError:
                metadata["status"] = "cancelled"
                metadata["failure"] = "cancelled"
                raise
            # Harbor persists exceptions. Redact unexpected world and provider data.
            except Exception:  # noqa: BLE001
                metadata["status"] = "failed"
                metadata["failure"] = "internal_error"
                raise _SeatFailure("internal_error") from None
