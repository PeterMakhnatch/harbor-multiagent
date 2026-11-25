from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import stat
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any

from .exhibit_pairs import (
    DIR_FLAGS,
    FILE_FLAGS,
    MAX_JSON,
    Outcome,
    open_folder,
    path_parts,
    reject_constant,
    reject_float,
    require,
    unique_object,
    validate_observation,
)
from .exhibit_plan import load_plan

CAPS = {"C": Fraction(2, 5), "P": Fraction(1, 2)}
STATUSES = (
    "no_outcome_leaf",
    "invalid_or_missing_evidence",
    "empty_filing",
    "nonempty_filing",
)
BOOTSTRAP_SEED = 20261125
BOOTSTRAP_REPLICATES = 10_000


def _record(folder: int, name: str) -> tuple[dict[str, Any], str]:
    with os.fdopen(os.open(name, FILE_FLAGS, dir_fd=folder), "rb") as source:
        before = os.fstat(source.fileno())
        require(
            stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_JSON,
            "invalid record file",
        )
        data = source.read(MAX_JSON + 1)
        after = os.fstat(source.fileno())
        require(
            len(data) == before.st_size
            and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "record changed",
        )
    raw = json.loads(
        data,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
        parse_float=reject_float,
    )
    require(type(raw) is dict, "invalid record")
    return raw, "sha256:" + hashlib.sha256(data).hexdigest()


def _admitted(
    folder: int, commitment: str, arm: str, enrollment: dict[str, Any]
) -> Outcome:
    receipt, receipt_digest = _record(folder, "receipt.json")
    expected = {
        "schema": "seatlab.exhibit-capture/v1",
        "status": "admitted",
        "case_commitment": commitment,
        "enrollment_status": enrollment["status"],
        "arm": arm,
        "manifest_digest": enrollment["manifest_digest"],
        "local_task_tree_digest": enrollment["local_task_tree_digest"],
        "host_harness_digest": enrollment["host_harness_digest"],
        "evidence": "declared-local/artifact-bound",
    }
    require(
        all(receipt.get(key) == value for key, value in expected.items())
        and receipt.get("fixed_prefix_verified") is True,
        "capture receipt mismatch",
    )
    stamp = receipt.get("depot_owner_request")
    require(
        isinstance(stamp, dict)
        and stamp.keys() == {"bytes", "sha256"}
        and type(stamp["bytes"]) is int
        and 0 < stamp["bytes"] <= 65536
        and isinstance(stamp["sha256"], str)
        and len(stamp["sha256"]) == 64
        and all(char in "0123456789abcdef" for char in stamp["sha256"]),
        "missing owner request receipt",
    )
    hashes = receipt.get("artifact_digests")
    if not isinstance(hashes, dict):
        raise TypeError("missing artifact digests")
    observation, digest = _record(folder, "observation.json")
    require(hashes.get("observation.json") == digest, "observation digest mismatch")
    complete = observation.get("status") == "complete"
    if complete:
        require(
            _record(folder, "reward.json")[1] == hashes.get("reward.json"),
            "reward digest mismatch",
        )
    else:
        require("reward.json" not in hashes, "unexpected reward digest")
    outcome = validate_observation(observation, folder, "coupling", arm, commitment)
    require(outcome.status in ("complete", "incomplete"), "invalid observation")
    handoff = observation["handoff"]
    require(
        receipt.get("observation_status") == outcome.status
        and receipt.get("stage") == handoff["stage"],
        "capture status mismatch",
    )
    require(
        handoff["stage"] in ("dispatch", "done")
        and outcome.total_weight == 10
        and handoff["nominated_count"] == 4
        and handoff["registrar_filed"] == 4
        and handoff["nominated_weight"] == 10
        and handoff["potential_intent_weight"] == 20,
        "fixed prefix mismatch",
    )
    require(
        outcome.C is not None
        and 0 <= outcome.C <= CAPS["C"]
        and outcome.P is not None
        and 0 <= outcome.P <= CAPS["P"],
        "infeasible coverage",
    )
    require(
        handoff["visible_item_weight"] + handoff["missing_depot_intent_weight"] == 20,
        "intent reconciliation mismatch",
    )
    empty = handoff["depot_filed"] == 0
    require(not empty or outcome.C == outcome.P == 0, "inconsistent empty filing")
    require(
        _record(folder, "receipt.json")[1] == receipt_digest
        and _record(folder, "observation.json")[1] == digest,
        "capture changed",
    )
    if complete:
        require(
            _record(folder, "reward.json")[1] == hashes["reward.json"], "reward changed"
        )
    return Outcome(
        "empty_filing" if empty else "nonempty_filing", C=outcome.C, P=outcome.P
    )


def _outcome(
    root: int, path: str, commitment: str, arm: str, enrollment: dict[str, Any]
) -> Outcome:
    try:
        folder = open_folder(root, path_parts(path))
    except FileNotFoundError:
        return Outcome("no_outcome_leaf")
    except OSError:
        return Outcome("invalid_or_missing_evidence")
    try:
        return _admitted(folder, commitment, arm, enrollment)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return Outcome("invalid_or_missing_evidence")
    finally:
        os.close(folder)


def _metric(
    rows: list[tuple[Outcome, Outcome]], metric: str, arms: list[str]
) -> dict[str, Any]:
    n = len(rows)
    cap = CAPS[metric]
    require(n > 0, "empty cohort")
    means = {}
    bounds = []
    counts = []
    paired = []
    for index, arm in enumerate(arms):
        values = [getattr(row[index], metric) for row in rows]
        known = [value for value in values if value is not None]
        total = sum(known, Fraction(0))
        count = len(known)
        lower, upper = total / n, (total + (n - count) * cap) / n
        bounds.append((lower, upper))
        counts.append(count)
        means[arm] = {
            "observed_mean": str(total / count) if count else None,
            "mean_bounds": [str(lower), str(upper)],
            "observed_count": count,
            "unknown_count": n - count,
        }
    for left, right in rows:
        a, b = getattr(left, metric), getattr(right, metric)
        if a is not None and b is not None:
            paired.append(b - a)
    return {
        "feasible_interval": ["0", str(cap)],
        "arms": means,
        "difference_bounds": [
            str(bounds[1][0] - bounds[0][1]),
            str(bounds[1][1] - bounds[0][0]),
        ],
        "paired_observed_difference": str(sum(paired, Fraction(0)) / len(paired))
        if paired
        else None,
        "coverage": {
            arms[0]: str(Fraction(counts[0], n)),
            arms[1]: str(Fraction(counts[1], n)),
            "paired": str(Fraction(len(paired), n)),
        },
        "paired_count": len(paired),
        "unpaired_or_unknown_count": n - len(paired),
    }


def _summary(rows: list[tuple[Outcome, Outcome]], arms: list[str]) -> dict[str, Any]:
    counts = {}
    for index, arm in enumerate(arms):
        observed = Counter(row[index].status for row in rows)
        counts[arm] = {status: observed[status] for status in STATUSES}
    return {
        "pair_count": len(rows),
        "filing_counts": counts,
        "metrics": {metric: _metric(rows, metric, arms) for metric in CAPS},
    }


def _bootstrap(layouts: dict[str, list[tuple[Outcome, Outcome]]]) -> dict[str, Any]:
    n = sum(len(rows) for rows in layouts.values())
    result = {
        "method": "balanced paired case bootstrap within layouts a and b",
        "metric": "C",
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "interpretation": "descriptive conditional approximation; not causal proof or real-model provenance",
        "percentile_interval_95": None,
    }
    if n < 100 or any(
        left.C is None or right.C is None
        for rows in layouts.values()
        for left, right in rows
    ):
        return result | {"status": "not_computed"}
    strata = []
    for layout in ("a", "b"):
        differences = []
        for left, right in layouts[layout]:
            if left.C is None or right.C is None:
                raise ValueError("missing paired coverage")
            scaled = 10 * (right.C - left.C)
            require(scaled.denominator == 1, "invalid coverage quantum")
            differences.append(int(scaled))
        strata.append(differences)
    rng = random.Random(BOOTSTRAP_SEED)
    samples = sorted(
        sum(sum(rng.choices(values, k=len(values))) for values in strata)
        for _ in range(BOOTSTRAP_REPLICATES)
    )
    endpoints = []
    for quantile in (Fraction(1, 40), Fraction(39, 40)):
        position = quantile * (BOOTSTRAP_REPLICATES - 1)
        index = int(position)
        interpolated = samples[index] + (position - index) * (
            samples[index + 1] - samples[index]
        )
        endpoints.append(str(interpolated / (10 * n)))
    return result | {
        "status": "computed",
        "percentile_interval_95": endpoints,
        "quantile_rule": "linear interpolation at q*(replicates-1)",
    }


def report(plan: Path) -> dict[str, Any]:
    manifest, schedule, enrollment = load_plan(plan)
    arms = manifest["arms"]
    require(
        manifest["profile"] == "coupling" and arms == ["masked", "bundled"],
        "unsupported fixed-prefix contrast",
    )
    layouts = {"a": [], "b": []}
    rows = []
    root = os.open(plan, DIR_FLAGS)
    try:
        for pair, case in zip(manifest["pairs"], schedule["cases"], strict=True):
            left = _outcome(
                root, pair["left"], pair["case_commitment"], arms[0], enrollment
            )
            right = _outcome(
                root, pair["right"], pair["case_commitment"], arms[1], enrollment
            )
            rows.append((left, right))
            layouts[case["layout"]].append((left, right))
    finally:
        os.close(root)
    return {
        "schema": "seatlab.exhibit-filing.summary/v1",
        "profile": "coupling",
        "arms": arms,
        "enrollment_status": enrollment["status"],
        "enrollable": enrollment["enrollable"],
        "evidence": "declared-local/artifact-bound; no model or CI authentication",
        "estimand": "fixed-prefix depot filing coverage, right minus left",
        "physical_reward_reported": False,
        "empirical_causal_verdict": None,
        "no_outcome_leaf_definition": "no registered outcome leaf; not evidence of zero coverage",
        **_summary(rows, arms),
        "layouts": {layout: _summary(group, arms) for layout, group in layouts.items()},
        "bootstrap": _bootstrap(layouts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report declared fixed-prefix filing evidence"
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("--json", action="store_true", required=True)
    args = parser.parse_args()
    try:
        result = report(args.plan)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        print("invalid enrollment", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
