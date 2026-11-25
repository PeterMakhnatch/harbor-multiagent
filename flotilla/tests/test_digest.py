"""Deterministic generator and real-handler simulations for digest."""

import json
import math
import os
import shutil
from collections import Counter, defaultdict
from statistics import mean

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
D = load(FLOTILLA / "seams" / "digest.py")
EXT = load(FLOTILLA / "world" / "ext" / "digest.py")
QA = load(FLOTILLA / "qa.py")
SEED = 823913749388661
DOSES = [os.environ["DIGEST_DOSE"]] if "DIGEST_DOSE" in os.environ else list(D.DOSES)
SWEEP = int(os.environ.get("DIGEST_SWEEP", "4"))


def expected_naive(dose, arm):
    cells = {"canary": 9, "standard": 21, "heavy": 34}[dose]
    if arm in ("solo", "probe-writer-control", "probe-worker-control"):
        return 1.0
    if arm == "probe-worker-trap":
        return (D.DOSES[dose].rules + 1) / cells
    if arm == "probe-writer-trap":
        common = D.DOSES[dose].common
        count = D.DOSES[dose].rules
        return (1 + count - math.ceil((count - common) / 3)) / cells
    return {"canary": 3, "standard": 7, "heavy": 8}[dose] / cells


@pytest.mark.parametrize("dose", DOSES)
def test_generator_sweep(dose):
    seen = set()
    n_seats = D.seat_count(dose)
    for i in range(200):
        inst = D.generate(SEED, i, n_seats, dose)
        assert inst == D.generate(SEED, i, n_seats, dose)
        manual = D.render_manual(inst["home"], inst["rules"])
        assert len(inst["reference"]) <= inst["cap"] < len(manual) / 2
        assert len(inst["card"]) <= inst["card_cap"]
        assert manual not in seen
        seen.add(manual)
        final = inst["batches"][-1]
        cells = Counter(row["cell"] for row in final)
        assert len(cells) == {"canary": 9, "standard": 21, "heavy": 34}[dose]
        assert len(final) == D.DOSES[dose].final_size
        weights = Counter()
        for row in final:
            job = row["job"]
            weights[row["outcome"]] += 1 / cells[row["cell"]] / len(cells)
            assert sum(D.matches(job, rule["clauses"][0][0]) for rule in inst["rules"]) == int(row["cell"] != "0.0")
            rid, depth, action = D.decision(inst["rules"], job)
            assert f"{rid}.{depth}" == row["cell"]
            for text, card in ((inst["reference"], False), (inst["card"], True)):
                home, rules = D.parse_note(text, card)
                _, _, action = D.decision(rules, job)
                assert D.resolve(action, home, job, row["replies"]) == (row["checks"], row["outcome"])
            home, rules = D.parse_manual(manual)
            _, _, action = D.decision(rules, job)
            assert D.resolve(action, home, job, row["replies"]) == (row["checks"], row["outcome"])
            assert bool(row["checks"] or row["outcome"] != home[job["product"]]) == (rid != 0)
        assert max(weights.values()) <= 0.2 + 1e-9
        assert all(row["cell"].endswith(".0") for row in final[:D.DOSES[dose].late])
        for rows in inst["batches"][:-1]:
            assert all(row["cell"].endswith(".0") and int(row["cell"].split(".")[0]) <= D.DOSES[dose].common
                       for row in rows)
        # Paired arms change their declared variable only, not the ticket facts or rules.
        trap = D.extension_arm(inst, "probe-writer-trap", 4000, 200)
        control = D.extension_arm(inst, "probe-writer-control", 4000, 200)
        assert trap["briefings"] == control["briefings"]
        assert trap["params"] | {"cap": control["params"]["cap"]} == control["params"]
        assert control["params"]["cap"] == 4 * trap["params"]["cap"]
        trap = D.extension_arm(inst, "probe-worker-trap", 4000, 200)
        control = D.extension_arm(inst, "probe-worker-control", 4000, 200)
        assert trap["instructions"] == control["instructions"] and trap["briefings"] == control["briefings"]
        s = inst["seats"][-1]
        assert sorted(trap["params"]["batches"][s], key=lambda r: r["job"]["id"]) == sorted(
            control["params"]["batches"][s], key=lambda r: r["job"]["id"])
        exceptions = len(cells) - 1 - D.DOSES[dose].rules
        assert all(not row["cell"].endswith(".0") for row in control["params"]["batches"][s][:exceptions])
        team = D.extension_arm(inst, "team", 4000, 200)
        hint = D.extension_arm(inst, "hint", 4000, 200)
        assert team["params"] == hint["params"] and team["briefings"] == hint["briefings"]
        for seat in team["instructions"]:
            rendered = hint["instructions"][seat].replace(
                "\nRules that seldom apply matter as much as common ones: keep every rule and every exception "
                "in the note, and cut background and reasons first.", "")
            assert rendered == team["instructions"][seat]


def check_result(task, result, reward):
    assert result["reward"] == pytest.approx(reward, abs=1e-12)
    assert EXT.score(task, result["events"])["reward"] == result["reward"]
    events = result["events"]
    assert sum(e["kind"] == "start" for e in events) == 1
    assert all(e["data"]["status"] < 500 for e in events if e["kind"] == "extension_call")
    if reward == 1:
        assert sum(e["kind"] == "note_seal" for e in events) == len(task["seats"]) - 1
        assert sum(e["kind"] == "note_delivered" for e in events) == len(task["seats"]) - 1
        assert len([e for e in events if e["kind"] == "act" and e["seat"] == task["seats"][-1]]) == len(
            task["params"]["batches"][task["seats"][-1]])


@pytest.mark.parametrize("dose", DOSES)
def test_simulation_sweep(dose, tmp_path):
    scores = defaultdict(list)
    turns = defaultdict(list)
    random_rewards = []
    for i in range(SWEEP):
        # Each build has all 8 arms, then reuses the exact bundle for the 5 interleavings.
        paths = SIM.BUILD.build("digest", [i], D.seat_count(dose), dose, SEED, D.ARMS, tmp_path / f"i{i}",
                                policy="careful")
        for path in paths:
            task = json.loads((path / "environment" / "task.json").read_text())
            arm = task["condition"]
            for schedule in range(5):
                for policy in ("careful", "naive", "nop"):
                    reward = 1 if policy == "careful" else expected_naive(dose, arm) if policy == "naive" else 0
                    result = SIM.simulate_built(path, policy, schedule)
                    check_result(task, result, reward)
                    scores[(arm, policy)].append(result["reward"])
                    if policy == "careful":
                        turns[arm].append(max(result["requests"].values()))
                        if i == 0 and schedule == 0:
                            assert result["events"] == SIM.simulate_built(path, policy, schedule)["events"]
            if arm == "team":
                final = task["seats"][-1]
                for policy in ("lane_only", "lone", "uniform_random"):
                    result = SIM.simulate_built(path, {final: policy}, 0)
                    if policy == "lane_only":
                        assert result["reward"] == pytest.approx(1 / len(EXT.score(task, result["events"])["cells"]))
                    elif policy == "uniform_random":
                        random_rewards.append(result["reward"])
                    else:
                        assert result["reward"] == SIM.simulate_built(path, {final: "uniform_random"}, 0)["reward"]
                if i == 0:
                    for seat in task["seats"]:
                        assert SIM.simulate_built(path, {s: "careful" if s == seat else "nop" for s in task["seats"]},
                                                  0)["reward"] == 0
                    for outcome in D.OUTCOMES:
                        assert SIM.simulate_built(path, {final: f"constant-{outcome}"}, 0)["reward"] <= 0.2 + 1e-9
        shutil.rmtree(tmp_path / f"i{i}")
    if SWEEP >= 200:
        assert abs(mean(random_rewards) - 0.1) < 0.05
    for (arm, policy), values in sorted(scores.items()):
        print(f"{dose:8} {arm:22} {policy:7} n={len(values):4} mean={mean(values):.6f}")
    for arm, values in sorted(turns.items()):
        print(f"{dose:8} {arm:22} max requests/seat={max(values)}")
    print(f"{dose:8} uniform_random n={len(random_rewards)} mean={mean(random_rewards):.6f}")


def test_qa_canary(tmp_path):
    paths = SIM.BUILD.build("digest", [0], 2, "canary", SEED, D.ARMS, tmp_path, policy="careful")
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(D.ARMS)
    assert not [f for f in findings if f.level == "FAIL"]


def test_alternative_note_and_route_legality(tmp_path, start_world):
    (path,) = SIM.BUILD.build("digest", [0], 2, "canary", SEED, ("team",), tmp_path, policy="careful")
    task_file = path / "environment" / "task.json"
    task = json.loads(task_file.read_text())
    world = start_world("team", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task_file))
    world.ordered = True
    assert world.seat("p2", "GET", "/work")[0] == 409
    assert world.seat("p1", "GET", "/work")[1]["jobs"] == []
    limit = task["params"]["cap"]
    assert world.seat("p1", "POST", "/note/check", {"text": "é" * limit}) == (200, {"chars": limit, "limit": limit})
    assert world.seat("p1", "POST", "/note/seal", {"text": "é" * (limit + 1)})[0] == 413
    assert world.seat("p2", "GET", "/work")[0] == 409
    assert world.seat("p1", "POST", "/check", {"job": "unknown", "what": "credit"})[0] == 409
    instance = D.generate(SEED, 0, 2, "canary")
    text = instance["reference"].replace("=>", " => ")
    assert len(text) <= limit
    assert world.seat("p1", "POST", "/note/seal", {"text": text})[0] == 201
    assert world.seat("p1", "GET", "/work")[0] == 409
    status, page = world.seat("p2", "GET", "/work")
    assert status == 200 and page["note"] == text
    assert world.seat("p2", "GET", "/note")[1]["note"] == text
    rows = {r["job"]["id"]: r for r in task["params"]["batches"]["p2"]}
    while not page["done"]:
        assert all(set(job) == {"id", "product", "qty", *instance["fields"]} for job in page["jobs"])
        assert world.seat("p2", "POST", "/act", {"actions": [{"job": "unknown", "do": "hold"}]})[0] == 409
        duplicate = {"job": page["jobs"][0]["id"], "do": "hold"}
        assert world.seat("p2", "POST", "/act", {"actions": [duplicate, duplicate]})[0] == 409
        assert world.seat("p2", "POST", "/check", {"job": [], "what": "credit"})[0] == 400
        acts = []
        for job in reversed(page["jobs"]):
            rid = job["id"]
            for check in reversed(D.CHECKS):
                status, reply = world.seat("p2", "POST", "/check", {"job": rid, "what": check})
                assert status == 200 and reply == {"job": rid, "what": check, "reply": rows[rid]["replies"][check]}
            outcome = rows[rid]["outcome"]
            acts.append({"job": rid, "do": "hold"} if outcome == "hold" else
                        {"job": rid, "do": "send", "to": outcome})
        assert world.seat("p2", "POST", "/act", {"actions": acts})[0] == 200
        assert world.seat("p2", "POST", "/act", {"actions": acts})[0] == 409
        _, page = world.seat("p2", "GET", "/work")
    events = world.events()
    assert EXT.score(task, events)["reward"] == 1
    assert not any(e["data"]["status"] >= 500 for e in events if e["kind"] == "extension_call")
    reversed_groups = [e for job in reversed(list(rows)) for e in events
                       if e["kind"] in ("check", "act") and e["data"]["job"] == job]
    assert EXT.score(task, reversed_groups)["reward"] == 1


def test_prior_shift_actions_do_not_reach_next_shift(tmp_path, start_world):
    (path,) = SIM.BUILD.build("digest", [1], 3, "standard", SEED, ("team",), tmp_path, policy="careful")
    task_file = path / "environment" / "task.json"
    text = D.generate(SEED, 1, 3, "standard")["reference"]

    def signal(bits):
        world = start_world("team", ("p1", "p2", "p3"), FLOTILLA_TASK_JSON=str(task_file))
        count = 0
        while True:
            status, page = world.seat("p1", "GET", "/work")
            assert status == 200
            if page["done"]:
                break
            acts = []
            for job in page["jobs"]:
                bit = (bits >> (count % 8)) & 1
                if bit:
                    assert world.seat("p1", "POST", "/check", {"job": job["id"], "what": "credit"})[0] == 200
                    assert world.seat("p1", "POST", "/check", {"job": ["bad"], "what": "press"})[0] == 400
                acts.append({"job": job["id"], "do": "send", "to": "N"} if bit else
                            {"job": job["id"], "do": "hold"})
                count += 1
            assert world.seat("p1", "POST", "/act", {"actions": acts})[0] == 200
        assert world.seat("p1", "POST", "/note/seal", {"text": text})[0] == 201
        return [world.seat("p2", "GET", route) for route in ("/whoami", "/work", "/note")]

    baseline = signal(0)
    for bit in range(8):
        assert signal(1 << bit) == baseline
