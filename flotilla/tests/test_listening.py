"""Listening (docs/results/scripts/listening.py) on synthetic world event logs: unread counting (direct, broadcast, own
messages, deliver before and after the action, events at one time stamp ordered by seq), exposed and unexposed
actions, the world v11 `unread` cross-check and read gate, seeded logs against an independent count, and whole trial
folders (cross-play seat models, a silent arm reported n/a)."""

from __future__ import annotations

import json
import random

from conftest import FLOTILLA, load

LI = load(FLOTILLA / "docs" / "results" / "scripts" / "listening.py", "flotilla_test_listening")
SIMULATION_SEEDS = (2, 13, 20261123)


class Log:
    def __init__(self, seats=("p1", "p2"), condition="talk", **start):
        self.seats, self.events = list(seats), []
        self.add("start", None, {"condition": condition, "seats": self.seats, "world_version": 9, **start})
        self.add("open", None, {"round": 1, "stock": 100})

    def add(self, kind, seat, data, t=None):
        seq = len(self.events) + 1
        self.events.append({"kind": kind, "seat": seat, "data": data, "seq": seq, "t": float(seq) if t is None else t})
        return self

    def send(self, seat, to, t=None):
        return self.add("send", seat, {"id": f"m{len(self.events) + 1}", "to": to, "chars": 5}, t)

    def deliver(self, seat, ids, t=None, **extra):
        return self.add("deliver", seat, {"ids": list(ids), **extra}, t)

    def act(self, seat, kind="request", t=None, **extra):
        return self.add(kind, seat, {"round": 1, **({"amount": 10} if kind == "request" else {}), **extra}, t)

    def listen(self, shuffle=None):
        events = list(self.events)
        if shuffle is not None:
            shuffle.shuffle(events)
        return LI.listen(events, self.seats)


def test_direct_and_broadcast_messages_count_and_own_messages_do_not():
    log = Log(("p1", "p2", "p3"))
    log.send("p2", "p1").send("p3", "all").send("p1", "all").send("p2", "p3").act("p1")
    log.deliver("p1", ["m3", "m4"]).act("p1")
    p1 = log.listen()["p1"]
    assert (p1["actions"], p1["exposed"], p1["exposed_unread"]) == (2, 2, 1)
    assert (p1["addressed"], p1["seen"], p1["get_calls"]) == (2, 2, 1)
    p3 = log.listen()["p3"]
    assert (p3["addressed"], p3["seen"], p3["actions"]) == (2, 0, 0)


def test_a_deliver_counts_only_before_the_action_and_seq_orders_one_time_stamp():
    log = Log()
    log.send("p2", "p1", t=5.0).act("p1", t=5.0).deliver("p1", ["m3"], t=5.0).act("p1", "pass", t=5.0)
    p1 = log.listen(random.Random(4))["p1"]
    assert p1["by_kind"] == {"request": {"actions": 1, "exposed": 1, "exposed_unread": 1},
                             "pass": {"actions": 1, "exposed": 1, "exposed_unread": 0}}
    early = Log().act("p1", t=5.0).send("p2", "p1", t=5.0).listen()["p1"]
    assert (early["actions"], early["exposed"], early["addressed"]) == (1, 0, 1)


def test_an_action_before_any_message_to_the_seat_is_not_exposed():
    log = Log(("p1", "p2", "p3")).act("p1").send("p2", "p3").act("p1", "propose").send("p1", "all").act("p1", "accept")
    p1 = log.listen()["p1"]
    assert (p1["actions"], p1["exposed"], p1["exposed_unread"], p1["addressed"]) == (3, 0, 0, 0)


def test_first_read_is_the_first_get_after_the_round_opened_and_a_seat_may_never_read():
    log = Log().deliver("p1", [], t=1.5).send("p2", "p1").act("p1")
    found = log.listen()
    assert found["p1"]["first_read"] == -0.5 and found["p1"]["get_calls"] == 1
    assert found["p2"]["first_read"] is None and found["p2"]["get_calls"] == 0


def test_world_unread_is_used_and_every_difference_is_listed():
    log = Log().send("p2", "p1").act("p1", unread=1).deliver("p1", ["m3"]).act("p1", unread=2)
    p1 = log.listen()["p1"]
    assert (p1["world_unread"], p1["exposed_unread"]) == (2, 2)
    assert p1["mismatches"] == [{"seq": 6, "kind": "request", "world": 2, "log": 0}]


def test_read_gate_events_count_apart_from_actions_and_get_calls():
    log = Log(condition="gated", read_gate=True).send("p2", "p1")
    log.add("gated", "p1", {"round": 1, "path": "/harvest", "ids": ["m3"], "action": {"amount": 9}, "unread": 1})
    log.deliver("p1", ["m3"], via="gate").act("p1", unread=0)
    p1 = log.listen()["p1"]
    assert (p1["gated"], p1["gate_deliveries"], p1["get_calls"], p1["first_read"]) == (1, 1, 0, None)
    assert (p1["actions"], p1["exposed"], p1["exposed_unread"], p1["seen"], p1["mismatches"]) == (1, 1, 0, 1, [])
    assert LI.messaging(log.events[0], "gated") and LI.messaging(None, "talk-liar")
    assert not LI.messaging(None, "silent") and not LI.messaging(None, "propose")


def brute_unread(events, seat, action):
    """An independent count: messages to the seat sent before the action and in no earlier deliver of the seat."""
    sent = [e for e in events if e["kind"] == "send" and e["seq"] < action["seq"] and e["seat"] != seat
            and e["data"]["to"] in (seat, "all")]
    seen = {i for e in events if e["kind"] == "deliver" and e["seat"] == seat and e["seq"] < action["seq"]
            for i in e["data"]["ids"]}
    return len(sent), sum(e["data"]["id"] not in seen for e in sent)


def random_log(rng):
    log = Log(("p1", "p2", "p3")[:rng.randint(2, 3)])
    for _step in range(rng.randint(5, 60)):
        seat, roll = rng.choice(log.seats), rng.random()
        if roll < 0.35:
            log.send(seat, rng.choice([*log.seats, "all"]), t=float(rng.randint(1, 9)))
        elif roll < 0.6:
            mine = [e["data"]["id"] for e in log.events if e["kind"] == "send" and e["seat"] != seat
                    and e["data"]["to"] in (seat, "all")]
            log.deliver(seat, rng.sample(mine, rng.randint(0, len(mine))), t=float(rng.randint(1, 9)))
        else:
            log.act(seat, rng.choice(LI.ACTIONS), t=float(rng.randint(1, 9)))
    return log


def test_seeded_logs_match_an_independent_count():
    for seed in SIMULATION_SEEDS:
        rng = random.Random(seed)
        for _trial in range(40):
            log = random_log(rng)
            found = log.listen(rng)
            for seat in log.seats:
                actions = [e for e in log.events if e["seat"] == seat and e["kind"] in LI.ACTIONS]
                counts = [brute_unread(log.events, seat, action) for action in actions]
                assert found[seat]["actions"] == len(actions)
                assert found[seat]["exposed"] == sum(sent > 0 for sent, _unread in counts)
                assert found[seat]["exposed_unread"] == sum(sent > 0 and unread > 0 for sent, unread in counts)
                assert 0 <= found[seat]["seen"] <= found[seat]["addressed"]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data))


class Jobs:
    def __init__(self, root):
        self.root, self.jobs, self.tasks, self.entries = root, root / "jobs", root / "tasks", {}

    def trial(self, name, arm, instance, models, log, started="2026-11-24T00:00:00Z"):
        self.entries[f"t-{arm}{instance}"] = {"family": "split", "instance": instance, "condition": arm,
                                              "path": f"split/t-{arm}{instance}"}
        trial = self.jobs / name / f"t-{arm}{instance}__x"
        config = {"task": {"path": str(self.tasks / "split" / f"t-{arm}{instance}")},
                  "participants": [{"model_name": m, "seat": s} for s, m in zip(("p1", "p2"), models, strict=True)]}
        write(trial / "config.json", config)
        write(trial / "result.json", {"config": config, "started_at": started, "exception_info": None,
                                      "seat_results": [{"seat": s, "exception_info": None} for s in ("p1", "p2")]})
        write(trial / "verifier" / "score.json", {"error": None, "instance": instance, "condition": arm})
        write(trial / "verifier" / "world-state.json", {"game": {"log": []}, "messages": []})
        if log is not None:
            write(trial / "artifacts" / "logs" / "world" / "events.jsonl",
                  "".join(json.dumps(event) + "\n" for event in log.events))

    def main(self, capsys):
        write(self.tasks / "manifest.json", {"format": "flotilla-manifest", "tasks": self.entries})
        LI.main([str(self.jobs), "xx-*", "--tasks", str(self.tasks), "--json", str(self.root / "out.json")])
        return capsys.readouterr().out, json.loads((self.root / "out.json").read_text())


def test_trial_folders_cross_play_silent_na_duplicates_and_missing_logs(tmp_path, capsys):
    jobs = Jobs(tmp_path)
    jobs.trial("xx-000001", "talk", 0, ("m/a", "m/b"), Log().send("p2", "p1").act("p1").act("p2"))
    jobs.trial("xx-000002", "talk", 0, ("m/a", "m/b"), Log().act("p1"), started="2026-11-24T01:00:00Z")
    jobs.trial("xx-000003", "talk", 1, ("m/a", "m/a"), None)
    jobs.trial("xx-000004", "silent", 0, ("m/a", "m/a"), Log(condition="silent").act("p1").act("p2"))
    printed, saved = jobs.main(capsys)
    groups = {(g["model"], g["arm"]): g for g in saved["groups"]}
    assert set(groups) == {("m/a", "talk"), ("m/b", "talk"), ("m/a", "silent")}
    talk_a, talk_b, silent = groups[("m/a", "talk")], groups[("m/b", "talk")], groups[("m/a", "silent")]
    assert (talk_a["exposed_unread"], talk_a["exposed"], talk_a["share"], talk_a["blind"]) == (1, 1, 1.0, 1)
    assert (talk_b["exposed"], talk_b["share"], talk_b["never_read"], talk_b["seats"]) == (0, None, 1, 1)
    assert silent["actions"] == 2 and silent["share"] is None and silent["exposed"] is None
    assert saved["duplicates"] == ["xx-000002/t-talk0__x"]
    assert saved["invalid"] == ["xx-000003/t-talk1__x: no world event log"]
    row = next(line for line in printed.splitlines() if line.startswith("| m/a | split | silent |"))
    assert row.count("| n/a ") == 10
