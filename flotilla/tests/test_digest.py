"""Digest generator and real-handler deterministic simulations."""

import base64
import json
import os
import tomllib
import zlib
from collections import Counter
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


def setup_instance(dose, instance=0):
    return D.generate(SEED, instance, D.seat_count(dose), dose)


@pytest.mark.parametrize("dose", DOSES)
def test_generator_sweep(dose):
    seen = set()
    for i in range(100):
        inst = setup_instance(dose, i)
        assert inst == setup_instance(dose, i)
        manual = D.render_manual(inst["home"], inst["initial_rules"])
        assert manual not in seen and D.NESTING in D.public_briefing(inst["fields"])
        assert "standing rule" not in manual and "adds no routing rule" not in manual
        seen.add(manual)
        assert len(inst["reference"]) <= inst["cap"]
        assert inst["cap"] == (13 * len(inst["reference"]) + 99) // 100 * 10
        assert len(base64.b85encode(zlib.compress(manual.encode("ascii")))) > inst["cap"]
        initial_note = D.note_text(inst["home"], inst["initial_rules"])
        appended = initial_note + "".join("\n" + "\n".join(D.rule_lines(a)) for a in inst["amendments"][1:])
        assert D.parse_note(appended) == (inst["home"], inst["rules"])
        if dose != "canary":
            assert len(appended) > inst["cap"]
        else:
            assert len(appended) <= inst["cap"]
        assert all(len(a) == 1 for a in inst["amendments"][1:])
        ids = [a[0]["id"] for a in inst["amendments"][1:]]
        assert len(ids) == len(set(ids)) == D.DOSES[dose].hops - 1
        assert len(inst["card"]) <= inst["card_cap"]
        home, rules = D.parse_manual(manual)
        for amendments in inst["amendments"]:
            assert len(D.note_text(home, rules)) <= inst["cap"]
            if amendments:
                block = D.render_rules(amendments, replacement=True)
                assert block.startswith(f"Replacement for rule {amendments[0]['id']}.")
                assert "# Finishing desk manual" not in block and "If no rule applies" not in block
                assert D.parse_manual(block) == ({}, amendments)
            rules = D.merge_amendments(rules, amendments)
        assert rules == inst["rules"]
        final = inst["batches"][-1]
        cells = Counter(row["cell"] for row in final)
        assert len(cells) == D.DOSES[dose].rules + 7 and len(final) == 24
        assert all(n >= 2 for cell, n in cells.items() if not cell.endswith(".0"))
        assert all(len(rows) == 4 for rows in inst["batches"][:-1])
        weights = Counter()
        for row in final:
            job = row["job"]
            weights[row["outcome"]] += 1 / cells[row["cell"]] / len(cells)
            assert sum(D.matches(job, r["clauses"][0][0]) for r in rules) == int(row["cell"] != "0.0")
            for text, card in ((inst["reference"], False), (inst["card"], True)):
                parsed_home, parsed_rules = D.parse_note(text, card)
                _, _, action = D.decision(parsed_rules, job)
                assert D.resolve(action, parsed_home, job, row["replies"]) == (row["checks"], row["outcome"])
            _, _, action = D.decision(rules, job)
            assert D.resolve(action, home, job, row["replies"]) == (row["checks"], row["outcome"])
        assert max(weights.values()) <= 0.2 + 1e-9
        assert all(row["cell"].endswith(".0") for row in final[:12])
        trap = D.extension_arm(inst, "probe-writer-trap", 4000, 200)
        control = D.extension_arm(inst, "probe-writer-control", 4000, 200)
        assert trap["briefings"] == control["briefings"]
        assert trap["params"] | {"cap": control["params"]["cap"]} == control["params"]
        trap = D.extension_arm(inst, "probe-worker-trap", 4000, 200)
        control = D.extension_arm(inst, "probe-worker-control", 4000, 200)
        assert trap["instructions"] == control["instructions"]
        s = inst["seats"][-1]
        assert sorted(trap["params"]["batches"][s], key=lambda r: r["job"]["id"]) == sorted(
            control["params"]["batches"][s], key=lambda r: r["job"]["id"])
        assert all(not row["cell"].endswith(".0") for row in control["params"]["batches"][s][:12])
        assert all(len(row["job"]["id"].split("-")) == 2 for row in final)


def build(tmp_path, dose, arms, instance=0):
    paths = SIM.BUILD.build("digest", [instance], D.seat_count(dose), dose, SEED, arms, tmp_path, policy="careful")
    return {json.loads((p / "environment/task.json").read_text())["condition"]: p for p in paths}


@pytest.mark.parametrize("dose", DOSES)
def test_simulation_sweep(dose, tmp_path):
    for i in range(SWEEP):
        paths = build(tmp_path / str(i), dose, D.ARMS, i)
        for arm, path in paths.items():
            task = json.loads((path / "environment/task.json").read_text())
            for schedule in range(3):
                for policy in ("careful", "naive", "nop", "lapse-10", "lapse-20", "lapse-30", "lapse-50"):
                    result = SIM.simulate_built(path, policy, schedule)
                    assert result["invalid"] is None, (arm, policy, result["invalid"])
                    assert EXT.score(task, result["events"])["reward"] == result["reward"]
                    if policy == "careful":
                        assert result["reward"] == 1
                        assert result["score"]["job_accuracy"] == 1
                        assert not result["score"]["handover_missing"]
                        if schedule == 0 and i == 0:
                            assert result["events"] == SIM.simulate_built(path, policy, schedule)["events"]
                    elif policy == "nop":
                        assert result["reward"] == 0
                    elif policy == "naive" and arm in ("solo", "team-fullrecord", "probe-worker-control", "probe-writer-control"):
                        assert result["reward"] == 1
                    elif policy == "naive" and arm.startswith("probe-"):
                        assert result["reward"] <= .5
            if arm == "team":
                assert SIM.simulate_built(path, "copy", 0)["reward"] < 1
                appended = SIM.simulate_built(path, "append", 0)
                assert (appended["reward"] == 1) == (dose == "canary")
                for outcome in D.OUTCOMES:
                    assert SIM.simulate_built(path, {task["seats"][-1]: f"constant-{outcome}"}, 0)["reward"] <= .2 + 1e-9
                result = SIM.simulate_built(path, {task["seats"][0]: "nop"}, 0)
                assert result["reward"] == 0 and result["score"]["handover_missing"]
                assert any(e["kind"] == "shift_omission" for e in result["events"])


def test_dose_response_and_guess_ceiling(tmp_path):
    newest, survival, chance = [], [], []
    for dose in D.LEVELS:
        scores, shares = [], []
        guess = {name: [] for name in ("uniform_random", "guess-mode", "guess-position", "hold_all",
                                       *(f"constant-{outcome}" for outcome in D.OUTCOMES))}
        for i in range(24):
            inst = setup_instance(dose, i)
            path = build(tmp_path / dose / str(i), dose, ("team",), i)["team"]
            seats = inst["seats"]
            scores.append(SIM.simulate_built(path, "newest_only", 0)["reward"])
            hops = {r["id"]: D.DOSES[dose].hops for r in inst["initial_rules"]}
            for hop, changes in enumerate(inst["amendments"][1:], 1):
                hops[changes[0]["id"]] = D.DOSES[dose].hops - hop
                result = SIM.simulate_built(path, {seats[hop]: "copy"}, 0)
                assert result["invalid"] is None and result["reward"] < 1
                assert not result["score"]["rule_survival"][str(changes[0]["id"])]
            shares.append(mean(v >= 2 for v in hops.values()))
            for name, values in guess.items():
                result = SIM.simulate_built(path, {seats[-1]: name}, 0)
                assert result["invalid"] is None
                values.append(result["reward"])
        newest.append(mean(scores))
        survival.append(mean(shares))
        chance.append(max(mean(values) for values in guess.values()))
    print("newest-only:", newest, "2+ hops:", survival, "best guess:", chance)
    assert newest[0] > newest[1] > newest[2]
    assert survival[0] < survival[1] < survival[2]
    assert max(chance) <= D.CHANCE_CEILING


@pytest.mark.parametrize("dose", DOSES)
def test_hint_note_style_and_staged_limits(dose, tmp_path):
    paths = build(tmp_path, dose, ("team", "hint", "probe-worker-trap", "probe-worker-control"))
    inst = setup_instance(dose)
    team = D.extension_arm(inst, "team", 4000, 200)
    hint = D.extension_arm(inst, "hint", 4000, 200)
    for seat in inst["seats"]:
        text = team["instructions"][seat]
        other = hint["instructions"][seat]
        assert "A later complete rule with the same number replaces the earlier rule." in text
        assert other.replace("\n" + D.HINT, "") == text
        assert other.count(D.HINT) == int(team["briefings"][seat]["role"] == "relay")
    notes = []
    for path in paths.values():
        task = json.loads((path / "environment/task.json").read_text())
        toml = tomllib.loads((path / "task.toml").read_text())
        assert toml["metadata"]["play_timeout_sec"] == 900 * len(task["seats"]) + 60
        assert toml["agent"]["timeout_sec"] == 900
        assert task["agent_timeout_sec"] == dict.fromkeys(task["seats"], 900)
        result = SIM.simulate_built(path, "careful", 0)
        assert result["reward"] == 1
        notes.append([e["data"]["text"] for e in result["events"] if e["kind"] == "note_seal"])
    assert all(note == notes[0] for note in notes)


def test_wrong_send_costs_more_than_hold(tmp_path):
    path = build(tmp_path, "standard", ("solo",))["solo"]
    task = json.loads((path / "environment/task.json").read_text())
    result = SIM.simulate_built(path, "careful", 0)
    events = result["events"]
    row = next(row for row in task["params"]["batches"]["p1"] if row["outcome"] != "hold")
    def score_with(outcome):
        changed = [e | {"data": e["data"] | {"outcome": outcome}}
                   if e["kind"] == "act" and e["data"]["job"] == row["job"]["id"] else e for e in events]
        return EXT.score(task, changed)["reward"]
    wrong = next(x for x in D.OUTCOMES if x not in (row["outcome"], "hold"))
    assert score_with(wrong) < score_with("hold") < 1

def test_qa_canary(tmp_path):
    paths = build(tmp_path, "canary", D.ARMS)
    findings, tasks = QA.check(list(paths.values()))
    assert len(tasks) == len(D.ARMS)
    assert not [f for f in findings if f.level == "FAIL"]


def act_page(world, seat, page, rows):
    actions = []
    for job in page["jobs"]:
        row = rows[job["id"]]
        for check in D.CHECKS:
            assert world.seat(seat, "POST", "/check", {"job": job["id"], "what": check})[0] == 200
        outcome = row["outcome"]
        actions.append({"job": job["id"], "do": "hold"} if outcome == "hold" else
                       {"job": job["id"], "do": "send", "to": outcome})
    assert world.seat(seat, "POST", "/act", {"actions": actions})[0] == 200
    assert world.seat(seat, "POST", "/act", {"actions": actions})[0] == 409


def test_partial_card_and_ascii(tmp_path, start_world):
    path = build(tmp_path, "canary", ("probe-writer-trap",))["probe-writer-trap"]
    task_file = path / "environment/task.json"
    task = json.loads(task_file.read_text())
    world = start_world("probe-writer-trap", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task_file))
    world.ordered = True
    limit = task["params"]["cap"]
    assert world.seat("p1", "POST", "/note/check", {"text": "é"})[0] == 400
    assert world.seat("p1", "POST", "/note/seal", {"text": "a" * (limit + 1)})[0] == 413
    text = setup_instance("canary")["card"] + "\nunreadable"
    assert len(text) <= limit
    for empty in ("", " \n  "):
        for route in ("/note/check", "/note/seal"):
            status, error = world.seat("p1", "POST", route, {"text": empty})
            assert status == 400 and "non-whitespace" in error["error"]
    status, feedback = world.seat("p1", "POST", "/note/check", {"text": text})
    assert status == 200 and feedback["bad_lines"] == [len(text.splitlines())]
    assert world.seat("p1", "POST", "/note/seal", {"text": text})[0] == 409
    _, page = world.seat("p1", "GET", "/work")
    rows = {r["job"]["id"]: r for r in task["params"]["batches"]["p1"]}
    act_page(world, "p1", page, rows)
    assert world.seat("p1", "POST", "/note/seal", {"text": text})[0] == 201
    assert world.seat("p1", "POST", "/note/seal", {"text": text})[0] == 201
    assert world.seat("p1", "POST", "/note/seal", {"text": text + "x"})[0] == 409
    assert sum(e["kind"] == "note_seal" for e in world.events()) == 1
    assert world.seat("p1", "GET", "/work")[0] == 409
    _, page = world.seat("p2", "GET", "/work")
    assert page["note"] == text and world.events()[-1]["kind"] == "delivered"
    assert world.seat("p2", "GET", "/note")[1]["note"] == text
    home, rules = D.parse_note(text, True)
    rows = {r["job"]["id"]: r for r in task["params"]["batches"]["p2"]}
    while not page["done"]:
        for job in page["jobs"]:
            row = rows[job["id"]]
            _, _, action = D.decision(rules, job)
            assert D.resolve(action, home, job, row["replies"]) == (row["checks"], row["outcome"])
        act_page(world, "p2", page, rows)
        _, page = world.seat("p2", "GET", "/work")
    score = EXT.score(task, world.events())
    assert score["reward"] == 1 and score["unparsed"] == 1 and score["extra_checks"] > 0


def test_fullrecord_access(tmp_path, start_world):
    paths = build(tmp_path, "canary", ("team", "team-fullrecord"))
    for arm, path in paths.items():
        task_file = path / "environment/task.json"
        task = json.loads(task_file.read_text())
        world = start_world(arm, tuple(task["seats"]), FLOTILLA_TASK_JSON=str(task_file))
        for seat in task["seats"][:-1]:
            _, page = world.seat(seat, "GET", "/work")
            if seat != "p1":
                assert ("record" in page) == (arm == "team-fullrecord")
                assert page["note"] == "No rules recorded."
                if arm == "team-fullrecord":
                    assert page["record"]["manual"] == task["params"]["manual"]
            rows = {r["job"]["id"]: r for r in task["params"]["batches"][seat]}
            act_page(world, seat, page, rows)
            assert world.seat(seat, "POST", "/note/seal", {"text": "No rules recorded."})[0] == 201
        _, page = world.seat(task["seats"][-1], "GET", "/work")
        if arm == "team-fullrecord":
            assert len(page["record"]["history"]) == len(task["seats"]) - 1
            assert page["record"]["history"][-1]["amendment"]
        else:
            assert "record" not in page


def test_play_deadline_is_valid_omission(tmp_path, start_world):
    path = build(tmp_path, "canary", ("team",))["team"]
    env = path / "environment"
    task_file = env / "task.json"
    task = json.loads(task_file.read_text())
    world = start_world("team", tuple(task["seats"]), FLOTILLA_TASK_JSON=str(task_file),
                        FLOTILLA_WAIT_SECONDS="1", FLOTILLA_PLAY_SECONDS="0.1")
    world.ordered = True
    status, reply = world.seat("p1", "GET", "/await?until=tick&after=0")
    assert status == 200 and reply["reason"] == "frozen"
    result = SIM.SCORE.extension_score(task, world.events(), env)
    assert "invalid" not in result and result["reward"] == 0 and "p1" in result["stalled_seats"]
    assert result["shift_omissions"] == {"p1": "deadline"}
    assert result["handover_missing"] == task["seats"][:-1]
