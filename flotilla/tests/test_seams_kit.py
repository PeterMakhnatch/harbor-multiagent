"""Deterministic proof of file-only discovery, extension lifecycle and read-first reliance."""

import json
import os
import subprocess
import sys

from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
BUILD = SIM.BUILD
QA = load(FLOTILLA / "qa.py")
SEED = 7236499753218553


def test_fixture_sweep(tmp_path):
    paths = BUILD.build("kit_fixture", [0, 1], 2, "easy", SEED, ("solo", "talk", "talk-control", "talk-trap"),
                        tmp_path)
    findings, tasks = QA.check(paths)
    assert len(tasks) == 8
    assert not [f for f in findings if f.level == "FAIL"]
    for instance in range(8):
        for schedule in range(8):
            for arm in ("solo", "talk", "talk-control", "talk-trap"):
                result = SIM.simulate("kit_fixture", arm, instance, "careful", SEED, schedule)
                assert result["reward"] == 1
                assert result["state"]["frozen"]
                assert result == SIM.simulate("kit_fixture", arm, instance, "careful", SEED, schedule)
                events = result["events"]
                if arm != "solo":
                    transitions = [e for e in events if e["kind"] == "seat_transition"]
                    assert [(e["seat"], e["data"]["status"]) for e in transitions] == [
                        ("p1", "retired"), ("p2", "active")]
                    routes = [event["data"] for event in events if event["kind"] == "message_route"]
                    assert len(routes) == 2 and routes[1]["arrival"] is None
                    assert routes[0]["arrival"] in (1, 2)
                    delivered = [identifier for e in events if e["kind"] == "deliver" for identifier in e["data"]["ids"]]
                    assert routes[0]["id"] in delivered and routes[1]["id"] not in delivered
                    assert next(e["t"] for e in events if e["kind"] == "commit_value") >= routes[0]["arrival"]
            for arm, reward in (("talk-control", 1), ("talk-trap", 0)):
                naive = SIM.simulate("kit_fixture", arm, instance, "naive", SEED, schedule)
                assert naive["reward"] == reward and naive["policies"]["p1"] == "sender"
                assert SIM.simulate("kit_fixture", arm, instance, "nop", SEED, schedule)["reward"] == 0
            for seat in ("p1", "p2"):
                assert SIM.simulate("kit_fixture", "talk", instance, {seat: "nop"}, SEED, schedule)["reward"] == 0
    for instance in range(4):
        for schedule in range(4):
            assert SIM.simulate("kit_fixture", "talk", instance, "careful", SEED, schedule, difficulty="hard")[
                "reward"] == 1


def test_extended_fixture_protocols(tmp_path):
    arms = ("talk-trap", "hint", "team", "staged")
    for difficulty in ("canary", "standard", "heavy"):
        count = BUILD.default_seats(BUILD.load_family("kit_fixture"), difficulty)
        paths = BUILD.build("kit_fixture", [0], count, difficulty, SEED, arms, tmp_path / difficulty)
        assert not [f for f in QA.check(paths)[0] if f.level == "FAIL"]
        for path in paths:
            for seed in range(8):
                result = SIM.simulate_built(path, "careful", seed)
                assert result["reward"] == result["score"]["reward"] == 1
                assert result["invalid"] is None
                events = result["events"]
                if QA.load_task(path).condition == "team":
                    assert len([e for e in events if e["kind"] == "ready"]) == count
                    assert len([e for e in events if e["kind"] == "send" and e["seat"] == "board"]) == 3
                    assert any(e["kind"] == "deliver" and e["data"].get("via") == "push" for e in events)
                if QA.load_task(path).condition == "staged":
                    assert any(e["kind"] == "delivered" and e["data"]["ids"] == ["note-1"] for e in events)
    paths = BUILD.build("kit_fixture", [0], None, "heavy", SEED, ("team",), tmp_path / "auto")
    assert not [f for f in QA.check(paths)[0] if f.level == "FAIL"]
    assert len(QA.load_task(paths[0]).seats) == 3


def test_rf1v_read_first_reliance_sweep(tmp_path):
    for policy in ("naive", "careful"):
        paths = BUILD.build("probe", [0], 4, "easy", SEED, ("talk-quiet", "talk-false"), tmp_path / policy,
                            variant="verify-readfirst", read_first_subject=True, policy=policy)
        assert not [f for f in QA.check(paths)[0] if f.level == "FAIL"]
    for instance in range(20):
        for schedule in range(5):
            for arm in ("talk-quiet", "talk-false"):
                for policy in ("naive", "careful"):
                    result = SIM.simulate("probe.verify-readfirst", arm, instance, policy, SEED, schedule,
                                          read_first_subject=True)
                    false = policy == "naive" and arm == "talk-false"
                    assert result["reward"] == (0 if false else 1)
                    assert len(result["files_read"]["p1"]) == (0 if false else 3)
                    subject_events = [e for e in result["events"] if e["seat"] == "p1"]
                    assert subject_events[0]["kind"] == "deliver"
                    assert subject_events[-1]["kind"] == "submit"
                    if false:
                        assert len(subject_events[0]["data"]["ids"]) == 3
                    assert all(result["requests"][s] == 0 for s in ("p2", "p3", "p4"))
                    assert result == SIM.simulate("probe.verify-readfirst", arm, instance, policy, SEED, schedule,
                                                  read_first_subject=True)


def test_rehearsal_over_http_with_built_policy_and_verifier(tmp_path, start_world):
    cases = [(path, 1) for path in BUILD.build(
        "kit_fixture", [0], 2, "easy", SEED, ("solo", "talk", "talk-control", "talk-trap"), tmp_path / "fixture")]
    cases += [(path, 1) for path in BUILD.build(
        "kit_fixture", [0], 3, "heavy", SEED, ("team", "hint", "staged"), tmp_path / "extra")]
    for name in ("careful", "naive"):
        for path in BUILD.build("probe", [0], 4, "easy", SEED, ("talk-quiet", "talk-false"), tmp_path / name,
                                variant="verify-readfirst", read_first_subject=True, policy=name):
            expected = 0 if name == "naive" and QA.load_task(path).condition == "talk-false" else 1
            cases.append((path, expected))
    for path, expected in cases:
        task = QA.load_task(path)
        world = start_world(task.condition, tuple(task.seats), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"),
                            FLOTILLA_SUBMIT_GATE=task.env("main").get("FLOTILLA_SUBMIT_GATE", ""))
        processes = []
        try:
            for seat in task.seats:
                env = os.environ | task.env(seat) | {"WORLD_URL": world.url, "SEAT_TOKEN": world.tokens[seat]}
                processes.append(subprocess.Popen([sys.executable, str(path / "solution" / "policy.py")],
                                                  cwd=task.env_dir / "player", env=env))
                if task.condition == "staged" and seat == "p1":
                    assert processes[-1].wait(timeout=30) == 0
            for process in processes:
                assert process.wait(timeout=30) == 0
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        verifier = load(task.env_dir / "score.py")
        verifier.WORLD, verifier.WORLD_DIR = world.url, task.env_dir
        verifier.VERIFIER, verifier.EVENTS = path / "verified", world.logs / "events.jsonl"
        verifier.main()
        assert json.loads((verifier.VERIFIER / "reward.json").read_text()) == {"reward": expected}
        assert json.loads((verifier.VERIFIER / "score.json").read_text())["error"] is None
