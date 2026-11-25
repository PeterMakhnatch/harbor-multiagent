"""Handoff design sweeps, world receipts, and matched-arm invariants."""

import json
import os
import random
import statistics
from collections import Counter, defaultdict

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


def task_for(inst, arm, dose):
    rendering = FAMILY.extension_arm(inst, arm, 600, 32)
    return {"condition": arm, "params": rendering["params"], "difficulty": dose,
            "seats": sorted(rendering["briefings"]), "initial_active": rendering["initial_active"],
            "extension": "handoff", "messaging": False}


class FamilyHost:
    def __init__(self, task):
        self.family = FAMILY
        self.tick = 0
        self.frozen = False
        self.status = {s: "active" if s in task["initial_active"] else "waiting" for s in task["seats"]}
        self.events = []

    def record(self, kind, seat, data=None):
        self.events.append({"seq": len(self.events) + 1, "t": float(self.tick), "seat": seat,
                            "kind": kind, "data": data or {}})

    def advance(self):
        assert not self.frozen
        self.tick += 1
        self.record("tick", None, {"tick": self.tick})
        return self.tick

    def retire(self, seat):
        assert self.status[seat] == "active"
        self.status[seat] = "retired"
        self.record("seat_transition", seat, {"status": "retired", "tick": self.tick})

    def activate(self, seat):
        assert self.status[seat] == "waiting"
        self.status[seat] = "active"
        self.record("seat_transition", seat, {"status": "active", "tick": self.tick})

    def freeze(self):
        if not self.frozen:
            self.frozen = True
            self.record("freeze", None, {"reason": "extension ended play"})

    def await_reply(self, seat, timed_out=False):
        active = self.status[seat]
        reason = "frozen" if self.frozen else "timeout" if timed_out else active
        return 200, {"active": active, "tick": self.tick, "frozen": self.frozen, "reason": reason}


def direct_run(task, rendering, policy, schedule):
    """Exercise the actual family and policy generators with a lightweight logical scheduler.

    All seats start ready, including waiting seats. A random interleaving tests activation,
    while one active shift owns the desk. The built simulator checks transport parity separately.
    """
    host = FamilyHost(task)
    desk = EXT.create(task, host)
    rng = random.Random(schedule)
    requests = dict.fromkeys(task["seats"], 0)
    attempts = requests.copy()
    reads = {seat: [] for seat in task["seats"]}
    names = {seat: rendering["scripted_policies"].get(seat, policy) for seat in task["seats"]}

    def no_file(name):
        raise AssertionError(f"handoff policy read a file: {name}")

    agents = {seat: SIM.POLICY.play(name, SIM.POLICY.Client(seat, no_file), rendering["briefings"][seat],
                                   FAMILY, None, POLICIES, extension=True) for seat, name in names.items()}
    ready = dict.fromkeys(agents)
    parked = set()
    budget = rendering["max_calls"]
    for _ in range(2000):
        for seat in sorted(parked):
            if host.status[seat] != "waiting" or host.frozen:
                ready[seat] = host.await_reply(seat)
                parked.remove(seat)
        if not ready:
            if not parked:
                break
            ready.update({seat: host.await_reply(seat, timed_out=True) for seat in parked})
            parked.clear()
        seat = rng.choice(sorted(ready))
        try:
            request = agents[seat].send(ready.pop(seat))
        except StopIteration:
            continue
        assert isinstance(request, SIM.POLICY.Request)
        requests[seat] += 1
        active = host.status[seat] == "active" and not host.frozen
        if active:
            attempts[seat] += 1
            assert attempts[seat] <= budget
        if request.path == "/await?until=active":
            if host.status[seat] == "waiting" and not host.frozen:
                parked.add(seat)
                continue
            code, reply = host.await_reply(seat)
        elif active:
            code, reply, ids = desk.handle(seat, request.method, request.path, request.body or {}, {})
            assert 200 <= code < 500
            host.record("delivered", seat, {"path": request.path, "ids": ids})
        else:
            code, reply = 409, {"error": "not active"}
        ready[seat] = code, reply
        if active and attempts[seat] == budget and host.status[seat] == "active" and not host.frozen:
            host.record("call_budget", seat, {"limit": budget})
            desk.on_budget(seat)
    else:
        raise AssertionError("handoff simulation exceeded its logical step bound")
    host.freeze()
    score = EXT.score(task, host.events)
    return {"reward": score.get("reward"), "score": score, "invalid": score.get("invalid"),
            "stalled": False, "events": host.events, "requests": requests, "files_read": reads}


def family_events(events):
    kinds = {"printed", "dispatched", "settled", "ticket", "handover", "desk_view", "delivered", "refused",
             "handover_missing"}
    return [(e["kind"], e["seat"], e["data"]) for e in events if e["kind"] in kinds]


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
    prepared = {}
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
            prepared[data["view"]] = (event["seat"], data["path"], reply, shift)
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
        elif event["kind"] == "delivered":
            ids = data["ids"]
            view = next(item for item in ids if item.startswith("view-"))
            assert view in prepared and view not in delivered
            seat, path, reply, shift = prepared[view]
            assert (seat, path) == (event["seat"], data["path"])
            assert {f"result-{r['reference']}" for r in reply["inbox"]} <= set(ids)
            assert {f"binding-{b}-{r}" for b, r in reply.get("references", {}).items()} <= set(ids)
            if shift > 1:
                assert f"note-{shift - 1}" in ids
            delivered.add(view)
    assert set(prepared) == delivered
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
    order, batch_numbers, reference_numbers, outcomes, lags = [], [], [], [], []
    for instance in range(200):
        inst = FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        assert inst == FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        fingerprints.add(json.dumps(inst, sort_keys=True))
        for index, batch in enumerate(inst["base"]["batches"]):
            if batch["lag"] is None:
                continue
            order.append(index)
            batch_numbers.append(int(batch["batch"][1:]))
            reference_numbers.append(int(batch["reference"][1:]))
            outcomes.append(int(batch["status"] == "returned"))
            lags.append(batch["lag"])
        for first, second in FAMILY.TWINS:
            left, right = (FAMILY.extension_arm(inst, arm, 600, 32) for arm in (first, second))
            assert left["instructions"] == right["instructions"]
            assert left["briefings"] == right["briefings"]
            assert left["scripted_seats"] == right["scripted_seats"]
            assert len(left["briefings"]) - len(left["scripted_seats"]) == 1
            changes = [(a["batch"], key) for a, b in zip(left["params"]["batches"], right["params"]["batches"])
                       for key in a if a[key] != b[key]]
            target = "hunt_target" if "hunt" in first else "ref_target"
            assert changes == [(left["params"][target], "shift" if "hunt" in first else "lag")]
        assert 0 < FAMILY.chance_baseline(inst["base"]) < 1
    assert len(fingerprints) == 200
    for left, right in ((reference_numbers, batch_numbers), (reference_numbers, outcomes),
                        (reference_numbers, lags), (order, outcomes), (batch_numbers, outcomes), (lags, outcomes)):
        assert abs(statistics.correlation(left, right)) < 0.08


@pytest.mark.parametrize("dose", FAMILY.DOSES)
def test_handoff_sweep(dose, tmp_path):
    tally = defaultdict(list)
    blind_chance = []
    turns = 0
    max_turns = 0
    for instance in range(COUNT):
        inst = FAMILY.generate(SEED, instance, FAMILY.DOSES[dose], dose)
        paths = {}
        if instance == 0:
            paths = {json.loads((p / "environment/task.json").read_text())["condition"]: p
                     for p in SIM.BUILD.build("handoff", [instance], FAMILY.DOSES[dose], dose, SEED,
                                              FAMILY.ARMS, tmp_path)}
        for arm in FAMILY.ARMS:
            rendering = FAMILY.extension_arm(inst, arm, 600, 32)
            task = task_for(inst, arm, dose)
            policies = ("careful", "naive", "nop", "lone", "blind") if arm == "team" else ("careful", "naive", "nop")
            for policy in policies:
                for schedule in range(SCHEDULES):
                    run = direct_run(task, rendering, policy, schedule)
                    if instance == schedule == 0:
                        built = SIM.simulate_built(paths[arm], policy, schedule)
                        assert built["score"] == run["score"]
                        assert family_events(built["events"]) == family_events(run["events"])
                    assert run["invalid"] is None and not run["stalled"]
                    reward = run["reward"]
                    tally[arm, policy].append(reward)
                    score = EXT.score(task, run["events"])
                    assert score == EXT.score(task, run["events"])
                    assert reward == score["reward"]
                    assert score == run["score"]
                    assert 0 <= score["full_reward"] <= 1
                    assert not any(run["files_read"].values())
                    if policy == "blind":
                        blind_chance.append(blind_expected(task, run["events"]))
                    else:
                        assert reward == pytest.approx(expected(arm, policy, dose))
                    count = receipt_invariants(task, run["events"])
                    if policy == "careful":
                        turns = max(turns, count)
                        if arm != "solo":
                            max_turns = max(max_turns, max(run["requests"].values()))
                            assert max(run["requests"].values()) <= FAMILY.CALL_CAPS[task["params"]["cap"]]
                        assert score["first_loss_shift"] is None
                        assert score["full_reward"] == 1
                    if instance == schedule == 0 and policy == "careful":
                        repeat = direct_run(task, rendering, policy, schedule)
                        assert repeat["events"] == run["events"]
            if instance == 0 and arm == "team":
                for alternate in ("reverse", "colon", "slash", "lines", "ref-first"):
                    assert direct_run(task, rendering, alternate, 4)["reward"] == 1
    print(f"\n{dose}: {COUNT} instances x {SCHEDULES} schedules; max desk calls/shift {turns}; "
          f"max all world calls/seat {max_turns}")
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
    world.ordered = True
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


def test_handoff_settlement_and_record_scope(tmp_path, start_world):
    inst = FAMILY.generate(SEED, 0, 3, "canary")
    task = task_for(inst, "team-record", "canary")
    config = tmp_path / "task.json"
    config.write_text(json.dumps(task))
    world = start_world("team-record", tuple(task["seats"]), FLOTILLA_TASK_JSON=str(config))
    world.ordered = True
    get = lambda seat, path: world.seat(seat, "GET", path)
    post = lambda seat, path, body: world.seat(seat, "POST", path, body)
    code, desk = get("s1", "/desk")
    assert code == 200
    ticket = task["params"]["bad"]
    assert post("s1", "/ticket", {"lot": ticket})[0] == 200
    assert post("s1", "/ticket", {"lot": desk["lots"][0]})[0] == 409
    first, second = [b for b in task["params"]["batches"] if b["shift"] == 1 and b["lag"] == 0]
    for batch in (first, second):
        assert post("s1", "/print", {"batch": batch["batch"]})[0] == 200
    code, reply = post("s1", "/dispatch", {"batches": [first["batch"], second["batch"]]})
    assert code == 200 and reply["inbox"] == []
    first_item = {"reference": first["reference"], "batch": first["batch"], "action": EXT.action_for(first["status"])}
    second_item = {"reference": second["reference"], "batch": second["batch"], "action": EXT.action_for(second["status"])}
    code, reply = post("s1", "/settle", {"items": [first_item]})
    assert code == 409 and {r["reference"] for r in reply["inbox"]} == {first["reference"], second["reference"]}
    assert post("s1", "/settle", {"items": [first_item]})[0] == 200
    assert post("s1", "/settle", {"items": [first_item]})[0] == 409
    assert post("s1", "/settle", {"items": [second_item | {"batch": first["batch"]}]})[0] == 409
    wrong_action = "close" if second_item["action"] == "reissue" else "reissue"
    code, reply = post("s1", "/settle", {"items": [second_item | {"action": wrong_action}]})
    assert code == 200 and reply["recorded"] == 1 and "correct" not in reply
    note = "é" * task["params"]["cap"]
    assert post("s1", "/handover", {"note": note})[0] == 200
    for path, methods in EXT.Desk.routes.items():
        if "GET" in methods:
            assert get("s1", path)[0] == 409
        else:
            assert post("s1", path, {})[0] == 409
    assert get("s2", "/desk")[1]["note"] == note
    assert all(call["shift"] == 2 for call in get("s2", "/log")[1]["calls"])
    assert post("s2", "/close", {})[0] == 200
    assert {call["shift"] for call in get("s2", "/log")[1]["calls"]} == {1, 2}
    assert post("s2", "/handover", {"note": ""})[0] == 200
    score = EXT.score(task, world.events())
    assert score["ticket"] == 1 and score["ticket_early"] == 1 and score["reconciled"] == 1


def test_handoff_budget_and_invalid_evidence():
    run = SIM.simulate("handoff", "team", 0, {"s1": "exhaust"}, SEED, 4, difficulty="canary")
    assert run["invalid"] is None and run["reward"] < 1
    assert run["score"]["handover_missing"] == 1
    assert any(e["kind"] == "desk_view" and e["seat"] == "s2" and e["data"]["reply"]["note"] == ""
               for e in run["events"])
    stalled = SIM.simulate("handoff", "team", 0, {"s1": "stall"}, SEED, 4, difficulty="canary")
    assert stalled["invalid"] and stalled["stalled"] and stalled["reward"] is None
    inst = FAMILY.generate(SEED, 0, 3, "canary")
    task = task_for(inst, "team", "canary")
    complete = SIM.simulate("handoff", "team", 0, "careful", SEED, 4, difficulty="canary")["events"]
    missing = next(e for e in complete if e["kind"] == "delivered")
    assert EXT.score(task, [e for e in complete if e is not missing]) == {"invalid": "desk reply lacks a delivery receipt"}


def test_handoff_canary_qa(tmp_path):
    paths = SIM.BUILD.build("handoff", [0], 3, "canary", SEED, FAMILY.ARMS, tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == len(FAMILY.ARMS)
    assert not [f for f in findings if f.level == "FAIL"]
