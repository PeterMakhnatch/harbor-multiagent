import argparse
import json
from fractions import Fraction
from math import comb
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

_SCHEMA = "seatlab.native-calendar-visible-check-results/v1"
_WORLDS = tuple(f"w{i:02d}" for i in range(24, 48))
_ARMS = ("lists", "checked")
_MEASUREMENTS = (
    "own_valid",
    "target_in_opened",
    "target_in_initial_unopened",
    "chose_opened",
    "chose_initial_unopened",
)
_ROW_FIELDS = frozenset(
    ("world", "arm", "run", "trial_uuid", "error", "evidence_complete", "stock")
    + _MEASUREMENTS
)


class _Cell(NamedTuple):
    score: int | None
    measurements: tuple[bool | None, ...]


def _trial_id(value: Any, seen: set[UUID]) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("invalid native trial UUID type")
    identifier = UUID(value)
    if str(identifier) != value:
        raise ValueError("expected a canonical native trial UUID")
    if identifier in seen:
        raise ValueError("duplicate native trial UUID")
    seen.add(identifier)
    return identifier


def _stock_score(stock: Any) -> int | None:
    if stock is None:
        return None
    if not isinstance(stock, dict):
        raise TypeError("expected a native stock grade object")
    if stock.keys() != {"outer", "verifier", "oracle"}:
        raise ValueError("missing or unknown native stock grade source")
    observed = [value for value in stock.values() if value is not None]
    if any(type(value) is not int or value not in (0, 1) for value in observed):
        raise ValueError("expected a binary integer for each observed native grade")
    if len(set(observed)) > 1:
        raise ValueError("discordant native stock grades")
    return observed[0] if len(observed) == 3 else None


def _cell(row: dict[str, Any], trial_ids: set[UUID]) -> _Cell:
    identifier = _trial_id(row["trial_uuid"], trial_ids)
    score = _stock_score(row["stock"])
    complete = row["evidence_complete"]
    if type(complete) is not bool:
        raise TypeError("native evidence completion flag must be a Boolean")
    measurements = tuple(row[field] for field in _MEASUREMENTS)
    for field, value in zip(_MEASUREMENTS, measurements, strict=True):
        if type(value) is not bool and (complete or value is not None):
            raise ValueError(f"invalid native measurement: {field}")
    _, target_opened, target_unopened, chose_opened, chose_unopened = measurements
    if (target_opened and target_unopened) or (chose_opened and chose_unopened):
        raise ValueError("opened and initially unopened slots must be disjoint")
    if identifier is None or not complete or row["error"] is not None:
        score = None
    return _Cell(score, measurements)


def _grid(receipt: dict[str, Any]) -> dict[tuple[str, str], _Cell]:
    if not isinstance(receipt, dict) or receipt.get("schema") != _SCHEMA:
        raise ValueError("expected a native calendar visible-check results receipt")
    cells = receipt.get("cells")
    if not isinstance(cells, list):
        raise TypeError("expected a list of registered cells")
    grid: dict[tuple[str, str], _Cell] = {}
    trial_ids: set[UUID] = set()
    for row in cells:
        if not isinstance(row, dict) or not _ROW_FIELDS.issubset(row):
            raise ValueError("invalid registered cell fields")
        world, arm = row["world"], row["arm"]
        if world not in _WORLDS or arm not in _ARMS:
            raise ValueError("unregistered world or arm")
        if (world, arm) in grid:
            raise ValueError("duplicate registered cell")
        if row["run"] != f"cvj-{world}-{arm}":
            raise ValueError("invalid registered run binding")
        grid[world, arm] = _cell(row, trial_ids)
    return grid


def _arm_counts(grid: dict[tuple[str, str], _Cell], arm: str) -> dict[str, int]:
    counts = dict.fromkeys(_MEASUREMENTS, 0)
    eligible = successes = 0
    for world in _WORLDS:
        cell = grid.get((world, arm))
        if cell is None or cell.score is None:
            continue
        eligible += 1
        successes += cell.score == 1
        for field, value in zip(_MEASUREMENTS, cell.measurements, strict=True):
            counts[field] += value is True
    return {
        "eligible": eligible,
        "missing": len(_WORLDS) - eligible,
        "successes": successes,
        **counts,
    }


def _primary(grid: dict[tuple[str, str], _Cell]) -> tuple[dict[str, Any], str]:
    wins = ties = losses = 0
    for world in _WORLDS:
        lists, checked = (grid.get((world, arm)) for arm in _ARMS)
        if (
            lists is None
            or checked is None
            or lists.score is None
            or checked.score is None
        ):
            continue
        wins += checked.score > lists.score
        ties += checked.score == lists.score
        losses += checked.score < lists.score
    matched = wins + ties + losses
    discordant = wins + losses
    result: dict[str, Any] = {
        "matched_worlds": matched,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "discordant_worlds": discordant,
        "checked_minus_lists": None,
        "mean_delta": None,
        "two_sided_sign_p": None,
    }
    if matched != len(_WORLDS):
        return result, "inconclusive"

    difference = wins - losses
    delta = Fraction(difference, len(_WORLDS))
    tail = sum(comb(discordant, k) for k in range(min(wins, losses) + 1))
    p = min(Fraction(1), Fraction(2 * tail, 2**discordant))
    result.update(
        checked_minus_lists=difference,
        mean_delta=float(delta),
        two_sided_sign_p={"numerator": p.numerator, "denominator": p.denominator},
    )
    verdict = (
        "pass"
        if delta >= Fraction(1, 10) and p <= Fraction(1, 20)
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
    parser = argparse.ArgumentParser(
        description="Summarize the native calendar visible-check study."
    )
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        result = summarize(receipt)
    except (ValueError, TypeError, OSError):
        parser.error("cannot read or summarize the native receipt")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
