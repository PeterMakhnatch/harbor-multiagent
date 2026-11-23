"""World version 11: the read gate of the `gated` condition, `unread` on action events, a decline with a counter."""

from __future__ import annotations

import itertools
import json
from fractions import Fraction

from conftest import FLOTILLA, load

SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_game_split_v11")
AMOUNT_ERROR = 'send {"amount": <a whole number from 0 to 1000000>}'
_FILES = itertools.count()


def split_world(start_world, tmp_path, monkeypatch, condition, seats=("p1", "p2"), game=None):
    """A split world on a pot of 100, its clock fixed at 0."""
    params = SPLIT._params(100, Fraction(4, 5), dict.fromkeys(seats, 20))
    path = tmp_path / f"split-{next(_FILES)}.json"
    path.write_text(json.dumps(params))
    world = start_world(condition, seats, FLOTILLA_GAME=str(game or FLOTILLA / "games" / "split.py"),
                        FLOTILLA_GAME_PARAMS=str(path), FLOTILLA_ROUND_SECONDS="30")
    monkeypatch.setattr(world.module, "now", lambda: 0.0)
    for seat in seats:
        assert world.seat(seat, "GET", "/state")[0] == 200
    return world


def proposals_with_talk(tmp_path):
    """The split game with its proposal protocol in every condition, so that the gate meets /propose."""
    source = (FLOTILLA / "games" / "split.py").read_text()
    path = tmp_path / "split_talk_propose.py"
    path.write_text(source + "\n\ndef round_protocol(condition, seats):\n    return ProposalRound(seats)\n")
    return path


def data(world, kind):
    return [event["data"] for event in world.events() if event["kind"] == kind]


def send(world, seat, text, to="all"):
    status, message = world.seat(seat, "POST", "/messages", {"to": to, "text": text})
    assert status == 201
    return message


def test_the_gate_holds_a_claim_until_the_seat_has_read_and_shows_the_messages(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "gated")
    assert world.events()[0]["data"]["read_gate"] is True and world.events()[0]["data"]["world_version"] == 11
    to_all = send(world, "p2", "I claim 50")
    status, reply = world.seat("p1", "POST", "/claim", {"amount": 40})
    assert status == 409 and reply == {"error": world.module.GATE_ERROR, "messages": [to_all]}
    assert data(world, "gated") == [{"round": 1, "path": "/claim", "ids": [to_all["id"]], "action": {"amount": 40},
                                     "unread": 1}]
    assert data(world, "deliver") == [{"ids": [to_all["id"]], "via": "gate"}]
    assert world.kinds()[-2:] == ["gated", "deliver"]
    assert "request" not in world.kinds() and not world.seat("p1", "GET", "/state")[1]["you_requested"]
    assert world.seat("p1", "POST", "/claim", {"amount": 40}) == (201, {"round": 1, "amount": 40})
    assert data(world, "request") == [{"round": 1, "amount": 40, "unread": 0}]

    direct = send(world, "p1", "fine", to="p2")
    assert world.seat("p2", "GET", "/messages") == (200, {"messages": [to_all, direct]})
    assert data(world, "deliver")[-1] == {"ids": [direct["id"]]}  # a read: the v10 event, no `via`
    assert world.seat("p2", "POST", "/claim", {"amount": 50}) == (201, {"round": 1, "amount": 50})
    assert data(world, "request")[-1] == {"round": 1, "amount": 50, "unread": 0}  # its own message never counts
    assert world.seat("p1", "GET", "/state")[1]["season_over"]
    assert len(data(world, "gated")) == 1


def test_a_message_to_another_seat_does_not_hold_and_a_pass_is_held(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "gated", seats=("p1", "p2", "p3"))
    direct = send(world, "p2", "p3 only", to="p3")
    assert world.seat("p1", "POST", "/claim", {"pass": True}) == (201, {"round": 1, "pass": True})
    assert data(world, "pass") == [{"round": 1, "pass": True, "unread": 0}]
    status, reply = world.seat("p3", "POST", "/claim", {"pass": True})
    assert status == 409 and reply["messages"] == [direct]
    assert data(world, "gated")[-1]["action"] == {"pass": True}
    assert world.seat("p3", "POST", "/claim", {"pass": True})[0] == 201


def test_rejected_actions_are_refused_before_the_gate(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "gated")
    assert world.seat("p1", "POST", "/claim", {"amount": 40})[0] == 201
    send(world, "p2", "first")
    for bad in (-1, 10**6 + 1, 1.5, True):
        assert world.seat("p1", "POST", "/claim", {"amount": bad}) == (400, {"error": AMOUNT_ERROR}), bad
    assert world.seat("p1", "POST", "/claim", {"amount": 40}) == (409, {"error": "you already requested in this round"})
    assert world.seat("p1", "POST", "/claim", {"pass": True, "amount": 1})[0] == 400
    assert "gated" not in world.kinds() and "deliver" not in world.kinds()
    assert world.seat("p2", "POST", "/claim", {"pass": True})[0] == 201  # p2 has no unread message
    assert world.seat("p1", "POST", "/claim", {"amount": 10**6})[0] == 409  # round 2: held, a whole number is valid
    assert data(world, "gated")[-1]["action"] == {"amount": 10**6}


def test_silent_talk_and_propose_never_hold_and_count_unread(start_world, tmp_path, monkeypatch):
    talk = split_world(start_world, tmp_path, monkeypatch, "talk")
    assert talk.events()[0]["data"]["read_gate"] is False and not talk.module.READ_GATE
    send(talk, "p2", "one")
    send(talk, "p2", "two", to="p1")
    send(talk, "p1", "mine")
    assert talk.seat("p1", "POST", "/claim", {"amount": 30}) == (201, {"round": 1, "amount": 30})
    assert data(talk, "request") == [{"round": 1, "amount": 30, "unread": 2}]
    talk.seat("p2", "GET", "/messages")
    assert talk.seat("p2", "POST", "/claim", {"pass": True})[0] == 201
    assert data(talk, "pass") == [{"round": 1, "pass": True, "unread": 0}]
    assert "gated" not in talk.kinds()

    silent = split_world(start_world, tmp_path, monkeypatch, "silent")
    assert silent.seat("p2", "POST", "/messages", {"to": "all", "text": "x"})[0] == 403
    assert silent.seat("p1", "POST", "/claim", {"amount": 30})[0] == 201
    assert data(silent, "request") == [{"round": 1, "amount": 30, "unread": 0}]

    propose = split_world(start_world, tmp_path, monkeypatch, "propose")
    status, offer = propose.seat("p1", "POST", "/propose", {"split": {"p1": 50, "p2": 50}})
    assert status == 201 and data(propose, "propose") == [{"round": 1, **offer, "unread": 0}]
    assert propose.seat("p2", "POST", "/accept", {"id": offer["id"]})[0] == 201
    assert data(propose, "accept") == [{"round": 1, "id": offer["id"], "accepted": True, "unread": 0}]


def test_the_gate_holds_round_protocol_actions_after_their_own_checks(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "gated", game=proposals_with_talk(tmp_path))
    message = send(world, "p2", "propose 50/50")
    assert world.seat("p1", "POST", "/propose", {"split": {"p1": 90, "p2": 90}})[0] == 400  # refused before the gate
    assert world.seat("p1", "POST", "/accept", {"id": "1-9"})[0] == 409
    assert "gated" not in world.kinds()
    split = {"split": {"p1": 50, "p2": 50}}
    assert world.seat("p1", "POST", "/propose", split) == (
        409, {"error": world.module.GATE_ERROR, "messages": [message]})
    assert data(world, "gated") == [{"round": 1, "path": "/propose", "ids": [message["id"]], "action": split,
                                     "unread": 1}]
    status, offer = world.seat("p1", "POST", "/propose", split)
    assert status == 201 and offer["id"] == "1-1"  # the held request posted nothing
    send(world, "p1", "accept it")
    assert world.seat("p2", "POST", "/accept", {"id": "1-1"})[0] == 409
    assert data(world, "gated")[-1]["path"] == "/accept"
    assert world.seat("p2", "POST", "/accept", {"id": "1-1"}) == (201, {"id": "1-1", "accepted": True})
    assert world.seat("p1", "GET", "/state")[1]["season_over"]


def test_a_decline_with_a_counter_keeps_a_full_round_open_for_the_counter(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "propose")
    assert world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 40}})[0] == 201
    assert world.seat("p1", "POST", "/claim", {"pass": True})[0] == 201
    assert world.seat("p2", "POST", "/claim", {"amount": 50})[0] == 201
    assert world.seat("p1", "GET", "/state")[1]["round"] == 1  # every seat acted; the proposal holds the round
    counter = {"split": {"p1": 45, "p2": 55}}
    status, reply = world.seat("p2", "POST", "/decline", {"id": "1-1", "counter": counter})
    made = {"id": "1-2", "proposer": "p2", "split": counter["split"], "time": 0.0}
    assert status == 201 and reply == {"id": "1-1", "declined": True, "counter": made}
    view = world.seat("p1", "GET", "/state")[1]
    assert view["round"] == 1 and not view["closed_rounds"]
    assert view["proposals"] == [{"id": "1-2", "proposer": "p2", "split": counter["split"], "round": 1}]
    events = [event for event in world.events() if event["kind"] in ("decline", "propose")][1:]
    assert [(event["kind"], event["seat"], event["t"]) for event in events] == [("decline", "p2", 0.0),
                                                                                ("propose", "p2", 0.0)]
    assert events[0]["data"] == {"round": 1, "id": "1-1", "declined": True, "counter": "1-2", "unread": 0}
    assert events[1]["data"] == {"round": 1, **made, "counter_to": "1-1", "unread": 0}
    assert world.seat("p1", "POST", "/accept", {"id": "1-2"}) == (201, {"id": "1-2", "accepted": True})
    log = world.request("GET", "/admin/state", token=world.admin)[1]["game"]["log"]
    deal, = log
    assert deal["via"] == "proposal" and deal["proposal_id"] == "1-2" and deal["catch"] == counter["split"]
    fates = {offer["id"]: (offer["fate"], offer.get("decline")) for offer in deal["offers"]}
    assert fates == {"1-1": ("declined", {"seat": "p2", "round": 1, "time": 0.0, "counter": "1-2"}),
                     "1-2": ("accepted", None)}
    assert SPLIT.score_game(SPLIT._params(100, Fraction(4, 5), {"p1": 20, "p2": 20}), log)["declined"] == 1


def test_a_decline_without_a_counter_still_closes_a_full_round(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "propose")
    assert world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 40}})[0] == 201
    world.seat("p1", "POST", "/claim", {"pass": True})
    world.seat("p2", "POST", "/claim", {"amount": 50})
    assert world.seat("p2", "POST", "/decline", {"id": "1-1"}) == (201, {"id": "1-1", "declined": True})
    assert world.seat("p1", "GET", "/state")[1]["round"] == 2
    assert data(world, "decline") == [{"round": 1, "id": "1-1", "declined": True, "unread": 0}]


def test_an_invalid_counter_refuses_the_whole_decline(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "propose")
    assert world.seat("p1", "POST", "/propose", {"split": {"p1": 60, "p2": 40}})[0] == 201
    world.seat("p1", "POST", "/claim", {"pass": True})
    world.seat("p2", "POST", "/claim", {"amount": 50})
    for counter in ({"split": {"p1": 60, "p2": 41}}, {"split": {"p1": 1}}, {"split": {"p1": -1, "p2": 1}},
                    {"split": {"p1": 1, "p2": 1}, "x": 1}, [1], "50/50", None):
        status, reply = world.seat("p2", "POST", "/decline", {"id": "1-1", "counter": counter})
        assert status == 400 and reply["error"].startswith("the counter is not valid, so the decline was not recorded")
    assert world.seat("p2", "POST", "/accept", {"id": "1-1", "counter": {"split": {"p1": 1, "p2": 1}}})[0] == 400
    for split in ({"p1": 10, "p2": 10}, {"p1": 11, "p2": 11}):
        assert world.seat("p2", "POST", "/propose", {"split": split})[0] == 201
    status, reply = world.seat("p2", "POST", "/decline", {"id": "1-1", "counter": {"split": {"p1": 1, "p2": 1}}})
    assert status == 429 and "the decline was not recorded" in reply["error"]
    assert "decline" not in world.kinds() and "declined" not in world.kinds()
    view = world.seat("p1", "GET", "/state")[1]
    assert view["round"] == 1 and "1-1" in {offer["id"] for offer in view["proposals"]}
    assert world.seat("p2", "POST", "/decline", {"id": "1-1"})[0] == 201


def test_a_gate_off_talk_round_logs_what_world_10_logged_plus_unread(start_world, tmp_path, monkeypatch):
    world = split_world(start_world, tmp_path, monkeypatch, "talk")
    message = send(world, "p1", "half each")
    assert world.seat("p2", "POST", "/claim", {"amount": 50}) == (201, {"round": 1, "amount": 50})
    assert world.seat("p2", "GET", "/messages") == (200, {"messages": [message]})
    assert world.seat("p1", "POST", "/claim", {"amount": 50}) == (201, {"round": 1, "amount": 50})
    assert world.kinds() == ["start", "look", "open", "look", "send", "request", "deliver", "request", "close",
                             "season-over"]
    events = world.events()
    assert [event["data"] for event in events[5:8]] == [{"round": 1, "amount": 50, "unread": 1},
                                                         {"ids": [message["id"]]},
                                                         {"round": 1, "amount": 50, "unread": 0}]
    assert set(events[0]["data"]) == {"condition", "seats", "world_version", "limits", "game", "round_seconds",
                                      "max_messages_per_round", "read_gate"}
