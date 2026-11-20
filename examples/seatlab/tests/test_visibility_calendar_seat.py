import asyncio
import http.client
import json
import os
import shlex
from contextlib import redirect_stdout
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from stat import S_IMODE
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from examples.seatlab.visibility import calendar_packet, calendar_seat, deepseek
from harbor.environments.base import ExecResult
from harbor.environments.seat import SeatEnvironment
from harbor.models.agent.context import AgentContext

_TABLES: tuple[calendar_packet.Table, calendar_packet.Table] = (
    ((240, 255, 255, 255, 255), (0, 0, 0, 0, 0), (0, 0, 0, 0, 128)),
    ((195, 255, 255, 255, 255), (0, 0, 0, 0, 0), (0, 0, 0, 0, 1)),
)
_ROLES = ("p1", "p2")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
_GUESSES = {"p1": [5, 14], "p2": [6, 18]}


def _briefing(seat: str, table: calendar_packet.Table) -> str:
    rows = {
        f"private-{seat}-person-{index}": {
            day: [9 + bit for bit in range(8) if mask & (1 << bit)]
            for day, mask in zip(_DAYS, row, strict=True)
        }
        for index, row in reversed(list(enumerate(table)))
    }
    return json.dumps({"calendars": rows})


class _World:
    """In-memory HTTP boundary with the stock auth, envelope, and inbox shapes."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.submissions: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[tuple[str, str, str]] = []
        self.deliveries: list[tuple[str, list[str]]] = []
        self.operations: list[tuple[str, str]] = []
        self.tables = dict(zip(_ROLES, _TABLES, strict=True))
        self.tokens = {role: f"offline-token-{role}" for role in _ROLES}
        self.inboxes: dict[str, list[dict[str, Any]]] = {}

    def _request(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        role = next(
            role
            for role in _ROLES
            if headers["Authorization"] == f"Bearer {self.tokens[role]}"
        )
        self.requests.append((method, path, role))
        if (method, path) == ("GET", "/whoami"):
            return 200, {
                "seat": role,
                "seats": list(_ROLES),
                "messaging": True,
                "limits": {"max_text": 2048, "max_messages": 8},
            }
        if (method, path) == ("GET", "/messages"):
            inbox = self.inboxes.get(
                role, [m for m in self.messages if role in (m["from"], m["to"])]
            )
            self.deliveries.append(
                (role, [m["id"] for m in inbox if m["from"] != role])
            )
            return 200, {"messages": inbox}
        assert method == "POST" and body is not None
        assert headers["Content-Type"] == "application/json"
        payload = json.loads(body)
        if path == "/messages":
            assert payload.keys() == {"to", "text"}
            assert payload["to"] in _ROLES and payload["to"] != role
            assert not any(m["from"] == role for m in self.messages)
            message = {
                "id": f"m{len(self.messages) + 1}",
                "from": role,
                "to": payload["to"],
                "text": payload["text"],
                "t": float(len(self.messages)),
            }
            self.messages.append(message)
            return 201, message
        assert path == "/submit" and not self.submissions
        assert payload.keys() == {"answer"} and payload["answer"].keys() == {
            "day",
            "start",
        }
        self.submissions.append((role, payload["answer"]))
        return 200, {"accepted": True}

    def _connect(self, host: str, port: int, *, timeout: int) -> SimpleNamespace:
        assert (host, port) == ("world", 8080) and timeout > 0

        def request(
            method: str, path: str, body: bytes | None, headers: dict[str, str]
        ) -> None:
            status, value = self._request(method, path, body, headers)
            connection.getresponse = lambda: SimpleNamespace(
                status=status, read=BytesIO(json.dumps(value).encode()).read
            )

        connection = SimpleNamespace(request=request, close=lambda: None)
        return connection

    def environment(
        self, seat: str, *, token_seat: str | None = None
    ) -> SeatEnvironment:
        environment = Mock(spec=SeatEnvironment)
        environment.service = seat

        async def exec_command(
            command: str, *, env: dict[str, str], timeout_sec: int
        ) -> ExecResult:
            assert command == calendar_seat._SEAT_COMMAND
            argv = shlex.split(command)
            assert argv[:3] == ["python3", "-I", "-c"] and len(argv) == 4
            assert timeout_sec > 0
            assert env.keys() <= {"SEATLAB_CALENDAR_OP", "SEATLAB_CALENDAR_BODY"}
            assert all(token not in command for token in self.tokens.values())
            self.operations.append((seat, env["SEATLAB_CALENDAR_OP"]))
            seat_env = {
                **env,
                "WORLD_URL": "http://world:8080",
                "SEAT_TOKEN": self.tokens[token_seat or seat],
                "SEAT_BRIEFING": _briefing(seat, self.tables[seat]),
                "EXTRA": "not-on-the-information-path",
            }
            code = 0
            with (
                patch.dict(os.environ, seat_env, clear=True),
                patch.object(http.client, "HTTPConnection", self._connect),
                redirect_stdout(StringIO()) as stdout,
            ):
                try:
                    # Run the fixed production command against the in-memory HTTP boundary.
                    exec(compile(argv[3], "<scoped-seat>", "exec"), {})  # noqa: S102
                except SystemExit as error:
                    assert type(error.code) is int
                    code = error.code
            value = stdout.getvalue()
            assert "private-" not in value and "offline-token-" not in value
            return ExecResult(stdout=value, return_code=code)

        environment.exec.side_effect = exec_command
        return cast(SeatEnvironment, environment)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    packet = tmp_path / "packet.json"
    value = {
        "schema": "seatlab.calendar-packet/v1",
        "worlds": [
            {"index": index, "task_checksum": f"{index:064x}", "tables": _TABLES}
            for index in range(12)
        ],
    }
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    packet.write_bytes(data)
    monkeypatch.setattr(calendar_packet, "PACKET", packet)
    monkeypatch.setattr(calendar_packet, "PACKET_SHA256", sha256(data).hexdigest())
    monkeypatch.setattr(calendar_seat, "_POLL_INTERVAL_SEC", 0.001)
    return _World()


def _seat(
    path: Path, role: str, arm: str, direction: int, *, control: bool
) -> calendar_seat.CalendarSeat:
    path.mkdir()
    return calendar_seat.CalendarSeat(
        path,
        world=0,
        direction=direction,
        repetition=0,
        arm=arm,
        seat=role,
        own_starts=[] if control else _GUESSES[role],
        control=control,
    )


async def _run_pair(
    path: Path, world: _World, arm: str, direction: int = 0, *, control: bool = False
) -> dict[str, AgentContext]:
    contexts = {role: AgentContext() for role in _ROLES}
    async with asyncio.TaskGroup() as tasks:
        for role, context in contexts.items():
            actor = _seat(path / role, role, arm, direction, control=control)
            tasks.create_task(
                actor.run(
                    "NATIVE INSTRUCTION MUST NOT REACH A MODEL",
                    world.environment(role),
                    context,
                )
            )
    return contexts


def _record(path: Path, role: str, name: str) -> dict[str, Any]:
    file = path / role / name
    assert S_IMODE(file.stat().st_mode) == 0o600
    return json.loads(file.read_text())


def _metadata(context: AgentContext) -> dict[str, Any]:
    assert context.metadata is not None
    return context.metadata["calendar"]


@pytest.mark.parametrize(("arm", "direction"), [("blind", 0), ("peer", 1)])
async def test_native_wire_and_submission_preserve_wrong_model_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    world: _World,
    arm: str,
    direction: int,
) -> None:
    sender, receiver = _ROLES[direction], _ROLES[1 - direction]
    calls: dict[str, str] = {}

    def turn(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        assert role not in calls and run_id == f"cf-w00-d{direction}-r0-{arm}"
        calls[role] = prompt
        if role == "cal_recheck":
            assert (sender, ["m1"]) in world.deliveries
            return deepseek.SeatTurn('{"starts": [9, 22]}', deepseek.MODEL, 11, 2, 5)
        assert role == "cal_decide" and (receiver, ["m2"]) in world.deliveries
        return deepseek.SeatTurn('{"slot": 34}', deepseek.MODEL, 13, 3, 6)

    monkeypatch.setattr(deepseek, "turn", turn)
    contexts = await _run_pair(tmp_path, world, arm, direction)
    feedback = (
        '{"ready":true}'
        if arm == "blind"
        else json.dumps({"starts": _GUESSES[receiver]}, separators=(",", ":"))
    )
    assert [(m["from"], m["to"], m["text"]) for m in world.messages] == [
        (receiver, sender, feedback),
        (sender, receiver, '{"starts":[9,22]}'),
    ]
    assert world.submissions == [(receiver, {"day": "Fri", "start": "15:00"})]
    assert sorted(req for req in world.requests if req[1] == "/whoami") == [
        ("GET", "/whoami", "p1"),
        ("GET", "/whoami", "p2"),
    ]
    recheck, decide = calls["cal_recheck"], calls["cal_decide"]
    monday = {
        "p1": "Mon: 13:00, 14:00, 15:00, 16:00",
        "p2": "Mon: 09:00, 10:00, 15:00, 16:00",
    }
    assert monday[sender] in recheck and monday[receiver] not in recheck
    assert monday[receiver] in decide and monday[sender] not in decide
    assert f"Receiver message: {feedback}\n" in recheck
    assert 'Sender\'s revised candidate list: {"starts":[9,22]}' in decide
    for role, prompt, call, ids, usage in (
        (sender, recheck, "cal_recheck", ("m1", "m2"), (11, 2, 5)),
        (receiver, decide, "cal_decide", ("m2", "m1"), (13, 3, 6)),
    ):
        own = json.dumps({"starts": _GUESSES[role]}, separators=(",", ":"))
        assert f"Your initial candidate list: {own}\n" in prompt
        assert all(
            text not in prompt
            for text in (
                "NATIVE INSTRUCTION",
                "private-",
                "offline-token-",
                "not-on-the-information-path",
            )
        )
        context = contexts[role]
        metadata = _metadata(context)
        receipt = _record(tmp_path, role, "calendar-turn.json")
        assert metadata["stage"] == "complete" and metadata["control"] is False
        assert (metadata["incoming_id"], metadata["outgoing_id"]) == ids
        digest = sha256(prompt.encode()).hexdigest()
        assert metadata["own_prompt_sha256"] == receipt["prompt_sha256"] == digest
        assert receipt["role"] == call and receipt["status"] == "accepted"
        assert (
            context.n_input_tokens,
            context.n_cache_tokens,
            context.n_output_tokens,
        ) == usage
        assert not (tmp_path / role / "calendar-control.json").exists()
    sender_receipt = _record(tmp_path, sender, "calendar-turn.json")
    assert sender_receipt["response_text"] == '{"starts": [9, 22]}'
    assert (
        _record(tmp_path, receiver, "calendar-turn.json")["response_text"]
        == '{"slot": 34}'
    )


@pytest.mark.parametrize("arm", ["blind", "peer"])
async def test_keyless_control_uses_ordinary_transport_and_receiver_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: _World, arm: str
) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("control reached the provider boundary")

    monkeypatch.setattr(deepseek, "turn", unexpected)
    contexts = await _run_pair(tmp_path, world, arm, control=True)
    assert [m["text"] for m in world.messages] == [
        '{"ready":true}' if arm == "blind" else '{"starts":[2,3,4]}',
        '{"starts":[0,1,2]}',
    ]
    assert ("p1", ["m1"]) in world.deliveries and ("p2", ["m2"]) in world.deliveries
    assert world.submissions == [("p2", {"day": "Mon", "start": "11:00"})]
    for role, value in (("p1", [0, 1, 2]), ("p2", 2)):
        metadata = _metadata(contexts[role])
        assert metadata["stage"] == "complete" and metadata["own_prompt_sha256"] is None
        assert contexts[role].n_input_tokens is None
        assert _record(tmp_path, role, "calendar-control.json") == {
            "schema": "seatlab.calendar-control/v1",
            "run": f"cf-w00-d0-r0-{arm}",
            "seat": role,
            "value": value,
        }
        assert not (tmp_path / role / "calendar-turn.json").exists()


@pytest.mark.parametrize("invalid_role", ["cal_recheck", "cal_decide"])
async def test_grammar_invalid_stops_without_inventing_an_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: _World, invalid_role: str
) -> None:
    response = '{"starts":[4,1]}' if invalid_role == "cal_recheck" else '{"slot":true}'
    calls = []

    def turn(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        calls.append(role)
        return deepseek.SeatTurn(
            response if role == invalid_role else '{"starts":[2]}',
            deepseek.MODEL,
            12,
            1,
            4,
        )

    monkeypatch.setattr(deepseek, "turn", turn)
    contexts = await _run_pair(tmp_path, world, "peer")
    role = "p1" if invalid_role == "cal_recheck" else "p2"
    receipt = _record(tmp_path, role, "calendar-turn.json")
    assert receipt["status"] == "invalid" and receipt["response_text"] == response
    assert (
        receipt["value"] is None
        and _metadata(contexts[role])["stage"] == "model_invalid"
    )
    assert contexts[role].n_input_tokens == 12 and not world.submissions
    if role == "p1":
        assert calls == ["cal_recheck"]
        assert world.messages[1]["text"] == '{"model_invalid":true}'
        assert _metadata(contexts["p2"])["stage"] == "peer_model_invalid"
        assert not (tmp_path / "p2" / "calendar-turn.json").exists()
    else:
        assert calls == ["cal_recheck", "cal_decide"]
        assert world.messages[1]["text"] == '{"starts":[2]}'


async def test_billed_missing_text_is_not_a_model_invalid_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: _World
) -> None:
    def no_text(prompt: str, *, run_id: str, role: str) -> deepseek.SeatTurn:
        raise deepseek.SeatUnavailable(
            "provider_response_invalid", settled_usage=(12, 1, 4)
        )

    world.inboxes["p1"] = [
        {"id": "m1", "from": "p2", "to": "p1", "text": '{"ready":true}', "t": 1}
    ]
    monkeypatch.setattr(deepseek, "turn", no_text)
    actor = _seat(tmp_path / "p1", "p1", "blind", 0, control=False)
    context = AgentContext()
    with pytest.raises(RuntimeError, match="model_failed"):
        await actor.run("", world.environment("p1"), context)
    receipt = _record(tmp_path, "p1", "calendar-turn.json")
    assert receipt["status"] == "failed" and receipt["response_text"] is None
    assert (
        context.n_input_tokens == 12 and _metadata(context)["stage"] == "model_failed"
    )
    assert not world.messages and not world.submissions


@pytest.mark.parametrize(
    "wrong", ["scope", "caller", "table", "envelope", "wire", "timeout"]
)
async def test_unverified_inputs_fail_closed_before_turn_or_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: _World, wrong: str
) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("unverified inputs reached the provider boundary")

    monkeypatch.setattr(deepseek, "turn", unexpected)
    monkeypatch.setattr(calendar_seat, "_INBOX_TIMEOUT_SEC", 0.02)
    if wrong == "table":
        world.tables["p1"] = _TABLES[1]
    elif wrong in {"envelope", "wire"}:
        world.inboxes["p1"] = [
            {
                "id": "m1",
                "from": "p2",
                "to": "all" if wrong == "envelope" else "p1",
                "text": '{"ready":true}' if wrong == "envelope" else '{"starts":[1]}',
                "t": 1,
            }
        ]
    actor = _seat(tmp_path / "p1", "p1", "blind", 0, control=False)
    environment = world.environment(
        "p2" if wrong == "scope" else "p1",
        token_seat="p2" if wrong == "caller" else None,
    )
    context = AgentContext()
    stage = "inbox_timeout" if wrong == "timeout" else "invalid_transport"
    with pytest.raises(RuntimeError, match=stage):
        await actor.run("", environment, context)
    assert _metadata(context)["stage"] == stage
    assert not world.messages and not world.submissions
    assert not (tmp_path / "p1" / "calendar-turn.json").exists()
    if wrong in {"scope", "caller"}:
        assert world.operations == ([] if wrong == "scope" else [("p1", "whoami")])
    elif wrong == "table":
        assert world.operations == [("p1", "whoami"), ("p1", "table")]
    else:
        assert ("GET", "/messages", "p1") in world.requests
