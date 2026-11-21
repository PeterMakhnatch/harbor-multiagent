import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

from . import repeat_summary, team_views
from .visible_replay_summary import _unique_object

PANEL_SCHEMA = "seatlab.native-calendar-turn-panel/v1"
PAIR_SCHEMA = "seatlab.native-calendar-turn-pair/v1"
_SCHEMA = "seatlab.native-calendar-turn-summary/v1"
_PAIR_FIELDS = {"schema", "world", "arm", "source_sha256", "trials", "turns"}
_TRIAL_FIELDS = {"run", "trial_uuid", "result_sha256", "reward"}
_TURN_FIELDS = {"phase", "seat", "same_prompt", "same_action"}
_MIN_DISAGREEMENTS = 8
_MAX_PANEL_BYTES = 128 * 1024
_DIRECTIONS = {
    (True, True): "same_prompt_same_action",
    (True, False): "same_prompt_different_action",
    (False, True): "different_prompt_same_action",
    (False, False): "different_prompt_different_action",
}


class _Turn(NamedTuple):
    same_prompt: bool
    same_action: bool


class _Pair(NamedTuple):
    rewards: tuple[int, int]
    turns: tuple[_Turn, ...]


type _Team = dict[str, Any]
type _Enrollment = dict[tuple[str, str], tuple[_Team, _Team]]


def _bind_trial(trial: Any, native: _Team) -> int:
    if not isinstance(trial, dict) or trial.keys() != _TRIAL_FIELDS:
        raise ValueError("unexpected native turn trial fields")
    reward = trial["reward"]
    if type(reward) is not int or any(
        trial[key] != native[key] for key in _TRIAL_FIELDS
    ):
        raise ValueError("native turn trial does not match its enrolled repetition")
    return reward


def _turns(rows: Any, cell: team_views.JoinCell) -> tuple[_Turn, ...]:
    if not isinstance(rows, list) or len(rows) != len(team_views.PHASES):
        raise ValueError("expected all four registered native turns")
    actors = (cell.sender, cell.sender, cell.receiver, cell.receiver)
    turns: list[_Turn] = []
    same_prefix = True
    for row, phase, seat in zip(rows, team_views.PHASES, actors, strict=True):
        if not isinstance(row, dict) or row.keys() != _TURN_FIELDS:
            raise ValueError("unexpected native turn fields")
        if (row["phase"], row["seat"]) != (phase, seat):
            raise ValueError("native turn does not match its registered phase and seat")
        same_prompt, same_action = row["same_prompt"], row["same_action"]
        if type(same_prompt) is not bool or type(same_action) is not bool:
            raise ValueError("native turn comparison flags must be Boolean")
        if same_prefix and not same_prompt:
            raise ValueError("unchanged earlier actions must give the same role prompt")
        same_prefix = same_prefix and same_action
        turns.append(_Turn(same_prompt, same_action))
    return tuple(turns)


def _bind_pair(pair: dict[str, Any], left: _Team, right: _Team) -> _Pair:
    source = pair["source_sha256"]
    if source != left["source_sha256"] or source != right["source_sha256"]:
        raise ValueError("native turn pair does not match the enrolled stock semantics")
    trials = pair["trials"]
    if not isinstance(trials, list) or len(trials) != 2:
        raise ValueError("expected two native trials in r01/r02 order")
    rewards = (_bind_trial(trials[0], left), _bind_trial(trials[1], right))
    turns = _turns(pair["turns"], team_views.JoinCell(pair["world"], pair["arm"]))
    if turns[-1].same_prompt and left["target_visible"] != right["target_visible"]:
        raise ValueError("identical final prompts must expose the same stock evidence")
    if turns[-1].same_action and rewards[0] != rewards[1]:
        raise ValueError(
            "identical final actions on identical stock must agree in reward"
        )
    if not turns[-1].same_action and rewards == (1, 1):
        raise ValueError("two successful teams must choose the unique stock meeting")
    return _Pair(rewards, turns)


def _panel_pairs(panel: Any, expected: _Enrollment) -> list[_Pair]:
    if (
        not isinstance(panel, dict)
        or panel.keys() != {"schema", "pairs"}
        or panel["schema"] != PANEL_SCHEMA
    ):
        raise ValueError("expected a native calendar turn panel")
    rows = panel["pairs"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("expected exactly the pairs with two complete native teams")
    pairs: list[_Pair] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.keys() != _PAIR_FIELDS
            or row["schema"] != PAIR_SCHEMA
        ):
            raise ValueError("unexpected native turn pair schema or fields")
        world, arm = row["world"], row["arm"]
        if not isinstance(world, str) or not isinstance(arm, str):
            raise TypeError("invalid native turn pair coordinates")
        members = expected.pop((world, arm), None)
        if members is None:
            raise ValueError("unknown, non-complete, or duplicate native turn pair")
        pairs.append(_bind_pair(row, *members))
    return pairs


def _counts(pairs: list[_Pair]) -> dict[str, Any]:
    per_phase = {
        phase: dict.fromkeys(_DIRECTIONS.values(), 0) for phase in team_views.PHASES
    }
    first_unequal = dict.fromkeys((*team_views.PHASES, "no_change"), 0)
    for pair in pairs:
        first = "no_change"
        for phase, turn in zip(team_views.PHASES, pair.turns, strict=True):
            per_phase[phase][_DIRECTIONS[turn]] += 1
            if not turn.same_action and first == "no_change":
                first = phase
        first_unequal[first] += 1
    return {
        "denominator": len(pairs),
        "per_phase": per_phase,
        "first_unequal_action": first_unequal,
    }


def _registered_rule(
    complete: bool, numerator: int, denominator: int
) -> tuple[str, str]:
    if not complete:
        return "insufficient", "incomplete_native_cohort"
    if denominator < _MIN_DISAGREEMENTS:
        return "insufficient", "too_few_disagreements"
    if 2 * numerator > denominator:
        return "supported", "strict_majority"
    if 2 * numerator == denominator:
        return "not_supported", "tie"
    return "not_supported", "below_half"


def summarize(native_receipt: Any, panel: Any) -> dict[str, Any]:
    repeat_summary.summarize(native_receipt)
    teams = {
        (row["world"], row["arm"], row["repetition"]): row
        for row in native_receipt["teams"]
    }
    statuses = Counter(row["status"] for row in teams.values())
    all_complete = statuses["complete"] == len(teams)
    enrolled = [
        (teams[world, arm, 1], teams[world, arm, 2])
        for world in team_views.JOIN_WORLDS
        for arm in team_views.JOIN_ARMS
    ]
    pairs = _panel_pairs(
        panel,
        {
            (left["world"], left["arm"]): (left, right)
            for left, right in enrolled
            if left["status"] == right["status"] == "complete"
        },
    )
    discordant = [pair for pair in pairs if pair.rewards[0] != pair.rewards[1]]
    same_reward = [pair for pair in pairs if pair.rewards[0] == pair.rewards[1]]
    numerator = sum(pair.turns[-1].same_prompt for pair in discordant)
    denominator = len(discordant)
    verdict, reason = _registered_rule(all_complete, numerator, denominator)
    return {
        "schema": _SCHEMA,
        "scope": "descriptive_world_by_arm",
        "worlds": len(team_views.JOIN_WORLDS),
        "native_teams": {
            "enrolled": len(teams),
            "complete": statuses["complete"],
            "model_invalid": statuses["model_invalid"],
            "missing": statuses["missing"],
            "all_complete": all_complete,
        },
        "verdict": verdict,
        "registered_rule": {
            "minimum_disagreements": _MIN_DISAGREEMENTS,
            "same_final_prompt_among_disagreements": {
                "numerator": numerator,
                "denominator": denominator,
            },
            "reason": reason,
        },
        "pairs": {
            "enrolled": len(enrolled),
            "comparable": _counts(pairs),
            "discordant_reward": _counts(discordant),
            "same_reward": _counts(same_reward),
            "by_reward_transition": {
                f"{first}_to_{second}": _counts(
                    [pair for pair in pairs if pair.rewards == (first, second)]
                )
                for first, second in ((0, 0), (0, 1), (1, 0), (1, 1))
            },
        },
    }


def _read_json(path: Path, limit: int) -> Any:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversize native calendar diagnostic input")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize paired native calendar turns without private data.",
        allow_abbrev=False,
    )
    parser.add_argument("native_receipt", type=Path, metavar="NATIVE_RECEIPT")
    parser.add_argument("panel", type=Path, metavar="PANEL")
    args = parser.parse_args()
    try:
        receipt = _read_json(args.native_receipt, repeat_summary._MAX_RECEIPT_BYTES)
        panel = _read_json(args.panel, _MAX_PANEL_BYTES)
        result = summarize(receipt, panel)
    except (OSError, RecursionError):
        parser.error("cannot read the native calendar diagnostic inputs")
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
