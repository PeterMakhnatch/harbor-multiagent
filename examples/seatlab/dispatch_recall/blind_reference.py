from fractions import Fraction
from math import comb
from typing import Any

from ...native_study import require

SCHEMA = "harbor.dispatch-recall.blind-reference/v1"
ROLE = "calibration_not_primary"
METRIC = "team_reward"
WORLDS = ("stay", "move")
ARMS = ("deferred", "withheld")
STATUSES = ("exceeded", "not_detected", "not_testable")
ALPHA_TOTAL = Fraction(1, 20)
ALPHA = ALPHA_TOTAL / len(ARMS)
MAX_SEEDS = 4096
MIN_BLOCKS = next(n for n in range(1, 64) if Fraction(1, 2**n) <= ALPHA)
ASSUMPTIONS = (
    (
        "Each assigned cell is a separate trial; in deferred and withheld the loader "
        "sees the same bytes before commit in stay and move, as certify checks per seed."
    ),
    (
        "With no information before commit, any loader policy gives E[stay + move] <= 1 "
        "per arm and seed: committing the initial bay wins only stay, the other bay only "
        "move, and an abstention wins neither."
    ),
    (
        "For independent cells P(stay + move = 2) <= P(stay + move = 0), so a one-sided "
        "exact binomial sign test at 1/2 over nonzero seed scores is conservative."
    ),
    "Wrong commits and abstentions score 0 and can only lower a seed score.",
    "The total alpha of 1/20 is split equally: one-sided 1/40 for each blind arm.",
)
LIMITS = (
    (
        "A calibration check of the two blind arms, not a new primary or secondary "
        "outcome."
    ),
    (
        "exceeded means native team reward above the no-information bound even with "
        "every missing reward set to 0; it does not identify the cause and does not "
        "prove out-of-band communication."
    ),
    (
        "not_detected is not validation of isolation or evidence of no leak; it is the "
        "absence of a detected excess at this alpha."
    ),
    (
        "not_testable means too few planned blocks, or missing rewards that could decide "
        "the status."
    ),
)


def _frac(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _value(
    cells: dict[tuple[int, str, str], dict[str, Any]], seed: int, world: str, arm: str
) -> int | None:
    record = cells.get((seed, world, arm))
    require(
        type(record) is dict and METRIC in record,
        f"no {METRIC} cell for seed {seed} {world} {arm}",
    )
    value = record[METRIC]
    require(
        value is None or (type(value) is int and value in (0, 1)),
        f"{METRIC} must be 0, 1 or None for seed {seed} {world} {arm}",
    )
    return value


def _configuration(scores: list[int]) -> tuple[dict[str, Any], Fraction]:
    successes = sum(score > 0 for score in scores)
    failures = sum(score < 0 for score in scores)
    trials = successes + failures
    tail = sum(comb(trials, k) for k in range(successes, trials + 1))
    p = Fraction(tail, 2**trials)
    assert 0 < p <= 1
    summary = {
        "successes": successes,
        "ties": len(scores) - trials,
        "failures": failures,
        "one_sided_p": _frac(p),
    }
    return summary, p


def _arm(
    seeds: list[int], cells: dict[tuple[int, str, str], dict[str, Any]], arm: str
) -> dict[str, Any]:
    lower: list[int] = []
    upper: list[int] = []
    missing = 0
    complete = 0
    for seed in seeds:
        values = [_value(cells, seed, world, arm) for world in WORLDS]
        unknown = values.count(None)
        missing += unknown
        complete += unknown == 0
        lower.append(sum(0 if value is None else value for value in values) - 1)
        upper.append(sum(1 if value is None else value for value in values) - 1)
    low, p_low = _configuration(lower)
    high, p_high = _configuration(upper)
    assert p_high <= p_low
    if p_low <= ALPHA:
        status = "exceeded"
    elif len(seeds) < MIN_BLOCKS or p_high <= ALPHA:
        status = "not_testable"
    else:
        status = "not_detected"
    assert status in STATUSES
    return {
        "timing": arm,
        "status": status,
        "planned_blocks": len(seeds),
        "complete_blocks": complete,
        "incomplete_blocks": len(seeds) - complete,
        "missing_cells": missing,
        "lower": low,
        "upper": high,
    }


def blind_reference(
    seeds: list[int], cells: dict[tuple[int, str, str], dict[str, Any]]
) -> dict[str, Any]:
    require(
        type(seeds) is list and len(seeds) <= MAX_SEEDS, "seeds must be a bounded list"
    )
    require(
        all(type(seed) is int and 0 <= seed < 2**63 for seed in seeds), "invalid seed"
    )
    require(len(set(seeds)) == len(seeds), "duplicate seed")
    require(type(cells) is dict, "cells must map (seed, world, timing) to records")
    arms = {arm: _arm(seeds, cells, arm) for arm in ARMS}
    return {
        "schema": SCHEMA,
        "role": ROLE,
        "metric": METRIC,
        "alpha_total": _frac(ALPHA_TOTAL),
        "alpha_per_arm": _frac(ALPHA),
        "min_blocks": MIN_BLOCKS,
        "planned_blocks": len(seeds),
        "exceeded": any(arms[arm]["status"] == "exceeded" for arm in ARMS),
        "arms": arms,
        "assumptions": list(ASSUMPTIONS),
        "limits": list(LIMITS),
    }
