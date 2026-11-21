import argparse
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any, NamedTuple, TypedDict
from uuid import UUID

_SCHEMA = "seatlab.calendar-visible-fixed-replay/v1"
_WORLDS = tuple(f"w{i:02d}" for i in range(24, 48))
_ARMS = ("lists", "checked")
_REPEATS = (0, 1)
_GRID = frozenset(
    (world, arm, repeat) for world in _WORLDS for arm in _ARMS for repeat in _REPEATS
)
_SOURCE_FIELDS = {
    "world",
    "trial_uuid",
    "result_sha256",
    "target_visible",
    "factual_prompt_sha256",
    "views",
}
_OBSERVATION_FIELDS = {
    "world",
    "arm",
    "repeat",
    "source_trial_uuid",
    "prompt_sha256",
    "status",
    "correct",
    "reply_sha256",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_RECEIPT_BYTES = 128 * 1024


class _Source(NamedTuple):
    trial_uuid: str
    result_sha256: str
    target_visible: bool
    views: dict[str, str]


class _Observation(NamedTuple):
    status: str
    correct: int | None


class _ArmCounts(TypedDict):
    successes: int
    eligible: int
    invalid: int
    missing: int
    repeat_disagreement: dict[str, int]


_MISSING = _Observation("missing", None)
type _Observations = dict[tuple[str, str, int], _Observation]


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("expected a lowercase SHA-256 digest")
    return value


def _source(row: dict[str, Any]) -> _Source:
    trial_uuid = row["trial_uuid"]
    if not isinstance(trial_uuid, str) or str(UUID(trial_uuid)) != trial_uuid:
        raise ValueError("expected a canonical source trial UUID")
    target_visible = row["target_visible"]
    if type(target_visible) is not bool:
        raise ValueError("expected a Boolean source visibility flag")
    views = row["views"]
    if not isinstance(views, dict) or views.keys() != set(_ARMS):
        raise ValueError("expected both registered source views")
    digests = {arm: _digest(views[arm]) for arm in _ARMS}
    factual = _digest(row["factual_prompt_sha256"])
    if digests["lists"] != factual or digests["checked"] == factual:
        raise ValueError("invalid factual or checked view binding")
    return _Source(trial_uuid, _digest(row["result_sha256"]), target_visible, digests)


def _sources(rows: Any) -> dict[str, _Source]:
    if not isinstance(rows, list) or len(rows) != len(_WORLDS):
        raise ValueError("expected all registered sources")
    sources: dict[str, _Source] = {}
    trial_uuids: set[str] = set()
    results: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.keys() != _SOURCE_FIELDS:
            raise ValueError("invalid source fields")
        world = row["world"]
        if world not in _WORLDS or world in sources:
            raise ValueError("unregistered or duplicate source world")
        source = _source(row)
        if source.trial_uuid in trial_uuids or source.result_sha256 in results:
            raise ValueError("duplicate source trial UUID or result digest")
        sources[world] = source
        trial_uuids.add(source.trial_uuid)
        results.add(source.result_sha256)
    return sources


def _observation(row: dict[str, Any], source: _Source, arm: str) -> _Observation:
    if (
        row["source_trial_uuid"] != source.trial_uuid
        or row["prompt_sha256"] != source.views[arm]
    ):
        raise ValueError("invalid observation source or prompt binding")
    status, correct, reply = row["status"], row["correct"], row["reply_sha256"]
    if status == "scored":
        if type(correct) is not int or correct not in (0, 1):
            raise ValueError("expected a binary integer grade")
        _digest(reply)
    elif status == "invalid":
        if correct is not None:
            raise ValueError("invalid reply cannot have a grade")
        _digest(reply)
    elif status == "missing":
        if correct is not None or reply is not None:
            raise ValueError("missing observation cannot have a grade or reply digest")
    else:
        raise ValueError("unregistered observation status")
    return _Observation(status, correct)


def _observations(rows: Any, sources: dict[str, _Source]) -> _Observations:
    if not isinstance(rows, list) or len(rows) > len(_GRID):
        raise ValueError("expected a list of registered observations")
    observations: _Observations = {}
    for row in rows:
        if not isinstance(row, dict) or row.keys() != _OBSERVATION_FIELDS:
            raise ValueError("invalid observation fields")
        world, arm, repeat = row["world"], row["arm"], row["repeat"]
        if world not in _WORLDS or arm not in _ARMS or type(repeat) is not int:
            raise ValueError("unregistered observation cell")
        key = world, arm, repeat
        if key not in _GRID or key in observations:
            raise ValueError("unregistered or duplicate observation cell")
        observations[key] = _observation(row, sources[world], arm)
    return observations


def _arm_counts(
    observations: _Observations, worlds: tuple[str, ...], arm: str
) -> _ArmCounts:
    successes = eligible = invalid = missing = disagreements = pairs = 0
    for world in worlds:
        first, second = (
            observations.get((world, arm, repeat), _MISSING) for repeat in _REPEATS
        )
        for item in (first, second):
            if item.correct is not None:
                eligible += 1
                successes += item.correct
            invalid += item.status == "invalid"
            missing += item.status == "missing"
        if first.correct is not None and second.correct is not None:
            pairs += 1
            disagreements += first.correct != second.correct
    return {
        "successes": successes,
        "eligible": eligible,
        "invalid": invalid,
        "missing": missing,
        "repeat_disagreement": {"disagreements": disagreements, "denominator": pairs},
    }


def _arm_reports(
    observations: _Observations, worlds: tuple[str, ...]
) -> dict[str, _ArmCounts]:
    return {arm: _arm_counts(observations, worlds, arm) for arm in _ARMS}


def _sign_flip_p(differences: list[int]) -> Fraction:
    distribution = Counter({0: 1})
    for difference in differences:
        weight = abs(difference)
        if weight == 0:
            continue
        next_distribution: Counter[int] = Counter()
        for total, count in distribution.items():
            next_distribution[total - weight] += count
            next_distribution[total + weight] += count
        distribution = next_distribution
    observed = abs(sum(differences))
    tail = sum(count for total, count in distribution.items() if abs(total) >= observed)
    return Fraction(tail, sum(distribution.values()))


def _primary(
    world_arms: dict[str, dict[str, _ArmCounts]],
) -> tuple[dict[str, Any], str]:
    differences = [
        arms["checked"]["successes"] - arms["lists"]["successes"]
        for arms in world_arms.values()
        if all(arms[arm]["eligible"] == len(_REPEATS) for arm in _ARMS)
    ]
    wins = sum(difference > 0 for difference in differences)
    losses = sum(difference < 0 for difference in differences)
    result: dict[str, Any] = {
        "matched_worlds": len(differences),
        "wins": wins,
        "ties": len(differences) - wins - losses,
        "losses": losses,
        "discordant_worlds": wins + losses,
        "checked_minus_lists": None,
        "mean_delta": None,
        "two_sided_sign_flip_p": None,
    }
    if len(differences) != len(_WORLDS):
        return result, "inconclusive"

    difference = sum(differences)
    delta = Fraction(difference, len(_WORLDS) * len(_REPEATS))
    p = _sign_flip_p(differences)
    result.update(
        checked_minus_lists=difference,
        mean_delta=float(delta),
        two_sided_sign_flip_p={"numerator": p.numerator, "denominator": p.denominator},
    )
    verdict = (
        "pass"
        if delta >= Fraction(1, 10) and p <= Fraction(1, 20)
        else "not_established"
    )
    return result, verdict


def summarize(receipt: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(receipt, dict)
        or receipt.keys() != {"schema", "sources", "observations"}
        or receipt["schema"] != _SCHEMA
    ):
        raise ValueError("expected a fixed calendar visible replay receipt")
    sources = _sources(receipt["sources"])
    observations = _observations(receipt["observations"], sources)
    world_arms = {world: _arm_reports(observations, (world,)) for world in _WORLDS}
    primary, verdict = _primary(world_arms)
    exposure = {}
    for label, visible in (("exposed", True), ("not_exposed", False)):
        worlds = tuple(
            world for world in _WORLDS if sources[world].target_visible is visible
        )
        exposure[label] = {
            "worlds": len(worlds),
            "arms": _arm_reports(observations, worlds),
        }
    return {
        "arms": _arm_reports(observations, _WORLDS),
        "by_world": {
            world: {"target_visible": sources[world].target_visible, "arms": arms}
            for world, arms in world_arms.items()
        },
        "by_target_visible": exposure,
        "primary": primary,
        "verdict": verdict,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON object field")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a fixed calendar visible replay receipt.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    args = parser.parse_args()
    try:
        with args.receipt.open("rb") as stream:
            data = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(data) > _MAX_RECEIPT_BYTES:
            raise ValueError("oversize replay receipt")
        receipt = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        result = summarize(receipt)
    except (OSError, ValueError, TypeError, RecursionError):
        parser.error("cannot read or summarize the fixed replay receipt")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
