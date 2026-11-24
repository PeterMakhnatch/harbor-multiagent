"""Staged stops (stages.py): cumulative nested stages, the sequential McNemar test with Holm, and its error rates.

The fast tests run under pytest. The full simulation (100000 replicates per scenario by default, fixed seed) is not a
pytest test; it runs only when this file is run directly, and prints its assumptions and a markdown table:

    uvx --quiet --python 3.12 --with pytest --with pyyaml python flotilla/tests/test_stages.py [--reps N] [--seed S]

Pass --futility to compare the optional futility rules with the staged baseline on the same paths.
"""

from __future__ import annotations

import argparse
import bisect
import copy
import math
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from functools import cache
from itertools import pairwise

import pytest
from conftest import FLOTILLA, load

STAGES = load(FLOTILLA / "stages.py")
ALPHA = 0.05
FRACTIONS = (0.25, 0.5, 1.0)
SEED = 20261124
REPS = 100_000
FUTILITY_RULES = tuple((delta, beta_f) for delta in (0.15, 0.25, 0.4) for beta_f in (0.025, 0.05))

PLAN = {
    "name": "staged-test",
    "order_seed": 7,
    "models": ["vendor/model-a", "vendor/model-b"],
    "task_sets": [
        {"tasks_dir": "sets/x", "manifest": "sets/x/manifest.json", "instances": list(range(10)),
         "arms": ["talk-quiet", "talk-wrong", "talk-false"]},
        {"tasks_dir": "sets/y", "manifest": "sets/y/manifest.json", "instances": [3, 5, 8, 13, 21, 34, 55],
         "arms": ["talk-quiet", "talk-wrong"]},
        {"tasks_dir": "sets/z", "manifest": "sets/z/manifest.json", "instances": [0],
         "arms": ["talk-quiet", "talk-wrong"]},
    ],
}


def key(cell: dict) -> tuple:
    return cell["task_set"], cell["instance"], cell["arm"], cell["model"]


def full_grid(plan: dict) -> set[tuple]:
    return {(index, instance, arm, model) for index, task_set in enumerate(plan["task_sets"])
            for instance in task_set["instances"] for arm in task_set["arms"] for model in plan["models"]}


# --- stage_cells ---------------------------------------------------------------------------------------------------

def test_stages_are_cumulative_nested_and_end_with_every_cell_once():
    stages = STAGES.stage_cells(PLAN)
    assert len(stages) == len(FRACTIONS)
    for stage in stages:
        assert all(set(cell) == {"task_set", "instance", "arm", "model"} for cell in stage)
        assert len({key(cell) for cell in stage}) == len(stage)  # no cell twice in one stage
    for earlier, later in pairwise(stages):
        assert earlier == later[:len(earlier)]
    assert {key(cell) for cell in stages[-1]} == full_grid(PLAN)
    explicit = STAGES.stage_cells(PLAN, fractions=(0.1, 0.3, 0.6, 1.0))
    assert len(explicit) == 4
    for earlier, later in pairwise(explicit):
        assert earlier == later[:len(earlier)]
    assert {key(cell) for cell in explicit[-1]} == full_grid(PLAN)


def test_each_stage_takes_the_ceiling_share_of_every_set_in_whole_blocks_for_every_model():
    for fraction, stage in zip(FRACTIONS, STAGES.stage_cells(PLAN)):
        for index, task_set in enumerate(PLAN["task_sets"]):
            cells = [cell for cell in stage if cell["task_set"] == index]
            chosen = {cell["instance"] for cell in cells}
            assert len(chosen) == math.ceil(fraction * len(task_set["instances"]))
            assert chosen <= set(task_set["instances"])
            for model in PLAN["models"]:  # the same instances for every model, each with every arm
                assert {(cell["instance"], cell["arm"]) for cell in cells if cell["model"] == model} == {
                    (instance, arm) for instance in chosen for arm in task_set["arms"]}
    lone = [{key(cell) for cell in stage if cell["task_set"] == 2} for stage in STAGES.stage_cells(PLAN)]
    assert lone[0] == lone[1] == lone[2]  # one instance: every stage repeats the same snapshot


def test_stages_are_deterministic_and_share_one_shuffled_prefix():
    stages = STAGES.stage_cells(PLAN)
    assert STAGES.stage_cells(copy.deepcopy(PLAN)) == stages  # same cells in the same order
    halves = STAGES.stage_cells(PLAN, fractions=(0.5, 1.0))
    assert {key(cell) for cell in halves[0]} == {key(cell) for cell in stages[1]}
    assert {key(cell) for cell in halves[1]} == {key(cell) for cell in stages[2]}


# --- mcnemar_p and holm --------------------------------------------------------------------------------------------

def test_mcnemar_is_the_exact_two_sided_binomial_test():
    assert STAGES.mcnemar_p(0, 0) == 1.0
    assert STAGES.mcnemar_p(3, 3) == 1.0
    assert STAGES.mcnemar_p(0, 5) == pytest.approx(2 / 32)
    assert STAGES.mcnemar_p(0, 6) == pytest.approx(2 / 64)
    assert STAGES.mcnemar_p(1, 15) == pytest.approx(2 * 17 / 2 ** 16)
    assert STAGES.mcnemar_p(2, 9) == STAGES.mcnemar_p(9, 2)


def test_holm_adjusts_step_down_and_rejection_is_strict():
    assert STAGES.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert STAGES.holm([0.5, 0.9]) == pytest.approx([1.0, 1.0])
    assert STAGES.holm([0.001, 1.0, 1.0]) == pytest.approx([0.003, 1.0, 1.0])
    at_boundary = STAGES.holm([0.025, 0.025])
    assert at_boundary == [0.05, 0.05] and not any(p < ALPHA for p in at_boundary)


# --- SequentialTest ------------------------------------------------------------------------------------------------

def seq_p(looks: tuple[int, ...], history: list[tuple[int, int, int]]) -> float:
    return min(1.0, len(looks) * min(STAGES.mcnemar_p(b, c) for b, c, _ in history))


def test_early_efficacy_needs_family_size_times_sequential_p_below_alpha():
    looks = (8, 16, 32)  # rf1v Luna: 32 pairs, b = 32
    family = STAGES.SequentialTest(looks, family_size=3, alpha=ALPHA)
    first = family.disposition([(8, 0, 8)])  # p < alpha but 3 * p >= alpha: continue, and not futility either
    assert first.status == "continue" and first.p == pytest.approx(3 * 2 / 2 ** 8) and first.p < ALPHA
    second = family.disposition([(8, 0, 8), (16, 0, 16)])
    assert (second.status, second.direction) == ("efficacy", 1)
    assert second.p == pytest.approx(3 * 2 / 2 ** 16)
    alone = STAGES.SequentialTest(looks, family_size=1, alpha=ALPHA)
    assert alone.disposition([(8, 0, 8)]).status == "efficacy"
    assert alone.disposition([(0, 8, 8)]).direction == -1
    empty = alone.disposition([])
    assert (empty.status, empty.p, empty.direction) == ("continue", 1.0, 0)


def test_futility_only_when_the_majority_cannot_reach_alpha_by_the_last_look():
    test = STAGES.SequentialTest((10, 20, 30), family_size=1, alpha=ALPHA)
    assert test.disposition([(5, 5, 10)]).status == "continue"  # (5, 25) would still reach
    stopped = test.disposition([(5, 5, 10), (10, 10, 20)])  # (10, 20) at best: 3 * 0.099 >= 0.05
    assert (stopped.status, stopped.p, stopped.direction) == ("futility", 1.0, 0)
    path = [(3, 6, 10), (6, 13, 20)]  # p >= alpha now, but 10 more for c give (6, 23): 3 * 0.0023
    majority = test.disposition(path)
    assert majority.status == "continue" and majority.p == pytest.approx(seq_p((10, 20, 30), path))
    earliest = test.disposition([(0, 5, 10), (5, 5, 20)])  # minimum is at look 1; (5, 15) at best: 3 * 0.041
    assert (earliest.status, earliest.direction) == ("futility", -1)
    assert earliest.p == pytest.approx(3 * 2 / 32)
    early = STAGES.SequentialTest((20, 24, 28), family_size=1, alpha=ALPHA)
    assert early.disposition([(10, 10, 20)]).status == "futility"  # (10, 18) at best: 3 * 0.185
    quiet = STAGES.SequentialTest((12, 24, 42, 48), family_size=3, alpha=ALPHA)
    ties = [(0, 0, 12), (0, 0, 24), (0, 0, 40)]  # 6 pairs remain; the 2 invalid pairs cannot return
    assert [quiet.disposition(ties[:k]).status for k in (1, 2, 3)] == ["continue", "continue", "futility"]
    assert (quiet.disposition(ties).p, quiet.disposition(ties).direction) == (1.0, 0)
    default = STAGES.SequentialTest((12, 24, 48), family_size=3, alpha=ALPHA)
    path = [*ties[:2], (0, 0, 48)]
    assert [default.disposition(path[:k]).status for k in (1, 2, 3)] == ["continue", "continue", "final"]


def test_looks_after_a_terminal_status_replay_the_first_terminal_result():
    test = STAGES.SequentialTest((10, 20, 30), family_size=1, alpha=ALPHA)
    first = test.disposition([(0, 10, 10)])  # 3 * 2 / 1024 < 0.05: efficacy at look 1
    later = test.disposition([(0, 10, 10), (0, 11, 20)])
    assert first.status == "efficacy" and first.look == later.look == 1
    assert later == first
    early = STAGES.SequentialTest((20, 24, 28), family_size=1, alpha=ALPHA)
    stopped = early.disposition([(10, 10, 20), (10, 10, 24), (12, 12, 28)])
    assert (stopped.status, stopped.look) == ("futility", 1)


def test_the_last_look_is_final_whatever_its_p():
    looks = (10, 20, 30)
    test = STAGES.SequentialTest(looks, family_size=1, alpha=ALPHA)
    weak = [(1, 2, 10), (2, 4, 20), (3, 7, 30)]
    assert test.disposition(weak[:2]).status == "continue"
    result = test.disposition(weak)
    assert (result.status, result.direction) == ("final", -1) and result.p == pytest.approx(1.0)
    strong = [(0, 3, 10), (0, 6, 20), (0, 14, 30)]
    assert [test.disposition(strong[:k]).status for k in (1, 2)] == ["continue", "continue"]
    result = test.disposition(strong)
    assert (result.status, result.direction) == ("final", -1)
    assert result.p == pytest.approx(seq_p(looks, strong)) and result.p < ALPHA


def test_missing_pairs_do_not_remove_a_test_or_change_its_planned_looks():
    looks = (10, 20, 30)
    test = STAGES.SequentialTest(looks)
    path = [(0, 3, 9), (0, 6, 19), (0, 14, 29)]
    result = test.disposition(path)
    assert result == STAGES.Disposition("final", seq_p(looks, path), -1, 3, 29)
    assert result.p < ALPHA
    assert test.disposition([(0, 0, 0), (0, 0, 0), (0, 10, 10)]) == STAGES.Disposition(
        "final", 3 * 2 / 2 ** 10, -1, 3, 10,
    )


@pytest.mark.parametrize("history", [
    [(0, 1, 10), (0, 2, 20), (0, 3, 30), (0, 4, 40)],  # more looks than planned
    [(3, 2, 10), (2, 4, 20)],  # cumulative counts fall
    [(6, 4, 9)],  # more discordant pairs than valid pairs
    [(0, 0, 10), (6, 4, 19)],  # 10 new discordant pairs among 9 new valid pairs
    [(0, 0, 11)],  # more valid pairs than planned
    [(0, 0, 9), (0, 0, 20)],  # cannot recover a pair excluded at the first look
    [(0, 0, 10), (0, 0, 9)],  # valid pairs cannot decrease
    [(-1, 2, 10)],
    [(0, 0, True)],
    [(1, 2)],  # actual n is required
    {"b": 0, "c": 1},  # not a sequence of looks
])
def test_invalid_histories_are_rejected(history):
    with pytest.raises(ValueError):
        STAGES.SequentialTest((10, 20, 30), family_size=1, alpha=ALPHA).disposition(history)


@pytest.mark.parametrize("n", (32, 48))
@pytest.mark.parametrize(("delta", "beta_f"), FUTILITY_RULES)
def test_futility_bounds_are_the_largest_counts_at_or_below_the_exact_tail_limit(n, delta, beta_f):
    looks = STAGES.stage_counts(n)
    test = STAGES.SequentialTest(looks, delta=delta, beta_f=beta_f)
    rate, risk = Fraction(str(delta)), Fraction(str(beta_f))
    for size, bound in zip(looks, test.futility_bounds):
        masses = [math.comb(size, j) * rate**j * (1 - rate)**(size - j) for j in range(size + 1)]
        assert sum(masses[:bound + 1]) <= risk < sum(masses[:bound + 2])
    assert STAGES.SequentialTest((2,), delta=0.5, beta_f=0.25).futility_bounds == (0,)


@pytest.mark.parametrize("options", [
    {"delta": 0.25}, {"beta_f": 0.05}, {"delta": 0.0, "beta_f": 0.05}, {"delta": 0.25, "beta_f": 1.0},
    {"delta": True, "beta_f": 0.05}, {"delta": 0.25, "beta_f": float("nan")},
])
def test_invalid_futility_settings_are_rejected(options):
    with pytest.raises((TypeError, ValueError)):
        STAGES.SequentialTest((8, 16, 32), **options)


def test_futility_replays_the_first_stop_without_a_rejection_or_direction():
    looks = (12, 24, 48)
    baseline = STAGES.SequentialTest(looks, family_size=3)
    optional = STAGES.SequentialTest(looks, family_size=3, delta=0.25, beta_f=0.05)
    path = ((0, 0, 12), (6, 0, 24), (20, 0, 48))
    stopped = STAGES.Disposition("nonbinding_futility", 1.0, 0, 1, 12)
    assert optional.disposition(path[:1]) == optional.disposition(path) == stopped
    assert baseline.disposition(path).p < ALPHA / 3
    assert optional.disposition(((1, 0, 12),)).status == optional.disposition(((0, 1, 12),)).status == "continue"
    assert optional.disposition(((0, 12, 12),)) == baseline.disposition(((0, 12, 12),))
    assert STAGES.SequentialTest((12,), delta=0.25, beta_f=0.05).disposition(((0, 0, 12),)).status == "final"


def test_nonbinding_futility_uses_the_actual_valid_pair_count():
    test = STAGES.SequentialTest((12, 24, 48), delta=0.25, beta_f=0.04)
    assert test.disposition([(0, 0, 12)]).status == "nonbinding_futility"
    assert test.disposition([(0, 0, 11)]) == STAGES.Disposition("continue", 1.0, 0, 1, 11)


def test_nonbinding_futility_does_not_discard_a_staged_p_below_alpha():
    looks = (24, 36, 48)
    baseline = STAGES.SequentialTest(looks, family_size=3)
    optional = STAGES.SequentialTest(looks, family_size=3, delta=0.55, beta_f=0.05)
    path = ((8, 0, 24), (8, 0, 36), (8, 0, 48))
    assert optional.futility_bounds[0] >= 8
    first = optional.disposition(path[:1])
    assert first.status == "continue" and ALPHA / 3 < first.p < ALPHA / 2
    result = optional.disposition(path)
    assert result == baseline.disposition(path) == STAGES.Disposition("final", first.p, 1, 3, 48)
    assert all(p < ALPHA for p in STAGES.holm([0.001, result.p, 0.04]))


# --- simulation machinery ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Model:
    """Observed (b, c) counts to permute, or independent pairs with discordance rate q and share b_share for b."""

    name: str
    n: int
    profile: tuple[int, int] | None = None
    q: float = 0.0
    b_share: float = 0.5


@dataclass(frozen=True)
class Scenario:
    name: str
    null: bool
    models: tuple[Model, ...]
    fixed_ones: int = 0  # family members held at p = 1, not simulated

    @property
    def family_size(self) -> int:
        return len(self.models) + self.fixed_ones

    @property
    def looks(self) -> tuple[int, ...]:
        sizes = {model.n for model in self.models}
        assert len(sizes) == 1, self.name
        return tuple(STAGES.stage_counts(sizes.pop(), FRACTIONS))


@dataclass
class Row:
    scenario: str
    kind: str
    unit: str
    n: int
    looks: tuple[int, ...]
    reps: int
    pairs_staged: float
    pairs_exact: float | None
    pairs_fixed: int
    staged: float
    fixed: float
    agree: float
    exact_staged: float | None
    exact_fixed: float | None
    stops: Counter = field(default_factory=Counter)


Table = tuple[tuple[float, ...], tuple[float, ...], tuple[tuple[int, int, int], ...]]


def table(outcomes: dict[tuple[int, int, int], float]) -> Table:
    kept = [(step, p) for step, p in outcomes.items() if p > 0.0]
    probs = tuple(p for _, p in kept)
    cumulative, total = [], 0.0
    for p in probs:
        total += p
        cumulative.append(total)
    assert abs(total - 1.0) < 1e-9, total
    return probs, tuple(cumulative), tuple(step for step, _ in kept)


@cache
def iid_steps(q: float, m: int, b_share: float) -> Table:
    """(db, dc, dt) for m independent pairs, with probabilities q*b_share, q*(1-b_share), 1-q."""
    return table({(db, dc, m - db - dc): math.comb(m, db) * math.comb(m - db, dc) * (q * b_share)**db
                  * (q * (1 - b_share))**dc
                  * (1 - q)**(m - db - dc) for db in range(m + 1) for dc in range(m + 1 - db)})


@cache
def draw_steps(left: tuple[int, int, int], m: int) -> Table:
    """(db, dc, dt) for m pairs drawn without replacement from the remaining (b, c, tie) pairs: hypergeometric."""
    rb, rc, rt = left
    assert min(left) >= 0 and 0 <= m <= rb + rc + rt, (left, m)
    total = math.comb(rb + rc + rt, m)
    return table({(db, dc, m - db - dc): math.comb(rb, db) * math.comb(rc, dc) * math.comb(rt, m - db - dc) / total
                  for db in range(min(rb, m) + 1) for dc in range(min(rc, m - db) + 1) if m - db - dc <= rt})


def steps(model: Model, counts: tuple[int, int, int], m: int) -> Table:
    if model.profile is None:
        return iid_steps(model.q, m, model.b_share)
    b, c = model.profile
    return draw_steps((b - counts[0], c - counts[1], model.n - b - c - counts[2]), m)


def add(counts: tuple[int, int, int], step: tuple[int, int, int]) -> tuple[int, int, int]:
    return counts[0] + step[0], counts[1] + step[1], counts[2] + step[2]


@cache
def raw(b: int, c: int) -> float:
    return STAGES.mcnemar_p(b, c)


def trajectory(model: Model, looks: tuple[int, ...], rng: random.Random) -> tuple[tuple[int, int, int], ...]:
    """The cumulative (b, c, n) at every planned look: the whole run, drawn before either design reads it."""
    counts, done, path = (0, 0, 0), 0, []
    for size in looks:
        _, cumulative, outs = steps(model, counts, size - done)
        counts, done = add(counts, outs[min(bisect.bisect_right(cumulative, rng.random()), len(outs) - 1)]), size
        path.append((*counts[:2], size))
    return tuple(path)


class Staged:
    """SequentialTest dispositions look by look until a terminal status, cached per trajectory."""

    def __init__(self, test):
        self.test = test
        self.cache: dict[tuple, tuple[str, float, int]] = {}

    def stop(self, path: tuple[tuple[int, int, int], ...]) -> tuple[str, float, int]:
        """(status, p, pairs used)"""
        if path not in self.cache:
            result = self.test.disposition(path)
            assert result.status in ("efficacy", "futility", "nonbinding_futility", "final"), result
            self.cache[path] = (result.status, result.p, result.n)
        return self.cache[path]


def exact(model: Model, test) -> tuple[float, float, float]:
    """Exact (P staged family_size * p < alpha, P fixed family_size * p < alpha, E[pairs staged]) for one model, by
    enumerating the transition distributions: a staged run crosses at early efficacy or at the last look."""
    looks, family_size = test.look_sizes, test.family_size
    live: dict[tuple, float] = {((0, 0, 0), ()): 1.0}
    crossed = pairs = settled = 0.0
    done = 0
    for size in looks[:-1]:
        grown: dict[tuple, float] = defaultdict(float)
        for (counts, path), mass in live.items():
            probs, _, outs = steps(model, counts, size - done)
            for p, step in zip(probs, outs):
                after = add(counts, step)
                grown[(after, path + ((*after[:2], size),))] += mass * p
        live, done = {}, size
        for (counts, path), mass in grown.items():
            status = test.disposition(list(path)).status
            assert status in ("continue", "efficacy", "futility", "nonbinding_futility"), status
            if status == "continue":
                live[(counts, path)] = mass
                continue
            settled, pairs = settled + mass, pairs + mass * size
            crossed += mass if status == "efficacy" else 0.0
    final: dict[tuple[int, int, int], float] = defaultdict(float)
    for (counts, _), mass in live.items():
        final[counts] += mass  # not crossed before: the last look crosses iff its own raw p does
    for counts, mass in final.items():
        probs, _, outs = steps(model, counts, looks[-1] - done)
        for p, step in zip(probs, outs):
            b, c, _ = add(counts, step)
            settled, pairs = settled + mass * p, pairs + mass * p * looks[-1]
            crossed += mass * p if family_size * len(looks) * raw(b, c) < test.alpha else 0.0
    probs, _, outs = steps(model, (0, 0, 0), looks[-1])
    fixed = sum(p for p, (b, c, _) in zip(probs, outs) if family_size * raw(b, c) < test.alpha)
    assert abs(settled - 1.0) < 1e-9, settled
    return crossed, fixed, pairs


def simulate(scenario: Scenario, reps: int, seed: int, **futility) -> list[Row]:
    """One row per model and one family row (any rejection) from reps seeded replicates, with exact columns."""
    assert reps > 0
    looks, models, m = scenario.looks, scenario.models, scenario.family_size
    rng = random.Random(seed)
    staged = Staged(STAGES.SequentialTest(looks, family_size=m, alpha=ALPHA, **futility))
    pairs = [0] * len(models)
    rejected = [[0, 0, 0] for _ in models]  # staged, fixed, agree
    family = [0, 0, 0]
    stops = [Counter() for _ in models]
    extras = [1.0] * scenario.fixed_ones
    for _ in range(reps):
        staged_p, fixed_p = [], []
        for i, model in enumerate(models):
            path = trajectory(model, looks, rng)
            status, p, used = staged.stop(path)
            staged_p.append(p)
            fixed_p.append(raw(*path[-1][:2]))
            pairs[i] += used
            stops[i][status] += 1
        s = [p < ALPHA for p in STAGES.holm(staged_p + extras)][:len(models)]
        f = [p < ALPHA for p in STAGES.holm(fixed_p + extras)][:len(models)]
        for i in range(len(models)):
            rejected[i][0] += s[i]
            rejected[i][1] += f[i]
            rejected[i][2] += s[i] == f[i]
        family[0] += any(s)
        family[1] += any(f)
        family[2] += s == f
    kind = "null" if scenario.null else "effect"
    rows, survive_staged, survive_fixed = [], 1.0, 1.0
    for i, model in enumerate(models):
        cross_staged, cross_fixed, expected = exact(model, staged.test)
        survive_staged, survive_fixed = survive_staged * (1 - cross_staged), survive_fixed * (1 - cross_fixed)
        rows.append(Row(scenario.name, kind, model.name, model.n, looks, reps, pairs[i] / reps, expected, model.n,
                        rejected[i][0] / reps, rejected[i][1] / reps, rejected[i][2] / reps, None, None, stops[i]))
    unit = "family" + (f" (+{scenario.fixed_ones} fixed p=1)" if scenario.fixed_ones else "")
    rows.append(Row(scenario.name, kind, unit, models[0].n, looks, reps, sum(pairs) / reps,
                    sum(row.pairs_exact for row in rows), sum(model.n for model in models), family[0] / reps,
                    family[1] / reps, family[2] / reps, 1 - survive_staged, 1 - survive_fixed, sum(stops, Counter())))
    return rows


def test_seeded_null_simulation_matches_the_exact_rates_and_stays_under_alpha():
    reps = 4000
    scenario = Scenario("null q=0.5", True, tuple(Model(name, 24, None, 0.5) for name in "abc"))
    rows = simulate(scenario, reps, SEED)
    family = rows[-1]
    assert family.exact_staged <= ALPHA and family.exact_fixed <= ALPHA
    for simulated, truth in ((family.staged, family.exact_staged), (family.fixed, family.exact_fixed)):
        assert abs(simulated - truth) <= 5 * math.sqrt(max(truth, 1e-3) / reps)
    assert family.staged <= ALPHA + 4 * math.sqrt(ALPHA * (1 - ALPHA) / reps)
    for row in rows[:-1]:
        assert abs(row.pairs_staged - row.pairs_exact) <= 5 * 24 / math.sqrt(reps)
    alone = exact(Model("a", 32, None, 0.5), STAGES.SequentialTest((8, 16, 32), alpha=ALPHA))
    assert alone[0] <= ALPHA and alone[1] <= ALPHA


def test_permuted_profiles_keep_their_totals_and_rf1v_stops_at_the_second_look():
    rng = random.Random(SEED)
    for model in SCENARIOS[1].models:
        for _ in range(50):
            assert trajectory(model, SCENARIOS[1].looks, rng)[-1] == (*model.profile, model.n)
    rows = simulate(SCENARIOS[0], 200, SEED)
    assert [(row.pairs_staged, row.staged, row.fixed, row.agree) for row in rows] == [(16, 1, 1, 1)] * 2
    assert rows[0].stops == Counter(efficacy=200) and rows[-1].exact_staged == pytest.approx(1.0)


SCENARIOS = (
    Scenario("rf1v Luna observed (32, 0)", False, (Model("Luna", 32, (32, 0)),), fixed_ones=2),
    Scenario("au1 observed", False, (Model("Haiku", 40, (1, 15)), Model("DeepSeek", 40, (1, 14)),
                                     Model("Luna", 40, (0, 20)))),
    Scenario("null ties (all pairs tied)", True, tuple(Model(name, 48, (0, 0)) for name in ("A", "B", "C"))),
    *(Scenario(f"null discordance q={q}", True, tuple(Model(name, 48, None, q) for name in ("A", "B", "C")))
      for q in (0.1, 0.2, 0.3, 0.4, 0.5)),
)


def test_futility_only_removes_holm_rejections_on_paired_seeded_trajectories():
    looks = (12, 24, 48)
    models = (Model("null", 48, q=0.3), Model("near", 48, q=0.25, b_share=1.0),
              Model("strong", 48, q=0.4, b_share=0.0))
    baseline = STAGES.SequentialTest(looks, family_size=3)
    tests = [STAGES.SequentialTest(looks, family_size=3, delta=d, beta_f=b) for d, b in FUTILITY_RULES]
    rng = random.Random(SEED)
    baseline_rejections = removed_rejections = 0
    for _ in range(300):
        paths = [trajectory(model, looks, rng) for model in models]
        before = [baseline.disposition(path) for path in paths]
        reject = [p < ALPHA for p in STAGES.holm([result.p for result in before])]
        baseline_rejections += sum(reject)
        for test in tests:
            after = [test.disposition(path) for path in paths]
            assert all(new.p >= old.p and new.look <= old.look for old, new in zip(before, after))
            with_futility = [p < ALPHA for p in STAGES.holm([result.p for result in after])]
            assert all(not new or old for old, new in zip(reject, with_futility))
            removed_rejections += sum(reject) - sum(with_futility)
    assert baseline_rejections > 0 and removed_rejections > 0


# --- full simulation (run this file directly) ----------------------------------------------------------------------

def outcome_text(scenario: Scenario) -> str:
    model = scenario.models[0]
    if model.profile is None:
        return (f"{len(scenario.models)} independent models, n={model.n} each; every pair discordant with "
                f"q={model.q}, then b or c with probability 1/2 (null)")
    profiles = ", ".join(f"{m.name} (b={m.profile[0]}, c={m.profile[1]}, ties={m.n - sum(m.profile)})"
                         for m in scenario.models)
    extra = f"; {scenario.fixed_ones} further family members held at p=1" if scenario.fixed_ones else ""
    return f"observed profiles permuted without replacement, independently per model, n={model.n}: {profiles}{extra}"


def pct(value: float | None, places: int) -> str:
    return "—" if value is None else f"{100 * value:.{places}f}%"


def print_report(rows: list[Row], reps: int, seed: int) -> None:
    print("## Staged-stop simulation")
    print()
    print(f"- Replicates: {reps} per scenario. Seed: {seed}; random.Random(seed) restarts for each scenario.")
    print(f"- Looks: cumulative pairs ceil(f * n) for f in {FRACTIONS}; K = {len(FRACTIONS)} looks; alpha = {ALPHA}.")
    print("- Each replicate draws a whole run (cumulative b, c at every look) once; the staged and fixed designs read "
          "the same outcomes.")
    print("- Staged: SequentialTest(looks, family_size=m) look by look until efficacy, futility or the final look; "
          "then Holm over the terminal p values, reject when adjusted p < alpha.")
    print("- Fixed: Holm over raw exact McNemar p at the full n, reject when adjusted p < alpha.")
    print("- Family rate: any rejection in the family (FWER for null scenarios). Agreement: same decision "
          "(per model), or the same decision vector (family).")
    print("- Exact columns (family rows): 1 - prod(1 - P(m * p < alpha)) over independent models, by enumeration of "
          "the transition distributions; E[pairs] exact per model.")
    print("- Null and effect rows: the rate is type I only for null scenarios; effect rows report the rejection "
          "rate, not type I.")
    for scenario in SCENARIOS:
        print(f"- {scenario.name}: {outcome_text(scenario)}.")
    print()
    print("| scenario | kind | unit | n | looks | reps | E[pairs] staged | E[pairs] exact | pairs fixed | staged rate "
          "| fixed rate | agreement | exact staged | exact fixed | stops eff/fut/final | rate is |")
    print("|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for row in rows:
        family = row.unit.startswith("family")
        meaning = ("FWER (type I)" if family else "type I (Holm)") if row.kind == "null" else \
            ("any rejection, not type I" if family else "rejection, not type I")
        total = sum(row.stops.values())
        stops = "/".join(f"{100 * row.stops[status] / total:.1f}" for status in ("efficacy", "futility", "final"))
        print(f"| {row.scenario} | {row.kind} | {row.unit} | {row.n} | {'/'.join(map(str, row.looks))} | {row.reps} "
              f"| {row.pairs_staged:.3f} | {row.pairs_exact:.3f} | {row.pairs_fixed} | {pct(row.staged, 3)} "
              f"| {pct(row.fixed, 3)} | {pct(row.agree, 3)} | {pct(row.exact_staged, 6)} | {pct(row.exact_fixed, 6)} "
              f"| {stops} | {meaning} |")


def futility_metrics(scenario: Scenario, reps: int, seed: int, **futility) -> dict[str, str]:
    rows = simulate(scenario, reps, seed, **futility)
    family, name = rows[-1], scenario.name
    if scenario.null or len(scenario.models) == 1:
        pairs = family.pairs_exact / len(scenario.models)
        return {name: f"{pairs:.2f} / {pct(family.exact_staged, 3)}"}
    return {
        **{f"{name} {row.unit}": f"{row.pairs_exact:.2f} / {pct(row.staged, 3)}" for row in rows[:-1]},
        f"{name} family (total)": f"{family.pairs_exact:.2f} / {pct(family.exact_staged, 3)}",
    }


def print_futility_report(reps: int, seed: int) -> None:
    synthetic = tuple(Scenario(f"one-way {q} (48 pairs)", False, (Model("effect", 48, q=q, b_share=1.0),),
                               fixed_ones=2) for q in (0.15, 0.25, 0.4, 0.6))
    labels = ["Off", *(f"{d}/{b}" for d, b in FUTILITY_RULES)]
    options = [{}, *({"delta": d, "beta_f": b} for d, b in FUTILITY_RULES)]
    print("## Exact futility bounds (last look shown but not applied)")
    print("| n | looks | " + " | ".join(labels[1:]) + " |")
    print("|---" * (len(labels) + 1) + "|")
    for n in (32, 48):
        looks = STAGES.stage_counts(n)
        bounds = [STAGES.SequentialTest(looks, **rule).futility_bounds for rule in options[1:]]
        print(f"| {n} | {'/'.join(map(str, looks))} | " + " | ".join("/".join(map(str, b)) for b in bounds) + " |")
    print(f"\n## Expected pairs / Holm rejection rate ({reps:,} replicates per scenario, seed {seed})")
    print("Each row uses a 3-test family and looks at 25%, 50%, and 100% of the planned pairs.")
    print("Expected pairs are exact. The au1 family row sums pairs. Rates are exact except for au1 model rows.")
    print("Null rates are family-wise error; the au1 family rate is power to reject at least one test.")
    print("For Luna rf1v and the one-way profiles, the other 2 members have p = 1.")
    columns: list[dict[str, str]] = [{} for _ in options]
    for scenario in (*SCENARIOS[2:], *synthetic, *SCENARIOS[:2]):
        for column, rule in zip(columns, options):
            column.update(futility_metrics(scenario, reps, seed, **rule))
    print("\n| Profile | " + " | ".join(labels) + " |")
    print("|---" * (len(labels) + 1) + "|")
    for name in columns[0]:
        print(f"| {name} | " + " | ".join(column[name] for column in columns) + " |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reps", type=int, default=REPS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--futility", action="store_true", help="compare futility rules with the staged baseline")
    args = parser.parse_args(argv)
    if args.reps < 1:
        parser.error("--reps must be at least 1")
    if args.futility:
        print_futility_report(args.reps, args.seed)
    else:
        rows = [row for scenario in SCENARIOS for row in simulate(scenario, args.reps, args.seed)]
        print_report(rows, args.reps, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
