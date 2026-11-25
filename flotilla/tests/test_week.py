"""Known-policy proofs and visibility checks against the actual logical HTTP simulator."""

import json
import tempfile
from collections import defaultdict
from pathlib import Path
from statistics import mean

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
FAMILY = load(FLOTILLA / "seams" / "week.py")
SEED = 8734308029136849
SEAMS = ("digest", "interface", "commit", "handoff")
DOSES = ("canary", "standard")


def check_reply(sim, seat, request, result):
    if result is None or seat == "admin":
        return
    status, reply = result
    assert 200 <= status < 500
    wire = json.dumps(reply)
    assert all(word not in wire for word in FAMILY.ARM_WORDS)
    assert '"seed"' not in wire and '"condition"' not in wire
    if request.path != "/desk" or status != 200:
        return
    model = sim.world.EXTENSION
    single = model.single
    assert all(set(row) == {"order", "carrier", "departure"} for row in reply["board"])
    assert ("handbook" in reply) == (single or seat == "a")
    assert ("ratefiles" in reply) == (single or seat == "r")
    assert ("amendments" in reply) == (single or seat == "a")
    assert ("orders" in reply) == (single or seat in ("n1", "n2", "s"))
    assert ("notebook" in reply) == (seat == "n2")
    if "orders" not in reply:
        assert not any(order["ref"] in wire for order in model.orders.values())
        return
    for row in reply["orders"]:
        order = model.orders[row["id"]]
        assert set(row) == set(FAMILY.public_order(order))
        assert row["release"] <= reply["round"] < row["departure"]
        assert single or FAMILY.desk_for(row["hub"], reply["round"], model.p["shift"]) == seat
    for receipt in reply["receipts"]:
        entry = model.bookings[receipt["order"]]
        assert entry["ref"] == receipt["carrier_ref"]
        assert single or entry["desk"] == seat or (seat == "n2" and model.p["fullrecord"])
    for callback in reply["callbacks"]:
        assert set(callback) == {"carrier_ref", "status"}
        order = next(o for o in model.orders.values() if o["ref"] == callback["carrier_ref"])
        assert order["callback"] <= reply["round"]
        assert single or FAMILY.desk_for(order["hub"], reply["round"], model.p["shift"]) == seat
    assert ("prior_record" in reply) == (seat == "n2" and model.p["fullrecord"])


def check_run(result, inst, single=False, nop=False):
    assert result["invalid"] is None, result["invalid"]
    score = result["score"]
    assert score["V_ref"] == inst["v_ref"]
    assert score["V_ref"] - score["V"] == sum(score["costs"].values())
    assert score["V"] == score["revenue"] - score["fees"]
    assert result["reward"] == max(0, score["V"] / score["V_ref"])
    assert all(row["seam"] in FAMILY.SEAMS for row in score["ledger"])
    assert all(row["fee"] >= 0 and row["lost_revenue"] >= 0 for row in score["ledger"])
    assert all(sum(row["fee"] + row["lost_revenue"] for row in score["ledger"] if row["seam"] == s) == cost
               for s, cost in score["costs"].items())
    events = result["events"]
    assert result["state"]["frozen"]
    assert not any(e["kind"] in ("extension_error", "week_rejected", "week_budget") for e in events)
    active = {"a"} if single else set(inst["seats"]) - {"n2"}
    ready = set()
    closed = 0
    sends = {}
    for e in events:
        if e["kind"] == "send":
            sends[e["data"]["id"]] = e
        if e["kind"] == "deliver":
            for mid in e["data"]["ids"]:
                sent = sends[mid]
                assert e["t"] >= sent["t"] + 1
                assert sent["seat"] != e["seat"]
                assert sent["data"]["to"] in ("all", e["seat"])
                assert e["seat"] != "n2" or sent["t"] >= inst["shift"]
        if e["kind"] == "week_book":
            b = e["data"]
            assert b["desk"] in active and b["round"] == closed + 1
            assert e["seat"] not in ready
        if e["kind"] == "week_ready":
            assert e["seat"] in active and e["seat"] not in ready
            assert e["data"]["round"] == closed + 1
            ready.add(e["seat"])
        if e["kind"] == "week_closed":
            assert ready == active
            closed += 1
            assert e["data"]["round"] == closed
            ready.clear()
        if e["kind"] == "seat_transition":
            assert closed == inst["shift"]
            if e["data"]["status"] == "active":
                active.add(e["seat"])
            else:
                active.remove(e["seat"])
        if e["kind"] == "extension_call":
            assert 200 <= e["data"]["status"] < 500
    assert closed == (0 if nop else inst["rounds"])
    return score["costs"]


@pytest.mark.parametrize("difficulty", DOSES)
def test_week_sweep(difficulty, monkeypatch):
    original = SIM.Simulation.request

    def observed(self, seat, request):
        response = original(self, seat, request)
        check_reply(self, seat, request, response)
        return response

    monkeypatch.setattr(SIM.Simulation, "request", observed)
    samples = defaultdict(list)
    instances = set()
    max_requests = defaultdict(int)
    for instance in range(100):
        inst = FAMILY.generate(SEED, instance, None, difficulty)
        assert FAMILY.generate(SEED, instance, None, difficulty) == inst
        instances.add(json.dumps(inst, sort_keys=True))
        with tempfile.TemporaryDirectory(prefix="week-proof-") as root:
            paths = SIM.BUILD.build("week", [instance], len(inst["seats"]), difficulty, SEED,
                                    ("team", "solo", "team-fullrecord", "hint"), Path(root), policy="careful")
            by_arm = {json.loads((path / "environment" / "task.json").read_text())["condition"]: path for path in paths}

            def run(arm, policy, schedule, by_arm=by_arm, inst=inst):
                result = SIM.simulate_built(by_arm[arm], policy, schedule)
                costs = check_run(result, inst, arm == "solo", policy == "nop")
                samples[arm, policy].append((result["reward"], costs))
                return result

            for schedule in range(5):
                careful = run("team", "careful", schedule)
                assert careful["reward"] == 1 and not any(careful["score"]["costs"].values())
                for seat, n in careful["requests"].items():
                    max_requests[seat] = max(max_requests[seat], n)
            schedule = instance % 5
            expected = {}
            for seam in SEAMS:
                variant = run("team", f"naive-{seam}", schedule)
                costs = variant["score"]["costs"]
                assert costs[seam] > 0
                assert all(cost == 0 for s, cost in costs.items() if s != seam)
                expected[seam] = costs[seam]
                assert all(row["seam"] == seam for row in variant["score"]["ledger"])
            naive = run("team", "naive", schedule)
            assert naive["score"]["costs"] == (expected | {"own": 0})
            assert run("team", "nop", schedule)["reward"] == 0
            assert run("solo", "careful", schedule)["reward"] == 1
            assert run("team-fullrecord", "naive-handoff", schedule)["reward"] == 1
            assert run("hint", "careful", schedule)["reward"] == 1
            run("team", "random-valid", schedule)
            lapse = {}
            for percent in (10, 20, 30, 50):
                lapse[percent] = run("team", f"lapse-{percent}", schedule)
            if instance < 5:
                replay = SIM.simulate_built(by_arm["team"], "careful", 4)
                assert replay["events"] == careful["events"]
                twin = SIM.simulate_built(by_arm["team"], "lapse-30", (schedule + 1) % 5)
                assert twin["score"] == lapse[30]["score"]
    assert len(instances) == 100
    for percent in (10, 20, 30, 50):
        assert 0 < mean(row[0] for row in samples["team", f"lapse-{percent}"]) < 1
    summary = {f"{arm}/{policy}": {"n": len(rows), "reward": mean(row[0] for row in rows),
                                   "costs": {s: mean(row[1][s] for row in rows) for s in FAMILY.SEAMS}}
               for (arm, policy), rows in sorted(samples.items())}
    print(json.dumps({"difficulty": difficulty, "summary": summary, "max_http_calls": dict(max_requests)}, sort_keys=True))


def test_week_private_inputs_and_malformed_requests(monkeypatch):
    original = SIM.POLICY.play

    def probe(name, client, own, family, oracle, custom, variant, extension, seed=None, instance=None):
        if own["role"] == "a":
            for card in (None, [], "x" * 500, "\ud800"):
                status, check = yield from client.post("/card/check", {"card": card})
                assert status == 200 and check["valid"] is False
            status, _ = yield from client.post("/round", {"round": 9})
            assert status == 400
            status, _ = yield from client.post("/messages", {"to": "n2", "text": "test"})
            assert status == 403
        if own["role"] == "n1":
            status, _ = yield from client.post("/rates/check", {"rates": {}})
            assert status == 403
            status, _ = yield from client.post("/round", {"round": 1, "bookings": [None]})
            assert status == 400
        yield from original(name, client, own, family, oracle, custom, variant, extension, seed, instance)

    monkeypatch.setattr(SIM.POLICY, "play", probe)
    result = SIM.simulate("week", "team", 3, "careful", SEED, 2, difficulty="canary")
    assert result["reward"] == 1
    assert not any(e["kind"] == "extension_error" for e in result["events"])


def test_week_partial_barrier_has_no_reward(monkeypatch):
    def one_seat(name, client, own, *args, **kwargs):
        if own["role"] == "a":
            status, _ = yield from client.post("/round", {"round": 1})
            assert status == 200

    monkeypatch.setattr(SIM.POLICY, "play", one_seat)
    result = SIM.simulate("week", "team", 0, "careful", SEED, 0, difficulty="canary")
    assert result["reward"] is None
    assert result["invalid"] == "round barrier did not finish"
    assert not any(e["kind"] == "week_closed" for e in result["events"])


def test_week_ledger_identity_failure_has_no_reward(tmp_path):
    (path,) = SIM.BUILD.build("week", [0], 4, "canary", SEED, ("team",), tmp_path, policy="careful")
    result = SIM.simulate_built(path, "careful", 0)
    assert result["reward"] == 1
    env = path / "environment"
    task = json.loads((env / "task.json").read_text())
    task["params"]["v_ref"] += 1
    invalid = SIM.SCORE.extension_score(task, result["events"], env)
    assert invalid["invalid"] == "ledger identity failed"
    assert "reward" not in invalid
