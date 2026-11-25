"""Handoff design sweeps, world receipts, and matched-arm invariants."""

import json
import os
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

FAMILY = load(FLOTILLA / "seams/handoff.py")
EXT = load(FLOTILLA / "world/ext/handoff.py")
SIM = load(FLOTILLA / "sim.py")
QA = load(FLOTILLA / "qa.py")
SEED = 7236499753218553
COUNT = int(os.environ.get("HANDOFF_SWEEP_INSTANCES", "3"))
SCHEDULES = 5 if COUNT >= 200 else 2


def task_for(inst, arm, dose):
    rendering = FAMILY.extension_arm(inst, arm, 600, 32)
    return {"condition": arm, "params": rendering["params"], "difficulty": dose,
            "seats": sorted(rendering["briefings"]), "initial_active": rendering["initial_active"],
            "extension": "handoff", "messaging": False}


def expected(arm, policy, dose):
    if policy == "careful":
        return 1
    if policy == "nop":
        return 0
    if policy == "lone":
        return {"canary": 1/3, "standard": 1/6, "heavy": 3/28}[dose]
    if arm in ("solo", "twin") or arm.endswith("-control"):
        return 1
    if arm.endswith("-trap"):
        return 0
    if dose == "canary":
        return 1
    return {"standard": 1/3, "heavy": 2/7}[dose] + (0.5 if arm == "team-record" else 0)


def receipt_invariants(task, events):
    params = task["params"]
    cases = {b["reference"]: b for b in params["batches"]}
    dispatches = defaultdict(set)
    accepted = set()
    views = Counter()
    handovers = []
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
            views[shift] += 1
            assert data["status"] == 200
            assert not {"bad", "params", "condition", "correct", "arm", "seed"} & reply.keys()
            assert reply["note"] == ("" if shift == 1 else handovers[shift - 2])
            assert set(reply.get("references", {}).values()) <= dispatches[shift]
            for result in reply["inbox"]:
                ref = result["reference"]
                batch = cases[ref]
                assert batch["lag"] is not None and FAMILY.arrival(batch) <= shift
                assert result["status"] == batch["status"]
                assert ref in dispatches[batch["shift"]]
                assert ref not in accepted
                if data["path"] == "/dispatch":
                    assert ref not in reply["references"].values()
            if "calls" in reply and task["condition"] != "team-record":
                assert all(call["shift"] == shift for call in reply["calls"])
    return max(views.values(), default=0)


def blind_expected(task, events):
    """Conditional chance after prior guesses have changed the open batch pool."""
    params = task["params"]
    true = {b["reference"]: b["batch"] for b in params["batches"]}
    dispatched = defaultdict(dict)
    desks = {}
    for event in events:
        if event["kind"] != "desk_view":
            continue
        data = event["data"]
        shift, reply = data["shift"], data["reply"]
        dispatched[shift].update({r: b for b, r in reply.get("references", {}).items()})
        if data["path"] == "/desk":
            desks[shift] = reply
    matches = ticket = 0.0
    for shift, desk in desks.items():
        local = {b["batch"]: b["lots"] for b in desk["plan"]}
        fresh = dispatched[shift]
        known = [{"lots": local[fresh[r["reference"]]], "status": r["status"]}
                 for r in desk["inbox"] if r["reference"] in fresh]
        unknown = [r for r in desk["inbox"] if r["reference"] not in fresh]
        open_ids = {b["batch"] for b in desk["open"]}
        matches += len(known)
        if open_ids:
            matches += sum(true[r["reference"]] in open_ids for r in unknown) / len(open_ids)
        if shift == params["shifts"]:
            possible, _ = FAMILY.visible_possibilities(params["lots"], known, desk["open"], unknown)
            ticket = 1 / len(possible) if params["bad"] in possible else 0
    total = sum(b["lag"] is not None for b in params["batches"])
    return 0.5 * (matches / total + ticket)


@pytest.mark.parametrize("dose", FAMILY.DOSES)
def test_handoff_generator(dose):
    fingerprints = set()
    for instance in range(200):
        inst = FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        assert inst == FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        fingerprints.add(json.dumps(inst, sort_keys=True))
        for first, second in FAMILY.TWINS:
            left, right = (FAMILY.extension_arm(inst, arm, 600, 32) for arm in (first, second))
            assert left["instructions"] == right["instructions"]
            assert left["briefings"] == right["briefings"]
            assert left["scripted_seats"] == right["scripted_seats"]
            changes = [(a["batch"], key) for a, b in zip(left["params"]["batches"], right["params"]["batches"])
                       for key in a if a[key] != b[key]]
            target = "hunt_target" if "hunt" in first else "ref_target"
            assert changes == [(left["params"][target], "shift" if "hunt" in first else "lag")]
        assert 0 < FAMILY.chance_baseline(inst["base"]) < 1
    assert len(fingerprints) == 200


@pytest.mark.parametrize("dose", FAMILY.DOSES)
def test_handoff_sweep(dose, tmp_path):
    tally = defaultdict(list)
    blind_chance = []
    turns = 0
    for instance in range(COUNT):
        with tempfile.TemporaryDirectory(dir=tmp_path) as root:
            paths = SIM.BUILD.build("handoff", [instance], FAMILY.DOSES[dose], dose, SEED, FAMILY.ARMS, Path(root))
            for path in paths:
                task = json.loads((path / "environment/task.json").read_text())
                arm = task["condition"]
                policies = ("careful", "naive", "nop", "lone", "blind") if arm == "team" else ("careful", "naive", "nop")
                for policy in policies:
                    for schedule in range(SCHEDULES):
                        run = SIM.simulate_built(path, policy, schedule)
                        reward = run["reward"]
                        tally[arm, policy].append(reward)
                        score = EXT.score(task, run["events"])
                        assert score == EXT.score(task, run["events"]) == EXT.score(task, run["events"])
                        assert reward == score["reward"]
                        assert 0 <= score["full_reward"] <= 1
                        assert not any(run["files_read"].values())
                        if policy == "blind":
                            blind_chance.append(blind_expected(task, run["events"]))
                        else:
                            assert reward == pytest.approx(expected(arm, policy, dose))
                        count = receipt_invariants(task, run["events"])
                        if policy == "careful":
                            turns = max(turns, count)
                            assert score["first_loss_shift"] is None
                            assert score["full_reward"] == 1
                        if instance == schedule == 0 and policy == "careful":
                            repeat = SIM.simulate_built(path, policy, schedule)
                            assert repeat["events"] == run["events"]
                if instance == 0 and arm == "team":
                    for alternate in ("reverse", "colon", "slash", "lines", "ref-first"):
                        assert SIM.simulate_built(path, alternate, 4)["reward"] == 1
    print(f"\n{dose}: {COUNT} instances x {SCHEDULES} schedules; max active calls/shift {turns}")
    for (arm, policy), scores in sorted(tally.items()):
        print(f"{arm:20} {policy:8} n={len(scores):4} mean={statistics.mean(scores):.6f}")
    actual = statistics.mean(tally["team", "blind"])
    chance = statistics.mean(blind_chance)
    print(f"blind conditional chance {chance:.6f}, observed {actual:.6f}")
    if COUNT >= 200:
        assert abs(actual - chance) < 0.05


def test_handoff_refusals_and_activation(tmp_path, start_world):
    inst = FAMILY.generate(SEED, 0, 3, "canary")
    task = task_for(inst, "team", "canary")
    config = tmp_path / "task.json"
    config.write_text(json.dumps(task))
    world = start_world("team", tuple(task["seats"]), FLOTILLA_TASK_JSON=str(config))
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
    assert post("s1", "/handover", {"note": "é" * 601})[0] == 413
    assert get("s1", "/desk")[1]["shift"] == 1
    assert post("s1", "/close", {})[0] == 200
    for path, body in (("/print", {"batch": batch}), ("/dispatch", {"batches": [batch]}),
                       ("/settle", {"items": []}), ("/ticket", {"lot": desk["lots"][0]})):
        assert post("s1", path, body)[0] == 409
    note = "Open carrier ref: " + dispatch["references"][batch]
    assert post("s1", "/handover", {"note": note})[0] == 200
    for path in ("/desk", "/log"):
        assert get("s1", path)[0] == 409
    code, desk = get("s2", "/desk")
    assert code == 200 and desk["shift"] == 2 and desk["note"] == note
    assert get("s2", "/log")[1]["calls"][0]["shift"] == 2
    assert post("s2", "/ticket", {"lot": desk["lots"][0]})[0] == 200
    assert post("s2", "/ticket", {"lot": desk["lots"][1]})[0] == 409
    assert post("s2", "/handover", {"note": ""})[0] == 200
    assert get("s2", "/desk")[0] == 409


def test_handoff_canary_qa(tmp_path):
    paths = SIM.BUILD.build("handoff", [0], 3, "canary", SEED, FAMILY.ARMS, tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(FAMILY.ARMS)
    assert not [f for f in findings if f.level == "FAIL"]
