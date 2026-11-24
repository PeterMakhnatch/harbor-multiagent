"""Staged stopping for Flotilla waves: cumulative stage cells and group-sequential exact McNemar tests.

stage_cells(plan, fractions) splits a models plan into cumulative stages. Each task set's instances are shuffled once
with the plan's order_seed. Stage k holds the first ceil(fraction_k * n_set) shuffled instances of every set, as whole
blocks: every arm with every model. The ceiling uses the exact decimal value of each fraction. Stage k is a literal
prefix of stage k+1, and every model sees the same instances. Rounding can make two consecutive stages equal; a test's
look sizes are then the distinct planned complete pair counts, e.g. sorted(set(stage_counts(n, fractions))).

SequentialTest(look_sizes, family_size, alpha) runs one exact McNemar test at K = len(look_sizes) fixed looks. Each
look's history entry is the cumulative discordant pair counts (b, c). The sequential p is min(1, K * min raw p).

Alpha: under H0, conditional on which pairs are discordant, the signs are independent and fair. Each look's exact p
is then super-uniform. A union bound over the K planned looks makes the sequential p valid; stopping reports the
minimum over fewer looks, which can only increase it. holm() over the family of family_size sequential p values
then controls the family-wise error at alpha with strict rejection adjusted < alpha.

Efficacy: an interim look stops when family_size * p_seq < alpha. Holm's adjusted p for this test is a running max of
(m - j) * p_(j) with m - j <= family_size and p_(j) <= p_seq, so it is below alpha whatever the other p values are.
The stop therefore equals the later Holm rejection. Final: the last look reports p and direction only; rejection is
decided by holm() over the whole family. Callers keep every test in the family: a stopped test keeps its frozen p and
an incomplete or invalid test enters with p = 1.

Futility: an interim look stops when p_seq >= alpha and also K * p(minority, majority + R) >= alpha, where R is the
count of pairs still planned. The exact p grows with the minority count and falls with the majority count, so any
reachable later state (b', c') with b' >= b, c' >= c and b' + c' - b - c <= R has p at least p(minority, majority + R).
Hence p_seq can never fall below alpha, Holm can never reject the test, and a p value at or above alpha does not
change the decision for any other test.

Optional non-binding futility: set both delta and beta_f before trials. At an interim look of n pairs, stop without
rejection if max(b, c) is at most the largest k with Pr[Binomial(n, delta) <= k] <= beta_f. Report p = 1 and no
direction. This can only increase a p value relative to the same full history with the rule disabled. Holm can only
lose rejections. Efficacy and deterministic futility take precedence; no futility rule applies at the final look.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache

FRACTIONS = (0.25, 0.5, 1.0)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fraction_of(value) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float, Fraction)):
        raise TypeError(f"refused: fraction {value!r} is not a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"refused: fraction {value!r} is not finite")
        return Fraction(str(value))
    return Fraction(value)


def _fractions_of(fractions) -> list[Fraction]:
    if not isinstance(fractions, (list, tuple)) or not fractions:
        raise ValueError("refused: fractions must be a nonempty list")
    parsed = [_fraction_of(value) for value in fractions]
    for before, after in zip([Fraction(0)] + parsed, parsed):
        if after <= before:
            raise ValueError("refused: fractions must be strictly increasing in (0, 1]")
    if parsed[-1] != 1:
        raise ValueError("refused: the last fraction must be 1")
    return parsed


def stage_counts(n: int, fractions=FRACTIONS) -> list[int]:
    """Planned instances per stage for a set of n instances: ceil(fraction * n), exact."""
    if not _is_int(n) or n < 1:
        raise ValueError(f"refused: instance count {n!r} must be an integer >= 1")
    return [-(-fraction.numerator * n // fraction.denominator) for fraction in _fractions_of(fractions)]


def _names(values, where: str, minimum: int) -> list[str]:
    if not isinstance(values, list) or len(values) < minimum:
        raise ValueError(f"refused: {where} must be a list of at least {minimum}")
    if any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
        raise ValueError(f"refused: {where} must be distinct nonempty strings")
    return list(values)


def _arms_of(task_set: dict, index: int) -> list[str]:
    if "arms" in task_set and "conditions" in task_set:
        raise ValueError(f"refused: task set {index} has both arms and conditions")
    key = "arms" if "arms" in task_set else "conditions"
    if key not in task_set:
        raise ValueError(f"refused: task set {index} has no arms")
    return _names(task_set[key], f"task set {index} {key}", 2)


def _instances_of(task_set: dict, index: int) -> list[int]:
    instances = task_set.get("instances")
    if not isinstance(instances, list) or not instances:
        raise ValueError(f"refused: task set {index} has no instances")
    if any(not _is_int(instance) for instance in instances) or len(set(instances)) != len(instances):
        raise ValueError(f"refused: task set {index} instances must be distinct integers")
    return list(instances)


def stage_cells(plan: dict, fractions=FRACTIONS) -> list[list[dict]]:
    """Cumulative cells per stage. Each cell is {task_set, instance, arm, model}; stage k is a prefix of stage k+1."""
    if not isinstance(plan, dict):
        raise TypeError("refused: plan must be a dict")
    if "seat_assignments" in plan:
        raise ValueError("refused: staged waves do not support seat_assignments")
    if not _is_int(plan.get("order_seed")):
        raise ValueError("refused: order_seed must be an integer")
    models = _names(plan.get("models"), "models", 1)
    task_sets = plan.get("task_sets")
    if not isinstance(task_sets, list) or not task_sets:
        raise ValueError("refused: plan has no task sets")
    parsed = _fractions_of(fractions)
    rng = random.Random(plan["order_seed"])
    added: list[list[dict]] = [[] for _ in parsed]
    for index, task_set in enumerate(task_sets):
        if not isinstance(task_set, dict):
            raise TypeError(f"refused: task set {index} is not an object")
        arms = _arms_of(task_set, index)
        instances = _instances_of(task_set, index)
        rng.shuffle(instances)
        start = 0
        for new, end in zip(added, stage_counts(len(instances), parsed)):
            for instance in instances[start:end]:
                new.extend({"task_set": index, "instance": instance, "arm": arm, "model": model}
                           for arm in arms for model in models)
            start = end
        assert start == len(instances)
    stages, ordered = [], []
    for new in added:
        ordered.extend(new)
        stages.append([dict(cell) for cell in ordered])
    return stages


def _two_sided(tail: int, total: int) -> float:
    return min(1.0, (2 * tail) / (1 << total))


@lru_cache(maxsize=8192)
def _mcnemar(minority: int, majority: int) -> float:
    total = minority + majority
    tail, term = 0, 1
    for count in range(minority + 1):
        tail += term
        term = term * (total - count) // (count + 1)
    return _two_sided(tail, total)


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p: min(1, 2 * P(Binomial(b + c, 1/2) <= min(b, c))). (0, 0) gives 1."""
    if not _is_int(b) or not _is_int(c) or b < 0 or c < 0:
        raise ValueError(f"refused: discordant counts {b!r}, {c!r} must be integers >= 0")
    return _mcnemar(min(b, c), max(b, c))


def holm(pvalues) -> list[float]:
    """Holm adjusted p values in input order. The caller rejects where adjusted < alpha."""
    values = list(pvalues)
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"refused: p value {value!r} must be a number in [0, 1]")
    count = len(values)
    adjusted = [0.0] * count
    running = 0.0
    for rank, index in enumerate(sorted(range(count), key=lambda position: (values[position], position))):
        running = max(running, min(1.0, (count - rank) * float(values[index])))
        adjusted[index] = running
    return adjusted


def _futility_bound(n: int, delta: Fraction, beta_f: Fraction) -> int:
    """Largest count in the lower binomial tail at or below beta_f; -1 if even zero is too likely."""
    a, d = delta.numerator, delta.denominator
    limit = beta_f.numerator * d**n // beta_f.denominator
    tail, term = 0, (d - a)**n
    for k in range(n + 1):
        tail += term
        if tail > limit:
            return k - 1
        term = term * (n - k) * a // ((k + 1) * (d - a))
    raise ArithmeticError("binomial mass did not exceed the futility limit")


@dataclass(frozen=True)
class Disposition:
    status: str
    p: float
    direction: int
    look: int


class SequentialTest:
    """Group-sequential exact McNemar test at fixed absolute complete pair counts. Stateless: see disposition()."""

    def __init__(
        self,
        look_sizes,
        family_size: int = 1,
        alpha: float = 0.05,
        *,
        delta: float | Fraction | None = None,
        beta_f: float | Fraction | None = None,
    ):
        if not isinstance(look_sizes, (list, tuple)) or not look_sizes:
            raise ValueError("refused: look_sizes must be a nonempty list")
        if any(not _is_int(size) for size in look_sizes):
            raise ValueError("refused: look sizes must be integers")
        for before, after in zip((0, *look_sizes), look_sizes):
            if after <= before:
                raise ValueError("refused: look sizes must be strictly increasing and positive")
        if not _is_int(family_size) or family_size < 1:
            raise ValueError("refused: family_size must be an integer >= 1")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
            raise ValueError("refused: alpha must be in (0, 1)")
        self.look_sizes = tuple(look_sizes)
        self.family_size = family_size
        self.alpha = float(alpha)
        if delta is None and beta_f is None:
            self.futility_bounds = (-1,) * len(self.look_sizes)
        elif delta is None or beta_f is None:
            raise ValueError("refused: specify both delta and beta_f, or neither")
        else:
            rate, risk = _fraction_of(delta), _fraction_of(beta_f)
            if not (0 < rate < 1 and 0 < risk < 1):
                raise ValueError("refused: delta and beta_f must both be in (0, 1)")
            self.futility_bounds = tuple(_futility_bound(n, rate, risk) for n in self.look_sizes)
        self.boundary = self._boundary()

    def _sequential(self, raw: float) -> float:
        return min(1.0, len(self.look_sizes) * raw)

    def _crosses(self, raw: float) -> bool:
        return self.family_size * self._sequential(raw) < self.alpha

    def _boundary(self) -> tuple[int, ...]:
        """Largest minority count that stops for efficacy, per discordant total; -1 where none does."""
        bounds = []
        for total in range(self.look_sizes[-1] + 1):
            bound, tail, term = -1, 0, 1
            for minority in range(total // 2 + 1):
                tail += term
                term = term * (total - minority) // (minority + 1)
                if not self._crosses(_two_sided(tail, total)):
                    break
                bound = minority
            bounds.append(bound)
        return tuple(bounds)

    def _validated(self, history) -> list[tuple[int, int]]:
        if not isinstance(history, (list, tuple)) or len(history) > len(self.look_sizes):
            raise ValueError(f"refused: history must be a list of at most {len(self.look_sizes)} looks")
        states: list[tuple[int, int]] = []
        before_b, before_c, before_size = 0, 0, 0
        for look, entry in enumerate(history):
            if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                    or any(not _is_int(count) or count < 0 for count in entry)):
                raise ValueError(f"refused: look {look} must be (b, c) integers >= 0")
            b, c = entry
            size = self.look_sizes[look]
            if b < before_b or c < before_c or (b + c) - (before_b + before_c) > size - before_size:
                raise ValueError(f"refused: look {look} counts {b}, {c} are not reachable from the look before")
            states.append((b, c))
            before_b, before_c, before_size = b, c, size
        return states

    def _hopeless(self, b: int, c: int, look: int) -> bool:
        remaining = self.look_sizes[-1] - self.look_sizes[look]
        return self._sequential(mcnemar_p(min(b, c), max(b, c) + remaining)) >= self.alpha

    def disposition(self, history) -> Disposition:
        """Replay cumulative (b, c) per completed look; return the first terminal result, else continue."""
        states = self._validated(history)
        best_raw, direction = 1.0, 0
        last = len(self.look_sizes) - 1
        for look, (b, c) in enumerate(states):
            raw = mcnemar_p(b, c)
            if look == 0 or raw < best_raw:
                best_raw, direction = raw, (b > c) - (b < c)
            p = self._sequential(best_raw)
            if look == last:
                return Disposition("final", p, direction, look + 1)
            if min(b, c) <= self.boundary[b + c]:
                assert self._crosses(best_raw) and direction != 0
                return Disposition("efficacy", p, direction, look + 1)
            if p >= self.alpha and self._hopeless(b, c, look):
                return Disposition("futility", p, direction, look + 1)
            if max(b, c) <= self.futility_bounds[look]:
                return Disposition("nonbinding_futility", 1.0, 0, look + 1)
        return Disposition("continue", self._sequential(best_raw), direction, len(states))
