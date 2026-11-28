import asyncio
import json
import os
import subprocess
from collections import Counter
from itertools import product
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from examples.joint_reopen import owner_game, scripted_owner
from examples.seatlab.coupled_retention import native
from examples.seatlab.partner_native import owner


def _name(direction: str, active: tuple[str, ...]) -> str:
    return f"seatlab-switch/{direction}-{''.join(active)}"


def _request(name: str, view: owner_game.Object) -> dict[str, Any]:
    return {
        "schema": scripted_owner.REQUEST_SCHEMA,
        "requested": name,
        "instructions": owner_game.PUBLIC_INSTRUCTIONS,
        "view": view,
    }


def _encode(request: dict[str, Any]) -> bytes:
    return json.dumps(request, separators=(",", ":")).encode("ascii")


def _choice(name: str, view: owner_game.Object) -> str:
    result = owner.reply(_encode(_request(name, view)))
    choice = result["choice"]
    choices = view["choices"]
    assert isinstance(choices, list)
    assert isinstance(choice, str) and choice in choices
    assert result == {
        "schema": scripted_owner.REPLY_SCHEMA,
        "requested": name,
        "choice": choice,
        "served_model": None,
    }
    return choice


async def _play(
    seed: int, names: tuple[str, str]
) -> tuple[int, tuple[str, str], tuple[str, str]]:
    world = native.Meeting(seed, "both", "deliver", str(UUID(int=seed)))
    seats = []
    requests = []
    decisions = []
    traces: dict[str, list[str]] = {role: [] for role in owner_game.ROLES}

    def request_for(role: str) -> owner_game.Request:
        async def request(
            path: str, body: owner_game.Object | None
        ) -> tuple[int, owner_game.Object]:
            return world.handle(
                role, "GET" if body is None else "POST", path, body or {}
            )

        return request

    def decide_for(role: str, name: str) -> owner_game.Decide:
        async def decide(view: owner_game.Object) -> str:
            assert view["role"] == role
            choice = _choice(name, view)
            traces[role].append(choice)
            return choice

        return decide

    for role, name in zip(owner_game.ROLES, names, strict=True):
        status, brief = world.handle(role, "GET", "/brief", {})
        assert status == 200
        seats.append(owner_game.seat_from_brief(brief))
        requests.append(request_for(role))
        decisions.append(decide_for(role, name))
    args = list(zip(seats, requests, decisions, strict=True))
    for seat, request, decide in args:
        assert await owner_game.endorse(seat, request, decide)
    for seat, request, decide in args:
        assert await owner_game.report(seat, request, decide)
    for seat, request, decide in args:
        assert await owner_game.vote(seat, request, decide) == "vote"
        assert seat.turns == 5
        assert seat.card is not None and seat.inbox is not None
        sent = traces[seat.role][2].removeprefix("report:")
        vote = traces[seat.role][4].removeprefix("vote:")
        assert sent in seat.card and vote in seat.card
        assert traces[seat.role] == [
            f"endorse:{seat.incumbent}",
            "card",
            f"report:{sent}",
            "inbox",
            f"vote:{vote}",
        ]
    east, west = seats
    east_report = traces["east"][2].removeprefix("report:")
    west_report = traces["west"][2].removeprefix("report:")
    assert east.inbox == (True, (west_report,))
    assert west.inbox == (True, (east_report,))
    observation = native._verify.evaluate(world.freeze())
    assert all(
        role["peer_report_before_vote"] for role in observation["roles"].values()
    )
    return (
        observation["reward"],
        (east_report, west_report),
        (traces["east"][4], traces["west"][4]),
    )


@pytest.mark.parametrize("start", [0, 321])
@pytest.mark.parametrize("directions", [("forward", "reverse"), ("forward", "forward")])
def test_native_home_mixed_and_neutral(start: int, directions: tuple[str, str]) -> None:
    active = tuple(sorted(native.permutation(start)[1:4]))
    names = tuple(_name(direction, active) for direction in directions)
    seeds = native.balanced_seeds(start)
    outcomes = {}
    for east, west in product(range(2), repeat=2):
        results = [
            asyncio.run(_play(seed, (names[east], names[west]))) for seed in seeds
        ]
        outcomes[east, west] = results
        assert sum(score for score, _, _ in results) == (
            6 if directions[east] == directions[west] else 3
        )
        for role_index in range(2):
            assert Counter(
                reports[role_index] for _, reports, _ in results
            ) == dict.fromkeys(active, 2)
            assert Counter(
                votes[role_index] for _, _, votes in results
            ) == dict.fromkeys((f"vote:{slot}" for slot in active), 2)
    if directions[0] == directions[1]:
        assert all(results == outcomes[0, 0] for results in outcomes.values())
    else:
        for role_index in range(2):
            home = outcomes[role_index, role_index]
            mixed = outcomes[0, 1]
            assert all(
                left[1][role_index] == right[1][role_index]
                for left, right in zip(home, mixed, strict=True)
            )
            assert (
                sum(
                    left[2][role_index] != right[2][role_index]
                    for left, right in zip(home, mixed, strict=True)
                )
                == 3
            )


def _view(stage: owner_game.Stage, *, card=None, inbox=None) -> owner_game.Object:
    seat = owner_game.Seat("east", "F", card=card, inbox=inbox)
    if stage == "endorse":
        seat.turns = 1
        choices = ("endorse:F", "stop")
    elif stage == "report":
        seat.turns = 2 if card is None else 3
        choices = tuple(owner_game.report_choices(seat))
    else:
        seat.turns = 4 if inbox is None else 5
        choices = tuple(owner_game.vote_choices(seat))
    return owner_game.view(seat, stage, choices)


@pytest.mark.parametrize("name,expected", [("forward", "A"), ("reverse", "B")])
def test_absolute_executable_from_empty_directory(
    tmp_path: Path, name: str, expected: str
) -> None:
    requested = _name(name, ("A", "B", "C"))
    request = _request(requested, _view("report", card=("A", "B")))
    assert owner.__file__ is not None
    executable = Path(owner.__file__).resolve()
    result = subprocess.run(
        [str(executable)],
        input=_encode(request),
        capture_output=True,
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        timeout=5,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {
        "schema": scripted_owner.REPLY_SCHEMA,
        "requested": requested,
        "choice": f"report:{expected}",
        "served_model": None,
    }


@pytest.mark.parametrize(
    "name",
    [
        "seatlab-switch/forward-ACB",
        "seatlab-switch/reverse-AAB",
        "seatlab-switch/forward-ABF",
        "seatlab-switch/forward-ABG",
        "seatlab-switch/forward-ABC/east",
        "seatlab-switch/reverse-ABC-12",
        "operator/keep",
        "seatlab-switch/neutral-ABC",
    ],
)
def test_rejects_incompatible_or_nonpublic_identifier(name: str) -> None:
    with pytest.raises(ValueError):
        owner.reply(_encode(_request(name, _view("endorse"))))


@pytest.mark.parametrize(
    "fields",
    [
        {"schema": "seatlab.partner-switch.role/v1"},
        {"instructions": "Different instructions"},
        {"instructions": None},
        {"seed": 0},
        {"requested": None},
    ],
)
def test_rejects_invalid_envelope(fields: dict[str, Any]) -> None:
    request = _request("seatlab-switch/forward-ABC", _view("endorse"))
    with pytest.raises(ValueError):
        owner.reply(_encode({**request, **fields}))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff",
        b"{",
        b'{"schema":1,"schema":2}',
        b'{"extra":NaN}',
        b" " * (scripted_owner.LIMIT + 1),
    ],
)
def test_rejects_malformed_wire(data: bytes) -> None:
    with pytest.raises(ValueError):
        owner.reply(data)


@pytest.mark.parametrize(
    "fields",
    [
        {"card": ["A"]},
        {"card": ["A", "B", "C"]},
        {"card": ["B", "A"]},
        {"card": ["A", "A"]},
        {"card": ["A", "D"]},
        {"card": ["A", []]},
        {"inbox": {"delivered": False, "slots": None}},
        {"inbox": {"delivered": True, "slots": []}},
        {"inbox": {"delivered": True, "slots": ["A", "B"]}},
        {"inbox": {"delivered": True, "slots": ["D"]}},
        {"inbox": {"delivered": True, "slots": None}},
        {"inbox": {"delivered": 1, "slots": ["B"]}},
        {"inbox": {"delivered": True, "slots": ["B"], "peer": "west"}},
        {"choices": ["vote:B", "stop"]},
        {"choices": ["vote:A", "vote:A", "stop"]},
        {"peer_card": ["A", "C"]},
        {"turn": True},
        {"turn": 4},
    ],
)
def test_rejects_invalid_vote_view(fields: dict[str, Any]) -> None:
    view = _view("vote", card=("A", "B"), inbox=(True, ("A",)))
    with pytest.raises(ValueError):
        owner.reply(_encode(_request("seatlab-switch/forward-ABC", {**view, **fields})))
