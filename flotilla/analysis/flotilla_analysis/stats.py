"""Statistics with the standard library only.

- Student t intervals for a mean of per-instance values (instances are the clusters).
- A paired sign-flip permutation test for a mean difference.
- A percentile bootstrap for recovery, a ratio of two paired mean differences.

Everything is deterministic: sums use ``math.fsum``, random draws come from a ``random.Random`` seeded
by the caller, and every iteration order is fixed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

LEVEL = 0.95
EXACT_MAX_NONZERO = 16  # the sign-flip test enumerates all 2**k sign patterns up to this k
DRAWS = 20_000
RESAMPLES = 10_000


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of no values")
    return math.fsum(values) / len(values)


def sample_sd(values: Sequence[float]) -> float:
    """Standard deviation with the n - 1 denominator."""
    if len(values) < 2:
        raise ValueError("a sample standard deviation needs at least 2 values")
    centre = mean(values)
    return math.sqrt(math.fsum((v - centre) ** 2 for v in values) / (len(values) - 1))


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """Continued fraction of the incomplete beta function (modified Lentz method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-16:
            return h
    raise ArithmeticError("incomplete beta did not converge")


def regularized_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b), the regularized incomplete beta function."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def t_cdf(t: float, df: int) -> float:
    """P(T <= t) for Student's t with ``df`` degrees of freedom."""
    if df < 1:
        raise ValueError("df must be at least 1")
    tail = 0.5 * regularized_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_quantile(p: float, df: int) -> float:
    """The ``p`` quantile of Student's t, for 0.5 <= p < 1, by bisection on ``t_cdf``."""
    if not 0.5 <= p < 1.0:
        raise ValueError("p must be in [0.5, 1)")
    low, high = 0.0, 1.0
    while t_cdf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if t_cdf(middle, df) < p:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


@dataclass(frozen=True)
class Estimate:
    """A point estimate with a 95% interval. ``low``/``high`` are None when no interval exists."""

    value: float | None
    low: float | None
    high: float | None
    n: int
    note: str = ""


def t_interval(values: Sequence[float], *, bounds: tuple[float, float] | None = None) -> Estimate:
    """Mean of ``values`` with a two-sided 95% Student t interval (df = n - 1).

    ``values`` are per-instance means, so the interval treats the instance as the unit (clustered by
    instance). ``bounds`` clips the interval to the range the quantity can take.
    """
    n = len(values)
    if n == 0:
        return Estimate(None, None, None, 0, "no data")
    centre = mean(values)
    if n < 2:
        return Estimate(centre, None, None, n, "one instance: no interval")
    sd = sample_sd(values)
    half = t_quantile(1.0 - (1.0 - LEVEL) / 2.0, n - 1) * sd / math.sqrt(n)
    low, high = centre - half, centre + half
    if bounds is not None:
        low, high = max(low, bounds[0]), min(high, bounds[1])
    note = "all instance values equal: zero-width interval" if sd == 0.0 else ""
    return Estimate(centre, low, high, n, note)


@dataclass(frozen=True)
class PermutationTest:
    p_value: float | None
    method: str  # "exact", "monte carlo" or "none"
    nonzero: int
    draws: int


def sign_flip_test(
    differences: Sequence[float], rng: random.Random, *, draws: int = DRAWS
) -> PermutationTest:
    """Two-sided paired sign-flip test of mean difference = 0.

    Under the null each instance's difference is equally likely to have either sign. The statistic is
    |sum of differences|; zero differences cannot change it and are left out. With at most 16 nonzero
    differences every sign pattern is enumerated (exact p, the observed pattern included); with more,
    ``draws`` random patterns give p = (1 + extreme draws) / (1 + draws).
    """
    nonzero = [abs(d) for d in differences if d != 0.0]
    k = len(nonzero)
    if not differences:
        return PermutationTest(None, "none", 0, 0)
    if k == 0:
        return PermutationTest(1.0, "exact", 0, 0)
    observed = abs(math.fsum(differences))
    tolerance = 1e-9 * math.fsum(nonzero)

    def extreme(mask: int) -> bool:
        total = math.fsum(v if mask >> i & 1 else -v for i, v in enumerate(nonzero))
        return abs(total) >= observed - tolerance

    if k <= EXACT_MAX_NONZERO:
        count = sum(1 for mask in range(1 << k) if extreme(mask))
        return PermutationTest(count / (1 << k), "exact", k, 1 << k)
    count = sum(1 for _ in range(draws) if extreme(rng.getrandbits(k)))
    return PermutationTest((count + 1) / (draws + 1), "monte carlo", k, draws)


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics (Hyndman and Fan type 7, numpy's default)."""
    if not sorted_values:
        raise ValueError("percentile of no values")
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] + weight * (sorted_values[upper] - sorted_values[lower])


@dataclass(frozen=True)
class Recovery:
    value: float | None
    low: float | None
    high: float | None
    n: int
    resamples: int
    undefined_resamples: int
    note: str = ""


def bootstrap_recovery(
    gain: Sequence[float], gap: Sequence[float], rng: random.Random, *, resamples: int = RESAMPLES
) -> Recovery:
    """Recovery = mean(gain) / mean(gap) with a 95% percentile bootstrap interval over instances.

    ``gain[i]`` is talk - silent and ``gap[i]`` is solo - silent for paired instance i. Recovery is
    defined only when the mean gap is positive (there is an information gap to close). A resample
    whose mean gap is <= 0 is undefined. If more than 2.5% of the resamples are undefined, the data
    cannot rule out that there is no gap at all, and the interval is reported as undefined (like
    Fieller's unbounded confidence set for a ratio). Otherwise the interval is the 2.5th and 97.5th
    percentiles of the defined resamples; the number left out is reported.
    """
    n = len(gain)
    if n != len(gap):
        raise ValueError("gain and gap must be paired")
    if n == 0:
        return Recovery(None, None, None, 0, 0, 0, "no paired instances")
    gap_total = math.fsum(gap)
    if gap_total <= 0.0:
        return Recovery(None, None, None, n, 0, 0, "undefined: solo - silent <= 0, no information gap")
    value = math.fsum(gain) / gap_total
    if n < 2:
        return Recovery(value, None, None, n, 0, 0, "one instance: no interval")
    defined: list[float] = []
    undefined = 0
    for _ in range(resamples):
        picks = [rng.randrange(n) for _ in range(n)]
        denominator = math.fsum(gap[i] for i in picks)
        if denominator <= 0.0:
            undefined += 1
            continue
        defined.append(math.fsum(gain[i] for i in picks) / denominator)
    tail = (1.0 - LEVEL) / 2.0
    if undefined > tail * resamples:
        note = f"interval undefined: solo - silent <= 0 in {undefined} of {resamples} resamples"
        return Recovery(value, None, None, n, resamples, undefined, note)
    defined.sort()
    note = f"{undefined} of {resamples} resamples undefined and left out" if undefined else ""
    low, high = percentile(defined, tail), percentile(defined, 1.0 - tail)
    return Recovery(value, low, high, n, resamples, undefined, note)
