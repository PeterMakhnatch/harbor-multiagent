import json
from typing import Any

import pytest

from examples.seatlab.visibility import feedback_protocol as protocol
from examples.seatlab.visibility.feedback_summary import summarize

_BAYS = "ABCD"
_DESKS = "XY"


def _observed(
    block: protocol.Block,
    decisions: dict[tuple[str, int], bool],
    *,
    stale_recent_with_exact_report: bool = False,
) -> list[dict[str, Any]]:
    shipments = protocol.shipments(block.seed)

    def ask(turn: protocol.Turn) -> str | None:
        shipment = shipments[turn.step]
        current = (block.seed + block.rep) % 2
        if block.regime == "switch" and turn.step >= 6:
            current ^= 1
        desk = current
        if turn.arm == "warm":
            desk = (((block.seed - 5201) // 2 + block.rep) % 2) ^ turn.step
        override = stale_recent_with_exact_report and (turn.arm, turn.step) == (
            "recent",
            6,
        )
        if override:
            desk ^= 1
        if turn.stage == "select":
            return json.dumps({"ask": _DESKS[desk]})
        if turn.stage == "review":
            stock = shipment.after if desk == current or override else shipment.before
            return json.dumps({"available": list(stock)})
        optimal = True if turn.step < 6 else decisions.get((turn.arm, turn.step))
        if optimal is None:
            return None
        best = max(shipment.after, key=lambda bay: shipment.utilities[_BAYS.index(bay)])
        chosen = (
            best if optimal else next(bay for bay in _BAYS if bay not in shipment.after)
        )
        return json.dumps({"bay": chosen})

    return protocol.episode(block, ask)


def _register(
    seeds: tuple[int, ...], observed: dict[protocol.Block, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    return [
        {
            "seed": block.seed,
            "regime": block.regime,
            "rep": block.rep,
            "block": block.key,
            "state": "claimed" if block in observed else "unclaimed",
            "rows": observed.get(block, []),
        }
        for block in protocol.grid(seeds)
    ]


def test_point_estimate_weights_seeds_not_pair_counts() -> None:
    first = protocol.Block(5201, "switch", 0)
    observed = {first: _observed(first, {("lifetime", 6): False, ("recent", 6): True})}
    for rep in (0, 1):
        block = protocol.Block(5202, "switch", rep)
        observed[block] = _observed(
            block,
            {
                (arm, step): arm == "lifetime"
                for arm in protocol.ARMS
                for step in range(6, 10)
            },
        )
    primary = summarize(_register((5201, 5202), observed))["primary"]
    assert primary["cluster_unit"] == "seed"
    assert primary["enrolled_seeds"] == primary["seeds_with_pairs"] == 2
    assert primary["scheduled_pairs"] == 16
    assert primary["complete_pairs"] == 9
    assert [seed["paired_complete_delta"] for seed in primary["by_seed"]] == [1, -1]
    assert primary["paired_complete_delta"] == 0


def test_seed_without_pairs_keeps_full_point_estimate_unknown() -> None:
    block = protocol.Block(5201, "switch", 0)
    rows = _observed(block, {("lifetime", 6): False, ("recent", 6): True})
    primary = summarize(_register((5201, 5202), {block: rows}))["primary"]
    assert primary["seeds_with_pairs"] == primary["complete_pairs"] == 1
    assert primary["scheduled_pairs"] == 16
    assert primary["paired_complete_delta"] is None
    assert primary["worst_delta"] == -14 / 16
    assert primary["best_delta"] == 1


@pytest.mark.parametrize(
    ("known", "worst", "best"), [(True, -1, 7 / 8), (False, -7 / 8, 1)]
)
def test_known_lifetime_choice_tightens_one_side_of_bound(
    known: bool, worst: float, best: float
) -> None:
    block = protocol.Block(5201, "switch", 0)
    rows = _observed(block, {("lifetime", 6): known})
    primary = summarize(_register((block.seed,), {block: rows}))["primary"]
    assert primary["complete_pairs"] == 0
    assert primary["paired_complete_delta"] is None
    assert primary["worst_delta"] == worst
    assert primary["best_delta"] == best


def test_no_substitution_across_repeats_or_from_review_and_selection() -> None:
    left = protocol.Block(5201, "switch", 0)
    right = protocol.Block(5201, "switch", 1)
    observed = {
        left: _observed(
            left, {("lifetime", 6): True}, stale_recent_with_exact_report=True
        ),
        right: _observed(right, {("recent", 6): False}),
    }
    result = summarize(_register((left.seed,), observed))
    primary = result["primary"]
    assert primary["complete_pairs"] == 0
    assert primary["paired_complete_delta"] is None
    assert primary["best_delta"] == 6 / 8
    recovery = result["recovery"]["switch"]["recent"][0]
    assert recovery["step"] == 6
    assert recovery["select_current_desk"] == {
        "observed": 2,
        "positive": 1,
        "rate": 0.5,
    }
    assert recovery["report_matches_current"] == {
        "observed": 2,
        "positive": 2,
        "rate": 1,
    }
    assert recovery["report_matches_assigned"] == {
        "observed": 2,
        "positive": 1,
        "rate": 0.5,
    }
    assert recovery["choose_optimal"] == {"observed": 1, "positive": 0, "rate": 0}
    # Both repeats have all four pre-cutoff choices. No post-cutoff choice
    # has an observed counterpart in the other repeat.
    assert result["repeat_drift"]["switch"]["recent"]["final_bay_differs"] == {
        "observed": 4,
        "positive": 0,
        "rate": 0,
    }


def test_rejects_incomplete_register_and_scores_without_an_observed_bay() -> None:
    block = protocol.Block(5201, "switch", 0)
    rows = _observed(block, {("lifetime", 6): True})
    register = _register((block.seed,), {block: rows})
    with pytest.raises(ValueError, match="incomplete feedback register"):
        summarize(register[:-1])
    missing = next(row for row in rows if row["arm"] == "recent" and row["step"] == 6)
    missing["optimal"] = False
    with pytest.raises(ValueError, match="unobserved feedback choice"):
        summarize(register)
    missing["optimal"] = None
    complete = next(
        row for row in rows if row["arm"] == "lifetime" and row["step"] == 6
    )
    complete["optimal"] = False
    with pytest.raises(ValueError, match="disagrees with stock score"):
        summarize(register)
