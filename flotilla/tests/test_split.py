"""Game family `split` (docs/SPLIT.md): the rules, the deadline, private outside options and what seats read."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from fractions import Fraction

import pytest
import yaml
from conftest import FLOTILLA, load

SPLIT = load(FLOTILLA / "games" / "split.py", "flotilla_test_game_split")
BUILD = load(FLOTILLA / "build_tasks.py", "flotilla_test_build_split")
QA = load(FLOTILLA / "qa.py", "flotilla_test_qa_split")


def example(outside=None):
    return SPLIT._params(100, Fraction(4, 5), outside or {"p1": 30, "p2": 20})


def test_generation_is_deterministic_and_outside_options_are_private_and_bounded():
    for i in range(30):
        params = SPLIT.generate(1, i, 2, "a")
        assert params == SPLIT.generate(1, i, 2, "a")
        assert params["capacity"] in SPLIT.POT and params["rounds"] == 6
        assert all(0 <= value <= 0.45 * params["capacity"] for value in params["outside"].values())
    with pytest.raises(ValueError):
        SPLIT.generate(1, 0, 2, "c")


def test_regime_b_gives_one_seat_an_outside_option_above_half_the_pot_and_keeps_a_surplus():
    strong_seats = set()
    for i in range(60):
        params = SPLIT.generate(1, i, 2, "b")
        pot, outside = params["capacity"], params["outside"]
        assert params == SPLIT.generate(1, i, 2, "b") and params["regime"] == "b"
        strong = [seat for seat, value in outside.items() if 2 * value > pot]
        assert len(strong) == 1 and 0.55 * pot - 1 < outside[strong[0]] <= 0.7 * pot
        assert sum(outside.values()) < pot and sum(outside.values()) - outside[strong[0]] <= 0.2 * pot
        strong_seats.add(strong[0])
    assert strong_seats == {"p1", "p2"}


def test_a_deal_ends_the_game_and_a_failed_round_shrinks_the_pot():
    params = example()
    deal = SPLIT.settle(100, {"p1": 60, "p2": 40}, params, missed=[])
    assert deal["agreed"] and deal["ends"] and deal["catch"] == {"p1": 60, "p2": 40} and deal["remaining"] == 0
    over = SPLIT.settle(100, {"p1": 60, "p2": 41}, params, missed=[])
    assert not over["agreed"] and over["catch"] == {"p1": 0, "p2": 0} and over["next_stock"] == 80
    missing = SPLIT.settle(100, {"p1": 10, "p2": 0}, params, missed=["p2"])
    assert not missing["agreed"]  # a seat that does not claim blocks the deal
    refusal = SPLIT.settle(100, {"p1": 10, "p2": 0}, params, missed=[], passed=["p2"])
    assert refusal == missing


def test_scores_with_a_deal_without_one_and_the_oracle():
    params = example()
    fair = SPLIT.play(params, {seat: SPLIT.half_policy(2) for seat in params["seats"]})
    score = SPLIT.score_game(params, fair)
    assert score["agreed"] and score["deal_round"] == 1 and score["reward"] == 1.0
    assert score["via"] == "claim" and score["proposals"] == score["acceptances"] == 0
    assert score["surplus_at_deal"] == 50 and score["surplus_shares"] == {"p1": 20 / 50, "p2": 30 / 50}
    assert score["below_outside"] == []
    greedy = SPLIT.play(params, {seat: SPLIT.fixed_policy(70) for seat in params["seats"]})
    score = SPLIT.score_game(params, greedy)
    assert not score["agreed"] and score["rounds_played"] == 6 and score["totals"] == {"p1": 30, "p2": 20}
    assert score["reward"] == 0.5 and score["over_claim_rounds"] == 6
    late = SPLIT.play(params, {"p1": SPLIT.fixed_policy(45), "p2": SPLIT.fixed_policy(20)})
    score = SPLIT.score_game(params, late)  # pots 100 -> 80 -> 64: 65 fits only from round 1
    assert score["deal_round"] == 1 and score["left_on_table"] == 35 and score["reward"] == 0.65


def test_old_round_logs_keep_their_scores_and_misses():
    params = example({"p1": 55, "p2": 20})
    log = [
        {"round": 1, "stock": 100, "requests": {"p1": 55, "p2": 50}, "agreed": False, "missed": []},
        {"round": 2, "stock": 80, "requests": {"p1": 0, "p2": 35}, "agreed": False, "missed": ["p1"]},
        {"round": 3, "stock": 64, "requests": {"p1": 0, "p2": 32}, "agreed": False, "missed": ["p1"]},
        {"round": 4, "stock": 51, "requests": {"p1": 0, "p2": 25}, "agreed": False, "missed": ["p1"]},
        {"round": 5, "stock": 40, "requests": {"p1": 0, "p2": 20}, "agreed": False, "missed": ["p1"]},
        {"round": 6, "stock": 32, "requests": {"p1": 0, "p2": 16}, "agreed": False, "missed": ["p1"]},
    ]
    assert SPLIT.score_game(params, log) == {
        "reward": 0.75, "efficiency": 0.75, "agreed": False, "deal_round": None, "rounds_played": 6,
        "via": None, "proposals": 0, "acceptances": 0,
        "rounds_planned": 6, "truncated": False, "totals": {"p1": 55, "p2": 20}, "outside": {"p1": 55, "p2": 20},
        "surplus_at_deal": 0, "surplus_shares": None, "below_outside": [], "left_on_table": None,
        "missed": 5 / 12, "passed": 0.0, "over_claim_rounds": 1,
    }


def test_standing_offer_scores_use_the_latest_ledger_and_old_logs_omit_them():
    params = example({"p1": 55, "p2": 20})
    offers = [
        {"id": "1-1", "proposer": "p1", "split": {"p1": 60, "p2": 20}, "round": 1, "time": 0.0,
         "seen": {}, "fate": "lapsed"},
        {"id": "1-2", "proposer": "p2", "split": {"p1": 40, "p2": 40}, "round": 1, "time": 1.0,
         "seen": {"p1": 2.0}, "fate": "replaced"},
        {"id": "2-1", "proposer": "p2", "split": {"p1": 55, "p2": 25}, "round": 2, "time": 3.0,
         "seen": {}, "fate": "accepted"},
    ]
    log = [
        {"round": 1, "stock": 100, "requests": {"p1": 0, "p2": 0}, "agreed": False, "missed": [],
         "proposals": 2, "acceptances": 0, "offers": offers[:2]},
        {"round": 2, "stock": 80, "requests": {"p1": 55, "p2": 25}, "agreed": True, "missed": [],
         "via": "proposal", "catch": {"p1": 55, "p2": 25}, "remaining": 0, "proposals": 1,
         "acceptances": 1, "offers": offers},
    ]
    score = SPLIT.score_game(params, log)
    assert score["proposals"] == 3 and score["acceptances"] == 1 and score["via"] == "proposal"
    assert (score["accepted"], score["replaced"], score["lapsed"], score["expired"]) == (1, 1, 1, 0)
    assert score["feasible"] == 2 and score["feasible_unseen"] == 1
    bare = SPLIT.score_game(params, [
        {"round": 1, "stock": 100, "requests": {}, "agreed": False, "missed": []}
    ])
    assert "feasible" not in bare


def test_seats_never_read_the_other_outside_option_or_a_steering_word():
    params = example()
    for arm in ("silent", "talk", "propose"):
        text = SPLIT.instruction_text(params, "p1", arm, 400, 2)
        assert text == SPLIT.instruction_text(example({"p1": 30, "p2": 44}), "p1", arm, 400, 2)
        assert "Your outside option is 30 points." in text and "20 points" not in text
        assert sum('`{"pass": true}` to `POST /claim`' in line for line in text.splitlines()) == 1
        assert not [word for word in SPLIT.STEERING_WORDS if word in text.lower()], text
        assert SPLIT.briefing(params, "p1", arm) == {
            "capacity": 100, "shrink": [4, 5], "seats": ["p1", "p2"], "players": ["p1", "p2"], "my_outside": 30
        }
    silent, talk = (SPLIT.instruction_text(params, "p2", arm, 400, 2).splitlines() for arm in ("silent", "talk"))
    assert [line for line in talk if line not in silent] and all(
        "messages" in line or line == "" for line in talk if line not in silent)
    propose = SPLIT.instruction_text(params, "p2", "propose", 400, 2)
    assert '`{"split": {"p1": <points>, "p2": <points>}}` to `POST /propose`' in propose
    assert '`{"id": "<id>"}` to `POST /accept`' in propose
    assert '`{"id": "<id>"}` to `POST /decline`' in propose
    assert "/messages" not in propose
    assert "stays open in later rounds" in propose
    assert "expire when their round closes" not in propose
    assert "feasible" not in propose.lower()


@pytest.mark.parametrize(("arm", "digest"), [
    ("silent", "10b9499f6eb0df8dc2258b47e7a3617cda05b9072050fade1ec1753cd279385d"),
    ("talk", "bbac9f3e34d0eee4e0c5cad149c287cbe3b377c55f059cb1302415d038807d3f"),
    ("open", "50800e2a87ee992f96238757ff0859b27ac3488adbedd0f2ef77775197a2b258"),
])
def test_non_propose_instructions_match_world_v9_bytes(arm, digest):
    text = "\0".join(
        SPLIT.instruction_text(SPLIT.generate(17377, 5, seats, "b"), f"p{s}", arm, 4000, 2)
        for seats in (2, 3) for s in range(1, seats + 1)
    )
    assert hashlib.sha256(text.encode()).hexdigest() == digest


@pytest.mark.parametrize("outside", [{"p1": 30, "p2": 20}, {"p1": 30, "p2": 20, "p3": 11}])
def test_open_replaces_only_the_private_option_line_with_one_line_per_seat(outside):
    params = example(outside)
    for seat, value in outside.items():
        own = f"Your outside option is {value} points."
        private = f"{own} Each other player has its own outside option; the world does not tell you its value."
        others = [f"{other}'s outside option is {points} points." for other, points in outside.items() if other != seat]
        options = "\n".join([own, *others])
        silent = SPLIT.instruction_text(params, seat, "silent", 400, 2)
        text = SPLIT.instruction_text(params, seat, "open", 400, 2)
        assert private in silent and text == silent.replace(private, options)
        assert "/messages" not in text
        assert not [word for word in SPLIT.STEERING_WORDS if word in text.lower()], text


def run_builder(*args):
    run = subprocess.run([sys.executable, str(FLOTILLA / "build_tasks.py"), "--family", "split", *map(str, args)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr


def task_data(task):
    return json.loads((task / "environment" / "task.json").read_text())


def test_a_split_build_passes_qa_and_exposes_outside_options_only_in_open(tmp_path):
    run_builder("--instances", "0-1", "--difficulty", "a", "--conditions", "silent,talk,open,propose", "--out", tmp_path)
    tasks = sorted((tmp_path / "split").iterdir())
    assert len(tasks) == 8
    assert {task_data(task)["condition"] for task in tasks} == {"silent", "talk", "open", "propose"}
    for task in tasks:
        data = task_data(task)
        assert data["mode"] == "rounds" and set(data["game"]["outside"]) == {"p1", "p2"}
        spec = yaml.safe_load((task / "environment" / "docker-compose.yaml").read_text())
        for seat in ("p1", "p2"):
            brief = json.loads(spec["services"][seat]["environment"]["SEAT_BRIEFING"])
            outside = data["game"]["outside"]
            assert brief["my_outside"] == outside[seat]
            text = (task / "instructions" / f"{seat}.md").read_text()
            assert f"Your outside option is {outside[seat]} points." in text
            if data["condition"] == "open":
                assert brief["outside"] == outside
                for other in data["seats"]:
                    if other != seat:
                        assert f"{other}'s outside option is {outside[other]} points." in text
                assert "/messages" not in text
            else:
                assert "outside" not in brief
                assert text == BUILD.game_instruction(SPLIT, {**data["game"], "outside": {seat: outside[seat]}},
                                                      seat, data["condition"], data["max_text"], 2)
        assert (task / "solution" / "oracle.py").read_bytes() == (FLOTILLA / "games" / "oracle.py").read_bytes()
    findings, _ = QA.check(tasks)
    assert not [f for f in findings if f.level == "FAIL"], findings


@pytest.mark.parametrize("arm", ["silent", "talk", "propose"])
def test_qa_catches_outside_options_leaked_in_an_instruction_or_briefing(tmp_path, arm):
    task, = BUILD.build("split", [0], 2, "b", 98765432101, (arm,), tmp_path)
    outside = task_data(task)["game"]["outside"]
    with (task / "instructions" / "p1.md").open("a") as instruction:
        instruction.write(f"p2's outside option is {outside['p2']} points.\n")
    compose = task / "environment" / "docker-compose.yaml"
    spec = yaml.safe_load(compose.read_text())
    environment = spec["services"]["p2"]["environment"]
    briefing = json.loads(environment["SEAT_BRIEFING"])
    environment["SEAT_BRIEFING"] = json.dumps(briefing | {"outside": outside})
    compose.write_text(yaml.safe_dump(spec))
    findings, _ = QA.check([task])
    assert [(f.seat, f.level) for f in findings if f.rule == "game-secret"] == [("p1", "FAIL"), ("p2", "FAIL")], findings


@pytest.mark.parametrize("new_arms", [("open",), ("open", "propose")])
def test_new_arms_keep_and_pair_with_the_earlier_arms_in_a_variant(tmp_path, new_arms):
    settings = ("--instances", "0,2,4", "--difficulty", "b", "--seats", "3", "--variant", "paired", "--out", tmp_path)
    run_builder(*settings, "--conditions", "silent,talk")
    label = tmp_path / "split.paired"
    original = {task: task_data(task) for task in label.iterdir()}
    assert len(original) == 6
    run_builder(*settings, "--conditions", ",".join(new_arms), "--seed-of", label)
    added = set(label.iterdir()) - original.keys()
    assert {(task_data(task)["instance"], task_data(task)["condition"]) for task in added} == {
        (instance, arm) for instance in (0, 2, 4) for arm in new_arms
    }
    assert len(added) == 3 * len(new_arms)
    assert {task: task_data(task) for task in original} == original
    for task in added:
        data = task_data(task)
        twins = [earlier for earlier in original.values() if earlier["instance"] == data["instance"]]
        assert {earlier["condition"] for earlier in twins} == {"silent", "talk"}
        assert all(earlier["seed"] == data["seed"] and earlier["game"] == data["game"] for earlier in twins)


def test_world_rejects_messages_in_open(start_world, tmp_path):
    task, = BUILD.build("split", [0], 2, "b", 98765432101, ("open",), tmp_path)
    environment = task / "environment"
    spec = yaml.safe_load((environment / "docker-compose.yaml").read_text())
    arm = spec["services"]["main"]["environment"]["FLOTILLA_CONDITION"]
    assert arm == "open"
    world = start_world(arm, FLOTILLA_GAME=str(environment / "family.py"),
                        FLOTILLA_GAME_PARAMS=str(environment / "task.json"))
    assert world.seat("p1", "GET", "/whoami")[1]["messaging"] is False
    assert world.seat("p1", "GET", "/messages")[0] == 403
    assert world.seat("p1", "POST", "/messages", {"to": "all", "text": "test"})[0] == 403
