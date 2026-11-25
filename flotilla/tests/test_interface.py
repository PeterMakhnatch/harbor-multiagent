"""Deterministic proofs of the interface seam and regressions for the red-team findings."""

import copy
import hashlib
import json
import os
import tempfile
import tomllib
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

FAMILY = load(FLOTILLA / "seams" / "interface.py")
WORLD = load(FLOTILLA / "world" / "ext" / "interface.py")
SIM = load(FLOTILLA / "sim.py")
BUILD = SIM.BUILD
QA = load(FLOTILLA / "qa.py")
SEED = 7236499753218553
LEVELS = FAMILY.LEVELS
ALL_ARMS = (*FAMILY.ARMS, *FAMILY.EXTRA_ARMS)
TARGET_ARMS = {"probe-control", "probe-trap-key", "probe-trap-unit", "probe-trap-key-hint", "solo-seam-key",
               "solo-seam-unit"}
EXACT = ("careful", "naive", "nop", "blind", "lone", "script-loop")


def build(root, level, arms, instances=(0,), policy="careful"):
    return BUILD.build("interface", list(instances), None, level, SEED, tuple(arms), Path(root),
                       max_text=1000, max_messages=20, policy=policy)


def load_task(path):
    return json.loads((path / "environment" / "task.json").read_text())


def hashed(name):
    digest = hashlib.sha256(name.encode()).digest()
    return FAMILY.CONVENTIONS[int.from_bytes(digest[:8], "big") % len(FAMILY.CONVENTIONS)]


def expected_reward(task, policy):
    params, arm = task["params"], task["condition"]
    if policy == "nop" or (policy == "lone" and arm not in TARGET_ARMS and arm not in ("solo", "solo-seam")):
        return 0.0
    buyer = tuple(params["buyer"])
    valid = {}
    for group in params["groups"]:
        desk = tuple(group["saved"])
        guess = hashed(group["name"]) if policy == "blind" or arm == "team-nosaved" else buyer
        if arm in ("solo", "team-fullrecord"):
            valid[group["name"]] = True
        elif policy in ("careful", "script-loop"):
            valid[group["name"]] = not (arm == "silent" and group["hidden"]) or guess == desk
        else:
            valid[group["name"]] = guess == desk
    if arm in TARGET_ARMS:
        return float(valid[params["target"]])
    return sum(valid.values()) / len(valid)


def check_run(task, run, policy):
    score = run["score"]
    assert run["invalid"] is None and not run["stalled"] and run["reward"] == score["reward"]
    if policy in EXACT:
        assert run["reward"] == expected_reward(task, policy), (task["condition"], policy)
    assert all(200 <= e["data"]["status"] < 500 for e in run["events"] if e["kind"] == "extension_call")
    published, frozen = set(), False
    for e in run["events"]:
        frozen = frozen or e["kind"] == "freeze"
        if e["kind"] == "file_publish":
            assert not frozen
            file = e["data"]["file"]
            assert (file["kind"], file["group"]) not in published
            if file["kind"] == "order":
                assert ("catalog", file["group"]) in published
            published.add((file["kind"], file["group"]))
    if task["condition"] == "solo":
        assert all("blind_match" not in entry and "exposed" not in entry for entry in score["groups"].values())
    elif policy == "careful" and task["condition"] not in ("silent", "solo-seam"):
        assert all(entry["valid_coordinated"] for entry in score["groups"].values())
    return score


@pytest.mark.parametrize("level", LEVELS)
def test_constant_work_and_visibility_dose(level):
    for i in range(50):
        base = FAMILY.generate(SEED, i, None, "canary")
        inst = FAMILY.generate(SEED, i, None, level)
        assert inst == FAMILY.generate(SEED, i, None, level)
        assert ({k: v for k, v in inst.items() if k not in ("difficulty", "hidden", "groups")}
                == {k: v for k, v in base.items() if k not in ("difficulty", "hidden", "groups")})
        assert [g["rows"] for g in inst["groups"]] == [g["rows"] for g in base["groups"]]
        assert [g["saved"] for g in inst["groups"]] == [g["saved"] for g in base["groups"]]
        assert len(inst["hidden"]) == FAMILY.HIDDEN[level]
        assert (inst["target"] in inst["hidden"]) == (level != "canary")
        assert len(inst["stream"]) == 4 and sum(e["kind"] == "correction" for b in inst["stream"] for e in b) == 1
        assert all("reason" not in e for batch in inst["stream"] for e in batch)
        for group in inst["groups"]:
            assert group["saved"] in group["support"]
            assert len(group["support"]) == (2 if level == "canary" else 4)
            for row in group["rows"]:
                assert row["case"] >= 2 and (row["target"] - row["on_hand"] - row["in_transit"]) % row["case"] == 0


def test_desk_default_is_independent_of_buyer_default():
    matches = total = 0
    for i in range(1200):
        inst = FAMILY.generate(SEED, i, None, "standard")
        for group in inst["groups"]:
            matches += group["saved"] == inst["buyer"]
            total += 1
    assert abs(matches / total - 1 / 12) < 0.02


def test_every_convention_and_invalid_rows():
    for i in range(48):
        for group in FAMILY.generate(SEED, i, None, "heavy")["groups"]:
            for c in group["support"]:
                values = [r["price"] for r in group["rows"]]
                needs = [r["target"] - r["on_hand"] - r["in_transit"] for r in group["rows"]]
                catalog = {"kind": "catalog", "unit": c[1], "rows": FAMILY.encode(group["rows"], values, c, "catalog")}
                for d in FAMILY.CONVENTIONS:
                    order = {"kind": "order", "unit": d[1], "rows": FAMILY.encode(group["rows"], needs, d, "order")}
                    assert WORLD.join_result(group, catalog, order)[2] == (tuple(c) == d)
                order = {"kind": "order", "unit": c[1], "rows": FAMILY.encode(group["rows"], needs, c, "order")}
                for change in range(3):
                    altered = copy.deepcopy(order)
                    if change == 0:
                        altered["rows"].pop()
                    elif change == 1:
                        altered["rows"][0]["qty"] += 1
                    else:
                        altered["rows"].append({"key": "unknown", "qty": 1})
                    assert not WORLD.join_result(group, catalog, altered)[2]


@pytest.mark.parametrize("level", LEVELS)
def test_sweep(level):
    full = os.environ.get("FLOTILLA_INTERFACE_SWEEP") == "1"
    count, schedules = (60, 2) if full else (3, 2)
    turns = {}
    with tempfile.TemporaryDirectory(prefix="interface-sweep-") as temp:
        paths = build(temp, level, ALL_ARMS, range(count))
        for path in paths:
            task = load_task(path)
            for policy in (*EXACT, "lapse-30"):
                rewards = []
                for schedule in range(schedules):
                    run = SIM.simulate_built(path, policy, schedule)
                    check_run(task, run, policy)
                    rewards.append(run["reward"])
                    if policy == "careful":
                        for seat, calls in run["requests"].items():
                            turns.setdefault((task["condition"], seat), []).append(calls)
                assert len(set(rewards)) == 1
            if task["instance"] == 0:
                assert (SIM.simulate_built(path, "careful", 0)["events"]
                        == SIM.simulate_built(path, "careful", 0)["events"])
    worst = {key: max(values) for key, values in turns.items()}
    print(f"{level} careful requests per seat: {worst}")
    assert max(worst.values()) <= 20


def test_silent_differs_from_team_only_where_catalogs_are_sealed(tmp_path):
    for level in LEVELS:
        means = {}
        for arm in ("team", "silent", "team-parallel"):
            paths = build(tmp_path / level / arm, level, (arm,), range(12))
            means[arm] = sum(SIM.simulate_built(path, "careful", 0)["reward"] for path in paths) / len(paths)
        assert means["team"] == means["team-parallel"] == 1
        assert (means["silent"] == 1) == (level == "canary")


def run_with(monkeypatch, path, seat_play):
    original = SIM.POLICY.play

    def play(name, client, own, *args):
        if client.seat in seat_play:
            yield from seat_play[client.seat](client, own, lambda policy: original(policy, client, own, *args))
            return
        yield from original(name, client, own, *args)

    monkeypatch.setattr(SIM.POLICY, "play", play)
    return SIM.simulate_built(path, "careful", 0)


def test_probe_desk_ignores_ack_and_greeting(tmp_path, monkeypatch):
    (path,) = build(tmp_path, "heavy", ("probe-trap-key",))
    task = load_task(path)

    def chatty(client, own, original):
        assert (yield from client.post("/messages", {"to": "all", "text": "ok"}))[0] == 201
        for desk in sorted({g["desk"] for g in own["groups"]}):
            assert (yield from client.post("/messages", {"to": desk, "text": "Hello, ready when you are."}))[0] == 201
        yield from original("naive")

    run = run_with(monkeypatch, path, {"p1": chatty})
    target = run["score"]["groups"][task["params"]["target"]]
    assert run["reward"] == 0 and target["asked"] == 0 and not target["exposed"]
    assert all(e["data"]["text"] == "Noted." for e in run["events"] if e["kind"] == "send" and e["seat"] != "p1")


def test_direct_question_gets_only_that_group(tmp_path):
    (path,) = build(tmp_path, "heavy", ("probe-trap-unit",), policy="ask")
    task = load_task(path)
    run = SIM.simulate_built(path, None, 0)
    replies = [e["data"]["text"] for e in run["events"] if e["kind"] == "send" and e["seat"] != "p1"]
    assert run["reward"] == 1 and len(replies) == len(task["params"]["groups"])
    assert all(sum(g["name"] in text for g in task["params"]["groups"]) == 1 for text in replies)
    assert all(entry["asked"] == 1 and entry["exposed"] and not entry["looked"]
               for entry in run["score"]["groups"].values())


@pytest.mark.parametrize("template,to_all", [
    ("Which setting did you use for {name}?", False),
    ("How are the keys formatted for {name}?", False),
    ("Is {name} in case or each?", False),
    ("{name}: SITE/ITEM or ITEM/SITE? each or case?", False),
    ("What are your settings for {space}?", False),
    ("What setting did you choose for {name}?", False),
    ("Which settings will you use for {name}?", False),
    ("How will your catalog keys look for {name}?", False),
    ("{code}: what key format/unit?", False),
    ("What key form and unit for {name}?", True),
    ("Please let me know the configuration of {name}", False),
    ("What key form and unit for {name}?", False),
])
def test_probe_replies_before_prepare_after_timeout(tmp_path, monkeypatch, template, to_all):
    (path,) = build(tmp_path, "standard", ("probe-trap-key",))
    task = load_task(path)
    target = next(g for g in task["params"]["groups"] if g["name"] == task["params"]["target"])

    def slow_buyer(client, own, original):
        yield from client.sleep(70)
        missing = {"to": target["desk"], "text": "What setting did you choose?"}
        assert (yield from client.post("/messages", missing))[0] == 201
        _, reply = yield from client.get("/await?until=message")
        assert any(m["text"] == "Which group?" for m in reply["new_messages"])
        name = target["name"]
        text = template.format(name=name, space=name.replace("-", " "), code=name.split("-")[1])
        assert (yield from client.post("/messages", {"to": "all" if to_all else target["desk"], "text": text}))[0] == 201
        _, reply = yield from client.get("/await?until=message")
        assert not reply["prepared"]
        assert any(m["from"] == target["desk"] and FAMILY.announced(target, target["saved"], m["text"])
                   for m in reply["new_messages"])
        yield from original("careful")

    run = run_with(monkeypatch, path, {"p1": slow_buyer})
    assert run["reward"] == 1
    assert run["score"]["groups"][target["name"]]["asked"] >= 1
    assert any(e["kind"] == "question_unparsed" for e in run["events"])
    assert any(e["kind"] == "await" and e["data"]["reason"] == "timeout" for e in run["events"])


def test_paraphrased_announcements_count():
    group = {"name": "Supplier-B17", "rows": [{"site": "C4", "item": "Q7"}]}
    yes = ["Supplier-B17 uses key form ITEM:SITE, unit case.",
           "For supplier-b17 the key is item then site separated by a colon; prices per case.",
           "Supplier-B17: keys look like Q7:C4 (unit: case)",
           'Supplier-B17 -> {"form": "ITEM:SITE", "unit": "case"}']
    no = ["Supplier-B17 uses key form ITEM:SITE, unit each.", "Supplier-B18 uses key form ITEM:SITE, unit case.",
          "Supplier-B17 uses key form SITE:ITEM, unit case.", "Supplier-B17: unit case."]
    assert all(FAMILY.announced(group, ("ITEM:SITE", "case"), text) for text in yes)
    assert not any(FAMILY.announced(group, ("ITEM:SITE", "case"), text) for text in no)


def test_word_answer_read_but_not_used(tmp_path):
    paths = build(tmp_path, "heavy", ("team-parallel",), range(12))
    flagged = 0
    for path in paths:
        task = load_task(path)
        run = SIM.simulate_built(path, {"p1": "naive_read"}, 0)
        for group in task["params"]["groups"]:
            entry = run["score"]["groups"][group["name"]]
            assert entry["announced"] and entry["exposed"]
            if group["saved"] != task["params"]["buyer"]:
                assert entry["read_not_used"] and not entry["valid"]
                flagged += 1
    assert flagged > 20


def test_shared_focal_default_is_flagged_not_coordinated(tmp_path):
    paths = build(tmp_path, "standard", ("team-nosaved",), range(80), policy="focal")
    valid = coordinated = groups = 0
    for path in paths:
        run = SIM.simulate_built(path, None, 0)
        for entry in run["score"]["groups"].values():
            groups += 1
            valid += entry["valid"]
            coordinated += entry["valid_coordinated"]
            assert entry["blind_match"] == entry["valid"]
    assert coordinated == 0
    assert valid / groups <= FAMILY.INFORMED_GUESS_CEILING["standard"] + 0.08


def test_empty_repeat_and_early_uploads(tmp_path, monkeypatch):
    (path,) = build(tmp_path, "canary", ("team",))

    def probing(client, own, original):
        group = own["groups"][0]
        empty = {"kind": "order", "group": group["name"], "unit": "each", "rows": []}
        assert (yield from client.post("/publish", empty))[0] == 400
        early = {**empty, "rows": [{"key": "A1/J1", "qty": 1}]}
        assert (yield from client.post("/publish", early))[0] == 409
        assert (yield from client.get("/staging?group=nobody"))[0] == 400
        assert (yield from client.post("/prepare", {}))[0] == 409
        assert (yield from client.post("/done", {"extra": 1}))[0] == 400
        generator = original("careful")
        reply = None
        while True:
            try:
                request = generator.send(reply)
            except StopIteration:
                return
            reply = yield request
            if request.path == "/publish" and reply[0] == 201:
                assert (yield from client.post("/publish", request.body))[0] == 409

    run = run_with(monkeypatch, path, {"p1": probing})
    assert run["reward"] == 1


def test_solo_is_a_real_control(tmp_path):
    for level in LEVELS:
        paths = build(tmp_path / level, level, ("solo",), range(8))
        for path in paths:
            for policy in ("careful", "naive", "lone", "blind", "script-loop", "lapse-50"):
                run = SIM.simulate_built(path, policy, 0)
                assert run["reward"] == 1
                assert all("blind_match" not in entry for entry in run["score"]["groups"].values())


def test_own_omission_is_valid_and_flagged(tmp_path):
    (path,) = build(tmp_path, "standard", ("team",))
    run = SIM.simulate_built(path, {"p1": "nop"}, 0)
    assert run["invalid"] is None and run["reward"] == 0
    assert "p1" in run["score"]["work_missing"] and run["score"]["preparation_missing"]


def test_no_unprompted_desk_messages_in_probes(tmp_path):
    for path in build(tmp_path, "heavy", ("probe-control", "probe-trap-key", "probe-trap-unit")):
        run = SIM.simulate_built(path, "naive", 0)
        assert not any(e["kind"] == "send" for e in run["events"])


def test_arms_timeouts_and_qa(tmp_path):
    paths = [*build(tmp_path / "canary", "canary", ALL_ARMS), *build(tmp_path / "heavy", "heavy", ALL_ARMS)]
    for path in paths:
        toml = tomllib.loads((path / "task.toml").read_text())
        assert toml["metadata"]["play_timeout_sec"] == 1260 and toml["agent"]["timeout_sec"] == 1200
        text = (path / "instruction.md").read_text() if (path / "instruction.md").exists() else ""
        assert "adapter" not in text
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(paths) == 2 * len(ALL_ARMS)
    failures = [f for f in findings if f.level == "FAIL"]
    assert not failures, failures


def test_hint_and_default_pairs_change_one_sentence(tmp_path):
    for level in LEVELS:
        inst = FAMILY.generate(SEED, 3, None, level)
        team = FAMILY.extension_arm(inst, "team", 1000, 20)
        hint = FAMILY.extension_arm(inst, "hint", 1000, 20)
        nosaved = FAMILY.extension_arm(inst, "team-nosaved", 1000, 20)
        assert hint["instructions"]["p1"] == team["instructions"]["p1"] + "\n" + FAMILY.HINT + "\n"
        saved = FAMILY.saved_sentence(inst["buyer"])
        assert nosaved["instructions"]["p1"] == team["instructions"]["p1"].replace(saved, "", 1)
        assert {k: v for k, v in team["briefings"]["p1"].items() if k != "saved"} == nosaved["briefings"]["p1"]
        assert team["params"] == hint["params"] == nosaved["params"]
        for arm in ("probe-trap-key", "probe-trap-unit"):
            trap = FAMILY.extension_arm(inst, arm, 1000, 20)
            control = FAMILY.extension_arm(inst, "probe-control", 1000, 20)
            assert trap["instructions"]["p1"] == control["instructions"]["p1"]
            assert trap["briefings"]["p1"] == control["briefings"]["p1"]
