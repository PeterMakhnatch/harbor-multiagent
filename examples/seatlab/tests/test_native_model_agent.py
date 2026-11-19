import asyncio
import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID, uuid4

import pytest

from examples.seatlab import claude_replay, seat_agent
from examples.seatlab.capsule import encode_view, view_hash
from examples.seatlab.native_model_agent import NativeModelAgent
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_MODEL = "claude-sonnet-5-5"
_DECLARED_MODEL = f"anthropic/{_MODEL}"
_AUTH_ARGS = ["auth", "status", "--json"]
_AUTH = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "max",
    "email": "identity-not-for-logs@example.invalid",
}
_TELEMETRY = {
    "input_tokens": 11,
    "cache_read_input_tokens": 23,
    "cache_creation_input_tokens": 17,
    "output_tokens": 31,
    "duration_ms": 113,
    "duration_api_ms": 87,
    "estimated_cost_usd": 0.012,
}
_REPORTS = [
    {"revision": 1, "available": ["A", "B"]},
    {"revision": 2, "available": ["C", "D"]},
]
# A shape-valid but incorrect report must still have its intended effect.
_SENT_REPORTS = [
    {"revision": 2, "available": ["A", "B"]},
    {"revision": 1, "available": ["C", "D"]},
]
_ANSWERS = {
    "inventory": {"rows": [{"id": 0, "reports": _SENT_REPORTS}]},
    "dispatch": {"rows": [{"id": 0, "bay": "A"}]},
}


def _seat(role: str) -> SeatEnvironment:
    environment = Mock(spec=SeatEnvironment)
    environment.service = role
    return cast(SeatEnvironment, environment)


def _view(role: str) -> dict[str, Any]:
    case = (
        {"id": 0, "reports": _REPORTS}
        if role == "inventory"
        else {
            "id": 0,
            "utilities": {"A": 1, "B": 4, "C": 2, "D": 3},
            "messages": _SENT_REPORTS,
            "pooled_available": None,
        }
    )
    return {"role": role, "condition": "reordered", "cases": [case]}


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    change: dict[str, Any] | None = None,
    auth: dict[str, Any] | None = None,
) -> Mock:
    def invoke(
        args: list[str], body: bytes | None, timeout: int
    ) -> CompletedProcess[bytes]:
        if args == _AUTH_ARGS:
            assert body is None
            document = _AUTH if auth is None else auth
        else:
            assert body is not None
            document = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": args[args.index("--session-id") + 1],
                "num_turns": 1,
                "permission_denials": [],
                "stop_reason": "end_turn",
                "duration_ms": 113,
                "duration_api_ms": 87,
                "total_cost_usd": 0.012,
                "modelUsage": {
                    _MODEL: {
                        "inputTokens": 11,
                        "cacheReadInputTokens": 23,
                        "cacheCreationInputTokens": 17,
                        "outputTokens": 31,
                    }
                },
                "result": json.dumps(_ANSWERS[json.loads(body)["role"]]),
                **(change or {}),
            }
        return CompletedProcess(args, 0, json.dumps(document).encode(), b"")

    mocked = Mock(side_effect=invoke)
    monkeypatch.setattr(claude_replay, "_managed_configuration", lambda: False)
    monkeypatch.setattr(claude_replay, "_invoke", mocked)
    return mocked


def _world(
    monkeypatch: pytest.MonkeyPatch,
    role: str = "inventory",
    *,
    acknowledgment: str = '{"accepted":true}',
) -> AsyncMock:
    mocked = AsyncMock(
        side_effect=[(200, json.dumps(_view(role))), (200, acknowledgment)]
    )
    monkeypatch.setattr(seat_agent, "_world_request", mocked)
    return mocked


@pytest.mark.parametrize("role", ["inventory", "dispatch"])
async def test_accepted_commit_binds_exact_scoped_model_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, role: str
) -> None:
    agent = NativeModelAgent(tmp_path, model_name=_DECLARED_MODEL)
    native_model = agent.to_agent_info().model_info
    assert native_model is not None
    assert (native_model.provider, native_model.name) == ("anthropic", _MODEL)
    assert agent.name() == "seatlab-first-party"
    world = _world(monkeypatch, role)
    client = _client(monkeypatch)
    seat = _seat(role)
    context = AgentContext()

    await agent.run("private-task-instruction", seat, context)

    assert client.call_count == 2
    args, body, timeout = client.call_args.args
    session = args[args.index("--session-id") + 1]
    assert UUID(session).version == 4
    assert args == claude_replay._args(
        seat_agent._RULES[role]
        + " Output only a JSON object. Do not include explanations.",
        session,
    )
    assert body == encode_view(_view(role))
    assert timeout == claude_replay._GENERATION_TIMEOUT
    digest = view_hash(_view(role))
    assert world.await_args_list == [
        call(seat, "/view"),
        call(seat, "/submit", _ANSWERS[role], view_sha256=digest),
    ]
    assert context.metadata == {
        "schema": "seatlab.native-model-seat/v1",
        "role": role,
        "model_turns": 1,
        "requested_model": _MODEL,
        "served_model": _MODEL,
        "view_sha256": digest,
        "stage": "complete",
        "telemetry": _TELEMETRY,
    }
    assert context.n_input_tokens == 51
    assert context.n_cache_tokens == 23
    assert context.n_output_tokens == 31
    assert context.cost_usd == 0.012
    assert context.model_usage is not None
    assert list(context.model_usage) == [_DECLARED_MODEL]
    assert context.model_usage[_DECLARED_MODEL].model_dump() == {
        "n_input_tokens": 51,
        "n_cache_tokens": 23,
        "n_output_tokens": 31,
        "cost_usd": 0.012,
    }
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("change", "stage"),
    [
        ({"result": '{"rows":[{"id":0,"bay":"A"}]}'}, "invalid_or_refused_decisions"),
        ({"result": '{"rows":[],"rows":[]}'}, "invalid_or_refused_decisions"),
        ({"session_id": "unexpected-session-not-for-logs"}, "session_unattested"),
        ({"num_turns": True}, "session_unattested"),
        ({"permission_denials": [{"tool_name": "Bash"}]}, "session_unattested"),
        ({"modelUsage": {"unapproved-model": {}}}, "model_unattested"),
        ({"duration_api_ms": -1}, "telemetry_invalid"),
        ({"total_cost_usd": 0.31}, "budget_estimate_exceeded"),
        ({"stop_reason": "refusal"}, "refused"),
    ],
)
async def test_bad_model_turn_cannot_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    change: dict[str, Any],
    stage: str,
) -> None:
    client = _client(monkeypatch, change=change)
    world = _world(monkeypatch)
    context = AgentContext()
    agent = NativeModelAgent(tmp_path)
    seat = _seat("inventory")

    with pytest.raises(seat_agent._Unmeasured, match=f"^Native model seat: {stage}$"):
        await agent.run("private-task-instruction", seat, context)
    with pytest.raises(seat_agent._Unmeasured, match="already started"):
        await agent.run("", seat, AgentContext())

    world.assert_awaited_once_with(seat, "/view")
    assert client.call_count == 2
    assert context.metadata is not None
    assert context.metadata["stage"] == stage
    assert context.metadata["model_turns"] == 1
    assert context.metadata["view_sha256"] is None
    assert context.cost_usd == change.get("total_cost_usd", 0.012)
    if stage == "refused":
        assert context.metadata["telemetry"] == _TELEMETRY
        assert context.metadata["served_model"] == _MODEL
    assert not list(tmp_path.iterdir())


async def test_bad_world_ack_is_not_a_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _client(monkeypatch)
    _world(monkeypatch, acknowledgment='{"accepted":1}')
    context = AgentContext()

    with pytest.raises(seat_agent._Unmeasured, match="submission_unacknowledged"):
        await NativeModelAgent(tmp_path).run("", _seat("inventory"), context)

    assert context.metadata is not None
    assert context.metadata["stage"] == "submission_unacknowledged"
    assert context.metadata["view_sha256"] is None
    assert context.metadata["telemetry"] == _TELEMETRY


async def test_unverified_subscription_does_not_start_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, auth={**_AUTH, "authMethod": "apiKey"})
    world = _world(monkeypatch)
    context = AgentContext()
    seat = _seat("inventory")

    with pytest.raises(seat_agent._Unmeasured, match="route_unverified"):
        await NativeModelAgent(tmp_path).run("", seat, context)

    client.assert_called_once_with(_AUTH_ARGS, None, claude_replay._AUTH_TIMEOUT)
    world.assert_awaited_once_with(seat, "/view")
    assert context.metadata is not None
    assert context.metadata["model_turns"] == 0
    assert context.metadata["view_sha256"] is None
    assert all(value is None for value in context.metadata["telemetry"].values())


async def test_bad_role_view_never_reaches_host_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch)
    _world(monkeypatch, "dispatch")
    context = AgentContext()

    with pytest.raises(seat_agent._Unmeasured, match="invalid_view"):
        await NativeModelAgent(tmp_path).run("", _seat("inventory"), context)

    client.assert_not_called()
    assert context.metadata is not None
    assert context.metadata["model_turns"] == 0
    assert context.metadata["view_sha256"] is None


async def test_dispatch_waits_for_inventory_and_shares_one_trial_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch)
    waiting = asyncio.Event()
    accepted = asyncio.Event()
    trial_id = uuid4()
    inventory, dispatch = NativeModelAgent(tmp_path), NativeModelAgent(tmp_path)
    inventory.context_id = dispatch.context_id = trial_id
    dispatch_context = AgentContext()

    async def world(
        environment: SeatEnvironment,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        view_sha256: str | None = None,
    ) -> tuple[int, str]:
        role = environment.service
        if path == "/view":
            if role == "dispatch" and not accepted.is_set():
                return 409, '{"error":"waiting for inventory"}'
            return 200, json.dumps(_view(role))
        assert path == "/submit"
        assert payload == _ANSWERS[role]
        assert view_sha256 == view_hash(_view(role))
        if role == "inventory":
            accepted.set()
        return 200, '{"accepted":true}'

    async def wait_for_inventory(seconds: float) -> None:
        assert seconds == 1
        client.assert_not_called()
        assert dispatch_context.metadata is not None
        assert dispatch_context.metadata["model_turns"] == 0
        waiting.set()
        await accepted.wait()

    monkeypatch.setattr(seat_agent, "_world_request", world)
    monkeypatch.setattr(seat_agent.asyncio, "sleep", wait_for_inventory)
    async with asyncio.TaskGroup() as group:
        group.create_task(dispatch.run("", _seat("dispatch"), dispatch_context))
        await waiting.wait()
        group.create_task(inventory.run("", _seat("inventory"), AgentContext()))

    assert client.call_count == 3
    assert client.call_args_list[0] == call(
        _AUTH_ARGS, None, claude_replay._AUTH_TIMEOUT
    )
    assert [item.args[1] for item in client.call_args_list[1:]] == [
        encode_view(_view("inventory")),
        encode_view(_view("dispatch")),
    ]
    assert dispatch_context.metadata is not None
    assert dispatch_context.metadata["stage"] == "complete"


@pytest.mark.parametrize(
    "override",
    [
        {"model_name": "anthropic/a-different-model"},
        {"base_url": "https://alternative.invalid"},
        {"model_api_key_name": "ANTHROPIC_API_KEY"},
        {"extra_env": {"ANTHROPIC_API_KEY": "not-a-key"}},
        {"skip_model_parsing": True},
        {"mcp_servers": [{}]},
    ],
)
def test_constructor_rejects_overrides(
    tmp_path: Path, override: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="does not accept overrides"):
        NativeModelAgent(tmp_path, **override)
