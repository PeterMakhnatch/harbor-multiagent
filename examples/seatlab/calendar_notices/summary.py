"""Read the published Flotilla weekday-notice receipt. No Harbor install is needed."""

import argparse
import json
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any

_RECEIPT = Path(__file__).resolve().parents[1] / "results/calendar-notice-live.json"
_ARMS = ("count", "named")
_WORLDS = tuple(f"w{i:02d}" for i in range(12))


def _grid(receipt: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    if receipt.get("schema") != "seatlab.calendar-notice-audit/v1":
        raise ValueError("not a weekday-notice receipt")
    grid = {}
    trials = set()
    for row in receipt["cells"]:
        key = (row["world"], row["arm"])
        if key in grid or row["control"] != "live" or type(row["complete"]) is not bool:
            raise ValueError("expected one first-attempt live cell per world and arm")
        reward, trial = row["reward"], row["trial_uuid"]
        if reward is not None and (
            type(reward) not in (int, float) or reward not in (0, 1) or not trial
        ):
            raise ValueError("invalid native reward or missing trial identity")
        if trial is not None:
            if not isinstance(trial, str) or not trial or trial in trials:
                raise ValueError("invalid or repeated native trial")
            trials.add(trial)
        changed, hits = row["changed_days"], row["opened_changed"]
        if changed is not None and (type(changed) is not int or not 0 <= changed <= 5):
            raise ValueError("invalid change count")
        if hits is not None and (
            type(hits) is not int
            or changed is None
            or not max(0, changed - 3) <= hits <= min(2, changed)
        ):
            raise ValueError("invalid changed-report read count")
        if row["complete"] and (
            reward is None or row["reward_verified"] is not True or hits is None
        ):
            raise ValueError(
                "a complete cell requires a native reward and exactly two audited reads"
            )
        grid[key] = row if row["reward_verified"] is True else {**row, "reward": None}
    if set(grid) != {(world, arm) for world in _WORLDS for arm in _ARMS}:
        raise ValueError(
            "expected all 12 registered world pairs, including missing attempts"
        )
    return grid


def _paired(
    grid: dict[tuple[str, str], dict[str, Any]], field: str, largest: int
) -> dict[str, Any]:
    differences = []
    low = high = 0
    for world in _WORLDS:
        count, named = (grid[world, arm] for arm in _ARMS)
        a, b = count[field], named[field]
        if count["complete"] and named["complete"]:
            differences.append(int(b) - int(a))
        low += (0 if b is None else int(b)) - (largest if a is None else int(a))
        high += (largest if b is None else int(b)) - (0 if a is None else int(a))
    wins = sum(d > 0 for d in differences)
    losses = sum(d < 0 for d in differences)
    n = wins + losses
    p = Fraction(sum(comb(n, k) for k in range(wins, n + 1)), 2**n)
    total = len(_WORLDS)
    full = len(differences) == total
    mean = Fraction(sum(differences), total) if full else None
    outcome = "inconclusive"
    if mean is not None:
        outcome = "pass" if mean > 0 and p <= Fraction(1, 20) else "not_established"
    return {
        "complete_pairs": len(differences),
        "wins": wins,
        "losses": losses,
        "ties": len(differences) - n,
        "mean_named_minus_count": str(mean) if mean is not None else None,
        "full_register_bounds": [str(Fraction(v, total)) for v in (low, high)],
        "one_sided_sign_p_complete_pairs": str(p) if differences else None,
        "outcome": outcome,
    }


def summarize(receipt: dict[str, Any]) -> dict[str, Any]:
    grid = _grid(receipt)
    arms = {}
    for arm in _ARMS:
        enrolled = [grid[world, arm] for world in _WORLDS]
        scored = [r for r in enrolled if r["reward"] is not None]
        complete = [r for r in enrolled if r["complete"]]
        arms[arm] = {
            "enrolled_teams": len(enrolled),
            "complete_teams": len(complete),
            "scored_teams": len(scored),
            "native_successes": sum(int(r["reward"]) for r in scored),
            "reads_in_complete_teams": 2 * len(complete),
            "changed_reports_opened": sum(r["opened_changed"] for r in complete),
            "maximum_changed_reads_given_actual_revisions": sum(
                min(2, r["changed_days"]) for r in complete
            ),
        }
    return {
        "arms": arms,
        "primary_native_reward": _paired(grid, "reward", 1),
        "secondary_changed_reads": _paired(grid, "opened_changed", 2),
    }


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("receipt", type=Path, nargs="?", default=_RECEIPT)
    result = summarize(json.loads(cli.parse_args().receipt.read_bytes()))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
