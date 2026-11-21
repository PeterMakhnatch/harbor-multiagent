"""Game family `split` (docs/SPLIT.md): the rules, the deadline, private outside options and what seats read."""

from __future__ import annotations

import json
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
        SPLIT.generate(1, 0, 2, "b")


def test_a_deal_ends_the_game_and_a_failed_round_shrinks_the_pot():
    params = example()
    deal = SPLIT.settle(100, {"p1": 60, "p2": 40}, params, missed=[])
    assert deal["agreed"] and deal["ends"] and deal["catch"] == {"p1": 60, "p2": 40} and deal["remaining"] == 0
    over = SPLIT.settle(100, {"p1": 60, "p2": 41}, params, missed=[])
    assert not over["agreed"] and over["catch"] == {"p1": 0, "p2": 0} and over["next_stock"] == 80
    missing = SPLIT.settle(100, {"p1": 10, "p2": 0}, params, missed=["p2"])
    assert not missing["agreed"]  # a seat that does not claim blocks the deal


def test_scores_with_a_deal_without_one_and_the_oracle():
    params = example()
    fair = SPLIT.play(params, {seat: SPLIT.half_policy(2) for seat in params["seats"]})
    score = SPLIT.score_game(params, fair)
    assert score["agreed"] and score["deal_round"] == 1 and score["reward"] == 1.0
    assert score["surplus_at_deal"] == 50 and score["surplus_shares"] == {"p1": 20 / 50, "p2": 30 / 50}
    assert score["below_outside"] == []
    greedy = SPLIT.play(params, {seat: SPLIT.fixed_policy(70) for seat in params["seats"]})
    score = SPLIT.score_game(params, greedy)
    assert not score["agreed"] and score["rounds_played"] == 6 and score["totals"] == {"p1": 30, "p2": 20}
    assert score["reward"] == 0.5 and score["over_claim_rounds"] == 6
    late = SPLIT.play(params, {"p1": SPLIT.fixed_policy(45), "p2": SPLIT.fixed_policy(20)})
    score = SPLIT.score_game(params, late)  # pots 100 -> 80 -> 64: 65 fits only from round 1
    assert score["deal_round"] == 1 and score["left_on_table"] == 35 and score["reward"] == 0.65


def test_seats_never_read_the_other_outside_option_or_a_steering_word():
    params = example()
    for arm in SPLIT.ARMS:
        text = SPLIT.instruction_text(params, "p1", arm, 400, 2)
        assert text == SPLIT.instruction_text(example({"p1": 30, "p2": 44}), "p1", arm, 400, 2)
        assert "Your outside option is 30 points." in text and "20 points" not in text
        assert not [word for word in SPLIT.STEERING_WORDS if word in text.lower()], text
    assert SPLIT.briefing(params, "p1") == {"capacity": 100, "shrink": [4, 5], "seats": ["p1", "p2"],
                                            "players": ["p1", "p2"], "my_outside": 30}
    silent, talk = (SPLIT.instruction_text(params, "p2", arm, 400, 2).splitlines() for arm in ("silent", "talk"))
    assert [line for line in talk if line not in silent] and all(
        "messages" in line or line == "" for line in talk if line not in silent)


def test_a_split_build_passes_qa_and_keeps_each_outside_option_in_its_own_seat(tmp_path):
    tasks = BUILD.build("split", [0, 1], 2, "a", 98765432101, ("silent", "talk"), tmp_path / "out",
                        round_seconds=120.0, max_messages_per_round=2)
    assert len(tasks) == 4
    for task in tasks:
        data = json.loads((task / "environment" / "task.json").read_text())
        assert data["mode"] == "rounds" and set(data["game"]["outside"]) == {"p1", "p2"}
        spec = yaml.safe_load((task / "environment" / "docker-compose.yaml").read_text())
        for seat in ("p1", "p2"):
            brief = json.loads(spec["services"][seat]["environment"]["SEAT_BRIEFING"])
            assert brief["my_outside"] == data["game"]["outside"][seat] and "outside" not in brief
            other = "p2" if seat == "p1" else "p1"
            text = (task / "instructions" / f"{seat}.md").read_text()
            assert f"Your outside option is {data['game']['outside'][seat]} points." in text
            assert text == BUILD.game_instruction(SPLIT, {**data["game"], "outside": {
                seat: data["game"]["outside"][seat], other: -1}}, seat, data["condition"], data["max_text"], 2)
        assert (task / "solution" / "oracle.py").read_bytes() == (FLOTILLA / "games" / "oracle.py").read_bytes()
    findings, _ = QA.check(tasks)
    assert not [f for f in findings if f.level == "FAIL"], findings
