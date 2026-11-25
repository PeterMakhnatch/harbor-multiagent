"""Deterministic proof of file-only discovery, extension lifecycle and read-first reliance."""

import hashlib
import json
import os
import subprocess
import sys

import pytest
from conftest import FLOTILLA, load

SIM = load(FLOTILLA / "sim.py")
BUILD = SIM.BUILD
QA = load(FLOTILLA / "qa.py")
SEED = 7236499753218553


def test_extension_canary(tmp_path):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("talk",), tmp_path)
    for file in BUILD.canary_files(path, "kit_fixture"):
        assert BUILD.CANARY_LINE in file.read_bytes().splitlines()
    assert not QA.check([path])[0]
    task = QA.load_task(path)
    for seat in task.seats:
        assert all(BUILD.CANARY not in text for text in task.view(seat).values())
    toml = path / "task.toml"
    toml.write_bytes(toml.read_bytes().replace(BUILD.CANARY_LINE, b""))
    assert any(f.rule == "canary" and f.seat == "*" for f in QA.check([path])[0])
    instruction = path / "instructions" / "p2.md"
    instruction.write_text(instruction.read_text() + BUILD.CANARY)
    assert any(f.rule == "canary" and f.seat == "p2" for f in QA.check([path])[0])
    (legacy,) = BUILD.build("schedule", [0], 2, "easy", SEED, ("solo",), tmp_path)
    assert all(BUILD.CANARY.encode() not in file.read_bytes() for file in legacy.rglob("*") if file.is_file())
    assert not any(f.rule == "canary" for f in QA.check([legacy])[0])


def test_extension_versions(tmp_path):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("talk",), tmp_path)
    task = QA.load_task(path)
    versions = task.task_json["versions"]
    files = {
        "family": "environment/family.py", "extension": "environment/ext/kit_fixture.py",
        "policies": "solution/family_policy.py", "policy_runner": "solution/policy.py",
        "world": "environment/world.py",
    }
    for key, name in files.items():
        assert versions[key] == hashlib.sha256((path / name).read_bytes()).hexdigest()[:12]
    assert versions["family_version"] == 1
    assert versions["instructions"] == {
        seat: hashlib.sha256((path / "instructions" / f"{seat}.md").read_bytes()).hexdigest()[:12]
        for seat in task.seats
    }
    simulated = SIM.simulate_built(path, "careful", 0)
    assert simulated["score"]["versions"] == simulated["versions"] == versions
    file = path / "instructions" / "p2.md"
    file.write_text(file.read_text() + "\nModified.")
    assert any(f.rule == "versions" for f in QA.check([path])[0])
    file.unlink()
    assert any(f.rule == "versions" for f in QA.check([path])[0])


@pytest.mark.parametrize("sink", ["instruction", "briefing", "file"])
def test_extension_leak_values(tmp_path, sink):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("talk-trap",), tmp_path)
    task = QA.load_task(path)
    (value,) = task.family.leak_values(task.task_json)
    assert not QA.check([path])[0]
    if sink == "instruction":
        file = path / "instructions" / "p2.md"
        file.write_text(file.read_text() + f"\nLeak: {value}.")
    elif sink == "briefing":
        service = task.compose["services"]["p1"]
        briefing = json.loads(service["environment"]["SEAT_BRIEFING"])
        service["environment"]["SEAT_BRIEFING"] = json.dumps(briefing | {"extra": value})
        (task.env_dir / "docker-compose.yaml").write_text(QA.yaml.safe_dump(task.compose))
    else:
        player = task.env_dir / "player"
        (player / "extra.txt").write_text(value)
        (player / "Dockerfile").write_text(BUILD.player_dockerfile(["extra.txt"]))
    findings = [f for f in QA.check([path])[0] if f.rule == "answer-leak"]
    expected = {"instruction": {"p2"}, "briefing": {"p1"}, "file": set(task.seats)}[sink]
    assert {f.seat for f in findings} == expected
    assert all(value not in f.detail for f in findings)


def test_extension_missing_leak_check_warns(tmp_path, monkeypatch):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("talk",), tmp_path)
    task = QA.load_task(path)
    monkeypatch.delattr(task.family, "qa")
    assert not QA.check([path])[0]
    monkeypatch.delattr(task.family, "leak_values")
    findings, _ = QA.check([path])
    assert [(f.rule, f.level) for f in findings] == [("leak-check-missing", "WARN")]


def test_fixture_lapse_sweep(tmp_path):
    paths = BUILD.build("kit_fixture", list(range(64)), 2, "canary", SEED,
                        ("talk-control", "talk-trap"), tmp_path, policy="lapse-30")
    assert not QA.check(paths)[0]
    rewards = {"talk-control": [], "talk-trap": []}
    for path in paths:
        arm = QA.load_task(path).condition
        result = SIM.simulate_built(path, None, 0)
        assert result["policies"] == {"p1": "sender", "p2": "lapse-30"}
        assert result == SIM.simulate_built(path, "lapse-30", 0)
        assert result["reward"] == SIM.simulate_built(path, None, 1)["reward"]
        assert result["invalid"] is None
        rewards[arm].append(result["reward"])
    assert sum(rewards["talk-control"]) == 64
    assert 32 < sum(rewards["talk-trap"]) < 57
    for instance in range(4):
        for policy, expected in (("lapse-0", 1), ("lapse-100", 0), ("script-loop", 0)):
            result = SIM.simulate("kit_fixture", "talk-trap", instance, policy, SEED, 0)
            assert result["reward"] == expected
            if policy == "script-loop":
                calls = [event["data"]["path"] for event in result["events"]
                         if event["kind"] == "extension_call" and event["seat"] == "p2"]
                assert calls[-2:] == ["/advance", "/commit"]
        assert SIM.simulate("kit_fixture", "talk-control", instance, "script-loop", SEED, 0)["reward"] == 1
    with pytest.raises(ValueError, match="lapse policy"):
        BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("talk-trap",), tmp_path, policy="lapse-101")


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


def test_extension_faults_and_waits_in_sim(tmp_path, monkeypatch):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("team",), tmp_path)
    for seed in range(8):
        result = SIM.simulate_built(path, {"p2": "wait-message"}, seed)
        assert result["reward"] == 1
    result = SIM.simulate_built(path, {"p1": "nop", "p2": "exhaust"}, 0)
    assert result["reward"] is None and result["invalid"] == "fixture budget exhausted"
    assert result["requests"]["p2"] == 21
    assert sum(e["kind"] == "call_budget" for e in result["events"]) == 1
    result = SIM.simulate_built(path, {"p1": "nop", "p2": "fault"}, 0)
    assert result["reward"] is None and result["invalid"] == "extension_error"
    monkeypatch.setattr(SIM, "MAX_STEPS", 30)
    result = SIM.simulate_built(path, {"p1": "nop", "p2": "stall"}, 0)
    assert result["stalled"] and result["reward"] is None
    result = SIM.simulate("kit_fixture", "talk", 0, {"p1": "nop"}, SEED, 0)
    assert result["reward"] == 0
    assert any(e["kind"] == "await" and e["data"]["reason"] == "timeout" for e in result["events"])


def test_extension_validation_and_pairs(tmp_path):
    family = BUILD.load_family("kit_fixture")
    inst = family.generate(SEED, 0, 2, "canary")
    arm = family.extension_arm(inst, "staged", 4000, 200)
    for fields in ({"unknown_key": 1}, {"messaging": 1}, {"initial_active": ["missing"]},
                   {"max_calls": 0}, {"start_after": {"p1": ["p2"], "p2": ["p1"]}}):
        with pytest.raises(ValueError):
            BUILD.validate_arm(arm | fields, inst["seats"], "staged")
    paths = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("talk-trap", "hint"), tmp_path)
    assert not [f for f in QA.check(paths)[0] if f.level == "FAIL"]
    instruction = paths[1] / "instructions" / "p2.md"
    instruction.write_text(instruction.read_text().replace("Your part is", "Your other part is"))
    assert any(f.rule == "twin-mismatch" for f in QA.check(paths)[0])


def test_extension_fail_closed_over_http(tmp_path, start_world):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("team",), tmp_path / "tasks")
    task = QA.load_task(path)
    for fault in ("raise", "status", "list", "nan", "delay"):
        world = start_world("team", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"))
        world.ordered = True
        if fault == "delay":
            world.module.EXTENSION.message_delay = lambda message, recipient: -1
            status, _ = world.seat("p1", "POST", "/messages", {"to": "p2", "text": "handoff 123"})
        else:
            status, _ = world.seat("p1", "POST", "/fixture-fault", {"kind": fault})
        assert status == 500 and world.seat("p1", "GET", "/whoami")[1]["frozen"]
        assert SIM.SCORE.extension_score(task.task_json, world.events(), task.env_dir) == {"invalid": "extension_error"}
    world = start_world("team", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"))
    world.ordered = True
    assert world.seat("p2", "POST", "/messages", {"to": "p1", "text": "not allowed"})[0] == 403
    for _ in range(19):
        assert world.seat("p2", "GET", "/whoami")[0] == 200
    assert sum(e["kind"] == "call_budget" for e in world.events()) == 1
    verifier = load(task.env_dir / "score.py")
    verifier.WORLD, verifier.WORLD_DIR = world.url, task.env_dir
    verifier.VERIFIER, verifier.EVENTS = path / "verified", world.logs / "events.jsonl"
    with pytest.raises(RuntimeError, match="fixture budget exhausted"):
        verifier.main()
    assert not (verifier.VERIFIER / "reward.json").exists()
    assert json.loads((verifier.VERIFIER / "score.json").read_text())["error"] == "fixture budget exhausted"


def test_extension_play_clock_and_note_cap_over_http(tmp_path, start_world):
    (path,) = BUILD.build("kit_fixture", [0], 2, "canary", SEED, ("staged",), tmp_path)
    task = QA.load_task(path)
    world = start_world("staged", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"),
                        FLOTILLA_WAIT_SECONDS="0.05", FLOTILLA_PLAY_SECONDS="10")
    world.ordered = True
    assert world.seat("p2", "GET", "/await?until=active")[1]["reason"] == "timeout"
    assert world.seat("p1", "POST", "/handoff", {"note": "1" * 13})[0] == 413
    assert world.seat("p1", "POST", "/handoff", {"note": "123"})[0] == 200
    assert world.seat("p1", "POST", "/handoff", {"note": "456"})[0] == 409
    assert world.seat("p2", "GET", "/board")[1]["note"] == "123"
    assert world.events()[-1]["kind"] == "delivered"
    world = start_world("staged", ("p1", "p2"), FLOTILLA_TASK_JSON=str(task.env_dir / "task.json"),
                        FLOTILLA_WAIT_SECONDS="1", FLOTILLA_PLAY_SECONDS="0.1")
    assert world.seat("p2", "GET", "/await?until=active")[1]["reason"] == "frozen"
    assert any(e["kind"] == "play_timeout" for e in world.events())


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
    lapse_cases = {}
    for path in BUILD.build("kit_fixture", list(range(16)), 2, "canary", SEED, ("talk-trap",),
                            tmp_path / "lapse", policy="lapse-30"):
        reward = SIM.simulate_built(path, None, 0)["reward"]
        lapse_cases[reward] = path
        if set(lapse_cases) == {0, 1}:
            break
    assert set(lapse_cases) == {0, 1}
    cases.extend((path, reward) for reward, path in lapse_cases.items())
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
        summary = json.loads((verifier.VERIFIER / "score.json").read_text())
        assert summary["error"] is None
        if task.task_json.get("mode") == "extension":
            assert summary["versions"] == SIM.simulate_built(path, None, 0)["score"]["versions"]
        else:
            assert "versions" not in summary
