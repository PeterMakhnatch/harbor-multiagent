import argparse
import json
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

_SCHEMA = "seatlab.calendar-role-view-audit/v1"
_DEFAULT = Path(__file__).resolve().parents[1] / "results/calendar-role-views-live.json"
_WORLDS = tuple(f"w{i}" for i in range(12, 24))
_ARMS = ("sender-exact", "receiver-exact")


class _Cell(NamedTuple):
    score: int | None
    complete: bool
    exact_revision: bool | None
    locally_valid_choice: bool | None
    unique_correct_candidate: bool | None


def _stock_score(row: dict[str, Any], trial_ids: set[UUID]) -> int | None:
    trial = row.get("trial_uuid")
    if trial is not None:
        if not isinstance(trial, str):
            raise ValueError("invalid native trial UUID")
        identifier = UUID(trial)
        if (
            identifier.version != 4
            or str(identifier) != trial
            or identifier in trial_ids
        ):
            raise ValueError("invalid or repeated native trial UUID")
        trial_ids.add(identifier)

    complete = row.get("complete")
    valid = row.get("reward_valid")
    verified = row.get("reward_verified")
    reward = row.get("reward")
    if (
        any(type(flag) is not bool for flag in (complete, valid, verified))
        or (verified and not valid)
        or (valid and reward is None)
        or (complete and not verified)
        or (complete and row.get("error") is not None)
    ):
        raise ValueError("invalid native stock score binding")
    if reward is not None and (
        type(reward) not in (int, float) or reward not in (0, 1) or trial is None
    ):
        raise ValueError("invalid native stock score or missing trial UUID")
    if reward is None or not verified:
        return None
    if complete or (row.get("error") == "model_invalid" and reward == 0):
        return int(reward)
    return None


def _measurement(row: dict[str, Any], field: str) -> bool | None:
    value = row.get(field)
    if value is not None and type(value) is not bool:
        raise ValueError(f"invalid measurement: {field}")
    return value


def _candidate(row: dict[str, Any]) -> bool | None:
    count = row.get("final_candidate_count")
    contains_target = _measurement(row, "joint_slot_in_final_report")
    if count is not None and (type(count) is not int or not 0 <= count <= 35):
        raise ValueError("invalid receiver candidate count")
    if count == 0 and contains_target:
        raise ValueError("invalid receiver candidate membership")
    if count is None or contains_target is None:
        return None
    return count == 1 and contains_target


def _grid(receipt: dict[str, Any]) -> dict[tuple[str, str], _Cell]:
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != _SCHEMA
        or receipt.get("mode") != "live"
    ):
        raise ValueError("expected a live calendar role-view audit receipt")
    cells = receipt.get("cells")
    if not isinstance(cells, list) or len(cells) != len(_WORLDS) * len(_ARMS):
        raise ValueError(
            "expected all 24 registered cells, including unscored attempts"
        )
    grid = {}
    trial_ids: set[UUID] = set()
    for row in cells:
        if not isinstance(row, dict):
            raise TypeError("invalid live cell")
        world, arm = row.get("world"), row.get("arm")
        if (
            world not in _WORLDS
            or arm not in _ARMS
            or (world, arm) in grid
            or row.get("control") != "live"
            or row.get("run") != f"crv-{world}-{arm}"
        ):
            raise ValueError("expected one registered live cell per world and arm")
        score = _stock_score(row, trial_ids)
        grid[world, arm] = (
            _Cell(None, False, None, None, None)
            if score is None
            else _Cell(
                score,
                row["complete"],
                _measurement(row, "revision_report_exact"),
                _measurement(row, "chosen_locally_fits"),
                _candidate(row),
            )
        )
    return grid


def _arm_counts(grid: dict[tuple[str, str], _Cell], arm: str) -> dict[str, int]:
    counts = {
        "stock_wins": 0,
        "stock_graded": 0,
        "complete": 0,
        "protocol_invalid_stock_zeros": 0,
        "exact_revisions": 0,
        "revisions_measured": 0,
        "locally_valid_choices": 0,
        "choices_measured": 0,
        "unique_correct_visible_candidates": 0,
        "candidates_measured": 0,
    }
    for world in _WORLDS:
        cell = grid[world, arm]
        counts["stock_wins"] += cell.score == 1
        counts["stock_graded"] += cell.score is not None
        counts["complete"] += cell.complete
        counts["protocol_invalid_stock_zeros"] += cell.score == 0 and not cell.complete
        counts["exact_revisions"] += cell.exact_revision is True
        counts["revisions_measured"] += cell.exact_revision is not None
        counts["locally_valid_choices"] += cell.locally_valid_choice is True
        counts["choices_measured"] += cell.locally_valid_choice is not None
        counts["unique_correct_visible_candidates"] += (
            cell.unique_correct_candidate is True
        )
        counts["candidates_measured"] += cell.unique_correct_candidate is not None
    return counts


def _primary(grid: dict[tuple[str, str], _Cell]) -> tuple[dict[str, Any], str]:
    wins = ties = losses = 0
    for world in _WORLDS:
        sender, receiver = (grid[world, arm].score for arm in _ARMS)
        if sender is None or receiver is None:
            continue
        wins += receiver > sender
        ties += receiver == sender
        losses += receiver < sender
    matched = wins + ties + losses
    result: dict[str, Any] = {
        "matched_worlds": matched,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "receiver_minus_sender": None,
        "two_sided_sign_p": None,
    }
    if matched != len(_WORLDS):
        return result, "inconclusive"

    difference = wins - losses
    discordant = wins + losses
    tail = sum(comb(discordant, k) for k in range(min(wins, losses) + 1))
    p = min(Fraction(1), Fraction(2 * tail, 2**discordant))
    result.update(
        receiver_minus_sender=difference,
        two_sided_sign_p={"numerator": p.numerator, "denominator": p.denominator},
    )
    verdict = (
        "established"
        if abs(difference) >= 3 and p <= Fraction(1, 20)
        else "not_established"
    )
    return result, verdict


def summarize(receipt: dict[str, Any]) -> dict[str, Any]:
    grid = _grid(receipt)
    primary, verdict = _primary(grid)
    return {
        "arms": {arm: _arm_counts(grid, arm) for arm in _ARMS},
        "primary": primary,
        "verdict": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize the live role-view audit.")
    parser.add_argument("path", nargs="?", type=Path, default=_DEFAULT)
    receipt = json.loads(parser.parse_args().path.read_text(encoding="utf-8"))
    print(json.dumps(summarize(receipt), indent=2))


if __name__ == "__main__":
    main()
