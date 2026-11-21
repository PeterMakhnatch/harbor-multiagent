import importlib
import json
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

with pytest.MonkeyPatch.context() as patch:
    root = Path(__file__).resolve().parents[3]
    patch.syspath_prepend(str(root / "examples" / "seatlab"))
    patch.syspath_prepend(str(root))
    agent = importlib.import_module("examples.seatlab.attention.agent")


def _seat(role: str) -> SeatEnvironment:
    seat = Mock(spec=SeatEnvironment)
    seat.service = role
    return cast(SeatEnvironment, seat)


def _world(monkeypatch: pytest.MonkeyPatch, *, reject_dispatch: bool = False):
    code = run_path(
        str(agent.native_pairs.run_panel.TASK_DIR / "environment" / "seatworld.py")
    )
    world = code["World"](
        {
            "schema": "seatlab.scenario/v1",
            "seed": 6401,
            "condition": "reordered",
            "episodes": 6,
        }
    )
    attempts = {}

    async def request(
        environment: SeatEnvironment,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        view_sha256: str | None = None,
    ) -> tuple[int, str]:
        role = environment.service
        view = world.view(role)
        if path == "/view":
            return 200, json.dumps(view)
        assert path == "/submit" and role not in attempts
        assert view_sha256 == agent.view_hash(view)
        attempts[role] = view_sha256
        if role == "dispatch" and reject_dispatch:
            return 409, '{"accepted":false}'
        return 200, json.dumps(world.submit(role, payload))

    monkeypatch.setattr(agent.seat_agent, "_world_request", request)
    return world, attempts


_REPORTS = [
    {
        "id": i,
        "reports": [
            {"revision": 1, "available": ["A", "B"]},
            {"revision": 2, "available": ["C", "D"] if i < 3 else ["A", "B"]},
        ],
    }
    for i in range(6)
]
_OPEN = '{"open":["Q2","Q6"]}'
_CHOICES = dict(zip((f"Q{i}" for i in range(1, 7)), "DACBAD", strict=True))
_DECISIONS = json.dumps({"choices": _CHOICES})


def _counts(context: AgentContext) -> tuple[int | None, int | None, int | None]:
    return context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens


@pytest.mark.parametrize("transport", ["direct", "commands"])
async def test_attention_submits_all_six_to_native_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, transport: str
) -> None:
    prompts = []
    for arm in ("named", "count"):
        world, attempts = _world(monkeypatch)
        cell_id = f"attn-s6401-r0-{arm}"
        inventory_reply = json.dumps({"rows": list(reversed(_REPORTS))}) + "\n"
        replies = (inventory_reply, _OPEN, _DECISIONS)
        stage_tokens = ((11, 2, 4), (21, 3, 5), (31, 7, 8))
        calls = []

        def direct(prompt: str, run: str, role: str):
            index = len(calls)
            calls.append((prompt, run, role))
            return agent.route_run.Answer(
                replies[index], "deepseek-flash", *stage_tokens[index]
            )

        def review(prompt: str) -> str:
            calls.append((prompt, None, "inventory"))
            return inventory_reply

        def decide(prompt: str) -> str:
            calls.append((prompt, None, "dispatch"))
            return _OPEN if len(calls) == 2 else _DECISIONS

        command_config = (
            str(tmp_path / "commands.json") if transport == "commands" else None
        )
        paid = Mock(side_effect=direct)
        commands = Mock(return_value=(review, decide))
        monkeypatch.setattr(agent.route_cli, "_direct", paid)
        monkeypatch.setattr(agent.seat_command, "load_commands", commands)
        turns = []
        for role in ("inventory", "dispatch"):
            seat = agent.AttentionSeat(
                tmp_path,
                cell_id=cell_id,
                transport=transport,
                command_config=command_config,
            )
            context = AgentContext()
            await seat.run("Do not include this instruction", _seat(role), context)
            if transport == "direct":
                expected = (11, 2, 4) if role == "inventory" else (52, 10, 13)
                assert _counts(context) == expected
            else:
                assert _counts(context) == (None, None, None)
            metadata = context.metadata
            assert metadata is not None
            assert metadata["stage"] == "complete"
            assert metadata["view_sha256"] == attempts[role]
            assert metadata["opened"] == ([] if role == "inventory" else ["Q2", "Q6"])
            turns.extend(metadata["turns"])
        evidence = world.evidence()
        assert evidence["complete"] is True
        aliases = agent.protocol.aliases(world.view("dispatch")["cases"], 6401, 0)
        chosen = {case["id"]: case["chosen"] for case in evidence["cases"]}
        assert chosen == {aliases[alias]: bay for alias, bay in _CHOICES.items()}
        assert [case["reports"] for case in evidence["cases"]] == [
            row["reports"] for row in _REPORTS
        ]
        if transport == "commands":
            paid.assert_not_called()
            assert commands.call_count == 2
        else:
            commands.assert_not_called()
            assert [(run, role) for _, run, role in calls] == [
                (f"{cell_id}-inventory", "inventory"),
                (f"{cell_id}-select", "dispatch"),
                (f"{cell_id}-decide", "dispatch"),
            ]
        for (prompt, _, _), reply, turn in zip(calls, replies, turns, strict=True):
            assert turn["text"] == reply
            assert turn["response_sha256"] == sha256(reply.encode("utf-8")).hexdigest()
            assert turn["prompt_sha256"] == sha256(prompt.encode("utf-8")).hexdigest()
        prompts.append(tuple(prompt for prompt, _, _ in calls))
    named, count = prompts
    assert named[0] == count[0]
    assert named[1] != count[1]
    assert named[2] == count[2]


@pytest.mark.parametrize(
    ("stage", "request_count", "submit"),
    [
        ("callback_error", 2, False),
        ("invalid_model_decisions", 1, False),
        ("submission_unacknowledged", 2, True),
    ],
)
async def test_attention_preserves_failed_attempt_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    request_count: int,
    submit: bool,
) -> None:
    world, attempts = _world(monkeypatch, reject_dispatch=True)
    world.submit("inventory", {"rows": _REPORTS})
    invalid_selection = '{"open":["Q2","Q2"]}'
    select = agent.route_run.Answer(
        invalid_selection if stage == "invalid_model_decisions" else _OPEN,
        "deepseek-flash",
        11,
        1,
        3,
    )
    decide = (
        ValueError("private provider diagnostic")
        if stage == "callback_error"
        else agent.route_run.Answer(_DECISIONS, "deepseek-flash", 7, 2, 4)
    )
    paid = Mock(side_effect=[select, decide])
    monkeypatch.setattr(agent.route_cli, "_direct", paid)
    seat = agent.AttentionSeat(
        tmp_path, cell_id="attn-s6401-r0-named", transport="direct"
    )
    context = AgentContext()
    with pytest.raises(
        agent.seat_agent._Unmeasured, match=f"^Native attention seat: {stage}$"
    ):
        await seat.run("", _seat("dispatch"), context)
    with pytest.raises(agent.seat_agent._Unmeasured, match="already started"):
        await seat.run("", _seat("dispatch"), context)
    assert paid.call_count == request_count
    assert ("dispatch" in attempts) is submit
    assert world.evidence()["complete"] is False
    metadata = context.metadata
    assert metadata is not None and metadata["stage"] == stage
    assert metadata["view_sha256"] == agent.view_hash(world.view("dispatch"))
    turns = metadata["turns"]
    assert len(turns) == request_count and turns[0]["text"] == select.text
    if stage == "callback_error":
        assert turns[1]["status"] == "callback_error"
        assert turns[1]["text"] is None
        assert turns[1]["response_sha256"] is None
        assert turns[1]["usage"] is None
        assert _counts(context) == (None, None, None)
    assert (metadata["decision_sha256"] is not None) is submit
