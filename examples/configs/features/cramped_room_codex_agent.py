"""Play a native Cramped Room seat with a shared draft and optional repair."""

import asyncio
import json
import re
import shlex
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Any, override

from codex_text_turn import (
    AUTH,
    PROVIDER,
    QUOTA_ERROR,
    completion,
    json_object,
    run_codex,
)
from cramped_room_prompts import phase_prompt

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

_ACTIONS = {
    "N": (0, -1),
    "S": (0, 1),
    "E": (1, 0),
    "W": (-1, 0),
    "WAIT": (0, 0),
    "USE": "interact",
}
_CHECKPOINT_TICKS = (12, 13, 14)
_FIRST_COUNT = 16
_IDLE_COUNT = 18
_SECOND_TICK = _FIRST_COUNT + _IDLE_COUNT
_LAST_COUNT = 4
_MAX_STATE_BYTES = 16_384
_OBSERVE_TIMEOUT_SEC = 8
# A seat can submit while its peer is still in a 120-second Codex turn.
_BATCH_TIMEOUT_SEC = 135
# Chef1's opening batch can span chef0's joint turn and checkpoint repair.
_CHEF1_FIRST_BATCH_TIMEOUT_SEC = 255
_peer_draft: asyncio.Future[tuple[str, ...]] | None = None


def prepare_exchange() -> None:
    """Create the draft promise for this runner's single trial."""
    global _peer_draft
    _peer_draft = asyncio.get_running_loop().create_future()


# http.client makes a direct connection, without proxies or redirects. The
# shell only runs this fixed program; validated actions arrive as JSON data.
_SEAT = """\
import http.client
import json
import signal
import sys
import time

role, start, raw_actions, budget, max_bytes = sys.argv[1:]
start, budget, max_bytes = int(start), int(budget), int(max_bytes)
actions = json.loads(raw_actions)
if role not in ("chef0", "chef1"):
    raise SystemExit("Invalid kitchen role")
signal.alarm(budget)
deadline = time.monotonic() + budget


def remaining():
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise SystemExit("Kitchen seat deadline expired")
    return seconds


def request(method, path, body=None):
    connection = http.client.HTTPConnection("world", 8080, timeout=min(2, remaining()))
    try:
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, body=body, headers=headers)
        with connection.getresponse() as response:
            if response.status != 200:
                raise SystemExit(f"Kitchen {method} returned HTTP {response.status}")
            if method == "GET":
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise SystemExit("Kitchen state exceeds its size limit")
                return json.loads(data)
    finally:
        connection.close()


def wait_for_tick(expected):
    while True:
        observed = request("GET", "/state")
        tick = observed.get("tick") if isinstance(observed, dict) else None
        if type(tick) is not int or not 0 <= tick <= expected:
            raise SystemExit("Unexpected kitchen tick")
        if tick == expected:
            if not isinstance(observed.get("state"), dict):
                raise SystemExit("Invalid kitchen state")
            return observed
        time.sleep(min(0.05, remaining()))


observation = wait_for_tick(start)
for tick, action in enumerate(actions, start):
    body = json.dumps({"tick": tick, "action": action}, separators=(",", ":"))
    request("POST", "/action/" + role, body)
    observation = wait_for_tick(tick + 1)
print(json.dumps(observation, separators=(",", ":"), allow_nan=False))
"""


async def _seat_phase(
    environment: BaseEnvironment, role: str, start: int, actions: list[str]
) -> dict[str, Any]:
    budget = _BATCH_TIMEOUT_SEC if actions else _OBSERVE_TIMEOUT_SEC
    if actions and role == "chef1" and start == 0:
        budget = _CHEF1_FIRST_BATCH_TIMEOUT_SEC
    payload = json.dumps(
        [_ACTIONS[action] for action in actions], separators=(",", ":")
    )
    command = shlex.join(
        [
            "python3",
            "-I",
            "-c",
            _SEAT,
            role,
            str(start),
            payload,
            str(budget),
            str(_MAX_STATE_BYTES),
        ]
    )
    error = f"Kitchen seat phase at tick {start} failed"
    try:
        async with asyncio.timeout(budget + 3):
            result = await environment.exec(command, timeout_sec=budget + 3)
    except Exception:
        raise RuntimeError(error) from None
    if result.return_code != 0:
        raise RuntimeError(error)
    stdout = result.stdout or ""
    if len(stdout) > _MAX_STATE_BYTES + 1:
        raise RuntimeError(error)
    observation = json_object(stdout, error)
    tick = observation.get("tick")
    if type(tick) is not int or tick != start + len(actions):
        raise RuntimeError(error)
    if not isinstance(observation.get("state"), dict):
        raise RuntimeError(error)
    return observation


def _repair_eligible(state: dict[str, Any], next_action: str) -> bool:
    if next_action not in ("W", "E", "S"):
        return False
    players = state.get("players")
    objects = state.get("objects")
    if not isinstance(players, list) or not players or not isinstance(objects, list):
        return False
    chef = players[0]
    if (
        not isinstance(chef, dict)
        or chef.get("held_object", False) is not None
        or chef.get("position") != [2, 1]
        or chef.get("orientation") != [0, -1]
    ):
        return False
    for obj in objects:
        if isinstance(obj, dict) and obj.get("position") == [2, 0]:
            ingredients = obj.get("_ingredients")
            return (
                obj.get("name") == "soup"
                and obj.get("cooking_tick") == -1
                and isinstance(ingredients, list)
                and len(ingredients) == 3
            )
    return False


def _action_batch(text: str, count: int) -> list[str]:
    error = "Codex returned an invalid kitchen action batch"
    text = text.strip()
    if text.startswith("```"):
        fence = re.fullmatch(
            r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text, re.DOTALL | re.IGNORECASE
        )
        if fence is None:
            raise RuntimeError(error)
        text = fence[1]
    value = json_object(text, error)
    actions = value.get("actions")
    if (
        value.keys() != {"actions"}
        or not isinstance(actions, list)
        or len(actions) != count
        or not all(isinstance(action, str) and action in _ACTIONS for action in actions)
    ):
        raise RuntimeError(error)
    return actions


def _record_turn(
    context: AgentContext,
    usage: AgentContext,
    row: dict[str, Any],
    turns: list[dict[str, Any]],
) -> None:
    row.update(
        input_tokens=usage.n_input_tokens,
        cached_input_tokens=usage.n_cache_tokens,
        output_tokens=usage.n_output_tokens,
    )
    turns.append(row)
    assert context.metadata is not None
    context.metadata["model_turn_seconds"] = sum(turn["seconds"] for turn in turns)
    for attr, field in (
        ("n_input_tokens", "input_tokens"),
        ("n_cache_tokens", "cached_input_tokens"),
        ("n_output_tokens", "output_tokens"),
    ):
        values = [turn[field] for turn in turns]
        setattr(
            context, attr, sum(values) if all(v is not None for v in values) else None
        )


class KitchenPilotAgent(BaseAgent):
    """Control a fixed chef seat without passing task instructions to Codex."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        role: str,
        mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if role not in ("chef0", "chef1"):
            raise ValueError("Kitchen role must be chef0 or chef1")
        if role == "chef0" and mode not in ("observe", "repair"):
            raise ValueError("chef0 kitchen mode must be observe or repair")
        if role == "chef1" and mode is not None:
            raise ValueError("chef1 does not take a kitchen mode")
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        model = self._parsed_model_name
        if (
            self._parsed_model_provider != PROVIDER
            or not model
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", model)
        ):
            raise ValueError("Kitchen pilot requires an openai-codex/<native-id> model")
        self._native_model = model
        self._role = role
        self._mode = mode

    @staticmethod
    @override
    def _parse_model_name_for_connection(
        model_name: str, *, skip_model_parsing: bool = False
    ) -> tuple[str | None, str]:
        if not skip_model_parsing:
            provider, separator, native = model_name.partition("/")
            if separator and provider == PROVIDER:
                return provider, native
        return BaseAgent._parse_model_name_for_connection(
            model_name, skip_model_parsing=skip_model_parsing
        )

    @staticmethod
    @override
    def name() -> str:
        return "cramped-room-codex"

    @override
    def version(self) -> str:
        return "8"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        # The seat image provides Python and the world needs no setup per seat.
        pass

    async def _turn(
        self,
        stage: str,
        tick: int,
        count: int,
        observation: dict[str, Any],
        context: AgentContext,
        turns: list[dict[str, Any]],
        *,
        own_plan: list[str] | None = None,
        peer_plan: tuple[str, ...] | list[str] | None = None,
    ) -> list[str]:
        prompt = phase_prompt(
            self._role, stage, tick, observation, own_plan=own_plan, peer_plan=peer_plan
        )
        usage = AgentContext()
        row: dict[str, Any] = {"phase": stage, "tick": tick}
        with TemporaryDirectory(prefix="kitchen-codex-", dir="/tmp") as scratch:
            started = monotonic()
            error_type = "CodexExecutionError"
            try:
                try:
                    result = await run_codex(self._native_model, prompt, scratch)
                finally:
                    row["seconds"] = monotonic() - started
                error_type = "CodexCompletionError"
                text = completion(*result, usage)
                error_type = "InvalidActionsError"
                actions = _action_batch(text, count)
                row["actions"] = actions
                return actions
            except asyncio.CancelledError:
                row["error_type"] = "CancelledError"
                raise
            except Exception as error:
                row["error_type"] = error_type
                if (
                    error_type == "CodexCompletionError"
                    and isinstance(error, RuntimeError)
                    and error.args == (QUOTA_ERROR,)
                ):
                    raise RuntimeError(QUOTA_ERROR) from None
                raise RuntimeError(
                    f"Kitchen model turn at tick {tick}: {error_type}"
                ) from None
            finally:
                _record_turn(context, usage, row, turns)

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        exchange = _peer_draft
        if exchange is None or exchange.get_loop() is not asyncio.get_running_loop():
            raise RuntimeError("Kitchen plan exchange is not prepared")

        turns: list[dict[str, Any]] = []
        checkpoints: list[dict[str, Any]] = []
        context.metadata = {
            **(context.metadata or {}),
            "model_auth": AUTH,
            "model_turn_seconds": 0.0,
            "phase_turns": turns,
            "peer_plan_seen": None,
            "peer_wait_seconds": None,
            "checkpoints": checkpoints,
            "repair_peer_plan_seen": None,
        }
        context.cost_usd = None
        context.n_input_tokens = context.n_cache_tokens = context.n_output_tokens = None

        start = 0
        try:
            observation = await _seat_phase(environment, self._role, 0, [])
            if self._role == "chef1":
                actions = await self._turn(
                    "draft", 0, _FIRST_COUNT, observation, context, turns
                )
                if exchange.done():
                    raise RuntimeError("Kitchen peer draft is already committed")
                exchange.set_result(tuple(actions))
            else:
                # Both modes use the same committed peer plan for the joint turn.
                started = monotonic()
                try:
                    peer_plan = await asyncio.shield(exchange)
                finally:
                    context.metadata["peer_wait_seconds"] = monotonic() - started
                context.metadata["peer_plan_seen"] = list(peer_plan)
                actions = await self._turn(
                    "joint",
                    0,
                    _FIRST_COUNT,
                    observation,
                    context,
                    turns,
                    peer_plan=peer_plan,
                )
                repair_used = False
                for tick in _CHECKPOINT_TICKS:
                    prefix_count = tick - start
                    observation = await _seat_phase(
                        environment, self._role, start, actions[:prefix_count]
                    )
                    actions = actions[prefix_count:]
                    start = tick
                    eligible = _repair_eligible(observation["state"], actions[0])
                    repair_called = (
                        eligible and self._mode == "repair" and not repair_used
                    )
                    checkpoints.append(
                        {
                            "tick": tick,
                            "eligible": eligible,
                            "repair_called": repair_called,
                        }
                    )
                    if repair_called:
                        repair_used = True
                        repair_peer_plan = list(peer_plan[tick:])
                        context.metadata["repair_peer_plan_seen"] = repair_peer_plan
                        actions = await self._turn(
                            "repair",
                            tick,
                            len(actions),
                            observation,
                            context,
                            turns,
                            own_plan=actions,
                            peer_plan=repair_peer_plan,
                        )
        except (Exception, asyncio.CancelledError):
            if self._role == "chef1":
                exchange.cancel()
            raise

        observation = await _seat_phase(
            environment, self._role, start, actions + ["WAIT"] * _IDLE_COUNT
        )
        actions = await self._turn(
            "finish", _SECOND_TICK, _LAST_COUNT, observation, context, turns
        )
        await _seat_phase(environment, self._role, _SECOND_TICK, actions)
