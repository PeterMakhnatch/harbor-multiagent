"""Handoff design sweeps and regressions from the independent referee review."""

import json
import os
import statistics
from collections import defaultdict

import pytest
from conftest import FLOTILLA, load

FAMILY = load(FLOTILLA / "seams/handoff.py")
EXT = load(FLOTILLA / "world/ext/handoff.py")
SIM = load(FLOTILLA / "sim.py")
QA = load(FLOTILLA / "qa.py")
POLICIES = load(FLOTILLA / "policies/handoff.py")
SEED = 7236499753218553
COUNT = int(os.environ.get("HANDOFF_SWEEP_INSTANCES", "1"))
SCHEDULES = 5 if COUNT >= 200 else 2
LAPSES = ("lapse-10", "lapse-20", "lapse-30", "lapse-50")
STYLES = ("reverse", "colon", "slash", "lines", "ref-first", "json", "arrow", "ref-eq", "cleared", "paren")


def task_for(inst, arm, dose):
    rendering = FAMILY.extension_arm(inst, arm, 600, 32)
    return {"condition": arm, "params": rendering["params"], "difficulty": dose,
            "seats": sorted(rendering["briefings"]), "initial_active": rendering["initial_active"],
            "max_calls": rendering["max_calls"], "extension": "handoff", "messaging": False}


def expected(arm, policy, dose):
    if policy == "careful":
        return 1.0
    if policy == "lone" and arm == "team":
        return {"canary": 1/3, "standard": 5/16, "heavy": 9/32}[dose]
    if policy != "naive":
        return None
    if arm in ("solo", "twin", "team-fullrecord") or arm.endswith("-control"):
        return 1.0
    if arm.endswith("-trap"):
        return 0.0
    return {"canary": 0.5, "standard": 0.375, "heavy": 0.3125}[dose] + (
        0.5 if arm == "team-record" else 0.0)


def receipt_invariants(task, events):
    params = task["params"]
    cases = {b["reference"]: b for b in params["batches"]}
    dispatches = defaultdict(set)
    accepted = set()
    views = {}
    handovers = []
    delivered = set()
    for event in events:
        data = event["data"]
        if event["kind"] == "dispatched":
            dispatches[data["shift"]].add(data["reference"])
        elif event["kind"] == "settled":
            assert data["reference"] not in accepted
            accepted.add(data["reference"])
        elif event["kind"] == "handover":
            assert data["length"] == len(data["note"]) <= params["cap"]
            handovers.append(data["note"])
        elif event["kind"] == "desk_view":
            shift, reply = data["shift"], data["reply"]
            views[data["view"]] = (event["seat"], data["path"], reply, shift)
            assert data["status"] == 200 and reply["calls_left"] >= 0
            assert not {"bad", "params", "condition", "correct", "arm", "seed"} & reply.keys()
            if data["path"] == "/handover":
                assert "note" not in reply
            else:
                assert reply["note"] == ("" if shift == 1 else handovers[shift - 2])
            assert set(reply.get("references", {}).values()) <= dispatches[shift]
            for result in reply["inbox"]:
                ref = result["reference"]
                batch = cases[ref]
                assert FAMILY.arrival(batch) <= shift
                assert result["status"] == batch["status"]
                assert ref in dispatches[batch["shift"]]
                assert ref not in accepted
                if data["path"] == "/dispatch":
                    assert ref not in reply["references"].values()
            if ("calls" in reply and task["condition"] not in ("team-record", "team-fullrecord")
                    and not (task["condition"].endswith("-control") and shift == params["shifts"])):
                assert all(call["shift"] == shift for call in reply["calls"])
        elif event["kind"] == "delivered":
            ids = data["ids"]
            view = next((item for item in ids if item.startswith("view-")), None)
            if view is None:
                continue
            assert view in views and view not in delivered
            seat, path, reply, shift = views[view]
            assert (seat, path) == (event["seat"], data["path"])
            assert {f"result-{r['reference']}" for r in reply["inbox"]} <= set(ids)
            assert {f"binding-{b}-{r}" for b, r in reply.get("references", {}).items()} <= set(ids)
            assert (f"note-{shift - 1}" in ids) == (shift > 1 and "note" in reply)
            delivered.add(view)
    assert set(views) == delivered


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_handoff_generator(dose):
    fingerprints = set()
    for instance in range(200):
        inst = FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        assert inst == FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        fingerprints.add(json.dumps(inst, sort_keys=True))
        params = inst["base"]
        assert params["cap"] == FAMILY.NOTE_CAP
        assert len(params["lots"]) == 32
        for shift in range(1, params["shifts"] + 1):
            planned = [b for b in params["batches"] if b["shift"] == shift]
            assert len(planned) == 4
            assert all(len(b["lots"]) == 16 for b in planned)
            # Every candidate lot could cause the same number of returns on the visible plan.
            # In particular, batch size and frequency cannot predict the returned member.
            assert all(sum(lot in b["lots"] for b in planned) == 2 for lot in params["lots"])
        assert max(b["lag"] for b in params["batches"]) == FAMILY.LEVELS.index(dose) + 1
        # The first shift carries a necessary observation unavailable in the final shift.
        assert len(FAMILY.candidates(params["lots"], [b for b in params["batches"] if FAMILY.arrival(b) > 1])) >= 2
        for first, second in FAMILY.TWINS:
            left, right = (FAMILY.extension_arm(inst, arm, 600, 32) for arm in (first, second))
            for key in ("instructions", "briefings", "scripted_seats", "params"):
                assert left[key] == right[key]
            assert len(left["briefings"]) - len(left["scripted_seats"]) == 1
            target = next(b for b in left["params"]["batches"] if b["batch"] == left["params"]["ref_target"])
            assert target["shift"] < left["params"]["shifts"] - 1
            assert FAMILY.arrival(target) == left["params"]["shifts"]
        assert 0 < FAMILY.chance_baseline(task_for(inst, "team", dose), "blind") < 0.5
    assert len(fingerprints) == 200


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_handoff_sweep(dose, tmp_path):
    tally = defaultdict(list)
    chance = []
    max_requests = 0
    for instance in range(COUNT):
        inst = FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        paths = {json.loads((p / "environment/task.json").read_text())["condition"]: p
                 for p in SIM.BUILD.build("handoff", [instance], FAMILY.DOSES[dose], dose, SEED,
                                          FAMILY.ARMS, tmp_path)}
        for arm, path in paths.items():
            task = task_for(inst, arm, dose)
            for policy in ("careful", "naive", "nop", "blind", "lone", *LAPSES):
                previous = None
                for schedule in range(SCHEDULES):
                    run = SIM.simulate_built(path, policy, schedule)
                    if policy == "nop":
                        assert run["invalid"] == "handoff_missing" and run["reward"] is None
                        assert "missing_bindings" not in run["score"]
                        continue
                    assert run["invalid"] is None and not run["stalled"]
                    reward, score = run["reward"], run["score"]
                    assert 0 <= reward <= 1 and 0 <= score["full_reward"] <= 1
                    assert score["handover_count"] == task["params"]["shifts"]
                    assert not any(run["files_read"].values())
                    assert {k: v for k, v in score.items() if k != "versions"} == EXT.score(task, run["events"])
                    tally[arm, policy].append(reward)
                    target = expected(arm, policy, dose)
                    if target is not None:
                        assert reward == pytest.approx(target)
                    receipt_invariants(task, run["events"])
                    if policy == "careful":
                        assert score["full_reward"] == 1
                        assert score["first_loss_shift"] is None
                        if arm != "solo":
                            max_requests = max(max_requests, max(run["requests"].values()))
                            assert max_requests <= 13
                    if policy == "blind" and arm == "team":
                        chance.append(FAMILY.chance_baseline(task, policy))
                    if policy in LAPSES and arm in ("solo", "team-fullrecord"):
                        assert reward == 1
                    if previous is not None:
                        assert previous == reward
                    previous = reward
                if instance == 0 and policy == "careful":
                    assert SIM.simulate_built(path, policy, 0)["events"] == SIM.simulate_built(path, policy, 0)["events"]
    print(f"\n{dose}: {COUNT} instances x {SCHEDULES} schedules; max careful calls/seat {max_requests}")
    for (arm, policy), scores in sorted(tally.items()):
        print(f"{arm:20} {policy:8} n={len(scores):4} mean={statistics.mean(scores):.6f}")
    print(f"blind exact chance {statistics.mean(chance):.6f}, observed {statistics.mean(tally['team', 'blind']):.6f}")
    if COUNT >= 200:
        assert abs(statistics.mean(chance) - statistics.mean(tally["team", "blind"])) < 0.05


@pytest.mark.parametrize("dose", FAMILY.LEVELS)
def test_handoff_probe_note_formats_and_actor(dose, tmp_path):
    arms = tuple(a for a in FAMILY.ARMS if a.startswith("probe-"))
    paths = SIM.BUILD.build("handoff", [0], FAMILY.DOSES[dose], dose, SEED, arms, tmp_path)
    for path in paths:
        task = json.loads((path / "environment/task.json").read_text())
        for policy in STYLES:
            run = SIM.simulate_built(path, policy, 0)
            assert run["reward"] == 1 and run["score"]["unparsed_bindings"] == 0
            params = task["params"]
            if "hunt" in task["condition"]:
                targets = [e for e in run["events"] if e["kind"] == "ticket"]
            else:
                targets = [e for e in run["events"]
                           if e["kind"] == "settled" and e["data"]["batch"] == params["ref_target"]]
            assert len(targets) == 1 and targets[0]["seat"] == f"s{params['shifts']}"


def test_handoff_parser_regressions():
    lots = ["L01", "L02", "L03"]
    gold = {"B01": "R01", "B02": "R02"}, {"L01"}
    for text in (
        '{"pending":{"B01":"R01","B02":"R02"},"suspects":["L01"]}',
        "B01 -> R01; B02 -> R02; Suspects L01",
        "R01=B01 R02=B02; Suspects L01",
        "B01 (ref R01); B02 (ref R02); Suspects L01",
        "Batch B01 has carrier ref R01; B02=R02; Suspects L01; ruled out L02 L03",
        "R01 B01 R02 B02; Cleared L02 L03",
    ):
        assert FAMILY.parse_note(text, lots) == gold
    assert FAMILY.parse_note("B01R01", lots)[0] == {}


def start_desk(tmp_path, start_world, arm="team", budget=None):
    task = task_for(FAMILY.generate(SEED, 0, 3, "canary"), arm, "canary")
    if budget is not None:
        task["max_calls"] = budget
    config = tmp_path / "task.json"
    config.write_text(json.dumps(task))
    (tmp_path / "family.py").write_text((FLOTILLA / "seams/handoff.py").read_text())
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext/handoff.py").write_text((FLOTILLA / "world/ext/handoff.py").read_text())
    world = start_world(arm, tuple(task["seats"]), FLOTILLA_TASK_JSON=str(config))
    world.ordered = True
    return task, world


def test_handoff_refusals_and_activation(tmp_path, start_world):
    task, world = start_desk(tmp_path, start_world, budget=100)
    get = lambda seat, path: world.seat(seat, "GET", path)
    post = lambda seat, path, body: world.seat(seat, "POST", path, body)
    assert get("s2", "/desk")[0] == 409
    code, desk = get("s1", "/desk")
    assert code == 200 and desk["note"] == "" and desk["inbox"] == []
    batch = desk["plan"][0]["batch"]
    assert post("s1", "/dispatch", {"batches": [batch]})[0] == 409
    assert post("s1", "/print", {"batch": []})[0] == 409
    assert post("s1", "/print", {"batch": batch})[0] == 200
    assert post("s1", "/print", {"batch": batch})[0] == 409
    code, dispatch = post("s1", "/dispatch", {"batches": [batch]})
    assert code == 200 and dispatch["inbox"] == []
    assert post("s1", "/handover", {"note": "é" * (task["params"]["cap"] + 1)})[0] == 413
    assert get("s1", "/desk")[1]["shift"] == 1
    assert post("s1", "/close", {})[0] == 200
    for path, body in (("/print", {"batch": batch}), ("/dispatch", {"batches": [batch]}),
                       ("/settle", {"items": []}), ("/ticket", {"lot": desk["lots"][0]})):
        assert post("s1", path, body)[0] == 409
    note = f"{batch}={dispatch['references'][batch]}"
    code, reply = post("s1", "/handover", {"note": note})
    assert code == 200 and "note" not in reply
    for path in ("/desk", "/log"):
        assert get("s1", path)[0] == 409
    code, desk = get("s2", "/desk")
    assert code == 200 and desk["shift"] == 2 and desk["note"] == note
    assert all(call["shift"] == 2 for call in get("s2", "/log")[1]["calls"])


@pytest.mark.parametrize("arm", ["team", "team-record", "team-fullrecord"])
def test_handoff_record_scope(tmp_path, start_world, arm):
    _, world = start_desk(tmp_path, start_world, arm)
    get = lambda seat, path: world.seat(seat, "GET", path)
    post = lambda seat, path, body: world.seat(seat, "POST", path, body)
    assert get("s1", "/desk")[0] == 200
    assert post("s1", "/handover", {"note": ""})[0] == 200
    assert get("s2", "/desk")[0] == 200
    assert {call["shift"] for call in get("s2", "/log")[1]["calls"]} == (
        {1, 2} if arm == "team-fullrecord" else {2})
    assert post("s2", "/close", {})[0] == 200
    assert {call["shift"] for call in get("s2", "/log")[1]["calls"]} == (
        {2} if arm == "team" else {1, 2})


def test_handoff_early_ticket_and_settlement(tmp_path, start_world):
    task, world = start_desk(tmp_path, start_world, budget=100)
    get = lambda seat, path: world.seat(seat, "GET", path)
    post = lambda seat, path, body: world.seat(seat, "POST", path, body)
    _, desk = get("s1", "/desk")
    assert post("s1", "/ticket", {"lot": task["params"]["bad"]})[0] == 200
    assert post("s1", "/ticket", {"lot": desk["lots"][0]})[0] == 409
    first, second = [b for b in task["params"]["batches"] if b["shift"] == 1 and b["lag"] == 0]
    for batch in (first, second):
        assert post("s1", "/print", {"batch": batch["batch"]})[0] == 200
    code, reply = post("s1", "/dispatch", {"batches": [first["batch"], second["batch"]]})
    assert code == 200 and reply["inbox"] == []
    first_item = {"reference": first["reference"], "batch": first["batch"], "action": EXT.action_for(first["status"])}
    second_item = {"reference": second["reference"], "batch": second["batch"], "action": EXT.action_for(second["status"])}
    assert post("s1", "/settle", {"items": [first_item]})[0] == 409
    assert post("s1", "/settle", {"items": [first_item]})[0] == 200
    assert post("s1", "/settle", {"items": [first_item]})[0] == 409
    assert post("s1", "/settle", {"items": [second_item | {"batch": first["batch"]}]})[0] == 409
    wrong_action = "close" if second_item["action"] == "reissue" else "reissue"
    code, reply = post("s1", "/settle", {"items": [second_item | {"action": wrong_action}]})
    assert code == 200 and reply["recorded"] == 1 and "correct" not in reply
    for seat in task["seats"]:
        assert post(seat, "/handover", {"note": ""})[0] == 200
    score = EXT.score(task, world.events())
    assert score["ticket"] == 0 and score["ticket_early"] == 1 and score["reconciled"] == 1


def test_handoff_reserved_calls(tmp_path, start_world):
    task, world = start_desk(tmp_path, start_world)
    get = lambda path: world.seat("s1", "GET", path)
    for _ in range(FAMILY.CALL_CAP - 3):
        code, reply = get("/desk")
        assert code == 200
    assert reply["calls_left"] == 3
    code, reply = get("/desk")
    assert code == 429 and reply["calls_left"] == 2
    code, reply = world.seat("s1", "POST", "/handover", {"note": "é" * task["params"]["cap"]})
    assert code == 200 and reply["calls_left"] == 1
    assert world.seat("s2", "GET", "/desk")[0] == 200


def test_handoff_budget_and_invalid_evidence():
    for policy in ("exhaust", "nop"):
        run = SIM.simulate("handoff", "team", 0, {"s2": policy}, SEED, 4, difficulty="canary")
        assert run["invalid"] == "handoff_missing" and run["reward"] is None
        assert run["score"]["handover_count"] == 1
        assert "missing_bindings" not in run["score"]
    for arm in ("probe-hunt-control", "probe-ref-control", "probe-hunt-trap", "probe-ref-trap"):
        run = SIM.simulate("handoff", arm, 0, "nop", SEED, 4, difficulty="canary")
        assert run["invalid"] == "handoff_missing" and run["reward"] is None
    stalled = SIM.simulate("handoff", "team", 0, {"s1": "stall"}, SEED, 4, difficulty="canary")
    assert stalled["invalid"] and stalled["stalled"] and stalled["reward"] is None
    task = task_for(FAMILY.generate(SEED, 0, 3, "canary"), "team", "canary")
    complete = SIM.simulate("handoff", "team", 0, "careful", SEED, 4, difficulty="canary")["events"]
    missing = next(e for e in complete if e["kind"] == "delivered")
    assert EXT.score(task, [e for e in complete if e is not missing]) == {"invalid": "desk reply lacks a delivery receipt"}


def test_handoff_canary_qa(tmp_path):
    paths = SIM.BUILD.build("handoff", [0], 3, "canary", SEED, FAMILY.ARMS, tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(FAMILY.ARMS)
    assert not [f for f in findings if f.level == "FAIL"]
