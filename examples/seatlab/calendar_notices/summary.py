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
    private_views = receipt.get("schema") == "seatlab.calendar-private-view-audit/v1"
    if (
        not private_views
        and receipt.get("schema") != "seatlab.calendar-notice-audit/v1"
    ):
        raise ValueError("not a weekday-notice receipt")
    arms = ("raw", "exact") if private_views else _ARMS
    worlds = tuple(f"w{i}" for i in range(12, 24)) if private_views else _WORLDS
    if private_views and receipt.get("mode") != "live":
        raise ValueError("expected registered live teams, not control teams")
    grid = {}
    trials = set()
    for row in receipt["cells"]:
        key = (row["world"], row["arm"])
        if key in grid or row["control"] != "live" or type(row["complete"]) is not bool:
            raise ValueError("expected one first-attempt live cell per world and arm")
        reward, trial = row["reward"], row["trial_uuid"]
        if private_views and (
            row["run"] != f"cpv-{key[0]}-{key[1]}"
            or type(row["reward_valid"]) is not bool
            or type(row["reward_verified"]) is not bool
            or row["reward_verified"]
            and not row["reward_valid"]
        ):
            raise ValueError("invalid native private-view cell binding")
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
        arm_bound = (
            not private_views or row["complete"] or row.get("error") == "model_invalid"
        )
        if row["reward_verified"] is not True or not arm_bound:
            row = {**row, "reward": None}
        grid[key] = row
    if set(grid) != {(world, arm) for world in worlds for arm in arms}:
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


def _single_candidate_replay(teams: list[dict[str, Any]]) -> dict[str, int]:
    seen = singles = correct = recorded = gains = losses = 0
    for team in teams:
        count = team.get("final_candidate_count")
        contains_target = team.get("joint_slot_in_final_report")
        if count is None or contains_target is None:
            continue
        if type(count) is not int or not 0 <= count <= 35:
            raise ValueError("invalid receiver candidate count")
        if type(contains_target) is not bool or (count == 0 and contains_target):
            raise ValueError("invalid receiver candidate membership")
        before = int(team["reward"])
        after = int(contains_target) if count == 1 else before
        seen += 1
        singles += count == 1
        correct += after
        recorded += before
        gains += after > before
        losses += after < before
    return {
        "audited_teams": seen,
        "teams_with_one_candidate": singles,
        "recorded_successes": recorded,
        "offline_successes": correct,
        "improved": gains,
        "worsened": losses,
    }


def _private_views(grid: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, dict[str, int]] = {}
    for arm in ("raw", "exact"):
        cells = [row for (_, name), row in grid.items() if name == arm]
        complete = [row for row in cells if row["complete"]]
        arms[arm] = {
            "enrolled": len(cells),
            "complete": len(complete),
            "scored": sum(row["reward"] is not None for row in cells),
            "native_successes": sum(row["reward"] == 1 for row in cells),
            "initial_exact": sum(
                row["initial_report_exact"] is True for row in complete
            ),
            "revised_exact": sum(
                row["revision_report_exact"] is True for row in complete
            ),
            "receiver_valid": sum(
                row["chosen_locally_fits"] is True for row in complete
            ),
            "one_candidate": sum(row["final_candidate_count"] == 1 for row in complete),
        }
    wins = ties = losses = low = high = 0
    for world in sorted({world for world, _ in grid}):
        raw, exact = (grid[world, arm]["reward"] for arm in ("raw", "exact"))
        low += (0 if exact is None else int(exact)) - (1 if raw is None else int(raw))
        high += (1 if exact is None else int(exact)) - (0 if raw is None else int(raw))
        if raw is not None and exact is not None:
            wins += exact > raw
            ties += exact == raw
            losses += exact < raw
    observed = wins + ties + losses
    discordant = wins + losses
    tail = sum(comb(discordant, k) for k in range(wins, discordant + 1))
    p = Fraction(tail, 2**discordant)
    return {
        "arms": arms,
        "verified_reward_pairs": observed,
        "exact_wins_ties_losses": [wins, ties, losses],
        "net_extra_wins_bounds": [low, high],
        "one_sided_p": str(p) if observed == 12 else None,
        "registered_result": (
            "inconclusive"
            if observed != 12
            else "pass"
            if low >= 4 and p <= Fraction(1, 20)
            else "not_established"
        ),
    }


def summarize(receipt: dict[str, Any]) -> dict[str, Any]:
    grid = _grid(receipt)
    if receipt.get("schema") == "seatlab.calendar-private-view-audit/v1":
        return _private_views(grid)
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
            "offline_single_candidate_replay": _single_candidate_replay(complete),
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
