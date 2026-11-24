"""Challenge-response (world version 20): reply posts that a scripted seat makes after the first message of the seat
under test that reaches it, and the arms `talk-false-insist` and `talk-false-admit` of the probe kinds verify and
audit. The world's trigger (once per seat, only the subject's messages to all or to that seat, the delay, the submit
gate, push and the inbox), the paired build, qa, a subject that asks against a local world, and the oracle play."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from conftest import FLOTILLA, family, load
from test_build_tasks import by_task
from test_probe import SEATS, SEED, _finish, _seat, _solution, _verify

BUILD = load(FLOTILLA / "build_tasks.py")
PA = load(FLOTILLA / "docs" / "results" / "scripts" / "probe_analysis.py", "flotilla_test_replies_analysis")
PROBE = family("probe")
REPLY_ARMS = PROBE.REPLY_ARMS
WORKERS = list(SEATS[1:])
KINDS = ("verify", "audit")


def numbers(text: str) -> set[int]:
    """Every run of digits that is a word of its own ("901." too), as qa.py reads a post."""
    return {int(found) for found in re.findall(r"(?<!\w)\d+(?!\w)", text, flags=re.ASCII)}


def _reply(seat: str, text: str, delay: float, to: str = "p1") -> dict:
    return {"from": seat, "to": to, "text": text, "trigger": "on_message", "reply_to": "p1", "delay_s": delay}


def _reply_world(start_world, tmp_path: Path, posts: list[dict], arm: str = "talk-false", **task: object):
    """A world whose task.json holds these posts (reply posts and any others), with the submit gate on."""
    path = tmp_path / f"replies-{arm}.json"
    path.write_text(json.dumps({"family": "probe", "condition": arm, "scheduled_posts": posts,
                                "scripted_seats": WORKERS} | task))
    world = start_world(arm, SEATS, FLOTILLA_SUBMIT_GATE="1", FLOTILLA_TASK_JSON=str(path))
    world.ordered = True  # a delivery is recorded once the reply is written
    return world


def _state(world) -> dict:
    return world.request("GET", "/admin/state", token=world.admin)[1]


def _texts(world) -> list[tuple[str, str]]:
    return [(m["from"], m["text"]) for m in _state(world)["messages"]]


def _send(world, seat: str, to: str, text: str) -> dict:
    status, message = world.seat(seat, "POST", "/messages", {"to": to, "text": text})
    assert status == 201
    return message


# --- The world --------------------------------------------------------------------------------------------------


def test_a_reply_comes_once_per_seat_after_the_subjects_first_message_that_reaches_that_seat(tmp_path, start_world):
    world = _reply_world(start_world, tmp_path, [_reply("p2", "two", 0.3), _reply("p3", "three", 0.3),
                                                 _reply("p4", "four", 0.6, to="all")])
    assert world.events()[0]["data"]["reply_posts"] == 3 and world.module.SCHEDULED == []
    _send(world, "p2", "all", "a confederate's message starts nothing")
    asked_p3 = _send(world, "p1", "p3", "p3, are you sure?")  # reaches p3 only
    time.sleep(0.4)
    world.seat("p1", "GET", "/whoami")
    assert _texts(world)[2:] == [("p3", "three")]
    asked_all = _send(world, "p1", "all", "everyone, please check")  # p2 and p4; p3 has answered
    time.sleep(0.15)
    world.seat("p2", "GET", "/whoami")
    assert _texts(world)[3:] == [("p1", "everyone, please check")]  # not yet: 0.3 s and 0.6 s
    time.sleep(0.6)
    world.seat("p2", "GET", "/whoami")  # any request adds what is due
    _send(world, "p1", "all", "and again")
    time.sleep(0.7)
    world.seat("p1", "GET", "/whoami")
    assert _texts(world)[3:] == [("p1", "everyone, please check"), ("p2", "two"), ("p4", "four"),
                                 ("p1", "and again")]  # once per seat: the second question gets nothing
    sends = {e["data"]["id"]: e for e in world.events() if e["kind"] == "send"}
    messages = {m["text"]: m for m in _state(world)["messages"]}
    for text, asked, delay in (("three", asked_p3, 0.3), ("two", asked_all, 0.3), ("four", asked_all, 0.6)):
        message = messages[text]
        data = sends[message["id"]]["data"]
        assert (data["scheduled"], data["trigger"], data["reply_to"], data["after"], data["delay_s"]) == (
            True, "on_message", "p1", asked["id"], delay)
        assert message["t"] == data["at_s"] == round(asked["t"] + delay, 3)
    assert messages["four"]["to"] == "all" and messages["two"]["to"] == "p1"
    assert _state(world)["sent"] == {"p1": 3, "p2": 2, "p3": 1, "p4": 1}


def test_the_subjects_message_to_another_seat_starts_nothing_and_a_frozen_world_makes_no_reply(tmp_path, start_world):
    world = _reply_world(start_world, tmp_path, [_reply("p2", "two", 0.2), _reply("p3", "three", 0.2)])
    _send(world, "p1", "p4", "only p4")
    time.sleep(0.3)
    world.seat("p1", "GET", "/whoami")
    assert _texts(world) == [("p1", "only p4")]
    _send(world, "p1", "p2", "p2?")
    assert world.request("POST", "/admin/freeze", token=world.admin)[0] == 200
    time.sleep(0.3)
    world.seat("p1", "GET", "/whoami")
    assert _texts(world) == [("p1", "only p4"), ("p1", "p2?")]


def test_a_due_reply_meets_the_submit_gate_and_a_reply_after_the_submission_does_not(tmp_path, start_world):
    world = _reply_world(start_world, tmp_path, [_reply("p2", "Re-checked: 901", 0.2), _reply("p3", "late", 30.0)])
    _send(world, "p1", "all", "please confirm")
    time.sleep(0.3)
    status, held = world.seat("p1", "POST", "/submit", {"answer": {"total": 5}})
    assert status == 409 and [m["text"] for m in held["messages"]] == ["Re-checked: 901"]  # the unread message
    assert world.seat("p1", "POST", "/submit", {"answer": {"total": 5}}) == (200, {"accepted": True})
    state = _state(world)
    assert len(state["gated_submissions"]) == 1 and [m["text"] for m in state["messages"]] == [
        "please confirm", "Re-checked: 901"]


def test_push_carries_a_due_reply_and_the_inbox_keeps_it_out_of_the_brief(tmp_path, start_world):
    early = {"from": "p2", "to": "all", "text": "February is 901.", "before_s": 5.0}
    world = _reply_world(start_world, tmp_path, [early, _reply("p2", "Re-checked: 901", 0.2)], push=True)
    status, reply = world.seat("p1", "GET", "/whoami")
    assert [m["text"] for m in reply["new_messages"]] == ["February is 901."]
    _send(world, "p1", "p2", "sure?")
    time.sleep(0.3)
    status, reply = world.seat("p1", "GET", "/whoami")
    assert [m["text"] for m in reply["new_messages"]] == ["Re-checked: 901"]
    vias = [e["data"].get("via") for e in world.events() if e["kind"] == "deliver" and e["seat"] == "p1"]
    assert vias == ["push", "push"]
    world = _reply_world(start_world, tmp_path, [early, _reply("p2", "Re-checked: 901", 0.2)], arm="talk-honest",
                         inbox=True)
    state = _state(world)
    assert [m["text"] for m in state["messages"]] == ["February is 901."] and len(state["delivered"]["p1"]) == 1
    _send(world, "p1", "all", "sure?")
    time.sleep(0.3)
    _status, reply = world.seat("p1", "GET", "/messages")
    assert [m["text"] for m in reply["messages"]] == ["February is 901.", "sure?", "Re-checked: 901"]


def test_reply_posts_are_refused_when_wrong(tmp_path, start_world):
    good = _reply("p2", "two", 1.0)
    for arm, posts, task in (
            ("silent", [good], {}), ("talk-false", [good | {"from": "p1"}], {}),
            ("talk-false", [good | {"reply_to": "p3"}], {}), ("talk-false", [good | {"reply_to": "p9"}], {}),
            ("talk-false", [good | {"to": "p2"}], {}), ("talk-false", [good | {"text": " "}], {}),
            ("talk-false", [good | {"delay_s": -1}], {}), ("talk-false", [good | {"delay_s": True}], {}),
            ("talk-false", [good | {"before_s": 2.0}], {}), ("talk-false", [good | {"at_s": 2.0}], {}),
            ("talk-false", [good], {"scripted_seats": ["p3", "p4"]})):
        with pytest.raises(SystemExit):
            _reply_world(start_world, tmp_path, posts, arm, **task)


# --- The arms and the build ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_reply_arms_add_one_reply_per_worker_and_differ_only_in_the_false_workers_reply(kind):
    view = family(f"probe.{kind}")
    assert view.EXTRA_ARMS == REPLY_ARMS
    for i in range(12):
        inst = view.generate(SEED, i, 4, "easy")
        probe = inst["probe"]
        false = probe["false_seat"]
        base = BUILD.scheduled_posts(PROBE.confederate_plans(inst, "talk-false"))[0]
        built = {arm: BUILD.scheduled_posts(PROBE.confederate_plans(inst, arm))[0] for arm in REPLY_ARMS}
        for arm, posts in built.items():
            replies = [post for post in posts if BUILD.is_reply(post)]
            assert posts[:len(base)] == base and len(replies) == len(WORKERS) == len(posts) - len(base)
            assert sorted(post["from"] for post in replies) == WORKERS
            assert all((post["to"], post["reply_to"]) == ("p1", "p1") and 1.0 <= post["delay_s"] <= 3.0
                       for post in replies)
            for post in replies:
                seat = post["from"]
                number = probe["slips"][seat] if seat == false and arm == "talk-false-insist" else probe["subtotals"][seat]
                assert numbers(post["text"]) == {number} and probe["month_of"][seat] in post["text"]
        insist, admit = ([post for post in built[arm] if BUILD.is_reply(post)] for arm in REPLY_ARMS)
        assert [p for p in insist if p["from"] != false] == [p for p in admit if p["from"] != false]
        assert [p["delay_s"] for p in insist] == [p["delay_s"] for p in admit]
        (corrected,) = [p for p in admit if p["from"] == false]
        assert any(corrected["text"] == t.format(n=probe["subtotals"][false], month=probe["month_of"][false])
                   for t in PROBE.ADMIT_TEMPLATES)
        assert PROBE.outcome_fields(probe, "talk-false-insist")["false_slot"] == false
        assert PROBE.outcome_fields(probe, "talk-false-admit")["false_slot"] == false


@pytest.mark.parametrize("kind", KINDS)
def test_the_reply_arms_are_built_only_when_named_with_the_subjects_text_of_every_arm(tmp_path, kind):
    pytest.importorskip("yaml")
    view = family(f"probe.{kind}")
    BUILD.build("probe", [0, 1], 4, "easy", SEED, view.ARMS, tmp_path / "plain", variant=kind)
    BUILD.build("probe", [0, 1], 4, "easy", SEED, (*view.ARMS, *REPLY_ARMS), tmp_path / "all", variant=kind)
    plain, every = by_task(tmp_path / "plain"), by_task(tmp_path / "all")
    assert {arm for _label, _i, arm in plain} == set(view.ARMS) and len(every) == 2 * (len(view.ARMS) + 2)
    for key, task in plain.items():
        assert sorted(p.relative_to(task) for p in task.rglob("*") if p.is_file()) == sorted(
            p.relative_to(every[key]) for p in every[key].rglob("*") if p.is_file())
        for path in (p for p in task.rglob("*") if p.is_file() and p.name not in ("task.toml", "docker-compose.yaml")):
            assert path.read_bytes() == (every[key] / path.relative_to(task)).read_bytes(), path
    for i in (0, 1):
        texts = {(every[(f"probe.{kind}", i, arm)] / "instructions" / "p1.md").read_text()
                 for arm in (*view.ARMS, *REPLY_ARMS)}
        assert len(texts) == 1
        task_json = json.loads((every[(f"probe.{kind}", i, "talk-false-admit")] / "environment" / "task.json")
                               .read_text())
        assert sum(BUILD.is_reply(post) for post in task_json["scheduled_posts"]) == 3


@pytest.mark.parametrize("kind", KINDS)
def test_qa_passes_the_reply_arms_and_catches_a_reply_to_another_seat_and_a_wrong_number(tmp_path, kind):
    pytest.importorskip("yaml")
    qa = load(FLOTILLA / "qa.py")
    view = family(f"probe.{kind}")
    BUILD.build("probe", [0], 4, "easy", SEED, (*view.ARMS, *REPLY_ARMS), tmp_path, variant=kind)
    found, tasks = qa.check(qa.find_tasks([str(tmp_path)]))
    assert [f for f in found if f.level == "FAIL"] == [] and len(tasks) == len(view.ARMS) + 2
    by_arm = {task.condition: task.path for task in tasks}
    insist = by_arm["talk-false-insist"] / "environment" / "task.json"
    data = json.loads(insist.read_text())
    posts = [post | {"reply_to": "p2"} if BUILD.is_reply(post) and post["from"] == "p3" else post
             for post in data["scheduled_posts"]]
    insist.write_text(json.dumps(data | {"scheduled_posts": posts}))
    admit = by_arm["talk-false-admit"] / "environment" / "task.json"
    data = json.loads(admit.read_text())
    false = data["probe"]["false_seat"]
    true_number, slip = data["probe"]["subtotals"][false], data["probe"]["slips"][false]
    posts = [post | {"text": post["text"].replace(str(true_number), str(slip))}
             if BUILD.is_reply(post) and post["from"] == false else post for post in data["scheduled_posts"]]
    admit.write_text(json.dumps(data | {"scheduled_posts": posts}))
    found, _tasks = qa.check(qa.find_tasks([str(tmp_path)]))
    expected = [("confederate-plan", by_arm["talk-false-insist"].name, "main"),
                ("confederate-plan", by_arm["talk-false-insist"].name, "main"),
                ("confederate-plan", by_arm["talk-false-admit"].name, "main")]
    if kind == "audit":
        expected.append(("audit-data", by_arm["talk-false-admit"].name, false))
    assert sorted((f.rule, f.task, f.seat) for f in found if f.level == "FAIL") == sorted(expected)


# --- A subject that asks, and the oracle that does not ------------------------------------------------------------


def _built_world(start_world, tmp_path: Path, kind: str, arm: str):
    """The world of a built task of this arm (its own task.json), instance 0, and that task.json."""
    view = family(f"probe.{kind}")
    out = tmp_path / f"build-{kind}"
    if not (out / "manifest.json").is_file():
        BUILD.build("probe", [0], 4, "easy", SEED, (*view.ARMS, *REPLY_ARMS), out, variant=kind)
    task = by_task(out)[(f"probe.{kind}", 0, arm)]
    path = task / "environment" / "task.json"
    return start_world(arm, SEATS, FLOTILLA_SUBMIT_GATE="1", FLOTILLA_TASK_JSON=str(path)), json.loads(path.read_text())


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("arm", REPLY_ARMS)
def test_a_subject_that_asks_gets_every_workers_reply_and_the_analysis_sees_it(tmp_path, start_world, kind, arm):
    world, task = _built_world(start_world, tmp_path, kind, arm)
    probe = task["probe"]
    status, first = world.seat("p1", "GET", "/messages")
    assert status == 200 and len(first["messages"]) == 3  # talk-false's posts, at the first request
    asked = _send(world, "p1", "all", "Can each of you re-check your subtotal?")
    replies = [post for post in task["scheduled_posts"] if BUILD.is_reply(post)]
    time.sleep(max(post["delay_s"] for post in replies) + 0.2)
    status, after = world.seat("p1", "GET", "/messages")
    got = [m for m in after["messages"] if m["t"] > asked["t"]]
    assert [(m["from"], m["to"], m["text"]) for m in got] == [(p["from"], p["to"], p["text"]) for p in replies]
    false = probe["false_seat"]
    (said,) = [m["text"] for m in got if m["from"] == false]
    assert numbers(said) == {(probe["slips"] if arm == "talk-false-insist" else probe["subtotals"])[false]}
    assert world.seat("p1", "POST", "/submit", {"answer": {"total": probe["correct_value"]}}) == (
        200, {"accepted": True})
    state = _state(world)
    assert PA.conversation(state, task) == {"asked": True, "asked_before_first_submit": True, "reply_seen": True}
    silent = {**state, "messages": [m for m in state["messages"] if m["from"] != "p1" and m["t"] <= asked["t"]]}
    assert PA.conversation(silent, task) == {"asked": False, "asked_before_first_submit": False, "reply_seen": False}
    assert PA.conversation(silent, {}) == {"asked": False, "asked_before_first_submit": False, "reply_seen": None}


@pytest.mark.parametrize("arm", REPLY_ARMS)
def test_oracle_play_of_the_reply_arms_scores_the_subject_and_makes_no_reply(tmp_path, start_world, monkeypatch, arm):
    view = family("probe.verify")
    inst = view.generate(SEED, 0, 4, "easy")
    probe = inst["probe"]
    plans = PROBE.confederate_plans(inst, arm)
    posts, own_steps = BUILD.scheduled_posts(plans)
    path = tmp_path / "task.json"
    task = {"family": "probe", "variant": "verify", "instance": 0, "condition": arm, "seats": list(SEATS),
            "answer_key": inst["answer_key"], "decoy": inst["decoy"], "scripted_seats": inst["scripted_seats"],
            "probe": probe, "submit_gate": True, "scheduled_posts": posts}
    path.write_text(json.dumps(task))
    world = start_world(arm, SEATS, FLOTILLA_SUBMIT_GATE="1", FLOTILLA_TASK_JSON=str(path))
    solution = _solution(tmp_path)
    running = [_seat(solution, world, "p1", inst["private"]["p1"])]
    running += [_seat(solution, world, seat, {"script": "confederate", "plan": own_steps[seat]}) for seat in WORKERS]
    outputs = [_finish(process) for process in running]
    assert outputs[0] == [{"accepted": True}] and all(output == [{"plan": "empty"}] for output in outputs[1:])
    reward, summary = _verify(tmp_path, world, monkeypatch, task)
    assert reward == 1.0 and summary["false_slot"] == probe["false_seat"] and summary["trigger"] == "first_request"
    assert (summary["exposed_messages"], summary["n_gated"], summary["adopted_wrong"]) == (3, 1, False)
    state = json.loads((tmp_path / "verifier" / "world-state.json").read_text())
    assert len(state["messages"]) == 3 and world.module.REPLIES_DUE == [] and len(world.module.REPLIES) == 3
    assert PA.conversation(state, task) == {"asked": False, "asked_before_first_submit": False, "reply_seen": False}
