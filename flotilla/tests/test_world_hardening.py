"""World version 5: the fixes of the review of fork PR #18 (world half): M2, M3, M5, M6, S1 and S2."""

from __future__ import annotations

import time

import pytest
from conftest import FLOTILLA, load


def test_messages_after_the_freeze_are_refused_and_logged(start_world):  # M2
    world = start_world()
    assert world.request("POST", "/admin/freeze", token=world.admin, body={})[0] == 200
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "after the end"})[0] == 409
    assert "late" in world.kinds()


def test_a_non_finite_number_is_refused(start_world):  # M3
    world = start_world()
    status, _reply, _headers = world.request("POST", "/submit", token=world.tokens["p1"],
                                             raw=b'{"answer": {"start": 1e999}}')
    assert status == 400
    assert "Infinity" not in (world.logs / "events.jsonl").read_text()


def test_the_admin_token_is_not_a_seat(start_world):  # M6
    world = start_world()
    for method, path, body in (("POST", "/submit", {"answer": {"day": "Mon"}}),
                               ("POST", "/messages", {"to": "all", "text": "x"}), ("GET", "/whoami", None)):
        assert world.request(method, path, token=world.admin, body=body)[0] == 403, path
    assert "denied" in world.kinds()
    assert world.request("GET", "/admin/state", token=world.admin)[1]["submissions"] == []


@pytest.mark.parametrize(("seats", "tokens", "admin", "problem"), [
    ("p1,admin", "p1:token-p1-000000000,admin:token-ad-000000000", "admin-token", "a seat is named admin"),
    ("p1,p2", "p1:same-token-00000000,p2:same-token-00000000", "admin-token", "two seats share a token"),
    ("p1,p2", "p1:,p2:token-p2-000000000", "admin-token", "a seat token is empty"),
    ("p1", "p1:token-p1-000000000,p9:token-p9-000000000", "admin-token", "a token names a seat"),
    ("p1", "p1:token-p1-000000000", "", "no admin token"),
    ("p1", "p1:admin-token", "admin-token", "the admin token is also a seat token"),
])
def test_the_world_refuses_a_config_that_confuses_seats(tmp_path, monkeypatch, seats, tokens, admin, problem):  # M5
    monkeypatch.delenv("FLOTILLA_GAME", raising=False)
    monkeypatch.setenv("FLOTILLA_LOGS", str(tmp_path / "logs"))
    monkeypatch.setenv("FLOTILLA_SEATS", seats)
    monkeypatch.setenv("FLOTILLA_TOKENS", tokens)
    monkeypatch.setenv("FLOTILLA_ADMIN", admin)
    world = load(FLOTILLA / "world" / "world.py")
    with pytest.raises(SystemExit, match=problem):
        world.serve("127.0.0.1", 0)


def test_the_world_freezes_itself_after_the_play_time(start_world):  # S1
    world = start_world(FLOTILLA_PLAY_SECONDS="0.3")
    assert world.seat("p1", "POST", "/submit", {"answer": {"day": "Mon"}})[0] == 200  # the first call starts it
    time.sleep(0.4)
    assert world.seat("p2", "POST", "/submit", {"answer": {"day": "Tue"}})[0] == 409
    assert world.seat("p2", "POST", "/messages", {"to": "all", "text": "late"})[0] == 409
    freezes = [event for event in world.events() if event["kind"] == "freeze"]
    assert freezes and freezes[0]["data"]["reason"] == "play time is over"
    state = world.request("GET", "/admin/state", token=world.admin)[1]
    assert [submission["answer"] for submission in state["submissions"]] == [{"day": "Mon"}]


def test_the_verifier_counts_world_restarts(tmp_path, monkeypatch):  # S2
    score = load(FLOTILLA / "world" / "score.py")
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(score, "EVENTS", events)
    assert score.world_restarts() == 0
    events.write_text('{"data": {}, "kind": "start", "seq": 1}\n{"data": {}, "kind": "submit", "seq": 2}\n')
    assert score.world_restarts() == 0
    events.write_text(events.read_text() + '{"data": {}, "kind": "start", "seq": 1}\n')
    assert score.world_restarts() == 1
