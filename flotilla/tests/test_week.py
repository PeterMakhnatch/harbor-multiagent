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
FLOORS = FAMILY.FLOORS


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
    assert all(set(row) == {"order", "carrier", "ref"} for row in reply.get("prior_record", []))


def check_run(result, inst, single=False, nop=False):
    assert result["invalid"] is None, result["invalid"]
    score = result["score"]
    assert score["V_ref"] == inst["v_ref"]
    assert score["V_ref"] - score["V"] == sum(score["costs"].values())
    assert score["V"] == score["revenue"] - score["fees"]
    assert result["reward"] == max(0, score["V"] / score["V_ref"])
    assert all(row["seam"] in FAMILY.SEAMS for row in score["ledger"])
    assert all(row["fee"] >= 0 and row["lost_revenue"] >= 0 for row in score["ledger"])
    for row in score["seams"].values():
        assert 0 <= row["failed"] + row["unobserved"] <= row["opportunities"]
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
        if e["kind"] in ("week_ready", "week_auto_ready"):
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
    max_turns = defaultdict(int)
    for instance in range(24):
        inst = FAMILY.generate(SEED, instance, None, difficulty)
        assert FAMILY.generate(SEED, instance, None, difficulty) == inst
        assert {r["carrier"] for r in inst["rules"]} == set(FAMILY.CARRIERS)
        assert all(o["eligible"] == list(FAMILY.CARRIERS) for o in inst["orders"])
        assert len({r["carrier"] for r in inst["rules"] if r["aside"]} |
                   {o["amendment"] for o in inst["orders"] if o["amendment"]}) > 1
        instances.add(json.dumps(inst, sort_keys=True))
        with tempfile.TemporaryDirectory(prefix="week-proof-") as root:
            paths = SIM.BUILD.build("week", [instance], len(inst["seats"]), difficulty, SEED,
                                    ("team", "solo", "team-fullrecord", "hint"), Path(root), policy="careful")
            by_arm = {json.loads((path / "environment" / "task.json").read_text())["condition"]: path for path in paths}

            def run(arm, policy, schedule, by_arm=by_arm, inst=inst):
                result = SIM.simulate_built(by_arm[arm], policy, schedule)
                costs = check_run(result, inst, arm == "solo", policy == "nop")
                samples[arm, policy].append((result["reward"], costs, result["score"]["seams"]))
                return result

            for schedule in range(3):
                careful = run("team", "careful", schedule)
                assert careful["reward"] == 1 and not any(careful["score"]["costs"].values())
                for seat, n in careful["requests"].items():
                    max_requests[seat] = max(max_requests[seat], n)
                for seat in inst["seats"]:
                    turns = sum(e["seat"] == seat and e["kind"] == "week_ready" for e in careful["events"]) + 1
                    assert turns == len(FAMILY.duty_rounds(inst, seat)) + 1
                    max_turns[seat] = max(max_turns[seat], turns)
                assert max_turns["r"] == 2
                if difficulty == "canary":
                    assert all(n <= 15 for n in max_turns.values())
            schedule = instance % 3
            expected = {}
            for seam in SEAMS:
                variant = run("team", f"naive-{seam}", schedule)
                costs = variant["score"]["costs"]
                assert all(cost == 0 for s, cost in costs.items() if s != seam)
                expected[seam] = costs[seam]
                assert all(row["seam"] == seam for row in variant["score"]["ledger"])
            naive = run("team", "naive", schedule)
            assert naive["score"]["costs"] == (expected | {"own": 0})
            assert run("team", "nop", schedule)["reward"] == 0
            solo = run("solo", "careful", schedule)
            assert solo["reward"] == 1
            max_turns["solo"] = max(max_turns["solo"], len(FAMILY.duty_rounds(inst, "all")) + 1)
            assert run("team-fullrecord", "naive-handoff", schedule)["reward"] == 1
            assert run("team-fullrecord", "naive-interface", schedule)["score"]["seams"]["interface"] == (
                samples["team", "naive-interface"][-1][2]["interface"])
            assert run("hint", "careful", schedule)["reward"] == 1
            for floor in FLOORS:
                run("team", floor, schedule)
            lapse = {}
            for percent in (10, 20, 30, 50):
                lapse[percent] = run("team", f"lapse-{percent}", schedule)
            if instance < 5:
                replay = SIM.simulate_built(by_arm["team"], "careful", 2)
                assert replay["events"] == careful["events"]
                twin = SIM.simulate_built(by_arm["team"], "lapse-30", (schedule + 1) % 3)
                assert twin["score"] == lapse[30]["score"]
    assert len(instances) == 24
    for seam in SEAMS:
        assert mean(row[1][seam] for row in samples["team", f"naive-{seam}"]) > 0
    for percent in (10, 20, 30, 50):
        assert 0 < mean(row[0] for row in samples["team", f"lapse-{percent}"]) < 1
    summary = {f"{arm}/{policy}": {"n": len(rows), "reward": mean(row[0] for row in rows),
                                   "costs": {s: mean(row[1][s] for row in rows) for s in FAMILY.SEAMS},
                                   "seams": {s: {key: sum(row[2][s][key] for row in rows)
                                                 for key in ("failed", "opportunities", "unobserved")} for s in SEAMS}}
               for (arm, policy), rows in sorted(samples.items())}
    chance = max(summary[f"team/{floor}"]["reward"] for floor in FLOORS)
    assert chance < 0.7
    print(json.dumps({"difficulty": difficulty, "summary": summary, "chance": chance,
                      "max_http_calls": dict(max_requests), "max_shell_turns": dict(max_turns)}, sort_keys=True))


def test_week_private_inputs_and_malformed_requests(monkeypatch):
    original = SIM.POLICY.play

    def probe(name, client, own, family, oracle, custom, variant, extension, seed=None, instance=None):
        if own["role"] == "a":
            for card in (None, [], "x" * 500, "\ud800", "HAL/Z1/cold>VIR\u2028", "HAL/Z1/cold>VIR\r"):
                status, check = yield from client.post("/card/check", {"card": card})
                assert status == 200 and check["valid"] is False
            status, _ = yield from client.post("/round", {"round": 9})
            assert status == 400
            status, _ = yield from client.post("/messages", {"to": "n2", "text": "test"})
            assert status == 403
        if own["role"] == "n1":
            status, _ = yield from client.post("/rates/check", {"rates": {}})
            assert status == 403
            status, _ = yield from client.post("/messages", {"to": "a", "text": "receipt"})
            assert status == 403
        yield from original(name, client, own, family, oracle, custom, variant, extension, seed, instance)

    monkeypatch.setattr(SIM.POLICY, "play", probe)
    result = SIM.simulate("week", "team", 3, "careful", SEED, 2, difficulty="canary")
    assert result["reward"] == 1
    assert not any(e["kind"] == "extension_error" for e in result["events"])


def test_week_partial_barrier_scores_omission(monkeypatch):
    def one_seat(name, client, own, *args, **kwargs):
        if own["role"] == "a":
            status, _ = yield from client.post("/round", {"round": 1})
            assert status == 200

    monkeypatch.setattr(SIM.POLICY, "play", one_seat)
    result = SIM.simulate("week", "team", 0, "careful", SEED, 0, difficulty="canary")
    assert result["reward"] == 0
    assert result["invalid"] is None
    assert "seat_omission" in result["score"]["flags"]
    assert "r" in result["score"]["stalled_seats"]
    assert not any(e["kind"] == "week_closed" for e in result["events"])


def test_week_budget_and_free_calls(monkeypatch):
    def spend(name, client, own, family, *args):
        if own["role"] != "r":
            return
        for _ in range(30):
            status, _ = yield from client.get("/whoami")
            assert status == 200
            status, reply = yield from client.get("/await?until=active")
            assert status == 200 and reply["active"] == "active"
        for _ in range(family.call_cap({"orders": [], "questions": []}, "r")):
            status, _ = yield from client.get("/desk")
            assert status == 200

    monkeypatch.setattr(SIM.POLICY, "play", spend)
    result = SIM.simulate("week", "team", 0, "careful", SEED, 0, difficulty="canary")
    assert result["reward"] == 0 and result["invalid"] is None
    assert "budget_exhausted" in result["score"]["flags"]
    assert result["score"]["stalled_seats"] == ["r"]
    assert result["requests"]["r"] == 84


def test_week_play_timeout_scores_omission(monkeypatch):
    def slow(name, client, own, *args):
        if own["role"] != "a":
            return
        yield from client.get("/desk")
        for _ in range(7):
            yield from client.sleep(300)
        yield from client.get("/desk")

    monkeypatch.setattr(SIM.POLICY, "play", slow)
    result = SIM.simulate("week", "team", 0, "careful", SEED, 0, difficulty="canary")
    assert result["invalid"] is None and result["reward"] == 0
    assert "seat_omission" in result["score"]["flags"]
    assert any(e["kind"] == "play_timeout" for e in result["events"])


def round_override(monkeypatch, transform):
    original = SIM.POLICY.play

    def override(name, client, own, *args, **kwargs):
        policy = original(name, client, own, *args, **kwargs)
        reply = None
        while True:
            try:
                request = policy.send(reply)
            except StopIteration:
                return
            if isinstance(request, SIM.POLICY.Request) and request.path == "/round":
                body = transform(own["role"], request.body)
                request = request._replace(body=body)
            reply = yield request
            if isinstance(request, SIM.POLICY.Request) and request.path == "/round" and any(
                    "error" in item for item in reply[1].get("results", [])):
                return

    monkeypatch.setattr(SIM.POLICY, "play", override)


def test_week_wrong_actions_cost_more_than_omission(monkeypatch):
    inst = FAMILY.generate(SEED, 0, None, "canary")
    crossing = [o for o in inst["orders"] if o["departure"] == inst["shift"]]
    target, wrong = crossing
    mode = "missing"

    def transform(role, body):
        for book in body.get("bookings", []):
            if book["order"] == target["id"]:
                book["rate_key"] = "" if mode == "missing" else "WRONG"
        if role == "n2":
            for rec in body.get("reconciles", [])[:]:
                if rec["carrier_ref"] == target["ref"]:
                    if mode == "missing":
                        body["reconciles"].remove(rec)
                    else:
                        rec["order"] = wrong["id"]
        return body

    round_override(monkeypatch, transform)
    missed = SIM.simulate("week", "team", 0, "careful", SEED, 0, difficulty="canary")["score"]
    mode = "wrong"
    guessed = SIM.simulate("week", "team", 0, "careful", SEED, 0, difficulty="canary")["score"]
    assert guessed["costs"]["interface"] == missed["costs"]["interface"] + 160
    assert guessed["costs"]["handoff"] == missed["costs"]["handoff"] + 300
    assert guessed["reward"] < missed["reward"]


def test_week_caps_and_eta_attribution(monkeypatch):
    instance = next(i for i in range(24) if any(
        o["amendment"] and any(q["order"] == o["id"] for q in inst["questions"])
        for inst in [FAMILY.generate(SEED, i, None, "canary")] for o in inst["orders"]))
    inst = FAMILY.generate(SEED, instance, None, "canary")
    order = next(o for o in inst["orders"] if o["amendment"])
    question = next(q for q in inst["questions"] if q["order"] == order["id"])
    wrong = next(c for c in FAMILY.CARRIERS if c != order["amendment"])
    card = FAMILY.encode_card(inst["rules"]) + f"\n{FAMILY.rule_key(order)}>{wrong}"
    assert FAMILY.parse_card(card, inst["card_cap"]) is not None

    def transform(role, body):
        if role == "a" and body["round"] == 1:
            body["card"] = card
        if role == "n1" and "notebook" in body:
            body["notebook"] += "\r"
        return body

    round_override(monkeypatch, transform)
    result = SIM.simulate("week", "team", instance, "careful", SEED, 0, difficulty="canary")
    assert result["invalid"] is None
    assert "notebook_missing" in result["score"]["flags"]
    assert any(row["subject"] == question["id"] and row["seam"] == "digest"
               for row in result["score"]["ledger"])
    assert result["score"]["seams"]["digest"]["failed"] == 1
    assert result["score"]["seams"]["commit"]["unobserved"] == 1


@pytest.mark.parametrize("difficulty", DOSES)
def test_week_artifact_caps_and_visible_briefings(difficulty):
    inst = FAMILY.generate(SEED, 4, None, difficulty)
    base = FAMILY.extension_arm(inst, "team", 4000, 200)
    hint = FAMILY.extension_arm(inst, "hint", 4000, 200)
    solo = FAMILY.extension_arm(inst, "solo", 4000, 200)
    assert base["play_timeout_sec"] >= 2 * base["agent_timeout_sec"] + 60
    for seat, text in base["instructions"].items():
        assert f"You have {base['max_calls'][seat]} calls" in text
        assert FAMILY.HINTS[seat] not in text
        assert hint["instructions"][seat] == text.rstrip() + "\n\n" + FAMILY.HINTS[seat] + "\n"
        assert "No deadlines depend on the clock" not in text
        assert "Read pushed messages on every reply" not in text
        assert "Forward it to the named desk" not in text
    assert "Dispatch desks do not have the rate files" not in solo["instructions"]["a"]
    assert "A forwarded message takes 1 round" not in solo["instructions"]["a"]
    receipts = [{"order": o["id"], "carrier_ref": o["ref"]} for o in inst["orders"]
                if o["hub"] == "North" and o["departure"] <= inst["shift"] < (o["callback"] or 0)]
    assert len(json.dumps(receipts, separators=(",", ":"))) > inst["note_cap"]
    assert len("\n".join(FAMILY.handbook(inst["rules"]))) > inst["card_cap"]
    result = SIM.simulate("week", "team", 4, "careful", SEED, 1, difficulty=difficulty)
    assert result["reward"] == 1
    note = next(e["data"]["note"] for e in result["events"] if e["kind"] == "week_notebook")
    assert len(note.encode("ascii")) <= inst["note_cap"]


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
