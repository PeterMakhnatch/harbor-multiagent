"""Seeded proofs for the freight booking seam, using the shipped referee and policy runner."""

import json
import os
from collections import Counter, defaultdict

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
FAMILY = load(FLOTILLA / "seams" / "commit.py")
EXT = load(FLOTILLA / "world" / "ext" / "commit.py")
QA = load(FLOTILLA / "qa.py")
SEED = 7920628754219
DOSES = tuple(FAMILY.DOSES)
N = int(os.getenv("COMMIT_SWEEP_INSTANCES", "3"))
EXPECTED_NAIVE = {"solo": 1, "solo-seam": 0, "team": 0, "probe-control": 1,
                  "probe-trap": 0, "hint": 0, "probe-quiet": 1}


def plausible_alternatives(inst):
    lanes = {FAMILY.lane_key(lane): lane for lane in inst["lanes"]}
    primary = defaultdict(list)
    for shipment in inst["shipments"]:
        primary[lanes[FAMILY.lane_key(shipment)]["primary"]].append(shipment["round"])
    first = inst["outages"][0]
    occupied = {o["carrier"] for o in inst["outages"][1:]}
    starts = range(3, 5) if inst["dose"] == "canary" else (first["start"],)
    count = 1 if inst["dose"] == "canary" else 3
    actual = FAMILY.answer_key(inst, inst["outages"])
    for carrier, rounds in primary.items():
        if carrier == first["carrier"] or carrier in occupied:
            continue
        for start in starts:
            if min(rounds) < start and sum(r >= start for r in rounds) >= count:
                alternative = [{"carrier": carrier, "start": start}, *inst["outages"][1:]]
                if FAMILY.answer_key(inst, alternative) != actual:
                    yield alternative


@pytest.mark.parametrize("dose", DOSES)
def test_commit_layouts(dose):
    unique = set()
    for i in range(256):
        inst = FAMILY.generate(SEED, i, 2, dose)
        assert inst == FAMILY.generate(SEED, i, 2, dose)
        unique.add(json.dumps(inst, sort_keys=True))
        assert Counter(s["round"] for s in inst["shipments"]) == dict.fromkeys(range(1, inst["rounds"] + 1), inst["batch"])
        assert len({s["id"] for s in inst["shipments"]}) == len(inst["shipments"])
        assert list(plausible_alternatives(inst)), "the booking table itself must not identify the one outage plan"
        arms = {arm: FAMILY.extension_arm(inst, arm, 2000, 30) for arm in FAMILY.ARMS}
        answer = arms["solo"]["params"]["answer"]
        for arm, setting in arms.items():
            params = setting["params"]
            if arm == "probe-quiet":
                assert params["answer"] == params["primary"] != answer
            else:
                assert params["answer"] == answer
                assert params["outages"] == inst["outages"]
            if arm != "solo":
                for o in inst["outages"]:
                    assert o["text"] not in setting["instructions"]["p1"]
                    assert o["text"] not in json.dumps(setting["briefings"]["p1"])
        for arm in ("probe-control", "probe-trap", "probe-quiet", "team"):
            assert arms[arm]["instructions"]["p1"] == arms["team"]["instructions"]["p1"]
            assert arms[arm]["briefings"]["p1"] == arms["team"]["briefings"]["p1"]
        assert arms["hint"]["instructions"]["p1"].replace(FAMILY.HINT + "\n", "") == arms["team"]["instructions"]["p1"]
    assert len(unique) == 256


def evidence(task, result):
    params = task["params"]
    events = result["events"]
    round_now = 0
    booked = set()
    live = set(task["seats"]) - set(task.get("scripted_seats", []))
    stepped = set()
    notices = {}
    deliveries = set()
    routes = {}
    for event in events:
        kind, seat, data = event["kind"], event["seat"], event["data"]
        if kind == "commit_round_open":
            assert data["round"] == round_now + 1
            round_now = data["round"]
        elif kind == "commit_book":
            assert seat == "p1"
            assert data["shipment"] not in booked
            assert data["round"] == round_now
            assert any(s["id"] == data["shipment"] and s["round"] == round_now for s in params["shipments"])
            booked.add(data["shipment"])
        elif kind == "commit_desk_done":
            live.remove(seat)
        elif kind == "commit_step":
            assert data["round"] == round_now and seat not in stepped
            stepped.add(seat)
        elif kind == "tick":
            assert live <= stepped
            stepped.clear()
        elif kind == "commit_notice":
            start = next(o["start"] for o in params["outages"] if o["carrier"] == data["carrier"])
            expected_arrival = {"probe-control": 0, "probe-trap": start - 1, "hint": start - 1,
                                "solo-seam": start - 1}.get(task["condition"], data["round"])
            notices[data["id"]] = expected_arrival
            assert 1 <= data["round"] <= params["rounds"]
        elif kind == "send" and seat in ("p2", "carrier-board") and data.get("to") in ("p1", "all"):
            for outage in params["outages"]:
                if outage["text"] in data.get("text", ""):
                    arrival = 0 if task["condition"] == "probe-control" else outage["start"] - 1
                    notices[data["id"]] = arrival
        elif kind == "message_route":
            if data["to"] == "p1":
                routes[data["id"]] = data["arrival"]
        elif kind == "deliver" and seat == "p1":
            for id_ in data["ids"]:
                assert id_ not in deliveries
                deliveries.add(id_)
                assert id_ in routes and event["t"] >= routes[id_]
                if id_ in notices:
                    assert event["t"] == notices[id_]
        elif kind == "extension_call":
            assert 200 <= data["status"] < 500
    score = EXT.score(task, events)
    assert result["invalid"] is None and score == result["score"]
    assert score["reward"] in (0, 1)
    assert score["reward"] == result["reward"]
    assert 0 <= score["fields_correct"] <= len(params["answer"])
    assert score["missed"] == len(params["answer"]) - len(booked)
    assert score["affected_correct"] + score["unaffected_correct"] == score["fields_correct"]
    assert not any(result["files_read"].values())
    return score


@pytest.mark.parametrize("dose", DOSES)
@pytest.mark.parametrize("arm", FAMILY.ARMS)
def test_commit_scheduler_sweep(tmp_path, dose, arm):
    random_scores = []
    requests = Counter()
    for i in range(N):
        (path,) = SIM.BUILD.build("commit", [i], 2, dose, SEED, (arm,), tmp_path)
        task = json.loads((path / "environment" / "task.json").read_text())
        for schedule in range(5):
            for policy, expected in (("careful", 1), ("naive", EXPECTED_NAIVE[arm]), ("nop", 0)):
                result = SIM.simulate_built(path, policy, schedule)
                score = evidence(task, result)
                assert score["reward"] == expected, (dose, arm, i, schedule, policy, score)
                if policy == "careful":
                    assert score["stale_after_delivery"] == 0
                    assert score["closed"]
                    if arm in ("team", "probe-control", "probe-trap", "hint", "solo-seam"):
                        assert score["delivered_outages"] == len(task["params"]["outages"])
                    assert score["missing_relay"] == 0
                    for seat, count in result["requests"].items():
                        requests[seat] = max(requests[seat], count)
                elif policy == "naive" and arm in ("probe-trap", "hint", "solo-seam"):
                    assert score["stale_after_delivery"] == score["affected_total"] > 0
        random_result = SIM.simulate_built(path, "random", 0)
        random_scores.append(evidence(task, random_result)["reward"])
        if i == 0:
            same_a = SIM.simulate_built(path, "careful", 9)
            same_b = SIM.simulate_built(path, "careful", 9)
            assert same_a["events"] == same_b["events"]
            for policy in ("lone", "primary", "backup"):
                expected = int(policy in ("lone", "primary") and arm == "probe-quiet")
                assert SIM.simulate_built(path, policy, 0)["reward"] == expected
            if arm == "team":
                assert SIM.simulate_built(path, {"p1": "nop"}, 0)["reward"] == 0
                stalled = SIM.simulate_built(path, {"p2": "nop"}, 0)
                assert stalled["reward"] is None and stalled["invalid"]
                assert any(event["kind"] == "play_timeout" for event in stalled["events"])
                assert SIM.simulate_built(path, {"p1": "lone"}, 0)["reward"] == 0
                assert SIM.simulate_built(path, {"p2": "naive"}, 0)["reward"] == 0
        if arm == "probe-trap":
            skims = [SIM.simulate_built(path, f"skim_{j}", 0)["reward"] for j in (2, 4, 8)]
            assert skims == sorted(skims, reverse=True)
    chance = 2 ** -len(task["params"]["shipments"])
    if N >= 200:
        # This is the independent per-shipment coin baseline, not an optimal informed guess.
        assert abs(sum(random_scores) / N - chance) < 0.1
    print(f"{dose} {arm}: {N * 5} schedules per policy; random={sum(random_scores)}/{N}; max calls={dict(requests)}")


def test_commit_built_pairs(tmp_path):
    paths = SIM.BUILD.build("commit", [0, 1], 2, "canary", SEED, FAMILY.ARMS, tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == 14
    assert not [f for f in findings if f.level == "FAIL"]


def test_commit_atomicity_and_receipts_over_http(tmp_path, start_world):
    (path,) = SIM.BUILD.build("commit", [0], 2, "standard", SEED, ("team",), tmp_path)
    task = json.loads((path / "environment" / "task.json").read_text())
    params = task["params"]
    world = start_world("team", ("p1", "p2"), FLOTILLA_TASK_JSON=str(path / "environment" / "task.json"))
    world.ordered = True
    _, view = world.seat("p1", "GET", "/view")
    assert view["round"] == 1 and not view["messages"]
    assert world.seat("p2", "GET", f"/shipment?id={params['shipments'][0]['id']}")[0] == 403
    entries = [{"shipment": s["id"], "carrier": params["answer"][s["id"]]}
               for s in reversed(params["shipments"]) if s["round"] == 1]
    for bad in ({"round": 2, "bookings": entries},
                {"round": 1, "bookings": [*entries, entries[0]]},
                {"round": 1, "bookings": [entries[0], {"shipment": [], "carrier": {}}]},
                {"round": 1, "bookings": [entries[0], {"shipment": params["shipments"][-1]["id"],
                                                      "carrier": entries[0]["carrier"]}]}):
        assert world.seat("p1", "POST", "/step", bad)[0] in (400, 409)
        assert world.seat("p1", "GET", "/view")[1]["bookings"] == {}
    assert world.seat("p1", "POST", "/step", {"round": 1, "bookings": entries})[1]["stepped"]
    assert world.seat("p1", "POST", "/step", {"round": 1, "bookings": entries})[0] == 409
    for outage in params["outages"]:
        assert world.seat("p2", "POST", "/messages", {"to": "p1", "text": outage["text"]})[0] == 201
    assert not world.seat("p1", "GET", "/view")[1]["messages"]
    assert world.seat("p2", "POST", "/step", {"round": 1, "done": True})[0] == 200
    _, reply = world.seat("p1", "GET", "/await?until=tick&after=0")
    assert any(m["text"] == params["outages"][0]["text"] for m in reply["new_messages"])
    for r in range(2, params["rounds"] + 1):
        entries = [{"shipment": s["id"], "carrier": params["answer"][s["id"]]}
                   for s in reversed(params["shipments"]) if s["round"] == r]
        assert world.seat("p1", "POST", "/step", {"round": r, "bookings": entries})[0] == 200
    assert world.seat("p1", "POST", "/step", {"round": params["rounds"], "bookings": entries})[0] == 409
    assert EXT.score(task, world.events())["reward"] == 1
    assert EXT.score(task, world.events())["stale_after_delivery"] == 0
