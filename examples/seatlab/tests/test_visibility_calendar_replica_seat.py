import asyncio
import json
from collections.abc import Buffer
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import override

import pytest

from examples.seatlab.tests import test_visibility_calendar_seat as original_tests
from examples.seatlab.visibility import calendar_protocol, calendar_seat, replica_io
from examples.seatlab.visibility.calendar_replica_seat import RelayCalendarSeat
from harbor.models.agent.context import AgentContext

world = original_tests.world
_RUN = "rx-w00-d0-r0-peer"


class _Dispatcher(BytesIO):
    def __init__(self, replies: dict[str, str]) -> None:
        super().__init__()
        self.incoming = BytesIO()
        self._replies = replies
        self.prompts: dict[str, str] = {}

    @override
    def write(self, data: Buffer, /) -> int:
        request = json.loads(bytes(data))
        assert request.keys() == {"type", "id", "prompt"} and request["type"] == "turn"
        turn_id = request["id"]
        assert turn_id not in self.prompts
        self.prompts[turn_id] = request["prompt"]
        self.incoming.seek(0)
        self.incoming.truncate()
        self.incoming.write(
            json.dumps({"id": turn_id, "text": self._replies[turn_id]}).encode() + b"\n"
        )
        self.incoming.seek(0)
        return super().write(data)


def _actor(path: Path, seat: str) -> RelayCalendarSeat:
    path.mkdir()
    return RelayCalendarSeat(
        path,
        world=0,
        direction=0,
        repetition=0,
        arm="peer",
        seat=seat,
        own_starts=original_tests._GUESSES[seat],
        control=False,
    )


@pytest.mark.parametrize("invalid_role", [None, "cal_recheck", "cal_decide"])
async def test_relay_actor_uses_the_original_mailbox_and_action_grammar(
    tmp_path: Path, world: original_tests._World, invalid_role: str | None
) -> None:
    roles = ("cal_recheck", "cal_decide")
    replies = {
        f"{_RUN}:{role}": " not a model action " if role == invalid_role else accepted
        for role, accepted in zip(
            roles, ('{"starts": [9, 22]}', '{"slot": 34}'), strict=True
        )
    }
    dispatcher = _Dispatcher(replies)
    turns = tmp_path / "turns"
    turns.mkdir(mode=0o700)
    exchange = replica_io.Exchange(dispatcher.incoming, dispatcher, turns)
    contexts = {seat: AgentContext() for seat in ("p1", "p2")}
    with replica_io.bind(exchange):
        async with asyncio.TaskGroup() as tasks:
            for seat, context in contexts.items():
                actor = _actor(tmp_path / seat, seat)
                info = actor.to_agent_info()
                assert (
                    info.name == "seatlab-calendar-external" and info.version == "1.0.0"
                )
                assert info.model_info is None and actor.model_name is None
                tasks.create_task(
                    actor.run("not passed to model", world.environment(seat), context)
                )

    p1, p2 = (tuple(original_tests._GUESSES[seat]) for seat in ("p1", "p2"))
    expected = {
        "cal_recheck": calendar_protocol.recheck_prompt(world.tables["p1"], p1, p2),
        "cal_decide": calendar_protocol.decision_prompt(
            world.tables["p2"], p2, (9, 22)
        ),
    }
    for (seat, context), role in zip(contexts.items(), roles, strict=True):
        metadata = original_tests._metadata(context)
        assert metadata["run"] == _RUN and metadata["seat"] == seat
        assert context.n_input_tokens is None
        assert context.n_cache_tokens is None
        assert context.n_output_tokens is None
        assert context.cost_usd is None and context.model_usage is None
        assert not list((tmp_path / seat).iterdir())
        path = turns / f"{_RUN}_{role}.json"
        if role == "cal_decide" and invalid_role == "cal_recheck":
            assert metadata["stage"] == "peer_model_invalid"
            assert metadata["own_prompt_sha256"] is None
            assert not path.exists() and f"{_RUN}:{role}" not in dispatcher.prompts
            continue
        stage = "model_invalid" if role == invalid_role else "complete"
        assert metadata["stage"] == stage
        prompt = dispatcher.prompts[f"{_RUN}:{role}"]
        receipt = json.loads(path.read_bytes())
        assert receipt["seat"] == seat
        assert receipt["response_text"] == replies[f"{_RUN}:{role}"]
        assert prompt == expected[role]
        digest = sha256(prompt.encode()).hexdigest()
        assert metadata["own_prompt_sha256"] == receipt["prompt_sha256"] == digest

    assert [(m["from"], m["to"], m["text"]) for m in world.messages] == [
        ("p2", "p1", '{"starts":[6,18]}'),
        (
            "p1",
            "p2",
            (
                calendar_seat.INVALID_WIRE
                if invalid_role == "cal_recheck"
                else '{"starts":[9,22]}'
            ),
        ),
    ]
    assert world.submissions == (
        [("p2", {"day": "Fri", "start": "15:00"})] if invalid_role is None else []
    )


async def test_lost_relay_response_raises_instead_of_sending_invalid_wire(
    tmp_path: Path, world: original_tests._World
) -> None:
    world.inboxes["p1"] = [
        {"id": "m1", "from": "p2", "to": "p1", "text": '{"starts":[6,18]}', "t": 1}
    ]
    turns = tmp_path / "turns"
    turns.mkdir(mode=0o700)
    exchange = replica_io.Exchange(BytesIO(), BytesIO(), turns)
    context = AgentContext()
    actor = _actor(tmp_path / "p1", "p1")
    with replica_io.bind(exchange), pytest.raises(RuntimeError, match="^model_failed$"):
        await actor.run("", world.environment("p1"), context)
    metadata = original_tests._metadata(context)
    assert metadata["stage"] == "model_failed"
    assert metadata["own_prompt_sha256"] is not None
    assert not world.messages and not world.submissions
    assert not list(turns.iterdir())
