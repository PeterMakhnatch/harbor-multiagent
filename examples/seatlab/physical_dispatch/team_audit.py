from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from harbor.models.trial.paths import TrialPaths

from ...native_study import native, task_identity
from ..exhibit_capture import read_record
from ..exhibit_enroll import digest
from ..exhibit_pairs import DIR_FLAGS, open_folder, require
from ..exhibit_prepare import CHECKOUT, ROLES
from ..exhibit_team_inputs import absolute_path, check_result, endpoints
from ..workbench.arm_sources import bounded_tree, require_unchanged
from .certificate import case_commitment, native_pair
from .physical_evidence import verifier_evidence
from .physical_inputs import equal
from .preflight import NAME, host_fingerprint
from .stage_labels import summarize_handoffs
from .team_history import replay_team
from .team_plan import build_records
from .team_sources import extension_digest


def _pinned_plan(
    plan: Path, expect: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    directory = os.open(plan, DIR_FLAGS)
    try:
        receipt, payload = read_record(directory, "receipt.json")
        require(
            digest(payload) == expect, "prepared receipt differs from expected digest"
        )
        request, config_bytes = read_record(directory, "config.json")
        lock, lock_bytes = read_record(directory, "lock.json")
        require(
            digest(config_bytes) == receipt["config_digest"]
            and digest(lock_bytes) == receipt["lock_digest"],
            "prepared input digest differs",
        )
    finally:
        os.close(directory)
    return receipt, request, lock


def _read_result(
    trial: Path,
    config: Any,
    lock: dict[str, Any],
    task: Any,
    identity: dict[str, str],
    case: dict[str, Any],
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, str]]:
    folder = os.open(trial, DIR_FLAGS)
    try:
        paths = TrialPaths(trial)
        raw_config, config_bytes = read_record(folder, paths.config_path.name)
        raw_lock, lock_bytes = read_record(folder, paths.lock_path.name)
        equal(
            raw_config,
            config.model_dump(mode="json", exclude={"agent"}),
            "archived config differs",
        )
        equal(raw_lock, lock, "archived lock differs")
        raw_result, result_bytes = read_record(folder, paths.result_path.name)
        result = check_result(raw_result, config, identity, task, lock)
        verifier = open_folder(folder, (paths.verifier_dir.name,))
        try:
            cut, raw, pins = verifier_evidence(verifier, case, result)
        finally:
            os.close(verifier)
    finally:
        os.close(folder)
    return (
        result,
        cut,
        raw,
        pins
        | {
            "config": digest(config_bytes),
            "lock": digest(lock_bytes),
            "result": digest(result_bytes),
        },
    )


def inspect_team(plan: Path, trial: Path, *, expect: str) -> dict[str, Any]:
    plan = plan.expanduser().absolute()
    receipt, request, lock = _pinned_plan(plan, expect)
    declared = absolute_path(request["task"]["path"])
    root, stamps = bounded_tree(declared)
    require(root == declared, "task root differs")
    seed, map_name = receipt["seed"], receipt["map"]
    paths = endpoints(request)
    originals = build_records(root, plan, seed, map_name, paths)
    for name, saved in (("receipt", receipt), ("config", request), ("lock", lock)):
        equal(
            originals[name + ".json"],
            saved,
            "prepared " + name + " differs from design",
        )
    harbor = native()
    task = harbor.Task(root)
    config = harbor.TrialConfig.model_validate(request)
    identity = task_identity(harbor, root, NAME)
    instructions = {role: task.seat_instruction(role) for role in ROLES}
    equal(
        {
            role: hashlib.sha256(text.encode()).hexdigest()
            for role, text in instructions.items()
        },
        receipt["role_instruction_digests"],
        "role instructions differ",
    )
    _, maps = native_pair(seed)
    case = maps[map_name]
    require(case_commitment(case) == receipt["case_commitment"], "private case differs")
    result, cut, raw, pins = _read_result(trial, config, lock, task, identity, case)
    metadata = {
        seat.seat: seat.agent_result.metadata if seat.agent_result is not None else None
        for seat in result.seat_results or []
    }
    turns, routes = replay_team(cut, metadata, instructions)
    matched = len(turns) == 4
    require(
        matched == (raw["status"] == "complete"),
        "accepted final turn and physical grade differ",
    )
    versions, senders = summarize_handoffs(cut)
    require_unchanged(root, stamps)
    require(
        receipt["host_source_digest"] == host_fingerprint(CHECKOUT)
        and receipt["team_source_digest"] == extension_digest(CHECKOUT),
        "method source changed",
    )
    return {
        "schema": "seatlab.physical-free-team.evidence/v1",
        "status": raw["status"],
        "evidence": "consistent_local_native_archive",
        "provider_authenticated": False,
        "effect_estimated": False,
        "native_trial_id": str(result.id),
        "recorded_exception_count": len(result.recorded_exceptions()),
        "case_commitment": receipt["case_commitment"],
        "native_reward": raw["reward"],
        "accepted_turns": turns,
        "final_request_matched": matched,
        "route_evidence": routes,
        "version_evidence": versions,
        "sender_evidence": senders,
        "artifact_digests": {
            "prepared_receipt": expect,
            "prepared_config": receipt["config_digest"],
            "prepared_lock": receipt["lock_digest"],
            "team_source": receipt["team_source_digest"],
            **pins,
        },
        "limits": "A three-turn cut exposes the legal final view, not a recorded final request. These are observations of local records, not authorship, attention, first attempts, or a causal effect.",
    }
