"""The verifier (world/score.py) and the reference oracle (world/oracle.py), against a real world."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FAMILIES, FLOTILLA, family, load

ORACLE = load(FLOTILLA / "world" / "oracle.py")


def _verifier(tmp_path: Path, world, monkeypatch, task: dict, family_file: Path):
    """score.py pointed at a test world, a temporary /world and a temporary /logs/verifier."""
    world_dir = tmp_path / "world-dir"
    world_dir.mkdir(exist_ok=True)
    (world_dir / "task.json").write_text(json.dumps(task))
    shutil.copy(family_file, world_dir / "family.py")
    score = load(FLOTILLA / "world" / "score.py")
    monkeypatch.setattr(score, "WORLD", world.url if world else "http://127.0.0.1:9")
    monkeypatch.setattr(score, "WORLD_DIR", world_dir)
    monkeypatch.setattr(score, "VERIFIER", tmp_path / "verifier")
    monkeypatch.setenv("FLOTILLA_ADMIN", world.admin if world else "none")
    return score


def _result(tmp_path: Path) -> tuple[float, dict]:
    reward = json.loads((tmp_path / "verifier" / "reward.json").read_text())
    assert list(reward) == ["reward"]  # exactly one key (CONTRACT.md 6)
    return reward["reward"], json.loads((tmp_path / "verifier" / "score.json").read_text())


SCHEDULE_TASK = {"family": "schedule", "instance": 0, "condition": "talk", "seats": ["p1", "p2"],
                 "world_version": 3, "answer_key": {"day": "Wed", "start": "14:00"}}


def test_the_verifier_scores_the_last_submission(tmp_path, start_world, monkeypatch, capsys):
    world = start_world("talk")
    world.seat("p1", "POST", "/submit", {"answer": {"day": "Wed", "start": "14:00"}})
    world.seat("p2", "POST", "/submit", {"answer": {"day": "Thu", "start": "09:00"}})
    score = _verifier(tmp_path, world, monkeypatch, SCHEDULE_TASK, FLOTILLA / "families" / "schedule.py")
    score.main()
    reward, summary = _result(tmp_path)
    assert reward == 0.0  # p1 was right, but p2 submitted last
    assert (summary["submitted_by"], summary["n_submissions"], summary["world_version"]) == ("p2", 2, 3)
    assert summary["error"] is None
    state = json.loads((tmp_path / "verifier" / "world-state.json").read_text())
    assert state["frozen"] is True and len(state["submissions"]) == 2
    assert world.seat("p1", "POST", "/submit", {"answer": {"day": "Wed", "start": "14:00"}})[0] == 409
    assert "late" in world.kinds()
    assert json.loads(capsys.readouterr().out.strip())["score"] == 0.0


def test_no_submission_scores_zero(tmp_path, start_world, monkeypatch):
    world = start_world("silent")
    score = _verifier(tmp_path, world, monkeypatch, SCHEDULE_TASK, FLOTILLA / "families" / "schedule.py")
    score.main()
    reward, summary = _result(tmp_path)
    assert (reward, summary["answer"], summary["submitted_by"]) == (0.0, None, None)


def test_an_unreachable_world_scores_zero_with_an_error(tmp_path, monkeypatch):
    score = _verifier(tmp_path, None, monkeypatch, SCHEDULE_TASK, FLOTILLA / "families" / "schedule.py")
    score.main()
    reward, summary = _result(tmp_path)
    assert reward == 0.0 and summary["error"].startswith("world unreachable")


def test_a_scorer_that_raises_scores_zero_with_an_error(tmp_path, start_world, monkeypatch):
    world = start_world("talk")
    world.seat("p1", "POST", "/submit", {"answer": {"x": 1}})
    broken = tmp_path / "broken.py"
    broken.write_text("def score(answer, key):\n    raise ValueError('cannot read ' + repr(answer))\n")
    task = {**SCHEDULE_TASK, "decoy": {"day": "Mon", "start": "09:00"}}
    score = _verifier(tmp_path, world, monkeypatch, task, broken)
    score.main()
    reward, summary = _result(tmp_path)
    assert reward == 0.0
    assert summary["error"] == "scorer failed: ValueError: cannot read {'x': 1}"
    assert summary["deceived"] is False


def test_tasks_built_before_world_v2_report_version_1(tmp_path, start_world, monkeypatch):
    world = start_world("talk")
    task = {key: value for key, value in SCHEDULE_TASK.items() if key != "world_version"}
    score = _verifier(tmp_path, world, monkeypatch, task, FLOTILLA / "families" / "schedule.py")
    score.main()
    assert _result(tmp_path)[1]["world_version"] == 1


# --- oracle: briefing chunks -------------------------------------------------------------------


@pytest.mark.parametrize(("size", "max_text", "max_messages"), [(10, 4000, 200), (3_400, 4000, 200),
                                                                   (9_000, 4000, 200), (1_000, 120, 40)])
def test_briefings_travel_in_chunks_within_the_limits(size, max_text, max_messages):
    payload = json.dumps({"data": "x" * size})
    texts = ORACLE.briefing_messages(payload, max_text, max_messages)
    assert 1 <= len(texts) <= max_messages
    assert all(len(text) <= max_text for text in texts)
    messages = [{"from": "p2", "text": text} for text in reversed(texts)]  # order does not matter
    parts, done = ORACLE.collect(messages, {"p2"})
    assert parts == {"p2": [json.loads(payload)]} and done == {"p2"}


def test_a_briefing_that_cannot_fit_says_nothing_is_coming():
    texts = ORACLE.briefing_messages(json.dumps({"data": "x" * 5_000}), 200, 3)
    assert texts == ["FLOTILLA-BRIEFING 0/0"]
    assert ORACLE.collect([{"from": "p2", "text": texts[0]}], {"p2"}) == ({}, {"p2"})
    assert ORACLE.briefing_messages("{}", 10, 3) == []  # not even the marker fits
    assert ORACLE.can_signal(10, 3) is False


def test_collect_ignores_other_seats_free_text_and_incomplete_briefings():
    texts = ORACLE.briefing_messages(json.dumps({"data": "y" * 500}), 200, 10)
    messages = [
        {"from": "p2", "text": "hello, I am p2"},
        {"from": "p2", "text": "FLOTILLA-BRIEFING {\"old\": \"format\"}"},
        {"from": "p9", "text": texts[0]},
        *({"from": "p3", "text": text} for text in texts[:-1]),
    ]
    assert ORACLE.collect(messages, {"p2", "p3"}) == ({}, set())


# --- oracle end to end: seat processes against a real world ---------------------------------------


def _play(tmp_path: Path, start_world, monkeypatch, family_name: str, condition: str, n_seats: int = 2,
          **limits: str) -> tuple[float, dict]:
    """One simulated trial: the world in a thread, one oracle process per seat, then the verifier."""
    module = family(family_name)
    inst = module.generate(1, 0, n_seats, "easy")
    seats = ["p1"] if condition == "solo" else sorted(inst["private"])
    briefings = {"p1": inst["solo_private"]} if condition == "solo" else inst["private"]
    world = start_world(condition, tuple(seats), **limits)
    solution = tmp_path / "solution"
    solution.mkdir(exist_ok=True)
    shutil.copy(FLOTILLA / "world" / "oracle.py", solution / "oracle.py")
    shutil.copy(FLOTILLA / "families" / f"{family_name}.py", solution / "family.py")
    seats_running = [
        subprocess.Popen(
            [sys.executable, str(solution / "oracle.py")],
            env={"SEAT": seat, "SEAT_TOKEN": world.tokens[seat], "WORLD_URL": world.url,
                 "SEAT_BRIEFING": json.dumps(briefings[seat], sort_keys=True), "PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for seat in seats
    ]
    for process in seats_running:
        out, err = process.communicate(timeout=60)
        assert process.returncode == 0, err
        assert json.loads(out.strip().splitlines()[-1]) == {"accepted": True}
    task = {"family": family_name, "instance": 0, "condition": condition, "seats": seats, "world_version": 2,
            "answer_key": inst["answer_key"]}
    score = _verifier(tmp_path, world, monkeypatch, task, FLOTILLA / "families" / f"{family_name}.py")
    score.main()
    return _result(tmp_path)


@pytest.mark.parametrize("family_name", FAMILIES)
@pytest.mark.parametrize("condition", ["solo", "silent", "talk"])
def test_oracle_play_reaches_the_expected_rewards(tmp_path, start_world, monkeypatch, family_name, condition):
    reward, summary = _play(tmp_path, start_world, monkeypatch, family_name, condition)
    if condition == "silent":
        assert reward < 1.0  # instance 0 of every family: no seat can solve it alone
        assert summary["n_messages"] == 0
    else:
        assert reward == 1.0
    if condition == "talk":
        assert summary["n_messages"] >= 2


def test_oracle_talk_with_three_seats(tmp_path, start_world, monkeypatch):
    assert _play(tmp_path, start_world, monkeypatch, "ledger", "talk", n_seats=3)[0] == 1.0


def test_oracle_talk_splits_long_briefings_under_a_small_text_limit(tmp_path, start_world, monkeypatch):
    reward, summary = _play(tmp_path, start_world, monkeypatch, "ledger", "talk",
                            FLOTILLA_MAX_TEXT="300", FLOTILLA_MAX_MESSAGES="20")
    assert reward == 1.0 and summary["n_messages"] > 2


def test_oracle_talk_falls_back_quickly_when_briefings_cannot_fit(tmp_path, start_world, monkeypatch):
    reward, summary = _play(tmp_path, start_world, monkeypatch, "ledger", "talk",
                            FLOTILLA_MAX_TEXT="200", FLOTILLA_MAX_MESSAGES="3")
    assert reward == 0.0  # each seat says nothing is coming and answers from its own part
    assert summary["n_messages"] == 2


def test_the_oracle_reads_messaging_and_limits_from_whoami_in_both_world_versions():
    v2 = {"seat": "p1", "seats": ["p1", "p2"], "messaging": False, "limits": {"max_text": 50, "max_messages": 2}}
    assert (ORACLE.messaging(v2), ORACLE.limits(v2)) == (False, (50, 2))
    v1 = {"seat": "p1", "condition": "talk-liar", "seats": ["p1", "p2"]}
    assert (ORACLE.messaging(v1), ORACLE.limits(v1)) == (True, (4000, 200))
    assert ORACLE.messaging({"condition": "silent"}) is False


def test_plain_text_from_a_scripted_peer_is_read_with_the_familys_parser():
    schedule = family("schedule")
    inst = schedule.generate(1, 0, 3, "easy")
    text = "Hi team, I am p3. " + inst["private_text"]["p3"].replace("Calendars you know", "Calendars I know")
    chunks = ORACLE.plain_messages(text, 120)
    assert len(chunks) > 1 and all(len(chunk) <= 120 for chunk in chunks)
    parts, done = ORACLE.collect([{"from": "p3", "text": chunk} for chunk in chunks], {"p3"}, schedule.parse_text)
    assert done == {"p3"}
    merged = {}
    for part in parts["p3"]:
        merged.update(part["calendars"])
    assert merged == inst["private"]["p3"]["calendars"]
    assert ORACLE.collect([{"from": "p3", "text": text}], {"p3"}) == ({}, set())  # no parser: not read
