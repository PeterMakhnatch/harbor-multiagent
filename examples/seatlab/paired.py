"""Analyze complete native SeatLab panels or enumerate the reference design."""

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from fractions import Fraction
from itertools import combinations, permutations, product
from pathlib import Path
from typing import Any, NamedTuple

CONDITIONS = ("silent", "stale", "fresh", "reordered", "pooled")
SPLITS = {"all": None, "stable": False, "changed": True}
METRICS = ("optimal", "feasible")


class Panel(NamedTuple):
    policy: str
    trials: Sequence[Mapping[str, Any]]


def _read_receipt(path: Path, schema: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != schema:
        raise ValueError(f"{path}: expected {schema}")
    return document


def _receipt_cells(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cells = {}
    coordinates = set()
    rows = document.get("cells")
    if not isinstance(rows, list):
        raise ValueError("receipt does not contain cells")
    for cell in rows:
        if (
            not isinstance(cell, dict)
            or not isinstance(cell.get("cell_id"), str)
            or not cell["cell_id"]
            or type(cell.get("seed")) is not int
            or cell.get("condition") not in CONDITIONS
        ):
            raise ValueError("invalid receipt cell")
        cell_id = cell["cell_id"]
        coordinate = (cell["seed"], cell["condition"])
        if cell_id in cells or coordinate in coordinates:
            raise ValueError(f"duplicate receipt cell {cell_id!r} at {coordinate}")
        cells[cell_id] = cell
        coordinates.add(coordinate)
    return cells


def read_panel(root: Path) -> Panel:
    """Join the plan and completion receipts, then re-read the native trials."""
    root = root.resolve()
    plan = _read_receipt(root / "plan.json", "seatlab.panel-plan/v1")
    runs = _read_receipt(root / "runs.json", "seatlab.panel-runs/v1")
    policy = plan.get("policy")
    seeds, conditions, episodes = (
        plan.get("seeds"),
        plan.get("conditions"),
        plan.get("episodes"),
    )
    if (
        not isinstance(policy, str)
        or not policy
        or not isinstance(seeds, list)
        or not seeds
        or any(type(seed) is not int for seed in seeds)
        or len(set(seeds)) != len(seeds)
        or not isinstance(conditions, list)
        or not conditions
        or any(condition not in CONDITIONS for condition in conditions)
        or len(set(conditions)) != len(conditions)
        or type(episodes) is not int
        or episodes < 1
    ):
        raise ValueError("invalid panel plan axes or policy")
    intended, completed = _receipt_cells(plan), _receipt_cells(runs)
    if {(cell["seed"], cell["condition"]) for cell in intended.values()} != set(
        product(seeds, conditions)
    ):
        raise ValueError("plan cells do not match its seed × condition grid")
    if intended.keys() != completed.keys():
        missing = sorted(intended.keys() - completed.keys())
        extra = sorted(completed.keys() - intended.keys())
        raise ValueError(
            f"incomplete panel receipts: missing {missing}, unexpected {extra}"
        )
    unmeasured = [
        f"{key}: {cell.get('error') or 'no native measurement'}"
        for key, cell in sorted(completed.items())
        if cell.get("status") != "measured"
    ]
    if unmeasured:
        raise ValueError(f"unmeasured cells: {'; '.join(unmeasured)}")

    if __package__:
        from .evidence import read_trial
    else:
        from evidence import read_trial

    trials: list[Mapping[str, Any]] = []
    identities: dict[str, str] = {}
    for key, cell in sorted(completed.items()):
        expected = intended[key]
        if any(expected[field] != cell[field] for field in ("seed", "condition")):
            raise ValueError(f"{key}: completion cell does not match the plan")
        path = cell.get("trial_path")
        if not isinstance(path, str) or not path or Path(path).is_absolute():
            raise ValueError(f"{key}: trial_path is not relative to the panel")
        trial_path = (root / path).resolve()
        if not trial_path.is_relative_to(root):
            raise ValueError(f"{key}: trial_path leaves the panel")
        trial = read_trial(trial_path)
        if (
            trial["seed"] != expected["seed"]
            or trial["condition"] != expected["condition"]
            or trial["score"]["episodes"] != episodes
            or trial != cell.get("evidence")
        ):
            raise ValueError(f"{key}: native trial does not match the receipt and plan")
        for role, seat in trial["seats"].items():
            # Pooled intentionally replaces the inventory policy with a no-op.
            slot = (
                "pooled inventory"
                if role == "inventory" and cell["condition"] == "pooled"
                else role
            )
            identity = json.dumps(
                [seat["agent_name"], seat["agent_version"], seat["model_info"]],
                sort_keys=True,
            )
            if identities.setdefault(slot, identity) != identity:
                raise ValueError(
                    f"{key}: native identity varies for {slot}; do not pool policies"
                )
        trials.append(trial)
    return Panel(policy, trials)


def _rows(trial: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    rows = trial["score"]["rows"]
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError(f"{trial['trial_id']}: duplicate episode ID")
    return by_id


def _signed(deltas: list[int]) -> dict[str, int]:
    wins = sum(delta > 0 for delta in deltas)
    losses = sum(delta < 0 for delta in deltas)
    return {
        "wins": wins,
        "losses": losses,
        "ties": len(deltas) - wins - losses,
        "net": wins - losses,
    }


def _compare(
    pairs: list[tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]],
    changed: bool | None,
) -> dict[str, Any]:
    selected = []
    for rows, reference in pairs:
        block = [
            (row, reference[episode])
            for episode, row in rows.items()
            if changed is None or row["changed"] == changed
        ]
        if block:
            selected.append(block)
    result: dict[str, Any] = {
        "episodes": sum(map(len, selected)),
        "seed_blocks_with_episodes": len(selected),
        "paired": {},
    }
    for metric in METRICS:
        result[metric] = sum(row[metric] for block in selected for row, _ in block)
        deltas = [
            [int(row[metric]) - int(ref[metric]) for row, ref in block]
            for block in selected
        ]
        result["paired"][metric] = {
            "episodes": _signed([delta for block in deltas for delta in block]),
            "seed_blocks": _signed([sum(block) for block in deltas]),
        }
    return result


def _extremes(scores: dict[str, int]) -> dict[str, list[str]]:
    best, worst = max(scores.values()), min(scores.values())
    return {
        "best": sorted(name for name, score in scores.items() if score == best),
        "worst": sorted(name for name, score in scores.items() if score == worst),
    }


def summarize(panels: dict[str, Panel], reference: str = "silent") -> dict[str, Any]:
    """Require a common seed × condition grid and identical latent rows per seed."""
    if not panels or any(not panel.trials for panel in panels.values()):
        raise ValueError("each named panel must contain native trials")
    grid = {}
    seeds = set()
    conditions = set()
    trial_ids = set()
    for group, panel in sorted(panels.items()):
        for trial in panel.trials:
            trial_id = trial["trial_id"]
            seed, condition = trial["seed"], trial["condition"]
            key = (group, condition, seed)
            if key in grid or trial_id in trial_ids:
                raise ValueError(
                    f"duplicate cell or native trial: {group}:{condition} "
                    f"seed {seed} ({trial_id})"
                )
            trial_ids.add(trial_id)
            seeds.add(seed)
            conditions.add(condition)
            grid[key] = trial, _rows(trial)

    ordered_seeds = sorted(seeds)
    cells = list(product(sorted(panels), sorted(conditions)))
    missing = [
        f"{group}:{condition} seed {seed}"
        for (group, condition), seed in product(cells, ordered_seeds)
        if (group, condition, seed) not in grid
    ]
    if missing:
        raise ValueError(f"unpaired cells: {', '.join(missing)}")
    if len(cells) < 2:
        raise ValueError("paired analysis needs at least two group:condition cells")

    fingerprints = {}
    latent_episodes = Counter()
    anchor_group, anchor_condition = cells[0]
    for seed in ordered_seeds:
        anchor, anchor_rows = grid[anchor_group, anchor_condition, seed]
        fingerprints[seed] = anchor["fingerprint"]
        latent_episodes.update(row["changed"] for row in anchor_rows.values())
        for group, condition in cells[1:]:
            trial, rows = grid[group, condition, seed]
            label = f"{group}:{condition} and {anchor_group}:{anchor_condition}"
            if trial["fingerprint"] != anchor["fingerprint"]:
                raise ValueError(
                    f"seed {seed}: latent fingerprint mismatch for {label}"
                )
            if rows.keys() != anchor_rows.keys() or any(
                rows[i]["changed"] != row["changed"] for i, row in anchor_rows.items()
            ):
                raise ValueError(
                    f"seed {seed}: episode IDs or change labels differ for {label}"
                )

    tagged_group, separator, ref_condition = reference.partition(":")
    if not separator:
        ref_condition = tagged_group
    if ref_condition not in conditions or (separator and tagged_group not in panels):
        raise ValueError(f"reference {reference!r} is not a measured cell")

    summaries = {}
    totals = {}
    policy_scores: dict[str, list[int]] = {group: [] for group in panels}
    for group, condition in cells:
        label = f"{group}:{condition}"
        ref_group = tagged_group if separator else group
        pairs = [
            (grid[group, condition, seed][1], grid[ref_group, ref_condition, seed][1])
            for seed in ordered_seeds
        ]
        splits = {name: _compare(pairs, changed) for name, changed in SPLITS.items()}
        totals[label] = splits["all"]["optimal"]
        policy_scores[group].append(totals[label])
        summaries[label] = {
            "reference": f"{ref_group}:{ref_condition}",
            "trial_ids_by_seed": {
                seed: grid[group, condition, seed][0]["trial_id"]
                for seed in ordered_seeds
            },
            "splits": splits,
        }

    minimum = {group: min(scores) for group, scores in policy_scores.items()}
    return {
        "mode": "native_paired",
        "groups": {name: panel.policy for name, panel in panels.items()},
        "units": {
            "paired_seed_blocks": len(seeds),
            "native_trials": len(trial_ids),
            "distinct_latent_batches": len(set(fingerprints.values())),
            "latent_episode_rows": {
                "all": latent_episodes.total(),
                "stable": latent_episodes[False],
                "changed": latent_episodes[True],
            },
        },
        "latent_fingerprint_by_seed": fingerprints,
        "cells": summaries,
        "cell_extremes_by_optimal_count": _extremes(totals),
        "group_extremes_by_total_optimal_count": _extremes(
            {group: sum(scores) for group, scores in policy_scores.items()}
        ),
        "group_minimum_condition_optimal_counts": minimum,
        "maximin_groups": _extremes(minimum)["best"],
        "notes": [
            "Episodes are nested in native trials. The paired design unit is the seed block.",
            "Seed signs use the sum of the cell's episode differences from its reference.",
            "Group extremes use all conditions. Maximin uses the worst condition.",
            "The plan declares the group policy. Model identity is not provider attestation.",
        ],
    }


def design() -> dict[str, Any]:
    """Enumerate uniform worlds conditional on a stable or changed episode."""
    bays = range(4)
    orderings = list(permutations(bays))
    old_sets = [frozenset(pair) for pair in combinations(bays, 2)]
    policies = ("highest_global", "highest_old", "highest_true")
    outcomes = ("optimal", "feasible_only", "infeasible")
    splits = {}
    for changed in (False, True):
        counts: dict[str, Counter[str]] = {policy: Counter() for policy in policies}
        joint: Counter[tuple[str, str]] = Counter()
        for old in old_sets:
            after_sets = (
                [old - {out} | {new} for out, new in product(old, set(bays) - old)]
                if changed
                else [old]
            )
            for after, ranked in product(after_sets, orderings):
                old_top = next(bay for bay in ranked if bay in old)
                true_top = next(bay for bay in ranked if bay in after)
                selections = (ranked[0], old_top, true_top)
                reports = [
                    "optimal"
                    if bay == true_top
                    else "feasible_only"
                    if bay in after
                    else "infeasible"
                    for bay in selections
                ]
                joint[reports[0], reports[1]] += 1
                for policy, outcome in zip(policies, reports, strict=True):
                    counts[policy][outcome] += 1
        worlds = joint.total()
        splits["changed" if changed else "stable"] = {
            "states": worlds,
            "after_sets_per_old_set": 4 if changed else 1,
            "rates": {
                policy: {
                    "optimal": str(Fraction(counter["optimal"], worlds)),
                    "feasible": str(
                        Fraction(counter["optimal"] + counter["feasible_only"], worlds)
                    ),
                }
                for policy, counter in counts.items()
            },
            "joint_counts_silent_then_stale": {
                silent: {stale: joint[silent, stale] for stale in outcomes}
                for silent in outcomes
            },
        }
    return {
        "mode": "exact_design_not_native_evidence",
        "utility_permutations": len(orderings),
        "old_sets": len(old_sets),
        "condition_to_policy": {
            "silent": "highest_global",
            "stale": "highest_old",
            "fresh": "highest_true",
            "pooled": "highest_true",
            "reordered_last_arrival": "highest_old",
            "reordered_version_aware": "highest_true",
        },
        "splits": splits,
        "derivation": [
            "Use 24 uniform utility permutations and 6 uniform old sets, independently.",
            "Each old set has 1 stable continuation and 2 × 2 equally likely changes.",
            "Normalize the cases separately. Enumerated states are not native trials.",
            "The policy predictions assume truthful availability reports.",
            "Silent succeeds when the global best is in the true set (1/2 in either case).",
            "Highest true is always optimal. Highest old is always optimal in stable worlds.",
            "With change, the old best is feasible if the retained bay beats the removed bay (1/2).",
            "With change, it is optimal if the retained bay beats the removed and added bays (1/3).",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--design", action="store_true", help="enumerate exact design probabilities"
    )
    mode.add_argument(
        "--panel",
        action="append",
        metavar="NAME=PATH",
        help="run_panel.py output directory; repeat for separate policy groups",
    )
    parser.add_argument(
        "--reference",
        metavar="[NAME:]CONDITION",
        help="paired reference; default: silent within each group",
    )
    args = parser.parse_args()
    if args.design:
        if args.reference is not None:
            parser.error("--reference only applies to native panels")
        result = design()
    else:
        panels = {}
        for spec in args.panel:
            name, separator, path = spec.partition("=")
            if not separator or not name or ":" in name or not path or name in panels:
                parser.error(
                    f"expected a unique panel NAME=PATH with no colon in NAME: {spec!r}"
                )
            try:
                panels[name] = read_panel(Path(path))
            except (ValueError, OSError) as exc:
                parser.error(f"panel {name!r}: {exc}")
        try:
            result = summarize(panels, args.reference or "silent")
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
