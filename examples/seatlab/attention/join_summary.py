from itertools import product
from typing import Any

ARMS = ("nested", "joined")
_WORLDS = 8
_SHIPMENTS = 6
_PERMUTATIONS = 1 << _WORLDS


def _check_score(score: tuple[int, int] | None) -> None:
    if score is not None and (
        not isinstance(score, tuple)
        or len(score) != 2
        or any(type(count) is not int for count in score)
        or not 0 <= score[0] <= score[1] <= _SHIPMENTS
    ):
        raise ValueError("expected reconciled stock counts for 6 shipments")


def primary(
    comparisons: dict[int, dict[str, tuple[int, int] | None]],
) -> dict[str, Any]:
    if len(comparisons) != _WORLDS or any(
        type(seed) is not int for seed in comparisons
    ):
        raise ValueError("expected 8 enrolled worlds")
    differences = []
    lower = upper = 0
    for seed, outcomes in sorted(comparisons.items()):
        if not isinstance(outcomes, dict) or outcomes.keys() != set(ARMS):
            raise ValueError("each world must enroll both arms")
        nested, joined = outcomes["nested"], outcomes["joined"]
        _check_score(nested)
        _check_score(joined)
        nested_min, nested_max = (0, _SHIPMENTS) if nested is None else (nested[0],) * 2
        joined_min, joined_max = (0, _SHIPMENTS) if joined is None else (joined[0],) * 2
        lower += joined_min - nested_max
        upper += joined_max - nested_min
        if nested is not None and joined is not None:
            differences.append(
                {
                    "seed": seed,
                    "stock_optimal": joined[0] - nested[0],
                    "feasible": joined[1] - nested[1],
                }
            )

    deltas = [world["stock_optimal"] for world in differences]
    net_optimal = sum(deltas) if differences else None
    net_feasible = (
        sum(world["feasible"] for world in differences) if differences else None
    )
    tail = p = None
    verdict = "inconclusive"
    if len(deltas) == _WORLDS and net_optimal is not None and net_feasible is not None:
        tail = sum(
            sum(sign * delta for sign, delta in zip(signs, deltas, strict=True))
            >= net_optimal
            for signs in product((-1, 1), repeat=_WORLDS)
        )
        p = tail / _PERMUTATIONS
        verdict = "not_established"
        if net_optimal >= _SHIPMENTS and p <= 0.05 and net_feasible >= 0:
            verdict = "established"
    return {
        "contrast": "joined_minus_nested_native_stock_optimal",
        "verdict": verdict,
        "matched_worlds": len(differences),
        "registered_worlds": _WORLDS,
        "world_differences": differences,
        "observed_net_optimal": net_optimal,
        "observed_net_feasible": net_feasible,
        "registered_bounds": {"lower": lower, "upper": upper},
        "p_value": p,
        "tail": tail,
        "permutations": _PERMUTATIONS,
    }
