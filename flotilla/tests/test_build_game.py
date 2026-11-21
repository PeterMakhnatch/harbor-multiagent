"""build_tasks.py and qa.py for a game family (`commons`, docs/COMMONS.md): what the tasks hold and what seats read."""

from __future__ import annotations

import difflib
import json

import pytest
import yaml
from conftest import FLOTILLA, load

BUILD = load(FLOTILLA / "build_tasks.py", "flotilla_test_build_game")
QA = load(FLOTILLA / "qa.py", "flotilla_test_qa_game")
COMMONS = BUILD.load_family("commons")
ARMS = ("solo", "silent", "talk")


def build(tmp_path, n_seats=3, regime="a"):
    return BUILD.build("commons", [0, 1], n_seats, regime, 98765432101, ARMS, tmp_path / "out",
                       round_seconds=90.0, max_messages_per_round=2)


def task_json(task):
    return json.loads((task / "environment" / "task.json").read_text())


def test_a_game_build_writes_round_tasks_with_the_secret_only_in_the_world(tmp_path):
    tasks = build(tmp_path)
    assert len(tasks) == 6
    for task in tasks:
        data = task_json(task)
        assert data["mode"] == "rounds" and "answer_key" not in data and data["game"]["rounds"] in COMMONS.ROUNDS
        spec = yaml.safe_load((task / "environment" / "docker-compose.yaml").read_text())
        world = spec["services"]["main"]["environment"]
        assert world["FLOTILLA_GAME"] == "/world/family.py" and world["FLOTILLA_GAME_PARAMS"] == "/world/task.json"
        assert world["FLOTILLA_ROUND_SECONDS"] == "90.0" and world["FLOTILLA_MAX_MESSAGES_PER_ROUND"] == "2"
        assert world["FLOTILLA_PLAY_SECONDS"] == "1020.0"  # the world freezes itself at the agent timeout
        for seat in data["seats"]:
            briefing = json.loads(spec["services"][seat]["environment"]["SEAT_BRIEFING"])
            assert "rounds" not in briefing and briefing["players"] == data["seats"]
        assert (task / "solution" / "oracle.py").read_bytes() == (FLOTILLA / "games" / "oracle.py").read_bytes()
        assert (task / "environment" / "family.py").read_bytes() == (FLOTILLA / "games" / "commons.py").read_bytes()
        assert "timeout_sec = 1020.0" in (task / "task.toml").read_text()  # 10 rounds x 90 s + 120 s
    solo = next(task_json(task) for task in tasks if task_json(task)["condition"] == "solo")
    assert solo["seats"] == ["p1"] and solo["game"]["players"] == ["p1"]


def test_seats_read_the_public_rules_and_talk_adds_only_the_message_lines(tmp_path):
    texts = {}
    for task in build(tmp_path):
        data = task_json(task)
        for seat in data["seats"]:
            text = (task / "instructions" / f"{seat}.md").read_text()
            for rounds in COMMONS.ROUNDS:
                assert text == BUILD.game_instruction(COMMONS, {**data["game"], "rounds": rounds}, seat,
                                                      data["condition"], data["max_text"], 2)
            assert not [word for word in COMMONS.STEERING_WORDS if word in text.lower()], text
            texts[(data["instance"], data["condition"], seat)] = text
    for instance in (0, 1):
        silent, talk = (texts[(instance, arm, "p2")].splitlines() for arm in ("silent", "talk"))
        changed = [line for line in difflib.ndiff(silent, talk) if line[:2] in ("+ ", "- ")]
        assert changed and all(line.startswith("+ ") and ("messages" in line or line == "+ ") for line in changed)
        assert "You own all 3 boats" in texts[(instance, "solo", "p1")]


@pytest.mark.parametrize("bad", [{"conditions": ("talk-liar",)}, {"regime": "easy"}, {"scripted": True}])
def test_a_game_build_refuses_answer_family_options(tmp_path, bad):
    with pytest.raises(SystemExit):
        BUILD.build("commons", [0], 3, bad.get("regime", "a"), 98765432101, bad.get("conditions", ARMS),
                    tmp_path / "out", scripted=bad.get("scripted", False))


def test_qa_passes_a_game_build_and_fails_a_leaked_season_length(tmp_path):
    tasks = build(tmp_path, regime="b")
    findings, _ = QA.check(tasks)
    assert not [f for f in findings if f.level == "FAIL"], findings
    leak = next(task for task in tasks if task_json(task)["condition"] == "silent")
    with (leak / "instructions" / "p1.md").open("a") as text:
        text.write("The season has 8 rounds.\n")
    compose = leak / "environment" / "docker-compose.yaml"
    spec = yaml.safe_load(compose.read_text())
    briefing = json.loads(spec["services"]["p2"]["environment"]["SEAT_BRIEFING"])
    spec["services"]["p2"]["environment"]["SEAT_BRIEFING"] = json.dumps(briefing | {"rounds": 8})
    compose.write_text(yaml.safe_dump(spec))
    findings, _ = QA.check([leak])
    secret = sorted((f.rule, f.seat) for f in findings if f.rule == "game-secret")
    assert secret == [("game-secret", "p1"), ("game-secret", "p2")], findings
