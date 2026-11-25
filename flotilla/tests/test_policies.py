"""Named policy builds execute the policy in a real seat process, not a model."""

import json
import os
import subprocess
import sys

from conftest import FLOTILLA, load

BUILD = load(FLOTILLA / "build_tasks.py")
QA = load(FLOTILLA / "qa.py")
SEED = 6873629991007281


def test_policy_builds_use_same_instruction_and_scripted_roster(tmp_path):
    variants = {}
    for name in (None, "careful", "naive", "nop"):
        tasks = BUILD.build("ledger", [0], 2, "easy", SEED, ("solo", "talk"), tmp_path / str(name), policy=name)
        findings, _ = QA.check(tasks)
        assert not [f for f in findings if f.level == "FAIL"]
        variants[name] = [(p / "instructions" / "p1.md").read_text() for p in tasks]
        for path in tasks:
            solution = path / "solution"
            if name is None:
                assert not (solution / "policy.json").exists()
                assert (solution / "solve.sh").read_text() == BUILD.SOLVE_SH
            else:
                settings = json.loads((solution / "policy.json").read_text())
                assert set(settings["seats"].values()) == {name}
    assert all(instructions == variants[None] for instructions in variants.values())
    (probe,) = BUILD.build("probe", [0], 4, "easy", SEED, ("talk-false",), tmp_path / "probe", variant="verify",
                           policy="nop")
    settings = json.loads((probe / "solution" / "policy.json").read_text())
    assert settings["seats"] == {"p1": "nop", "p2": "careful", "p3": "careful", "p4": "careful"}
    assert not [f for f in QA.check([probe])[0] if f.level == "FAIL"]


def test_built_policy_process_uses_http_world(tmp_path, start_world):
    for name in ("careful", "naive", "nop"):
        (task,) = BUILD.build("ledger", [0], 2, "easy", SEED, ("solo",), tmp_path / name, policy=name)
        config = QA.load_task(task)
        world = start_world("solo", ("p1",))
        env = os.environ | config.env("p1") | {"WORLD_URL": world.url, "SEAT_TOKEN": world.tokens["p1"]}
        subprocess.run([sys.executable, str(task / "solution" / "policy.py")], env=env, check=True, timeout=10,
                       cwd=task / "environment" / "player")
        answers = world.module.STATE["submissions"]
        if name == "nop":
            assert not answers
        else:
            assert len(answers) == 1
            assert config.family.score(answers[0]["answer"], config.answer_key)["score"] == 1
