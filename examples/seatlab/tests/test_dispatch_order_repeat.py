import json
from dataclasses import replace
from hashlib import sha256
from typing import cast

import pytest

from examples.seatlab.capsule import view_hash
from examples.seatlab.dispatch_order_pair import OrderArm, OrderPair, build_pair
from examples.seatlab.dispatch_order_repeat import (
    allocate,
    equal_world_excess,
    repeat_arm,
    score_repeats,
)

_SOURCE = sha256(b"dispatch order repeat fixture").hexdigest()
_SCHEMA = "seatlab.dispatch-order-repeat/v1"
_COUNTS = {
    "shipments": 3,
    "informative": 2,
    "within_comparisons": 6,
    "within_disagreements": 6,
    "cross_comparisons": 12,
    "cross_disagreements": 8,
    "informative_within_disagreements": 4,
    "informative_cross_disagreements": 6,
    "chronological_latest": 3,
    "reversed_latest": 2,
    "chronological_optimal": 2,
    "reversed_optimal": 1,
    "chronological_feasible": 3,
    "reversed_feasible": 2 + 1,
}


def _pair(block: int, *, shipments: int = 3) -> OrderPair:
    cases = [
        {
            "id": subject,
            "utilities": {"A": 4, "B": 3, "C": 2, "D": 1},
            "before": list(rev1),
            "after": list(after),
            "reports": [
                {"revision": 1, "available": list(rev1)},
                {"revision": 2, "available": list(rev2)},
            ],
        }
        for subject, (rev1, rev2, after) in enumerate(
            (("AB", "CD", "AB"), ("AD", "AC", "AB"), ("AD", "BD", "CD"))[:shipments]
        )
    ]
    return build_pair(block, _SOURCE, cases)


def _arms(pair: OrderPair) -> tuple[OrderArm, OrderArm, OrderArm, OrderArm]:
    return (
        pair.chronological,
        pair.reversed,
        repeat_arm(pair.chronological),
        repeat_arm(pair.reversed),
    )


def _observed(text: str) -> tuple[str, str]:
    return text, sha256(text.encode("utf-8")).hexdigest()


def _replies(
    pair: OrderPair, choices: tuple[str, str, str, str] = ("CAA", "BBA", "ABB", "DAB")
) -> dict[str, tuple[str, str]]:
    return {
        arm.id: _observed(
            json.dumps({"rows": [{"id": i, "bay": bay} for i, bay in enumerate(bays)]})
        )
        for arm, bays in zip(_arms(pair), choices, strict=True)
    }


@pytest.mark.parametrize(("block", "order"), [(4, (0, 1, 3, 2)), (5, (1, 0, 2, 3))])
def test_repeat_keeps_prompt_and_allocates_abba(
    block: int, order: tuple[int, ...]
) -> None:
    pair = _pair(block)
    arms = _arms(pair)
    for original, repeat in zip(arms[:2], arms[2:], strict=True):
        assert repeat.messages == original.messages
        assert repeat.prompt_sha256 == original.prompt_sha256
        assert repeat.id == view_hash(
            ["seatlab.dispatch-order-independent-repeat/v1", original.id]
        )
    waves = [allocate(pair, wave) for wave in range(4)]
    assert waves == [arms[index] for index in order]
    assert len({arm.id for arm in waves}) == 4


@pytest.mark.parametrize("wave", [-1, 4, False, True, 1.0, "2"])
def test_allocate_rejects_non_wave_values(wave: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        allocate(_pair(4), cast(int, wave))


@pytest.mark.parametrize(
    ("choices", "within", "cross"),
    [
        (("A", "B", "A", "C"), 1, 4),
        (("A", "B", "C", "B"), 1, 4),
        (("A", "A", "B", "C"), 2, 3),
        (("A", "B", "C", "A"), 2, 3),
        (("A", "B", "B", "C"), 2, 3),
        (("A", "B", "C", "C"), 2, 3),
    ],
)
def test_each_pairwise_comparison_contributes_once(
    choices: tuple[str, str, str, str], within: int, cross: int
) -> None:
    pair = _pair(4, shipments=1)
    counts = score_repeats([pair], _replies(pair, choices))["complete_totals"]
    assert counts["within_comparisons"] == 2
    assert counts["cross_comparisons"] == 4
    assert counts["within_disagreements"] == within
    assert counts["cross_disagreements"] == cross
    assert counts["informative_within_disagreements"] == within
    assert counts["informative_cross_disagreements"] == cross


def test_complete_world_separates_report_accuracy_from_stock_and_has_negative_excess() -> (
    None
):
    pair = _pair(4)
    result = score_repeats([pair], _replies(pair))
    assert result == {
        "schema": _SCHEMA,
        "coverage": {
            "expected_worlds": 1,
            "expected_replies": 4,
            "valid": 4,
            "invalid": 0,
            "missing": 0,
            "complete_worlds": 1,
        },
        "worlds": [{"block": 4, "states": ["valid"] * 4, "counts": _COUNTS}],
        "complete_totals": _COUNTS,
    }
    counts = result["complete_totals"]
    assert (
        counts["cross_disagreements"] * counts["within_comparisons"]
        < counts["within_disagreements"] * counts["cross_comparisons"]
    )


def test_incomplete_world_retains_coverage_but_does_not_score() -> None:
    first, partial, last = (_pair(block) for block in (4, 5, 6))
    c0, r0, _, r1 = _arms(partial)
    partial_replies = {
        c0.id: _observed('{"rows":[]}'),
        r0.id: _replies(partial)[r0.id],
        r1.id: _replies(partial)[r1.id],
    }
    partial_world = {
        "block": 5,
        "states": ["invalid", "valid", "missing", "valid"],
        "counts": None,
    }
    mixed = score_repeats(
        [last, partial, first], _replies(last) | partial_replies | _replies(first)
    )
    assert mixed["worlds"] == [
        {"block": 4, "states": ["valid"] * 4, "counts": _COUNTS},
        partial_world,
        {"block": 6, "states": ["valid"] * 4, "counts": _COUNTS},
    ]
    assert mixed["coverage"] == {
        "expected_worlds": 3,
        "expected_replies": 12,
        "valid": 10,
        "invalid": 1,
        "missing": 1,
        "complete_worlds": 2,
    }
    assert mixed["complete_totals"] == {
        key: 2 * value for key, value in _COUNTS.items()
    }
    partial_only = score_repeats([partial], partial_replies)
    assert partial_only["worlds"] == [partial_world]
    assert partial_only["complete_totals"] is None


def test_replayed_receipt_route_cannot_score_twice() -> None:
    first, second = _pair(4), _pair(5)
    replies = _replies(first)
    with pytest.raises(ValueError):
        score_repeats([first, replace(second, block=first.block)], replies)
    for reused in (first.chronological, repeat_arm(first.chronological)):
        replay = replace(second, chronological=reused)
        with pytest.raises(ValueError):
            score_repeats([first, replay], replies)
    with pytest.raises(ValueError):
        score_repeats([second], replies)


def test_equal_world_weight_is_not_a_pooled_shipment_weight() -> None:
    long, short = _pair(4), _pair(5, shipments=1)
    replies = _replies(long) | _replies(short, ("A", "B", "A", "B"))
    mixed = equal_world_excess(score_repeats([long, short], replies))
    assert mixed == {
        "worlds": 2,
        "informative_worlds": 2,
        "all": {"numerator": 1, "denominator": 3},
        "informative": {"numerator": 3, "denominator": 8},
    }
    del replies[short.reversed.id]
    assert all(
        value is None
        for value in equal_world_excess(score_repeats([long, short], replies)).values()
    )


def test_world_with_no_different_recommendation_has_no_informative_rate() -> None:
    case = _pair(5, shipments=1).cases[0]
    case["reports"][1]["available"] = case["reports"][0]["available"].copy()
    pair = build_pair(5, _SOURCE, [case])
    result = equal_world_excess(
        score_repeats([pair], _replies(pair, ("A", "A", "B", "B")))
    )
    assert result == {
        "worlds": 1,
        "informative_worlds": 0,
        "all": {"numerator": -1, "denominator": 2},
        "informative": None,
    }
