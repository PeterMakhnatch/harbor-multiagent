"""Report paired native box-ownership outcomes on the registered fixed prefixes."""

import argparse
import json
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path
from statistics import fmean
from typing import Any

from . import intent_protocol, ownership_evidence, ownership_protocol, ownership_source

_ARMS = ("neutral", "owner")
_AXES = ("instance", "numbering", "rotation", "repetition")
_ACTION_METRICS = ("distinct_new", "excess_new_collisions", "historical_repeats")
_METRICS = (
    "new_flags",
    "team_reward",
    *_ACTION_METRICS,
    "changed_proposals",
    "follows_ownership",
)


def _exclude_aliases(blocks: list[dict[str, Any]]) -> list[str]:
    locations: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for block in blocks:
        arms = block["arms"]
        for arm, row in arms.items():
            if (digest := row.get("trial_id_sha256")) is not None:
                locations[digest].append((arms, arm))

    aliases = []
    for digest, entries in sorted(locations.items()):
        if len(entries) < 2:
            continue
        aliases.append(digest)
        for arms, arm in entries:
            row = arms[arm]
            arms[arm] = {
                "run": row["run"],
                "status": "invalid",
                "reason": "reused_native_trial_id",
                "evidence_status": row["status"],
                "trial_id_sha256": digest,
            }
    return aliases


def _score(block: dict[str, Any]) -> None:
    proposals = block["proposals"]
    labels = intent_protocol.LABELS
    owners = [labels[proposals.index(box)] for box in proposals]
    block["proposal_owners"] = dict(zip(labels, owners, strict=True))
    for row in block["arms"].values():
        if row["status"] != "complete":
            continue
        behavior = {
            label: {
                "changed_proposal": choice != proposal,
                "follows_ownership": choice == proposal
                if label == owner
                else choice not in proposals,
            }
            for label, owner, proposal, choice in zip(
                labels, owners, proposals, row["choices"], strict=True
            )
        }
        row["label_behavior"] = behavior
        row["scores"] = {
            "new_flags": row["new_flags"],
            "team_reward": row["team_reward"],
            **{name: row["metrics"][name] for name in _ACTION_METRICS},
            "changed_proposals": sum(
                item["changed_proposal"] for item in behavior.values()
            ),
            "follows_ownership": sum(
                item["follows_ownership"] for item in behavior.values()
            ),
        }

    neutral, owner = (block["arms"][arm].get("scores") for arm in _ARMS)
    block["owner_minus_neutral"] = (
        {
            name: (
                float(Fraction(str(owner[name])) - Fraction(str(neutral[name])))
                if name == "team_reward"
                else owner[name] - neutral[name]
            )
            for name in _METRICS
        }
        if neutral is not None and owner is not None
        else None
    )


def _distribute(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [
        delta for row in blocks if (delta := row["owner_minus_neutral"]) is not None
    ]
    flags = Counter(delta["new_flags"] for delta in deltas)
    return {
        "possible_pairs": len(blocks),
        "complete_pairs": len(deltas),
        "new_flags_difference_counts": {
            str(change): count for change, count in sorted(flags.items())
        },
        "direction_counts": {
            "wins": sum(count for change, count in flags.items() if change > 0),
            "losses": sum(count for change, count in flags.items() if change < 0),
            "ties": flags[0],
        },
    }


def _totals(scores: list[dict[str, Any]]) -> dict[str, int | float]:
    return {name: sum(row[name] for row in scores) for name in _METRICS}


def _arm_summary(blocks: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    rows = [row["arms"][arm] for row in blocks]
    scores = [row["scores"] for row in rows if row["status"] == "complete"]
    rewards = [
        row["team_reward"]
        for row in rows
        if row["status"] in {"complete", "model_invalid"}
    ]
    return {
        "scheduled_arms": len(rows),
        "status_counts": dict(Counter(row["status"] for row in rows)),
        "native_reward_known_arms": len(rewards),
        "native_reward_mean": fmean(rewards) if rewards else None,
        "primary_known_arms": len(scores),
        "primary_known_seats": len(scores) * len(intent_protocol.LABELS),
        "primary_known_score_totals": _totals(scores),
    }


def _paired(blocks: list[dict[str, Any]], worlds: list[int]) -> dict[str, Any]:
    per_world = []
    world_means = []
    for instance in worlds:
        possible = [row for row in blocks if row["instance"] == instance]
        deltas = [
            delta
            for row in possible
            if (delta := row["owner_minus_neutral"]) is not None
        ]
        totals = _totals(deltas)
        mean = (
            {name: total / len(deltas) for name, total in totals.items()}
            if deltas
            else None
        )
        if mean is not None:
            world_means.append(mean)
        per_world.append(
            {
                "instance": instance,
                **_distribute(possible),
                "arms": {arm: _arm_summary(possible, arm) for arm in _ARMS},
                "owner_minus_neutral_numerator": totals,
                "owner_minus_neutral": mean,
            }
        )

    full_support = len(world_means) == len(worlds)
    return {
        **_distribute(blocks),
        "arms": {arm: _arm_summary(blocks, arm) for arm in _ARMS},
        "required_worlds": len(worlds),
        "supported_worlds": len(world_means),
        "full_world_support": full_support,
        "per_world": per_world,
        "owner_minus_neutral": (
            {name: fmean(row[name] for row in world_means) for name in _METRICS}
            if full_support
            else None
        ),
    }


def analyze(archive: Path, tasks: Path, study: Path) -> dict[str, Any]:
    """Verify each registered arm independently and publish only public evidence."""
    blocks = intent_protocol.blocks()
    seeds = ownership_protocol.seeds()
    registered = [
        (
            block,
            {
                arm: ownership_protocol.run_id(block, arm)
                for arm in ownership_protocol.arms(block)
            },
        )
        for block in blocks
    ]
    runs = [run for _, arms in registered for run in arms.values()]
    if (
        len(blocks) != 36
        or len(seeds) != 36
        or seeds.keys() != {block.id for block in blocks}
    ):
        raise ValueError("wrong ownership proposal registration")
    if len(runs) != 72 or len(set(runs)) != 72:
        raise ValueError("wrong ownership arm registration")

    worlds = sorted({block.instance for block in blocks})
    cuts = {world: ownership_source.prepare(archive, tasks, world) for world in worlds}
    rows: list[dict[str, Any]] = []
    for block, arms in registered:
        proposals = seeds[block.id]
        rows.append(
            {
                "id": block.id,
                **{axis: getattr(block, axis) for axis in _AXES},
                "proposals": list(proposals),
                "initial_collision": len(set(proposals)) < len(proposals),
                "arm_order": list(arms),
                "arms": {
                    arm: {
                        "run": run,
                        **ownership_evidence.read_arm(
                            study / run, cuts[block.instance], block, arm, proposals
                        ),
                    }
                    for arm, run in arms.items()
                },
            }
        )

    aliases = _exclude_aliases(rows)
    for row in rows:
        _score(row)

    return {
        "schema": "seatlab.box-ownership-report/v1",
        "scope": "One native move in 3 fixed historical worlds. No population or cost claim.",
        "counting": (
            "New flags are distinct native flag boxes beyond the common prefix. "
            "Only complete pairs enter contrasts. Average those pairs within each world, "
            "then weight the 3 worlds equally. A win means ownership found more new flags. "
            "Arm-only scores do not require a complete pair. Model-invalid arms can have "
            "a verified baseline reward but no primary outcome. Proposals and choices "
            "use original box numbers in A, B, C order. An initial collision is any "
            "duplicate proposal. The first label owns each proposed box."
        ),
        "proposal_source_sha256": ownership_protocol.RESULTS_SHA256,
        "sources": [
            {
                "instance": world,
                "task_checksum": cut.task_checksum,
                "task_digest": cut.task_digest,
                "prefix_sha256": cut.prefix_sha256,
                "baseline_flags": cut.baseline_flags,
            }
            for world, cut in cuts.items()
        ],
        "coverage": {
            "scheduled_pairs": len(rows),
            "scheduled_arms": len(runs),
            "possible_turns": len(runs) * len(intent_protocol.LABELS),
            "reused_trial_id_digests": aliases,
        },
        "primary": _paired(rows, worlds),
        "by_initial_collision": {
            name: _paired(
                [row for row in rows if row["initial_collision"] == colliding], worlds
            )
            for name, colliding in (("colliding", True), ("clean", False))
        },
        **{
            f"by_{axis}": [
                {
                    axis: value,
                    **_paired([row for row in rows if row[axis] == value], worlds),
                }
                for value in sorted({row[axis] for row in rows})
            ]
            for axis in _AXES[1:]
        },
        "blocks": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.archive, args.tasks, args.study)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        print(
            json.dumps({"error": "cannot_verify_box_ownership_study"}), file=sys.stderr
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
