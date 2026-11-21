import asyncio
import json
import logging
import os
import shlex
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, override

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment, ServiceOperationsUnsupportedError
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..committee import contract, seat
from ..committee.schedule import ROLES
from ..visibility import deepseek
from . import protocol

MODEL = f"deepseek/{deepseek.MODEL}"
RECEIPT = "coalition-turns.json"
SCHEMA = "seatlab.coalition-seat/v1"
_PHASE_TIMEOUT_SEC = 300
_TURN_TIMEOUT_SEC = 100
_BODY = "SEATLAB_COALITION_BODY"
_HTTP = (
    "import os, sys\n"
    f"if sys.argv[1] == 'POST': sys.argv.append(os.environ[{_BODY!r}])\n"
    + seat._SEAT_HTTP
)
_COMMANDS = {
    action: shlex.join(["python", "-I", "-c", _HTTP, *action.value])
    for action in seat._WorldAction
}


class _SeatFailure(NonZeroAgentExitCodeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"Coalition seat: {category}")


async def _world(
    environment: SeatEnvironment,
    action: seat._WorldAction,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if {"DEEPSEEK_API_KEY", "SEATLAB_SPEND_DIR"}.intersection(os.environ):
        raise _SeatFailure("unbound_host_environment")
    if (action.value[0] == "POST") != (body is not None):
        raise _SeatFailure("invalid_request_body")
    try:
        env = None if body is None else {_BODY: seat._canonical_json(body)}
        result = await environment.exec(
            command=_COMMANDS[action], env=env, timeout_sec=6
        )
    except (OSError, TypeError, ValueError, ServiceOperationsUnsupportedError):
        raise _SeatFailure("world_request_failed") from None
    if result.return_code != 0:
        raise _SeatFailure("world_request_failed")
    try:
        data = json.loads(result.stdout or "")
    except (RecursionError, ValueError):
        raise _SeatFailure("world_request_failed") from None
    if not isinstance(data, dict):
        raise _SeatFailure("world_request_failed")
    return data


class Exchange:
    def __init__(self, cell_id: str) -> None:
        self.cell_id = cell_id
        self._closed = {role: asyncio.Event() for role in ROLES}

    def close(self, role: str) -> None:
        self._closed[role].set()

    async def wait(self, *roles: str) -> None:
        try:
            async with asyncio.timeout(_PHASE_TIMEOUT_SEC):
                for role in roles:
                    await self._closed[role].wait()
        except TimeoutError:
            raise _SeatFailure("exchange_deadline") from None


def _turn_record(run_id: str, role: str, phase: str) -> protocol.TurnReceipt:
    return {
        "status": "not_started",
        "ledger_run_id": f"{run_id}-{phase}",
        "ledger_role": role,
        "requested_model": deepseek.MODEL,
        "served_model": None,
        "prompt_sha256": None,
        "response_sha256": None,
        "action": None,
        "action_sha256": None,
        "event_seq": None,
        "input_tokens": None,
        "cache_tokens": None,
        "output_tokens": None,
    }


def _usage(
    context: AgentContext, record: protocol.TurnReceipt, tokens: tuple[int, int, int]
) -> None:
    if not seat._accumulate_usage(context, tokens):
        raise _SeatFailure("usage_unverified")
    record["input_tokens"], record["cache_tokens"], record["output_tokens"] = tokens


async def _turn(
    context: AgentContext,
    record: protocol.TurnReceipt,
    prompt: str,
    decode: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    record["prompt_sha256"] = seat._digest(prompt)
    record["status"] = "requesting"
    try:
        async with asyncio.timeout(_TURN_TIMEOUT_SEC):
            result = await asyncio.to_thread(
                deepseek.turn,
                prompt,
                run_id=record["ledger_run_id"],
                role=record["ledger_role"],
            )
    except deepseek.SeatUnavailable as exc:
        record["status"] = "unavailable"
        if exc.settled_usage is not None:
            _usage(context, record, exc.settled_usage)
        failure = (
            exc.category
            if exc.category in seat._SAFE_PROVIDER_FAILURES
            else "model_unavailable"
        )
        raise _SeatFailure(failure) from None
    except TimeoutError:
        record["status"] = "unavailable"
        raise _SeatFailure("model_turn_timeout") from None
    except (OSError, RecursionError, ValueError, sqlite3.Error):
        record["status"] = "unavailable"
        raise _SeatFailure("model_unavailable") from None
    record["status"] = "rejected"
    _usage(
        context,
        record,
        (result.input_tokens, result.cache_tokens, result.output_tokens),
    )
    if result.served_model.casefold() not in (
        deepseek.MODEL.casefold(),
        deepseek.REVISION.casefold(),
    ):
        raise _SeatFailure("served_model_unverified")
    record["served_model"] = result.served_model
    try:
        record["response_sha256"] = seat._digest(result.text)
        action = decode(result.text)
    except (ValueError, TypeError):
        raise _SeatFailure("invalid_model_action") from None
    record["action"] = action
    record["action_sha256"] = seat._digest(seat._canonical_json(action))
    record["status"] = "submitting"
    return action


class _Participant(BaseAgent):
    _KIND: Literal["live", "fixed_helper"]

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        run_id: str,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        cell = protocol.CELLS.get(run_id)
        if cell is None or role not in ROLES or extra_env or kwargs:
            raise ValueError("Invalid coalition participant binding")
        if role in protocol.HELPERS:
            position: Literal["helper", "proposer", "responder"] = "helper"
        else:
            position = "proposer" if role == cell["proposer"] else "responder"
        model = None if position == "helper" else MODEL
        if model_name != model or (position == "helper") != (
            self._KIND == "fixed_helper"
        ):
            raise ValueError("Invalid coalition roster")
        super().__init__(logs_dir=logs_dir, model_name=model, logger=logger)
        self._role = role
        self._cell = cell
        self._position = position
        self._started = False
        self._exchange: Exchange | None = None

    def bind_exchange(self, exchange: Exchange) -> None:
        if self._exchange is not None or exchange.cell_id != self._cell["cell_id"]:
            raise ValueError("Invalid coalition exchange binding")
        self._exchange = exchange

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    def _receipt(self) -> protocol.SeatReceipt:
        run_id = self._cell["cell_id"]
        return {
            "schema": SCHEMA,
            "run_id": run_id,
            "pair": self._cell["pair"],
            "arm": self._cell["arm"],
            "role": self._role,
            "kind": self._KIND,
            "position": self._position,
            "status": "running",
            "failure": None,
            "done": False,
            "turns": {
                phase: _turn_record(run_id, self._role, phase)
                for phase in (self._position, "ballot")
            }
            if self._KIND == "live"
            else {},
            "message": None,
            "snapshots": {},
            "ballot": None,
        }

    async def _chat(
        self, environment: SeatEnvironment, receipt: protocol.SeatReceipt, phase: str
    ) -> dict[str, dict[str, Any]]:
        chat = await _world(environment, seat._WorldAction.READ_CHAT)
        try:
            messages, acts = protocol.read_thread(chat, self._cell)
        except (ValueError, TypeError):
            raise _SeatFailure("invalid_public_thread") from None
        receipt["snapshots"][phase] = [row["seq"] for row in messages]
        return acts

    async def _message(
        self,
        environment: SeatEnvironment,
        exchange: Exchange,
        context: AgentContext,
        receipt: protocol.SeatReceipt,
        dossier: dict[str, Any],
    ) -> None:
        phase = self._position
        record = receipt["turns"].get(phase)
        if record is None:
            if self._role == protocol.HELPERS[1]:
                await exchange.wait(protocol.HELPERS[0])
            action = protocol.fact_message(dossier)
        else:
            proposer = self._cell["proposer"]
            roles = (
                protocol.HELPERS
                if phase == "proposer"
                else (*protocol.HELPERS, proposer)
            )
            await exchange.wait(*roles)
            thread = await self._chat(environment, receipt, phase)
            if not all(role in thread for role in protocol.HELPERS):
                raise _SeatFailure("helper_facts_missing")
            action = await _turn(
                context,
                record,
                protocol.prompt(self._cell, self._role, phase, dossier, thread),
                lambda text: protocol.exchange_action(
                    text, phase, thread.get(proposer)
                ),
            )
        body = {"text": seat._canonical_json(action)}
        acknowledgment = await _world(environment, seat._WorldAction.SHARE, body)
        seq = acknowledgment.get("seq")
        if (
            type(seq) is not int
            or seq < 1
            or acknowledgment != {"seat": self._role, "text": body["text"], "seq": seq}
        ):
            raise _SeatFailure("message_unacknowledged")
        receipt["message"] = {"seat": self._role, "text": body["text"], "seq": seq}
        if record is not None:
            record.update(status="submitted", event_seq=seq)

    async def _ballot(
        self,
        environment: SeatEnvironment,
        exchange: Exchange,
        context: AgentContext,
        receipt: protocol.SeatReceipt,
        dossier: dict[str, Any],
    ) -> None:
        await exchange.wait(self._cell["proposer"], self._cell["responder"])
        thread = await self._chat(environment, receipt, "ballot")
        if set(thread) != set(ROLES):
            receipt["failure"] = receipt["failure"] or "exchange_incomplete"
        record = receipt["turns"].get("ballot")
        if record is None:
            ballot: protocol.Ballot = {
                "choice": "Option_A",
                "cited_facts": ["F_SHARED_4"],
            }
        else:
            visible = set(contract._dossier_facts(dossier))
            visible.update(
                thread[role]["id"] for role in protocol.HELPERS if role in thread
            )
            action = await _turn(
                context,
                record,
                protocol.prompt(self._cell, self._role, "ballot", dossier, thread),
                lambda text: dict(protocol.ballot_action(text, visible)),
            )
            ballot = {"choice": action["choice"], "cited_facts": action["cited_facts"]}
        if await _world(environment, seat._WorldAction.VOTE, dict(ballot)) != {
            "seat": self._role,
            **ballot,
        }:
            raise _SeatFailure("ballot_unacknowledged")
        receipt["ballot"] = ballot
        if record is not None:
            record["status"] = "submitted"

    async def _play(
        self,
        environment: SeatEnvironment,
        exchange: Exchange,
        context: AgentContext,
        receipt: protocol.SeatReceipt,
    ) -> None:
        dossier = None
        try:
            view = await _world(environment, seat._WorldAction.DOSSIER)
            try:
                contract._dossier_facts(view)
            except (ValueError, TypeError):
                raise _SeatFailure("invalid_dossier") from None
            if view["seat"] != self._role:
                raise _SeatFailure("wrong_dossier_seat")
            dossier = view
            await self._message(environment, exchange, context, receipt, dossier)
        except _SeatFailure as exc:
            receipt["failure"] = exc.category
        finally:
            exchange.close(self._role)
        if dossier is not None:
            try:
                await self._ballot(environment, exchange, context, receipt, dossier)
            except _SeatFailure as exc:
                receipt["failure"] = receipt["failure"] or exc.category

    async def _participate(
        self,
        environment: SeatEnvironment,
        exchange: Exchange,
        context: AgentContext,
        receipt: protocol.SeatReceipt,
    ) -> None:
        try:
            await self._play(environment, exchange, context, receipt)
        except (KeyError, OSError, RecursionError, TypeError, ValueError):
            receipt["failure"] = receipt["failure"] or "internal_error"
        try:
            acknowledgment = await _world(environment, seat._WorldAction.DONE, {})
            if acknowledgment != {"seat": self._role, "ok": True}:
                raise _SeatFailure("done_unacknowledged")
            receipt["done"] = True
        except _SeatFailure as exc:
            receipt["failure"] = receipt["failure"] or exc.category

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise _SeatFailure("seat_already_started")
        self._started = True
        receipt = self._receipt()
        context.metadata = dict(receipt)
        exchange = self._exchange
        try:
            if (
                not isinstance(environment, SeatEnvironment)
                or environment.service != self._role
                or exchange is None
            ):
                raise _SeatFailure("wrong_environment")
            try:
                fd = os.open(
                    self.logs_dir / RECEIPT,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
            except OSError:
                raise _SeatFailure("receipt_unavailable") from None
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                try:
                    await self._participate(environment, exchange, context, receipt)
                    receipt["status"] = "failed" if receipt["failure"] else "complete"
                except asyncio.CancelledError:
                    receipt.update(
                        status="cancelled", failure=receipt["failure"] or "cancelled"
                    )
                    raise
                finally:
                    for record in receipt["turns"].values():
                        if record["status"] in ("submitting", "requesting"):
                            record["status"] = "unacknowledged"
                    try:
                        stream.write(seat._canonical_json(dict(receipt)) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    except OSError:
                        raise _SeatFailure("receipt_unavailable") from None
        except _SeatFailure as exc:
            receipt.update(status="failed", failure=receipt["failure"] or exc.category)
        finally:
            if exchange is not None:
                exchange.close(self._role)
            context.metadata = dict(receipt)
        if receipt["failure"] is not None:
            raise _SeatFailure(receipt["failure"])


class CoalitionSeat(_Participant):
    _KIND = "live"

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-coalition-live"


class FixedCoalitionHelper(_Participant):
    _KIND = "fixed_helper"

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-coalition-fixed-helper"
