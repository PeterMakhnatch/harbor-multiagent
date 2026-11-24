"""Sharp finite bounds for 1..64 matched binary pairs `(continue, seal)`.

`summarize(arms)` takes each pair as two values in `{0, 1, None}`; `None` is an
unknown arm. A pair is positive when seal - continue = +1 and negative when it
is -1. Every unknown arm may take either binary value, so the report covers
every completion and assumes nothing about why an arm is unknown.

    denominator    number of registered pairs, known or not
    known_arms     arms with a binary grade; missing_arms = 2 * denominator - known_arms
    paired_finals  pairs with both arms known
    positive       [min, max] attainable count of positive pairs
    negative       [min, max] attainable count of negative pairs
    net_sum        [min, max] attainable sum of seal - continue over all pairs
    width          net_sum[1] - net_sum[0]
    joint          [[positive, [negative, ...]], ...]: every attainable
                   (positive, negative) count pair, both ascending

Each endpoint is attained by one completion. The report is no complete-case
mean and no estimate for a model population.
"""

from typing import Any

MAX_PAIRS = 64
GRADES = (0, 1)

Arm = int | None
Pair = tuple[Arm, Arm]


def _arm(value: object, label: str) -> Arm:
    if value is None:
        return None
    if type(value) is not int or value not in GRADES:
        raise ValueError(f"{label} is not 0, 1, or null")
    return value


def normalize(arms: object) -> list[Pair]:
    if not isinstance(arms, (list, tuple)) or not 1 <= len(arms) <= MAX_PAIRS:
        raise ValueError(f"arms is not a list of 1..{MAX_PAIRS} pairs")
    pairs: list[Pair] = []
    for index, pair in enumerate(arms):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"pair {index} is not (continue, seal)")
        first = _arm(pair[0], f"pair {index} continue")
        pairs.append((first, _arm(pair[1], f"pair {index} seal")))
    assert len(pairs) == len(arms)
    return pairs


def _options(pair: Pair) -> tuple[tuple[int, int], ...]:
    first = GRADES if pair[0] is None else (pair[0],)
    second = GRADES if pair[1] is None else (pair[1],)
    found = {(int(s > c), int(c > s)) for c in first for s in second}
    assert 1 <= len(found) <= 3
    return tuple(sorted(found))


def attainable(pairs: list[Pair]) -> set[tuple[int, int]]:
    assert 1 <= len(pairs) <= MAX_PAIRS
    states = {(0, 0)}
    for pair in pairs:
        options = _options(pair)
        states = {(p + dp, n + dn) for p, n in states for dp, dn in options}
        assert len(states) <= (MAX_PAIRS + 1) * (MAX_PAIRS + 2) // 2
    assert all(p + n <= len(pairs) for p, n in states)
    return states


def _closed_net(pairs: list[Pair]) -> tuple[int, int]:
    low = high = 0
    for pair in pairs:
        nets = [p - n for p, n in _options(pair)]
        low += min(nets)
        high += max(nets)
    return low, high


def summarize(arms: list[tuple[int | None, int | None]]) -> dict[str, Any]:
    pairs = normalize(arms)
    states = attainable(pairs)
    known = sum(value is not None for pair in pairs for value in pair)
    finals = sum(pair[0] is not None and pair[1] is not None for pair in pairs)
    nets = [p - n for p, n in states]
    net_sum = [min(nets), max(nets)]
    assert tuple(net_sum) == _closed_net(pairs)
    grouped: dict[int, list[int]] = {}
    for p, n in states:
        grouped.setdefault(p, []).append(n)
    joint = [[p, sorted(grouped[p])] for p in sorted(grouped)]
    assert sum(map(len, grouped.values())) == len(states)
    width = net_sum[1] - net_sum[0]
    assert width == 2 * len(pairs) - known
    assert finals <= len(pairs) and (width == 0) == (finals == len(pairs))
    return {
        "denominator": len(pairs),
        "known_arms": known,
        "missing_arms": 2 * len(pairs) - known,
        "paired_finals": finals,
        "positive": [min(p for p, _ in states), max(p for p, _ in states)],
        "negative": [min(n for _, n in states), max(n for _, n in states)],
        "net_sum": net_sum,
        "width": width,
        "joint": joint,
    }
