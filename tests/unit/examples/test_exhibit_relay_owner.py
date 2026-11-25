import asyncio
import json
import os
import shlex
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from importlib import import_module
from pathlib import Path
from typing import Any, override

import pytest

from examples.seatlab.exhibit_owner import RelayOwner
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_TASK = "examples.tasks.exhibit-relay.environment.exhibit_relay"
make_case = import_module(f"{_TASK}.cases").make_case
Session = import_module(f"{_TASK}.engine").Session
grade = import_module(f"{_TASK}.grading").grade


class SessionSeat(SeatEnvironment):
    def __init__(self, session, role: str):
        self.session = session
        self.service = role
        self.commands = []

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        assert cwd is None and env is None and user is None
        prefix, script, op, *args = shlex.split(command)
        assert (prefix, script) == ("python3", "/app/relay.py")
        self.commands.append(op)
        if op in ("wait", "view"):
            for _ in range(1000):
                view = self.session.view(self.service)
                if op == "view" or view["phase"] == args[0]:
                    break
                await asyncio.sleep(0.005)
            else:
                raise AssertionError(f"Phase not reached: {args}")
        else:
            if op == "request":
                action = {"op": op, "slips": args}
            elif op == "file":
                action = {"op": op, "ids": args}
            else:
                assert op == "commit"
                action = {"op": op, "routes": dict(pair.split("=") for pair in args)}
            try:
                view = self.session.apply(self.service, action)
            except ValueError:
                return ExecResult(return_code=1, stderr="invalid request")
        return ExecResult(return_code=0, stdout=json.dumps(view))


def listener(stack: ExitStack, root: Path, name: str) -> socket.socket:
    conn = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
    path = root / name
    conn.bind(str(path))
    path.chmod(0o600)
    conn.listen(1)
    conn.settimeout(8)
    return conn


def current_value(cards: list[dict[str, Any]], key: str, epoch: int) -> str:
    matches = [
        card["value"]
        for card in cards
        if card["key"] == key and card["start"] <= epoch <= card["end"]
    ]
    assert len(matches) == 1
    return matches[0]


def policy(payload: dict[str, Any]) -> dict[str, Any]:
    view = payload["view"]
    phase = view["phase"]
    if phase == "request":
        return {"op": "request", "slips": [view["briefs"][0]["id"]]}
    if phase in ("registrar", "depot"):
        return {"op": "file", "ids": sorted(card["id"] for card in view["cards"])}
    order = view["order"]
    routes = {}
    for item in order["items"]:
        crate = current_value(view["registrar_file"], item, order["epoch"])
        routes[item] = current_value(view["depot_file"], crate, order["epoch"])
    return {"op": "commit", "routes": routes}


def serve_once(
    listener: socket.socket, select=policy
) -> tuple[dict[str, Any], dict[str, Any]]:
    with listener.accept()[0] as conn:
        conn.settimeout(5)
        with conn.makefile("rb") as stream:
            line = stream.readline(65537)
        assert line.endswith(b"\n") and len(line) <= 65536
        frame = json.loads(line)
        assert frame.keys() == {"schema", "prompt"}
        assert frame["schema"] == "seatlab.seat-turn/v1"
        payload = json.loads(frame["prompt"].split("\nInput JSON: ", 1)[1])
        action = select(payload)
        conn.sendall((json.dumps({"text": json.dumps(action)}) + "\n").encode())
    listener.close()
    return payload, action


@pytest.fixture
def socket_root():
    with tempfile.TemporaryDirectory(prefix="relay-", dir="/tmp") as directory:
        yield Path(directory).resolve()


def test_owner_relay_four_distinct_turns(socket_root: Path):
    session = Session(make_case(41), "open")
    names = ("request", "registrar", "depot", "dispatch")
    roles = ("loader", "registrar", "depot")
    with ExitStack() as stack:
        listeners = [listener(stack, socket_root, name) for name in names]
        assert len({os.stat(sock.getsockname()).st_ino for sock in listeners}) == 4
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=4))
        futures = [pool.submit(serve_once, sock) for sock in listeners]
        owners = [
            RelayOwner(
                socket_root,
                role=role,
                owner_sockets=[str(socket_root / name) for name in turns],
            )
            for role, turns in zip(
                roles,
                (("request", "dispatch"), ("registrar",), ("depot",)),
                strict=True,
            )
        ]
        seats = [SessionSeat(session, role) for role in roles]
        contexts = [AgentContext() for _ in roles]

        async def run_team():
            for owner, seat in zip(owners, seats, strict=True):
                await owner.setup(seat)
            await asyncio.wait_for(
                asyncio.gather(
                    *(
                        owner.run(f"Instruction for {role}", seat, ctx)
                        for role, owner, seat, ctx in zip(
                            roles, owners, seats, contexts, strict=True
                        )
                    )
                ),
                10,
            )

        asyncio.run(run_team())
        turns = [future.result(timeout=2) for future in futures]
    request, registrar, depot, dispatch = [payload for payload, _ in turns]
    actions = [action for _, action in turns]
    assert [payload["view"]["phase"] for payload, _ in turns] == list(names)
    assert [payload["instruction"] for payload, _ in turns] == [
        "Instruction for loader",
        "Instruction for registrar",
        "Instruction for depot",
        "Instruction for loader",
    ]
    assert [
        request["previous_action"],
        registrar["previous_action"],
        depot["previous_action"],
    ] == [None, None, None]
    assert dispatch["previous_action"] == actions[0]
    assert request["view"]["cards"] == dispatch["view"]["cards"] == []
    assert registrar["view"]["depot_file"] == depot["view"]["registrar_file"] == []
    assert depot["view"]["briefs"] == []
    metadata = [ctx.metadata for ctx in contexts if ctx.metadata is not None]
    assert len(metadata) == len(contexts)
    assert [entry["accepted_actions"] for entry in metadata] == [
        [actions[0], actions[3]],
        [actions[1]],
        [actions[2]],
    ]
    assert all(entry["phase"] == "finished" for entry in metadata)
    cut = session.snapshot()
    assert [event["action"] for event in cut["events"]] == actions
    result = grade(cut)
    assert result["status"] == "complete" and result["reward"] == 1
    assert result["dispatch_supported"] == 2


def test_owner_rejects_invalid_role_and_action(socket_root: Path):
    session = Session(make_case(41), "linked")
    with ExitStack() as stack:
        first = listener(stack, socket_root, "request")
        listener(stack, socket_root, "dispatch")
        paths = [str(socket_root / turn) for turn in ("request", "dispatch")]
        with pytest.raises(ValueError):
            RelayOwner(socket_root, role="unknown", owner_sockets=paths)
        with pytest.raises(ValueError):
            RelayOwner(socket_root, role="loader", owner_sockets=[paths[0], paths[0]])
        owner = RelayOwner(socket_root, role="loader", owner_sockets=paths)
        wrong = SessionSeat(session, "depot")
        with pytest.raises(ValueError):
            asyncio.run(owner.setup(wrong))
        ctx = AgentContext()
        with pytest.raises(ValueError):
            asyncio.run(owner.run("wrong seat", wrong, ctx))
        assert wrong.commands == []
        seat = SessionSeat(session, "loader")
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=1))
        future = pool.submit(serve_once, first, lambda _: {"op": "file", "ids": []})
        with pytest.raises(ValueError):
            asyncio.run(owner.run("invalid action", seat, ctx))
        future.result(timeout=2)
        assert seat.commands == ["view"]
        assert ctx.metadata is not None and ctx.metadata["accepted_actions"] == []
    cut = session.snapshot()
    assert cut["events"] == []
    assert grade(cut)["reward"] is None
