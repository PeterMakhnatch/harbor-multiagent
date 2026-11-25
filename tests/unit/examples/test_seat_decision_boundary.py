import itertools
import json
import random
import subprocess
import sys
from fractions import Fraction

import pytest

from examples.seatlab.decision_boundary.core import World, analyze


def exhaustive(worlds: list[World], scale: int) -> dict:
    actions = tuple(worlds[0].scores)
    observations = tuple(dict.fromkeys(w.observation for w in worlds))
    denominator = scale * sum(w.mass for w in worlds)

    def payoff(choices):
        return Fraction(
            sum(w.mass * w.scores[a] for w, a in zip(worlds, choices, strict=True)),
            denominator,
        )

    unobserved = max(payoff([a] * len(worlds)) for a in actions)
    observed = max(
        payoff([mapping[observations.index(w.observation)] for w in worlds])
        for mapping in itertools.product(actions, repeat=len(observations))
    )
    informed = max(
        payoff(mapping) for mapping in itertools.product(actions, repeat=len(worlds))
    )
    ambiguous = 0
    for observation in observations:
        members = [w for w in worlds if w.observation == observation]
        informed_class = sum(w.mass * max(w.scores.values()) for w in members)
        observed_class = max(
            sum(w.mass * w.scores[a] for w in members) for a in actions
        )
        ambiguous += informed_class > observed_class
    return {
        "worlds": len(worlds),
        "observations": len(observations),
        "actions": len(actions),
        "mass": sum(w.mass for w in worlds),
        "scale": scale,
        "unobserved": str(unobserved),
        "observed": str(observed),
        "informed": str(informed),
        "gap": str(informed - observed),
        "ambiguous_classes": ambiguous,
    }


def check(worlds: list[World], scale: int) -> dict:
    expected = exhaustive(worlds, scale)
    actual = analyze(worlds, scale)
    assert {key: actual[key] for key in expected} == expected
    return expected


def test_enumerated_policies_and_order_invariance():
    rng = random.Random(712)
    for _ in range(160):
        scale = rng.randint(1, 9)
        actions = [f"action-{a}" for a in range(rng.randint(1, 3))]
        worlds = [
            World(
                str(i),
                rng.randint(1, 7),
                rng.choice([b"", b"\x00", b"\xff", b"x\n"]),
                {a: rng.randint(0, scale) for a in actions},
            )
            for i in range(rng.randint(1, 5))
        ]
        expected = check(worlds, scale)
        reversed_worlds = [
            World(w.name, w.mass, w.observation, dict(reversed(list(w.scores.items()))))
            for w in reversed(worlds)
        ]
        reordered = analyze(reversed_worlds, scale)
        assert {key: reordered[key] for key in expected} == expected


def test_unequal_masses_and_scaled_scores():
    worlds = [
        World("x", 2, b"same", {"a": 5, "b": 0}),
        World("y", 3, b"same", {"a": 1, "b": 4}),
    ]
    result = check(worlds, 5)
    assert result["unobserved"] == result["observed"] == "13/25"
    assert result["informed"] == "22/25"
    assert result["gap"] == "9/25"


def test_tie_with_common_optimal_action_has_no_information_conflict():
    worlds = [
        World("x", 1, b"same", {"a": 1, "b": 1, "c": 0}),
        World("y", 2, b"same", {"a": 0, "b": 1, "c": 1}),
    ]
    result = check(worlds, 1)
    assert result["gap"] == "0"
    assert result["ambiguous_classes"] == 0


def test_pairwise_compatible_argmax_sets_can_have_empty_global_intersection():
    worlds = [
        World("x", 1, b"same", {"a": 1, "b": 1, "c": 0}),
        World("y", 1, b"same", {"a": 0, "b": 1, "c": 1}),
        World("z", 1, b"same", {"a": 1, "b": 0, "c": 1}),
    ]
    result = check(worlds, 1)
    assert result["observed"] == "2/3"
    assert result["gap"] == "1/3"
    assert result["ambiguous_classes"] == 1


def test_literal_nuisance_byte_can_remove_measured_gap():
    worlds = [
        World("x", 1, b"same", {"a": 1, "b": 0}),
        World("y", 1, b"same", {"a": 0, "b": 1}),
    ]
    original = check(worlds, 1)
    changed = check([worlds[0], World("y", 1, b"same\x00", worlds[1].scores)], 1)
    assert original["gap"] == "1/2"
    assert changed["gap"] == "0"
    assert changed["observations"] == 2
    assert changed["informed"] == original["informed"] == "1"
    assert changed["unobserved"] == original["unobserved"] == "1/2"


@pytest.mark.parametrize(
    "worlds,scale",
    [
        ([], 1),
        ((World("x", 1, b"x", {"a": 0}),), 1),
        ([World("x", 1, b"x", {"a": 0})], 0),
        ([World("x", 1, b"x", {"a": 0})], True),
        ([World("x", 0, b"x", {"a": 0})], 1),
        ([World("x", -1, b"x", {"a": 0})], 1),
        ([World("x", True, b"x", {"a": 0})], 1),
        ([World("x", 1.5, b"x", {"a": 0})], 1),
        ([World("x", 1, "x", {"a": 0})], 1),
        ([World("x", 1, b"x", {})], 1),
        ([World("x", 1, b"x", {"a": False})], 1),
        ([World("x", 1, b"x", {"a": 0.5})], 1),
        ([World("x", 1, b"x", {"a": -1})], 1),
        ([World("x", 1, b"x", {"a": 2})], 1),
        ([World("x", 1, b"x", {1: 0})], 1),
        ([World("x", 1, b"x", {"a": 0}), World("y", 1, b"y", {"b": 0})], 1),
        ([World("x", 1, b"x", {"a": 0}), World("x", 1, b"y", {"a": 1})], 1),
    ],
)
def test_reject_invalid_worlds(worlds, scale):
    with pytest.raises((TypeError, ValueError)):
        analyze(worlds, scale)


def test_native_public_aggregate():
    completed = subprocess.run(
        [
            sys.executable,
            "-BS",
            "-m",
            "examples.seatlab.decision_boundary",
            "native",
            "--seed",
            "7",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    studies = json.loads(completed.stdout)["studies"]
    assert studies
    for result in studies.values():
        assert 1 <= result["observations"] <= result["worlds"] <= result["mass"]
        assert result["actions"] > 0
        assert result["scale"] > 0
        metrics = {
            k: Fraction(result[k])
            for k in ("unobserved", "observed", "informed", "gap")
        }
        assert all(str(v) == result[k] for k, v in metrics.items())
        assert (
            0
            <= metrics["unobserved"]
            <= metrics["observed"]
            <= metrics["informed"]
            <= 1
        )
        assert metrics["gap"] == metrics["informed"] - metrics["observed"]
        assert 0 <= result["ambiguous_classes"] <= result["observations"]
        assert (result["ambiguous_classes"] > 0) == (metrics["gap"] > 0)
