"""Run one native hidden-profile seat with direct host model turns."""

import asyncio
import json
import logging
import os
import shlex
from collections.abc import Callable
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, override

from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

from ..visibility import deepseek
from . import contract, recommendation, schedule

_SCHEMA = "seatlab.committee-seat/v1"
_MODEL = f"deepseek/{deepseek.MODEL}"
_ATTESTED_MODELS = frozenset({deepseek.MODEL.casefold(), deepseek.REVISION.casefold()})
_BARRIER_TIMEOUT_SEC = 120
_POLL_SEC = 1
_SAFE_PROVIDER_FAILURES = frozenset(
    {
        "balance_below_minimum",
        "balance_unverified",
        "budget_guard",
        "key_unavailable",
        "payload_invalid",
        "payload_too_large",
        "provider_response_invalid",
        "provider_timeout",
        "provider_unavailable",
        "route_unverified",
        "served_model_unverified",
        "usage_unverified",
    }
)


class _WorldAction(Enum):
    DOSSIER = ("GET", "/dossier", "200")
    READ_CHAT = ("GET", "/chat/public", "200")
    SHARE = ("POST", "/chat/public", "201")
    VOTE = ("POST", "/vote", "201")
    DONE = ("POST", "/done", "200")


# This code runs in the seat, not on the host. Only fixed route arguments and
# codec-normalized JSON reach it. The stock seat token never leaves the process.
_SEAT_HTTP = """\
import http.client, os, sys
from contextlib import closing
method, path, status = sys.argv[1:4]
headers = {'X-Seat-Token': os.environ.get('SEAT_TOKEN', '')}
body = None
if method == 'POST':
    headers['Content-Type'] = 'application/json'
    body = sys.argv[4].encode('utf-8')
if not headers['X-Seat-Token']:
    sys.exit(1)
try:
    with closing(http.client.HTTPConnection('world', 8080, timeout=3)) as connection:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status != int(status):
            sys.exit(1)
        data = response.read(16385)
        if len(data) > 16384:
            sys.exit(1)
        sys.stdout.buffer.write(data)
except (OSError, ValueError, http.client.HTTPException):
    sys.exit(1)
"""


class _SeatFailure(NonZeroAgentExitCodeError):
    """Keep the other Harbor seats running and expose only a fixed category."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"Committee seat: {category}")


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


async def _world(
    environment: SeatEnvironment,
    action: _WorldAction,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if "DEEPSEEK_API_KEY" in os.environ:
        raise _SeatFailure("unbound_host_key")
    args = ["python", "-I", "-c", _SEAT_HTTP, *action.value]
    if body is not None:
        args.append(_canonical_json(body))
    try:
        result = await environment.exec(command=shlex.join(args), timeout_sec=6)
        if result.return_code != 0:
            raise ValueError
        data = json.loads(result.stdout or "")
        if not isinstance(data, dict):
            raise ValueError
    except Exception:
        raise _SeatFailure("world_request_failed") from None
    return data


def _chat_message(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.keys() == {"seat", "text", "seq"}
        and isinstance(value["seat"], str)
        and value["seat"] in contract.ROLES
        and isinstance(value["text"], str)
        and type(value["seq"]) is int
        and value["seq"] > 0
    )


def _barrier_ready(chat: dict[str, Any], own: dict[str, Any]) -> bool:
    messages = chat.get("messages")
    speakers = chat.get("unique_speakers")
    if (
        not isinstance(messages, list)
        or not 1 <= len(messages) <= len(contract.ROLES)
        or not all(_chat_message(row) for row in messages)
        or not isinstance(speakers, list)
        or not all(isinstance(speaker, str) for speaker in speakers)
        or own not in messages
    ):
        raise _SeatFailure("invalid_chat_snapshot")
    seats = {row["seat"] for row in messages}
    if (
        len(seats) != len(messages)
        or len({row["seq"] for row in messages}) != len(messages)
        or len(speakers) != len(seats)
        or set(speakers) != seats
    ):
        raise _SeatFailure("invalid_chat_snapshot")
    return seats == set(contract.ROLES)


async def _wait_for_chat(
    environment: SeatEnvironment, own: dict[str, Any]
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(_BARRIER_TIMEOUT_SEC):
            while True:
                chat = await _world(environment, _WorldAction.READ_CHAT)
                if _barrier_ready(chat, own):
                    return chat
                await asyncio.sleep(_POLL_SEC)
    except TimeoutError:
        raise _SeatFailure("chat_barrier_timeout") from None


async def _wait_for_recommendation_chat(
    environment: SeatEnvironment,
    map_index: int,
    chair: str,
    phase: str,
    own: list[dict[str, Any]],
) -> tuple[recommendation.Board, list[int]]:
    try:
        async with asyncio.timeout(_BARRIER_TIMEOUT_SEC):
            while True:
                chat = await _world(environment, _WorldAction.READ_CHAT)
                try:
                    view = recommendation.board(chat, map_index, chair)
                except ValueError:
                    raise _SeatFailure("invalid_chat_snapshot") from None
                messages = chat["messages"]
                if [row for row in messages if row["seat"] == own[0]["seat"]] != own:
                    raise _SeatFailure("invalid_chat_snapshot")
                if (
                    len(view.facts["messages"]) == len(contract.ROLES)
                    and (
                        phase == "proposal"
                        or len(view.proposals) == len(contract.ROLES)
                    )
                    and (phase != "vote" or view.recommendation is not None)
                ):
                    return view, [row["seq"] for row in messages]
                await asyncio.sleep(_POLL_SEC)
    except TimeoutError:
        raise _SeatFailure("chat_barrier_timeout") from None


def _accumulate_usage(context: AgentContext, usage: object) -> bool:
    if (
        not isinstance(usage, tuple)
        or len(usage) != 3
        or any(type(count) is not int for count in usage)
    ):
        return False
    input_tokens, cache_tokens, output_tokens = usage
    if not 0 <= cache_tokens <= input_tokens or output_tokens < 0:
        return False
    context.n_input_tokens = (context.n_input_tokens or 0) + input_tokens
    context.n_cache_tokens = (context.n_cache_tokens or 0) + cache_tokens
    context.n_output_tokens = (context.n_output_tokens or 0) + output_tokens
    return True


class CommitteeSeat(BaseAgent):
    """Give the model only the authenticated scoped view and the public reports."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        map_index: int,
        arm: str,
        run_id: str,
        logger: logging.Logger | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if model_name not in (None, _MODEL) or extra_env or kwargs:
            raise ValueError("Committee seat does not accept overrides")
        if (
            role not in contract.ROLES
            or (
                arm not in contract.ARMS
                and arm not in schedule.TALLY_ARMS
                and arm not in schedule.RECOMMENDATION_ARMS
            )
            or type(map_index) is not int
            or not 0 <= map_index < 6
            or not isinstance(run_id, str)
            or not run_id
        ):
            raise ValueError("Invalid committee seat configuration")
        if arm in schedule.TALLY_ARMS and not any(
            cell["cell_id"] == run_id
            and cell["map_index"] == map_index
            and cell["arm"] == arm
            for cell in schedule.tally_grid()
        ):
            raise ValueError("Invalid tally run identity")
        chair = None
        if arm in schedule.RECOMMENDATION_ARMS or run_id.startswith("rc-"):
            cell = next(
                (
                    cell
                    for cell in schedule.recommendation_grid()
                    if cell["cell_id"] == run_id
                    and cell["map_index"] == map_index
                    and cell["arm"] == arm
                ),
                None,
            )
            if cell is None:
                raise ValueError("Invalid recommendation run identity")
            chair = schedule.chair(map_index, cell["repetition"])
        super().__init__(logs_dir=logs_dir, model_name=_MODEL, logger=logger)
        self._role = role
        self._map_index = map_index
        self._arm = arm
        self._run_id = run_id
        self._chair = chair
        self._started = False

    @staticmethod
    @override
    def name() -> str:
        return "seatlab-committee"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def _turn(
        self,
        phase: str,
        context: AgentContext,
        record: dict[str, Any],
        build_prompt: Callable[[], str],
        decode_action: Callable[
            [str], dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]
        ],
    ) -> dict[str, Any]:
        try:
            prompt = build_prompt()
            record["prompt_sha256"] = _digest(prompt)
        except Exception:
            record["status"] = "failed"
            raise _SeatFailure("invalid_view") from None
        record["status"] = "requesting"
        try:
            turn = await asyncio.to_thread(
                deepseek.turn, prompt, run_id=f"{self._run_id}-{phase}", role=self._role
            )
        except deepseek.SeatUnavailable as exc:
            record["status"] = "unavailable"
            settled_usage = exc.settled_usage
            if settled_usage is not None and not _accumulate_usage(
                context, settled_usage
            ):
                raise _SeatFailure("usage_unverified") from None
            category = exc.category
            if not isinstance(category, str) or category not in _SAFE_PROVIDER_FAILURES:
                category = "model_unavailable"
            raise _SeatFailure(category) from None
        except Exception:
            record["status"] = "unavailable"
            raise _SeatFailure("model_unavailable") from None
        try:
            record["response_sha256"] = _digest(turn.text)
        except (AttributeError, UnicodeError):
            pass
        served = turn.served_model
        if not isinstance(served, str) or served.casefold() not in _ATTESTED_MODELS:
            record["status"] = "rejected"
            raise _SeatFailure("served_model_unverified")
        record["served_model"] = served
        # The direct route returns counts only after verifying and settling them.
        # Keep a billable turn even if its response fails the action codec.
        if not _accumulate_usage(
            context, (turn.input_tokens, turn.cache_tokens, turn.output_tokens)
        ):
            record["status"] = "rejected"
            raise _SeatFailure("usage_unverified")
        if record["response_sha256"] is None:
            record["status"] = "rejected"
            raise _SeatFailure("provider_response_invalid")
        try:
            decoded = decode_action(turn.text)
            action, readout = decoded if isinstance(decoded, tuple) else (decoded, None)
            record["action_sha256"] = _digest(_canonical_json(action))
        except Exception:
            record["status"] = "rejected"
            raise _SeatFailure("invalid_model_action") from None
        if readout is not None:
            record["readout"] = readout
        record["status"] = "submitting"
        return action

    async def _submit_chat(
        self,
        environment: SeatEnvironment,
        phase: str,
        record: dict[str, Any],
        actions: dict[str, dict[str, Any]],
        action: dict[str, Any],
    ) -> dict[str, Any]:
        actions[phase] = action
        acknowledgment = await _world(environment, _WorldAction.SHARE, action)
        if (
            not _chat_message(acknowledgment)
            or acknowledgment["seat"] != self._role
            or acknowledgment["text"] != action["text"]
        ):
            raise _SeatFailure(f"{phase}_unacknowledged")
        record["status"] = "submitted"
        return acknowledgment

    async def _play_recommendation(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        metadata: dict[str, Any],
        actions: dict[str, dict[str, Any]],
        dossier: dict[str, Any],
        chair: str,
        share_ack: dict[str, Any],
    ) -> dict[str, Any]:
        own = [share_ack]

        async def turn(phase: str) -> dict[str, Any]:
            metadata["phase"] = phase
            view, seqs = await _wait_for_recommendation_chat(
                environment, self._map_index, chair, phase, own
            )
            # Record the actual GET even when it includes faster peers' posts.
            # The first ballot must still use only the frozen fact view.
            metadata["read_msg_seqs"][phase] = seqs

            def prompt() -> str:
                if phase == "proposal":
                    return contract.vote_prompt(dossier, view.facts, self._map_index)
                return recommendation.turn_prompt(
                    phase, dossier, view, self._map_index, self._role, chair, self._arm
                )

            def action(text: str) -> dict[str, Any]:
                vote = contract.vote_action(text, dossier, view.facts, self._map_index)
                if phase == "vote":
                    return vote
                return recommendation.post(phase, vote, self._map_index)

            return await self._turn(
                phase, context, metadata["turns"][phase], prompt, action
            )

        chat_phases = (
            ("proposal", "recommend") if self._role == chair else ("proposal",)
        )
        for phase in chat_phases:
            action = await turn(phase)
            own.append(
                await self._submit_chat(
                    environment, phase, metadata["turns"][phase], actions, action
                )
            )
        return await turn("vote")

    async def _play(
        self,
        environment: SeatEnvironment,
        context: AgentContext,
        metadata: dict[str, Any],
        actions: dict[str, dict[str, Any]],
    ) -> None:
        metadata["phase"] = "dossier"
        dossier = await _world(environment, _WorldAction.DOSSIER)
        if dossier.get("seat") != self._role:
            raise _SeatFailure("wrong_dossier_seat")

        metadata["phase"] = "share"
        tally = self._arm in schedule.TALLY_ARMS
        share_record = metadata["turns"]["share"]
        share = await self._turn(
            "share",
            context,
            share_record,
            lambda: contract.share_prompt(
                dossier,
                self._map_index,
                "novelty" if tally or self._chair is not None else self._arm,
            ),
            lambda text: contract.share_action(text, dossier, self._map_index),
        )
        acknowledgment = await self._submit_chat(
            environment, "share", share_record, actions, share
        )

        vote_record = metadata["turns"]["vote"]
        if self._chair is not None:
            vote = await self._play_recommendation(
                environment,
                context,
                metadata,
                actions,
                dossier,
                self._chair,
                acknowledgment,
            )
        else:
            metadata["phase"] = "barrier"
            chat = await _wait_for_chat(environment, acknowledgment)
            metadata["phase"] = "vote"
            if tally:
                vote = await self._turn(
                    "vote",
                    context,
                    vote_record,
                    lambda: contract.tally_prompt(
                        dossier, chat, self._map_index, self._arm
                    ),
                    lambda text: contract.tally_action(
                        text, dossier, chat, self._map_index, self._arm
                    ),
                )
            else:
                vote = await self._turn(
                    "vote",
                    context,
                    vote_record,
                    lambda: contract.vote_prompt(dossier, chat, self._map_index),
                    lambda text: contract.vote_action(
                        text, dossier, chat, self._map_index
                    ),
                )
        actions["vote"] = vote
        if await _world(environment, _WorldAction.VOTE, vote) != {
            "seat": self._role,
            **vote,
        }:
            raise _SeatFailure("vote_unacknowledged")
        vote_record["status"] = "submitted"

        metadata["phase"] = "done"
        done = await _world(environment, _WorldAction.DONE, {})
        if done.get("ok") is not True or done.get("seat") != self._role:
            raise _SeatFailure("done_unacknowledged")
        metadata["status"] = "complete"

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        if self._started:
            raise _SeatFailure("seat_already_started")
        self._started = True
        phases = ("share",)
        if self._chair is not None:
            phases += ("proposal",)
            if self._role == self._chair:
                phases += ("recommend",)
        phases += ("vote",)
        turns: dict[str, dict[str, Any]] = {
            phase: {
                "status": "not_started",
                "requested_model": deepseek.MODEL,
                "served_model": None,
                "prompt_sha256": None,
                "response_sha256": None,
                "action_sha256": None,
            }
            for phase in phases
        }
        metadata: dict[str, Any] = {
            "schema": _SCHEMA,
            "role": self._role,
            "run_id": self._run_id,
            "map_index": self._map_index,
            "arm": self._arm,
            "phase": "seat",
            "status": "running",
            "turns": turns,
        }
        if self._chair is not None:
            metadata["read_msg_seqs"] = {}
        context.metadata = metadata
        context.n_input_tokens = context.n_cache_tokens = context.n_output_tokens = None
        actions: dict[str, dict[str, Any]] = {}
        try:
            fd = os.open(
                self.logs_dir / "committee-turns.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except OSError:
            metadata.update(status="failed", failure="receipt_unavailable")
            raise _SeatFailure("receipt_unavailable") from None
        with os.fdopen(fd, "w", encoding="utf-8") as receipt:
            try:
                if (
                    not isinstance(environment, SeatEnvironment)
                    or environment.service != self._role
                ):
                    raise _SeatFailure("wrong_environment")
                await self._play(environment, context, metadata, actions)
            except _SeatFailure as exc:
                record = turns.get(metadata["phase"])
                if record is not None and record["status"] == "submitting":
                    record["status"] = "unacknowledged"
                metadata.update(status="failed", failure=exc.category)
                raise
            except asyncio.CancelledError:
                metadata.update(status="cancelled", failure="cancelled")
                raise
            except Exception:
                metadata.update(status="failed", failure="internal_error")
                raise _SeatFailure("internal_error") from None
            finally:
                try:
                    receipt.write(
                        _canonical_json(
                            {
                                **metadata,
                                "turns": {
                                    phase: {**record, "action": actions.get(phase)}
                                    for phase, record in turns.items()
                                },
                            }
                        )
                        + "\n"
                    )
                    receipt.flush()
                except OSError:
                    metadata.update(status="failed", failure="receipt_unavailable")
                    raise _SeatFailure("receipt_unavailable") from None
