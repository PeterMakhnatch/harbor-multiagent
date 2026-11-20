import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call

import pytest

from examples.seatlab.committee import contract, seat
from examples.seatlab.tests.test_committee_seat import (
    _MAP,
    _MODEL,
    _RECEIPT_NAME,
    _ROLE,
    _World,
    _action_hash,
    _alias,
    _digest,
    _receipt,
    world as world,
)
from examples.seatlab.visibility import deepseek
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext


def _agent(logs: Path, arm: str) -> seat.CommitteeSeat:
    return seat.CommitteeSeat(
        logs, _MODEL, role=_ROLE, map_index=_MAP, arm=arm, run_id=f"tv-m{_MAP}-r0-{arm}"
    )


def _share_turn() -> deepseek.SeatTurn:
    return deepseek.SeatTurn(
        json.dumps({"fact": _alias("F_PRIV_1")}), deepseek.REVISION, 11, 2, 7
    )


@pytest.mark.parametrize(
    ("arm", "field_order"),
    [
        ("plain", ["cited", "choice"]),
        ("first", ["choice", "cited", "totals"]),
        ("last", ["totals", "choice", "cited"]),
    ],
)
async def test_tally_posts_only_model_vote_and_keeps_private_readout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    world: _World,
    arm: str,
    field_order: list[str],
) -> None:
    totals = None if arm == "plain" else {"Z": -5, "X": 100, "Y": 0}
    fields = {"choice": "Z", "cited": [_alias("F_SHARED_2")], "totals": totals}
    text = " \n" + json.dumps({key: fields[key] for key in field_order}) + "\t\n"
    model = Mock(
        side_effect=[_share_turn(), deepseek.SeatTurn(text, deepseek.MODEL, 13, 3, 17)]
    )
    monkeypatch.setattr(deepseek, "turn", model)
    context = AgentContext()

    await _agent(tmp_path, arm).run("", world.environment, context)

    run_id = f"tv-m{_MAP}-r0-{arm}"
    chat = world.chat()
    plain_view = contract.vote_prompt(world.dossier, chat, _MAP)
    prompt = contract.tally_prompt(world.dossier, chat, _MAP, arm)
    if arm == "plain":
        assert prompt.encode("utf-8") == plain_view.encode("utf-8")
    assert model.call_args_list == [
        call(
            contract.share_prompt(world.dossier, _MAP, "novelty"),
            run_id=f"{run_id}-share",
            role=_ROLE,
        ),
        call(prompt, run_id=f"{run_id}-vote", role=_ROLE),
    ]
    vote = {"choice": "Option_C", "cited_facts": ["F_SHARED_2"]}
    if arm == "plain":
        assert vote == contract.vote_action(text, world.dossier, chat, _MAP)
    assert [entry for entry in world.calls if entry[0] == "POST"] == [
        (
            "POST",
            "/chat/public",
            {"text": '{"id":"F_PRIV_1","option":"Option_A","points":9}'},
        ),
        ("POST", "/vote", vote),
        ("POST", "/done", {}),
    ]
    receipt = _receipt(tmp_path, context)
    assert receipt["status"] == "complete"
    assert receipt["run_id"] == run_id
    assert receipt["arm"] == arm
    assert "readout" not in receipt["turns"]["share"]
    assert receipt["turns"]["vote"] == {
        "status": "submitted",
        "requested_model": deepseek.MODEL,
        "served_model": deepseek.MODEL,
        "prompt_sha256": _digest(prompt),
        "response_sha256": _digest(text),
        "action_sha256": _action_hash(vote),
        "action": vote,
        "readout": {
            "view_sha256": _digest(plain_view),
            "totals": totals,
            "field_order": field_order,
            "response_text": text,
        },
    }
    assert (tmp_path / _RECEIPT_NAME).stat().st_mode & 0o777 == 0o600
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (24, 5, 24)


@pytest.mark.parametrize(
    ("arm", "map_index", "run_id"),
    [
        ("plain", 0, "hp-m0-r0-plain"),
        ("first", 1, "tv-m0-r0-first"),
        ("last", 0, "tv-m0-r0-first"),
        ("last", 0, "tv-m0-r2-last"),
        ("plain", 0, "tv-m00-r0-plain"),
    ],
)
def test_tally_requires_exact_registered_run_identity(
    tmp_path: Path, arm: str, map_index: int, run_id: str
) -> None:
    with pytest.raises(ValueError, match="Invalid tally run identity"):
        seat.CommitteeSeat(
            tmp_path, _MODEL, role=_ROLE, map_index=map_index, arm=arm, run_id=run_id
        )


@pytest.mark.parametrize("invalid_field", ["choice", "totals"])
async def test_rejected_tally_keeps_verified_counts_without_partial_readout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: _World, invalid_field: str
) -> None:
    response = {
        "totals": {"X": 1, "Y": 0, "Z": -1},
        "choice": "Z",
        "cited": [_alias("F_SHARED_2")],
    }
    response[invalid_field] = "private invalid content"
    text = json.dumps(response)
    model = Mock(
        side_effect=[_share_turn(), deepseek.SeatTurn(text, deepseek.MODEL, 13, 3, 17)]
    )
    monkeypatch.setattr(deepseek, "turn", model)
    context = AgentContext()

    with pytest.raises(NonZeroAgentExitCodeError, match="invalid_model_action"):
        await _agent(tmp_path, "first").run("", world.environment, context)

    assert model.call_count == 2
    assert [route for method, route, _ in world.calls if method == "POST"] == [
        "/chat/public"
    ]
    receipt = _receipt(tmp_path, context)
    record = receipt["turns"]["vote"]
    assert (receipt["status"], receipt["phase"]) == ("failed", "vote")
    assert record["status"] == "rejected"
    assert record["response_sha256"] == _digest(text)
    assert record["action"] is record["action_sha256"] is None
    assert "readout" not in record
    assert "private invalid content" not in (tmp_path / _RECEIPT_NAME).read_text(
        encoding="utf-8"
    )
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (24, 5, 24)


async def test_cancelled_tally_request_keeps_only_verified_share_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: _World
) -> None:
    to_thread = AsyncMock(side_effect=[_share_turn(), asyncio.CancelledError()])
    monkeypatch.setattr(seat.asyncio, "to_thread", to_thread)
    context = AgentContext()

    with pytest.raises(asyncio.CancelledError):
        await _agent(tmp_path, "last").run("", world.environment, context)

    assert to_thread.await_count == 2
    assert [route for method, route, _ in world.calls if method == "POST"] == [
        "/chat/public"
    ]
    receipt = _receipt(tmp_path, context)
    assert (receipt["status"], receipt["phase"]) == ("cancelled", "vote")
    assert receipt["failure"] == "cancelled"
    assert receipt["turns"]["share"]["status"] == "submitted"
    record = receipt["turns"]["vote"]
    assert record["status"] == "requesting"
    assert (
        record["response_sha256"] is record["action"] is record["action_sha256"] is None
    )
    assert "readout" not in record
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (11, 2, 7)


async def test_cancelled_tally_submission_keeps_a_complete_attempt_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world: _World
) -> None:
    async def cancel_vote(*, command: str, timeout_sec: int) -> ExecResult:
        reply = await world._exec(command=command, timeout_sec=timeout_sec)
        if world.calls[-1][:2] == ("POST", "/vote"):
            raise asyncio.CancelledError
        return reply

    world.exec.side_effect = cancel_vote
    text = json.dumps(
        {
            "totals": {"X": 1, "Y": 0, "Z": -1},
            "choice": "Z",
            "cited": [_alias("F_SHARED_2")],
        }
    )
    model = Mock(
        side_effect=[_share_turn(), deepseek.SeatTurn(text, deepseek.MODEL, 13, 3, 17)]
    )
    monkeypatch.setattr(deepseek, "turn", model)
    context = AgentContext()

    with pytest.raises(asyncio.CancelledError):
        await _agent(tmp_path, "first").run("", world.environment, context)

    assert model.call_count == 2
    action = {"choice": "Option_C", "cited_facts": ["F_SHARED_2"]}
    assert world.calls[-1] == ("POST", "/vote", action)
    receipt = _receipt(tmp_path, context)
    assert (receipt["status"], receipt["phase"]) == ("cancelled", "vote")
    record = receipt["turns"]["vote"]
    assert record["status"] == "submitting"
    assert record["action"] == action
    assert record["action_sha256"] == _action_hash(action)
    assert record["readout"]["response_text"] == text
    assert record["response_sha256"] == _digest(text)
    assert (
        context.n_input_tokens,
        context.n_cache_tokens,
        context.n_output_tokens,
    ) == (24, 5, 24)
