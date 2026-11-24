"""World version 4, rounds mode (docs/COMMONS.md section 4), with the commons game and a real server."""

from __future__ import annotations

import json
import random
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


def split_world(start_world, tmp_path, condition="propose", seats=("p1", "p2")):
    params = SPLIT._params(100, Fraction(4, 5), {seat: 55 if seat == "p1" else 20 for seat in seats}, regime="b")
    path = tmp_path / "split.json"
    path.write_text(json.dumps(params))
    world = start_world(condition, seats, FLOTILLA_GAME=str(FLOTILLA / "games" / "split.py"),
                        FLOTILLA_GAME_PARAMS=str(path), FLOTILLA_ROUND_SECONDS="30")
    return world, params


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
    assert world.seat("p2", "POST", "/propose", {"split": {"p1": 24, "p2": 24}})[0] == 404
    assert world.seat("p2", "POST", "/accept", {"id": "1-1"})[0] == 404
    assert "repeat" in world.kinds()


def test_split_pass_blocks_the_deal_and_only_the_first_action_in_a_round_counts(start_world, tmp_path, monkeypatch):
    world, params = split_world(start_world, tmp_path, condition="silent")
    clock = 0.0
    monkeypatch.setattr(world.module, "now", lambda: clock)
    everyone_calls(world)
    assert "proposals" not in world.seat("p1", "GET", "/state")[1]
    assert world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 40}})[0] == 404
    assert world.seat("p2", "POST", "/accept", {"id": "1-1"})[0] == 404

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
    assert [record["inactive"] for record in log] == [record["missed"] for record in log]
    score = SPLIT.score_game(params, log)
    assert score["deal_round"] == 3 and score["over_claim_rounds"] == 0
    assert score["passed"] == score["missed"] == 1 / 6
    assert [e["seat"] for e in world.events() if e["kind"] == "pass"] == ["p1"]
    assert [e["data"]["passed"] for e in world.events() if e["kind"] == "close"] == [["p1"], [], []]


def test_an_accepted_split_proposal_settles_immediately_and_scores_as_one_deal(start_world, tmp_path, monkeypatch):
    world, params = split_world(start_world, tmp_path)
    monkeypatch.setattr(world.module, "now", lambda: 0.0)
    split = {"p1": 0, "p2": 72}
    assert world.seat("p1", "POST", "/propose", {"split": split})[0] == 409
    everyone_calls(world)
    assert world.seat("p1", "GET", "/messages")[0] == 403
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "test"})[0] == 403
    status, proposal = world.seat("p1", "POST", "/propose", {"split": split})
    assert status == 201 and proposal == {"id": proposal["id"], "proposer": "p1", "split": split, "time": 0.0}
    public = {"id": proposal["id"], "proposer": "p1", "split": split, "round": 1}
    for seat in params["seats"]:
        assert world.seat(seat, "GET", "/state")[1]["proposals"] == [public]
    accept = {"id": proposal["id"]}
    assert world.seat("p1", "POST", "/accept", accept)[0] == 409
    assert world.seat("p2", "POST", "/claim", {"amount": 72})[0] == 201
    assert world.seat("p1", "POST", "/claim", {"pass": True})[0] == 201
    assert world.seat("p1", "GET", "/state")[1]["round"] == 1
    assert world.seat("p2", "POST", "/accept", accept) == (201, {**accept, "accepted": True})
    for seat in params["seats"]:
        view = world.seat(seat, "GET", "/state")[1]
        assert view["season_over"] and view["your_total"] == split[seat] and view["proposals"] == []
    assert world.seat("p2", "POST", "/propose", {"split": split})[0] == 409
    assert world.seat("p2", "POST", "/accept", accept)[0] == 409
    assert world.seat("p2", "POST", "/claim", {"amount": 72})[0] == 409

    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    deal, = log
    assert deal["via"] == "proposal" and deal["proposal_id"] == proposal["id"]
    assert deal["closed"] == deal["opened"] == 0.0 and deal["missed"] == deal["passed"] == []
    assert deal["agreed"] and deal["requests"] == deal["catch"] == split and deal["remaining"] == 28
    score = SPLIT.score_game(params, log)
    assert score["via"] == "proposal" and score["proposals"] == score["acceptances"] == 1
    assert score["reward"] == 0.72 and score["totals"] == split and score["deal_round"] == 1
    events = [event for event in world.events() if event["kind"] in {"propose", "accept", "close"}]
    assert [event["kind"] for event in events] == ["propose", "accept", "close"]
    assert events[-1]["data"]["proposal_id"] == proposal["id"]


def test_split_proposals_validate_each_amount_and_lapse_when_the_pot_shrinks(start_world, tmp_path, monkeypatch):
    world, params = split_world(start_world, tmp_path)
    clock = 0.0
    monkeypatch.setattr(world.module, "now", lambda: clock)
    everyone_calls(world)
    for bad in ({"p1": 61, "p2": 40}, {"p1": -1, "p2": 1}, {"p1": 1, "p2": 1.0},
                {"p1": True, "p2": 10}, {"p1": 10, "p3": 10}, {"p1": 10}, {"p1": 0, "p2": 0, "p3": 1}):
        assert world.seat("p1", "POST", "/propose", {"split": bad})[0] == 400, bad
    for bad in (None, 1, [], "", {}):
        assert world.seat("p2", "POST", "/accept", {"id": bad})[0] == 400, bad
    assert world.seat("p2", "GET", "/state")[1]["proposals"] == []
    assert world.seat("p2", "POST", "/accept", {"id": "9-9"}) == (409, {"error": "no proposal with that ID"})

    split = {"split": {"p1": 60, "p2": 40}}
    first = world.seat("p1", "POST", "/propose", split)[1]
    second = world.seat("p1", "POST", "/propose", split)[1]
    assert second["replaced"] == first["id"]
    assert world.seat("p1", "POST", "/propose", split)[0] == 429
    third = world.seat("p2", "POST", "/propose", split)[1]
    shown = world.seat("p2", "GET", "/state")[1]["proposals"]
    assert [item["id"] for item in shown] == [second["id"], third["id"]]
    assert all(set(item) == {"id", "proposer", "split", "round"} for item in shown)
    clock = 30.0
    replaced = world.seat("p2", "POST", "/accept", {"id": first["id"]})
    assert replaced == (409, {"error": "that proposal was replaced"})
    view = world.seat("p1", "GET", "/state")[1]
    assert view["round"] == 2 and view["stock"] == 80 and view["proposals"] == []
    lapsed = world.seat("p2", "POST", "/accept", {"id": second["id"]})
    assert lapsed == (409, {"error": "that proposal lapsed"})
    assert world.seat("p2", "POST", "/accept", {"id": third["id"]}) == lapsed
    assert world.seat("p1", "POST", "/propose", split)[0] == 400
    status, next_proposal = world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 20}})
    assert status == 201 and next_proposal["id"] != first["id"]
    assert world.seat("p2", "POST", "/accept", {"id": next_proposal["id"]})[0] == 201
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    score = SPLIT.score_game(params, log)
    assert score["deal_round"] == 2 and score["proposals"] == 4 and score["acceptances"] == 1
    assert (score["replaced"], score["lapsed"], score["accepted"], score["expired"]) == (1, 2, 1, 0)
    assert any(event["kind"] == "denied" and event["data"]["status"] == 429 for event in world.events())


def test_every_other_seat_must_accept_the_same_split_proposal(start_world, tmp_path):
    seats = ("p1", "p2", "p3")
    world, params = split_world(start_world, tmp_path, seats=seats)
    for seat in seats:
        assert world.seat(seat, "GET", "/state")[0] == 200
    split = {"p1": 46, "p2": 28, "p3": 26}
    status, first = world.seat("p1", "POST", "/propose", {"split": split})
    assert status == 201
    status, second = world.seat("p2", "POST", "/propose", {"split": {"p1": 40, "p2": 30, "p3": 30}})
    assert status == 201
    assert world.seat("p3", "POST", "/accept", {"id": second["id"]})[0] == 201
    assert world.seat("p3", "POST", "/accept", {"id": second["id"]})[0] == 409
    assert world.seat("p3", "POST", "/decline", {"id": second["id"]})[0] == 409
    assert not world.seat("p1", "GET", "/state")[1]["season_over"]
    assert world.seat("p2", "POST", "/accept", {"id": first["id"]})[0] == 201
    assert not world.seat("p1", "GET", "/state")[1]["season_over"]
    assert world.seat("p3", "POST", "/accept", {"id": first["id"]})[0] == 201
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    assert log[-1]["proposal_id"] == first["id"] and log[-1]["catch"] == split
    score = SPLIT.score_game(params, log)
    assert score["acceptances"] == 3 and score["accepted"] == 1 and score["expired"] == 1


def test_a_claim_deal_can_end_the_propose_game(start_world, tmp_path):
    world, params = split_world(start_world, tmp_path)
    everyone_calls(world)
    assert world.seat("p2", "POST", "/claim", {"pass": True})[0] == 201
    assert world.seat("p1", "POST", "/claim", {"amount": 60})[0] == 201
    assert world.seat("p2", "GET", "/state")[1]["round"] == 2
    assert world.seat("p1", "POST", "/claim", {"amount": 20})[0] == 201
    assert world.seat("p2", "POST", "/claim", {"amount": 60})[0] == 201
    assert world.seat("p2", "GET", "/state")[1]["season_over"]
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    score = SPLIT.score_game(params, log)
    assert log[0]["passed"] == ["p2"] and not log[0]["agreed"]
    assert score["via"] == "claim" and score["deal_round"] == 2 and score["totals"] == {"p1": 20, "p2": 60}
    assert score["proposals"] == score["acceptances"] == 0
    assert score["declined"] == score["expired"] == score["accepted"] == 0


def test_a_round_1_proposal_can_be_accepted_in_round_2_while_it_still_fits(start_world, tmp_path, monkeypatch):
    world, params = split_world(start_world, tmp_path)
    clock = 0.0
    monkeypatch.setattr(world.module, "now", lambda: clock)
    everyone_calls(world)
    split = {"p1": 60, "p2": 20}
    status, proposal = world.seat("p1", "POST", "/propose", {"split": split})
    assert status == 201
    clock = 30.0
    assert world.seat("p1", "GET", "/state")[1]["round"] == 2
    shown = world.seat("p2", "GET", "/state")[1]["proposals"]
    assert shown == [{"id": proposal["id"], "proposer": "p1", "split": split, "round": 1}]
    assert "seen" not in shown[0] and "feasible" not in shown[0]
    assert world.seat("p2", "POST", "/accept", {"id": proposal["id"]})[0] == 201
    assert world.seat("p2", "GET", "/state")[1]["season_over"]
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    score = SPLIT.score_game(params, log)
    assert score["via"] == "proposal" and score["deal_round"] == 2 and score["totals"] == split
    assert score["accepted"] == 1 and score["lapsed"] == score["replaced"] == score["expired"] == 0
    assert score["feasible"] == 1 and score["feasible_unseen"] == 0
    assert [record["inactive"] for record in log] == [["p2"], ["p1"]]
    assert score["inactive"] == 0.5
    seen = [event for event in world.events() if event["kind"] == "seen"]
    assert [(event["seat"], event["data"]["id"]) for event in seen] == [("p2", proposal["id"])]


def test_a_feasible_proposal_no_other_seat_reads_counts_as_unseen(start_world, tmp_path, monkeypatch):
    world, params = split_world(start_world, tmp_path)
    clock = 0.0
    monkeypatch.setattr(world.module, "now", lambda: clock)
    everyone_calls(world)
    status, proposal = world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 20}})
    assert status == 201
    clock = 30.0
    assert world.seat("p1", "GET", "/state")[1]["proposals"][0]["id"] == proposal["id"]
    clock = 60.0
    view = world.seat("p1", "GET", "/state")[1]
    assert view["round"] == 3 and view["stock"] == 64 and view["proposals"] == []
    refused = world.seat("p2", "POST", "/accept", {"id": proposal["id"]})
    assert refused == (409, {"error": "that proposal lapsed"})
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    score = SPLIT.score_game(params, log)
    assert score["lapsed"] == 1 and score["feasible"] == 1 and score["feasible_unseen"] == 1
    assert score["accepted"] == score["replaced"] == score["expired"] == 0
    assert not [event for event in world.events() if event["kind"] == "seen"]
    assert "feasible" not in world.seat("p2", "GET", "/state")[1]


def test_a_newer_proposal_replaces_the_older_and_the_old_id_is_refused(start_world, tmp_path):
    world, params = split_world(start_world, tmp_path)
    everyone_calls(world)
    first = world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 20}})[1]
    second = world.seat("p1", "POST", "/propose", {"split": {"p1": 55, "p2": 25}})[1]
    assert second["replaced"] == first["id"]
    shown = world.seat("p2", "GET", "/state")[1]["proposals"]
    assert [item["id"] for item in shown] == [second["id"]]
    assert world.seat("p2", "POST", "/accept", {"id": first["id"]}) == (409, {"error": "that proposal was replaced"})
    assert world.seat("p2", "POST", "/accept", {"id": second["id"]})[0] == 201
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    score = SPLIT.score_game(params, log)
    assert score["replaced"] == 1 and score["accepted"] == 1 and score["feasible"] == 2
    assert score["feasible_unseen"] == 1


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
    assert admin["world_version"] == 19 and len(admin["game"]["log"]) == 3


def test_seeded_proposal_round_closes(start_world, tmp_path, monkeypatch):
    for seed in (17, 91, 247):
        rng = random.Random(seed)
        world, params = split_world(start_world, tmp_path)
        clock = 0.0
        monkeypatch.setattr(world.module, "now", lambda: clock)
        monkeypatch.setattr(world.module, "WAIT_SECONDS", 0)
        everyone_calls(world)
        policies = ["timer", "timer_idle", "decline", "decline_idle", "claim", "claim"]
        rng.shuffle(policies)
        pot = params["capacity"]
        declines = []
        for number, policy in enumerate(policies, 1):
            proposer, other = rng.sample(params["seats"], 2)
            pending = []
            if policy != "claim":
                for author in (proposer, other)[:1 + (policy == "decline")]:
                    amount = rng.randrange(pot + 1)
                    split = {proposer: amount, other: pot - amount}
                    status, post = world.seat(author, "POST", "/propose", {"split": split})
                    assert status == 201
                    pending.append((author, post["id"]))
                    assert world.seat(author, "GET", "/state")[1]["round"] == number
            actions = {proposer: {"pass": True}, other: {"amount": rng.randrange(pot + 1)}}
            order = [] if policy.endswith("_idle") else rng.sample(params["seats"], 2)
            for i, seat in enumerate(order):
                assert world.seat(seat, "POST", "/claim", actions[seat]) == (201, {"round": number, **actions[seat]})
                game = world.request("GET", "/admin/state", token=world.admin)[1]["game"]
                assert len(game["log"]) == (number - 1 if i == 0 or pending else number)
            if pending:
                clock += 29.0
                path = f"/wait?round={number}"
                for _ in range(2):
                    view = world.seat(other, "GET", path)[1]
                    assert view["round"] == number and view["stock"] == pot
                    assert {item["id"] for item in view["proposals"]} == {pid for _, pid in pending}
                if policy.startswith("timer"):
                    clock += 1.0
                    assert len(world.seat(other, "GET", path)[1]["closed_rounds"]) == number
                else:
                    rng.shuffle(pending)
                    for i, (author, pid) in enumerate(pending):
                        responder = other if author == proposer else proposer
                        body = {"id": pid}
                        for action in ("/decline", "/accept"):
                            assert world.seat(author, "POST", action, body)[0] == 409
                        assert world.seat(responder, "POST", "/decline", body) == (201, {**body, "declined": True})
                        declines.append((pid, responder, number, clock))
                        for action in ("/decline", "/accept"):
                            assert world.seat(responder, "POST", action, body)[0] == 409
                        view = world.seat(author, "GET", "/state")[1]
                        assert len(view["closed_rounds"]) == (number if order and i == 1 else number - 1)
                    if not order:
                        clock += 1.0
                        assert len(world.seat(other, "GET", path)[1]["closed_rounds"]) == number
            view = world.seat(proposer, "GET", "/state")[1]
            pot = pot * 4 // 5
            assert len(view["closed_rounds"]) == number and view["stock"] == pot
            assert view["season_over"] == (number == params["rounds"]) and not view["proposals"]
            events = world.events()
            if pending:
                pid = next(pid for author, pid in pending if author == proposer)
                looks = [e for e in events if e["kind"] == "seen" and e["data"]["id"] == pid]
                assert len(looks) == 1 and looks[0]["seat"] == other
                if policy.startswith("timer"):
                    assert looks[0]["t"] == clock - 1.0
            assert [(e["data"]["id"], e["seat"], e["data"]["round"], e["t"])
                    for e in events if e["kind"] == "decline"] == declines
            log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
            inactive = [other] if policy == "timer_idle" else []
            assert log[-1]["inactive"] == inactive
            assert [e for e in events if e["kind"] == "close"][-1]["data"]["inactive"] == inactive
            assert SPLIT.score_game(params, log)["declined"] == len(declines)
            assert {offer["id"]: offer["decline"] for offer in log[-1]["offers"] if offer["fate"] == "declined"} == {
                pid: {"seat": seat, "round": r, "time": t} for pid, seat, r, t in declines
            }


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
