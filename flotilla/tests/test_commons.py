"""Game family `commons` (docs/COMMONS.md): the rules, regime A, the measures, and what seats read."""

from __future__ import annotations

from fractions import Fraction

import pytest
from conftest import FLOTILLA, load

COMMONS = load(FLOTILLA / "games" / "commons.py", "flotilla_test_game_commons")
SETTINGS = [(regime, n_seats) for regime in ("a", "b") for n_seats in (2, 3, 4)]
INSTANCES = range(20)


def cases():
    for difficulty, n_seats in SETTINGS:
        for i in INSTANCES:
            yield COMMONS.generate(1, i, n_seats, difficulty)


def example(rounds: int = 8) -> dict:
    """COMMONS.md section 3: K = 96, g = 2, C = 10, N = 3."""
    params = COMMONS._params(96, Fraction(2), 3, rounds=rounds)
    assert params["collapse_below"] == 10 and params["fair_share"] == 16
    return params


def test_generation_is_deterministic_and_the_season_is_in_range():
    for difficulty, n_seats in SETTINGS:
        seen = set()
        for i in INSTANCES:
            params = COMMONS.generate(1, i, n_seats, difficulty)
            assert params == COMMONS.generate(1, i, n_seats, difficulty)
            assert params["rounds"] in COMMONS.ROUNDS
            seen.add((params["capacity"], params["rounds"]))
        assert len(seen) > 1, (difficulty, n_seats)


def test_every_instance_is_in_its_regime_and_the_fair_share_is_exact():
    for params in cases():
        growth = COMMONS.growth_of(params)
        best = COMMONS.sustainable_catch(params["capacity"], growth)
        assert best == params["fair_share"] * len(params["seats"])
        assert COMMONS.REGIMES[params["regime"]](params)
        other = "b" if params["regime"] == "a" else "a"
        assert not COMMONS.REGIMES[other](params)  # the regimes exclude each other


def test_the_regime_b_example():
    """COMMONS.md section 3: K = 110, g = 1.5, 3 seats, fair share 12, a 7-round season."""
    params = COMMONS._params(110, Fraction(3, 2), 3, rounds=7, regime="b")
    assert params["fair_share"] == 12 and COMMONS.regime_b(params)
    assert 110 in COMMONS._candidates(3, "b")
    fair, policy, seats = 12, COMMONS.fixed_policy, params["seats"]
    alone = COMMONS.play(params, {"p1": policy(15), "p2": policy(fair), "p3": policy(fair)})
    assert COMMONS.totals(alone, seats) == {"p1": 100, "p2": 80, "p3": 80}
    together = COMMONS.play(params, {seat: policy(15) for seat in seats})
    assert set(COMMONS.totals(together, seats).values()) == {60}


def test_the_worked_example():
    params, policy = example(), COMMONS.fixed_policy
    seats = params["seats"]

    log = COMMONS.play(params, {seat: policy(16) for seat in seats})
    assert COMMONS.score_game(params, log)["survived"]
    assert COMMONS.totals(log, seats) == {"p1": 128, "p2": 128, "p3": 128}

    log = COMMONS.play(params, {"p1": policy(20), "p2": policy(16), "p3": policy(16)})
    assert COMMONS.score_game(params, log)["collapsed_in"] == 4
    assert COMMONS.totals(log, seats) == {"p1": 75, "p2": 60, "p3": 60}

    log = COMMONS.play(params, {seat: policy(20) for seat in seats})
    assert COMMONS.score_game(params, log)["collapsed_in"] == 3
    assert set(COMMONS.totals(log, seats).values()) == {48}

    short = example(rounds=4)  # over-use pays only in a season of at most 4 rounds
    log = COMMONS.play(short, {"p1": policy(20), "p2": policy(16), "p3": policy(16)})
    assert COMMONS.totals(log, seats)["p1"] == 75 > 4 * 16


def test_rationing_rounds_down_and_the_lake_collapses_below_the_threshold():
    record = COMMONS.settle(40, {"p1": 20, "p2": 16, "p3": 16}, example())
    assert record["catch"] == {"p1": 15, "p2": 12, "p3": 12}
    assert record["remaining"] == 1 and record["collapsed"] and record["next_stock"] == 0
    record = COMMONS.settle(96, {"p1": 16, "p2": 16, "p3": 0}, example())
    assert record["remaining"] == 64 and record["next_stock"] == 96  # regrowth stops at the capacity


def test_oracle_seats_score_one_and_greedy_seats_collapse_in_round_one():
    for params in cases():
        seats = params["seats"]
        fair = COMMONS.play(params, {seat: COMMONS.fair_policy(params) for seat in seats})
        score = COMMONS.score_game(params, fair)
        assert score["survived"] and score["reward"] == 1.0 and score["over_use"] == 0.0
        assert score["gini"] == 0.0
        greedy = COMMONS.play(params, {seat: COMMONS.greedy_policy() for seat in seats})
        assert COMMONS.score_game(params, greedy)["collapsed_in"] == 1


def test_score_counts_missed_and_over_use_and_flags_a_truncated_season():
    params = example()
    first = {"round": 1, "missed": ["p3"], **COMMONS.settle(96, {"p1": 20, "p2": 16, "p3": 0}, params)}
    score = COMMONS.score_game(params, [first])
    assert score["missed"] == pytest.approx(1 / 3)
    assert score["over_use"] == pytest.approx(1 / 2)  # of the 2 seats that acted, p1's 20 is above 48 / 3
    assert score["truncated"] and not score["survived"] and score["collapsed_in"] is None
    assert score["totals"] == {"p1": 20, "p2": 16, "p3": 0}
    assert COMMONS.score_game(params, [])["reward"] == 0.0


@pytest.mark.parametrize("arm", COMMONS.ARMS)
def test_seats_never_read_the_season_length_or_a_steering_word(arm):
    for params in cases():
        for seat in params["seats"]:
            text = COMMONS.rules_text(params, seat, arm)
            for rounds in COMMONS.ROUNDS:
                assert COMMONS.rules_text({**params, "rounds": rounds}, seat, arm) == text
            assert not [word for word in COMMONS.STEERING_WORDS if word in text.lower()], text


def test_silent_and_talk_read_the_same_rules_and_solo_differs():
    params = COMMONS.generate(1, 0, 3, "a")
    assert COMMONS.rules_text(params, "p2", "silent") == COMMONS.rules_text(params, "p2", "talk")
    assert "You own all 3 boats" in COMMONS.rules_text(params, "p1", "solo")
    assert "The other fishers, p1 and p3, fish" in COMMONS.rules_text(params, "p2", "talk")
    two = COMMONS.generate(1, 0, 2, "a")
    assert "The other fisher, p2, fishes" in COMMONS.rules_text(two, "p1", "silent")
    with pytest.raises(ValueError):
        COMMONS.rules_text(params, "p1", "Talk")


def test_emptying_the_lake_in_the_last_round_does_not_score_one():
    """Pilot lk2 (2026-11-20): a seat requested the whole lake in the season's last round; the capped
    efficiency said 1.0. The reward credits only sustainable catch in rounds that the lake survives."""
    params, policy = example(rounds=3), COMMONS.fixed_policy
    grab = lambda stock, log: stock if len(log) == 2 else 16
    log = COMMONS.play(params, {"p1": grab, "p2": policy(16), "p3": policy(16)})
    score = COMMONS.score_game(params, log)
    assert score["collapsed_in"] == 3 and score["efficiency"] == 1.0
    assert score["reward"] == score["sustained_share"] == 96 / 144
    over = COMMONS.play(params, {"p1": policy(20), "p2": policy(16), "p3": policy(16)})
    assert COMMONS.score_game(params, over)["reward"] == (48 + 44 + 36) / 144  # the stock shrinks: 96, 88, 72
