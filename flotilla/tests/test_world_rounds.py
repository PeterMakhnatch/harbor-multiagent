"""World version 4, rounds mode (docs/COMMONS.md section 4), with the commons game and a real server."""

from __future__ import annotations

import json
import threading
import time
from fractions import Fraction

from conftest import FLOTILLA, load

COMMONS = load(FLOTILLA / "games" / "commons.py", "flotilla_test_game_commons_world")
SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_game_split_world")
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
    assert world.seat("p2", "POST", "/harvest", {"pass": True})[0] == 400
    assert world.seat("p2", "POST", "/submit", {"answer": {}})[0] == 404  # no answer slot in a game
    assert "repeat" in world.kinds()


def test_split_pass_blocks_the_deal_and_only_the_first_action_in_a_round_counts(start_world, tmp_path, monkeypatch):
    params = SPLIT._params(100, Fraction(4, 5), {"p1": 55, "p2": 20}, regime="b")
    path = tmp_path / "split.json"
    path.write_text(json.dumps(params))
    world = start_world("silent", FLOTILLA_GAME=str(FLOTILLA / "games" / "split.py"),
                        FLOTILLA_GAME_PARAMS=str(path), FLOTILLA_ROUND_SECONDS="30")
    clock = 0.0
    monkeypatch.setattr(world.module, "now", lambda: clock)
    everyone_calls(world)

    for bad in ({"pass": False}, {"pass": 1}, {"pass": "true"}, {"pass": True, "amount": 0}):
        assert world.seat("p1", "POST", "/claim", bad)[0] == 400
    assert world.seat("p1", "POST", "/claim", {"pass": True}) == (201, {"round": 1, "pass": True})
    assert world.seat("p1", "GET", "/state")[1]["you_requested"]
    other_view = world.seat("p2", "GET", "/state")[1]
    assert not other_view["you_requested"] and not other_view["closed_rounds"]
    assert world.seat("p1", "POST", "/claim", {"amount": 0})[0] == 409
    assert world.seat("p1", "POST", "/claim", {"pass": True})[0] == 409
    assert world.seat("p2", "POST", "/claim", {"amount": 55}) == (201, {"round": 1, "amount": 55})
    view = world.seat("p1", "GET", "/state")[1]
    first, = view["closed_rounds"]
    assert view["round"] == 2 and view["stock"] == 80 and not view["you_requested"]
    assert first["passed"] == ["p1"] and first["missed"] == [] and not first["agreed"]
    assert first["catch"] == {"p1": 0, "p2": 0}

    assert world.seat("p1", "POST", "/claim", {"amount": 40})[0] == 201
    assert world.seat("p1", "POST", "/claim", {"pass": True})[0] == 409
    clock = 31.0
    view = world.seat("p2", "GET", "/state")[1]
    second = view["closed_rounds"][1]
    assert second["passed"] == [] and second["missed"] == ["p2"] and not second["agreed"]
    assert view["round"] == 3 and view["stock"] == 64

    assert world.seat("p1", "POST", "/claim", {"amount": 0})[0] == 201
    assert world.seat("p2", "POST", "/claim", {"amount": 64})[0] == 201
    admin = world.request("GET", "/admin/state", token=world.admin)[1]
    log = admin["game"]["log"]
    assert log[0]["passed"] == ["p1"] and log[0]["missed"] == []
    assert log[-1]["agreed"] and log[-1]["passed"] == [] and log[-1]["missed"] == []
    score = SPLIT.score_game(params, log)
    assert score["deal_round"] == 3 and score["over_claim_rounds"] == 0
    assert score["passed"] == score["missed"] == 1 / 6
    assert [e["seat"] for e in world.events() if e["kind"] == "pass"] == ["p1"]
    assert [e["data"]["passed"] for e in world.events() if e["kind"] == "close"] == [["p1"], [], []]


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
    assert admin["world_version"] == 7 and len(admin["game"]["log"]) == 3


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
