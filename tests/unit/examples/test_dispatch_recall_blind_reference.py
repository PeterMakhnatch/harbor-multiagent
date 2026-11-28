from fractions import Fraction
from typing import cast

import pytest

from examples.seatlab.dispatch_recall import blind_reference as b
from examples.seatlab.dispatch_recall import design as d

WORLDS = ("stay", "move")
TIMINGS = ("immediate", "deferred", "withheld")


def _cells(seeds, rule):
    return {
        (seed, world, timing): {"team_reward": rule(seed, world, timing)}
        for seed in seeds
        for world in WORLDS
        for timing in TIMINGS
    }


def _p(config):
    return Fraction(
        config["one_sided_p"]["numerator"], config["one_sided_p"]["denominator"]
    )


def test_contract_fields_and_alpha_split():
    report = b.blind_reference([1, 2, 3, 4, 5, 6], _cells(range(1, 7), lambda *_: 1))
    assert report["schema"] == b.SCHEMA
    assert report["role"] == "calibration_not_primary"
    assert report["metric"] == "team_reward"
    assert report["alpha_total"] == {"numerator": 1, "denominator": 20}
    assert report["alpha_per_arm"] == {"numerator": 1, "denominator": 40}
    assert report["min_blocks"] == 6
    assert set(report["arms"]) == {"deferred", "withheld"}
    for arm in report["arms"].values():
        assert set(arm) == {
            "timing",
            "status",
            "planned_blocks",
            "complete_blocks",
            "incomplete_blocks",
            "missing_cells",
            "lower",
            "upper",
        }
        assert set(arm["lower"]) == {"successes", "ties", "failures", "one_sided_p"}
    assert all(
        "validat" not in text or "not validation" in text for text in report["limits"]
    )


def test_correlated_signal_in_both_arms_passes_old_placebo_but_exceeds():
    seeds = [1, 2, 3, 4, 5, 6]
    cells = _cells(seeds, lambda *_: 1)
    placebo = d._contrast(seeds, cells, d.CONTRASTS["negative_control"], "team_reward")
    assert placebo["grade"] == "not_established"
    assert placebo["status"] == "no_violation_detected"
    report = b.blind_reference(seeds, cells)
    assert report["exceeded"] is True
    for arm in report["arms"].values():
        assert arm["status"] == "exceeded"
        assert arm["lower"] == arm["upper"]
        assert arm["lower"]["successes"] == 6
        assert _p(arm["lower"]) == Fraction(1, 64)


@pytest.mark.parametrize(
    "policy",
    [
        lambda seed, world, timing: int(world == "stay"),
        lambda seed, world, timing: int(world == "move"),
        lambda seed, world, timing: int((world == "stay") == (seed % 2 == 0)),
    ],
)
def test_blind_policies_give_ties_and_no_detection(policy):
    seeds = list(range(10, 20))
    report = b.blind_reference(seeds, _cells(seeds, policy))
    assert report["exceeded"] is False
    for arm in report["arms"].values():
        assert arm["status"] == "not_detected"
        assert arm["lower"]["ties"] == len(seeds)
        assert _p(arm["lower"]) == 1


def test_abstention_and_wrong_commits_only_lower_the_score():
    seeds = list(range(1, 9))
    report = b.blind_reference(seeds, _cells(seeds, lambda *_: 0))
    assert report["exceeded"] is False
    for arm in report["arms"].values():
        assert arm["status"] == "not_detected"
        assert arm["lower"]["failures"] == len(seeds)
        assert arm["lower"]["successes"] == 0


def test_unknowns_that_could_decide_are_not_testable():
    seeds = [1, 2, 3, 4, 5, 6]

    def rule(seed, world, timing):
        if (seed, world, timing) == (6, "move", "deferred"):
            return None
        return 1

    arm = b.blind_reference(seeds, _cells(seeds, rule))["arms"]["deferred"]
    assert arm["status"] == "not_testable"
    assert (arm["complete_blocks"], arm["incomplete_blocks"], arm["missing_cells"]) == (
        5,
        1,
        1,
    )
    assert (arm["lower"]["successes"], arm["lower"]["ties"]) == (5, 1)
    assert _p(arm["lower"]) == Fraction(1, 32)
    assert arm["upper"]["successes"] == 6
    assert _p(arm["upper"]) == Fraction(1, 64)


def test_unknowns_that_cannot_decide_leave_not_detected():
    seeds = list(range(1, 9))

    def rule(seed, world, timing):
        if seed == 8 and world == "move":
            return None
        return int(world == "stay")

    report = b.blind_reference(seeds, _cells(seeds, rule))
    for arm in report["arms"].values():
        assert arm["status"] == "not_detected"
        assert arm["missing_cells"] == 1


def test_no_outcomes_is_not_testable():
    seeds = [1, 2, 3, 4, 5, 6]
    report = b.blind_reference(seeds, _cells(seeds, lambda *_: None))
    assert report["exceeded"] is False
    for arm in report["arms"].values():
        assert arm["status"] == "not_testable"
        assert arm["missing_cells"] == 12
        assert arm["complete_blocks"] == 0
    assert b.blind_reference([], {})["arms"]["deferred"]["status"] == "not_testable"


def test_five_positive_blocks_are_not_enough_and_six_are():
    five = [1, 2, 3, 4, 5]
    small = b.blind_reference(five, _cells(five, lambda *_: 1))["arms"]["deferred"]
    assert small["status"] == "not_testable"
    assert _p(small["lower"]) == Fraction(1, 32)
    six = [1, 2, 3, 4, 5, 6]

    def one_tie(seed, world, timing):
        return int(seed != 6 or world == "stay")

    tied = b.blind_reference(six, _cells(six, one_tie))["arms"]["deferred"]
    assert (tied["status"], tied["lower"]["successes"], tied["lower"]["ties"]) == (
        "not_detected",
        5,
        1,
    )
    full = b.blind_reference(six, _cells(six, lambda *_: 1))["arms"]["deferred"]
    assert full["status"] == "exceeded"
    seven = list(range(1, 8))

    def one_failure(seed, world, timing):
        return int(seed != 7)

    mixed = b.blind_reference(seven, _cells(seven, one_failure))["arms"]["deferred"]
    assert mixed["status"] == "not_detected"
    assert _p(mixed["lower"]) == Fraction(8, 128)


def test_one_arm_can_exceed_alone():
    seeds = [1, 2, 3, 4, 5, 6]

    def rule(seed, world, timing):
        return 1 if timing == "deferred" else int(world == "stay")

    report = b.blind_reference(seeds, _cells(seeds, rule))
    assert report["exceeded"] is True
    assert report["arms"]["deferred"]["status"] == "exceeded"
    assert report["arms"]["withheld"]["status"] == "not_detected"


@pytest.mark.parametrize("value", [True, 2, -1, 0.5, "1"])
def test_rejects_non_binary_rewards(value):
    seeds = [1]
    with pytest.raises(ValueError):
        b.blind_reference(seeds, _cells(seeds, lambda *_: value))


def test_rejects_missing_cells_and_bad_seeds():
    seeds = [1, 2]
    cells = _cells(seeds, lambda *_: 1)
    del cells[2, "move", "withheld"]
    with pytest.raises(ValueError):
        b.blind_reference(seeds, cells)
    with pytest.raises(ValueError):
        b.blind_reference([1, 1], _cells([1], lambda *_: 1))
    with pytest.raises(ValueError):
        b.blind_reference(cast(list[int], (1,)), _cells([1], lambda *_: 1))
