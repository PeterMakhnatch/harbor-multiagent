import asyncio
import json
import shlex
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from examples.seatlab.committee import (
    contract,
    prefix_packet,
    prefix_schedule,
    prefix_seat,
    recommendation,
    recommendation_report,
    schedule,
    seat,
)
from examples.seatlab.visibility import deepseek
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

_ROLES = schedule.ROLES
_CHAIR = schedule.chair(0, 0)
_CUT = {"message_count": 9, "votes_submitted": 0, "done": []}
_MODEL_VOTE = {"choice": "Option_C", "cited_facts": ["F_SHARED_5"]}


def _dossier(role: str) -> dict[str, Any]:
    dossier = recommendation_report._dossier(role)
    for row in dossier["shared_facts"]:
        row.update(kind="positive_criterion", text="Do not expose this description")
    row = dossier["private_facts"][0]
    row.update(kind="critical_flaw" if row["points"] < 0 else "major_strength")
    return dossier


@pytest.fixture
def checkpoint(monkeypatch: pytest.MonkeyPatch) -> prefix_packet.Checkpoint:
    events: list[dict[str, Any]] = []
    refs: list[int] = []

    def emit(
        kind: str,
        actor: str,
        payload: dict[str, Any],
        recipients: list[str] | None = None,
    ) -> None:
        public = kind == "chat_message"
        visibility = "world_private" if actor == "world" else "world_audit"
        events.append(
            {
                "seq": len(events) + 1,
                "task_family": "epistemic-crucible",
                "condition": "hidden_profile_unshared",
                "seed": 42,
                "event_type": kind,
                "actor": actor,
                "authenticated": actor != "world",
                "visibility": "public" if public else visibility,
                "recipients": list(_ROLES) if public else recipients or [],
                "payload": payload,
            }
        )
        if public:
            refs.append(len(events))

    def read(role: str) -> None:
        emit("message_read", role, {"channel": "public", "msg_seqs": refs.copy()})

    emit("world_started", "world", {"mode": "hidden_profile", "seats": list(_ROLES)})
    for role in _ROLES:
        emit("dossier_read", role, {})
        emit("dossier_truth", "world", prefix_packet._truth(role, len(events)), [role])
        body = contract.share_action(
            json.dumps({"fact": contract._mapping(0)[1]["F_SHARED_5"]}),
            _dossier(role),
            0,
        )
        emit("chat_message", role, {"seat": role, **body})
        if role == _CHAIR:
            read(role)  # Replay an original unsuccessful wait, not an extra poll.
    for role in reversed(_ROLES):
        read(role)
        emit(
            "chat_message",
            role,
            {"seat": role, **recommendation.post("proposal", _MODEL_VOTE, 0)},
        )
    read(_CHAIR)
    emit(
        "chat_message",
        _CHAIR,
        {"seat": _CHAIR, **recommendation.post("recommend", _MODEL_VOTE, 0)},
    )
    cut = prefix_packet._checkpoint(schedule.recommendation_grid()[0], events)
    monkeypatch.setattr(prefix_packet, "load", Mock(return_value={0: cut}))
    monkeypatch.setattr(prefix_schedule, "PACKET_SHA256", "0" * 64, raising=False)
    return cut


class _Backend:
    def __init__(
        self,
        root: Path,
        checkpoint: prefix_packet.Checkpoint,
        arm: str,
        *,
        mismatch: str | None = None,
        pause_dossier: bool = False,
    ) -> None:
        self.root = root
        self.checkpoint = checkpoint
        self.arm = arm
        self.cell = f"pf-m0-r1-{arm}"  # The source chair is from repetition 0.
        self.mismatch = mismatch
        self.pause_dossier = pause_dossier
        self.started_dossier = asyncio.Event()
        self.paused_final_read = asyncio.Event()
        self.cancelled_native_requests: list[tuple[str, str]] = []
        self.step = 0
        self.requests: list[tuple[str, str, str]] = []
        self.final_reads: set[str] = set()
        self.voted: set[str] = set()
        self.done: set[str] = set()
        self.audit_downloads = 0
        self.contexts = {role: AgentContext() for role in _ROLES}
        self.project = Mock(spec=BaseEnvironment)
        self.project.environment_dir = root
        self.project.environment_name = "hidden_profile_unshared"
        self.project.task_env_config = EnvironmentConfig()
        self.project.scoped_output_callback.return_value = nullcontext()
        self.project.service_exec = AsyncMock(side_effect=self.service_exec)
        self.project.download_file = AsyncMock(side_effect=self.download_audit)
        self.environments = {
            role: SeatEnvironment(
                self.project,
                role,
                session_id=f"{self.cell}__{role}",
                trial_paths=TrialPaths(trial_dir=root / role),
            )
            for role in _ROLES
        }
        self.agents = {role: self.make_agent(role) for role in _ROLES}

    async def _pause(self, service: str, route: str) -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled_native_requests.append((service, route))
            raise

    async def service_exec(
        self,
        command: str,
        *,
        service: str,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout_sec: int | None,
        user: str | int | None,
    ) -> ExecResult:
        exec_, bash, flag, inner = shlex.split(command)
        assert (exec_, bash, flag) == ("exec", "bash", "-c")
        assert (cwd, env, timeout_sec, user) == (None, None, 6, None)
        python, isolated, flag, source, method, route, status, *args = shlex.split(
            inner
        )
        assert (python, isolated, flag) == ("python", "-I", "-c")
        assert source == seat._SEAT_HTTP
        assert len(args) == (1 if method == "POST" else 0)
        body = json.loads(args[0]) if args else None
        self.requests.append((service, method, route))
        if self.step < len(self.checkpoint.steps):
            step = self.checkpoint.steps[self.step]
            self.step += 1
            assert service == step.role and body == step.body
            if step.kind == "dossier":
                assert (method, route, status) == ("GET", "/dossier", "200")
                self.started_dossier.set()
                if self.pause_dossier:
                    await self._pause(service, route)
                reply = _dossier(service)
            elif step.kind == "read":
                assert (method, route, status) == ("GET", "/chat/public", "200")
                reply = step.expected
                assert reply is not None
                if self.mismatch == "radio":
                    # Native types are part of the receipt, not Python equality.
                    reply = {
                        **reply,
                        "messages": [
                            {**row, "seq": float(row["seq"])}
                            for row in reply["messages"]
                        ],
                    }
            else:
                assert (method, route, status) == ("POST", "/chat/public", "201")
                reply = step.expected
        else:
            match method, route, status:
                case "GET", "/status", "200":
                    assert service == _CHAIR and self.audit_downloads == 0
                    reply = {
                        **_CUT,
                        "mode": "hidden_profile",
                        "report_counts": {},
                        "decisions_submitted": [],
                    }
                    if self.mismatch == "status":
                        reply["decisions_submitted"] = [1]
                case "GET", "/chat/public", "200":
                    assert self.audit_downloads == 1 and service not in self.final_reads
                    self.final_reads.add(service)
                    reply = self.checkpoint.board
                    if self.mismatch == "final_board":
                        if service == _CHAIR:
                            self.paused_final_read.set()
                            await self._pause(service, route)
                        if service == _ROLES[-1]:
                            await self.paused_final_read.wait()
                            reply = {**reply, "messages": reply["messages"][:-1]}
                case "POST", "/vote", "201":
                    assert self.final_reads == set(_ROLES) and service not in self.voted
                    assert body == _MODEL_VOTE
                    self.voted.add(service)
                    reply = {"seat": service, **body}
                case "POST", "/done", "200":
                    assert body == {} and service in self.voted
                    assert service not in self.done
                    self.done.add(service)
                    reply = {"seat": service, "ok": True}
                case _:
                    raise AssertionError("Unexpected native route")
        return ExecResult(return_code=0, stdout=json.dumps(reply))

    async def download_audit(self, source: str, path: Path) -> None:
        assert source == "/logs/world/events.jsonl"
        assert self.requests[-1] == (_CHAIR, "GET", "/status")
        self.audit_downloads += 1
        rows = [
            {**row, "ts": "2026-01-01T00:00:01+00:00"} for row in self.checkpoint.events
        ]
        if self.mismatch == "audit":
            read = next(row for row in rows if row["event_type"] == "message_read")
            read["payload"] = {"channel": "public", "msg_seqs": []}
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def make_agent(self, role: str, suffix: str = "") -> prefix_seat.PrefixSeat:
        logs = self.root / (role + suffix)
        logs.mkdir(parents=True)
        return prefix_seat.PrefixSeat(
            logs, role=role, run_id=self.cell, arm=self.arm, map_index=0
        )

    async def run(
        self, role: str, *, duplicate: prefix_seat.PrefixSeat | None = None
    ) -> None:
        agent = duplicate if duplicate is not None else self.agents[role]
        context = AgentContext() if duplicate is not None else self.contexts[role]
        await agent.run("Unused native instruction", self.environments[role], context)

    def receipt(self, role: str) -> dict[str, Any]:
        return json.loads((self.root / role / "prefix-vote.json").read_text())


@pytest.fixture(autouse=True)
def process_state(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(prefix_seat, "_ACTIVE", {})
    monkeypatch.setattr(prefix_seat, "_STARTED", set())

    # BaseAgent parses via a lazy LiteLLM import. Keep the boundary tests offline
    # and time only the rendezvous, not startup or provider cost-map discovery.
    def parse_model(agent: prefix_seat.PrefixSeat, model: str) -> tuple[str, str]:
        assert model == seat._MODEL == agent.model_name
        return "deepseek", deepseek.MODEL

    monkeypatch.setattr(prefix_seat.PrefixSeat, "_parse_model_name", parse_model)
    turn = Mock(side_effect=AssertionError("Paid turn must not start"))
    monkeypatch.setattr(deepseek, "turn", turn)
    return turn


async def _run_failure(world: _Backend, role: str) -> str:
    try:
        await world.run(role)
    except seat._SeatFailure as exc:
        return exc.category
    raise AssertionError("The seat unexpectedly completed")


@pytest.mark.parametrize(
    ("mismatch", "category", "audit_downloads"),
    [
        ("radio", "prefix_chat_read_mismatch", 0),
        ("status", "prefix_cut_status_mismatch", 0),
        ("audit", "prefix_audit_mismatch", 1),
        ("final_board", "prefix_final_board_mismatch", 1),
    ],
)
async def test_failure_keeps_fresh_votes_closed(
    tmp_path: Path,
    checkpoint: prefix_packet.Checkpoint,
    process_state: Mock,
    mismatch: str,
    category: str,
    audit_downloads: int,
) -> None:
    world = _Backend(tmp_path, checkpoint, "masked", mismatch=mismatch)
    async with asyncio.timeout(3), asyncio.TaskGroup() as group:
        tasks = [group.create_task(_run_failure(world, role)) for role in _ROLES]
    assert {task.result() for task in tasks} == {category, "prefix_peer_failed"}
    process_state.assert_not_called()
    assert not prefix_seat._ACTIVE and not world.voted and not world.done
    assert world.audit_downloads == audit_downloads
    if mismatch != "radio":
        assert world.step == len(checkpoint.steps)
    if mismatch == "final_board":
        assert world.cancelled_native_requests == [(_CHAIR, "/chat/public")]
    for role in _ROLES:
        receipt = world.receipt(role)
        assert receipt["status"] == "failed" and receipt["action"] is None
        assert world.contexts[role].n_input_tokens is None
        assert receipt["turn"]["status"] == "not_started"
        if mismatch != "final_board":
            assert receipt["read_msg_seqs"] is None
            assert not world.final_reads


@pytest.mark.parametrize("cancelled_role", [0, 1])
async def test_cancellation_wakes_waiting_peers_and_rejects_a_restart(
    tmp_path: Path,
    checkpoint: prefix_packet.Checkpoint,
    process_state: Mock,
    cancelled_role: int,
) -> None:
    world = _Backend(tmp_path, checkpoint, "masked", pause_dossier=True)
    async with asyncio.timeout(3), asyncio.TaskGroup() as group:
        tasks = [group.create_task(_run_failure(world, role)) for role in _ROLES]
        await world.started_dossier.wait()
        tasks[cancelled_role].cancel()
    assert tasks[cancelled_role].cancelled()
    assert world.receipt(_ROLES[cancelled_role])["status"] == "cancelled"
    peer_failures = {
        task.result() for index, task in enumerate(tasks) if index != cancelled_role
    }
    assert peer_failures == {"prefix_peer_failed"}
    assert world.cancelled_native_requests == [(_ROLES[0], "/dossier")]
    assert not prefix_seat._ACTIVE
    requests = world.requests.copy()
    restart = world.make_agent(_ROLES[0], "-restart")
    with pytest.raises(seat._SeatFailure, match="duplicate_cell"):
        await world.run(_ROLES[0], duplicate=restart)
    assert world.requests == requests
    process_state.assert_not_called()


async def test_two_backends_do_not_share_seats_or_board_barriers(
    tmp_path: Path, checkpoint: prefix_packet.Checkpoint, process_state: Mock
) -> None:
    worlds = [_Backend(tmp_path / arm, checkpoint, arm) for arm in ("masked", "shown")]
    masked, shown = worlds
    cells = {world.cell: world for world in worlds}
    duplicate = _Backend(tmp_path / "duplicate", checkpoint, "masked")
    repeated_role = masked.make_agent(_ROLES[0], "-extra")
    prompts: dict[tuple[str, str], str] = {}
    answer = json.dumps(recommendation.blind_vote(_MODEL_VOTE, 0))

    def generate(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        assert cells[run_id].final_reads == set(_ROLES)
        prompts[run_id, role] = prompt
        return deepseek.SeatTurn(answer, deepseek.REVISION, 11, 2, 7)

    process_state.side_effect = generate
    async with asyncio.timeout(3), asyncio.TaskGroup() as group:
        for role in _ROLES[:-1]:
            group.create_task(masked.run(role))
        shown_tasks = [group.create_task(shown.run(role)) for role in _ROLES]
        for task in shown_tasks:
            await task
        assert not masked.requests
        with pytest.raises(seat._SeatFailure, match="duplicate_seat"):
            await masked.run(_ROLES[0], duplicate=repeated_role)
        with pytest.raises(seat._SeatFailure, match="duplicate_cell"):
            await duplicate.run(_ROLES[-1])
        assert not masked.requests and not duplicate.requests
        group.create_task(masked.run(_ROLES[-1]))

    assert not prefix_seat._ACTIVE and process_state.call_count == 8
    for world in worlds:
        assert len(world.requests) == len(checkpoint.steps) + 1 + 3 * len(_ROLES)
        assert world.audit_downloads == 1
        for role, context in world.contexts.items():
            receipt = world.receipt(role)
            assert receipt.pop("action") == _MODEL_VOTE
            assert context.metadata == receipt
            assert receipt["status"] == "complete" and receipt["cut_status"] == _CUT
            assert receipt["prefix_sha256"] == checkpoint.prefix_sha256
            assert receipt["read_msg_seqs"] == [
                msg["seq"] for msg in checkpoint.board["messages"]
            ]
            assert (
                context.n_input_tokens,
                context.n_cache_tokens,
                context.n_output_tokens,
            ) == (11, 2, 7)
            prompt = prompts[world.cell, role]
            assert "Do not expose this description" not in prompt
            assert ("Recommendation: null" in prompt) == (
                world is masked and role != _CHAIR
            )
            assert receipt["turn"] == {
                "status": "submitted",
                "requested_model": deepseek.MODEL,
                "served_model": deepseek.REVISION,
                "prompt_sha256": seat._digest(prompt),
                "response_sha256": seat._digest(answer),
                "action_sha256": seat._digest(seat._canonical_json(_MODEL_VOTE)),
            }
    assert prompts[masked.cell, _CHAIR] == prompts[shown.cell, _CHAIR]


async def test_probe_restores_only_the_cut(
    tmp_path: Path, checkpoint: prefix_packet.Checkpoint, process_state: Mock
) -> None:
    world = _Backend(tmp_path, checkpoint, "masked")

    async def probe(role: str) -> None:
        async with prefix_seat.restored(
            world.environments[role], role, "cut-check-m0", checkpoint
        ) as (dossier, status):
            assert dossier == _dossier(role)
            assert status == _CUT

    async with asyncio.timeout(3), asyncio.TaskGroup() as group:
        for role in _ROLES:
            group.create_task(probe(role))
    assert not prefix_seat._ACTIVE and world.audit_downloads == 1
    assert len(world.requests) == len(checkpoint.steps) + 1
    assert not world.final_reads and not world.voted and not world.done
    process_state.assert_not_called()
