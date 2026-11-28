import base64
import json
from dataclasses import replace
from fractions import Fraction
from itertools import product
from pathlib import Path
from typing import TypedDict, cast

import pytest

from examples.seatlab.decision_boundary.core import BoundaryError, World, analyze
from examples.seatlab.decision_boundary.inputs import SCHEMA as INPUT_SCHEMA
from examples.seatlab.decision_boundary.read_budget import evaluate, main


def _fixture() -> tuple[list[World], list[World]]:
    before = [
        World("secret-world-1", 3, b"secret-known", {"secret-A": 1, "secret-B": 0}),
        World("secret-world-2", 1, b"secret-known", {"secret-A": 1, "secret-B": 0}),
        World("secret-world-3", 1, b"secret-mixed", {"secret-A": 1, "secret-B": 0}),
        World("secret-world-4", 2, b"secret-mixed", {"secret-A": 0, "secret-B": 1}),
    ]
    replies = [
        replace(world, observation=reply)
        for world, reply in zip(
            before, (b"secret-x", b"secret-x", b"secret-y", b"secret-x"), strict=True
        )
    ]
    return before, replies


class _Reference(TypedDict):
    never: dict[str, Fraction]
    always: dict[str, Fraction]
    adaptive: dict[str, Fraction]
    advantage: Fraction


def _brute_rules(
    before: list[World], replies: list[World], scale: int, price: Fraction
) -> _Reference:
    by_name = {world.name: world.observation for world in replies}
    priors = sorted({world.observation for world in before})
    actions = tuple(before[0].scores)
    mass = sum(world.mass for world in before)
    policies = []
    for bits in product((False, True), repeat=len(priors)):
        read = dict(zip(priors, bits, strict=True))
        states = [
            (
                world.observation,
                by_name[world.name] if read[world.observation] else None,
            )
            for world in before
        ]
        distinct = tuple(dict.fromkeys(states))
        best = 0
        for choices in product(actions, repeat=len(distinct)):
            policy = dict(zip(distinct, choices, strict=True))
            best = max(
                best,
                sum(
                    world.mass * world.scores[policy[state]]
                    for world, state in zip(before, states, strict=True)
                ),
            )
        reward = Fraction(best, mass * scale)
        reads = Fraction(
            sum(world.mass for world in before if read[world.observation]), mass
        )
        policies.append((reward, reads, reward - price * reads))
    adaptive = max(policies, key=lambda row: (row[2], -row[1]))
    advantage = adaptive[2] - max(policies[0][2], policies[-1][2])
    return {
        "never": dict(zip(("reward", "reads", "utility"), policies[0], strict=True)),
        "always": dict(zip(("reward", "reads", "utility"), policies[-1], strict=True)),
        "adaptive": dict(zip(("reward", "reads", "utility"), adaptive, strict=True)),
        "advantage": advantage,
    }


def test_exhaustive_deterministic_read_rules() -> None:
    partitions = ((0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1), (0, 1, 2))
    for old, new, price in product(
        partitions,
        partitions,
        (Fraction(0), Fraction(1, 6), Fraction(1, 3), Fraction(1)),
    ):
        before = [
            World(str(index), weight, bytes([old[index]]), scores)
            for index, (weight, scores) in enumerate(
                [(1, {"A": 4, "B": 0}), (2, {"A": 1, "B": 4}), (3, {"A": 3, "B": 2})]
            )
        ]
        replies = [
            replace(world, observation=bytes([new[index]]))
            for index, world in enumerate(before)
        ]
        expected = _brute_rules(before, replies, 4, price)
        report = evaluate(before, 4, replies, 4, price)
        for arm in ("never", "always", "adaptive"):
            assert {
                key: Fraction(value) for key, value in report[arm].items()
            } == expected[arm]
        assert Fraction(report["selective_advantage"]) == expected["advantage"]
        assert report["selective_reading_pattern"] == (expected["advantage"] > 0)
        assert sum(Fraction(row["prior_mass"]) for row in report["thresholds"]) == 1


def test_prior_knowledge_is_retained_and_selectivity_has_positive_value() -> None:
    before, replies = _fixture()
    assert analyze(before, 1)["observed"] == "6/7"
    assert analyze(replies, 1)["observed"] == "5/7"
    result = evaluate(before, 1, replies[::-1], 1, Fraction(1, 4))
    assert result["never"] == {"reward": "6/7", "reads": "0", "utility": "6/7"}
    assert result["always"] == {"reward": "1", "reads": "1", "utility": "3/4"}
    assert result["adaptive"] == {"reward": "1", "reads": "3/7", "utility": "25/28"}
    assert result["selective_advantage"] == "1/28"
    assert result["selective_reading_pattern"] is True
    assert result["thresholds"] == [
        {"gain": "0", "prior_mass": "4/7"},
        {"gain": "1/3", "prior_mass": "3/7"},
    ]
    assert "secret-" not in json.dumps(result)


def test_prior_irrelevant_homogeneous_threshold_and_tie_skip() -> None:
    before = [
        World(
            str(index), mass, bytes([index // 2]), {"A": index % 2, "B": 1 - index % 2}
        )
        for index, mass in enumerate((1, 2, 2, 4))
    ]
    replies = [
        replace(world, observation=bytes([index % 2]))
        for index, world in enumerate(before)
    ]
    pooled = [replace(world, observation=b"same") for world in before]
    for price in (Fraction(0), Fraction(1, 4), Fraction(1, 3), Fraction(3, 4)):
        report = evaluate(before, 1, replies, 1, price)
        assert report == evaluate(pooled, 1, replies, 1, price)
        assert report["thresholds"] == [{"gain": "1/3", "prior_mass": "1"}]
        assert report["selective_advantage"] == "0"
        assert report["selective_reading_pattern"] is False
        assert report["adaptive"]["reads"] == ("1" if price < Fraction(1, 3) else "0")
        assert report["adaptive"]["reward"] == (
            "1" if price < Fraction(1, 3) else "2/3"
        )


def test_heterogeneous_tie_skips_and_zero_physical_reward() -> None:
    before, replies = _fixture()
    tied = evaluate(before, 1, replies, 1, Fraction(1, 3))
    assert tied["adaptive"] == tied["never"]
    assert tied["selective_advantage"] == "0"
    assert tied["selective_reading_pattern"] is False
    zero_before = [
        replace(world, scores={action: 0 for action in world.scores})
        for world in before
    ]
    zero_replies = [
        replace(world, scores={action: 0 for action in world.scores})
        for world in replies
    ]
    zero = evaluate(zero_before, 1, zero_replies, 1, Fraction(1))
    assert zero["always"] == {"reward": "0", "reads": "1", "utility": "-1"}
    assert (
        zero["never"]
        == zero["adaptive"]
        == {"reward": "0", "reads": "0", "utility": "0"}
    )
    assert zero["thresholds"] == [{"gain": "0", "prior_mass": "1"}]
    assert zero["selective_reading_pattern"] is False


def test_label_permutation_preserves_aggregate_report() -> None:
    before, replies = _fixture()
    names = {world.name: str(index) for index, world in enumerate(before[::-1])}

    def relabel(worlds: list[World]) -> list[World]:
        return [
            replace(
                world,
                name=names[world.name],
                observation=world.observation[::-1],
                scores={
                    name[::-1]: score
                    for name, score in reversed(tuple(world.scores.items()))
                },
            )
            for world in worlds[::-1]
        ]

    assert evaluate(before, 1, replies, 1, Fraction(1, 4)) == evaluate(
        relabel(before), 1, relabel(replies), 1, Fraction(1, 4)
    )


def test_invalid_or_mismatched_inputs_rejected() -> None:
    before, replies = _fixture()
    changed = replace(replies[0], mass=4)
    payoff = replace(replies[0], scores={"secret-A": 0, "secret-B": 1})
    roster = replace(replies[0], name="other-world")
    actions = [replace(world, scores={"other-action": 1}) for world in replies]
    for old, scale, new, new_scale in (
        (before, 1, replies, 2),
        (before, True, replies, 1),
        (before, 1, [changed, *replies[1:]], 1),
        (before, 1, [payoff, *replies[1:]], 1),
        (before, 1, [roster, *replies[1:]], 1),
        (before, 1, actions, 1),
        (before, 1, replies[1:], 1),
        (before, 1, [replies[0], *replies], 1),
        ([], 1, replies, 1),
        (before * 257, 1, replies, 1),
    ):
        with pytest.raises(BoundaryError) as exc:
            evaluate(old, scale, new, new_scale, Fraction(1, 4))
        assert "secret-" not in str(exc.value)
    for invalid in (0, True, 0.25, "1/4", Fraction(-1, 4), Fraction(5, 4)):
        with pytest.raises(BoundaryError):
            evaluate(before, 1, replies, 1, cast(Fraction, invalid))


def _write_census(path: Path, worlds: list[World]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": INPUT_SCHEMA,
                "scale": 1,
                "worlds": [
                    {
                        "name": world.name,
                        "mass": world.mass,
                        "scores": world.scores,
                        "observation_b64": base64.b64encode(world.observation).decode(
                            "ascii"
                        ),
                    }
                    for world in worlds
                ],
            }
        )
    )


def test_cli_expectations_and_invalid_price(tmp_path: Path, capsys) -> None:
    before, replies = _fixture()
    old, new = tmp_path / "before.json", tmp_path / "reply.json"
    _write_census(old, before)
    _write_census(new, replies)
    expected = evaluate(before, 1, replies, 1, Fraction(1, 4))
    args = [str(old), str(new), "--price", "1/4"]
    for expectation, status in (("yes", 0), ("no", 1)):
        assert main([*args, "--expect-selective", expectation]) == status
        captured = capsys.readouterr()
        assert json.loads(captured.out) == expected
        assert not captured.err
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == expected
    for price in ("0", "1", "2/8"):
        assert main([str(old), str(new), "--price", price]) == 0
        captured = capsys.readouterr()
        assert Fraction(json.loads(captured.out)["price"]) == Fraction(price)
        assert not captured.err
    for price in (
        "1/0",
        "0.25",
        "1e-1",
        " 1/4",
        "+1/4",
        "-1/4",
        "1_0/40",
        "١/٤",
        "01/4",
        "1/04",
        "5/4",
        "1/" + "9" * 20,
    ):
        assert main([str(old), str(new), f"--price={price}"]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert captured.err.startswith("read-budget:")
    assert main(["-", "-", "--price", "1/4"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "stdin can supply only one census" in captured.err
    new.write_text('{"secret-response":')
    assert main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "secret-response" not in captured.err
