"""The probe family's report timing for the 2x2 of instruction x reports-before-work: `--inbox` (the arm's posts in the
subject's brief, made by the world at its start and delivered `via: "brief"`, world version 19) and `--post-trigger
first_submit` (every arm's posts at the subject's first submission). Builds, plans, qa, the world and the verifier."""

from __future__ import annotations

import json
import tomllib

import pytest
from conftest import FLOTILLA, family, load
from test_build_tasks import by_task
from test_probe import SEATS, SEED, _finish, _seat, _solution, _verify

BUILD = load(FLOTILLA / "build_tasks.py")
PROBE = family("probe")
VERIFY = family("probe.verify")
WORKERS = list(SEATS[1:])


def _build(tmp_path, name: str, variant: str, arms: tuple[str, ...], **flags) -> dict:
    BUILD.build("probe", [0, 1], 4, "easy", SEED, arms, tmp_path / name, variant=variant, **flags)
    return by_task(tmp_path / name)


def _text(task) -> str:
    return (task / "instructions" / "p1.md").read_text()


def _json(task) -> dict:
    return json.loads((task / "environment" / "task.json").read_text())


# --- The inbox build ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["verify", "conformity"])
def test_inbox_puts_the_arms_posts_in_the_brief_and_changes_nothing_else(tmp_path, kind):
    pytest.importorskip("yaml")
    arms = family(f"probe.{kind}").ARMS
    plain = _build(tmp_path, "plain", kind, arms)
    told = _build(tmp_path, "told", f"{kind}-inbox", arms, inbox=True)
    stripped = set()
    for (label, instance, arm), task in told.items():
        before = plain[(f"probe.{kind}", instance, arm)]
        posts = _json(task)["scheduled_posts"]
        assert posts == _json(before)["scheduled_posts"]
        assert _text(task) == BUILD.inbox_brief(_text(before), posts)
        rest, block = BUILD.strip_inbox(_text(task))
        assert rest == _text(before) and _text(task).count(BUILD.INBOX_HEADING) == 1
        assert block == BUILD.inbox_block(posts) and block.startswith("## Team messages so far\n\n")
        lines = block.split("\n\n", 1)[1].splitlines()
        if arm == "talk-quiet":
            assert lines == ["None yet."]
        else:
            assert lines == [f"- {p['from']} (posted {p['before_s']:g} s before you started): {p['text']}" for p in posts]
            assert [p["before_s"] for p in posts] == sorted((p["before_s"] for p in posts), reverse=True)
        assert _text(task).index(BUILD.INBOX_HEADING) < _text(task).index("## The question")
        assert BUILD.read_first_sentence(kind) not in _text(task)
        stripped.add((instance, rest))
        for seat in WORKERS:
            assert (task / "instructions" / f"{seat}.md").read_text() == (before / "instructions" / f"{seat}.md").read_text()
        assert _json(task) == _json(before) | {"inbox": True, "variant": label[6:]}
        meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
        assert meta == tomllib.loads((before / "task.toml").read_text())["metadata"] | {"variant": label[6:], "inbox": True}
    assert len(stripped) == 2  # one subject text per instance once the block is out, in every arm
    manifest = json.loads((tmp_path / "told" / "manifest.json").read_text())["tasks"]
    assert all(entry["build"]["inbox"] is True and "post_trigger" not in entry["build"] for entry in manifest.values())


def test_inbox_and_post_trigger_are_refused_where_they_do_not_apply(tmp_path):
    cases = [({"inbox": True}, "audit", "conformity and verify, not probe audit"),
             ({"post_trigger": "first_submit"}, "audit", "conformity and verify"),
             ({"inbox": True, "read_first_subject": True}, "verify", "do not apply"),
             ({"inbox": True, "push": True}, "verify", "do not apply"),
             ({"inbox": True, "post_trigger": "first_submit"}, "verify", "do not apply"),
             ({"post_trigger": "first_request"}, "verify", "is one of")]
    for flags, variant, message in cases:
        arms = family(f"probe.{variant}").ARMS
        with pytest.raises(SystemExit, match=message):
            BUILD.build("probe", [0], 4, "easy", SEED, arms, tmp_path / "refused", variant=variant, **flags)
    with pytest.raises(SystemExit, match="conformity and verify"):
        BUILD.build("schedule", [0], 2, "easy", 1, ("talk",), tmp_path / "other", inbox=True)


# --- Posts at the first submission -------------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["verify", "conformity"])
def test_first_submit_keeps_each_arms_posts_and_order_and_dates_them_before_the_submission(variant):
    kind = family(f"probe.{variant}")
    for i in range(12):
        inst = kind.generate(SEED, i, 4, "easy")
        offsets: dict[int, set[tuple[float, ...]]] = {}
        for arm in (*kind.ARMS, *kind.EXTRA_ARMS):
            plans = PROBE.confederate_plans(inst, arm)
            late = PROBE.at_first_submit(inst["probe"], plans)
            assert plans == PROBE.confederate_plans(inst, arm)  # the arm's own plans are not changed
            posts, late_posts = BUILD.scheduled_posts(plans)[0], BUILD.scheduled_posts(late)[0]
            replies = [p for p in posts if BUILD.is_reply(p)]  # world version 20: they keep their own trigger
            assert [p for p in late_posts if BUILD.is_reply(p)] == replies
            posts, late_posts = ([p for p in found if not BUILD.is_reply(p)] for found in (posts, late_posts))
            assert [(p["from"], p["to"], p["text"]) for p in late_posts] == [(p["from"], p["to"], p["text"])
                                                                               for p in posts]
            assert all(p["trigger"] == "first_submit" and 1.0 <= p["before_s"] <= 3.0 for p in late_posts)
            assert BUILD.scheduled_posts(late)[1] == BUILD.scheduled_posts(plans)[1]
            if arm in PROBE.PUSH_ARMS:
                assert late == plans  # already at the first submission
            elif posts:
                offsets.setdefault(len(posts), set()).add(tuple(p["before_s"] for p in late_posts))
        assert offsets and all(len(found) == 1 for found in offsets.values())  # one draw per instance and post count
    with pytest.raises(ValueError, match="conformity and verify"):
        inst = family("probe.audit").generate(SEED, 0, 4, "easy")
        PROBE.at_first_submit(inst["probe"], PROBE.confederate_plans(inst, "talk-false"))


def test_post_trigger_builds_the_late_plans_with_read_first_and_records_the_flag(tmp_path):
    pytest.importorskip("yaml")
    arms = VERIFY.ARMS
    early = _build(tmp_path, "early", "verify-readfirst", arms, read_first_subject=True)
    late = _build(tmp_path, "late", "verify-readfirst-late", arms, read_first_subject=True,
                  post_trigger="first_submit")
    for (_label, instance, arm), task in late.items():
        before = early[("probe.verify-readfirst", instance, arm)]
        inst = VERIFY.generate(SEED, instance, 4, "easy")
        plans = PROBE.at_first_submit(inst["probe"], PROBE.confederate_plans(inst, arm))
        assert _json(task)["scheduled_posts"] == BUILD.scheduled_posts(plans)[0]
        assert _json(task) == _json(before) | {"post_trigger": "first_submit", "variant": "verify-readfirst-late",
                                               "scheduled_posts": BUILD.scheduled_posts(plans)[0],
                                               "probe": _json(before)["probe"] | {"plans": plans}}
        assert _text(task) == _text(before)
        meta = tomllib.loads((task / "task.toml").read_text())["metadata"]
        assert meta["post_trigger"] == "first_submit" and meta["read_first"] is True
    manifest = json.loads((tmp_path / "late" / "manifest.json").read_text())["tasks"]
    assert all(entry["build"]["post_trigger"] == "first_submit" for entry in manifest.values())


# --- qa ----------------------------------------------------------------------------------------------------------


def test_qa_passes_both_builds_and_catches_a_changed_block_a_stray_block_and_lost_triggers(tmp_path):
    pytest.importorskip("yaml")
    qa = load(FLOTILLA / "qa.py")
    inbox = _build(tmp_path, "sets", "verify-inbox", VERIFY.ARMS, inbox=True)
    _build(tmp_path, "sets", "verify-readfirst-late", VERIFY.ARMS, read_first_subject=True,
           post_trigger="first_submit")
    _build(tmp_path, "sets", "verify", VERIFY.ARMS)
    found, tasks = qa.check(qa.find_tasks([str(tmp_path / "sets" / "*")]))
    assert [f for f in found if f.level == "FAIL"] == [] and len(tasks) == 18
    by = {(t.label, t.instance, t.condition): t.path for t in tasks}
    false = by[("probe.verify-inbox", 0, "talk-false")]
    text = _text(false)
    (false / "instructions" / "p1.md").write_text(text.replace("(posted ", "(sent ", 1))
    quiet = by[("probe.verify", 1, "talk-quiet")]
    (quiet / "instructions" / "p1.md").write_text(BUILD.inbox_brief(_text(quiet), []))
    late = by[("probe.verify-readfirst-late", 1, "talk-false")] / "environment" / "task.json"
    late.write_text(json.dumps({key: value for key, value in _json(late.parent.parent).items() if key != "post_trigger"}))
    found, _tasks = qa.check(qa.find_tasks([str(tmp_path / "sets" / "*")]))
    failed = {(f.rule, f.task, f.seat) for f in found if f.level == "FAIL"}
    assert failed == {("inbox", false.name, "p1"), ("inbox", quiet.name, "main"), ("inbox", quiet.name, "p1"),
                      ("twin-mismatch", by[("probe.verify", 1, "talk-honest")].name, "p1"),
                      ("twin-mismatch", by[("probe.verify", 1, "talk-false")].name, "p1"),
                      ("confederate-plan", late.parent.parent.name, "main")}
    assert inbox  # the inbox twins still match: only the changed block is a finding


# --- The world and the verifier ----------------------------------------------------------------------------------


def _world(start_world, tmp_path, inst: dict, arm: str, plans: dict, **task):
    posts = BUILD.scheduled_posts(plans)[0]
    path = tmp_path / f"task-{arm}.json"
    path.write_text(json.dumps({"family": "probe", "condition": arm, "scheduled_posts": posts,
                                "scripted_seats": WORKERS} | task))
    return start_world(arm, SEATS, FLOTILLA_SUBMIT_GATE="1", FLOTILLA_TASK_JSON=str(path)), posts


def _task(inst: dict, arm: str, plans: dict, **flags) -> dict:
    return {"family": "probe", "variant": "verify", "instance": 0, "condition": arm, "seats": list(SEATS),
            "world_version": 19, "answer_key": inst["answer_key"], "decoy": inst["decoy"],
            "scripted_seats": inst["scripted_seats"], "probe": inst["probe"] | {"plans": plans}, "submit_gate": True,
            "scheduled_posts": BUILD.scheduled_posts(plans)[0]} | flags


@pytest.mark.parametrize("arm", ["talk-quiet", "talk-false"])
def test_the_world_makes_inbox_posts_at_its_start_as_delivered_and_the_oracle_is_never_gated(
        tmp_path, start_world, monkeypatch, arm):
    inst = VERIFY.generate(SEED, 0, 4, "easy")
    plans = PROBE.confederate_plans(inst, arm)
    world, posts = _world(start_world, tmp_path, inst, arm, plans, inbox=True)
    world.ordered = True
    events = world.events()
    assert events[0]["data"]["inbox"] is True and world.module.INBOX_SEATS == ["p1"]
    sends = [e for e in events if e["kind"] == "send"]
    delivered = [(e["seat"], e["data"]) for e in events if e["kind"] == "deliver"]
    assert [e["seat"] for e in sends] == [p["from"] for p in posts]
    assert delivered == ([("p1", {"ids": [e["data"]["id"] for e in sends], "via": "brief"})] if posts else [])
    status, reply = world.seat("p1", "GET", "/messages")
    assert status == 200 and [m["text"] for m in reply["messages"]] == [p["text"] for p in posts]
    assert len([e for e in world.events() if e["kind"] == "deliver"]) == len(delivered) + 1  # a read logs as before
    solution = _solution(tmp_path)
    running = [_seat(solution, world, "p1", inst["private"]["p1"])]
    running += [_seat(solution, world, seat, {"script": "confederate", "plan": []}) for seat in WORKERS]
    outputs = [_finish(process) for process in running]
    assert outputs[0] == [{"accepted": True}]
    reward, summary = _verify(tmp_path, world, monkeypatch, _task(inst, arm, plans, inbox=True))
    assert reward == 1.0 and summary["inbox"] is True and summary["trigger"] == ("brief" if posts else None)
    assert (summary["exposed"], summary["exposed_messages"], summary["n_gated"]) == (bool(posts), len(posts), 0)
    assert summary["first_attempt_gated"] is False
    assert not [e for e in world.events() if e["kind"] == "gated"]


def test_the_inbox_world_refuses_triggered_posts_and_a_wrong_flag(tmp_path, start_world):
    inst = VERIFY.generate(SEED, 0, 4, "easy")
    plans = PROBE.confederate_plans(inst, "talk-false")
    late = PROBE.at_first_submit(inst["probe"], plans)
    for chosen, task in ((late, {"inbox": True}), (plans, {"inbox": "yes"}), (plans, {"inbox": True,
                                                                                         "scripted_seats": SEATS})):
        with pytest.raises(SystemExit, match="inbox"):
            _world(start_world, tmp_path, inst, "talk-false", chosen, **task)


@pytest.mark.parametrize("arm", ["talk-quiet", "talk-false"])
def test_late_posts_wait_for_the_first_submission_whose_gate_shows_all_three(tmp_path, start_world, monkeypatch, arm):
    inst = VERIFY.generate(SEED, 0, 4, "easy")
    plans = PROBE.at_first_submit(inst["probe"], PROBE.confederate_plans(inst, arm))
    world, posts = _world(start_world, tmp_path, inst, arm, plans, post_trigger="first_submit")
    world.ordered = True
    status, reply = world.seat("p1", "GET", "/messages")
    assert (status, reply) == (200, {"messages": []})  # the read-first look finds nothing yet
    solution = _solution(tmp_path)
    running = [_seat(solution, world, "p1", inst["private"]["p1"])]
    running += [_seat(solution, world, seat, {"script": "confederate", "plan": []}) for seat in WORKERS]
    outputs = [_finish(process) for process in running]
    assert outputs[0] == [{"accepted": True}]
    gated = [e for e in world.events() if e["kind"] == "gated"]
    assert len(gated) == (1 if posts else 0)
    gate = [e["data"] for e in world.events() if e["kind"] == "deliver" and e["data"].get("via") == "gate"]
    assert [len(d["ids"]) for d in gate] == ([3] if posts else [])
    reward, summary = _verify(tmp_path, world, monkeypatch, _task(inst, arm, plans, post_trigger="first_submit"))
    assert reward == 1.0 and summary["trigger"] == ("first_submit" if posts else None) and summary["inbox"] is False
    assert (summary["exposed"], summary["first_attempt_gated"], summary["n_gated"]) == (
        bool(posts), bool(posts), len(gated))
