import argparse
import json
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID

from .visible_replay_summary import _digest, _sign_flip_p, _unique_object

_SCHEMA = "seatlab.calendar-staged-report-study/v1"
_WORLDS = tuple(f"w{i:02d}" for i in range(24, 48))
_DONORS = ("lists", "checked")
_TIMINGS = ("immediate", "delayed")
_PAIR_COUNT = len(_WORLDS) * len(_DONORS)
_MAX_RECEIPT_BYTES = 128 * 1024
_SOURCE_FIELDS = {
    "world",
    "donor_arm",
    "trial_uuid",
    "result_sha256",
    "first_prompt_sha256",
    "first_reply_sha256",
    "same_second_day",
    "arms",
}
_ARM_FIELDS = {
    "status",
    "stage",
    "correct",
    "target_visible",
    "second_prompt_sha256",
    "second_reply_sha256",
    "book_prompt_sha256",
    "book_reply_sha256",
}


def _stage_digest(value: Any, present: bool) -> None:
    if present:
        _digest(value)
    elif value is not None:
        raise ValueError("unexpected digest for a skipped call or a missing reply")


def _validate_arm(arm: Any) -> None:
    if not isinstance(arm, dict) or arm.keys() != _ARM_FIELDS:
        raise ValueError("invalid timing arm fields")
    status, stage = arm["status"], arm["stage"]
    if status not in ("scored", "invalid", "missing") or stage not in (
        "first",
        "second",
        "book",
    ):
        raise ValueError("unregistered status or stage")
    booking = stage == "book"
    correct, visible = arm["correct"], arm["target_visible"]
    if status == "scored":
        if not booking or type(correct) is not int or correct not in (0, 1):
            raise ValueError("only a scored booking has a binary integer grade")
    elif correct is not None:
        raise ValueError("an invalid or missing reply cannot have a grade")
    if booking:
        if type(visible) is not bool and (visible is not None or status != "missing"):
            raise ValueError(
                "an observed booking reply requires Boolean target visibility"
            )
    elif visible is not None:
        raise ValueError("target visibility is only observed at booking")

    _stage_digest(arm["second_prompt_sha256"], stage != "first")
    _stage_digest(
        arm["second_reply_sha256"],
        booking or (stage == "second" and status == "invalid"),
    )
    _stage_digest(arm["book_prompt_sha256"], booking)
    _stage_digest(arm["book_reply_sha256"], booking and status != "missing")


def _validate_pair(row: dict[str, Any]) -> None:
    arms = row["arms"]
    if not isinstance(arms, dict) or arms.keys() != set(_TIMINGS):
        raise ValueError("expected both timing arms")
    immediate, delayed = (arms[timing] for timing in _TIMINGS)
    _validate_arm(immediate)
    _validate_arm(delayed)
    _digest(row["first_prompt_sha256"])
    first = immediate["stage"] == "first"
    if first or delayed["stage"] == "first":
        if immediate != delayed:
            raise ValueError("both timing arms share the first outcome")
    elif immediate["second_prompt_sha256"] == delayed["second_prompt_sha256"]:
        raise ValueError(
            "the second decision must distinguish released and withheld reports"
        )
    _stage_digest(
        row["first_reply_sha256"], not first or immediate["status"] == "invalid"
    )

    same_day = row["same_second_day"]
    if immediate["stage"] != "book" or delayed["stage"] != "book":
        if same_day is not None:
            raise ValueError("day comparison requires two valid second choices")
        return
    if type(same_day) is not bool:
        raise ValueError("expected a Boolean second-day comparison")
    shared = immediate["book_prompt_sha256"] == delayed["book_prompt_sha256"]
    if shared != same_day:
        raise ValueError("booking prompts must match exactly when the days match")
    if shared and any(
        immediate[field] != delayed[field]
        for field in ("book_reply_sha256", "correct", "status", "target_visible")
    ):
        raise ValueError("both timing arms share the booking outcome")


def _sources(receipt: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(receipt, dict)
        or receipt.keys() != {"schema", "sources"}
        or receipt["schema"] != _SCHEMA
    ):
        raise ValueError("expected a staged calendar report study receipt")
    rows = receipt["sources"]
    if not isinstance(rows, list) or len(rows) != _PAIR_COUNT:
        raise ValueError("expected all 48 registered sources")
    cells: set[tuple[str, str]] = set()
    trials: set[str] = set()
    results: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.keys() != _SOURCE_FIELDS:
            raise ValueError("invalid source fields")
        world, donor = row["world"], row["donor_arm"]
        if world not in _WORLDS or donor not in _DONORS or (world, donor) in cells:
            raise ValueError("unregistered or duplicate source cell")
        trial = row["trial_uuid"]
        if not isinstance(trial, str) or str(UUID(trial)) != trial:
            raise ValueError("expected a canonical source trial UUID")
        result = _digest(row["result_sha256"])
        if trial in trials or result in results:
            raise ValueError("duplicate source trial UUID or result digest")
        _validate_pair(row)
        cells.add((world, donor))
        trials.add(trial)
        results.add(result)
    return rows


def _ratio(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _primary(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    worlds = dict.fromkeys(_WORLDS, 0)
    incomplete: set[str] = set()
    for row in rows:
        world = row["world"]
        immediate, delayed = (row["arms"][timing] for timing in _TIMINGS)
        if immediate["status"] == "missing" or delayed["status"] == "missing":
            incomplete.add(world)
        else:
            worlds[world] += int(immediate["correct"] == 1) - int(
                delayed["correct"] == 1
            )
    differences = [delta for world, delta in worlds.items() if world not in incomplete]
    result: dict[str, Any] = {
        "worlds": len(differences),
        "wins": sum(delta > 0 for delta in differences),
        "ties": differences.count(0),
        "losses": sum(delta < 0 for delta in differences),
        "immediate_minus_delayed": None,
        "mean_delta": None,
        "two_sided_sign_flip_p": None,
    }
    if incomplete:
        return result, "inconclusive"

    difference = sum(differences)
    mean_delta = Fraction(difference, _PAIR_COUNT)
    p = _sign_flip_p(differences)
    result.update(
        immediate_minus_delayed=difference,
        mean_delta=_ratio(mean_delta),
        two_sided_sign_flip_p=_ratio(p),
    )
    verdict = (
        "pass"
        if mean_delta >= Fraction(1, 10) and p <= Fraction(1, 20)
        else "not_established"
    )
    return result, verdict


def summarize(receipt: Any) -> dict[str, Any]:
    rows = _sources(receipt)
    counts = {
        timing: dict.fromkeys(("successes", "invalid", "missing", "target_visible"), 0)
        for timing in _TIMINGS
    }
    changed = shared = 0
    for row in rows:
        for timing, arm in row["arms"].items():
            counts[timing]["successes"] += arm["correct"] == 1
            counts[timing]["invalid"] += arm["status"] == "invalid"
            counts[timing]["missing"] += arm["status"] == "missing"
            counts[timing]["target_visible"] += arm["target_visible"] is True
        changed += row["same_second_day"] is False
        shared += (
            row["same_second_day"] is True
            and row["arms"]["immediate"]["book_reply_sha256"] is not None
        )
    primary, verdict = _primary(rows)
    return {
        "verdict": verdict,
        "arms": counts,
        "primary": primary,
        "reads": {"changed_second_day": changed, "shared_finals": shared},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize a staged calendar report study receipt.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    args = parser.parse_args()
    try:
        with args.receipt.open("rb") as stream:
            data = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(data) > _MAX_RECEIPT_BYTES:
            raise ValueError("oversize staged report receipt")
        receipt = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        result = summarize(receipt)
    except (OSError, ValueError, RecursionError):
        parser.error("cannot read or summarize the staged report receipt")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
