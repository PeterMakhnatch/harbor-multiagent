from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from harbor.models.trial.paths import TrialPaths

from ..exhibit_capture import read_record
from ..exhibit_enroll import digest
from ..exhibit_pairs import DIR_FLAGS, open_folder, require, validate_observation
from .certificate import case_commitment, observation
from .physical_history import match_history
from .physical_inputs import equal, load_inputs

SCHEMA = "seatlab.exhibit-relay-physical.receiver-evidence/v1"
CAVEAT = "Consistency of supplied local records only; not proof of provider identity, remote delivery, unaccepted attempts or a treatment effect."


def verifier_evidence(
    folder: int, case: dict[str, Any], result: Any
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    cut, cut_bytes = read_record(folder, "private-exhibit-cut.json")
    require(cut.get("mode") == "linked", "cut mode differs")
    equal(cut.get("case"), case, "cut case differs")
    raw, observed_bytes = read_record(folder, "observation.json")
    equal(raw, observation(cut), "native observation differs")
    outcome = validate_observation(raw, folder, "fork", "linked", case_commitment(case))
    require(outcome.status in ("complete", "incomplete"), "invalid verifier status")
    hashes = {"private_cut": digest(cut_bytes), "observation": digest(observed_bytes)}
    if outcome.status == "complete":
        reward, reward_bytes = read_record(folder, "reward.json")
        require(
            result.reward_valid and result.verifier_result is not None,
            "invalid native reward",
        )
        equal(reward, {"reward": raw["reward"]}, "reward receipt differs")
        require(result.verifier_result.rewards == reward, "native reward differs")
        hashes["reward"] = digest(reward_bytes)
    else:
        require(
            not result.reward_valid
            and (result.verifier_result is None or not result.verifier_result.rewards),
            "incomplete trial claims a reward",
        )
    return cut, raw, hashes


def inspect_trial(plan: Path, cell: str, trial: Path) -> dict[str, Any]:
    trial_fd = os.open(trial, DIR_FLAGS)
    try:
        result, case, instructions, hashes, filing = load_inputs(plan, cell, trial_fd)
        verifier_fd = open_folder(trial_fd, (TrialPaths(trial).verifier_dir.name,))
        try:
            cut, raw, verifier_hashes = verifier_evidence(verifier_fd, case, result)
        finally:
            os.close(verifier_fd)
    finally:
        os.close(trial_fd)
    metadata = {
        seat.seat: seat.agent_result.metadata if seat.agent_result is not None else None
        for seat in result.seat_results or []
    }
    matched = match_history(cut, metadata, instructions, filing)
    require(
        type(matched) is list and len(matched) == len(cut["events"]),
        "history count differs",
    )
    final = any(
        row["role"] == "loader" and row["phase"] == "dispatch" for row in matched
    )
    require(final == (raw["status"] == "complete"), "final request and grade disagree")
    start, finish = result.started_at, result.finished_at
    if start is None or finish is None:
        raise ValueError("missing native trial interval")
    return {
        "schema": SCHEMA,
        "native_trial_id": str(result.id),
        "native_started_at": start.isoformat(),
        "native_finished_at": finish.isoformat(),
        "recorded_exception_count": len(result.recorded_exceptions()),
        "status": raw["status"],
        "cell": cell,
        "evidence": "consistent_local_native_archive",
        "provider_authenticated": False,
        "effect_estimated": False,
        "case_commitment": case_commitment(case),
        "native_reward": raw["reward"],
        "final_request_matched": final,
        "accepted_turns": matched,
        "artifact_digests": hashes | verifier_hashes,
        "caveat": CAVEAT,
    }
