import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Never, override

from examples.seatlab import seat_agent
from examples.seatlab.capsule import view_hash
from examples.seatlab.market_handoff import decisions
from examples.seatlab.visibility import deepseek, spend
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

DECLARED_MODEL = "deepseek/deepseek-flash"
SOURCES = ("replay", "deepseek")
_PERSONAS = {"sales": "sales analyst", "finance": "finance director"}
_LEDGER_ROLES = {"sales": "analyst_1", "finance": "decision_maker"}
_WORLD_ERRORS = (RuntimeError, OSError, ValueError, RecursionError)
_LEDGER_ERRORS = (spend.SpendRefused, sqlite3.Error, OSError)
_VIEW_KEYS = {
    "sales": frozenset({"role", "completed_days", "observation"}),
    "finance": frozenset({"role", "completed_days", "observation", "advice"}),
}
_PENDING = {"error": "waiting for sales"}
_RUN_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_VIEW_ATTEMPTS = 180
_VIEW_TIMEOUT_SEC = 200
_RETRY_SEC = 1
_MAX_REPLY_CHARS = 8192
_AGENT_NAME = "seatlab-market-seat"
_AGENT_VERSION = "1.0.0"


def _fail(metadata: dict[str, Any], phase: str) -> Never:
    assert phase.isidentifier() and phase == phase.lower()
    metadata["phase"] = phase
    raise seat_agent._Unmeasured(f"Market seat: {phase}") from None


def _checked_view(view: object, role: str) -> tuple[int, dict[str, Any], int | None]:
    assert role in _PERSONAS
    if not isinstance(view, dict):
        raise TypeError("Malformed market view")
    fields: dict[str, Any] = {key: value for key, value in view.items() if isinstance(key, str)}
    if len(fields) != len(view) or fields.keys() != _VIEW_KEYS[role] or fields["role"] != role:
        raise ValueError("Malformed market view")
    days, observation = fields["completed_days"], fields["observation"]
    if type(days) is not int or not 0 <= days <= decisions.PREFIX_DAYS:
        raise ValueError("Malformed market view days")
    if not isinstance(observation, dict) or not observation:
        raise ValueError("Malformed market observation")
    if role == "sales":
        return days, observation, None
    advice = fields["advice"]
    if type(advice) is not int or advice not in decisions.PICKS:
        raise ValueError("Malformed market advice")
    return days, observation, advice


def _prompt(role: str, days: int, observation: dict[str, Any], advice: int | None) -> str:
    assert role in _PERSONAS and (advice is None) == (role == "sales")
    task = (decisions.SALES_TASK if role == "sales"
            else decisions.FINANCE_TASK.format(advice=advice))
    prompt = decisions.GOAL.format(role=_PERSONAS[role], view=role, days=days,
                                   data=decisions.canonical(observation))
    return prompt + task + decisions.REPLY_RULE


def _reply_pick(text: object) -> int:
    if not isinstance(text, str) or len(text) > _MAX_REPLY_CHARS:
        raise ValueError("Invalid model reply")
    value = decisions.decode(text.strip())
    pick = value.get("pick") if isinstance(value, dict) and value.keys() == {"pick"} else None
    if type(pick) is not int or pick not in decisions.PICKS:
        raise ValueError("Invalid model pick")
    return pick


async def _view(environment: SeatEnvironment, role: str) -> object:
    assert role in _PERSONAS
    async with asyncio.timeout(_VIEW_TIMEOUT_SEC):
        for _ in range(_VIEW_ATTEMPTS):
            status, body = await seat_agent._world_request(environment, "/view")
            if status == 200:
                return decisions.decode(body)
            if status != 409 or role != "finance" or decisions.decode(body) != _PENDING:
                raise ValueError("Market view rejected")
            await asyncio.sleep(_RETRY_SEC)
    raise TimeoutError("Market view wait exhausted")


async def _submit(environment: SeatEnvironment, pick: int, digest: str) -> None:
    assert type(pick) is int and pick in decisions.PICKS and decisions.HEX64.fullmatch(digest)
    status, body = await seat_agent._world_request(
        environment, "/submit", {"pick": pick}, view_sha256=digest
    )
    accepted = decisions.decode(body)
    if (status != 200 or not isinstance(accepted, dict) or accepted.keys() != {"accepted"}
            or accepted["accepted"] is not True):
        raise ValueError("Market submission rejected")


async def _ask(prompt: str, role: str, run_id: str, metadata: dict[str, Any],
               context: AgentContext) -> str:
    assert role in _LEDGER_ROLES and _RUN_ID.fullmatch(run_id)
    metadata["phase"] = "model_pending"
    try:
        turn = await asyncio.to_thread(
            deepseek.turn, prompt, run_id=run_id, role=_LEDGER_ROLES[role]
        )
    except deepseek.SeatUnavailable as error:
        if error.settled_usage is not None:
            (context.n_input_tokens, context.n_cache_tokens,
             context.n_output_tokens) = error.settled_usage
        phase = error.category
        if not phase.isidentifier() or phase != phase.lower():
            phase = "direct_unavailable"
        _fail(metadata, phase)
    except _LEDGER_ERRORS:
        _fail(metadata, "direct_failed")
    context.n_input_tokens = turn.input_tokens
    context.n_cache_tokens = turn.cache_tokens
    context.n_output_tokens = turn.output_tokens
    metadata["served_model"] = turn.served_model
    return turn.text


class MarketSeat(BaseAgent):
    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        source: str | None = None,
        pick: int | None = None,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        if kwargs or extra_env:
            raise ValueError("Market seat does not accept overrides")
        if source not in SOURCES:
            raise ValueError("Market seat source must be replay or deepseek")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError("Market seat requires a host trial slug")
        if source == "replay":
            if type(pick) is not int or pick not in decisions.PICKS or model_name is not None:
                raise ValueError("Replay market seat requires only a supplied pick")
        elif pick is not None or model_name not in (None, DECLARED_MODEL):
            raise ValueError("DeepSeek market seat does not accept a pick or model override")
        super().__init__(
            logs_dir=logs_dir,
            model_name=DECLARED_MODEL if source == "deepseek" else None,
            logger=logger,
        )
        self._source = source
        self._pick = pick
        self._run_id = run_id
        self._started = False

    @override
    def _parse_model_name(self, model_name: str) -> tuple[str, str]:
        provider, name = model_name.split("/", 1)
        return provider, name

    @staticmethod
    @override
    def name() -> str:
        return _AGENT_NAME

    @override
    def version(self) -> str:
        return _AGENT_VERSION

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise seat_agent._Unmeasured("Market seat already started")
        self._started = True
        metadata: dict[str, Any] = {
            "role": None, "source": self._source, "served_model": None,
            "phase": "waiting_view", "view_sha256": None, "prompt_sha256": None, "pick": None,
        }
        context.metadata = metadata
        if not isinstance(environment, SeatEnvironment) or environment.service not in _PERSONAS:
            _fail(metadata, "invalid_seat")
        role = environment.service
        metadata["role"] = role
        try:
            view = await _view(environment, role)
        except TimeoutError:
            _fail(metadata, "view_timeout")
        except _WORLD_ERRORS:
            _fail(metadata, "view_unavailable")
        try:
            days, observation, advice = _checked_view(view, role)
            digest = view_hash(view)
            prompt = _prompt(role, days, observation, advice)
        except (ValueError, TypeError, RecursionError):
            _fail(metadata, "invalid_view")
        metadata.update(view_sha256=digest, prompt_sha256=decisions.sha(prompt))
        if self._source == "replay":
            pick = self._pick
        else:
            text = await _ask(prompt, role, self._run_id, metadata, context)
            try:
                pick = _reply_pick(text)
            except (ValueError, RecursionError):
                _fail(metadata, "invalid_model_reply")
        assert type(pick) is int and pick in decisions.PICKS
        metadata["phase"] = "submission_pending"
        try:
            await _submit(environment, pick, digest)
        except _WORLD_ERRORS:
            _fail(metadata, "submission_rejected")
        metadata.update(phase="submitted", pick=pick)
