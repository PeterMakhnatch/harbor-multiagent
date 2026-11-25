"""Deterministic proofs of the interface seam, including an opt-in full simulation sweep."""

import copy
import json
import os
import statistics
import tempfile
from fractions import Fraction
from pathlib import Path

import pytest
from conftest import FLOTILLA, load

FAMILY = load(FLOTILLA / "seams" / "interface.py")
WORLD = load(FLOTILLA / "world" / "ext" / "interface.py")
SIM = load(FLOTILLA / "sim.py")
BUILD = SIM.BUILD
QA = load(FLOTILLA / "qa.py")
SEED = 7236499753218553
LEVELS = tuple(FAMILY.DOSES)
TRAPS = {"solo-seam", "probe-trap-key", "probe-trap-unit", "probe-trap-key-hint"}


def instance(level, index):
    return FAMILY.generate(SEED, index, FAMILY.DOSES[level][0], level)


def render(inst, arm):
    return FAMILY.extension_arm(inst, arm, 1000, 20)


def file_for(group, convention, kind):
    values = [r["price"] if kind == "catalog" else r["target"] - r["on_hand"] - r["in_transit"]
              for r in group["rows"]]
    return {"kind": kind, "group": group["name"], "unit": convention[1],
            "rows": FAMILY.encode(group["rows"], values, convention, kind)}


@pytest.mark.parametrize("level", LEVELS)
def test_instances_and_twins(level):
    distinct = set()
    for i in range(200):
        inst = instance(level, i)
        assert inst == instance(level, i)
        distinct.add(json.dumps(inst, sort_keys=True))
        assert len(inst["groups"]) == FAMILY.DOSES[level][1]
        counts = {}
        complete = set()
        first_counts = {}
        corrected = []
        for batch in inst["stream"]:
            for e in batch:
                assert e["group"] not in complete
                if e["kind"] == "complete":
                    complete.add(e["group"])
                else:
                    counts[e["group"], e["site"], e["item"]] = e["on_hand"], e["in_transit"]
                    key = e["group"], e["site"], e["item"]
                    first_counts.setdefault(key, counts[key])
                    if e["kind"] == "correction":
                        corrected.append(key)
        control = render(inst, "probe-control")
        for arm, axis in (("probe-trap-key", 0), ("probe-trap-unit", 1)):
            trap = render(inst, arm)
            assert control["briefings"] == trap["briefings"]
            assert control["instructions"] == trap["instructions"]
            a, b = control["params"], trap["params"]
            assert {k: v for k, v in a.items() if k != "prebuilt"} == {k: v for k, v in b.items() if k != "prebuilt"}
            assert {g for g in a["prebuilt"] if a["prebuilt"][g] != b["prebuilt"][g]} == {inst["target"]}
            c, d = a["prebuilt"][inst["target"]], b["prebuilt"][inst["target"]]
            assert c[axis] != d[axis] and c[1 - axis] == d[1 - axis]
        for arm in ("team", "probe-trap-key"):
            base, hint = render(inst, arm), render(inst, f"{arm}-hint")
            assert hint["instructions"]["p1"].replace("\n" + FAMILY.HINT + "\n", "") == base["instructions"]["p1"]
            assert base["briefings"] == hint["briefings"] and base["params"] == hint["params"]
        team = render(inst, "team")
        own = team["briefings"]["p1"]
        assert all(set(g) == {"name", "desk", "rows"} for g in own["groups"])
        for g in inst["groups"]:
            assert g["saved"] in g["support"] and g["saved"] != inst["buyer"]
            assert len(g["support"]) == (2 if level == "canary" else 4)
            assert g["name"] in complete
            assert any(row["target"] > row["on_hand"] + row["in_transit"] for row in g["rows"])
            for row in g["rows"]:
                assert row["case"] >= 2
                assert (row["target"] - row["on_hand"] - row["in_transit"]) % row["case"] == 0
                assert counts[g["name"], row["site"], row["item"]] == (row["on_hand"], row["in_transit"])
                assert len({FAMILY.key_for(row, (form, "each")) for form in FAMILY.FORMS}) == 6
        assert len(corrected) == FAMILY.DOSES[level][-1]
        for name, site, item in corrected:
            group = next(g for g in inst["groups"] if g["name"] == name)
            altered = copy.deepcopy(group)
            row = next(r for r in altered["rows"] if (r["site"], r["item"]) == (site, item))
            row["on_hand"], row["in_transit"] = first_counts[name, site, item]
            correct = file_for(group, group["saved"], "catalog")
            stale = file_for(altered, group["saved"], "order")
            assert not WORLD.join_result(group, correct, stale)[2]
    assert len(distinct) == 200


def test_exact_blind_bounds():
    canary = [tuple(c for c in FAMILY.CONVENTIONS if c[0][4] == sep and c[1] == unit)
              for sep in "/:-" for unit in ("each", "case")]
    for supports, bound in ((canary, Fraction(1, 6)), (FAMILY.SUPPORTS, Fraction(53, 576))):
        maximum = Fraction(0)
        for buyer in FAMILY.CONVENTIONS:
            for guess in FAMILY.CONVENTIONS:
                support_prob = Fraction(sum(guess in group for group in supports), len(supports))
                assert support_prob == Fraction(len(supports[0]), 12)
                if buyer == guess:
                    continue
                prob = sum(Fraction(1, len(group) - int(buyer in group)) for group in supports if guess in group)
                maximum = max(maximum, prob / len(supports))
        assert maximum == bound


@pytest.mark.parametrize("level", LEVELS)
def test_every_convention_and_invalid_rows(level):
    for i in range(48):
        for group in instance(level, i)["groups"]:
            for c in group["support"]:
                catalog = file_for(group, c, "catalog")
                for d in FAMILY.CONVENTIONS:
                    order = file_for(group, d, "order")
                    valid = WORLD.join_result(group, catalog, order)[2]
                    assert valid == (tuple(c) == d)
                valid_order = file_for(group, c, "order")
                reverse = copy.deepcopy(valid_order)
                reverse["rows"].reverse()
                assert WORLD.join_result(group, catalog, reverse)[2]
                mutations = [copy.deepcopy(valid_order) for _ in range(4)]
                mutations[0]["rows"].pop()
                mutations[1]["rows"].append(copy.deepcopy(mutations[1]["rows"][0]))
                mutations[2]["rows"][0]["qty"] += 1
                mutations[3]["rows"].append({"key": "unknown", "qty": 1})
                assert all(not WORLD.join_result(group, catalog, changed)[2] for changed in mutations)
                wrong_unit = {**valid_order, "unit": "case" if c[1] == "each" else "each"}
                assert not WORLD.join_result(group, catalog, wrong_unit)[2]
            unsupported = next(c for c in FAMILY.CONVENTIONS if list(c) not in group["support"])
            assert not WORLD.join_result(group, file_for(group, unsupported, "catalog"),
                                         file_for(group, unsupported, "order"))[2]


def expected_reward(arm, policy, groups):
    if policy == "careful":
        return 1
    if policy == "nop":
        return 0
    if arm == "probe-control":
        return 1
    if arm in TRAPS:
        return (groups - 1) / groups
    return 0


def verify_run(task, run, arm, policy, replay):
    events = run["events"]
    score = run["score"]
    assert run["invalid"] is None and run["reward"] == score["reward"]
    if policy != "blind":
        assert run["reward"] == expected_reward(arm, policy, len(task["params"]["groups"]))
    if replay:
        assert WORLD.score(task, events) == score
    assert all(200 <= e["data"]["status"] < 500 for e in events if e["kind"] == "extension_call")
    assert all(not files for files in run["files_read"].values())
    published = set()
    frozen = False
    offered = set()
    for e in events:
        if e["kind"] == "freeze":
            frozen = True
        if e["kind"] in ("staging_notice", "staging_view", "work_events"):
            offered.add((e["seat"], e["data"]["item"]))
        if e["kind"] == "delivered":
            assert all((e["seat"], item) in offered for item in e["data"]["ids"])
        if e["kind"] == "file_publish":
            assert not frozen
            f = e["data"]["file"]
            key = f["kind"], f["group"]
            assert key not in published
            if f["kind"] == "order":
                assert ("catalog", f["group"]) in published
            published.add(key)
    if policy == "naive" and arm in TRAPS:
        assert score["target"] == 0
        assert score["groups"][task["params"]["target"]]["default_kept"]
    if policy == "careful" and arm != "solo":
        assert all(entry["looked"] and entry["exposed"] for entry in score["groups"].values())
    return score


@pytest.mark.parametrize("level", LEVELS)
def test_sweep(level):
    full = os.environ.get("FLOTILLA_INTERFACE_SWEEP") == "1"
    count, schedules = (200, 5) if full else (3, 2)
    rewards = {(arm, policy): [] for arm in FAMILY.ARMS for policy in ("careful", "naive", "nop", "blind")}
    turns = {}
    for i in range(count):
        with tempfile.TemporaryDirectory(prefix="interface-sweep-") as temp:
            paths = BUILD.build("interface", [i], None, level, SEED, FAMILY.ARMS, Path(temp),
                                max_text=1000, max_messages=21)
            for path in paths:
                task = json.loads((path / "environment" / "task.json").read_text())
                arm = task["condition"]
                for schedule in range(schedules):
                    for policy in ("careful", "naive", "nop", "blind"):
                        if full and schedule != 0 and policy in ("nop", "blind"):
                            continue
                        run = SIM.simulate_built(path, policy, schedule)
                        verify_run(task, run, arm, policy, i == 0)
                        rewards[arm, policy].append(run["reward"])
                        if policy == "careful":
                            for seat, calls in run["requests"].items():
                                turns[seat] = max(calls, turns.get(seat, 0))
                    if arm == "team":
                        assert SIM.simulate_built(path, {"p1": "lone"}, schedule)["reward"] == 0
                if i == 0:
                    assert SIM.simulate_built(path, "careful", 0)["events"] == SIM.simulate_built(path, "careful", 0)["events"]
        if full and (i + 1) % 25 == 0:
            print(f"{level}: {i + 1} instances × {schedules} schedules completed for all arms", flush=True)
    for (arm, policy), values in rewards.items():
        mean = statistics.mean(values)
        if full and policy == "blind":
            assert abs(mean - 1 / 12) < 0.075
        print(f"{level:8} {arm:20} {policy:7} n={len(values)} mean={mean:.4f}")
    print(f"{level} maximum careful requests: {turns}")
    assert turns["p1"] <= {"canary": 15, "standard": 35, "heavy": 100}[level]


@pytest.mark.parametrize("level", LEVELS)
def test_diagnostic_policies(level):
    for i in range(3):
        with tempfile.TemporaryDirectory(prefix="interface-diagnostics-") as temp:
            paths = BUILD.build("interface", [i], None, level, SEED,
                                ("team", "probe-trap-key", "probe-trap-unit"), Path(temp),
                                max_text=1000, max_messages=21)
            for path in paths:
                task = json.loads((path / "environment" / "task.json").read_text())
                arm = task["condition"]
                if arm == "team":
                    assert SIM.simulate_built(path, "alternate", 0)["reward"] == 1
                    if i == 0:
                        desk_nops = dict.fromkeys(task["seats"][1:], "nop")
                        assert SIM.simulate_built(path, desk_nops, 0)["reward"] == 0
                else:
                    asked = SIM.simulate_built(path, "ask", 0)
                    assert asked["reward"] == 1
                    assert all(entry["exposed"] and not entry["looked"] for entry in asked["score"]["groups"].values())
                    assert any(e["kind"] == "deliver" and e["seat"] == "p1" and e["data"].get("via") == "push"
                               for e in asked["events"])
                    careful = SIM.simulate_built(path, "careful", 0)
                    without_receipts = [e for e in careful["events"] if e["kind"] != "delivered"]
                    replay = WORLD.score(task, without_receipts)
                    assert replay["reward"] == 1
                    assert all(not entry["looked"] and not entry["exposed"] for entry in replay["groups"].values())
                    for name in ("naive_read", "windowed-8", "windowed-30"):
                        run = SIM.simulate_built(path, name, 0)
                        target = WORLD.score(task, run["events"])["groups"][task["params"]["target"]]
                        if name == "naive_read":
                            assert target["looked"] and target["read_not_used"] and not target["valid"]
                        else:
                            assert target["valid"] == (target["distance"] <= int(name.split("-")[1]))


def test_opaque_build_qa(tmp_path):
    paths = BUILD.build("interface", [0, 1], None, "canary", SEED, FAMILY.ARMS, tmp_path,
                        max_text=1000, max_messages=21)
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(paths) == 2 * len(FAMILY.ARMS)
    failures = [f for f in findings if f.level == "FAIL"]
    assert not failures, failures


def test_malformed_routes_and_message_limits(tmp_path, monkeypatch):
    (path,) = BUILD.build("interface", [0], None, "canary", SEED, ("probe-trap-key",), tmp_path,
                          max_text=2000, max_messages=21)
    original = SIM.POLICY.play

    def exercise(name, client, own, family, oracle, custom, variant, extension):
        if client.seat != "p1":
            yield from original(name, client, own, family, oracle, custom, variant, extension)
            return
        group = own["groups"][0]["name"]
        invalid = {"kind": "order", "unit": "each", "group": group, "rows": [{"key": "B4/Q7", "qty": True}]}
        assert (yield from client.post("/publish", invalid))[0] == 400
        assert (yield from client.get("/staging?group=not-a-group"))[0] == 400
        assert (yield from client.post("/done", {"extra": 1}))[0] == 400
        assert (yield from client.post("/publish", {"kind": "catalog", "group": group, "unit": "case", "rows": []}))[0] == 403
        assert (yield from client.post("/messages", {"to": "all", "text": "a" * 1001}))[0] == 413
        for _ in range(20):
            status, reply = yield from client.post("/messages", {"to": "all", "text": "What convention?"})
            assert status == 201
            assert any(group + ":" in msg["text"] for msg in reply["new_messages"])
        assert (yield from client.post("/messages", {"to": "all", "text": "Another question"}))[0] == 429
        generator = original(name, client, own, family, oracle, custom, variant, extension)
        response = None
        for _ in range(100):
            try:
                request = generator.send(response)
            except StopIteration:
                assert (yield from client.post("/next", {}))[0] == 409
                return
            response = yield request
            if isinstance(request, SIM.POLICY.Request) and request.path == "/publish" and response[0] == 201:
                assert (yield from client.post("/publish", request.body))[0] == 409
        raise ValueError("policy did not terminate")

    monkeypatch.setattr(SIM.POLICY, "play", exercise)
    run = SIM.simulate_built(path, "careful", 0)
    assert run["reward"] == 1 and run["invalid"] is None
    assert sum(e["kind"] == "file_publish" for e in run["events"]) == 2
