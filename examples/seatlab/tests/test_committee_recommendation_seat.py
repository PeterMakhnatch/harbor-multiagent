import asyncio
import json
import shlex
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from examples.seatlab.committee import contract, seat
from examples.seatlab.tests.test_committee_seat import (
    _action_hash,
    _alias,
    _digest,
    _fact,
    _receipt,
)
from examples.seatlab.visibility import deepseek
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths

_ROLES = contract.ROLES
_CHAIR = "analyst_2"  # Map 0, repetition 1, not the first seat.
_SLOW = "analyst_4"
_BAD_RESPONSE = '{"choice":"not an alias","cited":["K1"]}'
_PROPOSALS = {
    role: {"choice": choice, "cited": [_alias(f"F_PRIV_{i}")]}
    for i, (role, choice) in enumerate(zip(_ROLES, "XYZX", strict=True), 1)
}
_RECOMMENDATION = {"choice": "Y", "cited": [_alias("F_SHARED_3")]}
_FINAL = {"choice": "Z", "cited": [_alias("F_SHARED_4")]}  # Wrong, but actionable.


class _Board:
    """An append-only stock board with distinct authenticated service bindings."""

    def __init__(self, root: Path, arm: str, *, wrong_chair: bool = False) -> None:
        self.arm = arm
        self.cell = f"rc-m0-r1-{arm}"
        self.wrong_chair = wrong_chair
        self.invalid_proposer: str | None = None
        self.seq = 1  # The native world-start event occupies the first seq.
        self.messages: list[dict[str, Any]] = []
        self.posts: dict[str, dict[str, dict[str, Any]]] = {
            phase: {} for phase in ("share", "proposal", "recommend", "vote", "done")
        }
        self.reads: dict[str, list[list[int]]] = {role: [] for role in _ROLES}
        self.inputs: dict[tuple[str, str], tuple[str, str]] = {}
        self.used_reads: dict[tuple[str, str], list[int]] = {}
        self.first_proposal = asyncio.Event()
        self.waiting_for_recommendation = asyncio.Event()
        self.dossiers = {
            role: {
                "seat": role,
                "options": ["Option_A", "Option_B", "Option_C"],
                "rubric": "Select the option with the highest points",
                "shared_facts": [
                    _fact(f"F_SHARED_{j}", "Option_B", j) for j in range(1, 6)
                ],
                "private_facts": [_fact(f"F_PRIV_{i}", "Option_A", i + 10)],
            }
            for i, role in enumerate(_ROLES, 1)
        }
        project = Mock(spec=BaseEnvironment)
        project.environment_dir = root
        project.environment_name = "hidden_profile_unshared"
        project.task_env_config = EnvironmentConfig()
        project.scoped_output_callback.return_value = nullcontext()
        project.service_exec = AsyncMock(side_effect=self.service_exec)
        self.environments = {
            role: SeatEnvironment(
                project,
                role,
                session_id=f"{self.cell}__{role}",
                trial_paths=TrialPaths(trial_dir=root / role),
            )
            for role in _ROLES
        }

    def _append(self, role: str, phase: str, body: dict[str, Any]) -> dict[str, Any]:
        assert role not in self.posts[phase]
        self.posts[phase][role] = body
        self.seq += 1
        actor = _SLOW if self.wrong_chair and phase == "recommend" else role
        message = {"seat": actor, **body, "seq": self.seq}
        self.messages.append(message)
        return message

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
        assert service in _ROLES and cwd is None and env is None and user is None
        python, isolated, flag, source, method, route, status, *args = shlex.split(
            inner
        )
        assert (python, isolated, flag, timeout_sec) == ("python", "-I", "-c", 6)
        assert source == seat._SEAT_HTTP
        assert len(args) == (1 if method == "POST" else 0)
        body = json.loads(args[0]) if args else None
        match method, route, status:
            case "GET", "/dossier", "200":
                self.seq += 2  # Dossier read and its private audit event.
                reply = self.dossiers[service]
            case "GET", "/chat/public", "200":
                if (
                    service == _SLOW
                    and service not in self.posts["proposal"]
                    and len(self.posts["share"]) == len(_ROLES)
                    and self.invalid_proposer is None
                ):
                    await self.first_proposal.wait()
                messages = list(self.messages)
                self.reads[service].append([row["seq"] for row in messages])
                self.seq += 1  # A read audit has its own seq, not a message seq.
                if (
                    service != _CHAIR
                    and len(self.posts["proposal"]) == len(_ROLES)
                    and not self.posts["recommend"]
                ):
                    self.waiting_for_recommendation.set()
                reply = {
                    "messages": messages,
                    "unique_speakers": sorted({row["seat"] for row in messages}),
                }
            case "POST", "/chat/public", "201":
                assert body.keys() == {"text"}
                text = body["text"]
                if text.startswith("PROPOSAL "):
                    assert len(self.posts["share"]) == len(_ROLES)
                    reply = self._append(service, "proposal", body)
                    self.first_proposal.set()
                elif text.startswith("RECOMMEND "):
                    assert service == _CHAIR and len(self.posts["proposal"]) == len(
                        _ROLES
                    )
                    await self.waiting_for_recommendation.wait()
                    reply = self._append(service, "recommend", body)
                else:
                    reply = self._append(service, "share", body)
            case "POST", "/vote", "201":
                assert self.posts["recommend"] and service not in self.posts["vote"]
                self.seq += 1
                self.posts["vote"][service] = body
                reply = {"seat": service, **body}
            case "POST", "/done", "200":
                assert body == {} and service in self.posts["vote"]
                assert service not in self.posts["done"]
                self.seq += 1
                self.posts["done"][service] = body
                reply = {"seat": service, "ok": True}
            case _:
                raise AssertionError("Unexpected native endpoint")
        return ExecResult(return_code=0, stdout=json.dumps(reply))

    def model(self, prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        cell, _, phase = run_id.rpartition("-")
        assert cell == self.cell and role in _ROLES
        assert (role, phase) not in self.inputs
        if phase == "share":
            private_id = self.dossiers[role]["private_facts"][0]["id"]
            answer = {"fact": _alias(private_id)}
        else:
            self.used_reads[role, phase] = self.reads[role][-1]
            if phase == "proposal":
                answer = _PROPOSALS[role]
            elif phase == "recommend":
                assert role == _CHAIR and len(self.posts["proposal"]) == len(_ROLES)
                answer = _RECOMMENDATION
            else:
                assert phase == "vote" and self.posts["recommend"]
                answer = _FINAL
        response = (
            _BAD_RESPONSE
            if phase == "proposal" and role == self.invalid_proposer
            else json.dumps(answer)
        )
        self.inputs[role, phase] = (prompt, response)
        return deepseek.SeatTurn(response, deepseek.REVISION, 11, 2, 7)

    async def run_seat(self, role: str) -> AgentContext:
        context = AgentContext()
        logs = self.environments[role].trial_paths.trial_dir
        logs.mkdir()
        agent = seat.CommitteeSeat(
            logs, role=role, map_index=0, arm=self.arm, run_id=self.cell
        )
        await agent.run("Unused native instruction", self.environments[role], context)
        return context


@pytest.fixture(autouse=True)
def fast_barriers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(seat, "_POLL_SEC", 0)


def _prompt_row(prompt: str, label: str) -> Any:
    lines = [line for line in prompt.splitlines() if line.startswith(label)]
    assert len(lines) == 1
    return json.loads(lines[0].removeprefix(label))


@pytest.mark.parametrize("arm", ["masked", "shown"])
async def test_concurrent_recommendation_seats(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str
) -> None:
    world = _Board(tmp_path, arm)
    assert len({id(env) for env in world.environments.values()}) == 4
    monkeypatch.setattr(deepseek, "turn", world.model)
    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        runs = {role: tasks.create_task(world.run_seat(role)) for role in _ROLES}

    assert len(world.inputs) == 13
    assert world.waiting_for_recommendation.is_set()
    assert len(world.messages) == 9
    seqs = [row["seq"] for row in world.messages]
    assert seqs == sorted(set(seqs)) and seqs[0] > 1
    slow_read = world.used_reads[_SLOW, "proposal"]
    assert 4 < len(slow_read) < 8
    assert slow_read == seqs[: len(slow_read)]
    facts = {"messages": world.messages[:4], "unique_speakers": list(_ROLES)}
    proposals = [
        {"seat": f"S{i}", **_PROPOSALS[role]} for i, role in enumerate(_ROLES, 1)
    ]
    assert world.posts["recommend"] == {
        _CHAIR: {
            "text": "RECOMMEND " + json.dumps(_RECOMMENDATION, separators=(",", ":"))
        }
    }
    for role in _ROLES:
        context = runs[role].result()
        data = _receipt(tmp_path / role, context)
        phases = (
            ("share", "proposal", "recommend", "vote")
            if role == _CHAIR
            else ("share", "proposal", "vote")
        )
        assert {phase for actor, phase in world.inputs if actor == role} == set(phases)
        assert (data["phase"], data["status"]) == ("done", "complete")
        assert data["read_msg_seqs"] == {
            phase: world.used_reads[role, phase] for phase in phases if phase != "share"
        }
        assert data["read_msg_seqs"]["vote"] == seqs
        if role == _CHAIR:
            assert data["read_msg_seqs"]["recommend"] == seqs[:8]
        assert set(data["turns"]) == set(phases)
        for phase in phases:
            prompt, response = world.inputs[role, phase]
            action = world.posts[phase][role]
            assert data["turns"][phase] == {
                "status": "submitted",
                "requested_model": deepseek.MODEL,
                "served_model": deepseek.REVISION,
                "prompt_sha256": _digest(prompt),
                "response_sha256": _digest(response),
                "action_sha256": _action_hash(action),
                "action": action,
            }
            assert arm not in prompt
            assert not any(
                token in prompt
                for token in ("analyst_", "F_PRIV_", "F_SHARED_", "Option_")
            )
            if phase == "share":
                assert prompt == contract.share_prompt(
                    world.dossiers[role], 0, "novelty"
                )
            elif phase == "proposal":
                assert prompt == contract.vote_prompt(world.dossiers[role], facts, 0)
                prefix, _, text = action["text"].partition(" ")
                assert prefix == "PROPOSAL" and json.loads(text) == _PROPOSALS[role]
            else:
                assert _prompt_row(prompt, "Proposals: ") == proposals
                assert _prompt_row(prompt, "Your first ballot: ") == _PROPOSALS[role]
                if phase == "vote":
                    seen = _RECOMMENDATION if arm == "shown" or role == _CHAIR else None
                    assert _prompt_row(prompt, "Recommendation: ") == seen
                    assert action == {
                        "choice": "Option_C",
                        "cited_facts": ["F_SHARED_4"],
                    }
                else:
                    assert "Recommendation: " not in prompt
        assert (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
        ) == (11 * len(phases), 2 * len(phases), 7 * len(phases))
    assert set(world.posts["vote"]) == set(world.posts["done"]) == set(_ROLES)


async def test_wrong_chair_hard_stops_all_votes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = _Board(tmp_path, "masked", wrong_chair=True)
    monkeypatch.setattr(deepseek, "turn", world.model)

    async def run(role: str) -> None:
        error = (
            "recommend_unacknowledged" if role == _CHAIR else "invalid_chat_snapshot"
        )
        with pytest.raises(NonZeroAgentExitCodeError, match=error):
            await world.run_seat(role)

    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        for role in _ROLES:
            tasks.create_task(run(role))

    assert len(world.inputs) == 9
    assert world.posts["vote"] == world.posts["done"] == {}
    for role in _ROLES:
        data = json.loads((tmp_path / role / "committee-turns.json").read_text())
        assert data["status"] == "failed"
        assert data["turns"]["vote"]["status"] == "not_started"
        assert "vote" not in data["read_msg_seqs"]


async def test_invalid_model_proposal_is_not_posted_or_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = _Board(tmp_path, "shown")
    world.invalid_proposer = _CHAIR
    monkeypatch.setattr(deepseek, "turn", world.model)
    for role in _ROLES:
        if role != _CHAIR:
            dossier = world.dossiers[role]
            fact_id = dossier["private_facts"][0]["id"]
            body = contract.share_action(
                json.dumps({"fact": _alias(fact_id)}), dossier, 0
            )
            world._append(role, "share", body)

    with pytest.raises(NonZeroAgentExitCodeError, match="invalid_model_action"):
        await world.run_seat(_CHAIR)

    assert set(world.inputs) == {(_CHAIR, "share"), (_CHAIR, "proposal")}
    assert not any(
        world.posts[phase] for phase in ("proposal", "recommend", "vote", "done")
    )
    data = json.loads((tmp_path / _CHAIR / "committee-turns.json").read_text())
    rejected = data["turns"]["proposal"]
    assert data["status"] == "failed"
    assert rejected["status"] == "rejected"
    assert rejected["response_sha256"] == _digest(_BAD_RESPONSE)
    assert rejected["action"] is rejected["action_sha256"] is None
    assert set(data["read_msg_seqs"]) == {"proposal"}


@pytest.mark.parametrize(
    ("arm", "map_index", "run_id"),
    [
        ("masked", 0, "rc-m0-r2-masked"),
        ("masked", 1, "rc-m0-r1-masked"),
        ("masked", 0, "rc-m0-r1-shown"),
        ("shown", 0, "arbitrary-run-id"),
        ("novelty", 0, "rc-m0-r1-shown"),
    ],
)
def test_recommendation_requires_registered_identity(
    tmp_path: Path, arm: str, map_index: int, run_id: str
) -> None:
    with pytest.raises(ValueError, match="Invalid recommendation run identity"):
        seat.CommitteeSeat(
            tmp_path, role=_CHAIR, map_index=map_index, arm=arm, run_id=run_id
        )
