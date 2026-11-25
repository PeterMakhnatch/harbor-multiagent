from dataclasses import replace
from fractions import Fraction
from itertools import product

from examples.seatlab.decision_boundary import World, compare


def _partitions(count: int) -> list[tuple[int, ...]]:
    return [
        labels
        for labels in product(range(count), repeat=count)
        if all(
            label <= 1 + max(labels[:i], default=-1) for i, label in enumerate(labels)
        )
    ]


def _worlds(labels: tuple[int, ...], tied: bool, prefix: bytes) -> list[World]:
    return [
        World(
            f"world-{i}",
            i + 1,
            prefix + bytes([label]),
            {"left": 4 if tied or i % 2 == 0 else 0, "right": 4 if i % 2 else 0},
        )
        for i, label in enumerate(labels)
    ]


def _relation(before: list[World], after: list[World]) -> str:
    before_by_name = {world.name: world for world in before}
    after_by_name = {world.name: world for world in after}
    before_pairs = {
        (a, b)
        for a, b in product(before_by_name, repeat=2)
        if before_by_name[a].observation == before_by_name[b].observation
    }
    after_pairs = {
        (a, b)
        for a, b in product(after_by_name, repeat=2)
        if after_by_name[a].observation == after_by_name[b].observation
    }
    if before_pairs == after_pairs:
        return "equivalent"
    if after_pairs < before_pairs:
        return "refinement"
    if before_pairs < after_pairs:
        return "coarsening"
    return "incomparable"


def _observed(worlds: list[World], scale: int) -> Fraction:
    observations = sorted({world.observation for world in worlds})
    actions = tuple(worlds[0].scores)
    return Fraction(
        max(
            sum(
                world.mass * world.scores[policy[observations.index(world.observation)]]
                for world in worlds
            )
            for policy in product(actions, repeat=len(observations))
        ),
        scale * sum(world.mass for world in worlds),
    )


def _expected(before: list[World], after: list[World], scale: int) -> dict:
    before_value = _observed(before, scale)
    after_value = _observed(after, scale)
    return {
        "relation": _relation(before, after),
        "before_observed": str(before_value),
        "after_observed": str(after_value),
        "gain": str(after_value - before_value),
        "before_observations": len({world.observation for world in before}),
        "after_observations": len({world.observation for world in after}),
        "same_bytes": sum(
            a.name == b.name and a.observation == b.observation
            for a, b in product(before, after)
        ),
        "worlds": len(before),
    }


def test_exhaustive_four_world_partition_simulation() -> None:
    partitions = _partitions(4)
    assert len(partitions) == 15
    relations = set()
    gains = set()
    for before_labels, after_labels in product(partitions, repeat=2):
        for tied in (False, True):
            before = _worlds(before_labels, tied, b"old")
            for prefix in (b"old", b"new"):
                after = _worlds(after_labels, tied, prefix)
                after = [
                    replace(w, scores=dict(reversed(tuple(w.scores.items()))))
                    for w in reversed(after)
                ]
                expected = _expected(before, after, 4)
                actual = compare(before, 4, after, 4)
                assert actual == expected, (before_labels, after_labels, tied, prefix)
                assert compare(after, 4, before, 4) == _expected(after, before, 4)
                relations.add(actual["relation"])
                gain = Fraction(actual["gain"])
                gains.add((gain > 0) - (gain < 0))
                if tied or actual["relation"] == "equivalent":
                    assert gain == 0
                if prefix == b"new":
                    assert actual["same_bytes"] == 0
                if actual["relation"] == "refinement":
                    assert gain >= 0
                if actual["relation"] == "coarsening":
                    assert gain <= 0
    assert relations == {"equivalent", "refinement", "coarsening", "incomparable"}
    assert gains == {-1, 0, 1}


def _error(
    before: list[World], before_scale: int, after: list[World], after_scale: int
) -> str:
    try:
        compare(before, before_scale, after, after_scale)
    except ValueError as error:
        return str(error)
    raise AssertionError("comparison accepted a nonmatching or invalid matrix")


def _invalid_matrices(secret: str) -> list[tuple[list[World], int, str | None]]:
    before = [World(f"{secret}-world", 1, b"a", {f"{secret}-action": 1})]
    world = before[0]
    action = next(iter(world.scores))
    return [
        ([replace(world, name=f"{secret}-other")], 1, None),
        (before + [replace(world, name=f"{secret}-extra")], 1, None),
        ([replace(world, mass=2)], 1, None),
        (before, 2, None),
        ([replace(world, scores={f"{secret}-other-action": 1})], 1, None),
        ([replace(world, scores={action: 0})], 1, None),
        ([replace(world, mass=True)], 1, "invalid world mass"),
        (before, True, "invalid scale"),
        ([replace(world, scores={action: True})], 1, "invalid score"),
        ([replace(world, scores={action: False})], 1, "invalid score"),
        ([replace(world, observation=bytearray(b"a"))], 1, "invalid observation"),
    ]


def test_frozen_matrix_rejection_simulation() -> None:
    messages = []
    for secret in ("private-alpha-731", "private-beta-942"):
        before = [World(f"{secret}-world", 1, b"a", {f"{secret}-action": 1})]
        current = []
        for after, scale, validation_message in _invalid_matrices(secret):
            forward = _error(before, 1, after, scale)
            backward = _error(after, scale, before, 1)
            assert forward and backward
            assert secret not in forward and secret not in backward
            if validation_message is not None:
                assert forward == backward == validation_message
            current.append((forward, backward))
        messages.append(current)
    assert messages[0] == messages[1]
