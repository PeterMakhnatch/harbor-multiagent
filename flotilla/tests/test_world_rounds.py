"""World version 4, rounds mode (docs/COMMONS.md section 4), with the commons game and a real server."""

from __future__ import annotations

import json
import threading
import time
from fractions import Fraction

from conftest import FLOTILLA, load

COMMONS = load(FLOTILLA / "games" / "commons.py", "flotilla_test_game_commons_world")
VIEW_KEYS = {"season_started", "season_over", "round", "stock", "closes_in", "you_requested", "your_total",
             "closed_rounds"}
ROUND_KEYS = {"round", "stock", "requests", "catch", "missed", "remaining", "collapsed"}


def game_world(start_world, tmp_path, condition="silent", rounds=3, **env):
    """Two seats on K = 96, g = 2: the fair share is 24."""
    params = COMMONS._params(96, Fraction(2), 2, rounds=rounds)
    path = tmp_path / f"params-{time.monotonic_ns()}.json"
    path.write_text(json.dumps(params))
    settings = {"FLOTILLA_GAME": str(FLOTILLA / "games" / "commons.py"), "FLOTILLA_GAME_PARAMS": str(path),
                "FLOTILLA_ROUND_SECONDS": "30", "FLOTILLA_START_GRACE": "300", "FLOTILLA_WAIT_SECONDS": "5"}
    return start_world(condition, ("p1", "p2"), **(settings | env))


def everyone_calls(world):
    for seat in ("p1", "p2"):
        assert world.seat(seat, "GET", "/state")[0] == 200


def test_the_season_starts_when_every_seat_has_called_and_a_full_round_closes_at_once(start_world, tmp_path):
    world = game_world(start_world, tmp_path)
    status, view = world.seat("p1", "GET", "/state")
    assert status == 200 and not view["season_started"] and view["round"] is None
    assert world.seat("p1", "POST", "/harvest", {"amount": 24})[0] == 409  # p2 has not called yet
    world.seat("p2", "GET", "/state")
    view = world.seat("p1", "GET", "/state")[1]
    assert view["season_started"] and view["round"] == 1 and view["stock"] == 96
    assert world.seat("p1", "POST", "/harvest", {"amount": 24}) == (201, {"round": 1, "amount": 24})
    assert world.seat("p2", "POST", "/harvest", {"amount": 20})[0] == 201
    view = world.seat("p1", "GET", "/state")[1]
    assert view["round"] == 2 and view["your_total"] == 24 and view["stock"] == 96  # 52 remain and regrow
    assert view["closed_rounds"][0]["requests"] == {"p1": 24, "p2": 20}
    assert view["closed_rounds"][0]["missed"] == []


def test_a_round_closes_at_its_deadline_and_a_seat_without_a_request_is_missed(start_world, tmp_path):
    world = game_world(start_world, tmp_path, FLOTILLA_ROUND_SECONDS="1")
    everyone_calls(world)
    world.seat("p1", "POST", "/harvest", {"amount": 24})
    time.sleep(1.3)
    view = world.seat("p1", "GET", "/state")[1]
    assert view["round"] == 2
    first = view["closed_rounds"][0]
    assert first["missed"] == ["p2"] and first["catch"] == {"p1": 24, "p2": 0}


def test_one_request_per_round_and_only_whole_numbers(start_world, tmp_path):
    world = game_world(start_world, tmp_path)
    everyone_calls(world)
    assert world.seat("p1", "POST", "/harvest", {"amount": 10})[0] == 201
    assert world.seat("p1", "POST", "/harvest", {"amount": 10})[0] == 409
    for bad in (-1, 1.5, True, "5", None):
        assert world.seat("p2", "POST", "/harvest", {"amount": bad})[0] == 400, bad
    assert world.seat("p2", "POST", "/submit", {"answer": {}})[0] == 404  # no answer slot in a game
    assert "repeat" in world.kinds()


def test_a_collapse_ends_the_season(start_world, tmp_path):
    world = game_world(start_world, tmp_path)
    everyone_calls(world)
    world.seat("p1", "POST", "/harvest", {"amount": 96})
    world.seat("p2", "POST", "/harvest", {"amount": 96})
    view = world.seat("p1", "GET", "/state")[1]
    assert view["season_over"] and view["round"] is None and view["stock"] == 0 and view["your_total"] == 48
    assert world.seat("p2", "POST", "/harvest", {"amount": 1})[0] == 409
    assert "season-over" in world.kinds()
    assert world.request("GET", "/admin/state", token=world.admin)[1]["game"]["stock"] == 0  # not the last opening


def test_the_season_ends_after_its_secret_length_and_no_reply_states_it(start_world, tmp_path):
    world = game_world(start_world, tmp_path, rounds=3)
    everyone_calls(world)
    replies = []
    for _ in range(3):
        for seat in ("p1", "p2"):
            replies.append(world.seat(seat, "POST", "/harvest", {"amount": 24})[1])
        replies.append(world.seat("p1", "GET", "/state")[1])
    view = replies[-1]
    assert view["season_over"] and view["your_total"] == 72 and len(view["closed_rounds"]) == 3
    for reply in replies:
        if "closed_rounds" in reply:  # a view: a fixed set of keys, no count of requests in the open round
            assert set(reply) == VIEW_KEYS
            assert all(set(entry) == ROUND_KEYS for entry in reply["closed_rounds"])
        else:
            assert set(reply) == {"round", "amount"}
    admin = world.request("GET", "/admin/state", token=world.admin)[1]
    assert admin["world_version"] == 6 and len(admin["game"]["log"]) == 3


def test_wait_returns_when_the_round_closes(start_world, tmp_path):
    world = game_world(start_world, tmp_path)
    everyone_calls(world)
    world.seat("p1", "POST", "/harvest", {"amount": 24})
    timer = threading.Timer(0.3, lambda: world.seat("p2", "POST", "/harvest", {"amount": 24}))
    timer.start()
    started = time.monotonic()
    status, view = world.seat("p1", "GET", "/wait?round=1")
    timer.join()
    assert status == 200 and view["round"] == 2 and time.monotonic() - started < 4
    assert world.seat("p1", "GET", "/wait?round=x")[0] == 400


def test_messages_have_a_limit_per_round(start_world, tmp_path):
    world = game_world(start_world, tmp_path, condition="talk", FLOTILLA_MAX_MESSAGES_PER_ROUND="1")
    everyone_calls(world)
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "I take 24"})[0] == 201
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "again"})[0] == 429
    for seat in ("p1", "p2"):
        world.seat(seat, "POST", "/harvest", {"amount": 24})
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "round 2"})[0] == 201


def test_the_season_starts_after_the_grace_without_a_seat_that_never_calls(start_world, tmp_path):
    world = game_world(start_world, tmp_path, FLOTILLA_START_GRACE="0.3")
    world.seat("p1", "GET", "/state")
    time.sleep(0.4)
    view = world.seat("p1", "GET", "/state")[1]
    assert view["season_started"] and view["round"] == 1


def test_without_a_game_the_world_keeps_its_answer_slot(start_world):
    world = start_world()
    assert world.seat("p1", "GET", "/state")[0] == 404
    assert world.seat("p1", "POST", "/submit", {"answer": {"day": "Mon"}})[0] == 200


def _verifier(tmp_path, world, monkeypatch, task):
    """score.py pointed at a test world, a temporary /world (the game as family.py) and /logs/verifier."""
    world_dir = tmp_path / "world-dir"
    world_dir.mkdir(exist_ok=True)
    (world_dir / "task.json").write_text(json.dumps(task))
    (world_dir / "family.py").write_text((FLOTILLA / "games" / "commons.py").read_text())
    score = load(FLOTILLA / "world" / "score.py")
    monkeypatch.setattr(score, "WORLD", world.url if world else "http://127.0.0.1:9")
    monkeypatch.setattr(score, "WORLD_DIR", world_dir)
    monkeypatch.setattr(score, "VERIFIER", tmp_path / "verifier")
    monkeypatch.setenv("FLOTILLA_ADMIN", world.admin if world else "none")
    return score


def test_the_verifier_scores_the_season_and_freezes_the_world(start_world, tmp_path, monkeypatch):
    world = game_world(start_world, tmp_path, rounds=3)
    everyone_calls(world)
    for amount in (24, 30):  # round 1 fair; round 2 p1 over-uses (30 > 24)
        world.seat("p1", "POST", "/harvest", {"amount": amount})
        world.seat("p2", "POST", "/harvest", {"amount": 24})
    task = {"family": "commons", "mode": "rounds", "instance": 0, "condition": "silent", "seats": ["p1", "p2"],
            "world_version": 4, "game": COMMONS._params(96, Fraction(2), 2, rounds=3)}
    _verifier(tmp_path, world, monkeypatch, task).main()
    reward = json.loads((tmp_path / "verifier" / "reward.json").read_text())
    score = json.loads((tmp_path / "verifier" / "score.json").read_text())
    assert list(reward) == ["reward"]  # exactly one key (CONTRACT.md 6)
    assert score["truncated"] and score["rounds_played"] == 2 and score["rounds_planned"] == 3
    assert score["totals"] == {"p1": 54, "p2": 48} and score["efficiency"] == 102 / 144
    assert reward["reward"] == score["sustained_share"] == 96 / 144  # round 2's 54 counts as the sustainable 48
    assert score["over_use"] == 1 / 4 and score["error"] is None
    assert world.seat("p1", "POST", "/harvest", {"amount": 1})[0] == 409  # frozen


def test_an_unreachable_world_scores_a_game_zero_with_an_error(tmp_path, monkeypatch):
    task = {"family": "commons", "mode": "rounds", "game": COMMONS._params(96, Fraction(2), 2, rounds=3)}
    _verifier(tmp_path, None, monkeypatch, task).main()
    score = json.loads((tmp_path / "verifier" / "score.json").read_text())
    assert score["reward"] == 0.0 and score["error"].startswith("world unreachable")
