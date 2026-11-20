import asyncio
import json
import shlex
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, call

import pytest

from examples.seatlab.committee import contract, seat
from examples.seatlab.visibility import deepseek
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_ROLE = "analyst_1"
_MAP = 0
_ARM = "novelty"
_CELL = "hp-m0-r0-novelty"
_MODEL = "deepseek/deepseek-flash"
_RECEIPT_NAME = "committee-turns.json"
_RAW_PRIVATE_NARRATIVE = "Dossier narrative not for the model"


def _fact(fact_id: str, option: str, points: int) -> dict[str, Any]:
    return {
        "id": fact_id,
        "kind": "major_strength",
        "option": option,
        "points": points,
        "text": _RAW_PRIVATE_NARRATIVE,
    }


def _alias(fact_id: str) -> str:
    ids = [f"F_SHARED_{i}" for i in range(1, 6)] + [f"F_PRIV_{i}" for i in range(1, 5)]
    ids.sort(key=lambda native: _digest(f"seatlab-committee-v1:{_MAP}:{native}"))
    return f"K{ids.index(fact_id) + 1}"


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _action_hash(action: dict[str, Any]) -> str:
    return _digest(
        json.dumps(action, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )


class _World:
    """Respond at the scoped exec boundary, without starting a container."""

    def __init__(self) -> None:
        self.dossier = {
            "seat": _ROLE,
            "options": ["Option_A", "Option_B", "Option_C"],
            "rubric": "Select the option with the highest points",
            "shared_facts": [
                _fact(f"F_SHARED_{i}", "Option_B", i) for i in range(1, 6)
            ],
            "private_facts": [_fact("F_PRIV_1", "Option_A", 9)],
        }
        self.own: dict[str, Any] = {}
        self.peers = [
            {
                "seat": role,
                "text": '{"id":"F_SHARED_2","option":"Option_B","points":2}',
                "seq": seq,
            }
            for seq, role in enumerate(reversed(contract.ROLES[1:]), 6)
        ]
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.partial_read = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.exec = AsyncMock(side_effect=self._exec)
        environment = Mock(spec=SeatEnvironment)
        environment.service = _ROLE
        environment.exec = self.exec
        self.environment = cast(SeatEnvironment, environment)

    def chat(self) -> dict[str, Any]:
        messages = [*self.peers, self.own] if self.release.is_set() else [self.own]
        return {
            "messages": messages,
            "unique_speakers": sorted({row["seat"] for row in messages}),
        }

    async def _exec(self, *, command: str, timeout_sec: int) -> ExecResult:
        python, isolated, flag, source, method, route, status, *args = shlex.split(
            command
        )
        assert (python, isolated, flag, timeout_sec) == ("python", "-I", "-c", 6)
        assert "os.environ.get('SEAT_TOKEN'" in source
        assert "HTTPConnection('world', 8080" in source
        assert len(args) == (1 if method == "POST" else 0)
        body = json.loads(args[0]) if args else None
        self.calls.append((method, route, body))
        match (method, route, status):
            case ("GET", "/dossier", "200"):
                reply = self.dossier
            case ("POST", "/chat/public", "201"):
                self.own = {"seat": _ROLE, "text": body["text"], "seq": 5}
                reply = self.own
            case ("GET", "/chat/public", "200"):
                if not self.release.is_set():
                    self.partial_read.set()
                reply = self.chat()
            case ("POST", "/vote", "201"):
                reply = {"seat": _ROLE, **body}
            case ("POST", "/done", "200"):
                assert body == {}
                reply = {"ok": True, "seat": _ROLE}
            case _:
                raise AssertionError("Unexpected scoped world operation")
        return ExecResult(return_code=0, stdout=json.dumps(reply))


def _agent(logs: Path) -> seat.CommitteeSeat:
    return seat.CommitteeSeat(
        logs, _MODEL, role=_ROLE, map_index=_MAP, arm=_ARM, run_id=_CELL
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return _World()


def _receipt(logs: Path, context: AgentContext) -> dict[str, Any]:
    receipt = json.loads((logs / _RECEIPT_NAME).read_text(encoding="utf-8"))
    assert context.metadata is not None
    assert {key: value for key, value in receipt.items() if key != "turns"} == {
        key: value for key, value in context.metadata.items() if key != "turns"
    }
    for phase, row in receipt["turns"].items():
        assert {
            key: value for key, value in row.items() if key != "action"
        } == context.metadata["turns"][phase]
    return receipt


async def test_scoped_two_phase_turns_wait_for_four_posts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: _World
) -> None:
    world.release.clear()
    monkeypatch.setattr(seat, "_POLL_SEC", 0)
    share = f' {{"fact":"{_alias("F_PRIV_1")}"}}\n'
    vote = f'{{"choice":"Z","cited":["{_alias("F_SHARED_2")}"]}} '
    model = Mock(
        side_effect=[
            deepseek.SeatTurn(share, deepseek.REVISION, 11, 0, 7),
            deepseek.SeatTurn(vote, deepseek.MODEL, 13, 3, 5),
        ]
    )
    monkeypatch.setattr(deepseek, "turn", model)
    context = AgentContext()
    agent = _agent(tmp_path)
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(
            agent.run("Unused native instruction", world.environment, context)
        )
        await asyncio.wait_for(world.partial_read.wait(), timeout=2)
        assert model.call_count == 1
        assert {route for _, route, _ in world.calls} == {"/dossier", "/chat/public"}
        world.release.set()

    share_prompt = contract.share_prompt(world.dossier, _MAP, _ARM)
    vote_prompt = contract.vote_prompt(world.dossier, world.chat(), _MAP)
    assert model.call_args_list == [
        call(share_prompt, run_id=f"{_CELL}-share", role=_ROLE),
        call(vote_prompt, run_id=f"{_CELL}-vote", role=_ROLE),
    ]
    share_action = {"text": '{"id":"F_PRIV_1","option":"Option_A","points":9}'}
    vote_action = {"choice": "Option_C", "cited_facts": ["F_SHARED_2"]}
    assert [entry for entry in world.calls if entry[0] == "POST"] == [
        ("POST", "/chat/public", share_action),
        ("POST", "/vote", vote_action),
        ("POST", "/done", {}),
    ]
    data = _receipt(tmp_path, context)
    assert (data["schema"], data["phase"], data["status"]) == (
        "seatlab.committee-seat/v1",
        "done",
        "complete",
    )
    assert (data["role"], data["run_id"], data["map_index"], data["arm"]) == (
        _ROLE,
        _CELL,
        _MAP,
        _ARM,
    )
    for phase, prompt, response, served, action in (
        ("share", share_prompt, share, deepseek.REVISION, share_action),
        ("vote", vote_prompt, vote, deepseek.MODEL, vote_action),
    ):
        assert data["turns"][phase] == {
            "status": "submitted",
            "requested_model": deepseek.MODEL,
            "served_model": served,
            "prompt_sha256": _digest(prompt),
            "response_sha256": _digest(response),
            "action_sha256": _action_hash(action),
            "action": action,
        }
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (24, 3, 12)
    assert context.cost_usd is None
    path = tmp_path / _RECEIPT_NAME
    assert path.stat().st_mode & 0o777 == 0o600
    assert _RAW_PRIVATE_NARRATIVE not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("fault", "category", "executions"),
    [
        ("service", "wrong_environment", 0),
        ("dossier", "wrong_dossier_seat", 1),
        ("exec", "world_request_failed", 1),
        ("host_key", "unbound_host_key", 0),
    ],
)
async def test_wrong_seat_or_transport_failure_stops_before_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    world: _World,
    fault: str,
    category: str,
    executions: int,
) -> None:
    model = Mock()
    monkeypatch.setattr(deepseek, "turn", model)
    if fault == "service":
        world.environment.service = "analyst_2"
    elif fault == "dossier":
        world.dossier["seat"] = "analyst_2"
    elif fault == "exec":
        world.exec.side_effect = OSError("Private transport error")
    else:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-reach-seat")
    context = AgentContext()
    with pytest.raises(NonZeroAgentExitCodeError, match=category):
        await _agent(tmp_path).run("", world.environment, context)
    model.assert_not_called()
    assert world.exec.await_count == executions
    receipt = _receipt(tmp_path, context)
    assert (receipt["status"], receipt["failure"]) == ("failed", category)
    assert receipt["turns"]["share"]["status"] == "not_started"
    assert (
        context.n_input_tokens
        is context.n_cache_tokens
        is context.n_output_tokens
        is None
    )


@pytest.mark.parametrize("phase", ["share", "vote"])
async def test_invalid_first_response_keeps_hash_without_post_or_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: _World, phase: str
) -> None:
    invalid = '{"not_a_fact":"$(printf x); café"}'
    accepted_share = f'{{"fact":"{_alias("F_SHARED_2")}"}}'
    responses = [accepted_share, invalid] if phase == "vote" else [invalid]
    model = Mock(
        side_effect=[
            deepseek.SeatTurn(text, deepseek.MODEL, 11, 3, 7) for text in responses
        ]
    )
    monkeypatch.setattr(deepseek, "turn", model)
    context = AgentContext()
    with pytest.raises(NonZeroAgentExitCodeError, match="invalid_model_action"):
        await _agent(tmp_path).run("", world.environment, context)
    assert model.call_count == len(responses)
    assert [route for method, route, _ in world.calls if method == "POST"] == (
        ["/chat/public"] if phase == "vote" else []
    )
    receipt = _receipt(tmp_path, context)
    assert (receipt["phase"], receipt["status"]) == (phase, "failed")
    record = receipt["turns"][phase]
    assert record["status"] == "rejected"
    assert record["response_sha256"] == _digest(invalid)
    assert record["action_sha256"] is record["action"] is None
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (
        11 * len(responses),
        3 * len(responses),
        7 * len(responses),
    )
    assert "printf" not in (tmp_path / _RECEIPT_NAME).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("phase", "settled_usage", "expected_failure", "expected_usage"),
    [
        ("share", None, "provider_timeout", (None, None, None)),
        ("vote", None, "provider_timeout", (11, 3, 7)),
        ("share", (13, 5, 9), "provider_response_invalid", (13, 5, 9)),
        ("vote", (13, 5, 9), "provider_response_invalid", (24, 8, 16)),
        ("vote", (13, 5), "usage_unverified", (11, 3, 7)),
        ("vote", (13, 5, -1), "usage_unverified", (11, 3, 7)),
    ],
)
async def test_provider_failure_keeps_safe_category_and_prompt_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    world: _World,
    phase: str,
    settled_usage: tuple[int, ...] | None,
    expected_failure: str,
    expected_usage: tuple[int | None, int | None, int | None],
) -> None:
    accepted = deepseek.SeatTurn(
        f'{{"fact":"{_alias("F_SHARED_2")}"}}', deepseek.MODEL, 11, 3, 7
    )
    failure = deepseek.SeatUnavailable(
        "provider_timeout" if settled_usage is None else "provider_response_invalid",
        settled_usage=cast(tuple[int, int, int] | None, settled_usage),
    )
    model = Mock(side_effect=[accepted, failure] if phase == "vote" else [failure])
    monkeypatch.setattr(deepseek, "turn", model)
    context = AgentContext()
    with pytest.raises(NonZeroAgentExitCodeError, match=expected_failure):
        await _agent(tmp_path).run("", world.environment, context)
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == expected_usage
    assert context.cost_usd is context.model_usage is None
    if phase == "vote":
        assert model.call_count == 2
        assert [route for method, route, _ in world.calls if method == "POST"] == [
            "/chat/public"
        ]
        prompt = contract.vote_prompt(world.dossier, world.chat(), _MAP)
    else:
        model.assert_called_once()
        assert world.calls == [("GET", "/dossier", None)]
        prompt = contract.share_prompt(world.dossier, _MAP, _ARM)
    receipt = _receipt(tmp_path, context)
    assert (receipt["phase"], receipt["status"], receipt["failure"]) == (
        phase,
        "failed",
        expected_failure,
    )
    record = receipt["turns"][phase]
    assert record["status"] == "unavailable"
    assert record["prompt_sha256"] == _digest(prompt)
    assert record["response_sha256"] is record["served_model"] is None
    assert record["action_sha256"] is record["action"] is None


@pytest.mark.parametrize("fault", ["timeout", "duplicate"])
async def test_incomplete_or_duplicate_chat_does_not_start_vote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: _World, fault: str
) -> None:
    text = f'{{"fact":"{_alias("F_SHARED_2")}"}}'
    model = Mock(return_value=deepseek.SeatTurn(text, deepseek.MODEL, 11, 3, 7))
    monkeypatch.setattr(deepseek, "turn", model)
    if fault == "timeout":
        world.release.clear()
        monkeypatch.setattr(seat, "_BARRIER_TIMEOUT_SEC", 0.02)
        expected = "chat_barrier_timeout"
    else:
        world.peers[-1]["seat"] = world.peers[0]["seat"]
        expected = "invalid_chat_snapshot"
    context = AgentContext()
    with pytest.raises(NonZeroAgentExitCodeError, match=expected):
        await _agent(tmp_path).run("", world.environment, context)
    model.assert_called_once()
    receipt = _receipt(tmp_path, context)
    assert (receipt["phase"], receipt["failure"]) == ("barrier", expected)
    assert receipt["turns"]["share"]["status"] == "submitted"
    assert receipt["turns"]["vote"]["status"] == "not_started"
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (11, 3, 7)


async def test_existing_receipt_is_never_overwritten(
    tmp_path: Path, world: _World
) -> None:
    path = tmp_path / _RECEIPT_NAME
    path.write_text("first attempt", encoding="utf-8")
    with pytest.raises(NonZeroAgentExitCodeError, match="receipt_unavailable"):
        await _agent(tmp_path).run("", world.environment, AgentContext())
    world.exec.assert_not_called()
    assert path.read_text(encoding="utf-8") == "first attempt"
