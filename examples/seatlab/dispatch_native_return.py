"""Aggregate one owner-controlled stationary Dispatch Handoff trial offline."""

from pathlib import Path
from typing import Any
from uuid import UUID

if __package__:
    from . import dispatch_returns
    from .evidence import TrialEvidence, read_trial_with_world
    from .export_capsules import reconstructed_capsules
else:
    import dispatch_returns
    from evidence import TrialEvidence, read_trial_with_world
    from export_capsules import reconstructed_capsules

_BUCKETS = (
    "qualified_different_latest",
    "qualified_different_last",
    "qualified_different_neither",
    "qualified_same_best",
    "qualified_same_other",
)


def _reordered_counts(cases: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    truthful = dict.fromkeys(_BUCKETS, 0)
    truthful["unqualified_reports"] = 0
    delivered = dict.fromkeys(_BUCKETS, 0)
    incorrect = dict.fromkeys(_BUCKETS, 0)
    for case in cases:
        first, second = case["reports"]
        latest, last = case["visible"]
        rank = case["utilities"].__getitem__
        bucket = dispatch_returns.choice_bucket(
            case["chosen"],
            max(latest["available"], key=rank),
            max(last["available"], key=rank),
        )
        delivered[bucket] += 1
        if (
            first["available"] == case["before"]
            and second["available"] == case["after"]
        ):
            truthful[bucket] += 1
        else:
            truthful["unqualified_reports"] += 1
            incorrect[bucket] += 1
    return {
        "truthful_rules": truthful,
        "delivered_rules": delivered,
        "delivered_with_incorrect_inventory": incorrect,
    }


def checked_trial(
    path: Path, expected_trial: str, world_type: type
) -> tuple[TrialEvidence, dict[str, Any], dict[str, str]]:
    """Check native identity and latent cases, not authenticity of the unsigned files."""
    if not isinstance(expected_trial, str) or len(expected_trial) != 36:
        raise ValueError("invalid expected trial UUID")
    try:
        trial_id = UUID(expected_trial)
    except ValueError:
        raise ValueError("invalid expected trial UUID") from None
    if not trial_id.int or str(trial_id) != expected_trial:
        raise ValueError("invalid expected trial UUID")

    try:
        receipt, dossier = read_trial_with_world(path)
        observations: dict[str, str] = {
            role: capsule["basis"]
            for role, capsule in reconstructed_capsules(dossier).items()
        }
    except FileNotFoundError:
        raise
    except (OSError, ValueError, RecursionError):
        raise ValueError("invalid native trial evidence") from None
    if receipt["trial_id"] != expected_trial:
        raise ValueError("native trial UUID mismatch")

    _, fingerprint = dispatch_returns._latent_evidence(
        world_type, receipt["seed"], receipt["score"]["episodes"]
    )
    if receipt["fingerprint"] != fingerprint:
        raise ValueError("native dossier fingerprint mismatch")
    return receipt, dossier, observations


def inspect_trial(path: Path, expected_trial: str, world_type: type) -> dict[str, Any]:
    receipt, dossier, observations = checked_trial(path, expected_trial, world_type)
    seed, condition, score = receipt["seed"], receipt["condition"], receipt["score"]
    episodes = score["episodes"]

    reordered = condition == "reordered"
    result: dict[str, Any] = {
        "schema": "seatlab.dispatch-native-return/v1",
        "trial_id": expected_trial,
        "seed": seed,
        "condition": condition,
        "episodes": episodes,
        "status": "measured" if reordered else "not_reordered",
        "grade": {"optimal": score["optimal"], "feasible": score["feasible"]},
        "observations": observations,
        "truthful_rules": None,
        "delivered_rules": None,
        "delivered_with_incorrect_inventory": None,
    }
    if reordered:
        result.update(_reordered_counts(dossier["cases"]))
    return result
