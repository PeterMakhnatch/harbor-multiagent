import http.client
import json
import shlex
import sys
import urllib.request
from contextlib import redirect_stdout
from copy import deepcopy
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call, create_autospec

import pytest

from examples.seatlab import seat_agent
from examples.seatlab.capsule import view_hash
from harbor.agents.factory import AgentFactory
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_MODEL = "baidu/ernie-5.5-flash-preview:free"
_SLUG = "baidu"
_PROVIDER = "Baidu"
_CATALOG: dict[str, Any] = {
    "data": [
        {
            "id": _MODEL,
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "endpoints": ["/api/v1/chat/completions"],
            "pricing": {"prompt": "0", "completion": "0"},
        }
    ]
}
_ENDPOINTS: dict[str, Any] = {
    "data": {
        "id": _MODEL,
        "endpoints": [
            {
                "tag": _SLUG,
                "model_id": _MODEL,
                "provider_name": _PROVIDER,
                "pricing": {"prompt": "0", "completion": "0", "discount": 0},
                "supported_parameters": ["max_tokens"],
            }
        ],
    }
}
_REPORTS = [
    {"revision": 1, "available": ["A", "D"]},
    {"revision": 2, "available": ["B", "D"]},
]


def _dispatch_view(condition: str) -> dict[str, Any]:
    revisions = {
        "silent": [],
        "stale": [0],
        "fresh": [1],
        "reordered": [1, 0],
        "pooled": [],
    }
    return {
        "role": "dispatch",
        "condition": condition,
        "cases": [
            {
                "id": 0,
                "utilities": {"A": 3, "B": 4, "C": 2, "D": 1},
                "messages": [_REPORTS[i] for i in revisions[condition]],
                "pooled_available": ["C", "D"] if condition == "pooled" else None,
            }
        ],
    }


def _inventory_view() -> dict[str, Any]:
    return {
        "role": "inventory",
        "condition": "fresh",
        "cases": [{"id": 0, "reports": _REPORTS}],
    }


def _response() -> dict[str, Any]:
    return {
        "model": _MODEL.removesuffix(":free"),
        "openrouter_metadata": {
            "requested": _MODEL,
            "attempt": 1,
            "is_byok": False,
            "endpoints": {"available": [{"provider": _PROVIDER, "selected": True}]},
            "private_field": "discard-me",
        },
        "usage": {
            "prompt_tokens": 101,
            "completion_tokens": 22,
            "prompt_tokens_details": {"cached_tokens": 17},
            "cost": 0,
        },
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"rows":[{"id":0,"bay":"D"}]}',
                },
            }
        ],
    }


def _seat(role: str) -> SeatEnvironment:
    environment = Mock(spec=SeatEnvironment)
    environment.service = role
    return cast(SeatEnvironment, environment)


def _world(monkeypatch: pytest.MonkeyPatch, view: dict[str, Any]) -> AsyncMock:
    world = AsyncMock(side_effect=[(200, json.dumps(view)), (200, '{"accepted":true}')])
    monkeypatch.setattr(seat_agent, "_world_request", world)
    return world


def _world_wire(
    monkeypatch: pytest.MonkeyPatch, environment: SeatEnvironment, view: dict[str, Any]
) -> Mock:
    connection = create_autospec(http.client.HTTPConnection, instance=True)
    world_body = json.dumps(view, indent=2).encode("utf-8")
    response = Mock(status=200)
    response.read.side_effect = [world_body, b'{"accepted":true}']
    connection.getresponse.return_value = response
    connection_factory = Mock(return_value=connection)
    monkeypatch.setattr(http.client, "HTTPConnection", connection_factory)

    def run_cli(*, command: str, timeout_sec: int) -> ExecResult:
        python, flag, script, *args = shlex.split(command)
        assert (python, flag, timeout_sec) == ("python", "-c", 6)
        assert len(args) == {"GET": 2, "POST": 4}[args[0]]
        output = StringIO()
        with monkeypatch.context() as patch, redirect_stdout(output):
            patch.setattr(sys, "argv", ["-c", *args])
            exec(script, {})
        connection_factory.assert_called_with("world", 8080, timeout=3)
        return ExecResult(return_code=0, stdout=output.getvalue())

    monkeypatch.setattr(environment, "exec", AsyncMock(side_effect=run_cli))
    return connection.request


def _hosted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> seat_agent.SeatAgent:
    monkeypatch.setenv("SEATLAB_FREE_MODEL", _MODEL)
    monkeypatch.setenv("SEATLAB_FREE_PROVIDER", _SLUG)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-a-credential")
    return seat_agent.SeatAgent(tmp_path, policy="hosted")


@pytest.mark.parametrize(
    ("condition", "bay"),
    [
        ("silent", "B"),
        ("stale", "A"),
        ("fresh", "B"),
        ("reordered", "A"),
        ("pooled", "C"),
    ],
)
async def test_arrival_uses_last_visible_bulletin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, condition: str, bay: str
) -> None:
    view = _dispatch_view(condition)
    world = _world(monkeypatch, view)
    environment = _seat("dispatch")
    agent = seat_agent.SeatAgent(tmp_path, policy="arrival")
    context = AgentContext()

    await agent.run("", environment, context)

    assert world.await_args_list == [
        call(environment, "/view"),
        call(
            environment,
            "/submit",
            {"rows": [{"id": 0, "bay": bay}]},
            view_sha256=view_hash(view),
        ),
    ]
    assert context.metadata is not None
    assert context.metadata["decision_source"] == "script"
    assert context.metadata["model_turns"] == 0
    assert context.n_input_tokens == context.n_output_tokens == 0
    assert agent.to_agent_info().model_info is None


async def test_native_import_posts_inventory_actions_with_view_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = _inventory_view()
    environment = _seat("inventory")
    world = _world_wire(monkeypatch, environment, view)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    agent = AgentFactory.create_agent_from_import_path(
        "seat_agent:SeatAgent", tmp_path, policy="arrival"
    )

    await agent.run("", environment, AgentContext())

    assert world.call_args_list == [
        call("GET", "/view", body=None, headers={}),
        call(
            "POST",
            "/submit",
            body=json.dumps(
                {"rows": [{"id": 0, "reports": _REPORTS}]}, separators=(",", ":")
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-SeatLab-View-SHA256": view_hash(view),
            },
        ),
    ]


async def test_hosted_submits_the_model_answer_without_oracle_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = _dispatch_view("fresh")
    environment = _seat("dispatch")
    world = _world_wire(monkeypatch, environment, view)
    router = Mock(side_effect=[_CATALOG, _ENDPOINTS, _response()])
    monkeypatch.setattr(seat_agent, "_openrouter", router)
    agent = _hosted(monkeypatch, tmp_path)
    context = AgentContext()

    await agent.run("private instruction", environment, context)

    # D is visible but worse than B. Keep the model's choice as submitted.
    assert world.call_args_list == [
        call("GET", "/view", body=None, headers={}),
        call(
            "POST",
            "/submit",
            body=b'{"rows":[{"id":0,"bay":"D"}]}',
            headers={
                "Content-Type": "application/json",
                "X-SeatLab-View-SHA256": view_hash(view),
            },
        ),
    ]
    assert router.call_count == 3
    for lookup in router.call_args_list[:2]:
        assert not lookup.kwargs
    request = router.call_args.kwargs["payload"]
    assert request["model"] == _MODEL
    assert request["provider"]["only"] == [_SLUG]
    assert request["provider"]["allow_fallbacks"] is False
    assert request["provider"]["max_price"] == {
        "prompt": "0",
        "completion": "0",
        "request": "0",
    }
    assert not {"models", "plugins", "tools"}.intersection(request)
    assert json.loads(request["messages"][1]["content"]) == view
    serialized_request = json.dumps(request)
    assert "private instruction" not in serialized_request
    assert str(tmp_path) not in serialized_request
    assert view_hash(view) not in serialized_request
    assert "X-SeatLab-View-SHA256" not in serialized_request
    assert context.metadata == {
        "schema": "seatlab.seat/v1",
        "policy": "hosted",
        "role": "dispatch",
        "decision_source": "model",
        "model_turns": 1,
        "requested_model": _MODEL,
        "served_model": _MODEL.removesuffix(":free"),
        "provider": _PROVIDER,
    }
    assert (
        context.n_input_tokens,
        context.n_output_tokens,
        context.n_cache_tokens,
    ) == (101, 22, 17)
    assert context.cost_usd == 0.0
    assert context.model_usage is not None
    model = agent.to_agent_info().model_info
    assert model is not None
    assert (model.provider, model.name) == ("openrouter", _MODEL)


@pytest.mark.parametrize("role", ["inventory", "dispatch"])
def test_oracle_posts_actions_with_view_digest(
    monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    if role == "inventory":
        view = _inventory_view()
        rows = [{"id": 0, "reports": _REPORTS}]
    else:
        view = _dispatch_view("reordered")
        rows = [{"id": 0, "bay": "B"}]
    http = create_autospec(urllib.request.OpenerDirector, instance=True)
    http.open.side_effect = [
        BytesIO(json.dumps(view, indent=2).encode("utf-8")),
        BytesIO(b'{"accepted":true}'),
    ]
    monkeypatch.setattr(urllib.request, "build_opener", Mock(return_value=http))
    task = Path(__file__).resolve().parents[2] / "tasks/dispatch-handoff"
    source = (task / "solution/solve.sh").read_text(encoding="utf-8")
    script = source.split("<<'PY'\n", 1)[1].removesuffix("\nPY\n")

    exec(script, {})

    assert http.open.call_count == 2
    assert http.open.call_args_list[0].args == ("http://world:8080/view",)
    (request,) = http.open.call_args.args
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == "http://world:8080/submit"
    assert request.get_method() == "POST"
    assert {name.lower(): value for name, value in request.header_items()} == {
        "content-type": "application/json",
        "x-seatlab-view-sha256": view_hash(view),
    }
    assert request.data is not None
    assert json.loads(request.data) == {"rows": rows}
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        request, None, 303, "See Other", {}, "http://world:8080/view"
    )
    assert redirected is not None
    assert redirected.get_method() == "GET"
    assert not redirected.has_header("X-seatlab-view-sha256")


@pytest.mark.parametrize(
    "failure",
    [
        "json",
        "duplicate",
        "receipt",
        "tool",
        "model",
        "provider",
        "request_cost",
        "error",
    ],
)
async def test_hosted_fails_without_a_second_model_turn_or_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    world = _world(monkeypatch, _dispatch_view("fresh"))
    response = _response()
    message = response["choices"][0]["message"]
    if failure == "json":
        message["content"] = '```json\n{"rows":[]}\n```'
    elif failure == "duplicate":
        message["content"] = '{"rows":[{"id":0,"bay":"D","bay":"B"}]}'
    elif failure == "receipt":
        message["content"] = json.dumps(
            {
                "rows": [{"id": 0, "bay": "D"}],
                "ack_sha256": view_hash(_dispatch_view("fresh")),
            }
        )
    elif failure == "tool":
        message["tool_calls"] = [{}]
    elif failure == "model":
        response["model"] = "some/other-model:free"
    elif failure == "provider":
        response["openrouter_metadata"]["endpoints"]["available"][0]["provider"] = (
            "Other"
        )
    elif failure == "request_cost":
        response["usage"]["cost"] = 0.00001
    else:
        response["error"] = {"message": "private provider error not for export"}
    router = Mock(side_effect=[_CATALOG, _ENDPOINTS, response])
    monkeypatch.setattr(seat_agent, "_openrouter", router)
    agent = _hosted(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError) as error:
        await agent.run("", _seat("dispatch"), AgentContext())

    assert "private provider error" not in str(error.value)
    assert world.await_count == 1
    assert router.call_count == 3


@pytest.mark.parametrize(
    "failure", ["missing_model", "nonzero", "endpoint_nonzero", "ambiguous"]
)
async def test_unverified_route_never_calls_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    _world(monkeypatch, _dispatch_view("fresh"))
    catalog = deepcopy(_CATALOG)
    endpoints = deepcopy(_ENDPOINTS)
    if failure == "missing_model":
        catalog["data"] = []
    elif failure == "nonzero":
        catalog["data"][0]["pricing"]["request"] = "0.001"
    elif failure == "endpoint_nonzero":
        endpoints["data"]["endpoints"][0]["pricing"]["internal_reasoning"] = "0.001"
    else:
        other = {**endpoints["data"]["endpoints"][0], "tag": f"{_SLUG}/fast"}
        endpoints["data"]["endpoints"].append(other)
    router = Mock(side_effect=[catalog, endpoints])
    monkeypatch.setattr(seat_agent, "_openrouter", router)
    agent = _hosted(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError):
        await agent.run("", _seat("dispatch"), AgentContext())

    assert all(not lookup.kwargs for lookup in router.call_args_list)


async def test_missing_hosted_charge_is_not_reported_as_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _world(monkeypatch, _dispatch_view("fresh"))
    response = _response()
    del response["usage"]["cost"]
    del response["usage"]["prompt_tokens_details"]
    monkeypatch.setattr(
        seat_agent, "_openrouter", Mock(side_effect=[_CATALOG, _ENDPOINTS, response])
    )
    context = AgentContext()

    await _hosted(monkeypatch, tmp_path).run("", _seat("dispatch"), context)

    assert context.cost_usd is None
    assert context.n_cache_tokens is None
    assert (context.n_input_tokens, context.n_output_tokens) == (101, 22)


async def test_malformed_world_view_is_not_sent_to_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = _dispatch_view("fresh")
    view["cases"][0]["hidden_state"] = ["A", "C"]
    world = _world(monkeypatch, view)
    router = Mock()
    monkeypatch.setattr(seat_agent, "_openrouter", router)

    with pytest.raises(RuntimeError, match="Malformed scoped world case"):
        await _hosted(monkeypatch, tmp_path).run("", _seat("dispatch"), AgentContext())

    router.assert_not_called()
    assert world.await_count == 1


async def test_failed_world_commit_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = AsyncMock(
        side_effect=[
            (200, json.dumps(_dispatch_view("fresh"))),
            (500, '{"error":"failed"}'),
        ]
    )
    monkeypatch.setattr(seat_agent, "_world_request", world)
    agent = seat_agent.SeatAgent(tmp_path, policy="arrival")

    with pytest.raises(RuntimeError, match="World decision rejected"):
        await agent.run("", _seat("dispatch"), AgentContext())

    assert world.await_count == 2


def test_openrouter_rejects_redirect_without_reattaching_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = Mock()
    connection.getresponse.return_value.status = 307
    connection.getresponse.return_value.headers = {"Location": "https://other.example/"}
    opener = Mock(return_value=connection)
    monkeypatch.setattr(seat_agent.http.client, "HTTPSConnection", opener)

    with pytest.raises(RuntimeError, match="HTTP 307"):
        seat_agent._openrouter(
            "/api/v1/chat/completions",
            payload={},
            key="test-key-not-a-credential",
        )

    opener.assert_called_once_with("openrouter.ai", timeout=55)
    connection.request.assert_called_once()
