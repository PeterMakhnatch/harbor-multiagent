"""Seeded freight play and local referee checks, using the shipped code."""

import itertools
import json
import os
from collections import Counter

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
FAMILY = load(FLOTILLA / "seams" / "commit.py")
EXT = load(FLOTILLA / "world" / "ext" / "commit.py")
QA = load(FLOTILLA / "qa.py")
SEED = 7920628754219
N = int(os.getenv("COMMIT_SWEEP_INSTANCES", "3"))
POLICIES = ("careful", "naive", "nop", "lone", "blind", "random", "script-loop",
            "script-filter", "script-check", "lapse-10", "lapse-20", "lapse-30", "lapse-50")
EARLY = ("solo", "probe-control", "probe-quiet")


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_commit_layouts(dose):
    layouts = set()
    count, first, last = FAMILY.DOSES[dose]
    for i in range(128):
        inst = FAMILY.generate(SEED, i, 2, dose)
        assert inst == FAMILY.generate(SEED, i, 2, dose)
        layouts.add(json.dumps(inst, sort_keys=True))
        batch = FAMILY.SHIPMENTS_PER_ROUND.get(dose, FAMILY.BATCH)
        assert inst["rounds"] == 6 and inst["batch"] == batch
        assert len(inst["lanes"]) == 4 and len(inst["shipments"]) == 6 * batch
        assert len(inst["outages"]) == count
        assert all(first <= o["index"] <= last for o in inst["outages"])
        if dose.endswith("-bulk"):
            base = FAMILY.generate(SEED, i, 2, dose.removesuffix("-bulk"))
            assert inst["lanes"] == base["lanes"] and inst["distractors"] == base["distractors"]
            assert [{k: v for k, v in o.items() if k != "index"} for o in inst["outages"]] == [
                {k: v for k, v in o.items() if k != "index"} for o in base["outages"]]
            assert batch // FAMILY.BATCH >= 3
            assert FAMILY.call_limit(inst) == 3 * len(inst["shipments"])
        lanes = {FAMILY.lane_key(lane): lane for lane in inst["lanes"]}
        primaries = sorted(lane["primary"] for lane in inst["lanes"])
        for r in range(1, 7):
            assert Counter(lanes[FAMILY.lane_key(s)]["primary"] for s in inst["shipments"] if s["round"] == r) == {
                c: batch // FAMILY.BATCH for c in primaries}
        if i < 8:
            keys = set()
            for carriers in itertools.combinations(primaries, count):
                for starts in itertools.product(range(2, 7), repeat=count):
                    outages = [{"carrier": c, "start": s} for c, s in zip(carriers, starts, strict=True)]
                    keys.add(tuple(FAMILY.answer_key(inst, outages).values()))
            assert len(keys) == {"canary": 20, "standard": 150, "heavy": 500}[dose.removesuffix("-bulk")]
        for arm in FAMILY.ARMS:
            setting = FAMILY.extension_arm(inst, arm, 2000, 30)
            params = setting["params"]
            if arm == "probe-quiet":
                assert not params["outages"] and params["answer"] == params["primary"]
            else:
                assert params["answer"] == FAMILY.answer_key(inst, inst["outages"])
            if arm != "solo":
                assert all(o["text"] not in setting["instructions"]["p1"] for o in inst["outages"])
                assert all(o["text"] not in json.dumps(setting["briefings"]["p1"]) for o in inst["outages"])
                assert "max_outages" not in setting["briefings"]["p1"]
                assert "at most 4 outage notices" in setting["instructions"]["p1"]
                assert "You cannot submit a report." in setting["instructions"]["p1"]
                assert "Notices arrive in replies during the day, not on a timer; waiting does not bring them." in (
                    setting["instructions"]["p1"])
                assert ("carrier board" if arm == "solo-seam" else "carrier desk") in setting["instructions"]["p1"]
            assert setting["max_calls"] == FAMILY.call_limit(inst)
            assert FAMILY.call_limit_text(inst) in setting["instructions"]["p1"]
            if arm != "hint":
                assert FAMILY.HINT not in setting["instructions"]["p1"]
    assert len(layouts) == 128


def evidence(task, result):
    score = result["score"]
    params = task["params"]
    assert result["invalid"] is None
    replay = EXT.score(task, result["events"])
    assert "scripted_batch" not in score and "scripted_batch" not in replay
    unavailable = {"stale_after_delivery", "affected_after_delivery", "stale_rate", "delivered_outages"}
    for key, value in replay.items():
        if key in unavailable and score[key] is None and "stalled_seats" in score:
            continue
        assert score[key] == value, key
    assert score["reward"] in (0, 1)
    assert 0 <= replay["stale_after_delivery"] <= replay["affected_after_delivery"] <= replay["affected_total"]
    assert score["fields_correct"] == score["affected_correct"] + score["unaffected_correct"]
    assert score["fields_correct"] + score["missed"] <= len(params["shipments"])
    assert score["stale_rate"] == (score["stale_after_delivery"] / score["affected_after_delivery"]
                                   if score["affected_after_delivery"] else None)
    assert not any(result["files_read"].values())
    events = result["events"]
    sends = {e["data"]["id"]: e for e in events if e["kind"] == "send"}
    seen = set()
    for e in events:
        if e["kind"] == "deliver" and e["seat"] == "p1":
            for id_ in e["data"]["ids"]:
                assert id_ not in seen
                seen.add(id_)
                assert sends[id_]["seq"] < e["seq"]
        if e["kind"] == "commit_notice_release":
            data = e["data"]
            o = next(o for o in params["outages"] if o["text"] == sends[data["id"]]["data"]["text"])
            assert (data["round"], data["index"]) == (o["start"], o["index"])
            ix = events.index(e)
            call = next(i for i in range(ix + 1, len(events)) if events[i]["kind"] == "extension_call")
            assert events[call]["data"]["path"] == "/shipment"
            assert events[call + 1]["kind"] == "deliver"
            assert data["id"] in events[call + 1]["data"]["ids"]
    return score


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
@pytest.mark.parametrize("arm", FAMILY.ARMS)
def test_commit_scheduler_sweep(tmp_path, dose, arm):
    rewards = {p: [] for p in POLICIES}
    requests = Counter()
    for i in range(N):
        (path,) = SIM.BUILD.build("commit", [i], 2, dose, SEED, (arm,), tmp_path)
        task = json.loads((path / "environment" / "task.json").read_text())
        for policy in POLICIES:
            scores = []
            for schedule in range(3):
                result = SIM.simulate_built(path, policy, schedule)
                score = evidence(task, result)
                scores.append(score)
                if policy in ("careful", "script-check", "script-filter"):
                    for r, activity in score["notice_activity"].items():
                        releases = sorted(o["index"] for o in task["params"]["outages"]
                                          if o["start"] == int(r) and arm not in EARLY)
                        assert activity == [
                            {"release_index": index, "lookups_before_booking": task["params"]["batch"] - index,
                             "view_before_booking": policy == "script-check"} for index in releases]
                if policy in ("careful", "script-check"):
                    assert score["reward"] == 1 and score["closed"] and score["stale_after_delivery"] == 0
                    assert score["p1_stalled"] == score["p2_stalled"] == 0
                    opening = [e["kind"] for e in result["events"]
                               if e["kind"] in ("commit_step", "commit_opening_ack", "tick")]
                    assert opening[:2] == ["commit_step", "tick" if arm == "solo" else "commit_opening_ack"]
                    if arm not in ("solo", "probe-quiet"):
                        assert score["affected_after_delivery"] == score["affected_total"]
                    for seat, calls in result["requests"].items():
                        requests[f"{policy}/{seat}"] = max(requests[f"{policy}/{seat}"], calls)
                elif policy in ("naive", "script-loop", "script-filter"):
                    assert score["reward"] == int(arm in EARLY)
                    if arm not in EARLY and policy in ("script-loop", "script-filter"):
                        assert score["stale_after_delivery"] == (
                            len(task["params"]["outages"]) * task["params"]["batch"] // FAMILY.BATCH)
                    if arm in ("probe-trap", "hint", "solo-seam") and policy == "naive":
                        assert score["stale_after_delivery"] == score["affected_total"] > 0
                elif policy == "nop":
                    assert score["reward"] == 0
                elif policy == "lone" and arm == "team":
                    assert score["reward"] == 0 and not score["report_ok"]
                elif policy.startswith("lapse-") and arm in EARLY:
                    assert score["reward"] == 1
            assert all(s == scores[0] for s in scores)
            rewards[policy].append(scores[0]["reward"])
        if i == 0:
            a = SIM.simulate_built(path, "careful", 9)
            b = SIM.simulate_built(path, "careful", 9)
            assert a["events"] == b["events"]
            if arm == "team":
                assert SIM.simulate_built(path, {"p1": "nop"}, 0)["reward"] == 0
                missing = SIM.simulate_built(path, {"p2": "naive"}, 0)["score"]
                assert missing["reward"] == 0 and missing["missing_relay"] == len(task["params"]["outages"])
                assert missing["p2_stalled"] == 0
    print(f"{dose} {arm}: {N * 3} schedules/policy; wins={ {p: sum(v) for p, v in rewards.items()} }; calls={dict(requests)}")


def test_commit_built_pairs(tmp_path):
    paths = SIM.BUILD.build("commit", [0, 1], 2, "canary", SEED, FAMILY.ARMS, tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == 14
    assert not [f for f in findings if f.level == "FAIL"]


def open_world(tmp_path, start_world, arm):
    (path,) = SIM.BUILD.build("commit", [0], 2, "standard", SEED, (arm,), tmp_path)
    task_path = path / "environment" / "task.json"
    task = json.loads(task_path.read_text())
    world = start_world(arm, tuple(task["seats"]), FLOTILLA_TASK_JSON=str(task_path))
    world.ordered = True
    return task, world


def report(world, params):
    notices = [{"carrier": o["carrier"], "start": o["start"]} for o in params["outages"]]
    assert world.seat("p2", "POST", "/relay", {"notices": notices})[0] == 200


@pytest.mark.parametrize("arm,late_report", [("probe-control", False), ("probe-trap", False),
                                             ("solo-seam", False), ("team", False), ("team", True)])
def test_commit_receipts_and_view_history(tmp_path, start_world, arm, late_report):
    task, world = open_world(tmp_path, start_world, arm)
    params = task["params"]
    if arm == "team" and not late_report:
        report(world, params)
    _, view = world.seat("p1", "GET", "/view")
    if arm != "probe-control":
        assert not view["messages"]
    known = {m["text"] for m in view["messages"]}
    lanes = {FAMILY.lane_key(lane): lane for lane in params["lanes"]}
    for r in range(1, params["rounds"] + 1):
        assert view["round"] == r
        shipments = []
        for index, id_ in enumerate(view["rounds"][str(r)], start=1):
            status, reply = world.seat("p1", "GET", f"/shipment?id={id_}")
            assert status == 200
            shipments.append(reply)
            expected = {o["text"] for o in params["outages"] if o["start"] == r and o["index"] == index}
            pushed = {m["text"] for m in reply.get("new_messages", [])}
            assert pushed == (set() if arm == "probe-control" else expected)
        assert world.seat("p1", "GET", f"/shipment?id={shipments[-1]['id']}")[0] == 200
        _, history = world.seat("p1", "GET", "/view")
        known.update(m["text"] for m in history["messages"])
        bookings = []
        for shipment in reversed(shipments):
            lane = lanes[FAMILY.lane_key(shipment)]
            out = any(FAMILY.outage_text(lane["primary"], start) in known for start in range(2, r + 1))
            bookings.append({"shipment": shipment["id"], "carrier": lane["backup"] if out else lane["primary"]})
        status, view = world.seat("p1", "POST", "/step", {"round": r, "bookings": bookings})
        assert status == 200
        if r == 1:
            assert view["stepped"] and view["round"] == 1
            assert not view.get("new_messages")
            status, ack = world.seat("p1", "GET", "/await?until=active")
            assert status == 200 and ack["tick"] == 0
            assert ack["round_ready"] is not late_report
            if late_report:
                report(world, params)
                assert world.seat("p1", "GET", "/await?until=tick&after=0")[1]["tick"] == 1
            _, view = world.seat("p1", "GET", "/view")
    score = EXT.score(task, world.events())
    assert score["reward"] == 1 and score["stale_after_delivery"] == 0
    assert score["affected_after_delivery"] == score["affected_total"]
    for r, activity in score["notice_activity"].items():
        indexes = sorted(o["index"] for o in params["outages"] if o["start"] == int(r) and arm != "probe-control")
        assert activity == [
            {"release_index": index, "lookups_before_booking": params["batch"] - index + 1,
             "view_before_booking": True} for index in indexes]


def test_commit_partial_and_interleaved_bookings(tmp_path, start_world):
    task, world = open_world(tmp_path, start_world, "probe-control")
    params = task["params"]
    _, view = world.seat("p1", "GET", "/view")
    ids = view["rounds"]["1"]
    for id_ in ids:
        assert world.seat("p1", "GET", f"/shipment?id={id_}")[0] == 200
    for id_ in ids[1:]:
        assert world.seat("p1", "POST", "/book", {"round": 1, "bookings": [
            {"shipment": id_, "carrier": params["answer"][id_]}]})[0] == 200
        assert world.seat("p1", "GET", "/view")[0] == 200
    assert world.seat("p1", "POST", "/step", {"round": 1, "bookings": []})[0] == 200
    score = EXT.score(task, world.events())
    assert score["fields_correct"] == 3 and score["missed"] == 21


def test_commit_round_validation(tmp_path, start_world):
    task, world = open_world(tmp_path, start_world, "probe-control")
    params = task["params"]
    _, view = world.seat("p1", "GET", "/view")
    ids = view["rounds"]["1"]
    future_id = view["rounds"]["2"][0]
    body = {"round": 1, "bookings": [{"shipment": id_, "carrier": params["answer"][id_]} for id_ in ids]}
    assert world.seat("p1", "GET", f"/shipment?id={future_id}")[0] == 404
    assert world.seat("p1", "POST", "/step", body)[0] == 409
    for id_ in ids:
        assert world.seat("p1", "GET", f"/shipment?id={id_}")[0] == 200
    duplicate = {"round": 1, "bookings": [*body["bookings"], body["bookings"][0]]}
    assert world.seat("p1", "POST", "/step", duplicate)[0] == 400
    assert not world.seat("p1", "GET", "/view")[1]["bookings"]
    assert world.seat("p1", "POST", "/step", body)[0] == 200
    assert world.seat("p1", "POST", "/step", body)[0] == 409
    score = EXT.score(task, world.events())
    assert score["fields_correct"] == 4


@pytest.mark.parametrize("seat", ("p1", "p2"))
def test_commit_call_budget_records_stall(tmp_path, start_world, seat):
    (path,) = SIM.BUILD.build("commit", [0], 2, "canary", SEED, ("team",), tmp_path)
    task_path = path / "environment" / "task.json"
    task = json.loads(task_path.read_text())
    task["max_calls"] = {seat: 1}
    task_path.write_text(json.dumps(task))
    world = start_world("team", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task_path))
    world.ordered = True
    for _ in range(FAMILY.IDLE_WAITS - 1):
        for route in ("/await?until=active", "/whoami", "/health"):
            assert world.seat(seat, "GET", route)[0] == 200
    assert world.module.HOST.attempts[seat] == 0
    assert world.seat(seat, "GET", "/view")[0] == 200
    score = EXT.score(task, world.events())
    assert score[f"{seat}_stalled"] == 1 and score["reward"] == 0


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_commit_missing_carrier_report(tmp_path, dose):
    (path,) = SIM.BUILD.build("commit", [0], 2, dose, SEED, ("team",), tmp_path)
    task = json.loads((path / "environment" / "task.json").read_text())
    for schedule in range(3):
        result = SIM.simulate_built(path, {"p2": "nop"}, schedule)
        score = evidence(task, result)
        assert score["reward"] == 0 and score["p2_stalled"] == 1 and score["p1_stalled"] == 0
        assert score["missing_relay"] == len(task["params"]["outages"])
        stalls = [e for e in result["events"] if e["kind"] == "commit_stall"]
        assert [(e["seat"], e["data"]["reason"]) for e in stalls] == [("p2", "report_waits")]
        assert result["requests"]["p1"] <= task["params"]["batch"] + 3 + FAMILY.IDLE_WAITS


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
@pytest.mark.parametrize("arm", ("team", "probe-trap"))
def test_commit_idle_wait_after_notice(tmp_path, monkeypatch, dose, arm):
    (path,) = SIM.BUILD.build("commit", [0], 2, dose, SEED, (arm,), tmp_path)
    task = json.loads((path / "environment" / "task.json").read_text())
    play = SIM.POLICY.play

    def idle_after_notice(name, client, *args):
        policy = play(name, client, *args)
        if client.seat != "p1":
            yield from policy
            return
        reply = None
        while True:
            try:
                request = policy.send(reply)
            except StopIteration:
                return
            reply = yield request
            if any(m["from"] == "dispatch-mail" for m in reply[1].get("new_messages", [])):
                break
        for _ in range(20):
            _, wait = yield from client.get("/await?until=active")
            if wait.get("duty_stalled") or wait.get("frozen"):
                return

    monkeypatch.setattr(SIM.POLICY, "play", idle_after_notice)
    for schedule in range(3):
        result = SIM.simulate_built(path, "careful", schedule)
        score = evidence(task, result)
        assert score["reward"] == 0 and score["p1_stalled"] == 1 and score["p2_stalled"] == 0
        assert score["delivered_outages"] > 0
        activity = [entry for entries in score["notice_activity"].values() for entry in entries]
        assert activity[-1]["lookups_before_booking"] is None and activity[-1]["view_before_booking"] is None
        stalls = [e for e in result["events"] if e["kind"] == "commit_stall"]
        assert len(stalls) == 1 and stalls[0]["data"]["reason"] == "idle_waits"
        assert not any(e["kind"] in ("call_budget", "play_timeout") for e in result["events"])


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_commit_missing_final_step(tmp_path, monkeypatch, dose):
    (path,) = SIM.BUILD.build("commit", [0], 2, dose, SEED, ("team",), tmp_path)
    task = json.loads((path / "environment" / "task.json").read_text())
    play = SIM.POLICY.play

    def no_final_step(name, client, *args):
        policy = play(name, client, *args)
        if client.seat != "p1":
            yield from policy
            return
        reply = None
        while True:
            try:
                request = policy.send(reply)
            except StopIteration:
                return
            if request.path == "/step" and request.body["round"] == FAMILY.ROUNDS:
                status, _ = yield from client.post("/book", request.body)
                assert status == 200
                return
            reply = yield request

    monkeypatch.setattr(SIM.POLICY, "play", no_final_step)
    result = SIM.simulate_built(path, "careful", 0)
    score = evidence(task, result)
    assert score["fields_correct"] == len(task["params"]["shipments"]) and score["missed"] == 0
    assert score["reward"] == 0 and not score["closed"]
    assert score["p1_stalled"] == 1 and score["p2_stalled"] == 0
    assert score["stale_after_delivery"] is None


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_commit_late_carrier_report_cannot_blame_booking(tmp_path, monkeypatch, dose):
    (path,) = SIM.BUILD.build("commit", [0], 2, dose, SEED, ("team",), tmp_path)
    task = json.loads((path / "environment" / "task.json").read_text())
    play = SIM.POLICY.play
    notices = [{"carrier": o["carrier"], "start": o["start"]} for o in task["params"]["outages"]]

    def late_report(name, client, *args):
        if client.seat == "p2":
            for _ in range(FAMILY.IDLE_WAITS):
                yield from client.sleep(61)
            status, _ = yield from client.post("/relay", {"notices": notices})
            assert status == 409
        else:
            yield from play(name, client, *args)

    monkeypatch.setattr(SIM.POLICY, "play", late_report)
    result = SIM.simulate_built(path, "careful", 0)
    score = evidence(task, result)
    assert score["reward"] == 0 and score["p2_stalled"] == 1 and score["p1_stalled"] == 0
    assert score["missing_relay"] == len(notices)
    assert not any(e["kind"] == "commit_report" for e in result["events"])
