import argparse
import json
from fractions import Fraction
from math import ceil
from pathlib import Path
from typing import Any, NamedTuple

from ..calendar_case import _directory, _json, _read_at
from ..visibility import calendar_packet as packet
from . import transfer_result
from .repeat_summary import _ratio
from .visible_replay_summary import _sign_flip_p

RECEIPT_SCHEMA = "seatlab.calendar-transfer-fixed-receiver/v1"
SUMMARY_SCHEMA = "seatlab.calendar-transfer-fixed-receiver-summary/v1"
REGISTERED_COHORT_SHA256 = (
    "8995d5311e9627989a1a35065902ef89a39f72eb693409532a95513b22a4a61a"
)
_ARMS = ("lists", "checked")
_STATUSES = ("scored", "invalid", "missing", "not_attempted")
_REPETITIONS = 2
_MAX_BYTES = 128 * 1024
_MIN_GAIN_RATE = Fraction(5, 24)
_MAX_P = Fraction(1, 20)


class _Arm(NamedTuple):
    first: tuple[tuple[str, int | None], tuple[str, int | None]]
    same_slot: bool | None


class _World(NamedTuple):
    target_visible: bool
    arms: dict[str, _Arm]


def _native_lists(
    native: Any, manifest: Path, cohort_sha256: str
) -> dict[str, dict[str, Any]]:
    transfer_result.summarize(native, manifest, cohort_sha256)
    sources = {row["world"]: row for row in native["cells"] if row["arm"] == "lists"}
    if any(row["status"] != "complete" for row in sources.values()):
        raise ValueError("all enrolled native lists sources must be complete")
    return sources


def _decision(value: object) -> tuple[str, int | None]:
    row = packet._object(value, "status", "correct")
    status, correct = row["status"], row["correct"]
    if status not in _STATUSES:
        raise ValueError("unregistered first decision status")
    if status == "scored":
        if type(correct) is not int or correct not in (0, 1):
            raise ValueError("scored first decision requires a binary integer grade")
    elif correct is not None:
        raise ValueError("unscored first decision cannot have a grade")
    return status, correct


def _arm(value: object) -> _Arm:
    row = packet._object(value, "first", "same_slot")
    first = packet._list(row["first"])
    if len(first) != _REPETITIONS:
        raise ValueError("each format requires two first decisions")
    left, right = map(_decision, first)
    a, b, same_slot = left[1], right[1], row["same_slot"]
    if a is None or b is None:
        if same_slot is not None:
            raise ValueError("slot agreement requires two scored decisions")
    elif (
        type(same_slot) is not bool
        or (same_slot and a != b)
        or (not same_slot and a == b == 1)
    ):
        raise ValueError("slot agreement disagrees with the decision grades")
    return _Arm((left, right), same_slot)


def _worlds(
    receipt: object, sources: dict[str, dict[str, Any]], cohort_sha256: str
) -> list[_World]:
    data = packet._object(receipt, "schema", "cohort_sha256", "worlds")
    if data["schema"] != RECEIPT_SCHEMA or data["cohort_sha256"] != cohort_sha256:
        raise ValueError("invalid fixed receiver cohort binding")
    rows = packet._list(data["worlds"])
    if len(rows) != len(sources):
        raise ValueError("expected exactly one row per enrolled native lists source")
    seen: set[str] = set()
    worlds: list[_World] = []
    for value in rows:
        row = packet._object(
            value,
            "world",
            "trial_uuid",
            "result_sha256",
            "target_visible",
            "views",
            "arms",
        )
        world = row["world"]
        if not isinstance(world, str) or world not in sources or world in seen:
            raise ValueError("unregistered or duplicate receiver source world")
        seen.add(world)
        native = sources[world]
        if (
            row["trial_uuid"] != native["trial_uuid"]
            or row["result_sha256"] != native["result_sha256"]
            or row["target_visible"] is not native["target_visible"]
        ):
            raise ValueError("receiver source differs from the native lists record")
        views = packet._object(row["views"], *_ARMS)
        lists_digest = packet._check_digest(views["lists"])
        if lists_digest == packet._check_digest(views["checked"]):
            raise ValueError("the two full prompt commitments must differ")
        arms = packet._object(row["arms"], *_ARMS)
        worlds.append(
            _World(native["target_visible"], {arm: _arm(arms[arm]) for arm in _ARMS})
        )
    return worlds


def _arm_reports(worlds: list[_World]) -> dict[str, dict[str, Any]]:
    reports = {}
    for arm in _ARMS:
        counts = dict.fromkeys(_STATUSES, 0)
        correct = pairs = slot_disagreements = grade_disagreements = 0
        for world in worlds:
            first, same_slot = world.arms[arm]
            for status, grade in first:
                counts[status] += 1
                if grade is not None:
                    correct += grade
            if same_slot is not None:
                pairs += 1
                slot_disagreements += not same_slot
                grade_disagreements += first[0][1] != first[1][1]
        reports[arm] = {
            "coverage": {"expected": len(worlds) * _REPETITIONS, **counts},
            "correct": correct,
            "denominator": counts["scored"],
            "slot_disagreement": {
                "disagreements": slot_disagreements,
                "denominator": pairs,
            },
            "grade_disagreement": {
                "disagreements": grade_disagreements,
                "denominator": pairs,
            },
        }
    return reports


def _primary(worlds: list[_World]) -> tuple[dict[str, Any], str | None]:
    differences = []
    for world in worlds:
        lists, checked = (world.arms[arm] for arm in _ARMS)
        if lists.same_slot is not None and checked.same_slot is not None:
            checked_correct = sum(grade or 0 for _, grade in checked.first)
            lists_correct = sum(grade or 0 for _, grade in lists.first)
            differences.append(checked_correct - lists_correct)
    wins = sum(difference > 0 for difference in differences)
    losses = sum(difference < 0 for difference in differences)
    decisions_per_arm = len(worlds) * _REPETITIONS
    minimum_gain = ceil(_MIN_GAIN_RATE * decisions_per_arm)
    report: dict[str, Any] = {
        "enrolled_worlds": len(worlds),
        "matched_worlds": len(differences),
        "wins": wins,
        "ties": len(differences) - wins - losses,
        "losses": losses,
        "discordant_worlds": wins + losses,
        "decisions_per_arm": decisions_per_arm,
        "minimum_gain": minimum_gain,
        "maximum_p": _ratio(_MAX_P),
        "checked_minus_lists": None,
        "mean_delta": None,
        "two_sided_sign_flip_p": None,
    }
    if len(differences) != len(worlds):
        return report, None
    gain = sum(differences)
    p = _sign_flip_p(differences)
    report.update(
        checked_minus_lists=gain,
        mean_delta=float(Fraction(gain, decisions_per_arm)),
        two_sided_sign_flip_p=_ratio(p),
    )
    return report, "pass" if gain >= minimum_gain and p <= _MAX_P else "not_established"


def summarize(
    receipt: object, native: object, manifest: Path, cohort_sha256: str
) -> dict[str, Any]:
    sources = _native_lists(native, manifest, cohort_sha256)
    worlds = _worlds(receipt, sources, cohort_sha256)
    primary, outcome = _primary(worlds)
    registered = cohort_sha256 == REGISTERED_COHORT_SHA256 and len(worlds) == 24
    visibility = {}
    for label, visible in (("exposed", True), ("not_exposed", False)):
        subset = [world for world in worlds if world.target_visible is visible]
        visibility[label] = {"worlds": len(subset), "arms": _arm_reports(subset)}
    return {
        "schema": SUMMARY_SCHEMA,
        "cohort_sha256": cohort_sha256,
        "registration": "registered" if registered else "fresh_replication",
        "evidence_complete": outcome is not None,
        "verdict": outcome if registered else None,
        "replication_verdict": None if registered else outcome,
        "native_lists": {
            "correct": sum(row["reward"] for row in sources.values()),
            "denominator": len(sources),
        },
        "arms": _arm_reports(worlds),
        "by_target_visible": visibility,
        "primary": primary,
        "proof_scope": (
            "Binds source identity, visibility, and original outcome to the supplied "
            "native receipt. Does not verify private prompts, replies, or receiver grades."
        ),
    }


def _read(path: Path) -> object:
    with _directory(path.parent) as directory:
        return _json(_read_at(directory, path.name, _MAX_BYTES))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize a fixed calendar transfer receiver receipt.",
        allow_abbrev=False,
    )
    parser.add_argument("receipt", type=Path, metavar="RECEIPT")
    parser.add_argument("--native", type=Path, required=True, metavar="NATIVE")
    parser.add_argument("--manifest", type=Path, required=True, metavar="MANIFEST")
    parser.add_argument("--sha256", required=True, metavar="SHA")
    args = parser.parse_args(argv)
    try:
        result = summarize(
            _read(args.receipt), _read(args.native), args.manifest, args.sha256
        )
    except (OSError, TypeError, ValueError, RecursionError):
        parser.error("invalid or unreadable fixed calendar transfer receiver evidence")
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
