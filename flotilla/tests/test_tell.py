"""Replay the real referee with deterministic instance and schedule sweeps."""

import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
BUILD = SIM.BUILD
QA = load(FLOTILLA / "qa.py")
FAMILY = BUILD.load_family("tell")
SEED = 7863218242579537
LEVELS = FAMILY.LEVELS
ARMS = (*FAMILY.ARMS, *FAMILY.EXTRA_ARMS)


def test_generator_and_counterfactuals():
    for level in LEVELS:
        signatures = set()
        for instance in range(200):
            inst = FAMILY.generate(SEED, instance, 3, level)
            assert inst == FAMILY.generate(SEED, instance, 3, level)
            signatures.add(json.dumps(inst, sort_keys=True))
            a = FAMILY.extension_arm(inst, "probe-trap", 4000, 200)
            b = FAMILY.extension_arm(inst, "probe-control", 4000, 200)
            batch_a, batch_b = a["params"]["batch"], b["params"]["batch"]
            assert [i for i, (x, y) in enumerate(zip(batch_a, batch_b, strict=True)) if x != y] == [inst["hit"]]
            hit = inst["hit"]
            assert batch_a[hit]["lot"] != batch_b[hit]["lot"]
            batch_b[hit]["lot"] = batch_a[hit]["lot"]
            assert a == b
            assert len(inst["batch"]) == FAMILY.ITEMS
            assert all(len(rows) == 3 for rows in inst["boards"].values())
            assert sum(item["kind"] == "quarantine" for item in inst["batch"]) == 3
            assert sum(row["lot"] == batch_a[hit]["lot"] for rows in inst["boards"].values() for row in rows) == 1
            assert hit + 2 <= FAMILY.CARRIER - 1
        assert len(signatures) == 200


def invariants(result, params):
    assert result["invalid"] is None and 0 <= result["reward"] <= 1
    assert result["state"]["frozen"]
    assert not result["stalled"]
    actions = set()
    carrier = False
    deliveries = {}
    sends = {}
    views = set()
    records = set()
    retired = set()
    for event in result["events"]:
        kind, seat, data, tick = (event[key] for key in ("kind", "seat", "data", "t"))
        assert kind != "extension_error"
        if kind == "extension_call":
            assert data["status"] < 500
        elif kind == "delivered" and seat == "p1":
            views.update(data["ids"])
        elif kind == "record_item":
            assert seat == "p1" and data["item"] in views and data["item"] not in records
            records.add(data["item"])
        elif kind == "forward_document":
            assert seat == "p1" and data["item"] in views
            sends[data["message"]] = (tick, data["to"], data["item"])
        elif kind == "message_route" and data["arrival"] is not None:
            assert data["arrival"] in (tick, tick + 1)
        elif kind == "deliver":
            for identifier in data["ids"]:
                if identifier in sends:
                    sent, recipient, item = sends[identifier]
                    assert recipient == seat and tick >= sent + 1
                    deliveries.setdefault((seat, item), tick)
        elif kind == "desk_action":
            assert (seat, tick) not in actions and seat not in retired
            assert tick < FAMILY.END
            actions.add((seat, tick))
        elif kind == "sheet_change":
            assert not carrier and tick < FAMILY.CARRIER
            assert (data["notice"] in views if seat == "p1" else (seat, data["notice"]) in deliveries)
        elif kind == "carrier":
            assert not carrier and tick == FAMILY.CARRIER - 1
            carrier = True
        elif kind == "seat_transition" and data["status"] == "retired":
            retired.add(seat)
    assert result["score"]["own_ok"] == (len(records) == FAMILY.ITEMS)  # provided policies record correctly
    if result["reward"]:
        assert carrier
    assert not any(result["files_read"].values())
    if result["score"]["told_in_time"]:
        hit = params["batch"][params["hit"]]["item"]
        assert deliveries[params["receiver"], hit] < FAMILY.CARRIER


def sweep_cell(args):
    level, instance, full, cartesian = args
    rows = []
    with TemporaryDirectory(prefix="tell-test-") as directory:
        paths = BUILD.build("tell", [instance], 3, level, SEED, ARMS, Path(directory), policy="careful")
        for path in paths:
            task = QA.load_task(path)
            arm, params = task.condition, task.task_json["params"]
            policies = ["careful", "naive", "nop", "random", "lone", "after", "late", "spray"]
            policies.extend(f"lapse-{percent}" for percent in (10, 20, 30, 50))
            if arm in ("team", "probe-trap"):
                policies.extend(("fixed", "default"))
            if full and not cartesian and instance >= 5 and arm not in ("team", "probe-trap"):
                policies = ["careful", "naive"]
                if arm in ("solo", "probe-control"):
                    policies.extend(("nop", "after"))
                if arm == "auto":
                    policies.extend(("nop", "random"))
            for name in policies:
                schedules = (range(5) if name in ("careful", "naive") and (instance < 5 or cartesian or not full)
                             else (instance % 5,))
                for schedule in schedules:
                    result = SIM.simulate_built(path, name, schedule)
                    invariants(result, params)
                    reward = result["reward"]
                    if name in ("careful", "after"):
                        assert reward == 1, (level, instance, arm, name, schedule, result["score"])
                    elif name in ("naive", "late", "lone"):
                        expected = ((arm.startswith(("solo", "auto")) and not arm.startswith("solo-seam"))
                                    or arm.endswith("-control"))
                        assert reward == float(expected), (level, instance, arm, name, schedule, result["score"])
                    elif name == "nop":
                        assert reward == 0
                    if arm == "probe-trap" and name in ("careful", "naive", "late", "after"):
                        assert result["score"]["told_in_time"] == (name in ("careful", "after"))
                        assert result["score"]["receiver_applied"] == (name in ("careful", "after"))
                    if instance == 0 and name == "careful" and schedule == 0:
                        assert result == SIM.simulate_built(path, name, schedule)
                    baseline = None
                    if name == "random":
                        assert reward == FAMILY.chance_baseline(task.task_json, name)
                        baseline = FAMILY.chance_probability(task.task_json)
                    rows.append((level, arm, name, reward, baseline, result["requests"], result["score"]["told_in_time"]))
            if arm == "team" and (instance < 8 or not full):
                for seat in task.seats:
                    result = SIM.simulate_built(path, {seat: "nop"}, 0)
                    assert result["reward"] == 0 and result["invalid"] is None
    return rows


@pytest.mark.parametrize("level", LEVELS)
def test_policy_sweep(level):
    cartesian = os.environ.get("TELL_CARTESIAN_SWEEP") == "1"
    full = cartesian or os.environ.get("TELL_FULL_SWEEP") == "1"
    count = 200 if full else 2
    samples = [(level, instance, full, cartesian) for instance in range(count)]
    groups = defaultdict(list)
    if full:
        with ProcessPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(sweep_cell, samples))
    else:
        batches = [sweep_cell(sample) for sample in samples]
    max_requests = defaultdict(Counter)
    for batch in batches:
        for dose, arm, name, reward, baseline, requests, told in batch:
            groups[dose, arm, name].append((reward, baseline, told))
            if name == "careful":
                for seat, calls in requests.items():
                    max_requests[arm][seat] = max(max_requests[arm][seat], calls)
    for (dose, arm, name), values in sorted(groups.items()):
        mean = sum(reward for reward, _, _ in values) / len(values)
        if full and name == "random" and len(values) >= 200:
            expected = sum(baseline for _, baseline, _ in values) / len(values)
            assert abs(mean - expected) <= 0.10, (dose, arm, name, mean, expected)
        exposed = [told for _, _, told in values if told is not None]
        measure = f" told={sum(exposed) / len(exposed):.4f} n_exposed={len(exposed)}" if exposed else ""
        print(f"{dose:8} {arm:20} {name:10} n={len(values):4} reward={mean:.4f}{measure}")
    print(f"{level}: max careful calls {dict(max_requests)}")


@pytest.mark.parametrize("level", LEVELS)
def test_delayed_receiving_keeps_dispatch_awake(tmp_path, level):
    (path,) = BUILD.build("tell", [0], 3, level, SEED, ("probe-trap",), tmp_path)
    for schedule in range(5):
        result = SIM.simulate_built(path, {"p1": "delayed-careful"}, schedule)
        assert result["reward"] == 1 and result["score"]["told_in_time"]
        assert any(e["kind"] == "await" and e["data"]["reason"] == "timeout" for e in result["events"])


def test_built_static_views(tmp_path):
    paths = BUILD.build("tell", [0, 1], 3, "canary", SEED, ARMS, tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(ARMS) * 2
    assert not [finding for finding in findings if finding.level == "FAIL"]


def test_permission_errors_and_receipt_order(tmp_path, start_world):
    (path,) = BUILD.build("tell", [0], 3, "canary", SEED, ("team",), tmp_path)
    task = QA.load_task(path)
    world = start_world("team", tuple(task.seats), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"))
    world.ordered = True
    p1, p2, p3 = task.seats
    assert world.seat(p2, "GET", "/item")[0] == 403
    assert world.seat(p3, "GET", "/board")[0] == 403
    assert world.seat(p1, "GET", "/stock")[0] == 403
    assert world.seat(p1, "POST", "/sheet", {})[0] == 403
    assert world.seat(p1, "POST", "/forward", {"item": "made-up", "to": p2})[0] == 400
    assert world.seat(p2, "POST", "/record", {})[0] == 403
    assert world.seat(p1, "POST", "/messages", {"text": "hello", "to": p2})[0] == 403
    item = world.seat(p1, "GET", "/item")[1]["document"]
    assert world.events()[-1]["kind"] == "delivered"
    assert world.seat(p1, "POST", "/record", {})[0] == 409
    assert world.seat(p1, "POST", "/record", {"item": item["item"], "status": []})[0] == 400
    assert world.seat(p1, "POST", "/forward", {"item": item["item"], "to": p2})[0] == 200
    assert world.seat(p1, "POST", "/forward", {"item": item["item"], "to": p3})[0] == 409
    assert world.seat(p2, "POST", "/wait", {"until": "delivery"})[1]["round"] == 1
    status, reply = world.seat(p3, "POST", "/wait", {"until": "delivery"})
    assert status == 200 and reply["round"] == 2
    status, reply = world.seat(p2, "GET", "/await?until=message")
    assert status == 200 and json.loads(reply["new_messages"][0]["text"]) == item
    assert world.events()[-1]["kind"] == "deliver"
    assert world.seat(p1, "POST", "/forward", {"item": item["item"], "to": p2})[0] == 409
    assert world.seat(p2, "POST", "/sheet", {"order": "other", "lot": "code"})[0] == 403
    assert world.seat(p1, "POST", "/forward", {"item": item["item"], "to": p3})[0] == 200
    assert world.seat(p2, "POST", "/wait", {"until": "delivery"})[0] == 200
    assert world.seat(p1, "POST", "/forward", {"item": item["item"], "to": p2})[0] in (409, 429)
    assert world.seat(p1, "GET", "/whoami")[1]["tick"] == 2
    world.request("POST", "/admin/freeze", token=world.admin)
    score = SIM.SCORE.extension_score(task.task_json, world.events(), task.env_dir)
    assert score["reward"] == 0 and score["receiving_incomplete"] and score["stalled_seats"]


def test_guessed_edit_and_pull_path(tmp_path, start_world):
    (path,) = BUILD.build("tell", [11], 3, "heavy", SEED, ("team",), tmp_path)
    task = QA.load_task(path)
    world = start_world("team", tuple(task.seats), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"))
    world.ordered = True

    def call(seat, method, route, body=None):
        status, reply = world.seat(seat, method, route, body)
        assert status == 200, (seat, route, reply)
        return reply

    boards = call("p1", "GET", "/board")["boards"]
    docket = call("p1", "GET", "/docket")["docket"]
    stock = {}
    for desk in FAMILY.DESKS:
        sheet = call(desk, "GET", "/sheet")["sheets"][desk]
        stock[desk] = call(desk, "GET", "/stock")["stock"][desk]
        for row in sheet:
            other = next(lot for lot in stock[desk][row["order"]] if lot != row["lot"])
            assert world.seat(desk, "POST", "/sheet", {"order": row["order"], "lot": other})[0] == 400
        call(desk, "POST", "/wait", {"until": "delivery"})
    reply = call("p1", "GET", "/item")
    for _ in range(FAMILY.ITEMS):
        document = reply["document"]
        desk_row = next(((desk, row) for desk, rows in boards.items() for row in rows
                         if document.get("lot") == row["lot"]), None)
        if desk_row:
            desk, row = desk_row
            call("p1", "POST", "/forward", {"item": document["item"], "to": desk})
            inbox = call(desk, "GET", "/messages")["messages"]
            assert any(message["text"].startswith("{") and json.loads(message["text"]) == document
                       for message in inbox)
            other = next(lot for lot in stock[desk][row["order"]] if lot != row["lot"])
            call(desk, "POST", "/sheet", {"order": row["order"], "lot": other})
        round_no = call("p1", "GET", "/whoami")["tick"] + 1
        reply = call("p1", "POST", "/record", FAMILY.record_entry(document, docket))
        if round_no == FAMILY.CARRIER:
            for desk in FAMILY.DESKS:
                call(desk, "POST", "/wait", {})
        elif desk_row and call(desk, "GET", "/whoami")["tick"] + 1 < FAMILY.CARRIER:
            call(desk, "POST", "/wait", {"until": "delivery"})
    assert reply["document"] is None
    world.request("POST", "/admin/freeze", token=world.admin)
    events = world.events()
    expected = SIM.SCORE.extension_score(task.task_json, events, task.env_dir)
    assert expected["reward"] == 1 and expected["told_in_time"] and expected["receiver_applied"]
    assert expected == SIM.SCORE.extension_score(task.task_json, events, task.env_dir)
