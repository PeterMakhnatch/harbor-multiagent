import asyncio
import json
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, call
from uuid import UUID

import pytest

from examples.seatlab import seat_agent
from examples.seatlab.tests.test_agent import (
    _CATALOG,
    _ENDPOINTS,
    _MODEL,
    _REPORTS,
    _dispatch_view,
    _hosted,
    _inventory_view,
    _response,
    _seat,
    _world,
)
from examples.seatlab.workbench.generation_receipts import owner
from examples.seatlab.workbench.owner_journal import OwnerJournal
from harbor.environments.base import BaseEnvironment
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import TrialPaths

_OWNERS_DIR = "SEATLAB_OPENROUTER_OWNERS_DIR"
_OWNERS_FILE = "owners.jsonl"
_FIRST = UUID(int=1, version=4)
_RETRY = UUID(int=2, version=4)
_JOB_TREE = Path("jobs") / "handoff"
_TASK_TREE = Path("tasks") / "handoff"
_TURN = [
    ("GET", "/api/v1/models"),
    ("GET", f"/api/v1/models/{_MODEL}/endpoints"),
    ("POST", "/api/v1/chat/completions"),
]


def _actor(
    monkeypatch: pytest.MonkeyPatch, root: Path, trial: UUID, role: str
) -> tuple[seat_agent.SeatAgent, SeatEnvironment]:
    trial_dir = root / _JOB_TREE / str(trial)
    paths = TrialPaths(trial_dir / "seats" / role)
    paths.agent_dir.mkdir(parents=True)
    environment_dir = root / _TASK_TREE / "environment"
    environment_dir.mkdir(parents=True, exist_ok=True)
    project = Mock(spec=BaseEnvironment)
    project.trial_paths = TrialPaths(trial_dir)
    project.environment_dir = environment_dir
    seat = _seat(role)
    seat.project = project
    seat.trial_paths = paths
    seat.environment_dir = environment_dir
    agent = _hosted(monkeypatch, paths.agent_dir)
    agent.context_id = trial
    return agent, seat


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    path.chmod(0o700)
    return path


def _enable(monkeypatch: pytest.MonkeyPatch, directory: Path) -> Path:
    monkeypatch.setenv(_OWNERS_DIR, str(directory))
    return directory / _OWNERS_FILE


def _router_wire(monkeypatch: pytest.MonkeyPatch, *completions: dict[str, Any]) -> Mock:
    connection = Mock(sock=None)
    connection.getresponse.side_effect = [
        Mock(status=200, read1=BytesIO(json.dumps(body).encode("utf-8")).read1)
        for completion in completions
        for body in (_CATALOG, _ENDPOINTS, completion)
    ]
    monkeypatch.setattr(
        seat_agent.http.client, "HTTPSConnection", Mock(return_value=connection)
    )
    return connection.request


def _requests(wire: Mock) -> list[tuple[str, str]]:
    return [(entry.args[0], entry.args[1]) for entry in wire.call_args_list]


def _rows(journal: Path) -> list[dict[str, Any]]:
    if not journal.exists():
        return []
    return [json.loads(line) for line in journal.read_bytes().splitlines()]


async def test_hosted_ownership_tracks_interleaved_seats_and_retry_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    journal = _enable(monkeypatch, _private_dir(root / "private"))
    dispatch, dispatch_seat = _actor(monkeypatch, root, _FIRST, "dispatch")
    inventory, inventory_seat = _actor(monkeypatch, root, _FIRST, "inventory")
    retry, retry_seat = _actor(monkeypatch, root, _RETRY, "dispatch")
    inventory_response: dict[str, Any] = {**_response(), "id": "gen-inventory"}
    inventory_response["choices"][0]["message"]["content"] = json.dumps(
        {"rows": [{"id": 0, "reports": _REPORTS}]}
    )
    bad_response: dict[str, Any] = {**_response(), "id": "gen-invalid-decision"}
    bad_response["choices"][0]["message"]["content"] = "not a JSON decision"
    wire = _router_wire(
        monkeypatch,
        inventory_response,
        bad_response,
        {**_response(), "id": "gen-retry"},
    )
    waiting = asyncio.Event()
    inventory_submitted = asyncio.Event()
    inventory_owner = owner(str(_FIRST), "inventory", "gen-inventory")

    async def world(
        seat: SeatEnvironment,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        view_sha256: str | None = None,
    ) -> tuple[int, str]:
        if path == "/view":
            if seat is dispatch_seat:
                waiting.set()
                await inventory_submitted.wait()
            view = (
                _inventory_view() if seat is inventory_seat else _dispatch_view("fresh")
            )
            return 200, json.dumps(view)
        assert path == "/submit"
        if seat is inventory_seat:
            assert _rows(journal) == [inventory_owner]
            inventory_submitted.set()
        else:
            assert seat is retry_seat
        return 200, '{"accepted":true}'

    async def reject_dispatch() -> None:
        with pytest.raises(RuntimeError):
            await dispatch.run("", dispatch_seat, AgentContext())

    monkeypatch.setattr(seat_agent, "_world_request", AsyncMock(side_effect=world))
    async with asyncio.timeout(10), asyncio.TaskGroup() as group:
        group.create_task(reject_dispatch())
        await waiting.wait()
        await inventory.run("", inventory_seat, AgentContext())

    expected = [inventory_owner, owner(str(_FIRST), "dispatch", "gen-invalid-decision")]
    assert _rows(journal) == expected
    assert _requests(wire) == _TURN * 2

    await retry.run("", retry_seat, AgentContext())

    assert _rows(journal) == [*expected, owner(str(_RETRY), "dispatch", "gen-retry")]
    assert _requests(wire) == _TURN * 3
    assert list(root.rglob(_OWNERS_FILE)) == [journal]


@pytest.mark.parametrize("invalid_field", ["choices", "openrouter_metadata"])
async def test_hosted_ownership_records_id_before_rejecting_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_field: str
) -> None:
    root = tmp_path.resolve()
    journal = _enable(monkeypatch, _private_dir(root / "private"))
    agent, seat = _actor(monkeypatch, root, _FIRST, "dispatch")
    world = _world(monkeypatch, _dispatch_view("fresh"))
    response = {**_response(), "id": "gen-invalid-response"}
    del response[invalid_field]
    wire = _router_wire(monkeypatch, response)

    with pytest.raises(RuntimeError):
        await agent.run("", seat, AgentContext())

    assert _rows(journal) == [owner(str(_FIRST), "dispatch", "gen-invalid-response")]
    world.assert_awaited_once_with(seat, "/view")
    assert _requests(wire) == _TURN


@pytest.mark.parametrize(
    "other_ids",
    [
        {},
        {"generation_id": "gen-alternate"},
        {"id": "chatcmpl-not-a-generation", "generation_id": "gen-alternate"},
        {"id": "gen-contains whitespace"},
    ],
    ids=["absent", "alternate-key", "wrong-prefix", "bad-grammar"],
)
async def test_hosted_without_valid_top_level_id_still_submits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, other_ids: dict[str, Any]
) -> None:
    root = tmp_path.resolve()
    journal = _enable(monkeypatch, _private_dir(root / "private"))
    agent, seat = _actor(monkeypatch, root, _FIRST, "dispatch")
    world = _world(monkeypatch, _dispatch_view("fresh"))
    wire = _router_wire(monkeypatch, {**_response(), **other_ids})

    await agent.run("", seat, AgentContext())

    assert _rows(journal) == []
    assert _requests(wire) == _TURN
    assert [entry.args[1] for entry in world.await_args_list] == ["/view", "/submit"]


@pytest.mark.parametrize("unsafe_path", ["job-tree", "task-tree", "symlink", "public"])
async def test_hosted_refuses_unsafe_owner_directory_before_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unsafe_path: str
) -> None:
    root = tmp_path.resolve()
    private = _private_dir(root / "private")
    agent, seat = _actor(monkeypatch, root, _FIRST, "dispatch")
    if unsafe_path == "job-tree":
        directory = _private_dir(root / _JOB_TREE / "owners")
    elif unsafe_path == "task-tree":
        directory = _private_dir(root / _TASK_TREE / "owners")
    elif unsafe_path == "symlink":
        directory = root / "linked"
        directory.symlink_to(private, target_is_directory=True)
    else:
        directory = private
        directory.chmod(0o755)
    _enable(monkeypatch, directory)
    world = _world(monkeypatch, _dispatch_view("fresh"))
    wire = _router_wire(monkeypatch, {**_response(), "id": "gen-must-not-send"})

    with pytest.raises((RuntimeError, ValueError)):
        await agent.run("", seat, AgentContext())

    assert _requests(wire) in ([], _TURN[:1], _TURN[:2])
    assert world.await_args_list in ([], [call(seat, "/view")])
    assert not list(root.rglob(_OWNERS_FILE))


async def test_hosted_capture_disabled_leaves_no_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    monkeypatch.delenv(_OWNERS_DIR, raising=False)
    agent, seat = _actor(monkeypatch, root, _FIRST, "dispatch")
    world = _world(monkeypatch, _dispatch_view("fresh"))
    wire = _router_wire(monkeypatch, {**_response(), "id": "gen-disabled"})

    await agent.run("", seat, AgentContext())

    assert _requests(wire) == _TURN
    assert [entry.args[1] for entry in world.await_args_list] == ["/view", "/submit"]
    assert not list(root.rglob(_OWNERS_FILE))


async def test_post_capture_failure_preserves_decision_without_second_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    journal = _enable(monkeypatch, _private_dir(root / "private"))
    agent, seat = _actor(monkeypatch, root, _FIRST, "dispatch")
    world = _world(monkeypatch, _dispatch_view("fresh"))
    wire = _router_wire(monkeypatch, {**_response(), "id": "gen-unwritten"})
    monkeypatch.setattr(
        OwnerJournal, "append", Mock(side_effect=OSError("private detail"))
    )
    context = AgentContext()

    await agent.run("", seat, context)

    assert _requests(wire) == _TURN
    assert [entry.args[1] for entry in world.await_args_list] == ["/view", "/submit"]
    assert _rows(journal) == []
    assert context.metadata is not None
    assert context.metadata["owner_capture"] == "write_failed"
    assert "private detail" not in json.dumps(context.metadata)
