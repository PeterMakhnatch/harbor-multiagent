import asyncio
import http.client
import json
import os
import shlex
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import replace
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from examples.seatlab.visibility import (
    boxes_choice,
    intent_packet,
    intent_protocol,
    ownership_protocol,
    ownership_seat,
    ownership_source,
)
from examples.seatlab.visibility.boxes_packet import Case
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_SEATS = ("p1", "p2", "p3")
_BLOCK = intent_protocol.Block(1, 1, 2, 0)
_PROPOSALS = (10, 10, 33)
_LABEL_ORDER = ("p2", "p3", "p1")
_PRIVATE = "test-only replay message"
_MOVES = (
    ("p2", "msg", f"{_PRIVATE} 1:\n'café'"),
    ("p3", "open", 22),
    ("p1", "open", 1),
    ("p1", "msg", f"{_PRIVATE} 2"),
    ("p2", "open", 60),
    ("p1", "open", 2),
    ("p2", "open", 31),
    ("p3", "msg", f"{_PRIVATE} 3"),
    ("p2", "open", 31),
    *((_SEATS[i % 3], "msg", f"{_PRIVATE} {i}") for i in range(4, 10)),
)


def _observation(box: int) -> tuple[int, int, bool]:
    room = (box - 1) // 10 + 1
    return box, room, room in (1, 4)


class _World:
    """In-memory stock HTTP boundary. Run the actual seat-side client script."""

    def __init__(self, task: Path, arm: str) -> None:
        self.project = SimpleNamespace(
            trial_paths=SimpleNamespace(
                trial_dir=task.parent
                / "trials"
                / ownership_protocol.run_id(_BLOCK, arm)
            ),
            environment_dir=task / "environment",
        )
        self.history: dict[str, list[dict[str, Any]]] = {seat: [] for seat in _SEATS}
        self.messages: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.mismatch: str | None = None

    def _request(self, seat: str, method: str, path: str, body: Any) -> dict[str, Any]:
        self.requests.append((seat, method, path, body))
        if path == "/rules":
            assert method == "GET" and body is None
            left = 9 - len(self.history[seat])
            if self.mismatch == "rules" and left < 9 and seat == "p3":
                left += 1
            return {
                "you": seat,
                "seats": list(_SEATS),
                "channel": True,
                "budget_left": {"open": left},
            }
        if path == "/mine":
            assert method == "GET" and body is None
            opens = [dict(row) for row in self.history[seat]]
            if self.mismatch == "mine" and seat == "p3":
                opens[0]["budget_left"] = 8
            return {"opens": opens}
        if path == "/msgs":
            assert method == "GET" and body is None
            messages = [dict(row) for row in self.messages]
            if self.mismatch == "msgs" and seat == "p3":
                messages[-1]["text"] += " mismatch"
            return {"messages": messages}
        assert method == "POST"
        if path == "/msg":
            assert body.keys() == {"text"}
            message = {"from": seat, "id": len(self.messages) + 1, "text": body["text"]}
            self.messages.append(message)
            return message
        assert path == "/open" and body.keys() == {"box"}
        box, room, flag = _observation(body["box"])
        opened = {"box": box, "room": room, "flag": flag}
        self.history[seat].append(opened)
        return {
            **opened,
            "budget_left": (8 if self.mismatch == "open" else 9)
            - len(self.history[seat]),
        }

    def environment(
        self, seat: str, *, token_seat: str | None = None
    ) -> SeatEnvironment:
        environment = Mock(spec=SeatEnvironment)
        environment.service = seat
        environment.project = self.project
        auth = token_seat or seat
        token = f"test-only-token-for-{auth}"

        def connect(host: str, port: int, *, timeout: int) -> SimpleNamespace:
            assert (host, port) == ("world", 8080) and timeout > 0

            def request(
                method: str, path: str, body: bytes | None, headers: dict[str, str]
            ) -> None:
                assert headers["Authorization"] == f"Bearer {token}"
                if body is not None:
                    assert headers["Content-Type"] == "application/json"
                reply = self._request(
                    auth, method, path, None if body is None else json.loads(body)
                )
                connection.getresponse = lambda: SimpleNamespace(
                    status=200, read=BytesIO(json.dumps(reply).encode()).read
                )

            connection = SimpleNamespace(request=request, close=lambda: None)
            return connection

        async def exec_command(
            command: str, *, env: dict[str, str], timeout_sec: int
        ) -> ExecResult:
            argv = shlex.split(command)
            assert argv[:3] == ["python3", "-I", "-c"] and len(argv) == 4
            assert timeout_sec > 0
            assert env.keys() <= {
                "SEATLAB_BOX_OP",
                "SEATLAB_BOX_EXPECTED",
                "SEATLAB_BOX_BODY",
            }
            assert (
                _PRIVATE not in command
                and token not in command
                and token not in repr(env)
            )
            sandbox = {**env, "WORLD": "http://world:8080", "SEAT_TOKEN": token}
            code = 0
            with (
                patch.object(os, "environ", sandbox),
                patch.object(http.client, "HTTPConnection", connect),
                redirect_stdout(StringIO()) as output,
            ):
                try:
                    exec(compile(argv[3], "<stock-seat>", "exec"), {})  # noqa: S102
                except SystemExit as error:
                    assert error.code == 1
                    code = 1
            assert output.getvalue() in ("ok\n", "mismatch\n", "")
            return ExecResult(stdout=output.getvalue(), return_code=code)

        environment.exec.side_effect = exec_command
        return cast(SeatEnvironment, environment)


@pytest.fixture
def source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ownership_source.Cut, intent_packet.World]:
    actions = []
    seen: dict[str, list[tuple[int, int, bool]]] = {seat: [] for seat in _SEATS}
    message_id = 0
    for seat, kind, value in _MOVES:
        if kind == "open":
            assert isinstance(value, int)
            box, room, flag = _observation(value)
            seen[seat].append((box, room, flag))
            body = {"box": box}
            reply = {
                **body,
                "room": room,
                "flag": flag,
                "budget_left": 9 - len(seen[seat]),
            }
        else:
            assert isinstance(value, str)
            message_id += 1
            body = {"text": value}
            reply = {**body, "from": seat, "id": message_id}
        actions.append(ownership_source.Action(seat, kind, body, reply))

    def case(seat: str) -> Case:
        peers = {
            box
            for peer, history in seen.items()
            if peer != seat
            for box, _, _ in history
        }
        return Case(
            1, tuple(seen[seat]), tuple(sorted(peers)), 9 - len(seen[seat]), True
        )

    packet = intent_packet.World(1, (case("p1"), case("p2"), case("p3")))
    monkeypatch.setattr(intent_packet, "load", lambda: {1: packet})
    cut = ownership_source.Cut(
        instance=1,
        task=tmp_path / "boxes-i01",
        actions=tuple(actions),
        task_checksum="1" * 64,
        task_digest="sha256:" + "2" * 64,
        prefix_sha256=ownership_source.prefix_sha256(tuple(actions)),
        baseline_flags=3,
    )
    return cut, packet


def _prefix(
    cut: ownership_source.Cut,
) -> list[tuple[str, str, str, dict[str, Any] | None]]:
    return [
        *((seat, "GET", "/rules", None) for seat in _SEATS),
        *(
            (action.seat, "POST", f"/{action.kind}", action.request)
            for action in cut.actions
        ),
        *(
            (seat, "GET", path, None)
            for seat in _SEATS
            for path in ("/mine", "/rules", "/msgs")
        ),
    ]


def _run_seats(cut: ownership_source.Cut, environments: dict[str, SeatEnvironment]):
    contexts = {seat: AgentContext() for seat in _SEATS}
    tasks = {
        seat: asyncio.create_task(
            ownership_seat.OwnershipSeat(cut.task.parent / seat, seat=seat).run(
                "unused instructions", environments[seat], contexts[seat]
            )
        )
        for seat in ("p3", "p1", "p2")
    }
    return tasks, contexts


async def _run(
    cut: ownership_source.Cut,
    arm: str,
    environments: dict[str, SeatEnvironment],
    respond: Callable[[str, str], str],
):
    with ownership_seat.bind(cut, _BLOCK, arm, _PROPOSALS, respond):
        tasks, contexts = _run_seats(cut, environments)
        outcomes = await asyncio.wait_for(
            asyncio.gather(*tasks.values(), return_exceptions=True), timeout=5
        )
    return contexts, outcomes


@pytest.mark.parametrize(
    ("arm", "invalid"), [("neutral", False), ("owner", False), ("owner", True)]
)
async def test_stock_prefix_and_sealed_turn_order(source, arm: str, invalid: bool):
    cut, packet = source
    world = _World(cut.task, arm)
    prefix = _prefix(cut)
    contexts = []
    originals = {"p1": 43, "p2": 15, "p3": 58}
    if arm == "owner":
        # The policy does not make a known box or a colliding final illegal.
        originals = {"p1": 1, "p2": 15, "p3": 15}
    texts = {
        seat: json.dumps({"box": boxes_choice.map_box(box, _BLOCK.numbering)}, indent=2)
        for seat, box in originals.items()
    }
    if invalid:
        texts["p3"] = '{"box":true}'

    def respond(seat: str, prompt: str) -> str:
        assert ownership_seat._ACTIVE.get() is None
        assert world.requests == prefix
        label = len(contexts)
        assert seat == _LABEL_ORDER[label]
        assert prompt == ownership_protocol.prompt(
            packet, _BLOCK, label, arm, _PROPOSALS
        )
        assert _PRIVATE not in prompt
        assert all(text not in prompt for text in texts.values())
        contexts.append((seat, prompt))
        return texts[seat]

    native, outcomes = await _run(
        cut, arm, {seat: world.environment(seat) for seat in _SEATS}, respond
    )
    assert outcomes == [None, None, None]
    assert tuple(seat for seat, _ in contexts) == _LABEL_ORDER
    expected_commits = [
        (seat, "POST", "/open", {"box": originals[seat]}) for seat in _SEATS
    ]
    assert world.requests == prefix + ([] if invalid else expected_commits)
    prompts = dict(contexts)
    for seat, context in native.items():
        own_invalid = invalid and seat == "p3"
        status = (
            "model_invalid" if own_invalid else "peer_invalid" if invalid else "opened"
        )
        assert context.metadata == {
            "box_ownership": {
                "schema": "seatlab.box-ownership-seat/v1",
                "seat": seat,
                "run": ownership_protocol.run_id(_BLOCK, arm),
                "prefix_sha256": cut.prefix_sha256,
                "status": status,
                "prompt_sha256": sha256(prompts[seat].encode()).hexdigest(),
                "response_sha256": sha256(texts[seat].encode()).hexdigest(),
                "original_box": None if own_invalid else originals[seat],
            }
        }
        assert context.model_usage is None and context.cost_usd is None


@pytest.mark.parametrize(
    "wrong", ["service", "project", "source", "token", "mine", "msgs", "rules", "open"]
)
async def test_mixed_or_changed_live_source_never_calls_the_model(source, wrong: str):
    cut, _ = source
    world = _World(cut.task, "owner")
    environments = {seat: world.environment(seat) for seat in _SEATS}
    if wrong == "service":
        environments["p3"] = world.environment("p2")
    elif wrong == "project":
        environments["p3"] = _World(cut.task, "owner").environment("p3")
    elif wrong == "source":
        environments["p3"] = _World(
            cut.task.with_name("other-source"), "owner"
        ).environment("p3")
    elif wrong == "token":
        environments["p3"] = world.environment("p3", token_seat="p1")
    else:
        world.mismatch = wrong
    calls = []

    def respond(seat: str, prompt: str) -> str:
        calls.append(seat)
        return '{"box":1}'

    native, outcomes = await _run(cut, "owner", environments, respond)
    assert not calls
    assert all(context.metadata is None for context in native.values())
    category = {
        "service": "wrong_seat_environment",
        "project": "wrong_trial_environment",
        "source": "wrong_trial_environment",
    }.get(wrong, "live_source_mismatch")
    assert any(str(outcome) == category for outcome in outcomes)
    if wrong in ("service", "project", "source", "token"):
        assert not any(method == "POST" for _, method, _, _ in world.requests)


async def test_unbound_actor_cannot_use_the_seat(source):
    cut, _ = source
    world = _World(cut.task, "owner")
    seat = ownership_seat.OwnershipSeat(cut.task.parent / "p1", seat="p1")
    with pytest.raises(RuntimeError, match="^trial_unbound$"):
        await seat.run("", world.environment("p1"), AgentContext())
    assert not world.requests


def test_changed_prefix_order_cannot_bind(source):
    cut, _ = source
    actions = cut.actions
    changed = replace(cut, actions=(actions[1], actions[0], *actions[2:]))
    with (
        pytest.raises(RuntimeError, match="^source_mismatch$"),
        ownership_seat.bind(changed, _BLOCK, "owner", _PROPOSALS, Mock()),
    ):
        pytest.fail("accepted a different interleaving with the original digest")


async def test_callback_failure_is_redacted_and_does_not_retry(source):
    cut, _ = source
    world = _World(cut.task, "owner")
    calls = []

    def respond(seat: str, prompt: str) -> str:
        calls.append(seat)
        if seat == "p3":
            raise ValueError(_PRIVATE)
        return '{"box":10}'

    native, outcomes = await _run(
        cut, "owner", {seat: world.environment(seat) for seat in _SEATS}, respond
    )
    assert calls == ["p2", "p3"]
    assert world.requests == _prefix(cut)
    assert all(context.metadata is None for context in native.values())
    assert any(str(outcome) == "model_transport_failed" for outcome in outcomes)
    assert all(_PRIVATE not in str(outcome) for outcome in outcomes)


async def test_cancelling_one_waiter_releases_the_other_seats(source):
    cut, _ = source
    world = _World(cut.task, "owner")
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = Event()
    calls = []

    def respond(seat: str, prompt: str) -> str:
        calls.append(seat)
        loop.call_soon_threadsafe(entered.set)
        release.wait(5)
        return '{"box":10}'

    with ownership_seat.bind(cut, _BLOCK, "owner", _PROPOSALS, respond):
        tasks, contexts = _run_seats(
            cut, {seat: world.environment(seat) for seat in _SEATS}
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            tasks["p1"].cancel()
            _, pending = await asyncio.wait(tasks.values(), timeout=2)
            assert not pending
            assert all(task.cancelled() for task in tasks.values())
        finally:
            release.set()
    assert calls == ["p2"]
    assert world.requests == _prefix(cut)
    assert all(context.metadata is None for context in contexts.values())
