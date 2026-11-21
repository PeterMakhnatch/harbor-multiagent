import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import Mock

import pytest

from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    agent = importlib.import_module("examples.seatlab.briefing.agent")
    route_run = importlib.import_module("examples.seatlab.visibility.route_run")
    route_cli = importlib.import_module("examples.seatlab.visibility.route_cli")

_ROLES = ("dispatch", "inventory")
_ROWS = [
    {
        "id": index,
        "reports": [
            {"revision": 1, "available": ["A", "B"]},
            {"revision": 2, "available": ["C", "D"] if index < 3 else ["A", "B"]},
        ],
    }
    for index in reversed(range(6))
]
_CARD = "DCDCAD"
_OPENED = ("Q2", "Q6")
_PUSH_REPLY = json.dumps({"open": list(_OPENED)})
_FINAL_REPLY = json.dumps(
    {"choices": dict(zip((f"Q{i}" for i in range(1, 7)), "DACBAD", strict=True))}
)
_REPLIES = {
    "inventory": json.dumps({"rows": _ROWS}) + "\n",
    "brief": json.dumps({"brief": _CARD}),
    "push": _PUSH_REPLY,
    "decide": _FINAL_REPLY,
}


def _world(monkeypatch: pytest.MonkeyPatch, *, refuse_inventory: bool = False):
    world_code = run_path(
        str(agent.native_pairs.run_panel.TASK_DIR / "environment" / "seatworld.py")
    )
    world = world_code["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": 6501,
            "condition": "reordered",
            "episodes": 6,
        }
    )
    requests: list[tuple[str, str]] = []
    commits: set[str] = set()
    inventory_ready = asyncio.Event()

    async def request(
        environment: SeatEnvironment,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        view_sha256: str | None = None,
    ) -> tuple[int, str]:
        role = environment.service
        assert (role, path) not in requests
        requests.append((role, path))
        if path == "/view":
            if role == "dispatch":
                await inventory_ready.wait()
            return 200, json.dumps(world.view(role))
        assert path == "/submit" and view_sha256 == agent.view_hash(world.view(role))
        if role == "inventory" and refuse_inventory:
            return 409, '{"accepted":false}'
        accepted = world.submit(role, payload)
        commits.add(role)
        if role == "inventory":
            inventory_ready.set()
        return 200, json.dumps(accepted)

    monkeypatch.setattr(agent.seat_agent, "_world_request", request)
    return world, requests, commits


def _watch_relay(monkeypatch: pytest.MonkeyPatch, commits: set[str]):
    open_port = agent.channel.open_port
    sends: list[tuple[str, str, object]] = []

    @asynccontextmanager
    async def watch(channel_id: str, role: str):
        async with open_port(channel_id, role) as port:

            async def send(slot: str, value: str | tuple[str, str] | None) -> None:
                sends.append((role, slot, value))
                await port.send(slot, value)

            async def receive(slot: Literal["brief", "push"]):
                if role == "inventory":
                    assert "inventory" in commits
                return await port.receive(slot)

            yield SimpleNamespace(send=send, receive=receive)

    monkeypatch.setattr(agent.channel, "open_port", watch)
    return sends


def _model(monkeypatch: pytest.MonkeyPatch):
    def respond(prompt: str, run: str, role: str):
        stage = run.rsplit("-", 1)[1]
        return route_run.Answer(_REPLIES[stage], "deepseek-flash", 11, 2, 3)

    direct = Mock(side_effect=respond)
    monkeypatch.setattr(route_cli, "_direct", direct)
    return direct


async def _play(
    tmp_path: Path, cell_id: str, contexts: dict[str, AgentContext]
) -> None:
    channel_id = f"seatlab-team-{sha256(str(tmp_path).encode()).hexdigest()[:24]}"
    async with asyncio.timeout(5), asyncio.TaskGroup() as group:
        for role in _ROLES:
            env = Mock(spec=SeatEnvironment)
            env.service = role
            seat = agent.BriefingSeat(
                tmp_path, cell_id=cell_id, channel_id=channel_id, transport="direct"
            )
            seat.session_id = f"{channel_id}__{role}"
            group.create_task(seat.run("", cast(SeatEnvironment, env), contexts[role]))


@pytest.mark.parametrize("arm", ["brief", "masked"])
async def test_briefing_gates_header_and_submits_final_native_choices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str
) -> None:
    world, requests, commits = _world(monkeypatch)
    sends = _watch_relay(monkeypatch, commits)
    direct = _model(monkeypatch)
    cell_id = f"brf-s6501-r0-{arm}"
    contexts = {role: AgentContext() for role in _ROLES}
    await _play(tmp_path, cell_id, contexts)

    assert set(requests) == {
        (role, path) for role in _ROLES for path in ("/view", "/submit")
    }
    released = _CARD if arm == "brief" else None
    assert sends == [("dispatch", "brief", released), ("inventory", "push", _OPENED)]
    chronological = [
        ("inventory", "inventory"),
        ("brief", "dispatch"),
        ("push", "inventory"),
        ("decide", "dispatch"),
    ]
    prompts = {}
    for call, (stage, role) in zip(direct.call_args_list, chronological, strict=True):
        prompt, run, actual_role = call.args
        assert (run, actual_role) == (f"{cell_id}-{stage}", role)
        prompts[stage] = prompt
    assert prompts["push"] == agent.protocol.push_prompt(_ROWS, 6501, 0, released)
    assert (_CARD in prompts["push"]) is (arm == "brief")
    dispatch_cases = world.view("dispatch")["cases"]
    assert prompts["decide"] == agent.attention.decide_prompt(
        dispatch_cases, 6501, 0, _OPENED
    )
    assert _CARD not in prompts["decide"]
    evidence = world.evidence()
    assert evidence["complete"] is True
    expected = agent.attention.decide(_FINAL_REPLY, dispatch_cases, 6501, 0)
    assert [case["chosen"] for case in evidence["cases"]] == [
        row["bay"] for row in expected["rows"]
    ]
    assert [case["reports"] for case in evidence["cases"]] == [
        row["reports"] for row in reversed(_ROWS)
    ]
    for role, key, value in (
        ("inventory", "brief_seen", released),
        ("dispatch", "brief", _CARD),
    ):
        context = contexts[role]
        metadata = context.metadata
        assert metadata is not None and metadata["stage"] == "complete"
        assert metadata[key] == value and metadata["opened"] == list(_OPENED)
        assert metadata["view_sha256"] == agent.view_hash(world.view(role))
        assert context.n_input_tokens == 22
        assert [turn["stage"] for turn in metadata["turns"]] == [
            stage for stage, turn_role in chronological if turn_role == role
        ]
        for turn in metadata["turns"]:
            stage = turn["stage"]
            assert turn["text"] == _REPLIES[stage]
            assert turn["prompt_sha256"] == sha256(prompts[stage].encode()).hexdigest()


async def test_refused_inventory_never_receives_brief_or_pushes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world, requests, commits = _world(monkeypatch, refuse_inventory=True)
    sends = _watch_relay(monkeypatch, commits)
    direct = _model(monkeypatch)
    cell_id = "brf-s6501-r0-brief"
    contexts = {role: AgentContext() for role in _ROLES}
    with pytest.raises(ExceptionGroup) as failure:
        await _play(tmp_path, cell_id, contexts)
    assert len(failure.value.exceptions) == 1
    error = failure.value.exceptions[0]
    assert isinstance(error, agent.seat_agent._Unmeasured)
    assert str(error) == "Native briefing seat: submission_unacknowledged"
    direct.assert_called_once()
    assert direct.call_args.args[1:] == (f"{cell_id}-inventory", "inventory")
    assert sends == [] and commits == set()
    assert ("inventory", "/submit") in requests and (
        "dispatch",
        "/submit",
    ) not in requests
    assert world.evidence()["complete"] is False
    metadata = contexts["inventory"].metadata
    assert metadata is not None and metadata["stage"] == "submission_unacknowledged"
    assert metadata["brief_seen"] is None and metadata["opened"] == []
    assert [turn["stage"] for turn in metadata["turns"]] == ["inventory"]
