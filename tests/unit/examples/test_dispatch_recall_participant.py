import asyncio
import json
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from importlib import import_module
from pathlib import Path
from typing import Any, override

import pytest

from examples.native_study import native
from examples.seatlab.dispatch_recall.native import Session
from examples.seatlab.dispatch_recall.participant import (
    DECISIONS,
    IMPORT_PATH,
    TURNS,
    RecallParticipant,
    kwargs,
    roster,
)
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from tests.unit.examples.test_exhibit_relay_owner import listener, serve_once

TASK = Path(__file__).resolve().parents[3] / "examples/tasks/dispatch-recall"
CLIENT = TASK / "environment/seat/recall.py"
SERVER = "examples.tasks.dispatch-recall.environment.recall_server"
Family = import_module(SERVER).Family
SEED = 7
COMMIT_TICKET = "c0" * 16
CLOSE_TICKET = "d1" * 16
ORDER = (
    ("loader", "stage"),
    ("depot", "report"),
    ("loader", "commit"),
    ("loader", "close"),
)


class RecallSeat(SeatEnvironment):
    def __init__(self, root: Path, role: str, service: str):
        self.service = service
        self.endpoint = str(root / "s" / role / "rpc.sock")
        self.commands: list[list[str]] = []

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
        prefix, script, *args = shlex.split(command)
        assert (prefix, script) == ("python3", "/app/recall.py")
        self.commands.append(args)
        return await asyncio.to_thread(self._client, args, timeout_sec)

    def _client(self, args: list[str], timeout_sec: int | None) -> ExecResult:
        result = subprocess.run(
            [sys.executable, "-BS", str(CLIENT), *args],
            env={"RECALL_SEAT_SOCKET": self.endpoint},
            check=False,
            capture_output=True,
            text=True,
            timeout=min(timeout_sec or 8, 8),
        )
        return ExecResult(
            return_code=result.returncode, stdout=result.stdout, stderr=result.stderr
        )


def instructions() -> dict[str, str]:
    return {
        role: (TASK / "instructions" / f"{role}.md").read_text(encoding="utf-8")
        for role in DECISIONS
    }


def owners(
    silent: bool, commit: dict[str, Any] | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def decide(payload: dict[str, Any]) -> dict[str, Any]:
        view = payload["view"]
        turn = view["turn"]
        if turn == "stage":
            return {"op": "stage", "bay": view["initial_bay"]}
        if turn == "report":
            if silent:
                return {"op": "report", "silent": True}
            return {"op": "report", "bay": view["current_bay"]}
        if turn == "commit":
            if commit is not None:
                return commit
            notice = view["notice"]
            return {
                "op": "commit",
                "bay": view["initial_bay"] if notice == "--" else notice,
            }
        return {"op": "close"}

    return decide


async def run_team(participants, seats, contexts, texts):
    for role, participant in participants.items():
        await participant.setup(seats[role])
    async with asyncio.timeout(20):
        async with asyncio.TaskGroup() as group:
            for role, participant in participants.items():
                group.create_task(
                    participant.run(texts[role], seats[role], contexts[role])
                )


def play(root: Path, session, decide, served=TURNS):
    texts = instructions()
    with ExitStack() as stack:
        stack.enter_context(Family(root / "s", root / "c", session))
        sockets = {turn: listener(stack, root, turn) for turn in TURNS}
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=4))
        futures = {
            turn: pool.submit(serve_once, sockets[turn], decide) for turn in served
        }
        endpoints = {turn: str(root / turn) for turn in TURNS}
        participants = {
            role: RecallParticipant(root, **kwargs(role, endpoints))
            for role in DECISIONS
        }
        seats = {role: RecallSeat(root, role, role) for role in DECISIONS}
        contexts = {role: AgentContext() for role in DECISIONS}
        failure = None
        try:
            asyncio.run(run_team(participants, seats, contexts, texts))
        except ExceptionGroup as group:
            failure = group
        turns = {turn: future.result(timeout=8) for turn, future in futures.items()}
        unserved = {}
        for turn in set(TURNS) - set(served):
            sockets[turn].setblocking(False)
            try:
                sockets[turn].accept()[0].close()
                unserved[turn] = "contacted"
            except BlockingIOError:
                unserved[turn] = "idle"
        cut = session.snapshot()
    return texts, turns, unserved, seats, contexts, cut, failure


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix="dr-", dir="/tmp") as directory:
        yield Path(directory).resolve()


@pytest.mark.parametrize(
    ("timing", "transition", "silent", "reward"),
    [
        ("immediate", "move", False, 1),
        ("deferred", "move", False, 0),
        ("withheld", "move", False, 0),
        ("immediate", "stay", True, 1),
    ],
)
def test_four_owner_decisions_project_only_own_role_history(
    root, timing, transition, silent, reward
):
    session = Session(SEED, transition, timing, tickets=(COMMIT_TICKET, CLOSE_TICKET))
    texts, turns, _, seats, contexts, cut, failure = play(root, session, owners(silent))
    assert failure is None
    assert texts["loader"] != texts["depot"]
    report = None if silent else cut["current_bay"]
    delivered = "--" if report is None else report
    commit_notice = delivered if timing == "immediate" else "--"
    close_notice = delivered if timing in ("immediate", "deferred") else "--"
    payloads = {turn: turns[turn][0] for turn in TURNS}
    actions = {turn: turns[turn][1] for turn in TURNS}
    for role, turn in ORDER:
        payload = payloads[turn]
        view = payload["view"]
        assert payload.keys() == {"instruction", "view", "history"}
        assert payload["instruction"] == texts[role]
        assert view["role"] == role and view["turn"] == turn and view["active"]
        assert "ticket" not in view
        assert ("current_bay" in view) == (role == "depot")
        assert ("initial_bay" in view) == (role == "loader")
    assert payloads["commit"]["view"]["notice"] == commit_notice
    assert payloads["close"]["view"]["notice"] == close_notice
    assert payloads["report"]["history"] == payloads["stage"]["history"] == []
    stage = {"view": payloads["stage"]["view"], "action": actions["stage"]}
    commit = {"view": payloads["commit"]["view"], "action": actions["commit"]}
    assert payloads["commit"]["history"] == [stage]
    assert payloads["close"]["history"] == [stage, commit]
    frames = b"".join(json.dumps(turns[t][0]).encode() for t in TURNS)
    for secret in (COMMIT_TICKET, CLOSE_TICKET, timing):
        assert secret.encode() not in frames
    committed = report if timing == "immediate" and report else cut["initial_bay"]
    assert cut["actions"] == [
        {"role": "loader", "op": "stage", "bay": cut["initial_bay"]},
        {"role": "depot", "op": "report", "bay": report},
        {"role": "loader", "op": "commit", "bay": committed},
        {"role": "loader", "op": "close"},
    ]
    assert cut["reward"] == reward == int(committed == cut["current_bay"])
    assert cut["windows"] == {
        "commit": {"notice": commit_notice, "redeemed": True},
        "close": {"notice": close_notice, "redeemed": True},
    }
    assert seats["loader"].commands == [
        ["wait", "stage"],
        ["stage", cut["initial_bay"]],
        ["wait", "commit"],
        ["commit", committed, COMMIT_TICKET],
        ["wait", "close"],
        ["close", CLOSE_TICKET],
    ]
    assert seats["depot"].commands == [
        ["wait", "report"],
        ["report", "--silent"] if silent else ["report", report],
    ]
    for role, context in contexts.items():
        metadata = context.metadata
        assert metadata == {
            "role": role,
            "accepted_actions": [actions[t] for t in DECISIONS[role]],
            "accepted_requests": [turns[t][2] for t in DECISIONS[role]],
            "phase": "finished",
        }
        shown = json.dumps(metadata)
        assert COMMIT_TICKET not in shown and CLOSE_TICKET not in shown


@pytest.mark.parametrize(
    "commit",
    [
        {"op": "commit", "bay": "--"},
        {"op": "commit", "bay": "ZZZ"},
        {"op": "commit", "bay": None},
        {"op": "commit"},
        {"op": "commit", "bay": "TICKET", "ticket": COMMIT_TICKET},
        {"op": "stage", "bay": "BAY"},
        {"op": "close"},
    ],
)
def test_unsafe_commit_reply_stops_without_fallback(root, commit):
    session = Session(SEED, "move", "immediate", tickets=(COMMIT_TICKET, CLOSE_TICKET))
    bays = session.snapshot()["bays"]
    commit = {
        key: bays[0] if value in ("TICKET", "BAY") else value
        for key, value in commit.items()
    }
    _, turns, unserved, seats, contexts, cut, failure = play(
        root, session, owners(False, commit), served=("stage", "report", "commit")
    )
    assert failure is not None
    assert all(isinstance(error, ValueError) for error in failure.exceptions)
    assert turns["commit"][1] == commit
    assert unserved == {"close": "idle"}
    assert [action["op"] for action in cut["actions"]] == ["stage", "report"]
    assert cut["reward"] is None
    assert cut["windows"]["commit"] == {"notice": cut["current_bay"], "redeemed": False}
    assert seats["loader"].commands[-1] == ["wait", "commit"]
    assert contexts["loader"].metadata["phase"] == "commit"
    assert len(contexts["loader"].metadata["accepted_actions"]) == 1


def test_mistaken_role_binding_never_reaches_an_owner(root):
    session = Session(SEED, "stay", "immediate")
    endpoints = {turn: str(root / turn) for turn in TURNS}
    with ExitStack() as stack:
        stack.enter_context(Family(root / "s", root / "c", session))
        sockets = {turn: listener(stack, root, turn) for turn in TURNS}
        loader = RecallParticipant(root, **kwargs("loader", endpoints))
        with pytest.raises(ValueError, match="wrong seat"):
            asyncio.run(loader.setup(RecallSeat(root, "depot", "depot")))
        crossed = RecallSeat(root, "depot", "loader")
        with pytest.raises(ValueError, match="unexpected fields|active loader"):
            asyncio.run(loader.run(instructions()["loader"], crossed, AgentContext()))
        assert crossed.commands == [["wait", "stage"]]
        for sock in sockets.values():
            sock.setblocking(False)
            with pytest.raises(BlockingIOError):
                sock.accept()
        with pytest.raises(ValueError, match="one owner socket per decision"):
            RecallParticipant(
                root, role="depot", owner_sockets={"stage": endpoints["stage"]}
            )
        with pytest.raises(ValueError, match="distinct"):
            RecallParticipant(
                root,
                role="loader",
                owner_sockets=dict.fromkeys(DECISIONS["loader"], endpoints["stage"]),
            )
        with pytest.raises(ValueError, match="only a role"):
            RecallParticipant(
                root, model_name="hosted/model", **kwargs("depot", endpoints)
            )
    assert session.snapshot()["actions"] == []
    team = roster(native(), endpoints)
    assert [(m["seat"], m["import_path"], m["kwargs"]) for m in team] == [
        (role, IMPORT_PATH, kwargs(role, endpoints)) for role in DECISIONS
    ]
