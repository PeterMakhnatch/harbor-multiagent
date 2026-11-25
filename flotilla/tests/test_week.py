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
    assert ("handbook" in reply) == (single or seat == "A")
    assert ("ratefiles" in reply) == (single or seat == "R")
    assert ("amendments" in reply) == (single or seat == "A")
    assert ("orders" in reply) == (single or seat in ("N1", "N2", "S"))
    assert ("notebook" in reply) == (seat == "N2")
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
        assert single or entry["desk"] == seat or (seat == "N2" and model.p["fullrecord"])
    for callback in reply["callbacks"]:
        assert set(callback) == {"carrier_ref", "status"}
        order = next(o for o in model.orders.values() if o["ref"] == callback["carrier_ref"])
        assert order["callback"] <= reply["round"]
        assert single or FAMILY.desk_for(order["hub"], reply["round"], model.p["shift"]) == seat
    assert ("prior_record" in reply) == (seat == "N2" and model.p["fullrecord"])


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
    active = {"A"} if single else set(inst["seats"]) - {"N2"}
    ready = set()
    closed = 0
    for e in events:
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
            if instance < 5:
                replay = SIM.simulate_built(by_arm["team"], "careful", 4)
                assert replay["events"] == careful["events"]
    assert len(instances) == 100
    summary = {f"{arm}/{policy}": {"n": len(rows), "reward": mean(row[0] for row in rows),
                                   "costs": {s: mean(row[1][s] for row in rows) for s in FAMILY.SEAMS}}
               for (arm, policy), rows in sorted(samples.items())}
    print(json.dumps({"difficulty": difficulty, "summary": summary, "max_http_calls": dict(max_requests)}, sort_keys=True))


def test_week_private_inputs_and_malformed_requests(monkeypatch):
    original = SIM.POLICY.play

    def probe(name, client, own, family, oracle, custom, variant, extension):
        if own["role"] == "A":
            for card in (None, [], "x" * 500, "\ud800"):
                status, check = yield from client.post("/card/check", {"card": card})
                assert status == 200 and check["valid"] is False
            status, _ = yield from client.post("/round", {"round": 9})
            assert status == 400
            status, _ = yield from client.post("/messages", {"to": "N2", "text": "test"})
            assert status == 403
        if own["role"] == "N1":
            status, _ = yield from client.post("/rates/check", {"rates": {}})
            assert status == 403
            status, _ = yield from client.post("/round", {"round": 1, "bookings": [None]})
            assert status == 400
        yield from original(name, client, own, family, oracle, custom, variant, extension)

    monkeypatch.setattr(SIM.POLICY, "play", probe)
    result = SIM.simulate("week", "team", 3, "careful", SEED, 2, difficulty="canary")
    assert result["reward"] == 1
    assert not any(e["kind"] == "extension_error" for e in result["events"])
