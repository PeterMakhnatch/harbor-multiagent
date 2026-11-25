from __future__ import annotations

import argparse
import json
import os
from importlib import import_module
from pathlib import Path
from typing import Any

from .exhibit_authored import MODES, WORLD, canonical
from .exhibit_capture import read_record
from .exhibit_enroll import digest
from .exhibit_pairs import DIR_FLAGS, open_folder, require, validate_observation
from .exhibit_team_inputs import load_inputs
from .exhibit_turn_receipt import reconcile

SCHEMA = "seatlab.exhibit-team-receipt/v1"


def verifier_evidence(
    folder: int, expected: dict[str, Any], arm: str, result: Any
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    cut, cut_bytes = read_record(folder, "private-exhibit-cut.json")
    require(
        cut.get("mode") == arm and canonical(cut.get("case")) == canonical(expected),
        "trial world differs from prepared case",
    )
    raw, observed_bytes = read_record(folder, "observation.json")
    observation = import_module(f"{WORLD}.diagnostics").observation(cut)
    require(
        canonical(raw) == canonical(observation),
        "native observation differs from reconstructed world",
    )
    outcome = validate_observation(
        {key: value for key, value in raw.items() if key != "authored_handoff"},
        folder,
        "coupling",
        arm,
        observation["case_commitment"],
    )
    require(outcome.status in ("complete", "incomplete"), "unsupported verifier status")
    hashes = {"private_cut": digest(cut_bytes), "observation": digest(observed_bytes)}
    if outcome.status == "complete":
        reward, reward_bytes = read_record(folder, "reward.json")
        require(
            result.reward_valid and result.verifier_result is not None,
            "native reward not valid",
        )
        require(
            canonical(reward) == canonical({"reward": observation["reward"]})
            and result.verifier_result.rewards == reward,
            "native and verifier rewards differ",
        )
        hashes["reward"] = digest(reward_bytes)
    else:
        require(
            not result.reward_valid
            and (result.verifier_result is None or not result.verifier_result.rewards),
            "incomplete native trial claims a reward",
        )
    return cut, observation, hashes


def inspect_archive(plan: Path, arm: str, trial: Path) -> dict[str, Any]:
    from harbor.models.trial.paths import TrialPaths

    require(arm in MODES, "unknown authored transport")
    trial_fd = os.open(trial, DIR_FLAGS)
    try:
        result, case, instructions, hashes = load_inputs(plan, arm, trial_fd)
        verifier_fd = open_folder(trial_fd, (TrialPaths(trial).verifier_dir.name,))
        try:
            cut, observation, verifier_hashes = verifier_evidence(
                verifier_fd, case, arm, result
            )
        finally:
            os.close(verifier_fd)
    finally:
        os.close(trial_fd)
    owners = {
        seat.seat: (
            seat.agent_result.metadata if seat.agent_result is not None else None
        )
        for seat in result.seat_results or []
    }
    matched = reconcile(cut, owners, instructions)
    handoff = observation["handoff"]
    total = handoff["total_weight"]
    chain, item = handoff["visible_supported_weight"], handoff["visible_item_weight"]
    return {
        "schema": SCHEMA,
        "evidence": "native_archive_consistent",
        "status": observation["status"],
        "stage": handoff["stage"],
        "provider_authenticated": False,
        "operator_authenticated": False,
        "recorded_exception_count": len(result.recorded_exceptions()),
        "arm": arm,
        "case_commitment": observation["case_commitment"],
        "accepted_turns": matched,
        "depot_request_matched": any(row["role"] == "depot" for row in matched),
        "prescribed_handoff": observation["authored_handoff"],
        "native_reward": observation["reward"],
        "C": None if chain is None else chain / total,
        "P": None if item is None else item / (2 * total),
        "coverage_weight": {
            "complete": chain,
            "items": item,
            "total": total,
            "total_items": 2 * total,
        },
        "artifact_digests": hashes | verifier_hashes,
        "scope": "Consistency among supplied local inputs, accepted owner request stamps, actions and native verifier files. Not proof of remote delivery, identities, unaccepted attempts or a treatment effect.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check an archived three-owner Exhibit Relay trial. Never launches a trial."
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("arm", choices=MODES)
    parser.add_argument("trial", type=Path)
    args = parser.parse_args(argv)
    try:
        report = inspect_archive(args.plan, args.arm, args.trial)
    except FileNotFoundError:
        report = {
            "schema": SCHEMA,
            "evidence": "insufficient",
            "status": "missing_artifact",
        }
    except (
        OSError,
        ValueError,
        TypeError,
        IndexError,
        KeyError,
        AttributeError,
        ArithmeticError,
        RecursionError,
    ):
        report = {
            "schema": SCHEMA,
            "evidence": "unverified",
            "status": "inconsistent_or_unreadable",
        }
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if report["evidence"] == "native_archive_consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
