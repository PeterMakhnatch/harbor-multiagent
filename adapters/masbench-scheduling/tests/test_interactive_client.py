import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from test_interactive_world import relay

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import interactive_client  # noqa: E402
from interactive_client import (  # noqa: E402
    coordinate_round,
    feasible_starts,
    next_start,
    run_actor,
)

ROLES = ("m1", "m2", "m3", "m4", "m5")


def _view(role, busy):
    return {
        "role": role,
        "mode": "cursor",
        "horizon": 8,
        "duration": 2,
        "slots_per_day": 4,
        "busy": busy,
    }


def _wire(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def _hash(document):
    return hashlib.sha256(_wire(document)).hexdigest()


def _deliver(path, document):
    staging = path.with_suffix(".pending")
    staging.write_text(json.dumps(document, indent=2) + "\n", encoding="ascii")
    staging.rename(path)


def _await_submission(path, deadline, expected):
    while time.monotonic() < deadline:
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            time.sleep(0.005)
        else:
            assert payload == _wire(expected), path
            return
    pytest.fail(f"no submission at {path}")


def _port(parent, role, busy):
    exchange = parent / role
    (exchange / "out").mkdir(parents=True)
    view = _view(role, busy)
    _deliver(exchange / "input.json", view)
    return exchange, view


def test_feasible_starts_use_half_open_intervals_and_day_boundaries():
    view = _view("m4", [1, 7])
    starts = feasible_starts(view)
    assert starts == [2, 4, 5]
    assert [next_start(starts, bound) for bound in (0, 2, 3, 5, 6)] == [
        2,
        2,
        4,
        5,
        None,
    ]


@pytest.mark.parametrize("busy", [[True], [-1], [8], ["1"]])
def test_local_view_rejects_invalid_busy_ticks(busy):
    with pytest.raises(ValueError, match="busy ticks"):
        feasible_starts(_view("m2", busy))


def test_coordinator_validates_every_reply_even_if_a_seat_is_exhausted():
    replies = dict.fromkeys(ROLES, 12)
    replies["m1"] = None
    replies["m5"] = True
    with pytest.raises(ValueError, match="m5: invalid reply"):
        coordinate_round(replies, bound=0, horizon=20)


def test_coordinator_rejects_a_reply_that_discards_a_known_start():
    previous = dict.fromkeys(ROLES, 3)
    previous["m4"] = 4
    replies = dict.fromkeys(ROLES, 5)
    with pytest.raises(ValueError, match="m4: reply contradicts known start 4"):
        coordinate_round(replies, bound=4, horizon=20, previous=previous)


@pytest.mark.parametrize("exhausted", [False, True])
def test_all_five_actors_exchange_only_cursor_files(tmp_path, exhausted):
    calendars = {
        "m1": list(range(8)) if exhausted else [0, 4],
        "m2": [0, 1],
        "m3": [0, 1, 2, 3],
        "m4": [],
        "m5": [0, 1, 2],
    }
    ports = {role: _port(tmp_path, role, calendars[role]) for role in ROLES}
    leader, prior_view = ports["m1"]
    rounds = (
        [(0, (None, 2, 4, 0, 4))]
        if exhausted
        else [(0, (1, 2, 4, 0, 4)), (4, (5, 4, 4, 4, 4)), (5, (5, 5, 5, 5, 5))]
    )
    deadline = time.monotonic() + 5
    with ThreadPoolExecutor(max_workers=5) as executor:
        actors = [
            executor.submit(run_actor, role, port, timeout=5)
            for role, (port, _) in ports.items()
        ]
        for number, (bound, ticks) in enumerate(rounds, start=1):
            suffix = f"{number:03d}.json"
            query_name = f"query-{suffix}"
            reply_name = f"reply-{suffix}"
            query = {"round": number, "bound": bound}
            _await_submission(
                leader / "out" / query_name,
                deadline,
                {**query, "view_sha256": _hash(prior_view)},
            )
            replies = dict(zip(ROLES, ticks, strict=True))
            for role, (port, view) in ports.items():
                _deliver(port / query_name, query)
                _await_submission(
                    port / "out" / reply_name,
                    deadline,
                    {
                        "role": role,
                        "round": number,
                        "next": replies[role],
                        "initial_sha256": _hash(view),
                        "query_sha256": _hash(query),
                    },
                )
            prior_view = {**query, "replies": replies}
            _deliver(leader / f"round-{suffix}", prior_view)
        _await_submission(
            leader / "out" / "choice.json",
            deadline,
            {
                "round": len(rounds),
                "start": None if exhausted else 5,
                "view_sha256": _hash(prior_view),
            },
        )
        assert not any(actor.done() for actor in actors)
        for port, _ in ports.values():
            _deliver(port / "done.json", {"done": True})
        for actor in actors:
            assert actor.result() is None
    reply_names = {f"reply-{number:03d}.json" for number in range(1, len(rounds) + 1)}
    query_names = {f"query-{number:03d}.json" for number in range(1, len(rounds) + 1)}
    assert {path.name for path in (leader / "out").iterdir()} == (
        {"choice.json"} | reply_names | query_names
    )
    for role in ROLES[1:]:
        port, _ = ports[role]
        assert {path.name for path in (port / "out").iterdir()} == reply_names
        assert not list(port.glob("round-*.json"))


def test_five_actors_commit_null_after_eight_complete_rounds(tmp_path, monkeypatch):
    horizon = 16
    free = {
        **dict.fromkeys(ROLES, range(horizon)),
        "m1": [0, 2, 4, 6, 8, 10],
        "m2": [1, 3, 5, 7, 9, 10],
    }
    case = {
        "case_id": "2/1",
        "horizon": horizon,
        "duration": 1,
        "slots_per_day": horizon,
        "members": {
            role: sorted(set(range(horizon)).difference(free[role])) for role in ROLES
        },
    }
    ports = tmp_path / "ports"
    ports.mkdir()
    artifact = tmp_path / "world.calendar.json"
    world = relay.InteractiveWorld(case, "cursor", ports, artifact)
    leader = ports / "m1"
    choice = leader / "out" / "choice.json"
    choice_ready = Event()
    release_choice = Event()
    # Observe the broker before publication without replacing the actor's action.
    submit = interactive_client._submit

    def hold_choice(path, document):
        if path == choice:
            choice_ready.set()
            if not release_choice.wait(5):
                raise TimeoutError("test did not release the coordinator action")
        submit(path, document)

    monkeypatch.setattr(interactive_client, "_submit", hold_choice)
    with ThreadPoolExecutor(max_workers=5) as executor:
        actors = [
            executor.submit(run_actor, role, ports / role, timeout=5) for role in ROLES
        ]
        try:
            deadline = time.monotonic() + 5
            while not choice_ready.is_set() and time.monotonic() < deadline:
                assert world.advance() is False
                choice_ready.wait(0.005)
            assert choice_ready.is_set(), "coordinator did not attempt a choice"
            assert (leader / "round-008.json").is_file()
            assert not choice.exists()
            assert world.advance() is False
            with pytest.raises(TimeoutError, match="waiting for a coordinator action"):
                world.run(timeout=0)
            assert not artifact.exists()
            assert not any((ports / role / "done.json").exists() for role in ROLES)
            assert not any(actor.done() for actor in actors)
        finally:
            release_choice.set()
        world.run(timeout=5)
        for actor in actors:
            assert actor.result() is None

    views = {
        role: json.loads((ports / role / "input.json").read_bytes()) for role in ROLES
    }
    prior_view = views["m1"]
    reply_names, query_names = set(), set()
    for number in range(1, 9):
        bound = number - 1
        query = {"round": number, "bound": bound}
        query_name = f"query-{number:03d}.json"
        reply_name = f"reply-{number:03d}.json"
        query_names.add(query_name)
        reply_names.add(reply_name)
        assert (leader / "out" / query_name).read_bytes() == _wire(
            {**query, "view_sha256": _hash(prior_view)}
        )
        replies = {
            **dict.fromkeys(ROLES, bound),
            "m1": bound + bound % 2,
            "m2": bound + number % 2,
        }
        for role in ROLES:
            port = ports / role
            assert (port / query_name).read_bytes() == _wire(query)
            assert (port / "out" / reply_name).read_bytes() == _wire(
                {
                    "role": role,
                    "round": number,
                    "next": replies[role],
                    "initial_sha256": _hash(views[role]),
                    "query_sha256": _hash(query),
                }
            )
        prior_view = {**query, "replies": replies}
        assert (leader / f"round-{number:03d}.json").read_bytes() == _wire(prior_view)
    assert choice.read_bytes() == _wire(
        {"round": 8, "start": None, "view_sha256": _hash(prior_view)}
    )
    record = json.loads(artifact.read_bytes())
    assert record["choice"] is None and record["acknowledged"] is True
    assert len(record["rounds"]) == 8
    assert record["choice_view_sha256"] == _hash(prior_view)
    assert {path.name for path in (leader / "out").iterdir()} == (
        {"choice.json"} | reply_names | query_names
    )
    for role in ROLES:
        assert not (ports / role / "query-009.json").exists()
    for role in ROLES[1:]:
        port = ports / role
        assert {path.name for path in (port / "out").iterdir()} == reply_names
        assert not list(port.glob("round-*.json"))


@pytest.mark.parametrize(
    "query", [{"round": True, "bound": 0}, {"round": 1, "bound": 2}]
)
def test_seat_rejects_malformed_first_query(tmp_path, query):
    port, _ = _port(tmp_path, "m2", [])
    _deliver(port / "query-001.json", query)
    with pytest.raises(ValueError, match="invalid query: query-001.json"):
        run_actor("m2", port, timeout=1)
    assert not list((port / "out").iterdir())


def test_seat_times_out_waiting_for_the_next_message(tmp_path):
    port, _ = _port(tmp_path, "m2", [])
    _deliver(port / "query-001.json", {"round": 1, "bound": 0})
    with pytest.raises(TimeoutError, match="query-002.json"):
        run_actor("m2", port, timeout=0.5)
    assert (port / "out" / "reply-001.json").is_file()


def test_seat_does_not_replace_a_published_reply(tmp_path):
    port, _ = _port(tmp_path, "m2", [])
    _deliver(port / "query-001.json", {"round": 1, "bound": 0})
    reply = port / "out" / "reply-001.json"
    reply.write_bytes(b"first publication")
    with pytest.raises(FileExistsError):
        run_actor("m2", port, timeout=1)
    assert reply.read_bytes() == b"first publication"
    assert {path.name for path in reply.parent.iterdir()} == {reply.name}


def test_coordinator_does_not_commit_when_its_reply_changes(tmp_path):
    port, _ = _port(tmp_path, "m1", [])
    query = {"round": 1, "bound": 0}
    _deliver(port / "query-001.json", query)
    _deliver(port / "round-001.json", {**query, "replies": dict.fromkeys(ROLES, 1)})
    with pytest.raises(ValueError, match="changed the coordinator's own reply"):
        run_actor("m1", port, timeout=1)
    assert not (port / "out" / "choice.json").exists()
    assert not (port / "out" / "query-002.json").exists()
