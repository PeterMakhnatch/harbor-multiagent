import argparse
import json
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path
from typing import Any, NamedTuple, TypedDict
from uuid import UUID

from .visible_replay_summary import _digest, _sign_flip_p, _unique_object

_SCHEMA = "seatlab.calendar-native-repeatability/v1"
_WORLDS = tuple(f"w{i:02d}" for i in range(24, 48))
_ARMS = ("lists", "checked")
_REPETITIONS = (1, 2)
_GRID = frozenset(
    (world, arm, repetition)
    for world in _WORLDS
    for arm in _ARMS
    for repetition in _REPETITIONS
)
_FIELDS = {
    "world",
    "arm",
    "repetition",
    "run",
    "source_sha256",
    "trial_uuid",
    "result_sha256",
    "status",
    "reward",
    "target_visible",
}
_MAX_RECEIPT_BYTES = 128 * 1024


class _Team(NamedTuple):
    status: str
    reward: int | None
    target_visible: bool | None


class _WorldReport(TypedDict):
    rewards_by_repetition: dict[str, list[int | None]]
    checked_minus_lists: int | None
    outcome: str


type _Grid = dict[tuple[str, str, int], _Team]


def _trial_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a canonical version 4 trial UUID")
    identifier = UUID(value)
    if identifier.version != 4 or str(identifier) != value:
        raise ValueError("expected a canonical version 4 trial UUID")
    return value


def _team(row: dict[str, Any], seen_trials: set[str], seen_results: set[str]) -> _Team:
    status, reward, visible = row["status"], row["reward"], row["target_visible"]
    if status == "complete":
        if type(reward) is not int or reward not in (0, 1) or type(visible) is not bool:
            raise ValueError(
                "complete team requires a binary reward and Boolean visibility"
            )
    elif status == "model_invalid":
        if type(reward) is not int or reward != 0:
            raise ValueError("model-invalid team requires a verified zero reward")
        if visible is not None and type(visible) is not bool:
            raise ValueError("model-invalid visibility must be Boolean or null")
    elif status == "missing":
        if reward is not None or visible is not None:
            raise ValueError("missing team cannot have a reward or visibility")
    else:
        raise ValueError("unknown native repeat team status")

    if row["trial_uuid"] is not None or status != "missing":
        trial = _trial_uuid(row["trial_uuid"])
        if trial in seen_trials:
            raise ValueError("duplicate native trial UUID")
        seen_trials.add(trial)
    if row["result_sha256"] is not None or status != "missing":
        result = _digest(row["result_sha256"])
        if result in seen_results:
            raise ValueError("duplicate native result digest")
        seen_results.add(result)
    return _Team(status, reward, visible)


def _grid(receipt: Any) -> _Grid:
    if (
        not isinstance(receipt, dict)
        or receipt.keys() != {"schema", "teams"}
        or receipt["schema"] != _SCHEMA
    ):
        raise ValueError("expected a native calendar repeatability receipt")
    rows = receipt["teams"]
    if not isinstance(rows, list) or len(rows) != len(_GRID):
        raise ValueError("receipt must contain every enrolled native repeat team")

    teams: _Grid = {}
    sources: dict[str, str] = {}
    seen_trials: set[str] = set()
    seen_results: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.keys() != _FIELDS:
            raise ValueError("unexpected native repeat team fields")
        world, arm, repetition = row["world"], row["arm"], row["repetition"]
        if (
            not isinstance(world, str)
            or not isinstance(arm, str)
            or type(repetition) is not int
        ):
            raise ValueError("invalid native repeat cell coordinates")
        cell = (world, arm, repetition)
        if cell not in _GRID or cell in teams:
            raise ValueError("unknown or duplicate native repeat cell")
        if row["run"] != f"cvr-r{repetition:02d}-{world}-{arm}":
            raise ValueError("native repeat run does not match its enrolled cell")
        source = _digest(row["source_sha256"])
        if sources.setdefault(world, source) != source:
            raise ValueError(f"native semantics digest changed within {world}")
        teams[cell] = _team(row, seen_trials, seen_results)
    return teams


def _counts(teams: Iterable[_Team]) -> dict[str, int]:
    counts = dict.fromkeys(
        (
            "teams",
            "successes",
            "eligible",
            "model_invalid",
            "missing",
            "target_visible",
            "target_visibility_observed",
        ),
        0,
    )
    for team in teams:
        counts["teams"] += 1
        counts["successes"] += team.reward == 1
        counts["eligible"] += team.reward is not None
        counts["model_invalid"] += team.status == "model_invalid"
        counts["missing"] += team.status == "missing"
        counts["target_visible"] += team.target_visible is True
        counts["target_visibility_observed"] += team.target_visible is not None
    return counts


def _repeat_disagreement(teams: _Grid, arm: str) -> dict[str, int]:
    disagreements = pairs = 0
    for world in _WORLDS:
        first, second = (teams[world, arm, repeat].reward for repeat in _REPETITIONS)
        if first is not None and second is not None:
            pairs += 1
            disagreements += first != second
    return {"disagreements": disagreements, "denominator": pairs}


def _world_report(teams: _Grid, world: str) -> _WorldReport:
    rewards = {
        arm: [teams[world, arm, repeat].reward for repeat in _REPETITIONS]
        for arm in _ARMS
    }
    difference = None
    outcome = "missing"
    if all(reward is not None for pair in rewards.values() for reward in pair):
        lists, checked = (
            sum(reward for reward in rewards[arm] if reward is not None)
            for arm in _ARMS
        )
        difference = checked - lists
        outcome = "win" if difference > 0 else "loss" if difference < 0 else "tie"
    return {
        "rewards_by_repetition": rewards,
        "checked_minus_lists": difference,
        "outcome": outcome,
    }


def _ratio(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _primary(worlds: dict[str, _WorldReport]) -> tuple[dict[str, Any], str]:
    differences = [
        difference
        for world in worlds.values()
        if (difference := world["checked_minus_lists"]) is not None
    ]
    teams_per_arm = len(_WORLDS) * len(_REPETITIONS)
    result: dict[str, Any] = {
        "worlds": len(_WORLDS),
        "matched_worlds": len(differences),
        "teams_per_arm": teams_per_arm,
        "wins": None,
        "ties": None,
        "losses": None,
        "checked_minus_lists": None,
        "mean_delta": None,
        "two_sided_sign_flip_p": None,
    }
    if len(differences) != len(_WORLDS):
        return result, "inconclusive"

    difference = sum(differences)
    delta = Fraction(difference, teams_per_arm)
    p = _sign_flip_p(differences)
    result.update(
        wins=sum(value > 0 for value in differences),
        ties=differences.count(0),
        losses=sum(value < 0 for value in differences),
        checked_minus_lists=difference,
        mean_delta=_ratio(delta),
        two_sided_sign_flip_p=_ratio(p),
    )
    verdict = (
        "pass"
        if delta >= Fraction(1, 5) and p <= Fraction(1, 20)
        else "not_established"
    )
    return result, verdict


def summarize(receipt: Any) -> dict[str, Any]:
    teams = _grid(receipt)
    worlds = {world: _world_report(teams, world) for world in _WORLDS}
    primary, verdict = _primary(worlds)
    return {
        "arms": {
            arm: {
                **_counts(
                    teams[world, arm, repeat]
                    for world in _WORLDS
                    for repeat in _REPETITIONS
                ),
                "repeat_disagreement": _repeat_disagreement(teams, arm),
            }
            for arm in _ARMS
        },
        "by_wave": {
            f"r{repeat:02d}": {
                arm: _counts(teams[world, arm, repeat] for world in _WORLDS)
                for arm in _ARMS
            }
            for repeat in _REPETITIONS
        },
        "by_world": worlds,
        "primary": primary,
        "verdict": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a native calendar repeatability receipt.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    args = parser.parse_args()
    try:
        with args.receipt.open("rb") as stream:
            data = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(data) > _MAX_RECEIPT_BYTES:
            raise ValueError("oversize native repeatability receipt")
        receipt = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        result = summarize(receipt)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        parser.error(f"cannot summarize the native repeatability receipt: {error}")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
