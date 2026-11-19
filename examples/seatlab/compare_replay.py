"""Compare retained evidence from two independent native SeatLab panels.

This does not run a trial. Reconstructed views describe the world input, not
what an actor read, used, or received as exact transport bytes.
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Never, cast, override

if __package__:
    from .capsule import Capsule
    from .evidence import TrialEvidence, read_trial_with_world
    from .export_capsules import reconstructed_capsules
    from .paired import CONDITIONS, _read_receipt, _receipt_cells, read_panel
else:
    from capsule import Capsule
    from evidence import TrialEvidence, read_trial_with_world
    from export_capsules import reconstructed_capsules
    from paired import CONDITIONS, _read_receipt, _receipt_cells, read_panel

_SCHEMA = "seatlab.replay-comparison/v1"
_POLICIES = ("oracle", "arrival", "hosted")
_ACTOR_STAGES = (
    "inventory_view",
    "dispatch_view",
    "inventory_reports",
    "dispatch_choice",
)
_DRIFT = ("drift", "intentional_drift")


class _Invalid(ValueError):
    def __init__(self, code: str, **location: Any) -> None:
        super().__init__(code)
        self.detail = {"code": code, **location}


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message: str) -> Never:
        # argparse's error text can contain local paths or arbitrary arguments.
        raise _Invalid("invalid_arguments")


class _Trial(NamedTuple):
    evidence: TrialEvidence
    world: dict[str, Any]
    capsules: dict[str, Capsule]


class _Panel(NamedTuple):
    policy: str
    hosted_route: dict[str, str] | None
    episodes: int
    trials: dict[tuple[int, str], _Trial]


def _load_panel(root: Path, side: str) -> _Panel:
    try:
        root = root.resolve()
        panel = read_panel(root)
        runs = _receipt_cells(
            _read_receipt(root / "runs.json", "seatlab.panel-runs/v1")
        )
        route = None
        if panel.policy not in _POLICIES:
            raise _Invalid("unknown_panel_policy", panel=side)
        if panel.policy == "hosted":
            plan = _read_receipt(root / "plan.json", "seatlab.panel-plan/v1")
            route = plan.get("hosted_route")
            if (
                plan.get("policy") != panel.policy
                or not isinstance(route, dict)
                or route.keys() != {"model", "provider"}
                or any(
                    not isinstance(value, str) or not value for value in route.values()
                )
            ):
                raise _Invalid("invalid_hosted_route", panel=side)
    except _Invalid:
        raise
    except (OSError, ValueError, RuntimeError):
        raise _Invalid("invalid_or_incomplete_panel", panel=side) from None

    # The panel reader verifies the receipts but omits the trial paths and world
    # dossiers. Rejoin by coordinate and verify each second read against it.
    intended = {(trial["seed"], trial["condition"]): trial for trial in panel.trials}
    if intended.keys() != {(run["seed"], run["condition"]) for run in runs.values()}:
        raise _Invalid("completion_receipt_mismatch", panel=side)
    trials = {}
    trial_ids = set()
    for run in runs.values():
        seed, condition = run["seed"], run["condition"]
        coordinate = (seed, condition)
        location = {"panel": side, "seed": seed, "condition": condition}
        path = run.get("trial_path")
        if (
            run.get("status") != "measured"
            or not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
        ):
            raise _Invalid("invalid_completion_cell", **location)
        try:
            trial_path = (root / path).resolve()
            if not trial_path.is_relative_to(root):
                raise ValueError("trial path leaves panel")
            receipt, world = read_trial_with_world(trial_path)
        except (OSError, ValueError, RuntimeError):
            raise _Invalid("invalid_or_missing_trial", **location) from None
        if receipt != intended[coordinate] or receipt != run.get("evidence"):
            raise _Invalid("native_receipt_mismatch", **location)
        if receipt["trial_id"] in trial_ids:
            raise _Invalid("duplicate_native_trial_id", **location)
        trial_ids.add(receipt["trial_id"])
        try:
            capsules = reconstructed_capsules(world)
        except ValueError:
            raise _Invalid("invalid_actor_observation_receipt", **location) from None
        active_roles = (
            {"dispatch"} if condition == "pooled" else {"inventory", "dispatch"}
        )
        if capsules.keys() != active_roles:
            raise _Invalid("missing_actor_view", **location)
        trials[coordinate] = _Trial(receipt, world, capsules)
    return _Panel(panel.policy, route, panel.trials[0]["score"]["episodes"], trials)


def _different_rows(left: Sequence[Any], right: Sequence[Any]) -> list[int]:
    # The scorer validates the contiguous episode IDs and the order of cases.
    return [
        episode
        for episode, (a, b) in enumerate(zip(left, right, strict=True))
        if a != b
    ]


def _stage(
    episode_ids: list[int], *, different: bool = False, **details: Any
) -> dict[str, Any]:
    return {
        "status": "drift" if episode_ids or different else "match",
        "episode_ids": episode_ids,
        **details,
    }


def _view_stage(left: Capsule | None, right: Capsule | None) -> dict[str, Any]:
    # Matching conditions imply both seats are active or both are parked.
    if left is None or right is None:
        return {"status": "not_applicable", "episode_ids": None}

    def describe(capsule: Capsule) -> dict[str, str]:
        return {
            "canonical_sha256": capsule["sha256"],
            "basis": capsule["basis"],
            "acknowledgment": "verified" if capsule["basis"] == "bound" else "missing",
        }

    a, b = describe(left), describe(right)
    left_view = cast("Mapping[str, Any]", left["view"])
    right_view = cast("Mapping[str, Any]", right["view"])
    same_hash = left["sha256"] == right["sha256"]
    return _stage(
        _different_rows(left_view["cases"], right_view["cases"]),
        different=not same_hash,
        acknowledged_match=(
            same_hash if left["basis"] == right["basis"] == "bound" else None
        ),
        baseline=a,
        candidate=b,
    )


def _cell(baseline: _Trial, candidate: _Trial) -> dict[str, Any]:
    a, b = baseline.evidence, candidate.evidence
    left, right = baseline.world["cases"], candidate.world["cases"]
    a_score, b_score = a["score"], b["score"]
    latent_fields = ("utilities", "before", "after")
    stages = {
        "latent": _stage(
            _different_rows(
                [tuple(case[field] for field in latent_fields) for case in left],
                [tuple(case[field] for field in latent_fields) for case in right],
            ),
            different=a["fingerprint"] != b["fingerprint"],
            baseline_fingerprint=a["fingerprint"],
            candidate_fingerprint=b["fingerprint"],
        ),
        "inventory_view": _view_stage(
            baseline.capsules.get("inventory"), candidate.capsules.get("inventory")
        ),
        "dispatch_view": _view_stage(
            baseline.capsules["dispatch"], candidate.capsules["dispatch"]
        ),
        "inventory_reports": {"status": "not_applicable", "episode_ids": None}
        if a["condition"] == "pooled"
        else _stage(
            _different_rows(
                [case["reports"] for case in left], [case["reports"] for case in right]
            )
        ),
        "dispatch_choice": _stage(
            _different_rows(
                [case["chosen"] for case in left], [case["chosen"] for case in right]
            )
        ),
        "score": _stage(
            _different_rows(a_score["rows"], b_score["rows"]),
            different=a_score != b_score,
            # The checked episode counts match, so the exact reward ratios
            # match if and only if their numerators match.
            scalar_reward_matches=a_score["optimal"] == b_score["optimal"],
            baseline={key: value for key, value in a_score.items() if key != "rows"},
            candidate={key: value for key, value in b_score.items() if key != "rows"},
        ),
    }
    return {
        "condition": a["condition"],
        "native_trial_ids": {"baseline": a["trial_id"], "candidate": b["trial_id"]},
        "stages": stages,
    }


def compare_replay(
    baseline: Path,
    candidate: Path,
    *,
    intentional_drift: Sequence[str] = (),
) -> dict[str, Any]:
    """Compare native evidence; raise ValueError on invalid or incomplete input."""
    marked = set()
    for selector in intentional_drift:
        condition, _, stage = selector.partition(":")
        if condition not in CONDITIONS or stage not in _ACTOR_STAGES:
            raise _Invalid("invalid_intentional_drift_selector")
        marked.add((condition, stage))
    a, b = _load_panel(baseline, "baseline"), _load_panel(candidate, "candidate")
    if (a.policy, a.hosted_route) != (b.policy, b.hosted_route):
        raise _Invalid("policy_mismatch")
    if a.trials.keys() != b.trials.keys() or a.episodes != b.episodes:
        raise _Invalid("axis_mismatch")
    a_ids = {trial.evidence["trial_id"] for trial in a.trials.values()}
    if a_ids.intersection(trial.evidence["trial_id"] for trial in b.trials.values()):
        raise _Invalid("duplicate_native_trial_id", panel="candidate")

    conditions = sorted({condition for _, condition in a.trials})
    if any(condition not in conditions for condition, _ in marked):
        raise _Invalid("intentional_drift_condition_not_in_panel")
    seed_axis = sorted({seed for seed, _ in a.trials})
    seeds = []
    for seed in seed_axis:
        cells = []
        divergent_stages = set()
        for condition in conditions:
            cell = _cell(a.trials[seed, condition], b.trials[seed, condition])
            for stage, result in cell["stages"].items():
                if result["status"] == "drift" and (condition, stage) in marked:
                    result["status"] = "intentional_drift"
                if result["status"] in _DRIFT:
                    divergent_stages.add(stage)
            cells.append(cell)
        seeds.append(
            {"seed": seed, "divergent_stages": sorted(divergent_stages), "cells": cells}
        )
    return {
        "schema": _SCHEMA,
        "status": (
            "drift" if any(seed["divergent_stages"] for seed in seeds) else "match"
        ),
        "policy": a.policy,
        "axes": {"episodes": a.episodes, "conditions": conditions, "seeds": seed_axis},
        "intentional_drift": [
            {"condition": condition, "stage": stage}
            for condition, stage in sorted(marked)
        ],
        "seeds": seeds,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _Parser(prog="compare_replay.py", description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--intentional-drift",
        action="append",
        default=[],
        metavar="CONDITION:STAGE",
        help=(
            "Label a specified actor-stage difference without implying a cause. "
            f"Stages: {', '.join(_ACTOR_STAGES)}. Repeat as needed."
        ),
    )
    parser.epilog = (
        "Exit status: 0 match, 1 drift (including marked drift), 2 invalid input."
    )
    try:
        args = parser.parse_args(argv)
        result = compare_replay(
            args.baseline, args.candidate, intentional_drift=args.intentional_drift
        )
        exit_code = 1 if result["status"] == "drift" else 0
    except _Invalid as error:
        result = {"schema": _SCHEMA, "status": "invalid", "error": error.detail}
        exit_code = 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
